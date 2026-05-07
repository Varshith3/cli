# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

# ✅ Use your standard PlatformError
try:
    from platform_cli.core.errors import PlatformError  # type: ignore
except Exception:  # pragma: no cover
    try:
        from platform_cli.core.errors import PlatformError  # type: ignore
    except Exception:  # pragma: no cover
        class PlatformError(RuntimeError):
            def __init__(self, message: str, code: str = "E_INTERNAL", reason: str = "UNKNOWN", alert: bool = False):
                super().__init__(message)
                self.code = code
                self.reason = reason
                self.alert = alert


@dataclass(frozen=True)
class StatePaths:
    root: Path
    state_dir: Path
    state_file: Path
    lock_file: Path


def default_state_paths() -> StatePaths:
    root = Path.home() / ".ghdp"
    state_dir = root / "state"
    return StatePaths(
        root=root,
        state_dir=state_dir,
        state_file=state_dir / "state.json",
        lock_file=state_dir / ".state.lock",
    )


class FileLock:
    """
    Minimal cross-platform lock using an exclusive lock file.
    Good enough for v0.0.1 local usage.
    """

    def __init__(self, lock_path: Path, timeout_s: float = 8.0, poll_s: float = 0.1):
        self.lock_path = lock_path
        self.timeout_s = timeout_s
        self.poll_s = poll_s
        self._fd: Optional[int] = None

    def __enter__(self) -> "FileLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        start = time.time()

        while True:
            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(fd, str(os.getpid()).encode("utf-8"))
                self._fd = fd
                return self
            except FileExistsError:
                if (time.time() - start) > self.timeout_s:
                    raise PlatformError(
                        f"Timed out waiting for lock: {self.lock_path}",
                        code="E_LOCK_TIMEOUT",
                        reason=str(self.lock_path),
                    )
                time.sleep(self.poll_s)

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._fd is not None:
                os.close(self._fd)
            if self.lock_path.exists():
                self.lock_path.unlink()
        finally:
            self._fd = None


def _now_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def load_state(paths: Optional[StatePaths] = None) -> Dict[str, Any]:
    paths = paths or default_state_paths()
    paths.state_dir.mkdir(parents=True, exist_ok=True)

    if not paths.state_file.exists():
        return {"schema_version": "1.0", "updated_at": _now_ts(), "tools": {}}

    try:
        raw = paths.state_file.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("state root must be object")
        data.setdefault("schema_version", "1.0")
        data.setdefault("tools", {})
        return data
    except Exception as e:
        raise PlatformError(
            f"Failed to read state: {e}",
            code="E_STATE_READ_FAILED",
            reason=str(paths.state_file),
        )


def save_state(state: Dict[str, Any], paths: Optional[StatePaths] = None) -> None:
    paths = paths or default_state_paths()
    paths.state_dir.mkdir(parents=True, exist_ok=True)

    state["updated_at"] = _now_ts()

    tmp = paths.state_file.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(paths.state_file)
    except Exception as e:
        raise PlatformError(
            f"Failed to write state: {e}",
            code="E_STATE_WRITE_FAILED",
            reason=str(paths.state_file),
        )
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass


def update_tool_state(
    tool_name: str,
    patch: Dict[str, Any],
    *,
    paths: Optional[StatePaths] = None,
) -> Dict[str, Any]:
    """
    Atomically update state.tools[tool_name] with patch and return updated state.
    """
    paths = paths or default_state_paths()
    with FileLock(paths.lock_file):
        state = load_state(paths)
        tools = state.setdefault("tools", {})
        tool_obj = tools.setdefault(tool_name, {})
        tool_obj.update(patch)
        tool_obj.setdefault("name", tool_name)
        tool_obj.setdefault("first_seen_at", _now_ts())
        tool_obj["last_updated_at"] = _now_ts()
        save_state(state, paths)
        return state


def get_tool_state(tool_name: str, *, paths: Optional[StatePaths] = None) -> Dict[str, Any]:
    """
    Read-only helper for state.tools[tool_name]. Returns {} if not found.
    """
    state = load_state(paths)
    tools = state.get("tools", {}) or {}
    obj = tools.get(tool_name, {}) or {}
    return obj if isinstance(obj, dict) else {}
