from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from platform_cli.core.errors import PlatformError
from platform_cli.core.repo_roots import resolve_repo_root
from platform_cli.manifests.orchestrate_load import load_orchestrate_json_file
from platform_cli.manifests.orchestrate_policy_load import load_orchestrate_policy
from platform_cli.manifests.orchestrate_validate import validate_orchestrate_policy
from platform_cli.tools.orchestrate_contract import (
    inspect_orchestrate_contract,
    runtime_branch_folder_name,
    slugify_branch_name,
)
from platform_cli.tools.repo_ready_generation import current_branch_name


_BRANCHES_ROOT = Path(".ghdp/orchestrate/branches")
_INTENT_PATH = Path(".ghdp/frbr/intent.json")
_RUNTIME_BOOTSTRAP_STAGE = "stage_b_runtime_bootstrap"


@dataclass
class OrchestrateLifecycleResult:
    repo_root: str
    branch_name: str
    branch_slug: str
    ticket_key: str
    action: str
    active_run_key: str
    status: str
    current_stage: str
    next_action: str
    branch_runtime_root: str
    policy_source: str
    execution_mode: str
    provider_mode: str
    created_new_run: bool
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def start_orchestrate_run(*, repo_root: Path | None = None) -> OrchestrateLifecycleResult:
    resolved_root = resolve_repo_root(repo_root)
    policy, policy_source = _load_policy()
    branch_name = current_branch_name(resolved_root)
    if not branch_name:
        raise PlatformError(
            "Could not resolve the current branch for orchestrate start.",
            code="E_ORCHESTRATE_BRANCH_UNRESOLVED",
            reason="current_branch",
        )
    if not branch_name.startswith("feature/"):
        raise PlatformError(
            "Orchestrate start currently supports feature branches only.",
            code="E_ORCHESTRATE_BRANCH_INVALID",
            reason=branch_name,
        )

    contract = inspect_orchestrate_contract(repo_root=resolved_root)
    if not contract.repo_contract_ready:
        raise PlatformError(
            "Repo-level orchestrator contract is not ready. Finish Stage A contract setup before starting a run.",
            code="E_ORCHESTRATE_CONTRACT_NOT_READY",
            reason="repo_contract_ready",
        )

    branch_slug = slugify_branch_name(branch_name)
    runtime_root = resolved_root / _BRANCHES_ROOT / runtime_branch_folder_name(resolved_root, branch_name)
    ticket_key = _load_ticket_key(resolved_root)
    provider_mode = _resolve_provider_mode(policy)
    execution_mode = str(policy.get("runtime", {}).get("default_execution_mode", "auto"))

    branch_state_path = runtime_root / "branch_state.json"
    if branch_state_path.exists():
        branch_state = load_orchestrate_json_file(branch_state_path)
        active_run_key = str(branch_state.get("active_run_key", "")).strip()
        if active_run_key:
            branch_state["status"] = "in_progress"
            branch_state["current_stage"] = _RUNTIME_BOOTSTRAP_STAGE
            branch_state["last_updated_at"] = _iso_now(policy)
            branch_state["last_updated_by"] = provider_mode
            branch_state["next_action"] = "Continue Stage B runtime bootstrap and advance the orchestrator lifecycle."
            _write_json(branch_state_path, branch_state)
            _upsert_stage_status(
                runtime_root / "runs" / active_run_key / "stage_status.json",
                stage_name=_RUNTIME_BOOTSTRAP_STAGE,
                summary="Stage B runtime bootstrap is active for this branch run.",
            )
            _write_resume_context(
                runtime_root,
                active_run_key,
                current_stage=_RUNTIME_BOOTSTRAP_STAGE,
                next_action=str(branch_state["next_action"]),
                note="Run resumed through `ghdp orchestrate start` into Stage B runtime bootstrap.",
            )
            return OrchestrateLifecycleResult(
                repo_root=str(resolved_root),
                branch_name=branch_name,
                branch_slug=branch_slug,
                ticket_key=ticket_key,
                action="start",
                active_run_key=active_run_key,
                status=str(branch_state.get("status", "in_progress")),
                current_stage=str(branch_state.get("current_stage", "stage0_trigger")),
                next_action=str(branch_state.get("next_action", "")),
                branch_runtime_root=str(runtime_root),
                policy_source=policy_source,
                execution_mode=execution_mode,
                provider_mode=provider_mode,
                created_new_run=False,
                message="Reused the existing active branch run.",
            )

    active_run_key = _build_run_key(policy, provider_mode)
    _ensure_runtime_files(
        repo_root=resolved_root,
        runtime_root=runtime_root,
        branch_name=branch_name,
        branch_slug=branch_slug,
        ticket_key=ticket_key,
        active_run_key=active_run_key,
        provider_mode=provider_mode,
        execution_mode=execution_mode,
        next_action="Begin Stage B runtime bootstrap and advance the orchestrator beyond contract inspection.",
        current_stage=_RUNTIME_BOOTSTRAP_STAGE,
    )

    return OrchestrateLifecycleResult(
        repo_root=str(resolved_root),
        branch_name=branch_name,
        branch_slug=branch_slug,
        ticket_key=ticket_key,
        action="start",
        active_run_key=active_run_key,
        status="in_progress",
        current_stage=_RUNTIME_BOOTSTRAP_STAGE,
        next_action="Begin Stage B runtime bootstrap and advance the orchestrator beyond contract inspection.",
        branch_runtime_root=str(runtime_root),
        policy_source=policy_source,
        execution_mode=execution_mode,
        provider_mode=provider_mode,
        created_new_run=True,
        message="Created a new branch-scoped orchestrator run.",
    )


def resume_orchestrate_run(*, repo_root: Path | None = None) -> OrchestrateLifecycleResult:
    resolved_root = resolve_repo_root(repo_root)
    policy, policy_source = _load_policy()
    branch_name = current_branch_name(resolved_root)
    branch_slug = slugify_branch_name(branch_name)
    runtime_root = resolved_root / _BRANCHES_ROOT / runtime_branch_folder_name(resolved_root, branch_name)
    branch_state_path = runtime_root / "branch_state.json"
    if not branch_state_path.exists():
        raise PlatformError(
            "No branch runtime state exists yet. Start the orchestrator first.",
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

    branch_state["status"] = "in_progress"
    branch_state["current_stage"] = _RUNTIME_BOOTSTRAP_STAGE
    branch_state["last_updated_at"] = _iso_now(policy)
    branch_state["next_action"] = f"Resume run {active_run_key} from the current stage."
    _write_json(branch_state_path, branch_state)
    _upsert_stage_status(
        runtime_root / "runs" / active_run_key / "stage_status.json",
        stage_name=_RUNTIME_BOOTSTRAP_STAGE,
        summary="Stage B runtime bootstrap resumed for this branch run.",
    )
    _write_resume_context(
        runtime_root,
        active_run_key,
        current_stage=_RUNTIME_BOOTSTRAP_STAGE,
        next_action=str(branch_state["next_action"]),
        note="Run resumed through `ghdp orchestrate resume` into Stage B runtime bootstrap.",
    )

    return OrchestrateLifecycleResult(
        repo_root=str(resolved_root),
        branch_name=branch_name,
        branch_slug=branch_slug,
        ticket_key=str(branch_state.get("ticket_key", "")).strip(),
        action="resume",
        active_run_key=active_run_key,
        status=str(branch_state.get("status", "in_progress")),
        current_stage=str(branch_state.get("current_stage", "stage0_trigger")),
        next_action=str(branch_state.get("next_action", "")),
        branch_runtime_root=str(runtime_root),
        policy_source=policy_source,
        execution_mode=str(policy.get("runtime", {}).get("default_execution_mode", "auto")),
        provider_mode=_resolve_provider_mode(policy),
        created_new_run=False,
        message="Resumed the active orchestrator run.",
    )


def handoff_orchestrate_run(
    *,
    summary: str,
    next_action: str,
    repo_root: Path | None = None,
) -> OrchestrateLifecycleResult:
    resolved_root = resolve_repo_root(repo_root)
    policy, policy_source = _load_policy()
    branch_name = current_branch_name(resolved_root)
    branch_slug = slugify_branch_name(branch_name)
    runtime_root = resolved_root / _BRANCHES_ROOT / runtime_branch_folder_name(resolved_root, branch_name)
    branch_state_path = runtime_root / "branch_state.json"
    if not branch_state_path.exists():
        raise PlatformError(
            "No branch runtime state exists yet. Start the orchestrator first.",
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

    branch_state["status"] = "paused"
    branch_state["last_updated_at"] = _iso_now(policy)
    branch_state["next_action"] = next_action.strip()
    _write_json(branch_state_path, branch_state)
    _write_handoff(runtime_root, summary=summary.strip(), next_action=next_action.strip(), at=_iso_now(policy))
    _write_resume_context(
        runtime_root,
        active_run_key,
        current_stage=str(branch_state.get("current_stage", _RUNTIME_BOOTSTRAP_STAGE)),
        next_action=next_action.strip(),
        note=f"Handoff recorded. Next action: {next_action.strip()}",
    )

    return OrchestrateLifecycleResult(
        repo_root=str(resolved_root),
        branch_name=branch_name,
        branch_slug=branch_slug,
        ticket_key=str(branch_state.get("ticket_key", "")).strip(),
        action="handoff",
        active_run_key=active_run_key,
        status="paused",
        current_stage=str(branch_state.get("current_stage", "stage0_trigger")),
        next_action=next_action.strip(),
        branch_runtime_root=str(runtime_root),
        policy_source=policy_source,
        execution_mode=str(policy.get("runtime", {}).get("default_execution_mode", "auto")),
        provider_mode=_resolve_provider_mode(policy),
        created_new_run=False,
        message="Recorded a branch handoff and paused the active run.",
    )


def _ensure_runtime_files(
    *,
    repo_root: Path,
    runtime_root: Path,
    branch_name: str,
    branch_slug: str,
    ticket_key: str,
    active_run_key: str,
    provider_mode: str,
    execution_mode: str,
    next_action: str,
    current_stage: str,
) -> None:
    runtime_root.mkdir(parents=True, exist_ok=True)
    run_root = runtime_root / "runs" / active_run_key
    run_root.mkdir(parents=True, exist_ok=True)

    poa_path = runtime_root / "poa.md"
    if not poa_path.exists():
        poa_path.write_text(
            "\n".join(
                [
                    f"# {ticket_key or 'Feature'} POA",
                    "",
                    f"- Branch: `{branch_name}`",
                    f"- Intent: `{_INTENT_PATH.as_posix()}`",
                    "",
            "This POA was bootstrapped by `ghdp orchestrate start` and should be refined by the planner stage.",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    handoff_path = runtime_root / "handoff.md"
    if not handoff_path.exists():
        handoff_path.write_text(
            "\n".join(
                [
                    "# Handoff",
                    "",
                    f"- Branch: `{branch_name}`",
                    f"- Ticket: `{ticket_key or '(missing)'}`",
        "- Status: orchestrator bootstrapped",
                    "",
                    "## Next Steps",
                    f"- {next_action}",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    branch_state = {
        "branch_name": branch_name,
        "branch_slug": branch_slug,
        "ticket_key": ticket_key,
        "intent_ref": _INTENT_PATH.as_posix(),
        "active_run_key": active_run_key,
        "status": "in_progress",
        "current_stage": current_stage,
        "provider_mode": provider_mode,
        "next_action": next_action,
        "anomaly_flag": False,
        "last_updated_at": _iso_now_from_local(),
        "last_updated_by": provider_mode,
    }
    _write_json(runtime_root / "branch_state.json", branch_state)

    run_state = {
        "run_key": active_run_key,
        "started_at": _iso_now_from_local(),
        "ended_at": "",
        "status": "in_progress",
        "trigger_mode": "manual_user_trigger",
        "provider_selection": provider_mode,
        "execution_mode": execution_mode,
        "initiated_by": "user",
        "resume_of_run_key": "",
        "machine_hint": "local_windows",
        "repo_head_sha": "",
    }
    _write_json(run_root / "run_state.json", run_state)

    stage_status = {
        current_stage: {
            "status": "in_progress",
            "started_at": _iso_now_from_local(),
            "ended_at": "",
            "owner_agent": "orchestrator",
        "summary": "Branch run bootstrapped by the orchestrator runtime.",
            "artifacts": ["branch_state.json", "run_state.json", "resume_context.md"],
            "retry_count": 0,
        }
    }
    _write_json(run_root / "stage_status.json", stage_status)
    _write_json(
        run_root / "decisions.json",
        {
            "schema_version": "1.0",
            "decisions": [
                {
                    "id": "runtime_bootstrap",
                    "decision": "Created the initial active run for this branch.",
                    "status": "completed",
                    "source": "orchestrate_start",
                }
            ],
        },
    )
    (run_root / "resume_context.md").write_text(
        "\n".join(
            [
                "# Resume Context",
                "",
                f"Active run: `{active_run_key}`",
                f"Current focus: `{current_stage}`",
                f"Next action: {next_action}",
                "",
                "## Activity Log",
                "- Bootstrapped the initial branch-scoped orchestrator run.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _upsert_stage_status(path: Path, *, stage_name: str, summary: str) -> None:
    payload: Dict[str, Any]
    if path.exists():
        payload = load_orchestrate_json_file(path)
    else:
        payload = {}
    stage_payload = payload.get(stage_name)
    if not isinstance(stage_payload, dict):
        stage_payload = {
            "status": "in_progress",
            "started_at": _iso_now_from_local(),
            "ended_at": "",
            "owner_agent": "orchestrator",
            "artifacts": ["branch_state.json", "run_state.json", "resume_context.md"],
            "retry_count": 0,
        }
    stage_payload["status"] = "in_progress"
    stage_payload["summary"] = summary
    payload[stage_name] = stage_payload
    _write_json(path, payload)


def _write_handoff(runtime_root: Path, *, summary: str, next_action: str, at: str) -> None:
    handoff_path = runtime_root / "handoff.md"
    handoff_path.write_text(
        "\n".join(
            [
                "# Handoff",
                "",
                f"- Updated at: `{at}`",
                "- Status: paused",
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


def _write_resume_context(runtime_root: Path, active_run_key: str, *, current_stage: str, next_action: str, note: str) -> None:
    resume_path = runtime_root / "runs" / active_run_key / "resume_context.md"
    activity_lines: list[str] = []
    if resume_path.exists():
        existing_lines = resume_path.read_text(encoding="utf-8").splitlines()
        capture = False
        for line in existing_lines:
            if line.strip() == "## Activity Log":
                capture = True
                continue
            if capture and line.startswith("- "):
                activity_lines.append(line)
    activity_lines.append(f"- {note}")
    resume_path.write_text(
        "\n".join(
            [
                "# Resume Context",
                "",
                f"Active run: `{active_run_key}`",
                f"Current focus: `{current_stage}`",
                f"Next action: {next_action}",
                "",
                "## Activity Log",
                *activity_lines,
                "",
            ]
        ),
        encoding="utf-8",
    )


def _load_policy() -> Tuple[Dict[str, Any], str]:
    policy, source = load_orchestrate_policy()
    validate_orchestrate_policy(policy)
    return policy, source


def _resolve_provider_mode(policy: Dict[str, Any]) -> str:
    return str(policy.get("runtime", {}).get("default_provider_mode", "auto")).strip() or "auto"


def _load_ticket_key(repo_root: Path) -> str:
    intent_path = repo_root / _INTENT_PATH
    if not intent_path.exists():
        return ""
    try:
        payload = load_orchestrate_json_file(intent_path)
    except PlatformError:
        return ""
    return str(payload.get("ticket_key", "")).strip()


def _build_run_key(policy: Dict[str, Any], provider_mode: str) -> str:
    zone = _resolve_zone(policy)
    dt = datetime.now(zone)
    tz_slug = str(policy.get("runtime", {}).get("run_key_tz_slug", "ist")).strip() or "ist"
    return f"{dt.strftime('%Y%m%d-%H%M%S')}__{provider_mode}__{tz_slug}__run"


def _iso_now(policy: Dict[str, Any]) -> str:
    return datetime.now(_resolve_zone(policy)).isoformat(timespec="seconds")


def _iso_now_from_local() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _resolve_zone(policy: Dict[str, Any]) -> ZoneInfo:
    runtime = policy.get("runtime", {}) if isinstance(policy.get("runtime", {}), dict) else {}
    configured = str(runtime.get("run_key_timezone", "Asia/Calcutta")).strip() or "Asia/Calcutta"
    candidates = [configured]
    if configured == "Asia/Calcutta":
        candidates.append("Asia/Kolkata")
    for candidate in candidates:
        try:
            return ZoneInfo(candidate)
        except ZoneInfoNotFoundError:
            continue
    return datetime.now().astimezone().tzinfo or ZoneInfo("UTC")


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
