from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from platform_cli.core.errors import PlatformError
from platform_cli.tools import jira_context


def test_validate_jira_warns_when_acli_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*_args, **_kwargs):
        raise PlatformError("missing", code="E_CMD_NOT_FOUND", reason="acli")

    monkeypatch.setattr(jira_context, "run_cmd", _raise)
    result = jira_context.validate_jira_ticket("EPPE-1", mode="warn")
    assert result.found is False
    assert "skipping Jira validation" in result.warning


def test_validate_jira_enforce_fails_when_ticket_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        jira_context,
        "run_cmd",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="not found"),
    )

    with pytest.raises(PlatformError) as exc:
        jira_context.validate_jira_ticket("EPPE-1", mode="enforce")

    assert exc.value.code == "E_JIRA_TICKET_NOT_FOUND"


def test_fetch_jira_context_parses_summary_and_description(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "fields": {
            "summary": "Improve branch orchestration",
            "description": {
                "type": "doc",
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "Line one"}]},
                    {"type": "paragraph", "content": [{"type": "text", "text": "Line two"}]},
                ],
            },
        }
    }
    monkeypatch.setattr(
        jira_context,
        "run_cmd",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=""),
    )

    ctx = jira_context.fetch_jira_context("EPPE-1", mode="warn")
    assert ctx["summary"] == "Improve branch orchestration"
    assert "Line one" in ctx["description"]
    assert "Line two" in ctx["description"]
