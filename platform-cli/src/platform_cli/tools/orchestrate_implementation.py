from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence

from platform_cli.core.errors import PlatformError
from platform_cli.core.repo_roots import resolve_repo_root
from platform_cli.manifests.orchestrate_load import load_orchestrate_json_file
from platform_cli.manifests.orchestrate_stage_load import load_stage_contract
from platform_cli.tools.orchestrate_contract import load_agent_contract, runtime_branch_folder_name, slugify_branch_name
from platform_cli.tools.repo_ready_generation import current_branch_name


_BRANCHES_ROOT = Path(".ghdp/orchestrate/branches")
_STAGE_IMPLEMENTATION = "stage11_implementation"
_POA_IMPLEMENTATION_BEGIN = "<!-- GHDP:BEGIN STAGE11_IMPLEMENTATION -->"
_POA_IMPLEMENTATION_END = "<!-- GHDP:END STAGE11_IMPLEMENTATION -->"
_SKILLS_ROOT = Path(".ghdp/skills")
_PLUGINS_ROOT = Path(".ghdp/plugins")


@dataclass
class OrchestrateImplementationResult:
    repo_root: str
    branch_name: str
    branch_slug: str
    ticket_key: str
    active_run_key: str
    action: str
    status: str
    current_stage: str
    next_action: str
    branch_runtime_root: str
    implementation_agent: str
    allowed_skills: List[str]
    allowed_plugins: List[str]
    implementation_targets: List[str]
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_implementation_stage(*, repo_root: Path | None = None) -> OrchestrateImplementationResult:
    resolved_root = resolve_repo_root(repo_root)
    branch_name = current_branch_name(resolved_root)
    if not branch_name:
        raise PlatformError(
            "Could not resolve the current branch for orchestrate implementation.",
            code="E_ORCHESTRATE_BRANCH_UNRESOLVED",
            reason="current_branch",
        )

    runtime_root = resolved_root / _BRANCHES_ROOT / runtime_branch_folder_name(resolved_root, branch_name)
    branch_state_path = runtime_root / "branch_state.json"
    if not branch_state_path.exists():
        raise PlatformError(
            "No branch runtime state exists yet. Complete the earlier orchestrator stages before implementation begins.",
            code="E_ORCHESTRATE_RUN_MISSING",
            reason=str(branch_state_path),
        )

    branch_state = load_orchestrate_json_file(branch_state_path)
    stage_contract = load_stage_contract(stage_id=_STAGE_IMPLEMENTATION, repo_root=resolved_root)
    active_run_key = str(branch_state.get("active_run_key", "")).strip()
    if not active_run_key:
        raise PlatformError(
            "Branch runtime state does not contain an active run key.",
            code="E_ORCHESTRATE_RUN_MISSING",
            reason="active_run_key",
        )

    run_root = runtime_root / "runs" / active_run_key
    stage_status_path = run_root / "stage_status.json"
    stage_status = load_orchestrate_json_file(stage_status_path)
    if str(stage_status.get("stage_e_execution_prep", {}).get("status", "")).strip() != "completed":
        raise PlatformError(
            "Stage E execution prep must complete before Stage 11 implementation begins.",
            code="E_ORCHESTRATE_STAGE_ORDER",
            reason="stage_e_execution_prep",
        )

    implementation_plan_path = run_root / "implementation_plan.md"
    if not implementation_plan_path.exists():
        raise PlatformError(
            "Stage 11 implementation requires implementation_plan.md from Stage E execution prep.",
            code="E_ORCHESTRATE_IMPLEMENTATION_PLAN_MISSING",
            reason=str(implementation_plan_path),
        )

    implementation_plan = implementation_plan_path.read_text(encoding="utf-8")
    implementation_targets = _extract_bullets(implementation_plan, "## Primary Targets")
    capability_reuse_lines = _extract_bullets(implementation_plan, "## Capability Reuse Notes")

    agent_contract = load_agent_contract(agent_id="implementation", repo_root=resolved_root)
    allowed_skills = _normalize_list(agent_contract.get("allowed_skills", []))
    allowed_plugins = _normalize_list(agent_contract.get("allowed_plugins", []))
    _assert_skill_payloads(resolved_root, allowed_skills)
    _assert_plugin_payloads(resolved_root, allowed_plugins)

    _write_markdown(
        run_root / "implementation_prompt.md",
        [
            "# Stage 11 Implementation Prompt",
            "",
            f"- Agent: `{agent_contract['id']}`",
            f"- Role: `{agent_contract['role']}`",
            f"- Branch: `{branch_name}`",
            f"- Ticket: `{str(branch_state.get('ticket_key', '')).strip() or '(missing)'}`",
            "",
            "## Prompt Contract",
            *[f"- {line}" for line in agent_contract.get("prompt_contract", [])],
            "",
            "## Allowed Skills",
            *[f"- `{item}`" for item in allowed_skills],
            "",
            "## Allowed Plugins",
            *[f"- `{item}`" for item in allowed_plugins],
            "",
            "## Primary Targets",
            *[f"- `{item}`" for item in implementation_targets],
            "",
            "## Capability Reuse Notes",
            *[f"- {item}" for item in capability_reuse_lines],
            "",
            "## Expected Delivery Posture",
            *[f"- {line}" for line in stage_contract.get("delivery_posture", [])],
            "",
        ],
    )
    _write_json(
        run_root / "implementation_bindings.json",
        {
            "schema_version": "1.0",
            "agent_id": agent_contract["id"],
            "allowed_skills": [
                {
                    "id": skill_id,
                    "path": str((resolved_root / _SKILLS_ROOT / skill_id / "SKILL.md").relative_to(resolved_root)).replace("\\", "/"),
                }
                for skill_id in allowed_skills
            ],
            "allowed_plugins": [
                {
                    "id": plugin_id,
                    "path": str((resolved_root / _PLUGINS_ROOT / plugin_id / "plugin.json").relative_to(resolved_root)).replace("\\", "/"),
                }
                for plugin_id in allowed_plugins
            ],
            "implementation_targets": list(implementation_targets),
        },
    )
    _write_markdown(
        run_root / "implementation_summary.md",
        [
            "# Implementation Summary",
            "",
            "- Status: `ready_to_execute`",
            "- Owner agent: `implementation`",
            "",
            "## Ready Inputs",
            *[f"- `{item}`" for item in stage_contract.get("summary_ready_inputs", [])],
            "",
            "## Expected Next Step",
            f"- {str(stage_contract.get('summary_expected_next_step', '')).strip()}",
            "",
        ],
    )

    _upsert_stage_status(
        stage_status_path,
        stage_name=_STAGE_IMPLEMENTATION,
        status="in_progress",
        owner_agent="implementation",
        summary="Stage 11 implementation is active with explicit agent skill/plugin bindings and execution artifacts.",
        artifacts=[
            "implementation_plan.md",
            "implementation_prompt.md",
            "implementation_bindings.json",
            "implementation_summary.md",
        ],
    )
    _upsert_decisions(
        run_root / "decisions.json",
        [
            {
                "id": "stage11_implementation_activation",
                "decision": "Stage 11 implementation was activated from the repo-level implementation agent contract.",
                "status": "accepted",
                "source": "stage11_implementation",
            }
        ],
    )

    next_action = _stage_text(stage_contract, "next_actions", "active")
    branch_state["status"] = "in_progress"
    branch_state["current_stage"] = _STAGE_IMPLEMENTATION
    branch_state["next_action"] = next_action
    branch_state["anomaly_flag"] = False
    branch_state["last_updated_at"] = _iso_now()
    branch_state["last_updated_by"] = "implementation"
    _write_json(branch_state_path, branch_state)

    _update_poa(
        runtime_root / "poa.md",
        implementation_agent=agent_contract["id"],
        allowed_skills=allowed_skills,
        allowed_plugins=allowed_plugins,
        implementation_targets=implementation_targets,
    )
    _write_handoff(
        runtime_root / "handoff.md",
        summary=_stage_text(stage_contract, "handoff_summaries", "active"),
        next_action=next_action,
        status="in_progress",
        at=_iso_now(),
    )
    _write_resume_context(
        run_root / "resume_context.md",
        active_run_key=active_run_key,
        current_stage=_STAGE_IMPLEMENTATION,
        next_action=next_action,
        notes=_render_templates(
            stage_contract.get("resume_note_templates", []),
            implementation_agent=agent_contract["id"],
            allowed_skill_count=len(allowed_skills),
            allowed_plugin_count=len(allowed_plugins),
            implementation_target_count=len(implementation_targets),
        ),
    )

    return OrchestrateImplementationResult(
        repo_root=str(resolved_root),
        branch_name=branch_name,
        branch_slug=slugify_branch_name(branch_name),
        ticket_key=str(branch_state.get("ticket_key", "")).strip(),
        active_run_key=active_run_key,
        action="implementation",
        status="in_progress",
        current_stage=_STAGE_IMPLEMENTATION,
        next_action=next_action,
        branch_runtime_root=str(runtime_root),
        implementation_agent=str(agent_contract["id"]),
        allowed_skills=allowed_skills,
        allowed_plugins=allowed_plugins,
        implementation_targets=implementation_targets,
        message=_stage_text(stage_contract, "messages", "active"),
    )


def _extract_bullets(text: str, header: str) -> List[str]:
    pattern = re.compile(rf"{re.escape(header)}\n((?:- .+\n)+)")
    match = pattern.search(text)
    if not match:
        return []
    return [line[2:].strip().strip("`") for line in match.group(1).splitlines() if line.startswith("- ")]


def _normalize_list(items: Sequence[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for item in items:
        normalized = str(item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _assert_skill_payloads(repo_root: Path, skill_ids: Sequence[str]) -> None:
    missing = [skill_id for skill_id in skill_ids if not (repo_root / _SKILLS_ROOT / skill_id / "SKILL.md").exists()]
    if missing:
        raise PlatformError(
            "Stage 11 implementation requires repo-level skill payloads under .ghdp/skills/<id>/SKILL.md.",
            code="E_ORCHESTRATE_SKILL_PAYLOAD_MISSING",
            reason=", ".join(missing),
        )


def _assert_plugin_payloads(repo_root: Path, plugin_ids: Sequence[str]) -> None:
    missing = [plugin_id for plugin_id in plugin_ids if not (repo_root / _PLUGINS_ROOT / plugin_id / "plugin.json").exists()]
    if missing:
        raise PlatformError(
            "Stage 11 implementation requires repo-level plugin payloads under .ghdp/plugins/<id>/plugin.json.",
            code="E_ORCHESTRATE_PLUGIN_PAYLOAD_MISSING",
            reason=", ".join(missing),
        )


def _update_poa(
    path: Path,
    *,
    implementation_agent: str,
    allowed_skills: Sequence[str],
    allowed_plugins: Sequence[str],
    implementation_targets: Sequence[str],
) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    managed_block = "\n".join(
        [
            _POA_IMPLEMENTATION_BEGIN,
            "## Stage 11 Implementation Activation",
            "",
            f"- Implementation agent: `{implementation_agent}`",
            f"- Allowed skill count: `{len(allowed_skills)}`",
            f"- Allowed plugin count: `{len(allowed_plugins)}`",
            f"- Target count: `{len(implementation_targets)}`",
            "",
            "### Allowed Skills",
            *[f"- `{item}`" for item in allowed_skills],
            "",
            "### Allowed Plugins",
            *[f"- `{item}`" for item in allowed_plugins],
            "",
            "### Active Targets",
            *[f"- `{item}`" for item in implementation_targets],
            "",
            _POA_IMPLEMENTATION_END,
            "",
        ]
    )
    if _POA_IMPLEMENTATION_BEGIN in existing and _POA_IMPLEMENTATION_END in existing:
        pattern = re.compile(rf"{re.escape(_POA_IMPLEMENTATION_BEGIN)}.*?{re.escape(_POA_IMPLEMENTATION_END)}\n?", re.DOTALL)
        updated = pattern.sub(managed_block, existing)
    else:
        updated = existing + ("\n\n" if existing and not existing.endswith("\n\n") else "") + managed_block
    path.write_text(updated, encoding="utf-8")


def _upsert_stage_status(
    path: Path,
    *,
    stage_name: str,
    status: str,
    owner_agent: str,
    summary: str,
    artifacts: Sequence[str],
) -> None:
    payload = load_orchestrate_json_file(path) if path.exists() else {}
    existing = payload.get(stage_name)
    if not isinstance(existing, dict):
        existing = {"started_at": _iso_now(), "retry_count": 0}
    if "started_at" not in existing or not str(existing.get("started_at", "")).strip():
        existing["started_at"] = _iso_now()
    existing["status"] = status
    existing["owner_agent"] = owner_agent
    existing["summary"] = summary
    existing["artifacts"] = list(artifacts)
    payload[stage_name] = existing
    _write_json(path, payload)


def _upsert_decisions(path: Path, decisions: Sequence[Dict[str, Any]]) -> None:
    payload = load_orchestrate_json_file(path) if path.exists() else {"schema_version": "1.0", "decisions": []}
    existing = payload.get("decisions", [])
    indexed = {str(item.get("id", "")).strip(): item for item in existing if isinstance(item, dict)}
    for decision in decisions:
        indexed[str(decision.get("id", "")).strip()] = decision
    payload["schema_version"] = str(payload.get("schema_version", "1.0")).strip() or "1.0"
    payload["decisions"] = list(indexed.values())
    _write_json(path, payload)


def _write_markdown(path: Path, lines: Sequence[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_handoff(path: Path, *, summary: str, next_action: str, status: str, at: str) -> None:
    path.write_text(
        "\n".join(
            [
                "# Handoff",
                "",
                f"- Updated at: `{at}`",
                f"- Status: {status}",
                "",
                "## Summary",
                summary,
                "",
                "## Next Steps",
                f"- {next_action}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_resume_context(path: Path, *, active_run_key: str, current_stage: str, next_action: str, notes: Sequence[str]) -> None:
    path.write_text(
        "\n".join(
            [
                "# Resume Context",
                "",
                f"Active run: `{active_run_key}`",
                f"Current focus: `{current_stage}`",
                f"Next action: {next_action}",
                "",
                "## Activity Log",
                *[f"- {note}" for note in notes],
                "",
            ]
        ),
        encoding="utf-8",
    )


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _stage_text(contract: Dict[str, Any], section: str, key: str) -> str:
    payload = contract.get(section, {})
    if not isinstance(payload, dict):
        return ""
    return str(payload.get(key, "")).strip()


def _render_templates(templates: Sequence[str], **context: Any) -> List[str]:
    return [str(template).format(**context) for template in templates if str(template).strip()]


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
