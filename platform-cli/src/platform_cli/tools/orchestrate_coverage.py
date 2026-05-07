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
_STAGE_REGRESSION = "stage14_touched_scope_regression"
_STAGE_COVERAGE = "stage15_new_test_coverage"
_POA_COVERAGE_BEGIN = "<!-- GHDP:BEGIN STAGE15_COVERAGE -->"
_POA_COVERAGE_END = "<!-- GHDP:END STAGE15_COVERAGE -->"


@dataclass
class OrchestrateCoverageResult:
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
    coverage_agent: str
    allowed_skills: List[str]
    allowed_plugins: List[str]
    authored_test_count: int
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_coverage_stage(*, repo_root: Path | None = None) -> OrchestrateCoverageResult:
    resolved_root = resolve_repo_root(repo_root)
    branch_name = current_branch_name(resolved_root)
    if not branch_name:
        raise PlatformError(
            "Could not resolve the current branch for orchestrate coverage authoring.",
            code="E_ORCHESTRATE_BRANCH_UNRESOLVED",
            reason="current_branch",
        )

    runtime_root = resolved_root / _BRANCHES_ROOT / runtime_branch_folder_name(resolved_root, branch_name)
    branch_state_path = runtime_root / "branch_state.json"
    if not branch_state_path.exists():
        raise PlatformError(
            "No branch runtime state exists yet. Complete the earlier orchestrator stages before coverage authoring begins.",
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
    if str(stage_status.get(_STAGE_REGRESSION, {}).get("status", "")).strip() != "completed":
        raise PlatformError(
            "Stage 14 touched-scope regression validation must complete before Stage 15 new test coverage authoring begins.",
            code="E_ORCHESTRATE_STAGE_ORDER",
            reason=_STAGE_REGRESSION,
        )

    coverage_plan_path = run_root / "coverage_plan.md"
    if not coverage_plan_path.exists():
        raise PlatformError(
            "Stage 15 new test coverage authoring requires coverage_plan.md from Stage E execution prep.",
            code="E_ORCHESTRATE_COVERAGE_PLAN_MISSING",
            reason=str(coverage_plan_path),
        )

    qa_plan_path = run_root / "qa_scenario_plan.md"
    if not qa_plan_path.exists():
        raise PlatformError(
            "Stage 15 new test coverage authoring requires qa_scenario_plan.md from Stage 13.",
            code="E_ORCHESTRATE_QA_PLAN_MISSING",
            reason=str(qa_plan_path),
        )

    regression_selection_path = run_root / "regression_selection.md"
    if not regression_selection_path.exists():
        raise PlatformError(
            "Stage 15 new test coverage authoring requires regression_selection.md from Stage 14.",
            code="E_ORCHESTRATE_REGRESSION_SELECTION_MISSING",
            reason=str(regression_selection_path),
        )

    implementation_plan_path = run_root / "implementation_plan.md"
    if not implementation_plan_path.exists():
        raise PlatformError(
            "Stage 15 new test coverage authoring requires implementation_plan.md from Stage E execution prep.",
            code="E_ORCHESTRATE_IMPLEMENTATION_PLAN_MISSING",
            reason=str(implementation_plan_path),
        )

    stage_contract = load_stage_contract(stage_id=_STAGE_COVERAGE, repo_root=resolved_root)
    agent_contract = load_agent_contract(agent_id="test-coverage-authoring", repo_root=resolved_root)
    allowed_skills = _normalize_list(agent_contract.get("allowed_skills", []))
    allowed_plugins = _normalize_list(agent_contract.get("allowed_plugins", []))
    _assert_skill_payloads(resolved_root, allowed_skills)
    _assert_plugin_payloads(resolved_root, allowed_plugins)

    coverage_plan = coverage_plan_path.read_text(encoding="utf-8")
    qa_plan = qa_plan_path.read_text(encoding="utf-8")
    regression_selection = regression_selection_path.read_text(encoding="utf-8")
    implementation_plan = implementation_plan_path.read_text(encoding="utf-8")

    coverage_goals = _extract_bullets(coverage_plan, "## Coverage Goals")
    required_assertions = _extract_bullets(coverage_plan, "## Required New Assertions")
    selected_tests = _extract_bullets(regression_selection, "## Selected Tests")
    implementation_targets = _extract_bullets(implementation_plan, "## Primary Targets")
    authored_tests = _build_authored_test_candidates(
        coverage_goals=coverage_goals,
        required_assertions=required_assertions,
        selected_tests=selected_tests,
        implementation_targets=implementation_targets,
    )
    coverage_rationale = _build_coverage_rationale(
        authored_tests=authored_tests,
        coverage_goals=coverage_goals,
        qa_plan=qa_plan,
    )

    _write_markdown(
        run_root / "coverage_prompt.md",
        [
            "# Stage 15 Coverage Prompt",
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
            "## Coverage Authoring Posture",
            *[f"- {line}" for line in stage_contract.get("coverage_posture", [])],
            "",
        ],
    )
    _write_json(
        run_root / "coverage_bindings.json",
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
            "coverage_goals": list(coverage_goals),
            "required_assertions": list(required_assertions),
            "selected_tests": list(selected_tests),
            "authored_tests": list(authored_tests),
        },
    )
    _write_markdown(
        run_root / "coverage_backlog.md",
        [
            "# Coverage Backlog",
            "",
            "- Status: `authored`",
            "- Owner agent: `test-coverage-authoring`",
            f"- Authored test count: `{len(authored_tests)}`",
            "",
            "## New or Expanded Tests",
            *[f"- `{item}`" for item in authored_tests],
            "",
            "## Coverage Rationale",
            *[f"- {line}" for line in coverage_rationale],
            "",
        ],
    )
    _write_markdown(
        run_root / "coverage_summary.md",
        [
            "# Coverage Summary",
            "",
            "- Status: `completed`",
            "- Owner agent: `test-coverage-authoring`",
            f"- Authored test count: `{len(authored_tests)}`",
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
        stage_name=_STAGE_COVERAGE,
        status="completed",
        owner_agent="test-coverage-authoring",
        summary="Stage 15 new test coverage authoring translated the coverage goals into an explicit repo-backed backlog for Stage 16 execution.",
        artifacts=[
            "coverage_prompt.md",
            "coverage_bindings.json",
            "coverage_backlog.md",
            "coverage_summary.md",
        ],
    )
    _upsert_decisions(
        run_root / "decisions.json",
        [
            {
                "id": "stage15_new_test_coverage",
                "decision": "Stage 15 authored the new or expanded test backlog required to validate the current touched scope.",
                "status": "completed",
                "source": _STAGE_COVERAGE,
            }
        ],
    )

    next_action = _stage_text(stage_contract, "next_actions", "completed")
    branch_state["status"] = "paused"
    branch_state["current_stage"] = _STAGE_COVERAGE
    branch_state["next_action"] = next_action
    branch_state["anomaly_flag"] = False
    branch_state["last_updated_at"] = _iso_now()
    branch_state["last_updated_by"] = "test-coverage-authoring"
    _write_json(branch_state_path, branch_state)

    _update_poa(
        runtime_root / "poa.md",
        coverage_agent=agent_contract["id"],
        allowed_skills=allowed_skills,
        allowed_plugins=allowed_plugins,
        authored_tests=authored_tests,
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
        current_stage=_STAGE_COVERAGE,
        next_action=next_action,
        notes=_render_templates(
            stage_contract.get("resume_note_templates", []),
            coverage_agent=agent_contract["id"],
            allowed_skill_count=len(allowed_skills),
            allowed_plugin_count=len(allowed_plugins),
            authored_test_count=len(authored_tests),
            coverage_goal_count=len(coverage_goals),
        ),
    )

    return OrchestrateCoverageResult(
        repo_root=str(resolved_root),
        branch_name=branch_name,
        branch_slug=slugify_branch_name(branch_name),
        ticket_key=str(branch_state.get("ticket_key", "")).strip(),
        active_run_key=active_run_key,
        action="coverage",
        status="paused",
        current_stage=_STAGE_COVERAGE,
        next_action=next_action,
        branch_runtime_root=str(runtime_root),
        coverage_agent=str(agent_contract["id"]),
        allowed_skills=allowed_skills,
        allowed_plugins=allowed_plugins,
        authored_test_count=len(authored_tests),
        message=_stage_text(stage_contract, "messages", "completed"),
    )


def _build_authored_test_candidates(
    *,
    coverage_goals: Sequence[str],
    required_assertions: Sequence[str],
    selected_tests: Sequence[str],
    implementation_targets: Sequence[str],
) -> List[str]:
    authored: List[str] = []
    if coverage_goals:
        authored.append("platform-cli/tests/test_orchestrate_execution.py")
    if any("stage" in item.lower() for item in required_assertions):
        authored.append("platform-cli/tests/test_orchestrate_coverage.py")
    if selected_tests:
        authored.append("platform-cli/tests/test_orchestrate_regression.py")
    if any("commands/orchestrate.py" in item for item in implementation_targets):
        authored.append("platform-cli/tests/test_orchestrate_contract.py")
    if any(item.startswith(".ghdp/") for item in implementation_targets):
        authored.append("platform-cli/tests/test_orchestrate_manifests.py")
    return _normalize_list(authored)


def _build_coverage_rationale(*, authored_tests: Sequence[str], coverage_goals: Sequence[str], qa_plan: str) -> List[str]:
    rationale: List[str] = []
    for test_path in authored_tests:
        if test_path.endswith("test_orchestrate_execution.py"):
            rationale.append(f"`{test_path}` protects execution-prep bindings that downstream stages still depend on.")
        elif test_path.endswith("test_orchestrate_coverage.py"):
            rationale.append(f"`{test_path}` proves the new coverage-authoring stage stays repo-driven and resumable.")
        elif test_path.endswith("test_orchestrate_regression.py"):
            rationale.append(f"`{test_path}` keeps Stage 14 and Stage 15 aligned so coverage follows the same touched scope the regression set established.")
        elif test_path.endswith("test_orchestrate_contract.py"):
            rationale.append(f"`{test_path}` protects the visible orchestrate command surface because new coverage work still relies on stable command wiring.")
        elif test_path.endswith("test_orchestrate_manifests.py"):
            rationale.append(f"`{test_path}` keeps the `.ghdp` contract validation path healthy because the coverage stage consumes repo-owned recipes and agent contracts.")
        else:
            rationale.append(f"`{test_path}` is included because it sits on the focused changed-surface validation path.")
    if coverage_goals:
        rationale.append(f"The first coverage goal remains the anchor for this backlog: `{coverage_goals[0]}`.")
    if "Failure-path scenario" in qa_plan:
        rationale.append("The QA scenario plan still includes failure-path behavior, so at least one new coverage item must preserve that branch of execution.")
    return rationale


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
            "Stage 15 new test coverage authoring requires repo-level skill payloads under .ghdp/skills/<id>/SKILL.md.",
            code="E_ORCHESTRATE_SKILL_PAYLOAD_MISSING",
            reason=", ".join(missing),
        )


def _assert_plugin_payloads(repo_root: Path, plugin_ids: Sequence[str]) -> None:
    missing = [plugin_id for plugin_id in plugin_ids if not (repo_root / _PLUGINS_ROOT / plugin_id / "plugin.json").exists()]
    if missing:
        raise PlatformError(
            "Stage 15 new test coverage authoring requires repo-level plugin payloads under .ghdp/plugins/<id>/plugin.json.",
            code="E_ORCHESTRATE_PLUGIN_PAYLOAD_MISSING",
            reason=", ".join(missing),
        )


def _update_poa(
    path: Path,
    *,
    coverage_agent: str,
    allowed_skills: Sequence[str],
    allowed_plugins: Sequence[str],
    authored_tests: Sequence[str],
) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    managed_block = "\n".join(
        [
            _POA_COVERAGE_BEGIN,
            "## Stage 15 New Test Coverage",
            "",
            f"- Coverage agent: `{coverage_agent}`",
            f"- Allowed skill count: `{len(allowed_skills)}`",
            f"- Allowed plugin count: `{len(allowed_plugins)}`",
            f"- Authored test count: `{len(authored_tests)}`",
            "",
            "### Allowed Skills",
            *[f"- `{item}`" for item in allowed_skills],
            "",
            "### Allowed Plugins",
            *[f"- `{item}`" for item in allowed_plugins],
            "",
            "### Authored Test Backlog",
            *[f"- `{item}`" for item in authored_tests],
            "",
            _POA_COVERAGE_END,
            "",
        ]
    )
    if _POA_COVERAGE_BEGIN in existing and _POA_COVERAGE_END in existing:
        pattern = re.compile(rf"{re.escape(_POA_COVERAGE_BEGIN)}.*?{re.escape(_POA_COVERAGE_END)}\n?", re.DOTALL)
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
