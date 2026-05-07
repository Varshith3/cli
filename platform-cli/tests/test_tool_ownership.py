from __future__ import annotations

import pytest
from typer.testing import CliRunner

from platform_cli.cli import app
from platform_cli.commands import tools as tools_cmd
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.errors import PlatformError
from platform_cli.manifests.validate import validate_toolset
from platform_cli.state.store import get_tool_state, update_tool_state
from platform_cli.tools.ownership import build_ownership_policy, reconcile_tool_ownership, set_tool_ownership_override
from platform_cli.tools.service import ToolRuntimeSpec


runner = CliRunner()


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    cli_ctx.non_interactive = False
    cli_ctx.verbose = False
    cli_ctx.quiet = False
    cli_ctx.json = False
    return tmp_path


def _toolset_with_ownership(*, default_owner: str = "ghdp", allow_user_override: bool = False) -> dict:
    return {
        "schema_version": "0.0.1",
        "teams": {
            "platform": {
                "tools": {
                    "git": {
                        "op": ">=",
                        "version": "1.0.0",
                        "ownership": {
                            "default_owner": default_owner,
                            "allow_user_override": allow_user_override,
                        },
                    }
                }
            }
        },
    }


def _policy_req(*, default_owner: str = "ghdp", allow_user_override: bool = False) -> dict:
    return {
        "op": ">=",
        "version": "1.0.0",
        "ownership": {
            "default_owner": default_owner,
            "allow_user_override": allow_user_override,
        },
    }


def _spec(
    name: str = "git",
    *,
    owner: str = "ghdp",
    allow_user_override: bool = False,
    source: str = "pkg:platform_cli/resources/manifests/toolset.json",
) -> ToolRuntimeSpec:
    policy = build_ownership_policy(_policy_req(default_owner=owner, allow_user_override=allow_user_override), source)
    return ToolRuntimeSpec(
        name=name,
        display_name=name.upper(),
        detect_cmd=["detect"],
        version_cmd=["version"],
        install_cmd=["install"],
        upgrade_cmd=["upgrade"],
        uninstall_cmd=["uninstall"],
        version_req={"op": ">=", "version": "1.0.0"},
        ownership_policy=policy,
    )


def _tool_command_env(monkeypatch, spec: ToolRuntimeSpec) -> None:
    monkeypatch.setattr(tools_cmd, "_resolve_effective_team", lambda *_args, **_kwargs: "platform")
    manifests = (
        _toolset_with_ownership(default_owner=spec.ownership_policy.default_owner, allow_user_override=spec.ownership_policy.allow_user_override),
        {"schema_version": "0.0.1", "tools": {spec.name: {}}},
        {"toolset": spec.ownership_policy.source_label, "registry": "pkg:platform_cli/resources/manifests/tool-registry.json"},
    )
    monkeypatch.setattr(tools_cmd, "_load_manifests_with_team_toolset", lambda refresh_toolset=False: manifests)
    monkeypatch.setattr(
        tools_cmd,
        "load_manifests",
        lambda: manifests,
    )
    monkeypatch.setattr(tools_cmd, "resolve_team_tools", lambda *_args, **_kwargs: [spec])


def test_validate_toolset_accepts_ownership_object() -> None:
    validate_toolset(_toolset_with_ownership(default_owner="ghdp", allow_user_override=True))


@pytest.mark.parametrize(
    "invalid_ownership",
    [
        {"default_owner": "robot", "allow_user_override": True},
        {"default_owner": "ghdp", "allow_user_override": "yes"},
    ],
)
def test_validate_toolset_rejects_invalid_ownership_schema(invalid_ownership: dict) -> None:
    toolset = _toolset_with_ownership()
    toolset["teams"]["platform"]["tools"]["git"]["ownership"] = invalid_ownership

    with pytest.raises(PlatformError) as exc:
        validate_toolset(toolset)

    assert exc.value.code == "E_MANIFEST_INVALID"


@pytest.mark.parametrize(
    "source, expected_owner, expected_override",
    [
        ("managed:/tmp/team-toolset.managed.json", "user", True),
        ("pkg:platform_cli/resources/manifests/toolset.json", "user", True),
    ],
)
def test_build_ownership_policy_trusts_managed_and_packaged_sources(source: str, expected_owner: str, expected_override: bool) -> None:
    policy = build_ownership_policy(_policy_req(default_owner=expected_owner, allow_user_override=expected_override), source)

    assert policy.default_owner == expected_owner
    assert policy.allow_user_override is expected_override
    assert policy.trusted_source is True
    assert policy.source_kind in {"managed", "packaged"}


def test_build_ownership_policy_ignores_user_sources_for_user_managed_defaults() -> None:
    policy = build_ownership_policy(_policy_req(default_owner="user", allow_user_override=True), "user:/tmp/toolset.json")

    assert policy.default_owner == "ghdp"
    assert policy.allow_user_override is False
    assert policy.trusted_source is False
    assert policy.source_kind == "user"


def test_tools_ownership_set_command_persists_user_override(isolated_home, monkeypatch):
    spec = _spec("git", allow_user_override=True)
    _tool_command_env(monkeypatch, spec)

    set_result = runner.invoke(app, ["tools", "ownership", "set", "--tool", "git", "--owner", "user"])

    assert set_result.exit_code == 0
    assert "owner='user'" in set_result.output
    assert "owner_source='override'" in set_result.output
    assert "policy_source='packaged'" in set_result.output
    assert "resolution_source='override'" in set_result.output
    assert get_tool_state("git")["ownership"]["override_owner"] == "user"


def test_tools_ownership_clear_command_returns_to_policy_default(isolated_home, monkeypatch):
    spec = _spec("git", allow_user_override=True)
    _tool_command_env(monkeypatch, spec)
    update_tool_state(
        "git",
        {
            "managed_by": "ghdp",
            "ownership": {
                "schema_version": "1.0",
                "override_owner": "user",
                "override_source": "command:tools ownership set",
                "override_updated_at": "2026-04-19T00:00:00",
            },
        },
    )

    clear_result = runner.invoke(app, ["tools", "ownership", "clear", "--tool", "git"])

    assert clear_result.exit_code == 0
    assert "owner='ghdp'" in clear_result.output
    assert "owner_source='managed'" in clear_result.output
    assert "policy_source='packaged'" in clear_result.output
    assert "resolution_source='legacy_ghdp'" in clear_result.output
    assert get_tool_state("git")["ownership"]["override_owner"] == ""


def test_tools_status_keeps_ownership_summary_compact(isolated_home, monkeypatch):
    spec = _spec("git", allow_user_override=True)
    _tool_command_env(monkeypatch, spec)
    set_tool_ownership_override("git", spec.ownership_policy, "user", source="test:status-override")
    monkeypatch.setattr(tools_cmd, "detect_tool", lambda _spec: (True, "2.0.0"))

    result = runner.invoke(app, ["tools", "status", "--refresh"])

    assert result.exit_code == 0
    assert "owner='user'" in result.output
    assert "owner_source='override'" in result.output
    assert "policy_source=" not in result.output
    assert "resolution_source=" not in result.output


def test_tools_ownership_list_exposes_policy_provenance(isolated_home, monkeypatch):
    spec = _spec("git", allow_user_override=True)
    _tool_command_env(monkeypatch, spec)
    set_tool_ownership_override("git", spec.ownership_policy, "user", source="test:list-override")

    result = runner.invoke(app, ["tools", "ownership", "list"])

    assert result.exit_code == 0
    assert "owner='user'" in result.output
    assert "owner_source='override'" in result.output
    assert "policy_source='packaged'" in result.output
    assert "resolution_source='override'" in result.output
    assert "user_override_allowed=yes" in result.output
    assert "override='user'" in result.output


def test_policy_revocation_clears_legacy_user_override(isolated_home):
    spec = _spec("git", allow_user_override=False)
    update_tool_state("git", {"managed_by": "user", "ownership": {"schema_version": "1.0", "override_owner": "", "override_source": ""}})

    resolution = reconcile_tool_ownership("git", spec.ownership_policy)
    st = get_tool_state("git")

    assert resolution.effective_owner == "ghdp"
    assert resolution.effective_source == "legacy_revoked"
    assert st["managed_by"] == "ghdp"
    assert st["ownership"]["override_owner"] == ""
    assert st["ownership"]["override_last_invalid_owner"] == "user"
