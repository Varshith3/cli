# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
# src/platform_cli/core/telemetry.py
from __future__ import annotations

import json
import os
import platform
from datetime import datetime
from pathlib import Path
from typing import Optional

from .. import __version__
from .errors import PlatformError
from .config import get_bool

_HOME = Path.home()
_USAGE_LOG = _HOME / ".ghdp" / "usage.log"
_ERROR_LOG = _HOME / ".ghdp" / "errors.log"


def _telemetry_disabled() -> bool:
    """
    Decide whether telemetry should be disabled.

    Priority:
    1. GHDP_TELEMETRY env var (off/0/false disables).
    2. config key 'telemetry.enabled' (False disables).
    3. Default = enabled.
    """
    env = os.getenv("GHDP_TELEMETRY")
    if env is not None:
        return env.lower() in {"off", "0", "false"}

    try:
        enabled = get_bool("telemetry.enabled", default=True)
    except Exception:
        enabled = True

    return not enabled


def log_usage(
    command: str,
    service: Optional[str] = None,
    env: Optional[str] = None,
    status: str = "ok",
    error_code: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    """
    Append one JSON line per command usage.
    """
    if _telemetry_disabled():
        return

    try:
        _USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "command": command,
            "service": service,
            "env": env,
            "status": status,
            "error_code": error_code,
            "reason": reason,
            "user": os.getenv("USER") or os.getenv("USERNAME") or "unknown",
            "version": __version__,
            "platform": platform.platform(),
        }
        with _USAGE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        # Never break CLI for telemetry.
        pass


def log_error(err: PlatformError) -> None:
    """
    Structured error log (code + reason + message).
    """
    if _telemetry_disabled():
        return

    try:
        _ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "code": err.code,
            "reason": err.reason,
            "message": str(err),
        }
        with _ERROR_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def send_alert(err: PlatformError) -> None:
    """
    Stub for alerting (Slack/PagerDuty later).
    For now just a tiny hint so people see the hook exists.
    """
    from rich import print

    print("[dim]Alert hook stub → would send alert for this error.[/dim]")
