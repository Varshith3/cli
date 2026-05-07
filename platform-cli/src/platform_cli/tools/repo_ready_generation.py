from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

import yaml

from platform_cli.core.errors import PlatformError
from platform_cli.core.progress import GenerationProgressReporter
from platform_cli.exec.runner import run_cmd
from platform_cli.manifests.repo_ready_load import load_repo_ready_prompt, load_repo_ready_vocab
from platform_cli.manifests.repo_ready_validate import validate_repo_config, validate_runbook_config
from platform_cli.tools.branch_ai import build_intent_prompt
from platform_cli.tools.ai_provider import ProviderStatus, generate_text
from platform_cli.tools.jira_context import fetch_jira_context
from platform_cli.tools.repo_ready_assets import ensure_repo_ready_assets_synced

CONFIG_REVIEW_STATUS = "suggested"
RUNBOOK_REVIEW_STATUS = "suggested-review-required"
ARCHITECTURE_REVIEW_MARKER = "<!-- GHDP review_status: suggested -->"
INTENT_REVIEW_STATUS = "suggested-review-required"
FEATURE_BRANCH_PREFIX = "feature/"
FEATURE_BRANCH_INTENT_REL_PATH = ".ghdp/frbr/intent.json"
SUPPORTED_DRAFT_TARGETS: tuple[str, ...] = (
    ".ghdp/config.yaml",
    ".ghdp/runbook.yaml",
    ".ghdp/architecture.md",
)

_PROMPT_FILE_BY_TARGET = {
    ".ghdp/config.yaml": "config_yaml_generation.md",
    ".ghdp/runbook.yaml": "runbook_yaml_generation.md",
    ".ghdp/architecture.md": "architecture_md_generation.md",
}

_REQUIRED_ARCHITECTURE_HEADINGS = (
    "# GHDP Architecture",
    "## Module Map",
    "## Key Entry Points",
    "## Critical Flows",
    "## Validation",
    "## Ownership",
    "## Do Not Touch",
    "## Open Questions",
)
_FEATURE_BRANCH_RE = re.compile(
    r"^feature/(?P<ticket>[A-Z][A-Z0-9]+-\d+)-(?P<branch_type>[A-Z]+)-(?P<branch_slug>.+)$"
)


@dataclass
class RepoReadyDraftResult:
    provider: str
    generated: List[str] = field(default_factory=list)
    failed: Dict[str, str] = field(default_factory=dict)
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


def feature_branch_intent_warning(repo_root: Path) -> str | None:
    branch_name = current_branch_name(repo_root)
    if not branch_name.startswith(FEATURE_BRANCH_PREFIX):
        return None

    intent_path = repo_root / FEATURE_BRANCH_INTENT_REL_PATH
    if intent_path.exists():
        return None

    return (
        f"Feature branch '{branch_name}' is missing `{FEATURE_BRANCH_INTENT_REL_PATH}`. "
        "Create it before final review."
    )


def parse_feature_branch_name(branch_name: str) -> dict[str, str] | None:
    match = _FEATURE_BRANCH_RE.match(branch_name.strip())
    if not match:
        return None
    return {
        "ticket": match.group("ticket"),
        "branch_type": match.group("branch_type"),
        "branch_slug": match.group("branch_slug"),
    }


def _resolve_base_branch(repo_root: Path) -> str:
    remote_head = run_cmd(
        ["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        check=False,
        cwd=repo_root,
    )
    remote_head_name = (remote_head.stdout or "").strip()
    if remote_head.returncode == 0 and remote_head_name:
        return remote_head_name.split("/")[-1]

    for candidate in ("develop", "main", "master"):
        branch_check = run_cmd(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{candidate}"],
            check=False,
            cwd=repo_root,
        )
        if branch_check.returncode == 0:
            return candidate
    return ""


def _load_json_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def stale_feature_branch_intent_messages(payload: dict[str, object], *, branch_name: str) -> List[str]:
    if not branch_name.startswith(FEATURE_BRANCH_PREFIX):
        return []

    summary = str(payload.get("summary", "")).strip()
    intent_text = str(payload.get("intent", "")).strip()
    source = str(payload.get("source", "")).strip()
    intent_branch_name = str(payload.get("branch_name", "")).strip()
    ticket_key = str(payload.get("ticket_key", "")).strip()
    pending: List[str] = []

    if source == "repo_ready_fallback":
        pending.append("intent.source is still 'repo_ready_fallback'.")
    if summary.startswith("TODO"):
        pending.append("intent.summary still contains TODO placeholder content.")
    if not intent_text and not summary:
        pending.append("intent content is still empty.")

    parsed_branch = parse_feature_branch_name(branch_name)
    if not parsed_branch:
        return pending

    if not intent_branch_name:
        pending.append("intent.branch_name metadata is missing for this feature branch.")
    elif intent_branch_name != branch_name:
        pending.append(f"intent.branch_name targets '{intent_branch_name}' instead of '{branch_name}'.")

    expected_ticket = parsed_branch["ticket"]
    if not ticket_key:
        pending.append("intent.ticket_key metadata is missing for this feature branch.")
    elif ticket_key != expected_ticket:
        pending.append(f"intent.ticket_key targets '{ticket_key}' instead of '{expected_ticket}'.")

    return pending


def _intent_requires_generation(repo_root: Path, *, branch_name: str) -> bool:
    if not branch_name.startswith(FEATURE_BRANCH_PREFIX):
        return False

    intent_path = repo_root / FEATURE_BRANCH_INTENT_REL_PATH
    if not intent_path.exists():
        return True

    payload = _load_json_object(intent_path)
    return bool(stale_feature_branch_intent_messages(payload, branch_name=branch_name))


def _write_generated_intent(
    *,
    repo_root: Path,
    branch_name: str,
    ticket_key: str,
    provider: str,
    intent_text: str,
    jira_summary: str,
) -> None:
    intent_path = repo_root / FEATURE_BRANCH_INTENT_REL_PATH
    intent_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "generated_by": "ghdp",
        "source": "repo_ready_generated",
        "repo_name": repo_root.name,
        "branch_name": branch_name,
        "ticket_key": ticket_key,
        "intent": intent_text.strip(),
        "summary": (jira_summary or intent_text).strip(),
        "provider": provider,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": INTENT_REVIEW_STATUS,
    }
    intent_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _generate_feature_branch_intent(
    *,
    repo_root: Path,
    provider: str,
    statuses: Dict[str, ProviderStatus],
    progress: GenerationProgressReporter | None = None,
) -> str | None:
    branch_name = current_branch_name(repo_root)
    if not _intent_requires_generation(repo_root, branch_name=branch_name):
        return None

    parsed = parse_feature_branch_name(branch_name)
    if not parsed:
        return None

    jira_context = fetch_jira_context(parsed["ticket"], mode="warn")
    prompt = build_intent_prompt(
        jira_title=jira_context.get("summary", ""),
        jira_description=jira_context.get("description", ""),
        branch_name=branch_name,
        branch_type=parsed["branch_type"],
        branch_slug=parsed["branch_slug"],
        ticket_key=parsed["ticket"],
        repo=repo_root.name,
        base_branch=_resolve_base_branch(repo_root),
    )
    if progress is not None:
        progress.target_started(FEATURE_BRANCH_INTENT_REL_PATH)
    intent_exists = (repo_root / FEATURE_BRANCH_INTENT_REL_PATH).exists()
    generated = generate_text(
        provider=provider,
        statuses=statuses,
        prompt=prompt,
        heartbeat=(progress.heartbeat_callback(FEATURE_BRANCH_INTENT_REL_PATH) if progress is not None else None),
    ).strip()
    if not generated:
        raise PlatformError(
            f"{provider} returned an empty intent.",
            code="E_BRANCH_AI_EMPTY",
            reason=provider,
        )

    _write_generated_intent(
        repo_root=repo_root,
        branch_name=branch_name,
        ticket_key=parsed["ticket"],
        provider=provider,
        intent_text=generated,
        jira_summary=jira_context.get("summary", ""),
    )
    if progress is not None:
        progress.target_done(FEATURE_BRANCH_INTENT_REL_PATH, outcome="updated" if intent_exists else "generated")
    return FEATURE_BRANCH_INTENT_REL_PATH


def _repo_tree_summary(repo_root: Path) -> str:
    lines: List[str] = []
    top_level = sorted(
        [child for child in repo_root.iterdir() if child.name not in {".git", ".ghdp"}],
        key=lambda item: (item.is_file(), item.name.lower()),
    )
    for entry in top_level[:20]:
        kind = "[D]" if entry.is_dir() else "[F]"
        lines.append(f"{kind} {entry.name}")
        if entry.is_dir():
            children = sorted(
                [child for child in entry.iterdir() if child.name not in {".git", "__pycache__"}],
                key=lambda item: (item.is_file(), item.name.lower()),
            )
            for child in children[:8]:
                child_kind = "[D]" if child.is_dir() else "[F]"
                lines.append(f"  {child_kind} {entry.name}/{child.name}")
    return "\n".join(lines)


def _candidate_evidence_paths(repo_root: Path, *, target: str) -> List[Path]:
    patterns = [
        "ARCHITECTURE.md",
        "AGENTS.md",
        "*/README*",
        "*/ARCHITECTURE.md",
        "*/AGENTS.md",
        "*/CLAUDE.md",
        "*/pyproject.toml",
        "*/package.json",
        "*/setup.py",
        "README*",
        "Makefile",
        "package.json",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "tox.ini",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "requirements*.txt",
        "Dockerfile",
        "docker-compose*.yml",
        "docker-compose*.yaml",
        "compose*.yml",
        "compose*.yaml",
        ".github/workflows/*.yml",
        ".github/workflows/*.yaml",
    ]

    paths: List[Path] = []
    seen: set[str] = set()

    def _append_path(path: Path) -> None:
        if not path.exists():
            return
        try:
            relative = path.relative_to(repo_root)
        except ValueError:
            relative = path
        if target == ".ghdp/architecture.md" and relative.parts and relative.parts[0] == ".ghdp":
            return
        key = str(path.resolve())
        if key in seen:
            return
        seen.add(key)
        paths.append(path)

    for pattern in patterns:
        for match in sorted(repo_root.glob(pattern)):
            if not match.is_file():
                continue
            _append_path(match)

    if target == ".ghdp/architecture.md":
        for rel_path in ("src", "app", "services", "cmd", "lib"):
            _append_path(repo_root / rel_path)

        subproject_markers = (
            "ARCHITECTURE.md",
            "README.md",
            "README.rst",
            "README.txt",
            "pyproject.toml",
            "package.json",
            "setup.py",
            "pom.xml",
            "build.gradle",
            "build.gradle.kts",
            "go.mod",
            "Cargo.toml",
        )
        subproject_dirs = [
            child
            for child in sorted(repo_root.iterdir(), key=lambda item: item.name.lower())
            if child.is_dir() and child.name not in {".git", ".ghdp", "__pycache__"}
        ]
        for subproject in subproject_dirs[:8]:
            if not any((subproject / marker).exists() for marker in subproject_markers):
                continue
            for rel_path in (
                "ARCHITECTURE.md",
                "README.md",
                "README.rst",
                "README.txt",
                "AGENTS.md",
                "CLAUDE.md",
                "pyproject.toml",
                "package.json",
                "setup.py",
                "pom.xml",
            ):
                _append_path(subproject / rel_path)
            for rel_path in ("src", "app", "services", "cmd", "lib", "docs", "packaging"):
                _append_path(subproject / rel_path)

    return paths[:24]


def _path_lang(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".md": "markdown",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".json": "json",
        ".toml": "toml",
        ".py": "python",
    }.get(suffix, "text")


def _evidence_sections(repo_root: Path, *, target: str) -> str:
    parts: List[str] = []
    remaining_chars = 28000 if target == ".ghdp/architecture.md" else 16000
    for path in _candidate_evidence_paths(repo_root, target=target):
        if path.is_dir():
            subtree = sorted([child.name for child in path.iterdir()])[:12]
            body = "\n".join(f"- {path.name}/{name}" for name in subtree)
            section = f"File: {path.relative_to(repo_root)}\n```text\n{body}\n```"
        else:
            try:
                raw = path.read_text(encoding="utf-8")
            except Exception:
                continue
            snippet_limit = 4000
            if target == ".ghdp/architecture.md" and path.name in {"ARCHITECTURE.md", "AGENTS.md", "CLAUDE.md"}:
                snippet_limit = 12000
            snippet = raw[: min(snippet_limit, remaining_chars)].strip()
            if not snippet:
                continue
            section = (
                f"File: {path.relative_to(repo_root)}\n"
                f"```{_path_lang(path)}\n{snippet}\n```"
            )

        if len(section) > remaining_chars:
            break
        parts.append(section)
        remaining_chars -= len(section)
        if remaining_chars <= 0:
            break
    return "\n\n".join(parts)


def _optional_intent_text(repo_root: Path) -> str:
    intent_path = repo_root / FEATURE_BRANCH_INTENT_REL_PATH
    if not intent_path.exists():
        return ""
    try:
        return intent_path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _existing_file_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _is_placeholder_architecture_text(text: str) -> bool:
    normalized = (text or "").strip().lower()
    if not normalized:
        return False
    if "this file was scaffolded by ghdp" in normalized:
        return True
    return normalized.count("todo:") >= 2


def _build_prompt(*, repo_root: Path, rel_path: str, confirmed_tools: Sequence[str]) -> str:
    prompt_body = load_repo_ready_prompt(_PROMPT_FILE_BY_TARGET[rel_path], repo_root=repo_root)
    target_path = repo_root / rel_path
    branch_name = current_branch_name(repo_root)
    evidence = _evidence_sections(repo_root, target=rel_path)
    intent_text = _optional_intent_text(repo_root)

    parts = [
        prompt_body,
        "",
        "Repository context:",
        f"- Repository name: {repo_root.name}",
        f"- Current branch name: {branch_name or '(not resolved)'}",
        f"- Target file: {rel_path}",
    ]

    if rel_path == ".ghdp/config.yaml":
        parts.extend(
            [
                "",
                "Controlled vocabulary:",
                "```json",
                json.dumps(load_repo_ready_vocab(repo_root=repo_root), indent=2, sort_keys=True),
                "```",
            ]
        )

    if confirmed_tools:
        parts.extend(["", f"Confirmed tool-choice input: {', '.join(confirmed_tools)}"])

    existing_text = _existing_file_text(target_path)
    if existing_text:
        if rel_path == ".ghdp/architecture.md" and _is_placeholder_architecture_text(existing_text):
            parts.extend(
                [
                    "",
                    "Existing file content was detected as GHDP scaffold placeholder text. Replace it completely instead of preserving it.",
                ]
            )
        else:
            parts.extend(
                [
                    "",
                    "Existing file content:",
                    f"```{_path_lang(target_path)}",
                    existing_text,
                    "```",
                ]
            )

    parts.extend(["", "Repo tree summary:", "```text", _repo_tree_summary(repo_root), "```"])

    if evidence:
        parts.extend(["", "Evidence sources:", evidence])

    if intent_text:
        parts.extend(["", "Optional .ghdp/frbr/intent.json:", "```json", intent_text, "```"])

    parts.extend(["", "Generate the final file content now."])
    return "\n".join(parts).strip() + "\n"


def _parse_yaml_document(text: str, *, source: str) -> Dict[str, object]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PlatformError(
            f"Invalid YAML returned for {source}: {exc}",
            code="E_REPO_READY_GENERATION_INVALID_OUTPUT",
            reason=source,
        )

    if not isinstance(data, dict):
        raise PlatformError(
            f"Expected a YAML mapping for {source}",
            code="E_REPO_READY_GENERATION_INVALID_OUTPUT",
            reason=source,
        )
    return data


def _dump_yaml_document(data: Dict[str, object]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=False).rstrip() + "\n"


def _write_config_draft(path: Path, text: str, *, repo_root: Path) -> None:
    data = _parse_yaml_document(text, source=str(path))
    validate_repo_config(data, load_repo_ready_vocab(repo_root=repo_root))

    metadata = data.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        raise PlatformError(
            f"{path} metadata must be an object",
            code="E_REPO_READY_GENERATION_INVALID_OUTPUT",
            reason=str(path),
        )
    metadata["review_status"] = CONFIG_REVIEW_STATUS
    path.write_text(_dump_yaml_document(data), encoding="utf-8")


def _write_runbook_draft(path: Path, text: str) -> None:
    data = _parse_yaml_document(text, source=str(path))
    validate_runbook_config(data)

    notes = data.setdefault("notes", {})
    if not isinstance(notes, dict):
        raise PlatformError(
            f"{path} notes must be an object",
            code="E_REPO_READY_GENERATION_INVALID_OUTPUT",
            reason=str(path),
        )
    notes["status"] = RUNBOOK_REVIEW_STATUS
    path.write_text(_dump_yaml_document(data), encoding="utf-8")


def _write_architecture_draft(path: Path, text: str) -> None:
    normalized = text.strip()
    if not normalized:
        raise PlatformError(
            f"{path} cannot be empty",
            code="E_REPO_READY_GENERATION_INVALID_OUTPUT",
            reason=str(path),
        )

    missing = [heading for heading in _REQUIRED_ARCHITECTURE_HEADINGS if heading not in normalized]
    if missing:
        raise PlatformError(
            f"{path} is missing required headings: {missing}",
            code="E_REPO_READY_GENERATION_INVALID_OUTPUT",
            reason=str(path),
        )

    if ARCHITECTURE_REVIEW_MARKER not in normalized:
        normalized = f"{ARCHITECTURE_REVIEW_MARKER}\n\n{normalized}"

    path.write_text(normalized.rstrip() + "\n", encoding="utf-8")


def generate_repo_ready_drafts(
    *,
    repo_root: Path,
    provider: str,
    statuses: Dict[str, ProviderStatus],
    targets: Iterable[str],
    confirmed_tools: Sequence[str] | None = None,
    progress: GenerationProgressReporter | None = None,
) -> RepoReadyDraftResult:
    ensure_repo_ready_assets_synced(repo_root)
    confirmed_tools = list(confirmed_tools or [])
    result = RepoReadyDraftResult(provider=provider)

    for rel_path in targets:
        if rel_path not in SUPPORTED_DRAFT_TARGETS:
            continue

        abs_path = repo_root / rel_path
        existing_text = abs_path.read_text(encoding="utf-8") if abs_path.exists() else None
        try:
            if progress is not None:
                progress.target_started(rel_path)
            prompt = _build_prompt(repo_root=repo_root, rel_path=rel_path, confirmed_tools=confirmed_tools)
            generated_text = generate_text(
                provider=provider,
                statuses=statuses,
                prompt=prompt,
                heartbeat=(progress.heartbeat_callback(rel_path) if progress is not None else None),
            )

            if rel_path == ".ghdp/config.yaml":
                _write_config_draft(abs_path, generated_text, repo_root=repo_root)
            elif rel_path == ".ghdp/runbook.yaml":
                _write_runbook_draft(abs_path, generated_text)
            elif rel_path == ".ghdp/architecture.md":
                _write_architecture_draft(abs_path, generated_text)
            result.generated.append(rel_path)
            if progress is not None:
                progress.target_done(rel_path, outcome="updated" if existing_text is not None else "generated")
        except PlatformError as exc:
            result.failed[rel_path] = str(exc)
            if progress is not None:
                progress.target_failed(rel_path, str(exc))
            if existing_text is not None:
                abs_path.write_text(existing_text, encoding="utf-8")

    if provider in {"codex", "claude"}:
        try:
            generated_intent = _generate_feature_branch_intent(
                repo_root=repo_root,
                provider=provider,
                statuses=statuses,
                progress=progress,
            )
        except PlatformError as exc:
            result.failed[FEATURE_BRANCH_INTENT_REL_PATH] = str(exc)
            if progress is not None:
                progress.target_failed(FEATURE_BRANCH_INTENT_REL_PATH, str(exc))
        else:
            if generated_intent:
                result.generated.append(generated_intent)

    warning = feature_branch_intent_warning(repo_root)
    if warning:
        result.warnings.append(warning)

    return result
