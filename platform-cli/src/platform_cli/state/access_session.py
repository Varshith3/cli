from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from platform_cli.core.errors import PlatformError
from platform_cli.state.store import FileLock, StatePaths, default_state_paths, load_state, save_state

ACCESS_SCHEMA_VERSION = "1.0"
ACCESS_STATE_KEY = "access"
ACCESS_EVENTS_FILENAME = "access-events.jsonl"

DEFAULT_ACCESS_SESSION: Dict[str, Any] = {
    "schema_version": ACCESS_SCHEMA_VERSION,
    "remembered_actor": "",
    "active_token": "",
    "assumed_team": "",
}

__all__ = [
    "ACCESS_EVENTS_FILENAME",
    "ACCESS_SCHEMA_VERSION",
    "DEFAULT_ACCESS_SESSION",
    "append_access_event",
    "clear_access_session",
    "clear_active_token",
    "clear_remembered_actor",
    "clear_assumed_team",
    "get_access_session",
    "get_active_token",
    "get_assumed_team",
    "get_remembered_actor",
    "load_access_events",
    "set_active_token",
    "set_assumed_team",
    "set_remembered_actor",
]


def _state_paths(paths: Optional[StatePaths] = None) -> StatePaths:
    return paths or default_state_paths()


def _events_path(paths: Optional[StatePaths] = None) -> Path:
    return _state_paths(paths).state_dir / ACCESS_EVENTS_FILENAME


def _now_ts() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_session(raw: Any) -> Dict[str, Any]:
    session = dict(DEFAULT_ACCESS_SESSION)
    if isinstance(raw, dict):
        session.update(raw)
    session["schema_version"] = str(session.get("schema_version") or ACCESS_SCHEMA_VERSION).strip() or ACCESS_SCHEMA_VERSION
    session["remembered_actor"] = _normalize_text(session.get("remembered_actor", ""))
    session["active_token"] = _normalize_text(session.get("active_token", ""))
    session["assumed_team"] = _normalize_text(session.get("assumed_team", ""))
    return session


def _load_state(paths: Optional[StatePaths] = None) -> Dict[str, Any]:
    state = load_state(_state_paths(paths))
    if not isinstance(state, dict):
        raise PlatformError(
            "Access session state must be a JSON object.",
            code="E_ACCESS_SESSION_INVALID",
            reason="state_root",
        )
    return state


def _save_state(state: Dict[str, Any], paths: Optional[StatePaths] = None) -> None:
    save_state(state, _state_paths(paths))


def _update_access_session(paths: Optional[StatePaths], mutate) -> Dict[str, Any]:
    resolved_paths = _state_paths(paths)
    with FileLock(resolved_paths.lock_file):
        state = _load_state(resolved_paths)
        current = _normalize_session(state.get(ACCESS_STATE_KEY))
        updated = mutate(dict(current))
        session = _normalize_session(updated if isinstance(updated, dict) else current)
        state[ACCESS_STATE_KEY] = session
        _save_state(state, resolved_paths)
        return session


def get_access_session(paths: Optional[StatePaths] = None) -> Dict[str, Any]:
    state = _load_state(paths)
    return _normalize_session(state.get(ACCESS_STATE_KEY))


def set_remembered_actor(login: str, paths: Optional[StatePaths] = None) -> None:
    def mutate(session: Dict[str, Any]) -> Dict[str, Any]:
        session["remembered_actor"] = _normalize_text(login)
        return session

    _update_access_session(paths, mutate)


def get_remembered_actor(paths: Optional[StatePaths] = None) -> str:
    return _normalize_text(get_access_session(paths).get("remembered_actor", ""))


def clear_remembered_actor(paths: Optional[StatePaths] = None) -> None:
    set_remembered_actor("", paths)


def set_active_token(token: str, paths: Optional[StatePaths] = None) -> None:
    def mutate(session: Dict[str, Any]) -> Dict[str, Any]:
        session["active_token"] = _normalize_text(token)
        return session

    _update_access_session(paths, mutate)


def get_active_token(paths: Optional[StatePaths] = None) -> str:
    return _normalize_text(get_access_session(paths).get("active_token", ""))


def clear_active_token(paths: Optional[StatePaths] = None) -> None:
    set_active_token("", paths)


def set_assumed_team(team: str, paths: Optional[StatePaths] = None) -> None:
    def mutate(session: Dict[str, Any]) -> Dict[str, Any]:
        session["assumed_team"] = _normalize_text(team)
        return session

    _update_access_session(paths, mutate)


def get_assumed_team(paths: Optional[StatePaths] = None) -> str:
    return _normalize_text(get_access_session(paths).get("assumed_team", ""))


def clear_assumed_team(paths: Optional[StatePaths] = None) -> None:
    set_assumed_team("", paths)


def clear_access_session(paths: Optional[StatePaths] = None) -> None:
    def mutate(session: Dict[str, Any]) -> Dict[str, Any]:
        session["remembered_actor"] = ""
        session["active_token"] = ""
        session["assumed_team"] = ""
        return session

    _update_access_session(paths, mutate)


def append_access_event(event_type: str, details: Optional[Any] = None, paths: Optional[StatePaths] = None) -> Dict[str, Any]:
    event = {
        "ts": _now_ts(),
        "event_type": _normalize_text(event_type),
        "details": {} if details is None else details,
    }
    if not event["event_type"]:
        raise PlatformError(
            "Access event type is required.",
            code="E_ACCESS_EVENT_INVALID",
            reason="event_type",
        )

    resolved_paths = _state_paths(paths)
    resolved_paths.state_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(resolved_paths.lock_file):
        events_path = _events_path(resolved_paths)
        with events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    return event


def load_access_events(limit: Optional[int] = None, paths: Optional[StatePaths] = None) -> List[Dict[str, Any]]:
    events_path = _events_path(paths)
    if not events_path.exists():
        return []

    try:
        raw_lines = events_path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        raise PlatformError(
            f"Failed to read access events: {exc}",
            code="E_ACCESS_EVENT_READ_FAILED",
            reason=str(events_path),
        )

    events: List[Dict[str, Any]] = []
    for raw in raw_lines:
        line = raw.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception as exc:
            raise PlatformError(
                f"Failed to parse access event: {exc}",
                code="E_ACCESS_EVENT_INVALID",
                reason=str(events_path),
            )
        if isinstance(item, dict):
            events.append(item)
        else:
            raise PlatformError(
                "Access event entries must be JSON objects.",
                code="E_ACCESS_EVENT_INVALID",
                reason=str(events_path),
            )

    if limit is None:
        return events

    if limit <= 0:
        return []
    return events[-limit:]
