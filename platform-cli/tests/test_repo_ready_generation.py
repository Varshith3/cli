from __future__ import annotations

import json
from pathlib import Path

from platform_cli.tools.ai_provider import ProviderStatus
from platform_cli.tools.repo_ready import assess_repo_ready
from platform_cli.tools.repo_ready_generation import (
    ARCHITECTURE_REVIEW_MARKER,
    CONFIG_REVIEW_STATUS,
    INTENT_REVIEW_STATUS,
    RUNBOOK_REVIEW_STATUS,
    generate_repo_ready_drafts,
)


class _FakeProgress:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def target_started(self, rel_path: str, *, action: str = "Generating") -> None:
        self.events.append(("started", rel_path))

    def heartbeat_callback(self, rel_path: str, *, action: str = "Still generating"):
        self.events.append(("heartbeat_registered", rel_path))
        return lambda elapsed_s: self.events.append(("heartbeat", rel_path))

    def target_done(self, rel_path: str, *, outcome: str = "generated") -> None:
        self.events.append((outcome, rel_path))

    def target_failed(self, rel_path: str, message: str, *, action: str = "generation failed") -> None:
        self.events.append(("failed", rel_path))


def test_generate_repo_ready_drafts_writes_review_marked_outputs(tmp_path: Path, monkeypatch) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    (repo_dir / "README.md").write_text("# Sample Repo\n", encoding="utf-8")
    assess_repo_ready(mode="fix", repo_root=repo_dir)

    def _fake_generate_text(*, provider, statuses, prompt, heartbeat=None):
        if "Generate the intent now." in prompt:
            return "Align repo readiness with the spec by generating missing canonical repo artifacts and the readiness report while keeping feature-branch intent available for downstream automation."
        if "Target file: .ghdp/config.yaml" in prompt:
            return """
schema_version: "1.0"
repo:
  name: "sample-repo"
  type: backend
  traits:
    - cli
classification:
  source: inferred
  evidence:
    - pyproject.toml
risk:
  tier: medium
execution:
  mode: sandbox-only
enabled:
  tools: []
  teams: []
  subagents: false
metadata:
  managed_by: ghdp
  template_version: "1.0.0"
""".strip()
        if "Target file: .ghdp/runbook.yaml" in prompt:
            return """
schema_version: "1.0"
commands:
  build:
    - cmd: python -m build
  test:
    - cmd: python -m pytest -q
  lint:
    - cmd: ruff check .
  format:
    - cmd: ruff format .
  start: []
  dev: []
entrypoints:
  - src/platform_cli/cli.py
services: []
env_vars:
  - GHDP_NON_INTERACTIVE
notes:
  status: pending-user-review
metadata:
  managed_by: ghdp
  template_version: "1.0.0"
""".strip()
        return """
# GHDP Architecture

## Module Map

- `src/` contains the main application code.

## Key Entry Points

- `src/platform_cli/cli.py`

## Critical Flows

- CLI commands flow through the Typer application entrypoint.

## Validation

- Refer to `.ghdp/runbook.yaml` for validation commands.

## Ownership

- needs confirmation

## Do Not Touch

- Generated artifacts under `.ghdp/`

## Open Questions

- Ownership needs confirmation.
""".strip()

    monkeypatch.setattr("platform_cli.tools.repo_ready_generation.generate_text", _fake_generate_text)
    monkeypatch.setattr("platform_cli.tools.repo_ready_generation.current_branch_name", lambda repo_root: "feature/EPPE-1234-TECHNICAL-sample")
    monkeypatch.setattr("platform_cli.tools.repo_ready.current_branch_name", lambda repo_root: "feature/EPPE-1234-TECHNICAL-sample")
    monkeypatch.setattr(
        "platform_cli.tools.repo_ready_generation.fetch_jira_context",
        lambda ticket, mode: {
            "summary": "Align repo readiness with the implementation spec",
            "description": "Use the feature branch ticket to keep branch intent in sync with generated repo artifacts.",
        },
    )

    progress = _FakeProgress()
    result = generate_repo_ready_drafts(
        repo_root=repo_dir,
        provider="codex",
        statuses={
            "codex": ProviderStatus("codex", True, "codex", True, "ok"),
            "claude": ProviderStatus("claude", False, "", False, "missing"),
        },
        targets=[".ghdp/config.yaml", ".ghdp/runbook.yaml", ".ghdp/architecture.md"],
        progress=progress,
    )

    assert result.generated == [
        ".ghdp/config.yaml",
        ".ghdp/runbook.yaml",
        ".ghdp/architecture.md",
        ".ghdp/frbr/intent.json",
    ]
    assert result.warnings == []

    config_text = (repo_dir / ".ghdp" / "config.yaml").read_text(encoding="utf-8")
    runbook_text = (repo_dir / ".ghdp" / "runbook.yaml").read_text(encoding="utf-8")
    architecture_text = (repo_dir / ".ghdp" / "architecture.md").read_text(encoding="utf-8")
    intent_payload = (repo_dir / ".ghdp" / "frbr" / "intent.json").read_text(encoding="utf-8")

    assert f"review_status: {CONFIG_REVIEW_STATUS}" in config_text
    assert f"status: {RUNBOOK_REVIEW_STATUS}" in runbook_text
    assert ARCHITECTURE_REVIEW_MARKER in architecture_text
    assert f"\"status\": \"{INTENT_REVIEW_STATUS}\"" in intent_payload
    assert "\"intent\": \"Align repo readiness with the spec" in intent_payload

    verify_result = assess_repo_ready(mode="verify", repo_root=repo_dir)
    assert ".ghdp/config.yaml" in verify_result.pending_required
    assert ".ghdp/runbook.yaml" in verify_result.pending_required
    assert ".ghdp/architecture.md" in verify_result.pending_recommended
    assert ".ghdp/frbr/intent.json" in verify_result.pending_recommended
    assert ("started", ".ghdp/config.yaml") in progress.events
    assert ("updated", ".ghdp/config.yaml") in progress.events
    assert ("started", ".ghdp/frbr/intent.json") in progress.events
    assert ("generated", ".ghdp/frbr/intent.json") in progress.events


def test_generate_repo_ready_drafts_keeps_existing_file_on_invalid_output(tmp_path: Path, monkeypatch) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    assess_repo_ready(mode="fix", repo_root=repo_dir)

    original = (repo_dir / ".ghdp" / "config.yaml").read_text(encoding="utf-8")
    monkeypatch.setattr("platform_cli.tools.repo_ready_generation.generate_text", lambda **kwargs: "repo:\n  type: nope")

    progress = _FakeProgress()
    result = generate_repo_ready_drafts(
        repo_root=repo_dir,
        provider="codex",
        statuses={
            "codex": ProviderStatus("codex", True, "codex", True, "ok"),
            "claude": ProviderStatus("claude", False, "", False, "missing"),
        },
        targets=[".ghdp/config.yaml"],
        progress=progress,
    )

    assert ".ghdp/config.yaml" in result.failed
    assert (repo_dir / ".ghdp" / "config.yaml").read_text(encoding="utf-8") == original
    assert ("failed", ".ghdp/config.yaml") in progress.events


def test_generate_repo_ready_drafts_regenerates_stale_feature_branch_intent(tmp_path: Path, monkeypatch) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    monkeypatch.setattr(
        "platform_cli.tools.repo_ready_generation.current_branch_name",
        lambda repo_root: "feature/EPPE-1234-TECHNICAL-sample",
    )
    monkeypatch.setattr(
        "platform_cli.tools.repo_ready.current_branch_name",
        lambda repo_root: "feature/EPPE-1234-TECHNICAL-sample",
    )
    assess_repo_ready(mode="fix", repo_root=repo_dir)
    (repo_dir / ".ghdp" / "frbr" / "intent.json").write_text(
        """
{
  "schema_version": "1.0",
  "generated_by": "ghdp",
  "source": "branch_create_generated",
  "repo_name": "sample-repo",
  "branch_name": "feature/EPPE-9999-TECHNICAL-other-work",
  "ticket_key": "EPPE-9999",
  "intent": "Old branch intent",
  "summary": "Old summary",
  "provider": "codex",
  "generated_at": "2026-04-01T00:00:00Z",
  "status": "ready"
}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "platform_cli.tools.repo_ready_generation.fetch_jira_context",
        lambda ticket, mode: {
            "summary": "Align repo readiness with the implementation spec",
            "description": "Use the branch ticket to keep the feature branch intent current.",
        },
    )
    monkeypatch.setattr(
        "platform_cli.tools.repo_ready_generation.generate_text",
        lambda **kwargs: "Refresh the feature branch intent so it matches the current ticket and implementation scope.",
    )

    result = generate_repo_ready_drafts(
        repo_root=repo_dir,
        provider="codex",
        statuses={
            "codex": ProviderStatus("codex", True, "codex", True, "ok"),
            "claude": ProviderStatus("claude", False, "", False, "missing"),
        },
        targets=[],
    )

    assert result.generated == [".ghdp/frbr/intent.json"]
    payload = json.loads((repo_dir / ".ghdp" / "frbr" / "intent.json").read_text(encoding="utf-8"))
    assert payload["branch_name"] == "feature/EPPE-1234-TECHNICAL-sample"
    assert payload["ticket_key"] == "EPPE-1234"
    assert payload["status"] == INTENT_REVIEW_STATUS
    assert payload["intent"] == "Refresh the feature branch intent so it matches the current ticket and implementation scope."
