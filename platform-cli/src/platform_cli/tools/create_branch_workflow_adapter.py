from __future__ import annotations

import os
from pathlib import Path

from .create_branch_service import BranchCreateResult


def write_branch_outputs_if_supported(result: BranchCreateResult, *, explicit_path: str | None = None) -> bool:
    output_path = (explicit_path or str(os.getenv("GITHUB_OUTPUT", "") or "")).strip()
    if not output_path:
        return False

    values = {
        "request_id": result.request_id,
        "repo": result.repo,
        "ticket_key": result.ticket,
        "branch_type": result.branch_type,
        "slug": result.slug,
        "base_branch": result.base_branch,
        "branch_name": result.branch_name,
        "branch_created": _bool_text(result.branch_created),
        "dry_run": _bool_text(result.dry_run),
        "jira_validated": _bool_text(result.jira_validated),
        "jira_comment_posted": _bool_text(result.jira_comment_posted),
        "intent_generated": _bool_text(result.intent_generated),
        "intent_saved": _bool_text(result.intent_saved),
        "intent_committed": _bool_text(result.intent_committed),
        "intent_provider": result.intent_provider or "",
        "intent_path": result.intent_path or "",
    }
    _append_github_output(Path(output_path), values)
    return True


def _append_github_output(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def _bool_text(value: bool) -> str:
    return "true" if value else "false"
