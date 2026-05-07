from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence

from platform_cli.core.errors import PlatformError
from platform_cli.core.repo_roots import resolve_repo_root
from platform_cli.exec.runner import run_cmd
from platform_cli.manifests.orchestrate_load import load_orchestrate_json_file
from platform_cli.manifests.orchestrate_stage_load import load_stage_contract
from platform_cli.state.store import FileLock, default_state_paths
from platform_cli.tools.orchestrate_contract import load_agent_contract, runtime_branch_folder_name, slugify_branch_name
from platform_cli.tools.repo_ready_generation import current_branch_name


_BRANCHES_ROOT = Path(".ghdp/orchestrate/branches")
_SKILLS_ROOT = Path(".ghdp/skills")
_PLUGINS_ROOT = Path(".ghdp/plugins")
_STAGE_COVERAGE = "stage15_new_test_coverage"
_STAGE_TEST_EXECUTION = "stage16_developer_test_execution"
_POA_TEST_EXECUTION_BEGIN = "<!-- GHDP:BEGIN STAGE16_TEST_EXECUTION -->"
_POA_TEST_EXECUTION_END = "<!-- GHDP:END STAGE16_TEST_EXECUTION -->"


@dataclass
class OrchestrateTestExecutionResult:
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
    execution_agent: str
    allowed_skills: List[str]
    allowed_plugins: List[str]
    execution_mode: str
    executed_tests: List[str]
    failed_tests: List[str]
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_developer_test_execution_stage(*, repo_root: Path | None = None) -> OrchestrateTestExecutionResult:
    resolved_root = resolve_repo_root(repo_root)
    branch_name = current_branch_name(resolved_root)
    if not branch_name:
        raise PlatformError(
            "Could not resolve the current branch for orchestrate developer test execution.",
            code="E_ORCHESTRATE_BRANCH_UNRESOLVED",
            reason="current_branch",
        )

    runtime_root = resolved_root / _BRANCHES_ROOT / runtime_branch_folder_name(resolved_root, branch_name)
    branch_state_path = runtime_root / "branch_state.json"
    if not branch_state_path.exists():
        raise PlatformError(
            "No branch runtime state exists yet. Complete the earlier orchestrator stages before developer test execution begins.",
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
    if str(stage_status.get(_STAGE_COVERAGE, {}).get("status", "")).strip() != "completed":
        raise PlatformError(
            "Stage 15 new test coverage authoring must complete before Stage 16 developer test execution begins.",
            code="E_ORCHESTRATE_STAGE_ORDER",
            reason=_STAGE_COVERAGE,
        )

    qa_plan_path = run_root / "qa_scenario_plan.md"
    regression_selection_path = run_root / "regression_selection.md"
    coverage_backlog_path = run_root / "coverage_backlog.md"
    for required_path, error_code, message in (
        (qa_plan_path, "E_ORCHESTRATE_QA_PLAN_MISSING", "Stage 16 developer test execution requires qa_scenario_plan.md from Stage 13."),
        (regression_selection_path, "E_ORCHESTRATE_REGRESSION_SELECTION_MISSING", "Stage 16 developer test execution requires regression_selection.md from Stage 14."),
        (coverage_backlog_path, "E_ORCHESTRATE_COVERAGE_BACKLOG_MISSING", "Stage 16 developer test execution requires coverage_backlog.md from Stage 15."),
    ):
        if not required_path.exists():
            raise PlatformError(message, code=error_code, reason=str(required_path))

    stage_contract = load_stage_contract(stage_id=_STAGE_TEST_EXECUTION, repo_root=resolved_root)
    agent_contract = load_agent_contract(agent_id="developer-test-execution", repo_root=resolved_root)
    allowed_skills = _normalize_list(agent_contract.get("allowed_skills", []))
    allowed_plugins = _normalize_list(agent_contract.get("allowed_plugins", []))
    _assert_skill_payloads(resolved_root, allowed_skills)
    _assert_plugin_payloads(resolved_root, allowed_plugins)

    regression_selection = regression_selection_path.read_text(encoding="utf-8")
    coverage_backlog = coverage_backlog_path.read_text(encoding="utf-8")
    qa_plan = qa_plan_path.read_text(encoding="utf-8")
    selected_tests = _extract_bullets(regression_selection, "## Selected Tests")
    authored_tests = _extract_bullets(coverage_backlog, "## New or Expanded Tests")
    execution_targets = _normalize_list([*selected_tests, *authored_tests])
    if not execution_targets:
        raise PlatformError(
            "Stage 16 developer test execution could not resolve any tests to run from the regression and coverage artifacts.",
            code="E_ORCHESTRATE_TEST_SELECTION_EMPTY",
            reason=_STAGE_TEST_EXECUTION,
        )

    execution_mode = str(stage_contract.get("execution_mode", "sequential")).strip() or "sequential"
    pytest_cmd = [sys.executable, "-m", "pytest", *execution_targets, "-q"]

    _write_markdown(
        run_root / "test_execution_prompt.md",
        [
            "# Stage 16 Developer Test Execution Prompt",
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
            "## Execution Posture",
            *[f"- {line}" for line in stage_contract.get("execution_posture", [])],
            "",
            f"- Execution mode: `{execution_mode}`",
            "",
        ],
    )
    _write_json(
        run_root / "test_execution_bindings.json",
        {
            "schema_version": "1.0",
            "agent_id": agent_contract["id"],
            "execution_mode": execution_mode,
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
            "authored_tests": list(authored_tests),
            "execution_targets": list(execution_targets),
            "pytest_cmd": list(pytest_cmd),
        },
    )

    command_result = _run_pytest_with_lock(repo_root=resolved_root, pytest_cmd=pytest_cmd)
    failed_tests = [] if command_result.returncode == 0 else list(execution_targets)
    status_key = "completed" if command_result.returncode == 0 else "failed"
    next_action = _stage_text(stage_contract, "next_actions", status_key)
    branch_status = "paused" if command_result.returncode == 0 else "blocked"

    _write_markdown(
        run_root / "test_execution_log.md",
        [
            "# Test Execution Log",
            "",
            f"- Status: `{'passed' if command_result.returncode == 0 else 'failed'}`",
            f"- Execution mode: `{execution_mode}`",
            f"- Command: `{' '.join(pytest_cmd)}`",
            "",
            "## Executed Tests",
            *[f"- `{item}`" for item in execution_targets],
            "",
            "## Pytest Stdout",
            "```text",
            command_result.stdout or "(no stdout)",
            "```",
            "",
            "## Pytest Stderr",
            "```text",
            command_result.stderr or "(no stderr)",
            "```",
            "",
        ],
    )
    _write_markdown(
        run_root / "test_execution_summary.md",
        [
            "# Test Execution Summary",
            "",
            f"- Status: `{'completed' if command_result.returncode == 0 else 'failed'}`",
            "- Owner agent: `developer-test-execution`",
            f"- Executed test count: `{len(execution_targets)}`",
            f"- Failed test count: `{len(failed_tests)}`",
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
        stage_name=_STAGE_TEST_EXECUTION,
        status="completed" if command_result.returncode == 0 else "blocked",
        owner_agent="developer-test-execution",
        summary=(
            "Stage 16 developer test execution ran the selected regression and authored coverage tests successfully."
            if command_result.returncode == 0
            else "Stage 16 developer test execution detected failing validation that must be resolved before later release stages."
        ),
        artifacts=[
            "test_execution_prompt.md",
            "test_execution_bindings.json",
            "test_execution_log.md",
            "test_execution_summary.md",
        ],
    )
    _upsert_decisions(
        run_root / "decisions.json",
        [
            {
                "id": "stage16_developer_test_execution",
                "decision": (
                    "Stage 16 completed the planned validation run."
                    if command_result.returncode == 0
                    else "Stage 16 surfaced failing validation and blocked downstream release stages."
                ),
                "status": "completed" if command_result.returncode == 0 else "blocked",
                "source": _STAGE_TEST_EXECUTION,
            }
        ],
    )

    branch_state["status"] = branch_status
    branch_state["current_stage"] = _STAGE_TEST_EXECUTION
    branch_state["next_action"] = next_action
    branch_state["anomaly_flag"] = command_result.returncode != 0
    branch_state["last_updated_at"] = _iso_now()
    branch_state["last_updated_by"] = "developer-test-execution"
    _write_json(branch_state_path, branch_state)

    _update_poa(
        runtime_root / "poa.md",
        execution_agent=agent_contract["id"],
        allowed_skills=allowed_skills,
        allowed_plugins=allowed_plugins,
        execution_mode=execution_mode,
        executed_tests=execution_targets,
    )
    _write_handoff(
        runtime_root / "handoff.md",
        summary=_stage_text(stage_contract, "handoff_summaries", status_key),
        next_action=next_action,
        status=branch_status,
        at=_iso_now(),
    )
    _write_resume_context(
        run_root / "resume_context.md",
        active_run_key=active_run_key,
        current_stage=_STAGE_TEST_EXECUTION,
        next_action=next_action,
        notes=_render_templates(
            stage_contract.get("resume_note_templates", []),
            execution_agent=agent_contract["id"],
            allowed_skill_count=len(allowed_skills),
            allowed_plugin_count=len(allowed_plugins),
            executed_test_count=len(execution_targets),
            failed_test_count=len(failed_tests),
            execution_mode=execution_mode,
        ),
    )

    return OrchestrateTestExecutionResult(
        repo_root=str(resolved_root),
        branch_name=branch_name,
        branch_slug=slugify_branch_name(branch_name),
        ticket_key=str(branch_state.get("ticket_key", "")).strip(),
        active_run_key=active_run_key,
        action="test_execution",
        status=branch_status,
        current_stage=_STAGE_TEST_EXECUTION,
        next_action=next_action,
        branch_runtime_root=str(runtime_root),
        execution_agent=str(agent_contract["id"]),
        allowed_skills=allowed_skills,
        allowed_plugins=allowed_plugins,
        execution_mode=execution_mode,
        executed_tests=execution_targets,
        failed_tests=failed_tests,
        message=_stage_text(stage_contract, "messages", status_key),
    )


def _run_pytest_with_lock(*, repo_root: Path, pytest_cmd: Sequence[str]):
    lock_path = default_state_paths().state_dir / "orchestrate_test_execution.lock"
    with FileLock(lock_path, timeout_s=30.0, poll_s=0.25):
        return run_cmd(list(pytest_cmd), cwd=repo_root, check=False, capture=True, timeout_s=600)


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
            "Stage 16 developer test execution requires repo-level skill payloads under .ghdp/skills/<id>/SKILL.md.",
            code="E_ORCHESTRATE_SKILL_PAYLOAD_MISSING",
            reason=", ".join(missing),
        )


def _assert_plugin_payloads(repo_root: Path, plugin_ids: Sequence[str]) -> None:
    missing = [plugin_id for plugin_id in plugin_ids if not (repo_root / _PLUGINS_ROOT / plugin_id / "plugin.json").exists()]
    if missing:
        raise PlatformError(
            "Stage 16 developer test execution requires repo-level plugin payloads under .ghdp/plugins/<id>/plugin.json.",
            code="E_ORCHESTRATE_PLUGIN_PAYLOAD_MISSING",
            reason=", ".join(missing),
        )


def _update_poa(
    path: Path,
    *,
    execution_agent: str,
    allowed_skills: Sequence[str],
    allowed_plugins: Sequence[str],
    execution_mode: str,
    executed_tests: Sequence[str],
) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    managed_block = "\n".join(
        [
            _POA_TEST_EXECUTION_BEGIN,
            "## Stage 16 Developer Test Execution",
            "",
            f"- Execution agent: `{execution_agent}`",
            f"- Allowed skill count: `{len(allowed_skills)}`",
            f"- Allowed plugin count: `{len(allowed_plugins)}`",
            f"- Execution mode: `{execution_mode}`",
            f"- Executed test count: `{len(executed_tests)}`",
            "",
            "### Allowed Skills",
            *[f"- `{item}`" for item in allowed_skills],
            "",
            "### Allowed Plugins",
            *[f"- `{item}`" for item in allowed_plugins],
            "",
            "### Executed Tests",
            *[f"- `{item}`" for item in executed_tests],
            "",
            _POA_TEST_EXECUTION_END,
            "",
        ]
    )
    if _POA_TEST_EXECUTION_BEGIN in existing and _POA_TEST_EXECUTION_END in existing:
        pattern = re.compile(rf"{re.escape(_POA_TEST_EXECUTION_BEGIN)}.*?{re.escape(_POA_TEST_EXECUTION_END)}\n?", re.DOTALL)
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
