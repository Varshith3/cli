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
_STAGE_QA = "stage13_qa_scenario_design"
_STAGE_REGRESSION = "stage14_touched_scope_regression"
_POA_REGRESSION_BEGIN = "<!-- GHDP:BEGIN STAGE14_REGRESSION -->"
_POA_REGRESSION_END = "<!-- GHDP:END STAGE14_REGRESSION -->"


@dataclass
class OrchestrateRegressionResult:
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
    regression_agent: str
    allowed_skills: List[str]
    allowed_plugins: List[str]
    selected_tests: List[str]
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_regression_stage(*, repo_root: Path | None = None) -> OrchestrateRegressionResult:
    resolved_root = resolve_repo_root(repo_root)
    branch_name = current_branch_name(resolved_root)
    if not branch_name:
        raise PlatformError(
            "Could not resolve the current branch for orchestrate regression validation.",
            code="E_ORCHESTRATE_BRANCH_UNRESOLVED",
            reason="current_branch",
        )

    runtime_root = resolved_root / _BRANCHES_ROOT / runtime_branch_folder_name(resolved_root, branch_name)
    branch_state_path = runtime_root / "branch_state.json"
    if not branch_state_path.exists():
        raise PlatformError(
            "No branch runtime state exists yet. Complete the earlier orchestrator stages before regression validation begins.",
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
    stage_status = load_orchestrate_json_file(stage_status_path)
    if str(stage_status.get(_STAGE_QA, {}).get("status", "")).strip() != "completed":
        raise PlatformError(
            "Stage 13 QA scenario design must complete before Stage 14 touched-scope regression validation begins.",
            code="E_ORCHESTRATE_STAGE_ORDER",
            reason=_STAGE_QA,
        )

    regression_plan_path = run_root / "regression_plan.md"
    if not regression_plan_path.exists():
        raise PlatformError(
            "Stage 14 regression validation requires regression_plan.md from Stage E execution prep.",
            code="E_ORCHESTRATE_REGRESSION_PLAN_MISSING",
            reason=str(regression_plan_path),
        )

    qa_plan_path = run_root / "qa_scenario_plan.md"
    if not qa_plan_path.exists():
        raise PlatformError(
            "Stage 14 regression validation requires qa_scenario_plan.md from Stage 13.",
            code="E_ORCHESTRATE_QA_PLAN_MISSING",
            reason=str(qa_plan_path),
        )

    stage_contract = load_stage_contract(stage_id=_STAGE_REGRESSION, repo_root=resolved_root)
    agent_contract = load_agent_contract(agent_id="regression-validation", repo_root=resolved_root)
    allowed_skills = _normalize_list(agent_contract.get("allowed_skills", []))
    allowed_plugins = _normalize_list(agent_contract.get("allowed_plugins", []))
    _assert_skill_payloads(resolved_root, allowed_skills)
    _assert_plugin_payloads(resolved_root, allowed_plugins)

    regression_plan = regression_plan_path.read_text(encoding="utf-8")
    qa_plan = qa_plan_path.read_text(encoding="utf-8")
    poa_text = (runtime_root / "poa.md").read_text(encoding="utf-8")

    selected_tests = _select_regression_tests(
        mandatory_targets=_extract_bullets(regression_plan, "## Mandatory Regression Targets"),
        qa_targets=_extract_target_coverage_lines(qa_plan),
        impacted_areas=_extract_bullets(poa_text, "### Impacted Areas"),
    )
    selection_reasons = _build_selection_reasons(selected_tests, poa_text, qa_plan)

    _write_markdown(
        run_root / "regression_prompt.md",
        [
            "# Stage 14 Regression Prompt",
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
            "## Regression Selection Posture",
            *[f"- {line}" for line in stage_contract.get("selection_posture", [])],
            "",
        ],
    )
    _write_json(
        run_root / "regression_bindings.json",
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
            "selected_tests": list(selected_tests),
        },
    )
    _write_markdown(
        run_root / "regression_selection.md",
        [
            "# Regression Selection",
            "",
            "- Status: `selected`",
            "- Owner agent: `regression-validation`",
            f"- Selected test count: `{len(selected_tests)}`",
            "",
            "## Selected Tests",
            *[f"- `{item}`" for item in selected_tests],
            "",
            "## Selection Reasons",
            *[f"- {line}" for line in selection_reasons],
            "",
        ],
    )
    _write_markdown(
        run_root / "regression_summary.md",
        [
            "# Regression Summary",
            "",
            "- Status: `completed`",
            "- Owner agent: `regression-validation`",
            f"- Selected test count: `{len(selected_tests)}`",
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
        stage_name=_STAGE_REGRESSION,
        status="completed",
        owner_agent="regression-validation",
        summary="Stage 14 touched-scope regression validation selected the focused regression surface and documented why each test is included.",
        artifacts=[
            "regression_prompt.md",
            "regression_bindings.json",
            "regression_selection.md",
            "regression_summary.md",
        ],
    )
    _upsert_decisions(
        run_root / "decisions.json",
        [
            {
                "id": "stage14_touched_scope_regression",
                "decision": "Stage 14 regression validation selected the touched-scope regression set for downstream execution.",
                "status": "completed",
                "source": _STAGE_REGRESSION,
            }
        ],
    )

    next_action = _stage_text(stage_contract, "next_actions", "completed")
    branch_state["status"] = "paused"
    branch_state["current_stage"] = _STAGE_REGRESSION
    branch_state["next_action"] = next_action
    branch_state["anomaly_flag"] = False
    branch_state["last_updated_at"] = _iso_now()
    branch_state["last_updated_by"] = "regression-validation"
    _write_json(branch_state_path, branch_state)

    _update_poa(
        runtime_root / "poa.md",
        regression_agent=agent_contract["id"],
        allowed_skills=allowed_skills,
        allowed_plugins=allowed_plugins,
        selected_tests=selected_tests,
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
        current_stage=_STAGE_REGRESSION,
        next_action=next_action,
        notes=_render_templates(
            stage_contract.get("resume_note_templates", []),
            regression_agent=agent_contract["id"],
            allowed_skill_count=len(allowed_skills),
            allowed_plugin_count=len(allowed_plugins),
            selected_test_count=len(selected_tests),
            selection_reason_count=len(selection_reasons),
        ),
    )

    return OrchestrateRegressionResult(
        repo_root=str(resolved_root),
        branch_name=branch_name,
        branch_slug=slugify_branch_name(branch_name),
        ticket_key=str(branch_state.get("ticket_key", "")).strip(),
        active_run_key=active_run_key,
        action="regression",
        status="paused",
        current_stage=_STAGE_REGRESSION,
        next_action=next_action,
        branch_runtime_root=str(runtime_root),
        regression_agent=str(agent_contract["id"]),
        allowed_skills=allowed_skills,
        allowed_plugins=allowed_plugins,
        selected_tests=selected_tests,
        message=_stage_text(stage_contract, "messages", "completed"),
    )


def _select_regression_tests(
    *,
    mandatory_targets: Sequence[str],
    qa_targets: Sequence[str],
    impacted_areas: Sequence[str],
) -> List[str]:
    selected = list(mandatory_targets)
    if any("orchestrate_contract.py" in item for item in impacted_areas):
        selected.append("platform-cli/tests/test_orchestrate_contract.py")
    if any("orchestrate_runtime.py" in item for item in impacted_areas):
        selected.append("platform-cli/tests/test_orchestrate_runtime.py")
    if any(item.startswith(".ghdp/") for item in impacted_areas):
        selected.append("platform-cli/tests/test_orchestrate_manifests.py")
    if qa_targets:
        selected.append("platform-cli/tests/test_orchestrate_qa.py")
    return _normalize_list(selected)


def _build_selection_reasons(selected_tests: Sequence[str], poa_text: str, qa_plan: str) -> List[str]:
    reasons: List[str] = []
    for test_path in selected_tests:
        if test_path.endswith("test_orchestrate_contract.py"):
            reasons.append(f"`{test_path}` protects repo-owned contract validation and stage-registry integrity.")
        elif test_path.endswith("test_orchestrate_runtime.py"):
            reasons.append(f"`{test_path}` protects lifecycle bootstrap, pause/resume, and branch-state mutation behavior.")
        elif test_path.endswith("test_orchestrate_front_door.py"):
            reasons.append(f"`{test_path}` keeps the intake/classification path stable before later execution stages build on it.")
        elif test_path.endswith("test_orchestrate_review.py"):
            reasons.append(f"`{test_path}` protects the architecture and UX/DX review loop that gates execution.")
        elif test_path.endswith("test_orchestrate_manifests.py"):
            reasons.append(f"`{test_path}` covers `.ghdp` contract validation because the touched surface includes repo-owned orchestrator artifacts.")
        elif test_path.endswith("test_orchestrate_qa.py"):
            reasons.append(f"`{test_path}` keeps the Stage 13 QA packet aligned with the scenarios that Stage 14 inherits.")
        else:
            reasons.append(f"`{test_path}` remains in scope because it is part of the focused touched-surface regression set.")
    if "Stage 13 QA Scenario Design" in poa_text:
        reasons.append("Stage 13 is now upstream of regression selection, so its packet must remain stable when later stages consume it.")
    if "Failure-path scenario" in qa_plan:
        reasons.append("The QA plan explicitly includes failure-path coverage, so regression selection preserves the narrow tests that surface those failures early.")
    return reasons


def _extract_bullets(text: str, header: str) -> List[str]:
    pattern = re.compile(rf"{re.escape(header)}\n((?:- .+\n)+)")
    match = pattern.search(text)
    if not match:
        return []
    return [line[2:].strip().strip("`") for line in match.group(1).splitlines() if line.startswith("- ")]


def _extract_target_coverage_lines(qa_plan: str) -> List[str]:
    return [line[2:].strip() for line in qa_plan.splitlines() if line.startswith("- Target coverage:")]


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
            "Stage 14 regression validation requires repo-level skill payloads under .ghdp/skills/<id>/SKILL.md.",
            code="E_ORCHESTRATE_SKILL_PAYLOAD_MISSING",
            reason=", ".join(missing),
        )


def _assert_plugin_payloads(repo_root: Path, plugin_ids: Sequence[str]) -> None:
    missing = [plugin_id for plugin_id in plugin_ids if not (repo_root / _PLUGINS_ROOT / plugin_id / "plugin.json").exists()]
    if missing:
        raise PlatformError(
            "Stage 14 regression validation requires repo-level plugin payloads under .ghdp/plugins/<id>/plugin.json.",
            code="E_ORCHESTRATE_PLUGIN_PAYLOAD_MISSING",
            reason=", ".join(missing),
        )


def _update_poa(
    path: Path,
    *,
    regression_agent: str,
    allowed_skills: Sequence[str],
    allowed_plugins: Sequence[str],
    selected_tests: Sequence[str],
) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    managed_block = "\n".join(
        [
            _POA_REGRESSION_BEGIN,
            "## Stage 14 Touched Scope Regression",
            "",
            f"- Regression agent: `{regression_agent}`",
            f"- Allowed skill count: `{len(allowed_skills)}`",
            f"- Allowed plugin count: `{len(allowed_plugins)}`",
            f"- Selected regression test count: `{len(selected_tests)}`",
            "",
            "### Allowed Skills",
            *[f"- `{item}`" for item in allowed_skills],
            "",
            "### Allowed Plugins",
            *[f"- `{item}`" for item in allowed_plugins],
            "",
            "### Selected Tests",
            *[f"- `{item}`" for item in selected_tests],
            "",
            _POA_REGRESSION_END,
            "",
        ]
    )
    if _POA_REGRESSION_BEGIN in existing and _POA_REGRESSION_END in existing:
        pattern = re.compile(rf"{re.escape(_POA_REGRESSION_BEGIN)}.*?{re.escape(_POA_REGRESSION_END)}\n?", re.DOTALL)
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


def _stage_text(contract: Dict[str, Any], section: str, key: str) -> str:
    payload = contract.get(section, {})
    if not isinstance(payload, dict):
        return ""
    return str(payload.get(key, "")).strip()


def _render_templates(templates: Sequence[str], **context: Any) -> List[str]:
    return [str(template).format(**context) for template in templates if str(template).strip()]


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
