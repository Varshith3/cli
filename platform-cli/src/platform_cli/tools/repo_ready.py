# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from platform_cli.core.errors import PlatformError
from platform_cli.core.repo_roots import resolve_repo_root as resolve_git_repo_root
from platform_cli.manifests.repo_ready_load import (
    load_repo_ready_template,
    load_repo_ready_vocab,
    load_repo_ready_yaml_file,
)
from platform_cli.manifests.repo_ready_validate import (
    validate_guardrails_config,
    validate_lock_config,
    validate_repo_config,
    validate_runbook_config,
)
from platform_cli.tools.repo_ready_adapters import (
    ADAPTER_STATUS_READY,
    RepoReadyAdapterResult,
    accept_repo_local_adapter_reviews,
    inspect_repo_local_adapters,
)
from platform_cli.tools.repo_jenkins_contract import inspect_repo_jenkins_contract
from platform_cli.tools.repo_ready_assets import ensure_repo_ready_assets_synced
from platform_cli.tools.repo_ready_generation import (
    ARCHITECTURE_REVIEW_MARKER,
    CONFIG_REVIEW_STATUS,
    FEATURE_BRANCH_INTENT_REL_PATH,
    FEATURE_BRANCH_PREFIX,
    INTENT_REVIEW_STATUS,
    RUNBOOK_REVIEW_STATUS,
    current_branch_name,
    parse_feature_branch_name,
    stale_feature_branch_intent_messages,
)

REPO_READY_TEMPLATE_VERSION = "1.0.0"
REPO_READY_SCHEMA_VERSION = "1.0"
REPO_READY_REPORT_SCHEMA_VERSION = "1.0"
READINESS_REL_PATH = ".ghdp/readiness.json"


@dataclass(frozen=True)
class RepoReadyFileSpec:
    rel_path: str
    template_path: str
    required: bool
    kind: str
    description: str


@dataclass
class RepoReadyFileResult:
    rel_path: str
    abs_path: str
    required: bool
    exists: bool
    valid: Optional[bool]
    status: str
    messages: List[str] = field(default_factory=list)


@dataclass
class RepoReadyResult:
    repo_root: str
    mode: str
    ready: bool
    compliant: bool
    template_version: str
    files: List[RepoReadyFileResult] = field(default_factory=list)
    adapters: List[RepoReadyAdapterResult] = field(default_factory=list)
    created: List[str] = field(default_factory=list)
    missing_required: List[str] = field(default_factory=list)
    recommended_missing: List[str] = field(default_factory=list)
    invalid_required: List[str] = field(default_factory=list)
    invalid_recommended: List[str] = field(default_factory=list)
    pending_required: List[str] = field(default_factory=list)
    pending_recommended: List[str] = field(default_factory=list)
    missing_required_adapters: List[str] = field(default_factory=list)
    pending_required_adapters: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo_root": self.repo_root,
            "mode": self.mode,
            "ready": self.ready,
            "compliant": self.compliant,
            "template_version": self.template_version,
            "files": [asdict(item) for item in self.files],
            "adapters": [asdict(item) for item in self.adapters],
            "created": list(self.created),
            "missing_required": list(self.missing_required),
            "recommended_missing": list(self.recommended_missing),
            "invalid_required": list(self.invalid_required),
            "invalid_recommended": list(self.invalid_recommended),
            "pending_required": list(self.pending_required),
            "pending_recommended": list(self.pending_recommended),
            "missing_required_adapters": list(self.missing_required_adapters),
            "pending_required_adapters": list(self.pending_required_adapters),
            "warnings": list(self.warnings),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


PHASE1_FILE_SPECS: List[RepoReadyFileSpec] = [
    RepoReadyFileSpec(
        rel_path=".ghdp/config.yaml",
        template_path="ghdp/config.yaml",
        required=True,
        kind="config",
        description="Canonical repo classification and enablement config.",
    ),
    RepoReadyFileSpec(
        rel_path=".ghdp/guardrails.yaml",
        template_path="ghdp/guardrails.yaml",
        required=True,
        kind="guardrails",
        description="Canonical repo guardrails and policy baseline.",
    ),
    RepoReadyFileSpec(
        rel_path=".ghdp/lock.yaml",
        template_path="ghdp/lock.yaml",
        required=True,
        kind="lock",
        description="Template and provenance lock file.",
    ),
    RepoReadyFileSpec(
        rel_path=".ghdp/runbook.yaml",
        template_path="ghdp/runbook.yaml",
        required=True,
        kind="runbook",
        description="Machine-readable build/test/lint/format runbook.",
    ),
    RepoReadyFileSpec(
        rel_path=".ghdp/architecture.md",
        template_path="ghdp/architecture.md",
        required=False,
        kind="architecture",
        description="Human-reviewed repo architecture summary.",
    ),
    RepoReadyFileSpec(
        rel_path=".github/workflows/ghdp-agent-policy.yml",
        template_path="workflows/ghdp-agent-policy.yml",
        required=True,
        kind="workflow",
        description="CI gate for GHDP repo readiness verification.",
    ),
]

FEATURE_BRANCH_INTENT_SPEC = RepoReadyFileSpec(
    rel_path=FEATURE_BRANCH_INTENT_REL_PATH,
    template_path="ghdp/frbr/intent.json",
    required=False,
    kind="intent",
    description="Feature branch intent summary and acceptance criteria.",
)


def required_phase1_files() -> List[str]:
    return [spec.rel_path for spec in PHASE1_FILE_SPECS if spec.required]


def resolve_repo_root(explicit_repo_root: Optional[Path] = None) -> Path:
    return resolve_git_repo_root(explicit_repo_root)


def _render_template_content(template_path: str, *, repo_root: Path, branch_name: str = "") -> str:
    parsed_branch = parse_feature_branch_name(branch_name) or {}
    content = load_repo_ready_template(template_path, repo_root=repo_root)
    return (
        content.replace("__GHDP_TEMPLATE_VERSION__", REPO_READY_TEMPLATE_VERSION)
        .replace("__GHDP_APPLIED_AT__", datetime.now(timezone.utc).isoformat())
        .replace("__GHDP_REPO_NAME__", repo_root.name)
        .replace("__GHDP_BRANCH_NAME__", branch_name)
        .replace("__GHDP_TICKET_KEY__", parsed_branch.get("ticket", ""))
    )


def _render_template(spec: RepoReadyFileSpec, *, repo_root: Path, branch_name: str = "") -> str:
    return _render_template_content(spec.template_path, repo_root=repo_root, branch_name=branch_name)


def _load_json_file(path: Path, *, source: str) -> Dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PlatformError(
            f"Repo readiness file not found: {path}",
            code="E_REPO_READY_FILE_NOT_FOUND",
            reason=str(path),
        )

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlatformError(
            f"Invalid JSON in {source}: {exc}",
            code="E_REPO_READY_INVALID_JSON",
            reason=source,
        )

    if not isinstance(payload, dict):
        raise PlatformError(
            f"Expected a JSON object in {source}",
            code="E_REPO_READY_INVALID_JSON",
            reason=source,
        )
    return payload


def _write_yaml_file(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=False).rstrip() + "\n",
        encoding="utf-8",
    )


def _inspect_existing_file(spec: RepoReadyFileSpec, path: Path, *, repo_root: Path, branch_name: str) -> List[str]:
    if spec.kind == "config":
        data = load_repo_ready_yaml_file(path)
        validate_repo_config(data, load_repo_ready_vocab(repo_root=repo_root))

        pending: List[str] = []
        repo_type = str(data.get("repo", {}).get("type", "")).strip()
        if repo_type == "unknown":
            pending.append("config.repo.type is still set to the scaffold default 'unknown'.")
        review_status = str(data.get("metadata", {}).get("review_status", "")).strip()
        if review_status == CONFIG_REVIEW_STATUS:
            pending.append("config.metadata.review_status is still 'suggested'.")
        return pending

    if spec.kind == "guardrails":
        validate_guardrails_config(load_repo_ready_yaml_file(path))
        return []

    if spec.kind == "lock":
        validate_lock_config(load_repo_ready_yaml_file(path), expected_managed_files=required_phase1_files())
        return []

    if spec.kind == "runbook":
        data = load_repo_ready_yaml_file(path)
        validate_runbook_config(data)

        pending: List[str] = []
        commands = data.get("commands", {})
        if all(not commands.get(key) for key in ("build", "test", "lint", "format", "start", "dev")):
            pending.append("runbook.commands are still empty.")

        notes_status = str(data.get("notes", {}).get("status", "")).strip()
        if notes_status == "pending-user-review":
            pending.append("runbook.notes.status is still 'pending-user-review'.")
        elif notes_status == RUNBOOK_REVIEW_STATUS:
            pending.append("runbook.notes.status is still 'suggested-review-required'.")

        return pending

    text = path.read_text(encoding="utf-8")

    if spec.kind == "workflow":
        if "ghdp repo verify" not in text and "ghdp repo ready --verify" not in text:
            raise PlatformError(
                f"{path} does not run a GHDP repo verification command",
                code="E_REPO_READY_INVALID_WORKFLOW",
                reason=str(path),
            )
        return []

    if spec.kind == "architecture":
        if not text.strip():
            raise PlatformError(
                f"{path} is empty",
                code="E_REPO_READY_INVALID_ARCHITECTURE",
                reason=str(path),
            )

        pending = []
        if "TODO:" in text:
            pending.append("architecture.md still contains TODO markers.")
        if ARCHITECTURE_REVIEW_MARKER in text:
            pending.append("architecture.md contains a suggested draft marker that still needs review.")
        return pending

    if spec.kind == "intent":
        data = _load_json_file(path, source=str(path))
        pending = stale_feature_branch_intent_messages(data, branch_name=branch_name)
        status = str(data.get("status", "")).strip()
        if status == "pending-user-review":
            pending.append("intent.status is still 'pending-user-review'.")
        elif status == INTENT_REVIEW_STATUS:
            pending.append("intent.status is still 'suggested-review-required'.")
        return pending

    return []


def _is_feature_branch(branch_name: str) -> bool:
    return bool(branch_name) and branch_name.startswith(FEATURE_BRANCH_PREFIX)


def _next_steps(result: RepoReadyResult) -> List[str]:
    steps: List[str] = []
    for rel_path in result.missing_required:
        steps.append(f"Create required file: {rel_path}")
    for rel_path in result.invalid_required:
        steps.append(f"Fix invalid required file: {rel_path}")
    for rel_path in result.pending_required:
        steps.append(f"Review and finalize pending required file: {rel_path}")
    for rel_path in result.missing_required_adapters:
        steps.append(f"Generate required repo-local adapter: {rel_path}")
    for rel_path in result.pending_required_adapters:
        steps.append(f"Review and finalize required repo-local adapter: {rel_path}")
    for rel_path in result.recommended_missing:
        steps.append(f"Review whether to add recommended file: {rel_path}")
    for rel_path in result.pending_recommended:
        steps.append(f"Review and finalize recommended file: {rel_path}")
    for adapter in result.adapters:
        if adapter.state == "missing":
            steps.append(f"Generate repo-local adapter: {adapter.rel_path}")
        elif adapter.state == "scaffolded_placeholder":
            steps.append(f"Replace placeholder repo-local adapter: {adapter.rel_path}")
        elif adapter.state == "draft_generated_review_required":
            steps.append(f"Review repo-local adapter draft: {adapter.rel_path}")
    if not steps and result.ready:
        steps.append("No action required. Repo readiness is compliant.")
    return steps


def build_repo_readiness_report(result: RepoReadyResult) -> Dict[str, Any]:
    repo_root = Path(result.repo_root)
    branch_name = current_branch_name(repo_root)
    return {
        "schema_version": REPO_READY_REPORT_SCHEMA_VERSION,
        "generated_by": "ghdp",
        "template_version": result.template_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": result.repo_root,
        "branch_name": branch_name,
        "mode": result.mode,
        "summary": "ready" if result.ready else "not_ready",
        "compliant": result.compliant,
        "files": [
            {
                "path": item.rel_path,
                "state": item.status,
                "required": item.required,
                "messages": list(item.messages),
            }
            for item in result.files
        ],
        "adapters": [
            {
                "path": item.rel_path,
                "state": item.state,
                "required_by_enabled_tools": item.required_by_enabled_tools,
                "template_version": item.template_version,
                "messages": list(item.messages),
            }
            for item in result.adapters
        ],
        "missing_required": list(result.missing_required),
        "invalid_required": list(result.invalid_required),
        "pending_required": list(result.pending_required),
        "missing_required_adapters": list(result.missing_required_adapters),
        "pending_required_adapters": list(result.pending_required_adapters),
        "recommended_missing": list(result.recommended_missing),
        "pending_recommended": list(result.pending_recommended),
        "warnings": list(result.warnings),
        "next_steps": _next_steps(result),
    }


def write_repo_readiness_report(result: RepoReadyResult) -> Path:
    repo_root = Path(result.repo_root)
    readiness_path = repo_root / READINESS_REL_PATH
    readiness_path.parent.mkdir(parents=True, exist_ok=True)
    readiness_path.write_text(
        json.dumps(build_repo_readiness_report(result), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return readiness_path


def accept_repo_ready_reviews(*, repo_root: Optional[Path] = None) -> List[str]:
    resolved_root = resolve_repo_root(repo_root)
    ensure_repo_ready_assets_synced(resolved_root)
    branch_name = current_branch_name(resolved_root)
    specs = list(PHASE1_FILE_SPECS)
    if _is_feature_branch(branch_name):
        specs.append(FEATURE_BRANCH_INTENT_SPEC)

    changed: List[str] = []
    for spec in specs:
        path = resolved_root / spec.rel_path
        if not path.exists():
            continue

        if spec.kind == "config":
            data = load_repo_ready_yaml_file(path)
            metadata = data.get("metadata")
            if isinstance(metadata, dict) and str(metadata.get("review_status", "")).strip() == CONFIG_REVIEW_STATUS:
                metadata["review_status"] = "confirmed"
                _write_yaml_file(path, data)
                changed.append(spec.rel_path)
            continue

        if spec.kind == "runbook":
            data = load_repo_ready_yaml_file(path)
            notes = data.get("notes")
            status = str((notes or {}).get("status", "")).strip() if isinstance(notes, dict) else ""
            if isinstance(notes, dict) and status in {"pending-user-review", RUNBOOK_REVIEW_STATUS}:
                notes["status"] = "ready"
                _write_yaml_file(path, data)
                changed.append(spec.rel_path)
            continue

        if spec.kind == "architecture":
            text = path.read_text(encoding="utf-8")
            updated = text
            for marker_variant in (
                f"{ARCHITECTURE_REVIEW_MARKER}\r\n\r\n",
                f"{ARCHITECTURE_REVIEW_MARKER}\n\n",
                f"{ARCHITECTURE_REVIEW_MARKER}\r\n",
                f"{ARCHITECTURE_REVIEW_MARKER}\n",
                ARCHITECTURE_REVIEW_MARKER,
            ):
                if marker_variant in updated:
                    updated = updated.replace(marker_variant, "", 1)
                    break
            if updated != text:
                path.write_text(updated.lstrip("\r\n"), encoding="utf-8")
                changed.append(spec.rel_path)
            continue

        if spec.kind == "intent":
            payload = _load_json_file(path, source=str(path))
            status = str(payload.get("status", "")).strip()
            if status in {"pending-user-review", INTENT_REVIEW_STATUS}:
                payload["status"] = "ready"
                path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                changed.append(spec.rel_path)

    changed.extend(accept_repo_local_adapter_reviews(repo_root=resolved_root))
    return changed


def assess_repo_ready(*, mode: str = "report", repo_root: Optional[Path] = None) -> RepoReadyResult:
    if mode not in {"report", "fix", "verify"}:
        raise PlatformError(
            f"Unsupported repo readiness mode: {mode}",
            code="E_REPO_READY_INVALID_MODE",
            reason=mode,
        )

    resolved_root = resolve_repo_root(repo_root)
    ensure_repo_ready_assets_synced(resolved_root)
    branch_name = current_branch_name(resolved_root)
    result = RepoReadyResult(
        repo_root=str(resolved_root),
        mode=mode,
        ready=False,
        compliant=False,
        template_version=REPO_READY_TEMPLATE_VERSION,
    )

    specs = list(PHASE1_FILE_SPECS)
    if _is_feature_branch(branch_name):
        specs.append(FEATURE_BRANCH_INTENT_SPEC)

    for spec in specs:
        abs_path = resolved_root / spec.rel_path
        file_result = RepoReadyFileResult(
            rel_path=spec.rel_path,
            abs_path=str(abs_path),
            required=spec.required,
            exists=abs_path.exists(),
            valid=None,
            status="unknown",
            messages=[],
        )

        was_created = False

        if not abs_path.exists():
            if mode == "fix":
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                abs_path.write_text(
                    _render_template(spec, repo_root=resolved_root, branch_name=branch_name),
                    encoding="utf-8",
                )
                file_result.exists = True
                was_created = True
                result.created.append(spec.rel_path)
            else:
                file_result.valid = False
                file_result.status = "missing" if spec.required else "recommended_missing"
                if spec.required:
                    result.missing_required.append(spec.rel_path)
                else:
                    result.recommended_missing.append(spec.rel_path)
                    result.warnings.append(f"Recommended file missing: {spec.rel_path}")

                result.files.append(file_result)
                continue

        try:
            pending_messages = _inspect_existing_file(
                spec,
                abs_path,
                repo_root=resolved_root,
                branch_name=branch_name,
            )
        except PlatformError as e:
            file_result.valid = False
            file_result.status = "invalid"
            file_result.messages.append(str(e))
            if spec.required:
                result.invalid_required.append(spec.rel_path)
                if mode == "fix":
                    file_result.messages.append("Existing file left unchanged; Phase 1 fix only scaffolds missing files.")
            else:
                result.invalid_recommended.append(spec.rel_path)
                result.warnings.append(f"Recommended file needs review: {spec.rel_path}")
        else:
            file_result.valid = True
            file_result.messages.extend(pending_messages)
            if pending_messages:
                file_result.status = "pending_user"
                if spec.required:
                    result.pending_required.append(spec.rel_path)
                else:
                    result.pending_recommended.append(spec.rel_path)
                    result.warnings.append(f"Recommended file still needs user input: {spec.rel_path}")
            else:
                file_result.status = "created" if was_created else "present"

        result.files.append(file_result)

    adapter_results, adapter_warnings = inspect_repo_local_adapters(resolved_root)
    result.adapters.extend(adapter_results)
    result.warnings.extend(adapter_warnings)
    for adapter in adapter_results:
        if not adapter.required_by_enabled_tools:
            continue
        if adapter.state == "missing":
            result.missing_required_adapters.append(adapter.rel_path)
        elif adapter.state != ADAPTER_STATUS_READY:
            result.pending_required_adapters.append(adapter.rel_path)

    jenkins_contract = inspect_repo_jenkins_contract(resolved_root)
    result.warnings.extend(jenkins_contract.messages)

    result.compliant = (
        not result.missing_required
        and not result.invalid_required
        and not result.pending_required
        and not result.missing_required_adapters
        and not result.pending_required_adapters
    )
    result.ready = result.compliant
    return result
