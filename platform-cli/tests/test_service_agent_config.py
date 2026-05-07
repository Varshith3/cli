from __future__ import annotations

from pathlib import Path

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.tools.service import ToolOnboardingStatus, ToolRuntimeSpec, install_tool
from platform_cli.tools.user_global_agent_config import MANAGED_BLOCK_BEGIN


def _spec(name: str) -> ToolRuntimeSpec:
    return ToolRuntimeSpec(
        name=name,
        display_name=name.upper(),
        detect_cmd=["detect"],
        version_cmd=["version"],
        install_cmd=["install"],
        upgrade_cmd=None,
        uninstall_cmd=["uninstall"],
        version_req=None,
    )


def _set_home(monkeypatch, home: Path) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    cli_ctx.non_interactive = True
    cli_ctx.verbose = False
    cli_ctx.quiet = False
    cli_ctx.json = False


def test_install_tool_runs_global_agent_config_post_step_for_supported_tools(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    calls: list[str] = []

    monkeypatch.setattr("platform_cli.tools.service.detect_tool", lambda spec: (True, "1.0.0"))
    monkeypatch.setattr(
        "platform_cli.tools.service._run_codex_post_step",
        lambda spec: (calls.append("codex_post") or ToolOnboardingStatus(spec.name, spec.display_name, "ready", "Installed and ready")),
    )
    monkeypatch.setattr(
        "platform_cli.tools.service._run_claude_post_step",
        lambda spec: (calls.append("claude_post") or ToolOnboardingStatus(spec.name, spec.display_name, "ready", "Installed and ready")),
    )
    monkeypatch.setattr(
        "platform_cli.tools.service._run_agent_config_post_step",
        lambda tool: (calls.append(f"{tool}_config") or ToolOnboardingStatus(tool, tool.upper(), "ready", "Global agent config ready")),
    )

    install_tool(_spec("codex"), dry_run=False, upgrade=False, adopt_existing=False)
    install_tool(_spec("claude"), dry_run=False, upgrade=False, adopt_existing=False)

    assert calls == ["codex_post", "codex_config", "claude_post", "claude_config"]


def test_install_tool_claude_adopts_preexisting_claude_md_without_overwriting_user_content(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _set_home(monkeypatch, tmp_path)
    target = tmp_path / ".claude" / "CLAUDE.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Personal Notes\n\nKeep this text.\n", encoding="utf-8")

    monkeypatch.setattr("platform_cli.tools.service.detect_tool", lambda spec: (True, "1.0.0"))
    monkeypatch.setattr(
        "platform_cli.tools.service._run_claude_post_step",
        lambda spec: ToolOnboardingStatus(spec.name, spec.display_name, "ready", "Installed and ready"),
    )

    install_tool(_spec("claude"), dry_run=False, upgrade=False, adopt_existing=False)

    text = target.read_text(encoding="utf-8")
    assert text.startswith("# Personal Notes")
    assert "Keep this text." in text
    assert text.count(MANAGED_BLOCK_BEGIN) == 1
    assert "## GHDP Global Rules" in text


def test_install_tool_claude_updates_existing_managed_block_in_place(
    tmp_path: Path,
    monkeypatch,
) -> None:
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
                "<!-- GHDP:END GLOBAL RULES -->",
                "",
                "## User Notes",
                "",
                "keep me",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("platform_cli.tools.service.detect_tool", lambda spec: (True, "1.0.0"))
    monkeypatch.setattr(
        "platform_cli.tools.service._run_claude_post_step",
        lambda spec: ToolOnboardingStatus(spec.name, spec.display_name, "ready", "Installed and ready"),
    )

    install_tool(_spec("claude"), dry_run=False, upgrade=False, adopt_existing=False)

    text = target.read_text(encoding="utf-8")
    assert "# Personal Notes" in text
    assert "## User Notes" in text
    assert "keep me" in text
    assert "stale managed text" not in text
    assert text.count(MANAGED_BLOCK_BEGIN) == 1
    assert "## GHDP Global Rules" in text
