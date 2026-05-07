from __future__ import annotations

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from platform_cli.cli import app
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.state.store import get_tool_state
from platform_cli.tools.ownership import build_ownership_policy, set_tool_ownership_override
from platform_cli.tools.service import ToolRuntimeSpec, detect_tool, detect_tool_details, install_tool, uninstall_tool

try:
    from platform_cli.core.errors import PlatformError
except Exception:  # pragma: no cover
    from platform_cli.core.errors import PlatformError  # type: ignore


runner = CliRunner()


class RunStub:
    """Tiny run_cmd stub for ownership-focused service tests."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.detect_ok = True
        self.version_stdout = "2.0.0"
        self.after_uninstall_detect_ok = False
        self._uninstalled = False

    def __call__(self, cmd, check=True, **_kwargs):
        self.calls.append(list(cmd))

        if cmd[:1] == ["detect"]:
            if self._uninstalled:
                if self.after_uninstall_detect_ok:
                    return SimpleNamespace(stdout="")
                raise RuntimeError("not installed")
            if self.detect_ok:
                return SimpleNamespace(stdout="")
            raise RuntimeError("not installed")

        if cmd[:1] == ["version"]:
            return SimpleNamespace(stdout=self.version_stdout)

        if cmd[:1] == ["uninstall"]:
            self._uninstalled = True
            return SimpleNamespace(stdout="")

        if cmd[:1] in (["install"], ["upgrade"]):
            return SimpleNamespace(stdout="")

        return SimpleNamespace(stdout="")


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    cli_ctx.non_interactive = False
    cli_ctx.verbose = False
    cli_ctx.quiet = False
    cli_ctx.json = False
    return tmp_path


def _spec(
    name: str = "git",
    *,
    owner: str = "ghdp",
    allow_user_override: bool = False,
    source: str = "pkg:platform_cli/resources/manifests/toolset.json",
) -> ToolRuntimeSpec:
    policy = build_ownership_policy(
        {
            "op": ">=",
            "version": "1.0.0",
            "ownership": {
                "default_owner": owner,
                "allow_user_override": allow_user_override,
            },
        },
        source,
    )
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


def test_detect_marks_preinstalled_as_ghdp_managed_under_trusted_policy(isolated_home, monkeypatch):
    stub = RunStub()
    monkeypatch.setattr("platform_cli.tools.service.run_cmd", stub)

    installed, ver = detect_tool(_spec("git", allow_user_override=True))

    assert installed is True
    assert ver == "2.0.0"
    st = get_tool_state("git")
    assert st["detected"] is True
    assert st["managed_by"] == "ghdp"
    assert st["ownership"]["effective_source"] == "policy_default"
    assert st["ownership"]["policy_trusted_source"] is True


def test_detect_tool_details_classifies_version_check_failure(isolated_home, monkeypatch):
    def _run(cmd, check=True, **_kwargs):
        if cmd[:1] == ["detect"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if cmd[:1] == ["version"]:
            return SimpleNamespace(stdout="", stderr="version probe failed", returncode=1)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("platform_cli.tools.service.run_cmd", _run)
    monkeypatch.setattr("platform_cli.tools.service._active_path_and_version", lambda _spec: ("", ""))

    result = detect_tool_details(_spec("git", allow_user_override=True))

    assert result.status == "version_check_failed"
    assert result.code == "E_TOOL_VERSION_CHECK_FAILED"
    assert get_tool_state("git")["detection_status"] == "version_check_failed"


def test_detect_tool_details_classifies_path_only_presence_as_ambiguous(isolated_home, monkeypatch):
    monkeypatch.setattr(
        "platform_cli.tools.service.run_cmd",
        lambda cmd, check=True, **_kwargs: (_ for _ in ()).throw(
            PlatformError("detect failed", code="E_CMD_FAILED", reason="nonzero_exit")
        ),
    )
    monkeypatch.setattr("platform_cli.tools.service._active_path_and_version", lambda _spec: ("/usr/local/bin/git", "2.0.0"))

    result = detect_tool_details(_spec("git", allow_user_override=True))

    assert result.status == "detection_ambiguous"
    assert result.installed_any is True
    assert get_tool_state("git")["detection_status"] == "detection_ambiguous"


def test_install_skips_existing_ghdp_managed_tool_without_prompt(isolated_home, monkeypatch):
    stub = RunStub()
    monkeypatch.setattr("platform_cli.tools.service.run_cmd", stub)
    monkeypatch.setattr(
        "platform_cli.tools.service.typer.confirm",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("install should not prompt")),
    )

    install_tool(_spec("gh", allow_user_override=True), dry_run=False, upgrade=False, adopt_existing=False)

    assert ["install"] not in stub.calls
    st = get_tool_state("gh")
    assert st["managed_by"] == "ghdp"
    assert st["last_status"] == "skipped"
    assert st["reason"] == "already_managed"


def test_user_override_blocks_upgrade_and_uninstall(isolated_home, monkeypatch):
    stub = RunStub()
    monkeypatch.setattr("platform_cli.tools.service.run_cmd", stub)

    spec = _spec("vscode", allow_user_override=True)
    set_tool_ownership_override(spec.name, spec.ownership_policy, "user", source="test:user-override")

    install_tool(spec, dry_run=False, upgrade=True, adopt_existing=False)
    assert ["upgrade"] not in stub.calls
    st = get_tool_state("vscode")
    assert st["last_action"] == "upgrade"
    assert st["last_status"] == "skipped"
    assert st["reason"] == "ownership_user_managed"

    with pytest.raises(PlatformError) as exc:
        uninstall_tool(spec, dry_run=False, force=False)

    assert exc.value.code == "E_UNINSTALL_NOT_GHDP_MANAGED"


def test_commands_discover_release_aliases() -> None:
    res = runner.invoke(app, ["commands", "--category", "release"])

    assert res.exit_code == 0
    assert "release feature-to-dev" in res.output
    assert "ftd" in res.output
    assert "release make-release" in res.output
    assert "mr" in res.output
