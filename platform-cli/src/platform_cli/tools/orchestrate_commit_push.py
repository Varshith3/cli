from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence

from platform_cli.core.errors import PlatformError
from platform_cli.core.repo_roots import resolve_repo_root
from platform_cli.exec.runner import run_cmd
from platform_cli.manifests.orchestrate_load import load_orchestrate_json_file
from platform_cli.manifests.orchestrate_stage_load import load_stage_contract
from platform_cli.tools.orchestrate_contract import load_agent_contract, runtime_branch_folder_name, slugify_branch_name
from platform_cli.tools.repo_ready_generation import current_branch_name


_BRANCHES_ROOT = Path(".ghdp/orchestrate/branches")
_STAGE_IMPLEMENTATION = "stage11_implementation"
_STAGE_COMMIT_PUSH = "stage12_commit_push"
_POA_COMMIT_BEGIN = "<!-- GHDP:BEGIN STAGE12_COMMIT_PUSH -->"
_POA_COMMIT_END = "<!-- GHDP:END STAGE12_COMMIT_PUSH -->"


@dataclass
class OrchestrateCommitPushResult:
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
    commit_message: str
    files_committed: List[str]
    remote_name: str
    remote_branch: str
    head_sha: str
    pushed: bool
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_commit_push_stage(*, repo_root: Path | None = None) -> OrchestrateCommitPushResult:
    resolved_root = resolve_repo_root(repo_root)
    branch_name = current_branch_name(resolved_root)
    if not branch_name:
        raise PlatformError(
            "Could not resolve the current branch for orchestrate commit/push.",
            code="E_ORCHESTRATE_BRANCH_UNRESOLVED",
            reason="current_branch",
        )

    runtime_root = resolved_root / _BRANCHES_ROOT / runtime_branch_folder_name(resolved_root, branch_name)
    branch_state_path = runtime_root / "branch_state.json"
    if not branch_state_path.exists():
        raise PlatformError(
            "No branch runtime state exists yet. Complete the earlier orchestrator stages before commit/push begins.",
            code="E_ORCHESTRATE_RUN_MISSING",
            reason=str(branch_state_path),
        )

    branch_state = load_orchestrate_json_file(branch_state_path)
    stage_contract = load_stage_contract(stage_id=_STAGE_COMMIT_PUSH, repo_root=resolved_root)
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
    if str(stage_status.get(_STAGE_IMPLEMENTATION, {}).get("status", "")).strip() not in {"in_progress", "completed"}:
        raise PlatformError(
            "Stage 11 implementation must be active before Stage 12 commit/push begins.",
            code="E_ORCHESTRATE_STAGE_ORDER",
            reason=_STAGE_IMPLEMENTATION,
        )

    implementation_summary_path = run_root / "implementation_summary.md"
    if not implementation_summary_path.exists():
        raise PlatformError(
            "Stage 12 commit/push requires implementation_summary.md from Stage 11.",
            code="E_ORCHESTRATE_IMPLEMENTATION_SUMMARY_MISSING",
            reason=str(implementation_summary_path),
        )

    agent_contract = load_agent_contract(agent_id="implementation", repo_root=resolved_root)
    _stage_all_changes(resolved_root)
    files_committed = _cached_files(resolved_root)
    if not files_committed:
        raise PlatformError(
            "There are no staged repository changes to commit for Stage 12.",
            code="E_ORCHESTRATE_NOTHING_TO_COMMIT",
            reason="empty_index",
        )

    remote_name = _resolve_remote_name(resolved_root)
    ticket_key = str(branch_state.get("ticket_key", "")).strip()
    commit_message = _build_commit_message(ticket_key=ticket_key, branch_name=branch_name, files_committed=files_committed)
    commit_body = _build_commit_body(
        active_run_key=active_run_key,
        branch_name=branch_name,
        files_committed=files_committed,
        prompt_contract=agent_contract.get("prompt_contract", []),
    )

    _write_markdown(
        run_root / "commit_summary.md",
        [
            "# Commit Summary",
            "",
            f"- Commit message: `{commit_message}`",
            "- Head SHA: `resolved_after_commit`",
            f"- Remote: `{remote_name}`",
            f"- Branch: `{branch_name}`",
            "",
            "## Files Committed",
            *[f"- `{item}`" for item in files_committed],
            "",
        ],
    )
    _write_json(
        run_root / "commit_payload.json",
        {
            "schema_version": "1.0",
            "commit_message": commit_message,
            "commit_body": commit_body,
            "files_committed": list(files_committed),
            "head_sha": "",
            "remote_name": remote_name,
            "remote_branch": branch_name,
        },
    )

    _upsert_stage_status(
        stage_status_path,
        stage_name=_STAGE_IMPLEMENTATION,
        status="completed",
        owner_agent="implementation",
        summary="Stage 11 implementation artifacts were committed and handed off into Stage 12 commit/push.",
        artifacts=[
            "implementation_plan.md",
            "implementation_prompt.md",
            "implementation_bindings.json",
            "implementation_summary.md",
        ],
    )
    _upsert_stage_status(
        stage_status_path,
        stage_name=_STAGE_COMMIT_PUSH,
        status="completed",
        owner_agent="implementation",
        summary="Stage 12 commit/push committed the active implementation changes and pushed the branch.",
        artifacts=[
            "commit_summary.md",
            "commit_payload.json",
        ],
    )
    _upsert_decisions(
        run_root / "decisions.json",
        [
            {
                "id": "stage12_commit_push",
                "decision": "Stage 12 commit/push committed and pushed the current implementation worktree.",
                "status": "completed",
                "source": "stage12_commit_push",
            }
        ],
    )

    next_action = _stage_text(stage_contract, "next_actions", "completed")
    branch_state["status"] = "paused"
    branch_state["current_stage"] = _STAGE_COMMIT_PUSH
    branch_state["next_action"] = next_action
    branch_state["anomaly_flag"] = False
    branch_state["last_updated_at"] = _iso_now()
    branch_state["last_updated_by"] = "implementation"
    _write_json(branch_state_path, branch_state)

    _update_poa(runtime_root / "poa.md", commit_message=commit_message, files_committed=files_committed)
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
        current_stage=_STAGE_COMMIT_PUSH,
        next_action=next_action,
        notes=_render_templates(
            stage_contract.get("resume_note_templates", []),
            commit_message=commit_message,
            files_committed_count=len(files_committed),
            head_sha="resolved after commit",
            remote_name=remote_name,
            branch_name=branch_name,
        ),
    )

    _stage_all_changes(resolved_root)
    _git_commit(resolved_root, commit_message, commit_body)
    head_sha = _current_head_sha(resolved_root)
    _git_push(resolved_root, remote_name=remote_name, branch_name=branch_name)

    return OrchestrateCommitPushResult(
        repo_root=str(resolved_root),
        branch_name=branch_name,
        branch_slug=slugify_branch_name(branch_name),
        ticket_key=ticket_key,
        active_run_key=active_run_key,
        action="commit_push",
        status="paused",
        current_stage=_STAGE_COMMIT_PUSH,
        next_action=next_action,
        branch_runtime_root=str(runtime_root),
        commit_message=commit_message,
        files_committed=files_committed,
        remote_name=remote_name,
        remote_branch=branch_name,
        head_sha=head_sha,
        pushed=True,
        message=_stage_text(stage_contract, "messages", "completed"),
    )


def _stage_all_changes(repo_root: Path) -> None:
    run_cmd(["git", "add", "-A"], cwd=repo_root, check=True)


def _cached_files(repo_root: Path) -> List[str]:
    result = run_cmd(["git", "diff", "--cached", "--name-only"], cwd=repo_root, check=True)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _resolve_remote_name(repo_root: Path) -> str:
    result = run_cmd(["git", "remote"], cwd=repo_root, check=True)
    remotes = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if "origin" in remotes:
        return "origin"
    if remotes:
        return remotes[0]
    raise PlatformError(
        "Stage 12 commit/push requires a configured git remote.",
        code="E_ORCHESTRATE_REMOTE_MISSING",
        reason="remote",
    )


def _build_commit_message(*, ticket_key: str, branch_name: str, files_committed: Sequence[str]) -> str:
    label = ticket_key or _derive_branch_label(branch_name) or "orchestrate"
    return f"[{label}] Orchestrator implementation checkpoint ({len(files_committed)} files)"


def _build_commit_body(
    *,
    active_run_key: str,
    branch_name: str,
    files_committed: Sequence[str],
    prompt_contract: Sequence[str],
) -> str:
    lines = [
        f"Run: {active_run_key}",
        f"Branch: {branch_name}",
        "",
        "Prompt contract:",
        *[f"- {item}" for item in prompt_contract],
        "",
        "Files committed:",
        *[f"- {item}" for item in files_committed],
    ]
    return "\n".join(lines)


def _git_commit(repo_root: Path, title: str, body: str) -> None:
    run_cmd(
        [
            "git",
            "-c",
            "user.name=GHDP Orchestrator",
            "-c",
            "user.email=ghdp-orchestrator@local",
            "commit",
            "-m",
            title,
            "-m",
            body,
        ],
        cwd=repo_root,
        check=True,
    )


def _git_push(repo_root: Path, *, remote_name: str, branch_name: str) -> None:
    run_cmd(["git", "push", remote_name, branch_name], cwd=repo_root, check=True)


def _current_head_sha(repo_root: Path) -> str:
    result = run_cmd(["git", "rev-parse", "HEAD"], cwd=repo_root, check=True)
    return result.stdout.strip()


def _derive_branch_label(branch_name: str) -> str:
    match = re.search(r"([A-Z][A-Z0-9]+-\d+)", branch_name or "")
    return match.group(1) if match else ""


def _update_poa(path: Path, *, commit_message: str, files_committed: Sequence[str]) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    managed_block = "\n".join(
        [
            _POA_COMMIT_BEGIN,
            "## Stage 12 Commit Push",
            "",
            f"- Commit message: `{commit_message}`",
            "- Head SHA: `resolved_after_commit`",
            f"- Files committed: `{len(files_committed)}`",
            "",
            "### Files Committed",
            *[f"- `{item}`" for item in files_committed],
            "",
            _POA_COMMIT_END,
            "",
        ]
    )
    if _POA_COMMIT_BEGIN in existing and _POA_COMMIT_END in existing:
        pattern = re.compile(rf"{re.escape(_POA_COMMIT_BEGIN)}.*?{re.escape(_POA_COMMIT_END)}\n?", re.DOTALL)
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
