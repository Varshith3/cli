from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from platform_cli.cli import app
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.config import get_value


runner = CliRunner()


def test_config_claude_athena_workgroup_show_set_and_clear(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    cli_ctx.non_interactive = True
    sync_calls: list[str] = []
    monkeypatch.setattr(
        "platform_cli.commands.config_cli.sync_saved_claude_workgroup_runtime",
        lambda workgroup: (sync_calls.append(workgroup) or (tmp_path / ".zshrc")),
    )

    show_empty = runner.invoke(app, ["config", "claude-athena-workgroup"])
    assert show_empty.exit_code == 0
    assert "not configured" in show_empty.output

    set_result = runner.invoke(app, ["config", "claude-athena-workgroup", "--value", "wg-team"])
    assert set_result.exit_code == 0
    assert get_value("claude.athena_workgroup", "") == "wg-team"
    assert sync_calls[-1] == "wg-team"

    show_value = runner.invoke(app, ["config", "claude-athena-workgroup"])
    assert show_value.exit_code == 0
    assert "wg-team" in show_value.output

    clear_result = runner.invoke(app, ["config", "claude-athena-workgroup", "--clear"])
    assert clear_result.exit_code == 0
    assert get_value("claude.athena_workgroup", "") == ""
    assert sync_calls[-1] == ""


def test_config_claude_athena_workgroup_rejects_conflicting_flags(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    cli_ctx.non_interactive = True

    result = runner.invoke(
        app,
        ["config", "claude-athena-workgroup", "--value", "wg-team", "--clear"],
    )

    assert result.exit_code == 1
    assert "Use either --value or --clear" in str(result.exception)
