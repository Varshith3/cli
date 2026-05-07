from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List

from platform_cli.core.errors import PlatformError
from platform_cli.exec.runner import run_cmd
from platform_cli.manifests.orchestrate_stage_load import load_stage_contract
from platform_cli.orchestrate_kernel.runtime_support import (
    assert_stage_completed,
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


_STAGE_RELEASE_READY = "stage18_release_readiness"
_STAGE_PRERELEASE = "stage19_prerelease_creation"
_STAGE_RELEASE_NOTES = "stage20_release_notes_refresh"
_NOTES_PATH = Path(".github/release-notes/notes.md")
_POA_BEGIN = "<!-- GHDP:BEGIN STAGE20_RELEASE_NOTES -->"
_POA_END = "<!-- GHDP:END STAGE20_RELEASE_NOTES -->"


@dataclass
class ReleaseNotesStageResult:
    repo_root: str
    branch_name: str
    active_run_key: str
    status: str
    current_stage: str
    next_action: str
    release_agent: str
    notes_path: str
    freshness_commit: str
    blocked_reason: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_release_notes_refresh_stage(*, repo_root: Path | None = None) -> ReleaseNotesStageResult:
    context = resolve_active_run_context(repo_root=repo_root)
    assert_stage_completed(context.stage_status, _STAGE_RELEASE_READY)

    stage_contract = load_stage_contract(stage_id=_STAGE_RELEASE_NOTES, repo_root=context.repo_root)
    agent_contract = load_agent_contract(agent_id="release-prerelease", repo_root=context.repo_root)
    allowed_skills = _normalize_list(agent_contract.get("allowed_skills", []))
    allowed_plugins = _normalize_list(agent_contract.get("allowed_plugins", []))

    notes_path = context.repo_root / _NOTES_PATH
    summary_payload = _collect_release_note_context(context.run_root)
    notes_body = _render_notes(summary_payload)
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(notes_body, encoding="utf-8")

    freshness_commit = ""
    blocked_reason = ""
    status_key = "completed"
    branch_status = "paused"
    try:
        freshness_commit = _commit_and_push_notes(context.repo_root, notes_path, context.ticket_key or "orchestrate")
    except PlatformError as exc:
        blocked_reason = f"{exc.code}:{exc.reason}"
        status_key = "blocked"
        branch_status = "blocked"

    write_json(
        context.run_root / "release_notes_context.json",
        {
            "schema_version": "1.0",
            "notes_path": _NOTES_PATH.as_posix(),
            "release_agent": agent_contract["id"],
            "allowed_skills": allowed_skills,
            "allowed_plugins": allowed_plugins,
            "sections": summary_payload,
            "freshness_commit": freshness_commit,
            "blocked_reason": blocked_reason,
        },
    )
    write_json(
        context.run_root / "release_notes_commit.json",
        {
            "schema_version": "1.0",
            "commit": freshness_commit,
            "notes_path": _NOTES_PATH.as_posix(),
            "blocked_reason": blocked_reason,
        },
    )
    write_markdown(
        context.run_root / "release_notes_refresh.md",
        [
            "# Stage 20 Release Notes Refresh",
            "",
            f"- Status: `{status_key}`",
            f"- Agent: `{agent_contract['id']}`",
            f"- Notes path: `{_NOTES_PATH.as_posix()}`",
            f"- Freshness commit: `{freshness_commit or '(none)'}`",
            f"- Blocked reason: `{blocked_reason or 'none'}`",
            "",
            "## Delivery Posture",
            *[f"- {item}" for item in stage_contract.get("delivery_posture", [])],
        ],
    )

    upsert_stage_status(
        context.stage_status_path,
        stage_name=_STAGE_RELEASE_NOTES,
        status="blocked" if blocked_reason else "completed",
        owner_agent="release-prerelease",
        summary=(
            "Stage 20 refreshed the branch release notes and created a freshness commit."
            if not blocked_reason
            else "Stage 20 could not refresh and push release notes cleanly."
        ),
        artifacts=["release_notes_refresh.md", "release_notes_commit.json", "release_notes_context.json"],
    )
    upsert_decisions(
        context.decisions_path,
        [
            {
                "id": _STAGE_RELEASE_NOTES,
                "decision": (
                    "Release notes were refreshed from repo-backed orchestrator artifacts and committed."
                    if not blocked_reason
                    else "Release notes refresh was blocked before the freshness commit could be pushed."
                ),
                "status": "blocked" if blocked_reason else "completed",
                "source": _STAGE_RELEASE_NOTES,
            }
        ],
    )

    next_action = stage_text(stage_contract, "next_actions", status_key)
    context.branch_state["status"] = branch_status
    context.branch_state["current_stage"] = _STAGE_RELEASE_NOTES
    context.branch_state["next_action"] = next_action
    context.branch_state["anomaly_flag"] = bool(blocked_reason)
    context.branch_state["last_updated_at"] = iso_now()
    context.branch_state["last_updated_by"] = "release-prerelease"
    write_json(context.branch_state_path, context.branch_state)

    update_poa_section(
        context.poa_path,
        begin_marker=_POA_BEGIN,
        end_marker=_POA_END,
        lines=[
            "## Stage 20 Release Notes Refresh",
            f"- Owner agent: `release-prerelease`",
            f"- Allowed skills: {', '.join(f'`{item}`' for item in allowed_skills) or '(none)'}",
            f"- Allowed plugins: {', '.join(f'`{item}`' for item in allowed_plugins) or '(none)'}",
            f"- Notes path: `{_NOTES_PATH.as_posix()}`",
            f"- Freshness commit: `{freshness_commit or '(none)'}`",
            f"- Blocked reason: `{blocked_reason or 'none'}`",
        ],
    )
    write_handoff(
        context.handoff_path,
        summary=stage_text(stage_contract, "handoff_summaries", status_key),
        next_action=next_action,
        status=branch_status,
        at=iso_now(),
    )
    write_resume_context(
        context.resume_context_path,
        active_run_key=context.active_run_key,
        current_stage=_STAGE_RELEASE_NOTES,
        next_action=next_action,
        notes=render_templates(
            stage_contract.get("resume_note_templates", []),
            release_agent=agent_contract["id"],
            notes_path=_NOTES_PATH.as_posix(),
            freshness_commit=freshness_commit or "(none)",
            blocked_reason=blocked_reason or "none",
        ),
    )

    return ReleaseNotesStageResult(
        repo_root=str(context.repo_root),
        branch_name=context.branch_name,
        active_run_key=context.active_run_key,
        status=branch_status,
        current_stage=_STAGE_RELEASE_NOTES,
        next_action=next_action,
        release_agent=agent_contract["id"],
        notes_path=_NOTES_PATH.as_posix(),
        freshness_commit=freshness_commit,
        blocked_reason=blocked_reason,
        message=stage_text(stage_contract, "messages", "completed") or "Stage 20 release notes refresh completed.",
    )


def _collect_release_note_context(run_root: Path) -> Dict[str, List[str]]:
    return {
        "summary": [
            "Adds the reusable GHDP orchestration kernel contract and repo-driven provider/topology model.",
            "Finishes the remaining Phase 1 stages for release notes refresh, PR/Jira integration, and final historian capture.",
            "Introduces a repo-defined sub-agent scenario path for Codex, Claude, and VS Code-hosted agent compatibility."
        ],
        "commands": [
            "ghdp orchestrate release-notes-refresh",
            "ghdp orchestrate pr-integrate",
            "ghdp orchestrate historian-closeout",
            "ghdp orchestrate subagent-scenario --scenario-id new_feature_subagent_smoke --execute-provider"
        ],
        "evidence": [
            _first_heading_line(run_root / "release_readiness_summary.md"),
            _first_heading_line(run_root / "artifact_validation_summary.md"),
            _first_heading_line(run_root / "test_execution_summary.md"),
        ],
    }


def _render_notes(payload: Dict[str, List[str]]) -> str:
    lines = [
        "## Summary",
        *[f"- {item}" for item in payload.get("summary", [])],
        "",
        "## Commands",
        "```bash",
        *payload.get("commands", []),
        "```",
        "",
        "## Evidence",
        *[f"- {item}" for item in payload.get("evidence", []) if item],
    ]
    return "\n".join(lines).rstrip() + "\n"


def _first_heading_line(path: Path) -> str:
    if not path.exists():
        return ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("- ") and len(line) > 2:
            return line[2:].strip()
    return ""


def _commit_and_push_notes(repo_root: Path, notes_path: Path, ticket_key: str) -> str:
    rel_path = str(notes_path.relative_to(repo_root)).replace("\\", "/")
    diff = run_cmd(["git", "diff", "--name-only", "--", rel_path], check=True, cwd=repo_root).stdout.strip()
    staged = run_cmd(["git", "diff", "--cached", "--name-only", "--", rel_path], check=True, cwd=repo_root).stdout.strip()
    if not diff and not staged:
        head = run_cmd(["git", "rev-parse", "HEAD"], check=True, cwd=repo_root).stdout.strip()
        return head
    run_cmd(["git", "add", rel_path], check=True, cwd=repo_root)
    commit_message = f"docs(release-notes): refresh {ticket_key.lower()} orchestration notes"
    commit_result = run_cmd(["git", "commit", "-m", commit_message], check=False, cwd=repo_root)
    if commit_result.returncode != 0 and "nothing to commit" not in ((commit_result.stderr or "") + (commit_result.stdout or "")).lower():
        raise PlatformError(
            "Failed to create the release-notes freshness commit.",
            code="E_ORCHESTRATE_GIT_COMMIT_FAILED",
            reason="release_notes",
        )
    run_cmd(["git", "push"], check=True, cwd=repo_root)
    return run_cmd(["git", "rev-parse", "HEAD"], check=True, cwd=repo_root).stdout.strip()


def _normalize_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
