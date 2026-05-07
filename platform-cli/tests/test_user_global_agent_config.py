from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from platform_cli.cli import app
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.tools.user_global_agent_config import (
    MANAGED_BLOCK_BEGIN,
    MANAGED_BLOCK_END,
    sync_user_global_agent_config,
)

runner = CliRunner()


def _set_home(monkeypatch, home: Path) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    cli_ctx.non_interactive = False
    cli_ctx.verbose = False
    cli_ctx.quiet = False
    cli_ctx.json = False


def test_sync_user_global_agent_config_creates_missing_file(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)

    result = sync_user_global_agent_config("claude")

    target = tmp_path / ".claude" / "CLAUDE.md"
    assert result.action == "created"
    assert target.exists()
    text = target.read_text(encoding="utf-8")
    assert MANAGED_BLOCK_BEGIN in text
    assert "## GHDP Global Rules" in text
    assert MANAGED_BLOCK_END in text


def test_sync_user_global_agent_config_appends_when_no_markers_exist(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    target = tmp_path / ".codex" / "AGENTS.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Existing Notes\n\nKeep this text.\n", encoding="utf-8")

    result = sync_user_global_agent_config("codex")

    text = target.read_text(encoding="utf-8")
    assert result.action == "appended"
    assert text.startswith("# Existing Notes")
    assert MANAGED_BLOCK_BEGIN in text
    assert "## GHDP Global Rules" in text


def test_sync_user_global_agent_config_replaces_only_managed_block(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    target = tmp_path / ".claude" / "CLAUDE.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(
            [
                "# Personal Notes",
                "",
                MANAGED_BLOCK_BEGIN,
                "generated_by: old",
                "",
                "stale managed text",
                MANAGED_BLOCK_END,
                "",
                "## User Notes",
                "",
                "keep me",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = sync_user_global_agent_config("claude")

    text = target.read_text(encoding="utf-8")
    assert result.action == "updated"
    assert "# Personal Notes" in text
    assert "## User Notes" in text
    assert "keep me" in text
    assert "stale managed text" not in text
    assert text.count(MANAGED_BLOCK_BEGIN) == 1
    assert "## GHDP Global Rules" in text


def test_tools_setup_agent_config_command_writes_selected_tool(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)

    res = runner.invoke(app, ["tools", "setup-agent-config", "--tool", "codex"])

    assert res.exit_code == 0
    assert "codex: created" in res.output
    assert (tmp_path / ".codex" / "AGENTS.md").exists()
    assert not (tmp_path / ".claude" / "CLAUDE.md").exists()


def test_tools_setup_agent_config_command_adopts_preexisting_claude_file(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    target = tmp_path / ".claude" / "CLAUDE.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Existing Notes\n\nKeep this text.\n", encoding="utf-8")

    res = runner.invoke(app, ["tools", "setup-agent-config", "--tool", "claude"])

    assert res.exit_code == 0
    assert "claude: appended" in res.output
    text = target.read_text(encoding="utf-8")
    assert text.startswith("# Existing Notes")
    assert "Keep this text." in text
    assert text.count(MANAGED_BLOCK_BEGIN) == 1
    assert "## GHDP Global Rules" in text
