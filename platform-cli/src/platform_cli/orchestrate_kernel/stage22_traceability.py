from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List

from platform_cli.manifests.orchestrate_stage_load import load_stage_contract
from platform_cli.orchestrate_kernel.runtime_support import (
    assert_stage_completed,
    export_audit_packet,
    iso_now,
    render_templates,
    resolve_active_run_context,
    stage_text,
    update_poa_section,
    upsert_decisions,
    upsert_stage_status,
    write_handoff,
    write_json,
    write_markdown,
    write_resume_context,
)
from platform_cli.tools.orchestrate_contract import load_agent_contract


_STAGE_PR = "stage21_pr_external_integration"
_STAGE_HISTORIAN = "stage22_traceability_capture"
_POA_BEGIN = "<!-- GHDP:BEGIN STAGE22_HISTORIAN -->"
_POA_END = "<!-- GHDP:END STAGE22_HISTORIAN -->"


@dataclass
class HistorianStageResult:
    repo_root: str
    branch_name: str
    active_run_key: str
    status: str
    current_stage: str
    next_action: str
    historian_agent: str
    final_status: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_traceability_capture_stage(*, repo_root: Path | None = None) -> HistorianStageResult:
    context = resolve_active_run_context(repo_root=repo_root)
    assert_stage_completed(context.stage_status, _STAGE_PR)
    stage_contract = load_stage_contract(stage_id=_STAGE_HISTORIAN, repo_root=context.repo_root)
    agent_contract = load_agent_contract(agent_id="traceability-historian", repo_root=context.repo_root)
    allowed_skills = _normalize_list(agent_contract.get("allowed_skills", []))
    allowed_plugins = _normalize_list(agent_contract.get("allowed_plugins", []))

    scenario_result_path = context.run_root / "subagent_execution_result.json"
    scenario_payload = load_or_default_json(scenario_result_path)
    executed_agent_count = len(scenario_payload.get("outputs", [])) if isinstance(scenario_payload.get("outputs"), list) else 0
    audit_export = export_audit_packet(context=context)

    write_markdown(
        context.run_root / "historian_closeout.md",
        [
            "# Stage 22 Historian Closeout",
            "",
            f"- Status: `completed`",
            f"- Historian: `{agent_contract['id']}`",
            f"- Active run: `{context.active_run_key}`",
            f"- Scenario packet present: `{'yes' if scenario_payload else 'no'}`",
            f"- Executed sub-agents: `{executed_agent_count}`",
            f"- Audit export mode: `{audit_export.get('destination_mode', 'local')}`",
            f"- Audit export path: `{audit_export.get('export_path', '(missing)')}`",
        ],
    )
    write_json(context.run_root / "audit_export_summary.json", {"schema_version": "1.0", **audit_export})
    upsert_stage_status(
        context.stage_status_path,
        stage_name=_STAGE_HISTORIAN,
        status="completed",
        owner_agent="traceability-historian",
        summary="Stage 22 finalized the run packet and historian closeout artifacts.",
        artifacts=["historian_closeout.md", "subagent_execution_plan.json", "subagent_execution_result.json", "audit_export_summary.json"],
    )
    upsert_decisions(
        context.decisions_path,
        [
            {
                "id": _STAGE_HISTORIAN,
                "decision": "Traceability and final resume state were captured for the branch run.",
                "status": "completed",
                "source": _STAGE_HISTORIAN,
            }
        ],
    )
    next_action = stage_text(stage_contract, "next_actions", "completed")
    context.branch_state["status"] = "paused"
    context.branch_state["current_stage"] = _STAGE_HISTORIAN
    context.branch_state["next_action"] = next_action
    context.branch_state["anomaly_flag"] = False
    context.branch_state["last_updated_at"] = iso_now()
    context.branch_state["last_updated_by"] = "traceability-historian"
    write_json(context.branch_state_path, context.branch_state)

    update_poa_section(
        context.poa_path,
        begin_marker=_POA_BEGIN,
        end_marker=_POA_END,
        lines=[
            "## Stage 22 Traceability Capture",
            f"- Owner agent: `traceability-historian`",
            f"- Allowed skills: {', '.join(f'`{item}`' for item in allowed_skills) or '(none)'}",
            f"- Allowed plugins: {', '.join(f'`{item}`' for item in allowed_plugins) or '(none)'}",
            f"- Scenario packet present: `{'yes' if scenario_payload else 'no'}`",
            f"- Executed sub-agents: `{executed_agent_count}`",
            f"- Audit export path: `{audit_export.get('export_path', '(missing)')}`",
        ],
    )
    write_handoff(
        context.handoff_path,
        summary=stage_text(stage_contract, "handoff_summaries", "completed"),
        next_action=next_action,
        status="paused",
        at=iso_now(),
    )
    write_resume_context(
        context.resume_context_path,
        active_run_key=context.active_run_key,
        current_stage=_STAGE_HISTORIAN,
        next_action=next_action,
        notes=render_templates(
            stage_contract.get("resume_note_templates", []),
            historian_agent=agent_contract["id"],
            scenario_id=str(scenario_payload.get("scenario_id", "pending") or "pending"),
            executed_agent_count=executed_agent_count,
            final_status="completed",
        ),
    )
    return HistorianStageResult(
        repo_root=str(context.repo_root),
        branch_name=context.branch_name,
        active_run_key=context.active_run_key,
        status="paused",
        current_stage=_STAGE_HISTORIAN,
        next_action=next_action,
        historian_agent=agent_contract["id"],
        final_status="completed",
        message=stage_text(stage_contract, "messages", "completed") or "Stage 22 traceability capture completed.",
    )


def load_or_default_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
