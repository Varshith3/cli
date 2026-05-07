from __future__ import annotations

from typer.testing import CliRunner

from platform_cli.cli import app
from platform_cli.state.store import update_tool_state
from platform_cli.tools.service import ToolRuntimeSpec


runner = CliRunner()


def _spec(name: str = "gh") -> ToolRuntimeSpec:
    return ToolRuntimeSpec(
        name=name,
        display_name=name.upper(),
        detect_cmd=["detect"],
        version_cmd=["version"],
        install_cmd=["install"],
        upgrade_cmd=["upgrade"],
        uninstall_cmd=["uninstall"],
        version_req=None,
    )


def test_tools_list_uses_cached_team_toolset_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    calls: list[bool] = []
    monkeypatch.setattr(
        "platform_cli.commands.tools.ensure_team_toolset_available",
        lambda force_refresh=False: calls.append(force_refresh) or {"local_status": "cached"},
    )
    monkeypatch.setattr(
        "platform_cli.commands.tools.load_manifests",
        lambda: (
            {"schema_version": "0.0.1", "teams": {"data_platform": {"tools": {}}}},
            {"schema_version": "1.0", "tools": {}},
            {
                "toolset": "managed:/tmp/team-toolset.managed.json",
                "registry": "pkg:platform_cli/resources/manifests/tool-registry.json",
            },
        ),
    )
    monkeypatch.setattr("platform_cli.commands.tools._resolve_effective_team", lambda _toolset, _team: "data_platform")
    monkeypatch.setattr("platform_cli.commands.tools.resolve_team_tools", lambda *_args, **_kwargs: [])

    res = runner.invoke(app, ["tools", "list", "--team", "data_platform"])

    assert res.exit_code == 0
    assert calls == [False]
    assert "Using cached team toolset." in res.output


def test_tools_list_refresh_toolset_forces_sync(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    calls: list[bool] = []
    monkeypatch.setattr(
        "platform_cli.commands.tools.ensure_team_toolset_available",
        lambda force_refresh=False: calls.append(force_refresh) or {"local_status": "synced"},
    )
    monkeypatch.setattr(
        "platform_cli.commands.tools.load_manifests",
        lambda: (
            {"schema_version": "0.0.1", "teams": {"data_platform": {"tools": {}}}},
            {"schema_version": "1.0", "tools": {}},
            {
                "toolset": "managed:/tmp/team-toolset.managed.json",
                "registry": "pkg:platform_cli/resources/manifests/tool-registry.json",
            },
        ),
    )
    monkeypatch.setattr("platform_cli.commands.tools._resolve_effective_team", lambda _toolset, _team: "data_platform")
    monkeypatch.setattr("platform_cli.commands.tools.resolve_team_tools", lambda *_args, **_kwargs: [])

    res = runner.invoke(app, ["tools", "list", "--team", "data_platform", "--refresh-toolset"])

    assert res.exit_code == 0
    assert calls == [True]
    assert "Refreshed team toolset before running." in res.output


def test_tools_install_access_check_does_not_prompt_for_github_login(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    interactive_flags: list[bool] = []

    monkeypatch.setattr(
        "platform_cli.core.access.evaluate_capability_requirement",
        lambda capability, team=None, command_name=None, interactive=True: (
            interactive_flags.append(bool(interactive))
            or type(
                "Decision",
                (),
                {
                    "status": "allowed",
                    "message": "",
                    "code": "",
                    "reason": capability,
                },
            )()
        ),
    )
    monkeypatch.setattr(
        "platform_cli.commands.tools._load_manifests_with_team_toolset_resolution",
        lambda refresh_toolset=False: (
            {"schema_version": "0.0.1", "teams": {"data_platform": {"tools": {}}}},
            {"schema_version": "1.0", "tools": {}},
            {
                "toolset": "pkg:platform_cli/resources/manifests/toolset.json",
                "registry": "pkg:platform_cli/resources/manifests/tool-registry.json",
            },
            {"local_status": "fallback"},
        ),
    )
    monkeypatch.setattr("platform_cli.commands.tools._resolve_effective_team", lambda _toolset, _team: "data_platform")
    monkeypatch.setattr("platform_cli.commands.tools.resolve_team_tools", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "platform_cli.commands.tools.scheduler_tools.scheduler_initialization_status",
        lambda **_kwargs: {"supported": True, "initialized": True},
    )

    res = runner.invoke(app, ["tools", "install", "--team", "data_platform"])

    assert res.exit_code == 1
    assert interactive_flags == [False]


def test_tools_status_surfaces_detection_classification(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    spec = _spec("gh")

    monkeypatch.setattr(
        "platform_cli.commands.tools.ensure_team_toolset_available",
        lambda force_refresh=False: {"local_status": "current", "sync_result": {}},
    )
    monkeypatch.setattr(
        "platform_cli.commands.tools.load_manifests",
        lambda: (
            {"schema_version": "0.0.1", "teams": {"data_platform": {"tools": {"gh": {}}}}},
            {"schema_version": "1.0", "tools": {"gh": {}}},
            {
                "toolset": "managed:/tmp/team-toolset.managed.json",
                "registry": "pkg:platform_cli/resources/manifests/tool-registry.json",
            },
        ),
    )
    monkeypatch.setattr("platform_cli.commands.tools._resolve_effective_team", lambda _toolset, _team: "data_platform")
    monkeypatch.setattr("platform_cli.commands.tools.resolve_team_tools", lambda *_args, **_kwargs: [spec])

    def _fake_detect(_spec: ToolRuntimeSpec):
        update_tool_state(
            "gh",
            {
                "detection_status": "version_check_failed",
                "detection_error_code": "E_TOOL_VERSION_CHECK_FAILED",
                "managed_version": "",
                "active_path": "/usr/local/bin/gh",
                "active_version": "2.90.0",
            },
        )
        return True, "2.90.0"

    monkeypatch.setattr("platform_cli.commands.tools.detect_tool", _fake_detect)

    res = runner.invoke(app, ["tools", "status", "--team", "data_platform", "--refresh"])

    assert res.exit_code == 0
    assert "detect='version_check_failed'" in res.output
    assert "detect_code='E_TOOL_VERSION_CHECK_FAILED'" in res.output


def test_tools_install_returns_nonzero_when_summary_reports_failures(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(
        "platform_cli.commands.tools._load_manifests_with_team_toolset_resolution",
        lambda refresh_toolset=False: (
            {"schema_version": "0.0.1", "teams": {"data_platform": {"tools": {}}}},
            {"schema_version": "1.0", "tools": {}},
            {
                "toolset": "managed:/tmp/team-toolset.managed.json",
                "registry": "pkg:platform_cli/resources/manifests/tool-registry.json",
            },
            {"local_status": "current"},
        ),
    )
    monkeypatch.setattr(
        "platform_cli.commands.tools._resolve_effective_team",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(Exception("team missing")),
    )
    monkeypatch.setattr("platform_cli.commands.tools.build_tool_runtime_spec", lambda *_args, **_kwargs: None)

    res = runner.invoke(app, ["--non-interactive", "tools", "install", "--team", "not_a_team", "--dry-run"])

    assert res.exit_code == 1
    assert "install finished with failures" in res.output
