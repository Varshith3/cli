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
_STAGE_EXECUTION_PREP = "stage_e_execution_prep"
_POA_EXECUTION_BEGIN = "<!-- GHDP:BEGIN STAGE_E_EXECUTION -->"
_POA_EXECUTION_END = "<!-- GHDP:END STAGE_E_EXECUTION -->"
_SKILLS_ROOT = Path(".ghdp/skills")
_PLUGINS_ROOT = Path(".ghdp/plugins")
_EXECUTION_AGENT_IDS = (
    "implementation",
    "qa-scenario-design",
    "regression-validation",
    "test-coverage-authoring",
    "developer-test-execution",
    "binary-validation",
    "release-readiness",
    "release-prerelease",
    "pr-external-integration",
    "traceability-historian",
)


@dataclass
class OrchestrateExecutionPrepResult:
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
    work_type: str
    implementation_targets: List[str]
    regression_targets: List[str]
    coverage_targets: List[str]
    skills_bound: List[str]
    plugins_bound: List[str]
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_execution_prep(*, repo_root: Path | None = None) -> OrchestrateExecutionPrepResult:
    resolved_root = resolve_repo_root(repo_root)
    branch_name = current_branch_name(resolved_root)
    if not branch_name:
        raise PlatformError(
            "Could not resolve the current branch for orchestrate execution prep.",
            code="E_ORCHESTRATE_BRANCH_UNRESOLVED",
            reason="current_branch",
        )

    runtime_root = resolved_root / _BRANCHES_ROOT / runtime_branch_folder_name(resolved_root, branch_name)
    branch_state_path = runtime_root / "branch_state.json"
    if not branch_state_path.exists():
        raise PlatformError(
            "No branch runtime state exists yet. Complete the earlier orchestrator stages before execution prep.",
            code="E_ORCHESTRATE_RUN_MISSING",
            reason=str(branch_state_path),
        )

    branch_state = load_orchestrate_json_file(branch_state_path)
    stage_contract = load_stage_contract(stage_id=_STAGE_EXECUTION_PREP, repo_root=resolved_root)
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
    if str(stage_status.get("stage_d_review_layer", {}).get("status", "")).strip() != "completed":
        raise PlatformError(
            "Stage D review must complete before Stage E execution prep begins.",
            code="E_ORCHESTRATE_STAGE_ORDER",
            reason="stage_d_review_layer",
        )

    poa_path = runtime_root / "poa.md"
    poa_text = poa_path.read_text(encoding="utf-8") if poa_path.exists() else ""
    work_type = _extract_field(poa_text, "Work type") or "new_feature"
    impacted_areas = _extract_bullet_block(poa_text, "### Impacted Areas")
    capability_matches = _extract_bullet_block(poa_text, "### Capability Matches")

    implementation_targets = [
        target
        for target in impacted_areas
        if target.startswith("platform-cli/") or target.startswith(".ghdp/")
    ] or ["platform-cli/src/platform_cli/commands/orchestrate.py"]
    regression_targets = [
        "platform-cli/tests/test_orchestrate_contract.py",
        "platform-cli/tests/test_orchestrate_runtime.py",
        "platform-cli/tests/test_orchestrate_front_door.py",
        "platform-cli/tests/test_orchestrate_review.py",
    ]
    coverage_targets = [
        "Add or extend tests for any new Stage E command surface and runtime artifact writer.",
        "Ensure branch runtime artifact mutations remain deterministic in repo-backed runs.",
        "Protect the manifest/tool layering assumptions introduced in earlier stages.",
    ]
    capability_reuse_lines = (
        [f"- `{item}`" for item in capability_matches]
        if capability_matches
        else ["- Reuse the existing orchestrate runtime/contract layers before inventing new abstractions."]
    )

    execution_agents = [load_agent_contract(agent_id=agent_id, repo_root=resolved_root) for agent_id in _EXECUTION_AGENT_IDS]
    skills_bound = _unique_items(
        skill_id
        for agent in execution_agents
        for skill_id in agent.get("allowed_skills", [])
    )
    plugins_bound = _unique_items(
        plugin_id
        for agent in execution_agents
        for plugin_id in agent.get("allowed_plugins", [])
    )

    _assert_skill_payloads(resolved_root, skills_bound)
    _assert_plugin_payloads(resolved_root, plugins_bound)

    _write_markdown(
        run_root / "implementation_plan.md",
        [
            "# Implementation Plan",
            "",
            f"- Work type: `{work_type}`",
            "- Intent: convert the accepted orchestrator plan into concrete code changes without breaking the earlier runtime slices.",
            "",
            "## Primary Targets",
            *[f"- `{item}`" for item in implementation_targets],
            "",
            "## Capability Reuse Notes",
            *capability_reuse_lines,
            "",
            "## Execution Posture",
            "- Keep commands thin and put runtime logic under `tools/`.",
            "- Keep manifests and policy loading under `manifests/`.",
            "- Update repo-local runtime artifacts together with code changes when the stage meaning evolves.",
            "",
        ],
    )
    _write_markdown(
        run_root / "qa_scenario_plan.md",
        [
            "# QA Scenario Plan",
            "",
            "- Validate a happy-path branch run from Stage A through Stage E prep.",
            "- Validate stale or missing branch state errors for out-of-order execution attempts.",
            "- Validate repo-local artifact refresh in `.ghdp/orchestrate/...` after each lifecycle command.",
            "- Validate JSON and text CLI output for the new execution command.",
            "- Validate that review-cleared runs can be paused and resumed without losing Stage E context.",
            "",
        ],
    )
    _write_markdown(
        run_root / "regression_plan.md",
        [
            "# Regression Plan",
            "",
            "## Mandatory Regression Targets",
            *[f"- `{item}`" for item in regression_targets],
            "",
            "## Why These Matter",
            "- They protect earlier Stage A through Stage D contracts while Stage E expands the execution layer.",
            "- They verify that repo-backed orchestration history remains deterministic across repeated runs.",
            "",
        ],
    )
    _write_markdown(
        run_root / "coverage_plan.md",
        [
            "# Coverage Plan",
            "",
            "## Coverage Goals",
            *[f"- {item}" for item in coverage_targets],
            "",
            "## Required New Assertions",
            "- Execution-prep command writes all expected Stage E artifacts.",
            "- Skill/plugin binding files exist for the execution-layer contract.",
            "- Stage status advances into `stage_e_execution_prep` with a clear next action.",
            "",
        ],
    )
    _write_json(
        run_root / "execution_bindings.json",
        {
            "schema_version": "1.0",
            "skills_bound": [
                {
                    "id": skill_id,
                    "path": str((resolved_root / _SKILLS_ROOT / skill_id / "SKILL.md").relative_to(resolved_root)).replace("\\", "/"),
                }
                for skill_id in skills_bound
            ],
            "plugins_bound": [
                {
                    "id": plugin_id,
                    "path": str((resolved_root / _PLUGINS_ROOT / plugin_id / "plugin.json").relative_to(resolved_root)).replace("\\", "/"),
                }
                for plugin_id in plugins_bound
            ],
        },
    )

    _upsert_stage_status(
        stage_status_path,
        stage_name=_STAGE_EXECUTION_PREP,
        status="completed",
        owner_agent="orchestrator",
        summary="Stage E execution prep generated implementation, QA, regression, coverage, and plugin-binding artifacts.",
        artifacts=[
            "implementation_plan.md",
            "qa_scenario_plan.md",
            "regression_plan.md",
            "coverage_plan.md",
            "execution_bindings.json",
        ],
    )
    for stage_name, owner, summary, artifacts in (
        ("stage11_implementation", "implementation", "Implementation should follow the generated execution prep artifacts.", ["implementation_plan.md"]),
        ("stage13_qa_scenario_design", "qa-scenario-design", "QA scenario design should use the generated QA scenario plan as the baseline.", ["qa_scenario_plan.md"]),
        ("stage14_touched_scope_regression", "regression-validation", "Regression validation should execute the generated regression plan.", ["regression_plan.md"]),
        ("stage15_new_test_coverage", "test-coverage-authoring", "Coverage authoring should execute the generated coverage plan.", ["coverage_plan.md"]),
        ("stage16_developer_test_execution", "developer-test-execution", "Developer test execution should consume the Stage E execution artifacts.", ["implementation_plan.md", "regression_plan.md", "coverage_plan.md"]),
    ):
        _upsert_stage_status(
            stage_status_path,
            stage_name=stage_name,
            status="pending",
            owner_agent=owner,
            summary=summary,
            artifacts=list(artifacts),
        )

    _upsert_decisions(
        run_root / "decisions.json",
        [
            {
                "id": "stage_e_execution_prep",
                "decision": "Stage E execution prep artifacts were generated from the accepted Stage D plan.",
                "status": "accepted",
                "source": "stage_e_execution_prep",
            }
        ],
    )

    next_action = _stage_text(stage_contract, "next_actions", "completed")
    branch_state["status"] = "paused"
    branch_state["current_stage"] = _STAGE_EXECUTION_PREP
    branch_state["next_action"] = next_action
    branch_state["anomaly_flag"] = False
    branch_state["last_updated_at"] = _iso_now()
    branch_state["last_updated_by"] = "codex"
    _write_json(branch_state_path, branch_state)

    _update_poa(
        poa_path,
        work_type=work_type,
        implementation_targets=implementation_targets,
        regression_targets=regression_targets,
        coverage_targets=coverage_targets,
        skills_bound=skills_bound,
        plugins_bound=plugins_bound,
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
        current_stage=_STAGE_EXECUTION_PREP,
        next_action=next_action,
        notes=[
            f"Implementation targets: {len(implementation_targets)}.",
            f"Regression targets: {len(regression_targets)}.",
            f"Coverage goals: {len(coverage_targets)}.",
            f"Skills bound: {len(skills_bound)}.",
            f"Plugins bound: {len(plugins_bound)}.",
        ],
    )

    return OrchestrateExecutionPrepResult(
        repo_root=str(resolved_root),
        branch_name=branch_name,
        branch_slug=slugify_branch_name(branch_name),
        ticket_key=str(branch_state.get("ticket_key", "")).strip(),
        active_run_key=active_run_key,
        action="execution_prep",
        status="paused",
        current_stage=_STAGE_EXECUTION_PREP,
        next_action=next_action,
        branch_runtime_root=str(runtime_root),
        work_type=work_type,
        implementation_targets=implementation_targets,
        regression_targets=regression_targets,
        coverage_targets=coverage_targets,
        skills_bound=skills_bound,
        plugins_bound=plugins_bound,
        message=_stage_text(stage_contract, "messages", "completed"),
    )


def _assert_skill_payloads(repo_root: Path, skill_ids: Sequence[str]) -> None:
    missing = [skill_id for skill_id in skill_ids if not (repo_root / _SKILLS_ROOT / skill_id / "SKILL.md").exists()]
    if missing:
        raise PlatformError(
            "Stage E execution prep requires repo-level skill payloads under .ghdp/skills/<id>/SKILL.md.",
            code="E_ORCHESTRATE_SKILL_PAYLOAD_MISSING",
            reason=", ".join(missing),
        )


def _assert_plugin_payloads(repo_root: Path, plugin_ids: Sequence[str]) -> None:
    missing = [plugin_id for plugin_id in plugin_ids if not (repo_root / _PLUGINS_ROOT / plugin_id / "plugin.json").exists()]
    if missing:
        raise PlatformError(
            "Stage E execution prep requires repo-level plugin payloads under .ghdp/plugins/<id>/plugin.json.",
            code="E_ORCHESTRATE_PLUGIN_PAYLOAD_MISSING",
            reason=", ".join(missing),
        )


def _unique_items(items: Sequence[str] | Any) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for item in items:
        normalized = str(item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _extract_field(poa_text: str, field_name: str) -> str:
    pattern = re.compile(rf"- {re.escape(field_name)}: `([^`]+)`")
    match = pattern.search(poa_text)
    return match.group(1).strip() if match else ""


def _extract_bullet_block(poa_text: str, header: str) -> List[str]:
    pattern = re.compile(rf"{re.escape(header)}\n((?:- .+\n)+)")
    match = pattern.search(poa_text)
    if not match:
        return []
    return [line[2:].strip().strip("`") for line in match.group(1).splitlines() if line.startswith("- ")]


def _update_poa(
    path: Path,
    *,
    work_type: str,
    implementation_targets: Sequence[str],
    regression_targets: Sequence[str],
    coverage_targets: Sequence[str],
    skills_bound: Sequence[str],
    plugins_bound: Sequence[str],
) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    managed_block = "\n".join(
        [
            _POA_EXECUTION_BEGIN,
            "## Stage E Execution Prep Outputs",
            "",
            f"- Work type: `{work_type}`",
            f"- Implementation target count: `{len(implementation_targets)}`",
            f"- Regression target count: `{len(regression_targets)}`",
            f"- Coverage goal count: `{len(coverage_targets)}`",
            "",
            "### Bound Skills",
            *[f"- `{item}`" for item in skills_bound],
            "",
            "### Bound Plugins",
            *[f"- `{item}`" for item in plugins_bound],
            "",
            "### Implementation Targets",
            *[f"- `{item}`" for item in implementation_targets],
            "",
            "### Regression Targets",
            *[f"- `{item}`" for item in regression_targets],
            "",
            "### Coverage Goals",
            *[f"- {item}" for item in coverage_targets],
            "",
            _POA_EXECUTION_END,
            "",
        ]
    )
    if _POA_EXECUTION_BEGIN in existing and _POA_EXECUTION_END in existing:
        pattern = re.compile(rf"{re.escape(_POA_EXECUTION_BEGIN)}.*?{re.escape(_POA_EXECUTION_END)}\n?", re.DOTALL)
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
    existing["status"] = status
    existing["owner_agent"] = owner_agent
    existing["summary"] = summary
    existing["artifacts"] = list(artifacts)
    if status in {"completed", "blocked"}:
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


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
