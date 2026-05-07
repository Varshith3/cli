from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from platform_cli.core.errors import PlatformError
from platform_cli.core.repo_roots import resolve_repo_root
from platform_cli.manifests.orchestrate_load import load_orchestrate_json_file
from platform_cli.orchestrate_kernel.runtime_support import (
    assert_stage_completed,
    iso_now,
    resolve_active_run_context,
    write_json,
    write_markdown,
)
from platform_cli.tools.orchestrate_contract import runtime_branch_folder_name, slugify_branch_name
from platform_cli.tools.repo_ready_generation import current_branch_name


_CONFIG_PATH = Path(".ghdp/orchestrate/merge-hygiene.json")
_STAGE_TRACEABILITY = "stage22_traceability_capture"


@dataclass
class MergeHygieneFinalizeResult:
    repo_root: str
    branch_name: str
    branch_slug: str
    active_run_key: str
    status: str
    runtime_removed: bool
    archive_path: str
    memory_summary_path: str
    memory_receipt_path: str
    retention_days: int
    purged_archives: List[str]
    next_action: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MergeHygieneVerifyResult:
    repo_root: str
    branch_name: str
    branch_slug: str
    status: str
    merge_safe: bool
    branch_runtime_mode: str
    runtime_root: str
    memory_receipt_path: str
    memory_summary_path: str
    warnings: List[str]
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_finalize_merge_hygiene(*, repo_root: Path | None = None) -> MergeHygieneFinalizeResult:
    context = resolve_active_run_context(repo_root=repo_root)
    assert_stage_completed(context.stage_status, _STAGE_TRACEABILITY)
    config = _load_merge_hygiene_config(context.repo_root)
    closeout_paths = _closeout_paths(context.repo_root, context.branch_slug, config)
    archive_summary = _archive_runtime_bundle(
        repo_root=context.repo_root,
        runtime_root=context.runtime_root,
        branch_slug=context.branch_slug,
        active_run_key=context.active_run_key,
        config=config,
    )
    write_markdown(closeout_paths["summary_path"], _build_memory_summary_lines(context, archive_summary))
    write_json(closeout_paths["receipt_path"], _build_memory_receipt(context, archive_summary, closeout_paths))
    shutil.rmtree(context.runtime_root)
    return MergeHygieneFinalizeResult(
        repo_root=str(context.repo_root),
        branch_name=context.branch_name,
        branch_slug=context.branch_slug,
        active_run_key=context.active_run_key,
        status="completed",
        runtime_removed=True,
        archive_path=str(archive_summary["archive_path"]),
        memory_summary_path=str(closeout_paths["summary_path"]),
        memory_receipt_path=str(closeout_paths["receipt_path"]),
        retention_days=int(archive_summary["retention_days"]),
        purged_archives=[str(item) for item in archive_summary["purged_archives"]],
        next_action="Commit the promoted memory receipt and summary, then run `ghdp orchestrate verify-merge-hygiene` before merge.",
        message="Merge hygiene finalization completed: runtime artifacts were archived, promotable memory was written, and the branch runtime folder was pruned.",
    )


def run_verify_merge_hygiene(*, repo_root: Path | None = None) -> MergeHygieneVerifyResult:
    resolved_root = resolve_repo_root(repo_root)
    branch_name = current_branch_name(resolved_root)
    if not branch_name:
        raise PlatformError(
            "Could not resolve the current branch for merge-hygiene verification.",
            code="E_ORCHESTRATE_BRANCH_UNRESOLVED",
            reason="current_branch",
        )
    branch_slug = slugify_branch_name(branch_name)
    config = _load_merge_hygiene_config(resolved_root)
    runtime_root = resolved_root / ".ghdp" / "orchestrate" / "branches" / runtime_branch_folder_name(resolved_root, branch_name)
    closeout_paths = _closeout_paths(resolved_root, branch_slug, config)

    issues: List[str] = []
    warnings: List[str] = []
    branch_runtime_mode = "finalized"

    if runtime_root.exists():
        issues.append(f"Runtime branch folder still exists: {runtime_root.relative_to(resolved_root).as_posix()}")
        branch_runtime_mode = "active"

    if not closeout_paths["receipt_path"].exists():
        issues.append(
            f"Merge-hygiene receipt is missing: {closeout_paths['receipt_path'].relative_to(resolved_root).as_posix()}"
        )
        branch_runtime_mode = "missing_receipt"
    else:
        receipt = load_orchestrate_json_file(closeout_paths["receipt_path"])
        summary_rel_path = str(receipt.get("memory_summary_relpath", "")).strip()
        recorded_branch = str(receipt.get("branch_name", "")).strip()
        recorded_slug = str(receipt.get("branch_slug", "")).strip()
        if recorded_branch and recorded_branch != branch_name:
            issues.append(
                f"Merge-hygiene receipt branch_name mismatch: expected '{branch_name}', found '{recorded_branch}'."
            )
        if recorded_slug and recorded_slug != branch_slug:
            issues.append(
                f"Merge-hygiene receipt branch_slug mismatch: expected '{branch_slug}', found '{recorded_slug}'."
            )
        if not bool(receipt.get("runtime_pruned", False)):
            issues.append("Merge-hygiene receipt does not confirm runtime_pruned=true.")
        if not closeout_paths["summary_path"].exists():
            issues.append(
                f"Promoted memory summary is missing: {closeout_paths['summary_path'].relative_to(resolved_root).as_posix()}"
            )
        elif summary_rel_path and summary_rel_path != closeout_paths["summary_path"].relative_to(resolved_root).as_posix():
            warnings.append(
                "Merge-hygiene receipt recorded a different summary path than the current configured closeout path."
            )
        retention_days = receipt.get("archive_retention_days", 0)
        if not isinstance(retention_days, int) or retention_days < 1:
            issues.append("Merge-hygiene receipt archive_retention_days must be an integer >= 1.")

    foreign_runtime_roots = _foreign_runtime_roots(resolved_root, branch_slug)
    if foreign_runtime_roots:
        warnings.append(
            "Other branch runtime folders are still present in the repo: "
            + ", ".join(item.relative_to(resolved_root).as_posix() for item in foreign_runtime_roots)
        )

    if issues:
        raise PlatformError(
            "Branch is not merge-hygienic yet:\n- " + "\n- ".join(issues),
            code="E_ORCHESTRATE_MERGE_HYGIENE_FAILED",
            reason="merge_hygiene",
        )

    return MergeHygieneVerifyResult(
        repo_root=str(resolved_root),
        branch_name=branch_name,
        branch_slug=branch_slug,
        status="completed",
        merge_safe=True,
        branch_runtime_mode=branch_runtime_mode,
        runtime_root=str(runtime_root),
        memory_receipt_path=str(closeout_paths["receipt_path"]),
        memory_summary_path=str(closeout_paths["summary_path"]),
        warnings=warnings,
        message="Merge-hygiene verification passed: runtime-only branch artifacts are pruned and the promoted memory receipt is present.",
    )


def _load_merge_hygiene_config(repo_root: Path) -> Dict[str, Any]:
    config_path = repo_root / _CONFIG_PATH
    if not config_path.exists():
        raise PlatformError(
            "Merge-hygiene config is missing. Expected .ghdp/orchestrate/merge-hygiene.json.",
            code="E_ORCHESTRATE_MERGE_HYGIENE_CONFIG_MISSING",
            reason=_CONFIG_PATH.as_posix(),
        )
    payload = load_orchestrate_json_file(config_path)
    retained_memory = payload.get("retained_memory", {})
    archive = payload.get("archive", {})
    local = archive.get("local", {}) if isinstance(archive, dict) else {}
    if not isinstance(retained_memory, dict) or not str(retained_memory.get("shared_closeout_dir", "")).strip():
        raise PlatformError(
            "Merge-hygiene config retained_memory.shared_closeout_dir is missing.",
            code="E_ORCHESTRATE_MERGE_HYGIENE_CONFIG_INVALID",
            reason="retained_memory.shared_closeout_dir",
        )
    if str(archive.get("destination_mode", "")).strip() not in {"local", "aws_s3"}:
        raise PlatformError(
            "Merge-hygiene config archive.destination_mode must be local or aws_s3.",
            code="E_ORCHESTRATE_MERGE_HYGIENE_CONFIG_INVALID",
            reason="archive.destination_mode",
        )
    if not isinstance(local, dict) or not str(local.get("output_dir", "")).strip():
        raise PlatformError(
            "Merge-hygiene config archive.local.output_dir is missing.",
            code="E_ORCHESTRATE_MERGE_HYGIENE_CONFIG_INVALID",
            reason="archive.local.output_dir",
        )
    retention_days = local.get("retention_days", 0)
    if not isinstance(retention_days, int) or retention_days < 1:
        raise PlatformError(
            "Merge-hygiene config archive.local.retention_days must be an integer >= 1.",
            code="E_ORCHESTRATE_MERGE_HYGIENE_CONFIG_INVALID",
            reason="archive.local.retention_days",
        )
    return payload


def _closeout_paths(repo_root: Path, branch_slug: str, config: Dict[str, Any]) -> Dict[str, Path]:
    shared_closeout_dir = Path(str(config["retained_memory"]["shared_closeout_dir"]).strip())
    closeout_root = repo_root / shared_closeout_dir
    closeout_root.mkdir(parents=True, exist_ok=True)
    return {
        "root": closeout_root,
        "summary_path": closeout_root / f"{branch_slug}.md",
        "receipt_path": closeout_root / f"{branch_slug}.json",
    }


def _archive_runtime_bundle(
    *,
    repo_root: Path,
    runtime_root: Path,
    branch_slug: str,
    active_run_key: str,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    archive_config = config["archive"]
    destination_mode = str(archive_config.get("destination_mode", "local")).strip() or "local"
    local_config = archive_config.get("local", {})
    output_dir = Path(str(local_config.get("output_dir", "tmp/orchestrate-merge-archives")).strip())
    retention_days = int(local_config.get("retention_days", 7))
    archive_root = (repo_root / output_dir).resolve()
    archive_root.mkdir(parents=True, exist_ok=True)

    if destination_mode != "local":
        raise PlatformError(
            "Only local merge-hygiene archive mode is supported today. Keep archive.destination_mode=local until the external archive target is wired.",
            code="E_ORCHESTRATE_MERGE_HYGIENE_CONFIG_INVALID",
            reason="archive.destination_mode",
        )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_stem = _compact_archive_stem(branch_slug, active_run_key, stamp)
    archive_base = archive_root / archive_stem
    archive_path = Path(
        shutil.make_archive(
            str(archive_base),
            "zip",
            root_dir=runtime_root.parent,
            base_dir=runtime_root.name,
        )
    )
    purged_archives = _purge_expired_archives(archive_root, retention_days)
    return {
        "archive_path": archive_path,
        "retention_days": retention_days,
        "purged_archives": purged_archives,
    }


def _purge_expired_archives(archive_root: Path, retention_days: int) -> List[Path]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    purged: List[Path] = []
    for candidate in archive_root.glob("*.zip"):
        modified_at = datetime.fromtimestamp(candidate.stat().st_mtime, tz=timezone.utc)
        if modified_at < cutoff:
            candidate.unlink()
            purged.append(candidate)
    return sorted(purged)


def _build_memory_summary_lines(context: Any, archive_summary: Dict[str, Any]) -> List[str]:
    stage_status = context.stage_status
    completed_stages = _completed_stages(stage_status)
    audit_export_summary = _load_optional_json(context.run_root / "audit_export_summary.json")
    prerelease_plan = _load_optional_json(context.run_root / "prerelease_plan.json")
    return [
        "# Orchestrate Closeout",
        "",
        f"- Branch: `{context.branch_name}`",
        f"- Ticket: `{context.ticket_key or '(missing)'}`",
        f"- Active run: `{context.active_run_key}`",
        f"- Finalized at: `{iso_now()}`",
        f"- Completed stages: `{len(completed_stages)}`",
        f"- Archive path: `{archive_summary['archive_path']}`",
        f"- Archive retention days: `{archive_summary['retention_days']}`",
        f"- Published prerelease tag: `{str(prerelease_plan.get('tag', '')).strip() or '(not recorded)'}`",
        f"- Audit export mode: `{str(audit_export_summary.get('destination_mode', 'local')).strip() or 'local'}`",
        f"- Audit export path: `{str(audit_export_summary.get('export_path', '(missing)')).strip()}`",
        "",
        "## Completed Stages",
        *[f"- `{stage_name}`" for stage_name in completed_stages],
        "",
        "## Merge Hygiene",
        "- Runtime-only orchestrate artifacts were archived and pruned from `.ghdp/orchestrate/branches/...`.",
        "- This closeout summary is the durable repo-shared memory artifact that should survive the merge.",
    ]


def _build_memory_receipt(context: Any, archive_summary: Dict[str, Any], closeout_paths: Dict[str, Path]) -> Dict[str, Any]:
    repo_root = Path(context.repo_root)
    completed_stages = _completed_stages(context.stage_status)
    return {
        "schema_version": "1.0",
        "branch_name": context.branch_name,
        "branch_slug": context.branch_slug,
        "ticket_key": context.ticket_key,
        "active_run_key": context.active_run_key,
        "finalized_at": iso_now(),
        "runtime_pruned": True,
        "runtime_root_relpath": context.runtime_root.relative_to(repo_root).as_posix(),
        "memory_summary_relpath": closeout_paths["summary_path"].relative_to(repo_root).as_posix(),
        "memory_receipt_relpath": closeout_paths["receipt_path"].relative_to(repo_root).as_posix(),
        "archive_path": str(archive_summary["archive_path"]),
        "archive_retention_days": int(archive_summary["retention_days"]),
        "purged_archives": [str(path) for path in archive_summary["purged_archives"]],
        "completed_stages": completed_stages,
        "final_stage": _STAGE_TRACEABILITY,
    }


def _completed_stages(stage_status: Dict[str, Any]) -> List[str]:
    completed: List[str] = []
    for stage_name, payload in stage_status.items():
        if isinstance(payload, dict) and str(payload.get("status", "")).strip() == "completed":
            completed.append(str(stage_name))
    return sorted(completed)


def _load_optional_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _foreign_runtime_roots(repo_root: Path, current_branch_slug: str) -> List[Path]:
    branches_root = repo_root / ".ghdp" / "orchestrate" / "branches"
    if not branches_root.exists():
        return []
    return sorted(
        path
        for path in branches_root.iterdir()
        if path.is_dir() and path.name != current_branch_slug
    )


def _compact_archive_stem(branch_slug: str, active_run_key: str, stamp: str) -> str:
    branch_prefix = branch_slug[:32].rstrip("-") or "branch"
    digest = hashlib.sha1(f"{branch_slug}:{active_run_key}".encode("utf-8")).hexdigest()[:10]
    return f"{branch_prefix}__{digest}__{stamp}"
