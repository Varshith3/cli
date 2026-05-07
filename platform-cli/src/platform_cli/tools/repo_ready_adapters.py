from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

from platform_cli.core.errors import PlatformError
from platform_cli.core.progress import GenerationProgressReporter
from platform_cli.exec.runner import run_cmd
from platform_cli.manifests.repo_ready_load import load_repo_ready_prompt, load_repo_ready_template, load_repo_ready_yaml_file
from platform_cli.tools.ai_provider import ProviderStatus, detect_provider_statuses, generate_text
from platform_cli.tools.repo_ready_assets import ensure_repo_ready_assets_synced

ADAPTER_TEMPLATE_VERSION = "1.0.0"
ADAPTER_STATUS_PLACEHOLDER = "scaffolded_placeholder"
ADAPTER_STATUS_DRAFT = "draft_generated_review_required"
ADAPTER_STATUS_READY = "ready"
CLAUDE_ADAPTER_REL_PATH = "CLAUDE.md"
AGENTS_ADAPTER_REL_PATH = "AGENTS.md"
CLAUDE_SETTINGS_REL_PATH = ".claude/settings.json"
CODEX_CONFIG_REL_PATH = ".codex/config.toml"
MCP_CONFIG_REL_PATH = ".mcp.json"
MANAGED_BLOCK_BEGIN = "<!-- GHDP:BEGIN MANAGED BLOCK -->"
MANAGED_BLOCK_END = "<!-- GHDP:END MANAGED BLOCK -->"

_CANONICAL_REL_PATHS = (
    ".ghdp/readiness.json",
    ".ghdp/architecture.md",
    ".ghdp/runbook.yaml",
    ".ghdp/config.yaml",
    ".ghdp/guardrails.yaml",
    ".ghdp/lock.yaml",
    ".ghdp/frbr/intent.json",
)
_PROMPT_FILE_BY_TARGET = {
    CLAUDE_ADAPTER_REL_PATH: "claude_md_generation.md",
    AGENTS_ADAPTER_REL_PATH: "agents_md_generation.md",
}
_NATIVE_PROVIDER_BY_TARGET = {
    CLAUDE_ADAPTER_REL_PATH: "claude",
    AGENTS_ADAPTER_REL_PATH: "codex",
}
_MARKDOWN_TARGETS = (CLAUDE_ADAPTER_REL_PATH, AGENTS_ADAPTER_REL_PATH)
_ADAPTER_STATUS_RE = re.compile(r"adapter_status:\s*\"?(?P<status>[a-z_]+)\"?")


@dataclass(frozen=True)
class RepoReadyAdapterResult:
    rel_path: str
    state: str
    exists: bool
    required_by_enabled_tools: bool
    template_version: str = ADAPTER_TEMPLATE_VERSION
    messages: List[str] = field(default_factory=list)


@dataclass
class RepoReadyAdapterSyncResult:
    generated: List[str] = field(default_factory=list)
    updated: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def current_branch_name(repo_root: Path) -> str:
    result = run_cmd(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        check=False,
        cwd=repo_root,
    )
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def _load_json_object(path: Path) -> Dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _enabled_tools(repo_root: Path) -> List[str]:
    config_path = repo_root / ".ghdp/config.yaml"
    if not config_path.exists():
        return []
    try:
        payload = load_repo_ready_yaml_file(config_path)
    except Exception:
        return []
    enabled = payload.get("enabled", {})
    tools = enabled.get("tools", []) if isinstance(enabled, dict) else []
    return [tool for tool in tools if isinstance(tool, str)]


def _guardrails_mcp_allowlist(repo_root: Path) -> List[str]:
    guardrails_path = repo_root / ".ghdp/guardrails.yaml"
    if not guardrails_path.exists():
        return []
    try:
        payload = load_repo_ready_yaml_file(guardrails_path)
    except Exception:
        return []
    mcp = payload.get("mcp", {})
    allow = mcp.get("allow", []) if isinstance(mcp, dict) else []
    return [item for item in allow if isinstance(item, str)]


def _existing_mcp_sources(repo_root: Path) -> Dict[str, object]:
    candidates = [
        repo_root / ".mcp.json",
        repo_root / ".vscode/mcp.json",
    ]
    for candidate in candidates:
        payload = _load_json_object(candidate)
        servers = payload.get("servers")
        if isinstance(servers, dict):
            return servers
    return {}


def _required_by_enabled_tools(repo_root: Path) -> Dict[str, bool]:
    enabled = set(_enabled_tools(repo_root))
    has_guarded_mcp = bool(_guardrails_mcp_allowlist(repo_root))
    return {
        CLAUDE_ADAPTER_REL_PATH: "claude" in enabled,
        CLAUDE_SETTINGS_REL_PATH: "claude" in enabled,
        MCP_CONFIG_REL_PATH: "claude" in enabled or has_guarded_mcp,
        AGENTS_ADAPTER_REL_PATH: "codex" in enabled,
        CODEX_CONFIG_REL_PATH: "codex" in enabled,
    }


def _detect_markdown_state(path: Path) -> str:
    if not path.exists():
        return "missing"
    text = path.read_text(encoding="utf-8")
    match = _ADAPTER_STATUS_RE.search(text)
    if not match:
        return ADAPTER_STATUS_READY
    status = match.group("status")
    if status in {ADAPTER_STATUS_PLACEHOLDER, ADAPTER_STATUS_DRAFT, ADAPTER_STATUS_READY}:
        return status
    return ADAPTER_STATUS_READY


def inspect_repo_local_adapters(repo_root: Path) -> tuple[List[RepoReadyAdapterResult], List[str]]:
    required = _required_by_enabled_tools(repo_root)
    adapter_results: List[RepoReadyAdapterResult] = []
    warnings: List[str] = []

    for rel_path in (
        CLAUDE_ADAPTER_REL_PATH,
        AGENTS_ADAPTER_REL_PATH,
        CLAUDE_SETTINGS_REL_PATH,
        CODEX_CONFIG_REL_PATH,
        MCP_CONFIG_REL_PATH,
    ):
        path = repo_root / rel_path
        if rel_path in _MARKDOWN_TARGETS:
            state = _detect_markdown_state(path)
        else:
            state = ADAPTER_STATUS_READY if path.exists() else "missing"

        messages: List[str] = []
        if state == "missing":
            messages.append("Adapter file has not been generated yet.")
            warnings.append(f"Adapter missing: {rel_path}")
        elif state == ADAPTER_STATUS_PLACEHOLDER:
            messages.append("Adapter is still a GHDP placeholder.")
            warnings.append(f"Adapter placeholder still needs completion: {rel_path}")
        elif state == ADAPTER_STATUS_DRAFT:
            messages.append("Adapter draft still needs user review.")
            warnings.append(f"Adapter draft still needs review: {rel_path}")

        adapter_results.append(
            RepoReadyAdapterResult(
                rel_path=rel_path,
                state=state,
                exists=path.exists(),
                required_by_enabled_tools=required.get(rel_path, False),
                messages=messages,
            )
        )

    return adapter_results, warnings


def _canonical_context_sections(repo_root: Path) -> str:
    parts: List[str] = []
    for rel_path in _CANONICAL_REL_PATHS:
        path = repo_root / rel_path
        if not path.exists():
            parts.append(f"File: {rel_path}\n(missing)")
            continue
        try:
            content = path.read_text(encoding="utf-8").strip()
        except Exception:
            parts.append(f"File: {rel_path}\n(unreadable)")
            continue
        lang = "json" if path.suffix == ".json" else "yaml" if path.suffix in {".yaml", ".yml"} else "markdown"
        parts.append(f"File: {rel_path}\n```{lang}\n{content[:12000]}\n```")
    return "\n\n".join(parts)


def _build_markdown_prompt(*, repo_root: Path, rel_path: str) -> str:
    prompt = load_repo_ready_prompt(_PROMPT_FILE_BY_TARGET[rel_path], repo_root=repo_root)
    existing_path = repo_root / rel_path
    branch_name = current_branch_name(repo_root)
    parts = [
        prompt,
        "",
        "Repository context:",
        f"- Repository name: {repo_root.name}",
        f"- Current branch name: {branch_name or '(not resolved)'}",
        f"- Target file: {rel_path}",
    ]
    if existing_path.exists():
        parts.extend(
            [
                "",
                "Existing adapter content:",
                "```markdown",
                existing_path.read_text(encoding='utf-8').strip(),
                "```",
            ]
        )
    parts.extend(
        [
            "",
            "Canonical GHDP context:",
            _canonical_context_sections(repo_root),
            "",
            "Generate the final adapter body now.",
        ]
    )
    return "\n".join(parts).strip() + "\n"


def _render_markdown_adapter(*, repo_root: Path, template_name: str, status: str, body: str) -> str:
    return (
        load_repo_ready_template(f"adapters/{template_name}", repo_root=repo_root)
        .replace("__GHDP_TEMPLATE_VERSION__", ADAPTER_TEMPLATE_VERSION)
        .replace("__GHDP_ADAPTER_STATUS__", status)
        .replace("__GHDP_ADAPTER_BODY__", body.strip())
        .rstrip()
        + "\n"
    )


def _placeholder_body(rel_path: str) -> str:
    heading = "# Claude Code Instructions" if rel_path == CLAUDE_ADAPTER_REL_PATH else "# Agent Instructions"
    tool_name = "Claude Code" if rel_path == CLAUDE_ADAPTER_REL_PATH else "Codex"
    return "\n".join(
        [
            heading,
            "",
            f"This {tool_name} adapter is currently scaffolded as a GHDP placeholder.",
            "",
            "- Canonical source of truth: `.ghdp/*`",
            "- Read in order: `.ghdp/frbr/intent.json` (if present), `.ghdp/readiness.json`, `.ghdp/architecture.md`, `.ghdp/runbook.yaml`, `.ghdp/config.yaml`, `.ghdp/guardrails.yaml`, `.ghdp/lock.yaml`",
            "- Do not invent missing GHDP content.",
            "- Review `.ghdp/readiness.json` before unrelated changes.",
            "- Re-run `ghdp repo ready --fix` once the native AI provider is available to replace this placeholder with a suggested draft.",
        ]
    )


def _write_if_different(path: Path, content: str) -> str:
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    if previous == content:
        return "unchanged"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return "updated" if previous is not None else "generated"


def _render_claude_settings(repo_root: Path) -> str:
    return load_repo_ready_template("adapters/claude_settings.json", repo_root=repo_root).rstrip() + "\n"


def _render_codex_config(repo_root: Path) -> str:
    return load_repo_ready_template("adapters/codex_config.toml", repo_root=repo_root).rstrip() + "\n"


def _render_mcp_config(repo_root: Path) -> str:
    allowlist = set(_guardrails_mcp_allowlist(repo_root))
    source_servers = _existing_mcp_sources(repo_root)
    if allowlist:
        servers = {name: source_servers[name] for name in sorted(allowlist) if name in source_servers}
        payload = {"servers": servers}
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"
    return load_repo_ready_template("adapters/mcp.json", repo_root=repo_root).rstrip() + "\n"


def _write_markdown_placeholder(repo_root: Path, rel_path: str) -> str:
    template_name = rel_path
    content = _render_markdown_adapter(
        repo_root=repo_root,
        template_name=template_name,
        status=ADAPTER_STATUS_PLACEHOLDER,
        body=_placeholder_body(rel_path),
    )
    return _write_if_different(repo_root / rel_path, content)


def _write_markdown_draft(
    *,
    repo_root: Path,
    rel_path: str,
    provider: str,
    statuses: Dict[str, ProviderStatus],
    progress: GenerationProgressReporter | None = None,
) -> str:
    body = generate_text(
        provider=provider,
        statuses=statuses,
        prompt=_build_markdown_prompt(repo_root=repo_root, rel_path=rel_path),
        heartbeat=(progress.heartbeat_callback(rel_path) if progress is not None else None),
    )
    content = _render_markdown_adapter(
        repo_root=repo_root,
        template_name=rel_path,
        status=ADAPTER_STATUS_DRAFT,
        body=body,
    )
    return _write_if_different(repo_root / rel_path, content)


def sync_repo_local_adapters(
    *,
    repo_root: Path,
    statuses: Dict[str, ProviderStatus] | None = None,
    allow_ai: bool,
    progress: GenerationProgressReporter | None = None,
) -> RepoReadyAdapterSyncResult:
    ensure_repo_ready_assets_synced(repo_root)
    result = RepoReadyAdapterSyncResult()
    statuses = statuses or detect_provider_statuses(refresh=False)

    for rel_path in _MARKDOWN_TARGETS:
        target_path = repo_root / rel_path
        current_state = _detect_markdown_state(target_path)
        native_provider = _NATIVE_PROVIDER_BY_TARGET[rel_path]
        native_available = allow_ai and statuses.get(native_provider) and statuses[native_provider].available

        action = "unchanged"
        if current_state == "missing":
            if native_available:
                try:
                    if progress is not None:
                        progress.target_started(rel_path)
                    action = _write_markdown_draft(
                        repo_root=repo_root,
                        rel_path=rel_path,
                        provider=native_provider,
                        statuses=statuses,
                        progress=progress,
                    )
                    if progress is not None and action != "unchanged":
                        progress.target_done(rel_path, outcome=action)
                except PlatformError as exc:
                    if progress is not None:
                        progress.target_failed(rel_path, str(exc))
                    result.warnings.append(
                        f"{rel_path}: {exc} Falling back to a GHDP placeholder."
                    )
                    action = _write_markdown_placeholder(repo_root, rel_path)
            else:
                action = _write_markdown_placeholder(repo_root, rel_path)
        elif current_state == ADAPTER_STATUS_PLACEHOLDER and native_available:
            try:
                if progress is not None:
                    progress.target_started(rel_path)
                action = _write_markdown_draft(
                    repo_root=repo_root,
                    rel_path=rel_path,
                    provider=native_provider,
                    statuses=statuses,
                    progress=progress,
                )
                if progress is not None and action != "unchanged":
                    progress.target_done(rel_path, outcome=action)
            except PlatformError as exc:
                if progress is not None:
                    progress.target_failed(rel_path, str(exc))
                result.warnings.append(
                    f"{rel_path}: {exc} Keeping the existing GHDP placeholder."
                )

        if action == "generated":
            result.generated.append(rel_path)
        elif action == "updated":
            result.updated.append(rel_path)

    deterministic_targets = {
        CLAUDE_SETTINGS_REL_PATH: _render_claude_settings(repo_root),
        CODEX_CONFIG_REL_PATH: _render_codex_config(repo_root),
        MCP_CONFIG_REL_PATH: _render_mcp_config(repo_root),
    }
    for rel_path, content in deterministic_targets.items():
        action = _write_if_different(repo_root / rel_path, content)
        if action == "generated":
            result.generated.append(rel_path)
        elif action == "updated":
            result.updated.append(rel_path)

    _, warnings = inspect_repo_local_adapters(repo_root)
    result.warnings.extend(warnings)
    return result


def accept_repo_local_adapter_reviews(*, repo_root: Path) -> List[str]:
    changed: List[str] = []
    for rel_path in _MARKDOWN_TARGETS:
        path = repo_root / rel_path
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        updated = text.replace(
            f"adapter_status: \"{ADAPTER_STATUS_DRAFT}\"",
            f"adapter_status: \"{ADAPTER_STATUS_READY}\"",
        )
        if updated == text:
            updated = updated.replace(
                f"adapter_status: {ADAPTER_STATUS_DRAFT}",
                f"adapter_status: {ADAPTER_STATUS_READY}",
            )
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            changed.append(rel_path)
    return changed
