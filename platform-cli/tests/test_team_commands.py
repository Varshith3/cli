from __future__ import annotations

from typer.testing import CliRunner

from platform_cli.cli import app
from platform_cli.core.config import get_value, set_value


runner = CliRunner()


def _toolset() -> dict:
    return {
        "schema_version": "0.0.1",
        "teams": {
            "data_ops": {"tools": {"git": {"op": ">=", "version": "2.0.0"}}},
            "platform": {"tools": {"gh": {"op": ">=", "version": "2.0.0"}}},
        },
    }


def test_team_current_reports_stale_selection_and_fallback_notice(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    set_value("team.selected", "legacy")
    monkeypatch.setattr("platform_cli.commands.team.ensure_team_toolset_synced", lambda: {"local_status": "none"})
    monkeypatch.setattr(
        "platform_cli.commands.team.load_manifests",
        lambda: (
            _toolset(),
            {},
            {
                "toolset": "pkg:platform_cli/resources/manifests/toolset.json",
                "registry": "pkg:platform_cli/resources/manifests/tool-registry.json",
            },
        ),
    )
    monkeypatch.setattr("platform_cli.commands.team.resolve_effective_team_name", lambda: "")

    res = runner.invoke(app, ["team", "current"])

    assert res.exit_code == 0
    assert "managed synced team toolset is not active" in res.output
    assert "Saved team 'legacy' is no longer available" in res.output
    assert "ghdp team use --team <name>" in res.output


def test_team_list_reports_active_session_and_stale_selection(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    set_value("team.selected", "legacy")
    monkeypatch.setattr("platform_cli.commands.team.ensure_team_toolset_synced", lambda: {"local_status": "none"})
    monkeypatch.setattr(
        "platform_cli.commands.team.load_manifests",
        lambda: (
            _toolset(),
            {},
            {
                "toolset": "managed:/tmp/team-toolset.managed.json",
                "registry": "pkg:platform_cli/resources/manifests/tool-registry.json",
            },
        ),
    )
    monkeypatch.setattr("platform_cli.commands.team.resolve_effective_team_name", lambda: "platform")

    res = runner.invoke(app, ["team", "list"])

    assert res.exit_code == 0
    assert "* platform (active session)" in res.output
    assert "Saved team 'legacy' is no longer available" in res.output


def test_team_use_reselects_after_stale_selection_and_uses_fallback_notice(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    set_value("team.selected", "legacy")
    monkeypatch.setattr("platform_cli.commands.team.ensure_team_toolset_synced", lambda: {"local_status": "none"})
    monkeypatch.setattr(
        "platform_cli.commands.team.load_manifests",
        lambda: (
            _toolset(),
            {},
            {
                "toolset": "pkg:platform_cli/resources/manifests/toolset.json",
                "registry": "pkg:platform_cli/resources/manifests/tool-registry.json",
            },
        ),
    )

    res = runner.invoke(app, ["team", "use", "--team", "platform"])

    assert res.exit_code == 0
    assert "managed synced team toolset is not active" in res.output
    assert "Saved team 'legacy' is no longer available" in res.output
    assert "Saved team: platform" in res.output
    assert get_value("team.selected") == "platform"


def test_team_list_runs_team_toolset_sync_pre_hook(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    called: list[bool] = []
    monkeypatch.setattr("platform_cli.commands.team.ensure_team_toolset_synced", lambda: called.append(True) or {"local_status": "none"})
    monkeypatch.setattr(
        "platform_cli.commands.team.load_manifests",
        lambda: (
            _toolset(),
            {},
            {
                "toolset": "managed:/tmp/team-toolset.managed.json",
                "registry": "pkg:platform_cli/resources/manifests/tool-registry.json",
            },
        ),
    )
    monkeypatch.setattr("platform_cli.commands.team.resolve_effective_team_name", lambda: "data_ops")

    res = runner.invoke(app, ["team", "list"])

    assert res.exit_code == 0
    assert called == [True]
