from __future__ import annotations

import pytest

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.errors import PlatformError
from platform_cli.tools import branch_ai


def test_build_intent_prompt_includes_expected_fields() -> None:
    prompt = branch_ai.build_intent_prompt(
        jira_title="Improve branch orchestration",
        jira_description="Need a reusable stored intent artifact.",
        branch_name="feature/EPPE-6654-ENHANCEMENT-branch-orchestration",
        branch_type="ENHANCEMENT",
        branch_slug="branch-orchestration",
        ticket_key="EPPE-6654",
        repo="owner/repo",
        base_branch="main",
    )

    assert "Improve branch orchestration" in prompt
    assert "Need a reusable stored intent artifact." in prompt
    assert "feature/EPPE-6654-ENHANCEMENT-branch-orchestration" in prompt
    assert "ENHANCEMENT" in prompt
    assert "branch-orchestration" in prompt


def test_choose_provider_prefers_available_configured_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        branch_ai,
        "select_provider",
        lambda preferred, interactive, refresh_on_missing: (
            ("claude" if preferred == "claude" else "manual"),
            {},
        ),
    )
    assert branch_ai.choose_provider(preferred="claude", refresh_on_missing=True) == "claude"
    assert branch_ai.choose_provider(preferred="codex", refresh_on_missing=True) == "manual"


def test_choose_provider_prompts_when_both_available(monkeypatch: pytest.MonkeyPatch) -> None:
    cli_ctx.non_interactive = False
    monkeypatch.setattr(
        branch_ai,
        "select_provider",
        lambda preferred, interactive, refresh_on_missing: ("claude", {}),
    )
    assert branch_ai.choose_provider(preferred="auto", refresh_on_missing=True) == "claude"


def test_generate_intent_uses_provider_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(branch_ai, "detect_provider_statuses", lambda refresh: {"codex": object()})
    monkeypatch.setattr(branch_ai, "generate_text", lambda provider, statuses, prompt: "Ship the new behavior.")
    intent = branch_ai.generate_intent(
        provider="codex",
        jira_summary="Improve branch orchestration",
        jira_description="Need stored repo-local intent.",
        branch_name="feature/EPPE-1-ENHANCEMENT-branch-orchestration",
        branch_type="ENHANCEMENT",
        branch_slug="branch-orchestration",
        ticket_key="EPPE-1",
        repo="owner/repo",
        base_branch="main",
    )
    assert intent.intent == "Ship the new behavior."
    assert intent.provider == "codex"


def test_manual_intent_requires_interactive() -> None:
    cli_ctx.non_interactive = False
    with pytest.raises(PlatformError) as exc:
        branch_ai.manual_intent(jira_summary="Summary", jira_description="Description")
    assert exc.value.code == "E_BRANCH_INTENT_MANUAL_REQUIRED"
