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


_STAGE_PRERELEASE = "stage19_prerelease_creation"
_STAGE_PUBLISHED = "stage19b_published_prerelease_retest"
_STAGE_PR = "stage21_pr_external_integration"
_POA_BEGIN = "<!-- GHDP:BEGIN STAGE21_PR_INTEGRATION -->"
_POA_END = "<!-- GHDP:END STAGE21_PR_INTEGRATION -->"


@dataclass
class PrExternalStageResult:
    repo_root: str
    branch_name: str
    active_run_key: str
    status: str
    current_stage: str
    next_action: str
    integration_agent: str
    pr_link: str
    blocked_reason: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_pr_external_integration_stage(*, repo_root: Path | None = None) -> PrExternalStageResult:
    context = resolve_active_run_context(repo_root=repo_root)
    assert_stage_completed(context.stage_status, _STAGE_PRERELEASE)
    assert_stage_completed(context.stage_status, _STAGE_PUBLISHED)
    stage_contract = load_stage_contract(stage_id=_STAGE_PR, repo_root=context.repo_root)
    agent_contract = load_agent_contract(agent_id="pr-external-integration", repo_root=context.repo_root)
    allowed_skills = _normalize_list(agent_contract.get("allowed_skills", []))
    allowed_plugins = _normalize_list(agent_contract.get("allowed_plugins", []))

    blocked_reason = ""
    pr_link = ""
    jira_note = ""
    prerelease_comment_note = ""
    prerelease_url = _resolve_prerelease_url(context.run_root / "prerelease_plan.json")
    try:
        hygiene = _validate_branch_hygiene(context)
        pr_link = _ensure_pr(context)
        prerelease_comment_note = _comment_prerelease_on_pr(pr_link=pr_link, prerelease_url=prerelease_url, ticket_key=context.ticket_key)
        jira_note = _comment_on_jira(context.ticket_key, pr_link)
    except PlatformError as exc:
        blocked_reason = f"{exc.code}:{exc.reason}"
        hygiene = exc.reason if exc.code == "E_ORCHESTRATE_PR_BRANCH_HYGIENE" else "not_recorded"

    if not blocked_reason:
        _write_branch_hygiene_artifacts(context.run_root, hygiene)
    else:
        _write_branch_hygiene_artifacts(context.run_root, hygiene if isinstance(hygiene, dict) else {"status": "blocked", "reason": hygiene})

    status_key = "blocked" if blocked_reason else "completed"
    branch_status = "blocked" if blocked_reason else "paused"
    next_action = stage_text(stage_contract, "next_actions", status_key)

    write_json(
        context.run_root / "pr_integration_bindings.json",
        {
            "schema_version": "1.0",
            "integration_agent": agent_contract["id"],
            "allowed_skills": allowed_skills,
            "allowed_plugins": allowed_plugins,
            "pr_link": pr_link,
            "prerelease_url": prerelease_url,
            "prerelease_comment_note": prerelease_comment_note,
            "jira_ticket": context.ticket_key,
            "blocked_reason": blocked_reason,
        },
    )
    write_markdown(
        context.run_root / "pr_integration_summary.md",
        [
            "# Stage 21 PR Integration",
            "",
            f"- Status: `{status_key}`",
            f"- PR link: `{pr_link or '(missing)'}`",
            f"- Prerelease URL: `{prerelease_url or '(missing)'}`",
            f"- Jira ticket: `{context.ticket_key or '(missing)'}`",
            f"- Blocked reason: `{blocked_reason or 'none'}`",
        ],
    )
    write_markdown(
        context.run_root / "pr_prerelease_comment.md",
        [
            "# PR Prerelease Comment",
            "",
            f"- PR link: `{pr_link or '(missing)'}`",
            f"- Prerelease URL: `{prerelease_url or '(missing)'}`",
            f"- Result: `{prerelease_comment_note or '(not posted)'}`",
        ],
    )
    write_json(
        context.run_root / "pr_prerelease_comment_result.json",
        {
            "schema_version": "1.0",
            "pr_link": pr_link,
            "prerelease_url": prerelease_url,
            "result": prerelease_comment_note,
            "blocked_reason": blocked_reason,
        },
    )
    write_markdown(
        context.run_root / "jira_update_summary.md",
        [
            "# Jira Update Summary",
            "",
            f"- Ticket: `{context.ticket_key or '(missing)'}`",
            f"- Result: `{jira_note or '(not posted)'}`",
        ],
    )

    upsert_stage_status(
        context.stage_status_path,
        stage_name=_STAGE_PR,
        status=status_key,
        owner_agent="pr-external-integration",
        summary=(
            "Stage 21 passed branch hygiene, created or reused the PR, posted the prerelease comment, and recorded the Jira update."
            if not blocked_reason
            else "Stage 21 encountered a PR or Jira integration blocker."
        ),
        artifacts=[
            "pr_integration_summary.md",
            "pr_integration_bindings.json",
            "pr_branch_hygiene.md",
            "pr_branch_hygiene.json",
            "pr_prerelease_comment.md",
            "pr_prerelease_comment_result.json",
            "jira_update_summary.md",
        ],
    )
    upsert_decisions(
        context.decisions_path,
        [
            {
                "id": _STAGE_PR,
                "decision": "PR and Jira integration completed." if not blocked_reason else "PR or Jira integration blocked.",
                "status": status_key,
                "source": _STAGE_PR,
            }
        ],
    )
    context.branch_state["status"] = branch_status
    context.branch_state["current_stage"] = _STAGE_PR
    context.branch_state["next_action"] = next_action
    context.branch_state["anomaly_flag"] = bool(blocked_reason)
    context.branch_state["last_updated_at"] = iso_now()
    context.branch_state["last_updated_by"] = "pr-external-integration"
    write_json(context.branch_state_path, context.branch_state)

    update_poa_section(
        context.poa_path,
        begin_marker=_POA_BEGIN,
        end_marker=_POA_END,
        lines=[
            "## Stage 21 PR and External Integration",
            f"- Owner agent: `pr-external-integration`",
            f"- Allowed skills: {', '.join(f'`{item}`' for item in allowed_skills) or '(none)'}",
            f"- Allowed plugins: {', '.join(f'`{item}`' for item in allowed_plugins) or '(none)'}",
            f"- PR link: `{pr_link or '(missing)'}`",
            f"- Prerelease URL: `{prerelease_url or '(missing)'}`",
            f"- Jira ticket: `{context.ticket_key or '(missing)'}`",
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
        current_stage=_STAGE_PR,
        next_action=next_action,
        notes=render_templates(
            stage_contract.get("resume_note_templates", []),
            integration_agent=agent_contract["id"],
            pr_link=pr_link or "(missing)",
            ticket_key=context.ticket_key or "(missing)",
            blocked_reason=blocked_reason or "none",
        ),
    )

    return PrExternalStageResult(
        repo_root=str(context.repo_root),
        branch_name=context.branch_name,
        active_run_key=context.active_run_key,
        status=branch_status,
        current_stage=_STAGE_PR,
        next_action=next_action,
        integration_agent=agent_contract["id"],
        pr_link=pr_link,
        blocked_reason=blocked_reason,
        message=stage_text(stage_contract, "messages", status_key) or "Stage 21 PR integration completed.",
    )


def _ensure_pr(context: Any) -> str:
    branch = context.branch_name
    view = run_cmd(
        ["gh", "pr", "list", "--head", branch, "--json", "url", "--limit", "1"],
        check=True,
        cwd=context.repo_root,
    )
    payload = json.loads(view.stdout or "[]")
    if isinstance(payload, list) and payload:
        return str(payload[0].get("url", "")).strip()

    title = f"[{context.ticket_key or 'EPPE'}] Phase 1 agentic orchestrator foundation"
    body = "\n".join(
        [
            "## Summary",
            "- Completes the Phase 1 orchestrator run through release, PR integration, and historian closeout.",
            "- Adds repo-driven kernel, topology, provider-adapter, and sub-agent scenario contracts.",
            "",
            "## Validation",
            "- Orchestrator stage suite",
            "- Repo-defined sub-agent scenario plan",
        ]
    )
    create = run_cmd(
        ["gh", "pr", "create", "--base", "develop", "--head", branch, "--title", title, "--body", body, "--draft"],
        check=True,
        cwd=context.repo_root,
    )
    return (create.stdout or "").strip()


def _validate_branch_hygiene(context: Any) -> Dict[str, Any]:
    run_cmd(["git", "fetch", "origin", "develop"], check=True, cwd=context.repo_root)
    develop_sha = (run_cmd(["git", "rev-parse", "origin/develop"], check=True, cwd=context.repo_root).stdout or "").strip()
    merge_base = (run_cmd(["git", "merge-base", "HEAD", "origin/develop"], check=True, cwd=context.repo_root).stdout or "").strip()
    merge_commits = (run_cmd(["git", "rev-list", "--merges", "origin/develop..HEAD"], check=True, cwd=context.repo_root).stdout or "").splitlines()
    if merge_base != develop_sha:
        raise PlatformError(
            "Branch is not rebased onto the latest origin/develop.",
            code="E_ORCHESTRATE_PR_BRANCH_HYGIENE",
            reason="rebase_required",
        )
    if any(item.strip() for item in merge_commits):
        raise PlatformError(
            "Branch history contains merge commits after origin/develop.",
            code="E_ORCHESTRATE_PR_BRANCH_HYGIENE",
            reason="merge_commits_present",
        )
    return {
        "status": "completed",
        "develop_sha": develop_sha,
        "merge_base": merge_base,
        "merge_commits": [],
    }


def _write_branch_hygiene_artifacts(run_root: Path, hygiene: Dict[str, Any]) -> None:
    write_markdown(
        run_root / "pr_branch_hygiene.md",
        [
            "# PR Branch Hygiene",
            "",
            f"- Status: `{str(hygiene.get('status', 'blocked')).strip() or 'blocked'}`",
            f"- develop SHA: `{str(hygiene.get('develop_sha', '(missing)')).strip() or '(missing)'}`",
            f"- merge-base: `{str(hygiene.get('merge_base', '(missing)')).strip() or '(missing)'}`",
            f"- Reason: `{str(hygiene.get('reason', 'none')).strip() or 'none'}`",
        ],
    )
    write_json(
        run_root / "pr_branch_hygiene.json",
        {
            "schema_version": "1.0",
            **hygiene,
        },
    )


def _resolve_prerelease_url(prerelease_plan_path: Path) -> str:
    if not prerelease_plan_path.exists():
        return ""
    payload = json.loads(prerelease_plan_path.read_text(encoding="utf-8"))
    release_plan = payload.get("release_plan", {})
    repo_name = str(release_plan.get("repo_name_with_owner", "")).strip()
    tag = str(release_plan.get("tag", "")).strip()
    if not repo_name or not tag:
        return ""
    return f"https://github.com/{repo_name}/releases/tag/{tag}"


def _comment_prerelease_on_pr(*, pr_link: str, prerelease_url: str, ticket_key: str) -> str:
    if not pr_link or not prerelease_url:
        return "skipped: missing pr or prerelease link"
    body = "\n".join(
        [
            f"Latest prerelease for `{ticket_key or 'this branch'}`:",
            prerelease_url,
        ]
    )
    result = run_cmd(["gh", "pr", "comment", pr_link, "--body", body], check=False)
    if result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip()
        raise PlatformError(
            f"Failed to post prerelease comment on PR {pr_link}: {output}",
            code="E_ORCHESTRATE_PR_COMMENT_FAILED",
            reason="pr_prerelease_comment",
        )
    return "posted via gh"


def _comment_on_jira(ticket_key: str, pr_link: str) -> str:
    if not ticket_key:
        return "skipped: missing ticket key"
    body = f"Phase 1 agentic orchestrator branch is ready for review.\nPR: {pr_link}"
    result = run_cmd(
        ["acli", "jira", "workitem", "comment", "create", "--key", ticket_key, "--body", body],
        check=False,
    )
    if result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip()
        raise PlatformError(
            f"Failed to comment on Jira ticket {ticket_key}: {output}",
            code="E_ORCHESTRATE_JIRA_COMMENT_FAILED",
            reason="jira_comment",
        )
    return "posted via acli"


def _normalize_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
