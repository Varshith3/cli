from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from platform_cli.core.errors import PlatformError
from platform_cli.core.repo_roots import resolve_repo_root
from platform_cli.exec.runner import run_cmd
from platform_cli.manifests.orchestrate_load import load_orchestrate_json_file
from platform_cli.tools.orchestrate_contract import runtime_branch_folder_name, slugify_branch_name
from platform_cli.tools.repo_ready_generation import current_branch_name


_BRANCHES_ROOT = Path(".ghdp/orchestrate/branches")
_AUDIT_EXPORT_PATH = Path(".ghdp/orchestrate/audit-export.json")


@dataclass
class ActiveRunContext:
    repo_root: Path
    branch_name: str
    branch_slug: str
    runtime_root: Path
    run_root: Path
    branch_state_path: Path
    stage_status_path: Path
    decisions_path: Path
    resume_context_path: Path
    poa_path: Path
    handoff_path: Path
    active_run_key: str
    branch_state: Dict[str, Any]
    stage_status: Dict[str, Any]
    ticket_key: str


def resolve_active_run_context(*, repo_root: Path | None = None) -> ActiveRunContext:
    resolved_root = resolve_repo_root(repo_root)
    branch_name = current_branch_name(resolved_root)
    if not branch_name:
        raise PlatformError(
            "Could not resolve the current branch for orchestrator execution.",
            code="E_ORCHESTRATE_BRANCH_UNRESOLVED",
            reason="current_branch",
        )
    runtime_root = resolved_root / _BRANCHES_ROOT / runtime_branch_folder_name(resolved_root, branch_name)
    branch_state_path = runtime_root / "branch_state.json"
    if not branch_state_path.exists():
        raise PlatformError(
            "No branch runtime state exists yet. Start the orchestrator before running this stage.",
            code="E_ORCHESTRATE_RUN_MISSING",
            reason=str(branch_state_path),
        )
    branch_state = load_orchestrate_json_file(branch_state_path)
    active_run_key = str(branch_state.get("active_run_key", "")).strip()
    if not active_run_key:
        raise PlatformError(
            "Branch runtime state does not contain an active run key.",
            code="E_ORCHESTRATE_RUN_MISSING",
            reason="active_run_key",
        )
    run_root = runtime_root / "runs" / active_run_key
    stage_status_path = run_root / "stage_status.json"
    if not stage_status_path.exists():
        raise PlatformError(
            "The active run is missing stage_status.json.",
            code="E_ORCHESTRATE_RUN_MISSING",
            reason=str(stage_status_path),
        )
    return ActiveRunContext(
        repo_root=resolved_root,
        branch_name=branch_name,
        branch_slug=slugify_branch_name(branch_name),
        runtime_root=runtime_root,
        run_root=run_root,
        branch_state_path=branch_state_path,
        stage_status_path=stage_status_path,
        decisions_path=run_root / "decisions.json",
        resume_context_path=run_root / "resume_context.md",
        poa_path=runtime_root / "poa.md",
        handoff_path=runtime_root / "handoff.md",
        active_run_key=active_run_key,
        branch_state=branch_state,
        stage_status=load_orchestrate_json_file(stage_status_path),
        ticket_key=str(branch_state.get("ticket_key", "")).strip(),
    )


def assert_stage_completed(stage_status: Dict[str, Any], stage_name: str) -> None:
    if str(stage_status.get(stage_name, {}).get("status", "")).strip() != "completed":
        raise PlatformError(
            f"Stage '{stage_name}' must complete before this stage can begin.",
            code="E_ORCHESTRATE_STAGE_ORDER",
            reason=stage_name,
        )


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_markdown(path: Path, lines: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def upsert_stage_status(
    path: Path,
    *,
    stage_name: str,
    status: str,
    owner_agent: str,
    summary: str,
    artifacts: Sequence[str],
) -> Dict[str, Any]:
    payload = load_orchestrate_json_file(path) if path.exists() else {}
    payload[stage_name] = {
        "status": status,
        "owner_agent": owner_agent,
        "summary": summary,
        "artifacts": list(artifacts),
        "updated_at": iso_now(),
    }
    write_json(path, payload)
    return payload


def upsert_decisions(path: Path, decisions: Sequence[Dict[str, Any]]) -> None:
    payload = load_orchestrate_json_file(path) if path.exists() else {"decisions": []}
    existing = payload.get("decisions", [])
    if not isinstance(existing, list):
        existing = []
    decision_index = {str(item.get("id", "")).strip(): item for item in existing if isinstance(item, dict)}
    for item in decisions:
        decision_id = str(item.get("id", "")).strip()
        if not decision_id:
            continue
        decision_index[decision_id] = dict(item)
    payload["decisions"] = list(decision_index.values())
    write_json(path, payload)


def write_handoff(path: Path, *, summary: str, next_action: str, status: str, at: str) -> None:
    write_markdown(
        path,
        [
            "# GHDP Handoff",
            "",
            f"- Status: `{status}`",
            f"- Recorded at: `{at}`",
            "",
            "## Summary",
            summary.strip(),
            "",
            "## Next Action",
            next_action.strip(),
        ],
    )


def write_resume_context(path: Path, *, active_run_key: str, current_stage: str, next_action: str, notes: Sequence[str]) -> None:
    write_markdown(
        path,
        [
            "# Resume Context",
            "",
            f"- Active run: `{active_run_key}`",
            f"- Current stage: `{current_stage}`",
            f"- Next action: `{next_action}`",
            "",
            "## Notes",
            *[f"- {line}" for line in notes if str(line).strip()],
        ],
    )


def update_poa_section(path: Path, *, begin_marker: str, end_marker: str, lines: Sequence[str]) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else "# Plan of Action\n"
    block = begin_marker + "\n" + "\n".join(lines).rstrip() + "\n" + end_marker
    pattern = re.compile(re.escape(begin_marker) + r".*?" + re.escape(end_marker), re.DOTALL)
    if pattern.search(text):
        text = pattern.sub(block, text)
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
    path.write_text(text, encoding="utf-8")


def stage_text(stage_contract: Dict[str, Any], field: str, key: str) -> str:
    value = stage_contract.get(field, {})
    if isinstance(value, dict):
        resolved = str(value.get(key, "")).strip()
        if resolved:
            return resolved
    return ""


def render_templates(templates: Sequence[str], **kwargs: Any) -> List[str]:
    rendered: List[str] = []
    for item in templates:
        template = str(item).strip()
        if not template:
            continue
        rendered.append(template.format(**kwargs))
    return rendered


def iso_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def export_audit_packet(*, context: ActiveRunContext) -> Dict[str, Any]:
    config_path = context.repo_root / _AUDIT_EXPORT_PATH
    if not config_path.exists():
        raise PlatformError(
            "Audit export config is missing. Expected .ghdp/orchestrate/audit-export.json.",
            code="E_ORCHESTRATE_AUDIT_EXPORT_CONFIG_MISSING",
            reason=_AUDIT_EXPORT_PATH.as_posix(),
        )
    config = load_orchestrate_json_file(config_path)
    destination_mode = str(config.get("destination_mode", "local")).strip() or "local"
    packet = {
        "schema_version": "1.0",
        "exported_at": iso_now(),
        "repo_root": str(context.repo_root),
        "branch_name": context.branch_name,
        "ticket_key": context.ticket_key,
        "active_run_key": context.active_run_key,
        "stage_status": context.stage_status,
        "branch_state": context.branch_state,
        "decisions": load_orchestrate_json_file(context.decisions_path) if context.decisions_path.exists() else {},
        "artifacts": _collect_run_artifacts(context.run_root),
    }
    summary: Dict[str, Any]
    if destination_mode == "local":
        summary = _export_audit_packet_local(context=context, config=config, packet=packet)
    elif destination_mode == "aws_s3":
        summary = _export_audit_packet_s3(context=context, config=config, packet=packet)
    else:
        raise PlatformError(
            f"Unsupported audit export destination_mode '{destination_mode}'.",
            code="E_ORCHESTRATE_AUDIT_EXPORT_CONFIG_INVALID",
            reason="destination_mode",
        )
    return {"destination_mode": destination_mode, **summary}


def _collect_run_artifacts(run_root: Path) -> List[str]:
    return sorted(
        str(path.relative_to(run_root)).replace("\\", "/")
        for path in run_root.rglob("*")
        if path.is_file()
    )


def _export_audit_packet_local(*, context: ActiveRunContext, config: Dict[str, Any], packet: Dict[str, Any]) -> Dict[str, Any]:
    local_config = config.get("local", {})
    output_dir = str(local_config.get("output_dir", "tmp/orchestrate-audit-exports")).strip() or "tmp/orchestrate-audit-exports"
    export_root = (context.repo_root / output_dir).resolve()
    export_root.mkdir(parents=True, exist_ok=True)
    export_path = export_root / f"{context.branch_slug}__{context.active_run_key}.json"
    write_json(export_path, packet)
    return {"export_path": str(export_path)}


def _export_audit_packet_s3(*, context: ActiveRunContext, config: Dict[str, Any], packet: Dict[str, Any]) -> Dict[str, Any]:
    s3_config = config.get("aws_s3", {})
    bucket = str(s3_config.get("bucket", "")).strip()
    prefix = str(s3_config.get("prefix", "")).strip().strip("/")
    region = str(s3_config.get("region", "")).strip()
    profile = str(s3_config.get("profile", "")).strip()
    enabled = bool(s3_config.get("enabled", False))
    if not enabled or not bucket:
        raise PlatformError(
            "Audit export destination_mode is aws_s3 but bucket configuration is not ready yet.",
            code="E_ORCHESTRATE_AUDIT_EXPORT_CONFIG_INVALID",
            reason="aws_s3",
        )
    export_root = (context.run_root / "audit-export-staging").resolve()
    export_root.mkdir(parents=True, exist_ok=True)
    export_path = export_root / f"{context.branch_slug}__{context.active_run_key}.json"
    write_json(export_path, packet)
    s3_uri = f"s3://{bucket}/{prefix + '/' if prefix else ''}{export_path.name}"
    cmd = ["aws"]
    if profile:
        cmd.extend(["--profile", profile])
    if region:
        cmd.extend(["--region", region])
    cmd.extend(["s3", "cp", str(export_path), s3_uri])
    result = run_cmd(cmd, check=False, cwd=context.repo_root)
    if result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip()
        raise PlatformError(
            f"Failed to export audit packet to {s3_uri}: {output}",
            code="E_ORCHESTRATE_AUDIT_EXPORT_FAILED",
            reason="aws_s3",
        )
    return {"export_path": str(export_path), "s3_uri": s3_uri}
