from __future__ import annotations

import importlib.resources as pkg_resources
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

from platform_cli.core.errors import PlatformError

MANAGED_BLOCK_BEGIN = "<!-- GHDP:BEGIN GLOBAL RULES -->"
MANAGED_BLOCK_END = "<!-- GHDP:END GLOBAL RULES -->"
VALID_AGENT_TOOLS = {"claude", "codex"}


@dataclass(frozen=True)
class AgentConfigTarget:
    tool: str
    relative_resource_path: str
    target_path: Path


@dataclass
class AgentConfigSyncResult:
    tool: str
    path: str
    action: str
    messages: List[str] = field(default_factory=list)


def _target(tool: str) -> AgentConfigTarget:
    normalized = str(tool or "").strip().lower()
    if normalized not in VALID_AGENT_TOOLS:
        raise PlatformError(
            f"Unsupported agent-config tool '{tool}'. Use one of: claude, codex.",
            code="E_AGENT_CONFIG_TOOL_INVALID",
            reason="tool",
        )

    home = Path.home()
    if normalized == "claude":
        return AgentConfigTarget(
            tool="claude",
            relative_resource_path="user_global/claude/CLAUDE.md",
            target_path=home / ".claude" / "CLAUDE.md",
        )
    return AgentConfigTarget(
        tool="codex",
        relative_resource_path="user_global/codex/AGENTS.md",
        target_path=home / ".codex" / "AGENTS.md",
    )


def _load_template(relative_resource_path: str) -> str:
    try:
        target = pkg_resources.files("platform_cli.resources") / relative_resource_path
        return target.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        raise PlatformError(
            f"Agent-config template missing: {relative_resource_path}",
            code="E_AGENT_CONFIG_TEMPLATE_MISSING",
            reason=relative_resource_path,
        )
    except ModuleNotFoundError:
        raise PlatformError(
            "Packaged agent-config resources are missing.",
            code="E_AGENT_CONFIG_TEMPLATE_MISSING",
            reason="platform_cli.resources.user_global",
        )


def _render_managed_block(tool: str, body: str) -> str:
    title = "CLAUDE.md" if tool == "claude" else "AGENTS.md"
    return (
        f"{MANAGED_BLOCK_BEGIN}\n"
        f"generated_by: ghdp\n"
        f"target: {title}\n"
        f"warning: Do not edit this managed block by hand.\n"
        f"\n"
        f"{body.strip()}\n"
        f"{MANAGED_BLOCK_END}\n"
    )


def _replace_or_append_managed_block(existing_text: str, managed_block: str) -> tuple[str, str]:
    pattern = re.compile(
        rf"{re.escape(MANAGED_BLOCK_BEGIN)}.*?{re.escape(MANAGED_BLOCK_END)}",
        re.DOTALL,
    )
    match = pattern.search(existing_text)
    if match:
        updated = existing_text[: match.start()] + managed_block.rstrip() + existing_text[match.end() :]
        return updated.rstrip() + "\n", "updated"

    if existing_text.strip():
        joined = existing_text.rstrip() + "\n\n" + managed_block.rstrip() + "\n"
        return joined, "appended"
    return managed_block, "created"


def sync_user_global_agent_config(tool: str) -> AgentConfigSyncResult:
    target = _target(tool)
    managed_block = _render_managed_block(
        target.tool,
        _load_template(target.relative_resource_path),
    )

    target.target_path.parent.mkdir(parents=True, exist_ok=True)

    if not target.target_path.exists():
        target.target_path.write_text(managed_block, encoding="utf-8")
        return AgentConfigSyncResult(
            tool=target.tool,
            path=str(target.target_path),
            action="created",
        )

    existing_text = target.target_path.read_text(encoding="utf-8")
    updated_text, action = _replace_or_append_managed_block(existing_text, managed_block)
    if updated_text == existing_text:
        return AgentConfigSyncResult(
            tool=target.tool,
            path=str(target.target_path),
            action="unchanged",
        )

    target.target_path.write_text(updated_text, encoding="utf-8")
    return AgentConfigSyncResult(
        tool=target.tool,
        path=str(target.target_path),
        action=action,
    )


def sync_user_global_agent_configs(*, tools: List[str]) -> List[AgentConfigSyncResult]:
    results: List[AgentConfigSyncResult] = []
    for tool in tools:
        results.append(sync_user_global_agent_config(tool))
    return results
