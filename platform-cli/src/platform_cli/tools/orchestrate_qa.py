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
_SKILLS_ROOT = Path(".ghdp/skills")
_PLUGINS_ROOT = Path(".ghdp/plugins")
_STAGE_COMMIT_PUSH = "stage12_commit_push"
_STAGE_QA_SCENARIOS = "stage13_qa_scenario_design"
_POA_QA_BEGIN = "<!-- GHDP:BEGIN STAGE13_QA_SCENARIOS -->"
_POA_QA_END = "<!-- GHDP:END STAGE13_QA_SCENARIOS -->"


@dataclass
class OrchestrateQaResult:
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
    qa_agent: str
    allowed_skills: List[str]
    allowed_plugins: List[str]
    scenario_count: int
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_qa_scenario_stage(*, repo_root: Path | None = None) -> OrchestrateQaResult:
    resolved_root = resolve_repo_root(repo_root)
    branch_name = current_branch_name(resolved_root)
    if not branch_name:
        raise PlatformError(
            "Could not resolve the current branch for orchestrate QA scenario design.",
            code="E_ORCHESTRATE_BRANCH_UNRESOLVED",
            reason="current_branch",
        )

    runtime_root = resolved_root / _BRANCHES_ROOT / runtime_branch_folder_name(resolved_root, branch_name)
    branch_state_path = runtime_root / "branch_state.json"
    if not branch_state_path.exists():
        raise PlatformError(
            "No branch runtime state exists yet. Complete the earlier orchestrator stages before QA scenario design begins.",
            code="E_ORCHESTRATE_RUN_MISSING",
            reason=str(branch_state_path),
        )

    branch_state = load_orchestrate_json_file(branch_state_path)
    stage_contract = load_stage_contract(stage_id=_STAGE_QA_SCENARIOS, repo_root=resolved_root)
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
    if str(stage_status.get(_STAGE_COMMIT_PUSH, {}).get("status", "")).strip() != "completed":
        raise PlatformError(
            "Stage 12 commit/push must complete before Stage 13 QA scenario design begins.",
            code="E_ORCHESTRATE_STAGE_ORDER",
            reason=_STAGE_COMMIT_PUSH,
        )

    implementation_plan_path = run_root / "implementation_plan.md"
    if not implementation_plan_path.exists():
        raise PlatformError(
            "Stage 13 QA scenario design requires implementation_plan.md from Stage E execution prep.",
            code="E_ORCHESTRATE_IMPLEMENTATION_PLAN_MISSING",
            reason=str(implementation_plan_path),
        )

    intent_path = resolved_root / ".ghdp" / "frbr" / "intent.json"
    if not intent_path.exists():
        raise PlatformError(
            "Stage 13 QA scenario design requires the repo intent under .ghdp/frbr/intent.json.",
            code="E_ORCHESTRATE_INTENT_MISSING",
            reason=str(intent_path),
        )

    agent_contract = load_agent_contract(agent_id="qa-scenario-design", repo_root=resolved_root)
    allowed_skills = _normalize_list(agent_contract.get("allowed_skills", []))
    allowed_plugins = _normalize_list(agent_contract.get("allowed_plugins", []))
    _assert_skill_payloads(resolved_root, allowed_skills)
    _assert_plugin_payloads(resolved_root, allowed_plugins)

    implementation_plan = implementation_plan_path.read_text(encoding="utf-8")
    poa_text = (runtime_root / "poa.md").read_text(encoding="utf-8")
    intent_payload = load_orchestrate_json_file(intent_path)

    implementation_targets = _extract_bullets(implementation_plan, "## Primary Targets")
    regression_targets = _extract_bullets(implementation_plan, "## Regression Targets")
    coverage_goals = _extract_bullets(implementation_plan, "## Coverage Goals")
    acceptance_lines = _extract_acceptance_lines(intent_payload)
    scenario_lines = _build_scenarios(
        ticket_key=str(branch_state.get("ticket_key", "")).strip(),
        branch_name=branch_name,
        implementation_targets=implementation_targets,
        regression_targets=regression_targets,
        coverage_goals=coverage_goals,
        acceptance_lines=acceptance_lines,
        poa_text=poa_text,
    )
    edge_cases = _build_edge_cases(implementation_targets, coverage_goals)

    _write_markdown(
        run_root / "qa_scenario_prompt.md",
        [
            "# Stage 13 QA Scenario Prompt",
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
            "## Acceptance Anchors",
            *[f"- {line}" for line in acceptance_lines],
            "",
            "## Scenario Design Posture",
            *[f"- {line}" for line in stage_contract.get("scenario_design_posture", [])],
            "",
        ],
    )
    _write_json(
        run_root / "qa_scenario_bindings.json",
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
            "acceptance_lines": list(acceptance_lines),
            "implementation_targets": list(implementation_targets),
            "regression_targets": list(regression_targets),
            "coverage_goals": list(coverage_goals),
        },
    )
    _write_markdown(
        run_root / "qa_scenario_plan.md",
        [
            "# QA Scenario Plan",
            "",
            "- Status: `designed`",
            "- Owner agent: `qa-scenario-design`",
            f"- Scenario count: `{len(scenario_lines)}`",
            "",
            "## Acceptance Anchors",
            *[f"- {line}" for line in acceptance_lines],
            "",
            "## Scenarios",
            *[f"- {line}" for line in scenario_lines],
            "",
            "## Edge Cases",
            *[f"- {line}" for line in edge_cases],
            "",
        ],
    )
    _write_markdown(
        run_root / "qa_scenario_summary.md",
        [
            "# QA Scenario Summary",
            "",
            "- Status: `completed`",
            "- Owner agent: `qa-scenario-design`",
            f"- Scenario count: `{len(scenario_lines)}`",
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
        stage_name=_STAGE_QA_SCENARIOS,
        status="completed",
        owner_agent="qa-scenario-design",
        summary="Stage 13 QA scenario design generated acceptance-linked scenarios, edge cases, and downstream bindings.",
        artifacts=[
            "qa_scenario_prompt.md",
            "qa_scenario_bindings.json",
            "qa_scenario_plan.md",
            "qa_scenario_summary.md",
        ],
    )
    _upsert_decisions(
        run_root / "decisions.json",
        [
            {
                "id": "stage13_qa_scenario_design",
                "decision": "Stage 13 QA scenario design produced acceptance-linked validation scenarios for the current branch run.",
                "status": "completed",
                "source": _STAGE_QA_SCENARIOS,
            }
        ],
    )

    next_action = _stage_text(stage_contract, "next_actions", "completed")
    branch_state["status"] = "paused"
    branch_state["current_stage"] = _STAGE_QA_SCENARIOS
    branch_state["next_action"] = next_action
    branch_state["anomaly_flag"] = False
    branch_state["last_updated_at"] = _iso_now()
    branch_state["last_updated_by"] = "qa-scenario-design"
    _write_json(branch_state_path, branch_state)

    _update_poa(
        runtime_root / "poa.md",
        qa_agent=agent_contract["id"],
        allowed_skills=allowed_skills,
        allowed_plugins=allowed_plugins,
        scenario_count=len(scenario_lines),
        edge_case_count=len(edge_cases),
    )
    _write_handoff(
        runtime_root / "handoff.md",
        summary=_stage_text(stage_contract, "handoff_summaries", "completed"),
        next_action=next_action,
        status="paused",
        at=_iso_now(),
    )
    _write_resume_context(
        run_root / "resume_context.md",
        active_run_key=active_run_key,
        current_stage=_STAGE_QA_SCENARIOS,
        next_action=next_action,
        notes=_render_templates(
            stage_contract.get("resume_note_templates", []),
            qa_agent=agent_contract["id"],
            allowed_skill_count=len(allowed_skills),
            allowed_plugin_count=len(allowed_plugins),
            scenario_count=len(scenario_lines),
            edge_case_count=len(edge_cases),
        ),
    )

    return OrchestrateQaResult(
        repo_root=str(resolved_root),
        branch_name=branch_name,
        branch_slug=slugify_branch_name(branch_name),
        ticket_key=str(branch_state.get("ticket_key", "")).strip(),
        active_run_key=active_run_key,
        action="qa_scenarios",
        status="paused",
        current_stage=_STAGE_QA_SCENARIOS,
        next_action=next_action,
        branch_runtime_root=str(runtime_root),
        qa_agent=str(agent_contract["id"]),
        allowed_skills=allowed_skills,
        allowed_plugins=allowed_plugins,
        scenario_count=len(scenario_lines),
        message=_stage_text(stage_contract, "messages", "completed"),
    )


def _extract_bullets(text: str, header: str) -> List[str]:
    pattern = re.compile(rf"{re.escape(header)}\n((?:- .+\n)+)")
    match = pattern.search(text)
    if not match:
        return []
    return [line[2:].strip().strip("`") for line in match.group(1).splitlines() if line.startswith("- ")]


def _extract_acceptance_lines(intent_payload: Dict[str, Any]) -> List[str]:
    summary = str(intent_payload.get("summary", "")).strip()
    match = re.search(r"Acceptance Criteria\s*(.+)", summary, re.DOTALL)
    if not match:
        return ["Validate that the Phase 1 orchestrator flow remains trustworthy, practical, and testable today."]
    lines = []
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            lines.append(stripped[2:].strip())
    return lines or ["Validate the acceptance criteria captured in the branch intent."]


def _build_scenarios(
    *,
    ticket_key: str,
    branch_name: str,
    implementation_targets: Sequence[str],
    regression_targets: Sequence[str],
    coverage_goals: Sequence[str],
    acceptance_lines: Sequence[str],
    poa_text: str,
) -> List[str]:
    scenarios: List[str] = []
    ticket_label = ticket_key or "branch"
    scenarios.append(f"Validate the happy-path orchestrator run for `{ticket_label}` on `{branch_name}` from `orchestrate start` through `commit-push` without losing `.ghdp/orchestrate` state.")
    for index, line in enumerate(acceptance_lines[:3], start=1):
        scenarios.append(f"Acceptance scenario {index}: confirm `{line}` with repo-backed artifacts and CLI-visible state transitions.")
    for target in implementation_targets[:3]:
        scenarios.append(f"Target coverage: change or inspect `{target}` and verify the orchestrator can still explain the touched scope through the POA and runtime artifacts.")
    for target in regression_targets[:2]:
        scenarios.append(f"Regression scenario: run `{target}` or equivalent protection to ensure earlier orchestrate stages still behave deterministically after Stage 12.")
    if "Stage 12 Commit Push" in poa_text:
        scenarios.append("Commit/push continuity: confirm Stage 12 committed branch artifacts remain understandable when Stage 13 rewrites QA artifacts afterward.")
    for goal in coverage_goals[:2]:
        scenarios.append(f"Coverage scenario: validate that `{goal}` is represented in the scenario plan and can drive a concrete follow-up test task.")
    scenarios.append("Failure-path scenario: simulate a missing or stale run artifact and confirm the next stage can identify the gap before running tests.")
    scenarios.append("Resume-path scenario: pause after QA design, reopen the branch later, and confirm `resume_context.md` still points the next owner to regression and coverage work.")
    return scenarios


def _build_edge_cases(implementation_targets: Sequence[str], coverage_goals: Sequence[str]) -> List[str]:
    items = [
        "The active branch has a valid run key but stale Stage 12 artifacts from an older checkpoint.",
        "The QA scenario plan is regenerated after new runtime files are added and must stay deterministic.",
        "Touched-scope regression focuses only on orchestrate files while a missed `.ghdp` artifact mutation slips in.",
        "Coverage goals drift into broad churn instead of staying proportional to the current branch change.",
    ]
    if implementation_targets:
        items.append(f"At least one scenario must explicitly validate runtime behavior around `{implementation_targets[0]}`.")
    if coverage_goals:
        items.append(f"At least one scenario must preserve the coverage posture: `{coverage_goals[0]}`.")
    return items


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
            "Stage 13 QA scenario design requires repo-level skill payloads under .ghdp/skills/<id>/SKILL.md.",
            code="E_ORCHESTRATE_SKILL_PAYLOAD_MISSING",
            reason=", ".join(missing),
        )


def _assert_plugin_payloads(repo_root: Path, plugin_ids: Sequence[str]) -> None:
    missing = [plugin_id for plugin_id in plugin_ids if not (repo_root / _PLUGINS_ROOT / plugin_id / "plugin.json").exists()]
    if missing:
        raise PlatformError(
            "Stage 13 QA scenario design requires repo-level plugin payloads under .ghdp/plugins/<id>/plugin.json.",
            code="E_ORCHESTRATE_PLUGIN_PAYLOAD_MISSING",
            reason=", ".join(missing),
        )


def _update_poa(
    path: Path,
    *,
    qa_agent: str,
    allowed_skills: Sequence[str],
    allowed_plugins: Sequence[str],
    scenario_count: int,
    edge_case_count: int,
) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    managed_block = "\n".join(
        [
            _POA_QA_BEGIN,
            "## Stage 13 QA Scenario Design",
            "",
            f"- QA agent: `{qa_agent}`",
            f"- Allowed skill count: `{len(allowed_skills)}`",
            f"- Allowed plugin count: `{len(allowed_plugins)}`",
            f"- Scenario count: `{scenario_count}`",
            f"- Edge-case count: `{edge_case_count}`",
            "",
            "### Allowed Skills",
            *[f"- `{item}`" for item in allowed_skills],
            "",
            "### Allowed Plugins",
            *[f"- `{item}`" for item in allowed_plugins],
            "",
            _POA_QA_END,
            "",
        ]
    )
    if _POA_QA_BEGIN in existing and _POA_QA_END in existing:
        pattern = re.compile(rf"{re.escape(_POA_QA_BEGIN)}.*?{re.escape(_POA_QA_END)}\n?", re.DOTALL)
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
    existing["ended_at"] = _iso_now()
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
