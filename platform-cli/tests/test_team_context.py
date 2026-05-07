from __future__ import annotations

import pytest

from platform_cli.core.errors import PlatformError
from platform_cli.core.config import set_value
from platform_cli.core.team_context import get_selected_team, resolve_team


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _toolset() -> dict:
    return {
        "schema_version": "0.0.1",
        "teams": {
            "default": {"tools": {"git": {"op": ">=", "version": "2.0.0"}}},
            "platform": {"tools": {"gh": {"op": ">=", "version": "2.0.0"}}},
        },
    }


def test_resolve_team_uses_explicit_cli_value(isolated_home):
    res = resolve_team(_toolset(), "platform", non_interactive=False)
    assert res.team == "platform"
    assert res.source == "cli"


def test_resolve_team_rejects_unknown_cli_value(isolated_home):
    with pytest.raises(PlatformError) as exc:
        resolve_team(_toolset(), "nope", non_interactive=False)
    assert exc.value.code == "E_TEAM_UNKNOWN"


def test_resolve_team_prompts_and_saves_when_missing(isolated_home, monkeypatch):
    monkeypatch.setattr("platform_cli.core.team_context.typer.prompt", lambda *_args, **_kwargs: "2")
    res = resolve_team(_toolset(), None, non_interactive=False)
    assert res.team == "platform"
    assert res.source == "prompt"
    assert res.persisted is True
    assert get_selected_team() == "platform"


def test_resolve_team_non_interactive_requires_selection(isolated_home):
    with pytest.raises(PlatformError) as exc:
        resolve_team(_toolset(), None, non_interactive=True)
    assert exc.value.code == "E_TEAM_REQUIRED_NON_INTERACTIVE"


def test_resolve_team_non_interactive_rejects_stale_saved_team(isolated_home):
    set_value("team.selected", "legacy")

    with pytest.raises(PlatformError) as exc:
        resolve_team(_toolset(), None, non_interactive=True)

    assert exc.value.code == "E_TEAM_INVALID_AFTER_SYNC"
    assert "reselect" in exc.value.message.lower()


def test_resolve_team_interactive_prompts_after_stale_saved_team(isolated_home, monkeypatch):
    set_value("team.selected", "legacy")
    monkeypatch.setattr("platform_cli.core.team_context.typer.prompt", lambda *_args, **_kwargs: "2")

    res = resolve_team(_toolset(), None, non_interactive=False)

    assert res.team == "platform"
    assert res.source == "prompt"
    assert res.persisted is True
    assert "no longer available" in res.notice
    assert get_selected_team() == "platform"


def test_resolve_team_explicit_choice_replaces_stale_saved_team(isolated_home):
    set_value("team.selected", "legacy")

    res = resolve_team(_toolset(), "platform", non_interactive=True)

    assert res.team == "platform"
    assert res.source == "cli"
    assert res.persisted is True
    assert get_selected_team() == "platform"
