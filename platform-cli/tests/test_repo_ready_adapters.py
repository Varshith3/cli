from __future__ import annotations

import json
from pathlib import Path

from platform_cli.core.errors import PlatformError
from platform_cli.tools.ai_provider import ProviderStatus
from platform_cli.tools.repo_ready import accept_repo_ready_reviews, assess_repo_ready, build_repo_readiness_report
from platform_cli.tools.repo_ready_adapters import (
    ADAPTER_STATUS_DRAFT,
    ADAPTER_STATUS_PLACEHOLDER,
    AGENTS_ADAPTER_REL_PATH,
    CLAUDE_ADAPTER_REL_PATH,
    CLAUDE_SETTINGS_REL_PATH,
    CODEX_CONFIG_REL_PATH,
    MCP_CONFIG_REL_PATH,
    inspect_repo_local_adapters,
    sync_repo_local_adapters,
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


def _finalize_required_repo_files(repo_dir: Path, *, enabled_tools: list[str]) -> None:
    (repo_dir / ".ghdp" / "config.yaml").write_text(
        f"""
schema_version: "1.0"
repo:
  name: sample-repo
  type: infrastructure
  traits:
    - cli
classification:
  source: inferred
  evidence:
    - platform-cli/src
risk:
  tier: medium
execution:
  mode: sandbox-only
enabled:
  tools: {json.dumps(enabled_tools)}
  teams: []
  subagents: false
metadata:
  managed_by: ghdp
  template_version: "1.0.0"
""".strip(),
        encoding="utf-8",
    )
    (repo_dir / ".ghdp" / "runbook.yaml").write_text(
        """
schema_version: "1.0"
commands:
  build:
    - cmd: python -m build
  test:
    - cmd: python -m pytest -q
  lint: []
  format: []
  start: []
  dev: []
entrypoints: []
services: []
env_vars: []
notes:
  status: ready
metadata:
  managed_by: ghdp
  template_version: "1.0.0"
""".strip(),
        encoding="utf-8",
    )


def test_sync_repo_local_adapters_creates_placeholders_and_templates_without_ai(tmp_path: Path) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    assess_repo_ready(mode="fix", repo_root=repo_dir)

    result = sync_repo_local_adapters(
        repo_root=repo_dir,
        statuses={
            "codex": ProviderStatus("codex", False, "", False, "missing"),
            "claude": ProviderStatus("claude", False, "", False, "missing"),
        },
        allow_ai=False,
    )

    assert result.generated == [
        CLAUDE_ADAPTER_REL_PATH,
        AGENTS_ADAPTER_REL_PATH,
        CLAUDE_SETTINGS_REL_PATH,
        CODEX_CONFIG_REL_PATH,
        MCP_CONFIG_REL_PATH,
    ]

    claude_text = (repo_dir / CLAUDE_ADAPTER_REL_PATH).read_text(encoding="utf-8")
    agents_text = (repo_dir / AGENTS_ADAPTER_REL_PATH).read_text(encoding="utf-8")
    assert f"adapter_status: \"{ADAPTER_STATUS_PLACEHOLDER}\"" in claude_text
    assert f"adapter_status: \"{ADAPTER_STATUS_PLACEHOLDER}\"" in agents_text

    adapters, _ = inspect_repo_local_adapters(repo_dir)
    states = {item.rel_path: item.state for item in adapters}
    assert states[CLAUDE_ADAPTER_REL_PATH] == ADAPTER_STATUS_PLACEHOLDER
    assert states[AGENTS_ADAPTER_REL_PATH] == ADAPTER_STATUS_PLACEHOLDER
    assert states[CLAUDE_SETTINGS_REL_PATH] == "ready"
    assert states[CODEX_CONFIG_REL_PATH] == "ready"
    assert states[MCP_CONFIG_REL_PATH] == "ready"


def test_sync_repo_local_adapters_uses_native_providers_for_markdown(tmp_path: Path, monkeypatch) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    assess_repo_ready(mode="fix", repo_root=repo_dir)
    (repo_dir / ".ghdp" / "readiness.json").write_text(
        json.dumps({"summary": "not_ready", "next_steps": []}, indent=2) + "\n",
        encoding="utf-8",
    )

    def _fake_generate_text(*, provider, statuses, prompt, heartbeat=None):
        if "Target file: CLAUDE.md" in prompt:
            assert provider == "claude"
            return """
# Claude Code Instructions

`.ghdp/*` is the canonical source of truth for this repository.

## Read Order

1. `.ghdp/frbr/intent.json` when present
2. `.ghdp/readiness.json` when present
3. `.ghdp/architecture.md`
4. `.ghdp/runbook.yaml`
5. `.ghdp/config.yaml`
6. `.ghdp/guardrails.yaml`
7. `.ghdp/lock.yaml`

## Working Rules

- Do not invent missing GHDP content.

## Validation

- Refer to `.ghdp/runbook.yaml` and `.ghdp/readiness.json`.

## Notes

- Ask the user when missing GHDP context blocks safe progress.
""".strip()
        assert provider == "codex"
        return """
# Agent Instructions

`.ghdp/*` is the canonical source of truth for this repository.

## Read Order

1. `.ghdp/frbr/intent.json` when present
2. `.ghdp/readiness.json` when present
3. `.ghdp/architecture.md`
4. `.ghdp/runbook.yaml`
5. `.ghdp/config.yaml`
6. `.ghdp/guardrails.yaml`
7. `.ghdp/lock.yaml`

## Working Rules

- Do not invent missing GHDP content.

## Validation

- Refer to `.ghdp/runbook.yaml` and `.ghdp/readiness.json`.

## Notes

- Ask the user when missing GHDP context blocks safe progress.
""".strip()

    monkeypatch.setattr("platform_cli.tools.repo_ready_adapters.generate_text", _fake_generate_text)

    progress = _FakeProgress()
    result = sync_repo_local_adapters(
        repo_root=repo_dir,
        statuses={
            "codex": ProviderStatus("codex", True, "codex", True, "ok"),
            "claude": ProviderStatus("claude", True, "claude", True, "ok"),
        },
        allow_ai=True,
        progress=progress,
    )

    assert result.generated == [
        CLAUDE_ADAPTER_REL_PATH,
        AGENTS_ADAPTER_REL_PATH,
        CLAUDE_SETTINGS_REL_PATH,
        CODEX_CONFIG_REL_PATH,
        MCP_CONFIG_REL_PATH,
    ]
    assert f"adapter_status: \"{ADAPTER_STATUS_DRAFT}\"" in (repo_dir / CLAUDE_ADAPTER_REL_PATH).read_text(encoding="utf-8")
    assert f"adapter_status: \"{ADAPTER_STATUS_DRAFT}\"" in (repo_dir / AGENTS_ADAPTER_REL_PATH).read_text(encoding="utf-8")

    report = assess_repo_ready(mode="report", repo_root=repo_dir)
    adapter_states = {item.rel_path: item.state for item in report.adapters}
    assert adapter_states[CLAUDE_ADAPTER_REL_PATH] == ADAPTER_STATUS_DRAFT
    assert adapter_states[AGENTS_ADAPTER_REL_PATH] == ADAPTER_STATUS_DRAFT
    assert ("started", CLAUDE_ADAPTER_REL_PATH) in progress.events
    assert ("generated", CLAUDE_ADAPTER_REL_PATH) in progress.events
    assert ("started", AGENTS_ADAPTER_REL_PATH) in progress.events
    assert ("generated", AGENTS_ADAPTER_REL_PATH) in progress.events


def test_repo_accept_clears_adapter_review_markers(tmp_path: Path, monkeypatch) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    assess_repo_ready(mode="fix", repo_root=repo_dir)
    monkeypatch.setattr(
        "platform_cli.tools.repo_ready_adapters.generate_text",
        lambda **kwargs: """
# Agent Instructions

`.ghdp/*` is the canonical source of truth for this repository.

## Read Order

1. `.ghdp/frbr/intent.json` when present
2. `.ghdp/readiness.json` when present
3. `.ghdp/architecture.md`
4. `.ghdp/runbook.yaml`
5. `.ghdp/config.yaml`
6. `.ghdp/guardrails.yaml`
7. `.ghdp/lock.yaml`

## Working Rules

- Do not invent missing GHDP content.

## Validation

- Refer to `.ghdp/runbook.yaml` and `.ghdp/readiness.json`.

## Notes

- Ask the user when missing GHDP context blocks safe progress.
""".strip(),
    )

    sync_repo_local_adapters(
        repo_root=repo_dir,
        statuses={
            "codex": ProviderStatus("codex", True, "codex", True, "ok"),
            "claude": ProviderStatus("claude", True, "claude", True, "ok"),
        },
        allow_ai=True,
    )

    changed = accept_repo_ready_reviews(repo_root=repo_dir)

    assert CLAUDE_ADAPTER_REL_PATH in changed
    assert AGENTS_ADAPTER_REL_PATH in changed
    report = assess_repo_ready(mode="report", repo_root=repo_dir)
    adapter_states = {item.rel_path: item.state for item in report.adapters}
    assert adapter_states[CLAUDE_ADAPTER_REL_PATH] == "ready"
    assert adapter_states[AGENTS_ADAPTER_REL_PATH] == "ready"


def test_build_repo_readiness_report_includes_adapters(tmp_path: Path) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    assess_repo_ready(mode="fix", repo_root=repo_dir)
    sync_repo_local_adapters(
        repo_root=repo_dir,
        statuses={
            "codex": ProviderStatus("codex", False, "", False, "missing"),
            "claude": ProviderStatus("claude", False, "", False, "missing"),
        },
        allow_ai=False,
    )

    result = assess_repo_ready(mode="report", repo_root=repo_dir)
    payload = build_repo_readiness_report(result)

    assert payload["adapters"]
    adapter_states = {item["path"]: item["state"] for item in payload["adapters"]}
    assert adapter_states[CLAUDE_ADAPTER_REL_PATH] == ADAPTER_STATUS_PLACEHOLDER
    assert adapter_states[AGENTS_ADAPTER_REL_PATH] == ADAPTER_STATUS_PLACEHOLDER
    assert payload["missing_required_adapters"] == []
    assert payload["pending_required_adapters"] == []


def test_sync_repo_local_adapters_falls_back_to_placeholder_when_native_generation_fails(
    tmp_path: Path, monkeypatch
) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    assess_repo_ready(mode="fix", repo_root=repo_dir)

    def _fake_generate_text(*, provider, statuses, prompt, heartbeat=None):
        if provider == "claude":
            raise PlatformError(
                "Claude did not return text output.",
                code="E_PROVIDER_GENERATION_FAILED",
                reason="claude",
            )
        return """
# Agent Instructions

`.ghdp/*` is the canonical source of truth for this repository.

## Read Order

1. `.ghdp/frbr/intent.json` when present
2. `.ghdp/readiness.json` when present
3. `.ghdp/architecture.md`
4. `.ghdp/runbook.yaml`
5. `.ghdp/config.yaml`
6. `.ghdp/guardrails.yaml`
7. `.ghdp/lock.yaml`

## Working Rules

- Do not invent missing GHDP content.

## Validation

- Refer to `.ghdp/runbook.yaml` and `.ghdp/readiness.json`.

## Notes

- Ask the user when missing GHDP context blocks safe progress.
""".strip()

    monkeypatch.setattr("platform_cli.tools.repo_ready_adapters.generate_text", _fake_generate_text)

    progress = _FakeProgress()
    result = sync_repo_local_adapters(
        repo_root=repo_dir,
        statuses={
            "codex": ProviderStatus("codex", True, "codex", True, "ok"),
            "claude": ProviderStatus("claude", True, "claude", True, "ok"),
        },
        allow_ai=True,
        progress=progress,
    )

    assert any("CLAUDE.md: Claude did not return text output." in warning for warning in result.warnings)
    assert f"adapter_status: \"{ADAPTER_STATUS_PLACEHOLDER}\"" in (repo_dir / CLAUDE_ADAPTER_REL_PATH).read_text(
        encoding="utf-8"
    )
    assert f"adapter_status: \"{ADAPTER_STATUS_DRAFT}\"" in (repo_dir / AGENTS_ADAPTER_REL_PATH).read_text(
        encoding="utf-8"
    )
    assert (repo_dir / CLAUDE_SETTINGS_REL_PATH).exists()
    assert (repo_dir / CODEX_CONFIG_REL_PATH).exists()
    assert (repo_dir / MCP_CONFIG_REL_PATH).exists()
    assert ("failed", CLAUDE_ADAPTER_REL_PATH) in progress.events
    assert ("generated", AGENTS_ADAPTER_REL_PATH) in progress.events


def test_enabled_tool_adapters_block_compliance_until_ready(tmp_path: Path) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    assess_repo_ready(mode="fix", repo_root=repo_dir)
    _finalize_required_repo_files(repo_dir, enabled_tools=["codex"])

    report = assess_repo_ready(mode="report", repo_root=repo_dir)

    assert report.compliant is False
    assert report.missing_required_adapters == [AGENTS_ADAPTER_REL_PATH, CODEX_CONFIG_REL_PATH]
    assert report.pending_required_adapters == []


def test_accepting_required_adapter_drafts_restores_compliance(tmp_path: Path, monkeypatch) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    assess_repo_ready(mode="fix", repo_root=repo_dir)
    _finalize_required_repo_files(repo_dir, enabled_tools=["codex"])
    monkeypatch.setattr(
        "platform_cli.tools.repo_ready_adapters.generate_text",
        lambda **kwargs: """
# Agent Instructions

`.ghdp/*` is the canonical source of truth for this repository.

## Read Order

1. `.ghdp/frbr/intent.json` when present
2. `.ghdp/readiness.json` when present
3. `.ghdp/architecture.md`
4. `.ghdp/runbook.yaml`
5. `.ghdp/config.yaml`
6. `.ghdp/guardrails.yaml`
7. `.ghdp/lock.yaml`
""".strip(),
    )

    sync_repo_local_adapters(
        repo_root=repo_dir,
        statuses={
            "codex": ProviderStatus("codex", True, "codex", True, "ok"),
            "claude": ProviderStatus("claude", False, "", False, "missing"),
        },
        allow_ai=True,
    )

    before_accept = assess_repo_ready(mode="report", repo_root=repo_dir)
    assert before_accept.compliant is False
    assert before_accept.pending_required_adapters == [AGENTS_ADAPTER_REL_PATH]

    accept_repo_ready_reviews(repo_root=repo_dir)

    after_accept = assess_repo_ready(mode="report", repo_root=repo_dir)
    assert after_accept.compliant is True
    assert after_accept.missing_required_adapters == []
    assert after_accept.pending_required_adapters == []
