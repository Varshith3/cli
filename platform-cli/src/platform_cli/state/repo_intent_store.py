# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from platform_cli.core.errors import PlatformError


def persist_repo_intent(
    *,
    repo_root: Path,
    intent: str,
    summary: str,
    provider: str,
    relative_path: str,
    branch_name: str = "",
    ticket_key: str = "",
    source: str = "branch_create_generated",
) -> Path:
    if not intent.strip():
        raise PlatformError(
            "Intent text cannot be empty.",
            code="E_REPO_INTENT_EMPTY",
            reason="intent",
        )

    target = repo_root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "generated_by": "ghdp",
        "source": source.strip() or "branch_create_generated",
        "repo_name": repo_root.name,
        "branch_name": branch_name.strip(),
        "ticket_key": ticket_key.strip(),
        "intent": intent.strip(),
        "summary": (summary or "").strip(),
        "provider": provider.strip(),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return target
