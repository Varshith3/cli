from __future__ import annotations

import json
from pathlib import Path

from platform_cli.state.access_session import (
    ACCESS_EVENTS_FILENAME,
    append_access_event,
    clear_active_token,
    clear_assumed_team,
    get_access_session,
    get_active_token,
    get_assumed_team,
    get_remembered_actor,
    load_access_events,
    set_active_token,
    set_assumed_team,
    set_remembered_actor,
)


def _set_home(monkeypatch, home: Path) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


def test_access_session_reads_and_writes_nested_state(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)

    session = get_access_session()

    assert session["schema_version"] == "1.0"
    assert session["remembered_actor"] == ""
    assert session["active_token"] == ""
    assert session["assumed_team"] == ""

    set_remembered_actor("gh-mshyam")
    set_active_token("token-123")
    set_assumed_team("inform")

    updated = get_access_session()
    assert updated["remembered_actor"] == "gh-mshyam"
    assert updated["active_token"] == "token-123"
    assert updated["assumed_team"] == "inform"
    assert get_remembered_actor() == "gh-mshyam"
    assert get_active_token() == "token-123"
    assert get_assumed_team() == "inform"

    state_path = tmp_path / ".ghdp" / "state" / "state.json"
    raw_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert raw_state["access"]["remembered_actor"] == "gh-mshyam"
    assert raw_state["access"]["active_token"] == "token-123"
    assert raw_state["access"]["assumed_team"] == "inform"
    assert raw_state["tools"] == {}


def test_clearing_token_and_assumed_team_preserves_remembered_actor(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)

    set_remembered_actor("gh-mshyam")
    set_active_token("token-123")
    set_assumed_team("inform")

    clear_active_token()
    clear_assumed_team()

    session = get_access_session()
    assert session["remembered_actor"] == "gh-mshyam"
    assert session["active_token"] == ""
    assert session["assumed_team"] == ""


def test_remembered_actor_precedence_over_other_updates(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)

    set_remembered_actor("gh-mshyam")
    set_active_token("token-123")
    set_assumed_team("inform")
    set_remembered_actor("gh-mshyam-2")

    assert get_remembered_actor() == "gh-mshyam-2"
    assert get_active_token() == "token-123"
    assert get_assumed_team() == "inform"


def test_access_events_append_and_load_with_limit(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)

    assert load_access_events() == []

    first = append_access_event("token_issued", {"actor": "gh-mshyam"})
    second = append_access_event("token_activated")

    events = load_access_events()
    assert len(events) == 2
    assert events[0]["event_type"] == "token_issued"
    assert events[0]["details"] == {"actor": "gh-mshyam"}
    assert events[1]["event_type"] == "token_activated"
    assert events[1]["details"] == {}
    assert events[0]["ts"] == first["ts"]
    assert events[1]["ts"] == second["ts"]

    limited = load_access_events(limit=1)
    assert len(limited) == 1
    assert limited[0]["event_type"] == "token_activated"

    events_path = tmp_path / ".ghdp" / "state" / ACCESS_EVENTS_FILENAME
    raw_lines = events_path.read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == 2
