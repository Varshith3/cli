from __future__ import annotations

import json
from pathlib import Path

from platform_cli.state.repo_intent_store import persist_repo_intent


def test_persist_repo_intent_writes_minimal_payload(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    path = persist_repo_intent(
        repo_root=repo_root,
        intent="Implement remote branch creation and intent persistence.",
        summary="Improve branch orchestration",
        provider="codex",
        relative_path=".ghdp/frbr/intent.json",
        branch_name="feature/EPPE-1234-TECHNICAL-branch-orchestration",
        ticket_key="EPPE-1234",
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert path == repo_root / ".ghdp/frbr/intent.json"
    assert payload["schema_version"] == "1.0"
    assert payload["generated_by"] == "ghdp"
    assert payload["source"] == "branch_create_generated"
    assert payload["repo_name"] == "repo"
    assert payload["branch_name"] == "feature/EPPE-1234-TECHNICAL-branch-orchestration"
    assert payload["ticket_key"] == "EPPE-1234"
    assert payload["intent"] == "Implement remote branch creation and intent persistence."
    assert payload["summary"] == "Improve branch orchestration"
    assert payload["provider"] == "codex"
    assert payload["generated_at"].endswith("Z")
