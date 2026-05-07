# NOTE: Architectural rules in ARCHITECTURE.md -- do not refactor cross-layer.

from __future__ import annotations
from platform_cli.core.rich_ui import log_step, rich_progress

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Optional

# Using the SAME PlatformError class the CLI catches
try:
    from platform_cli.core.errors import PlatformError  # primary
except Exception:  # pragma: no cover
    class PlatformError(RuntimeError):
        def __init__(self, message: str, code: str = "E_INTERNAL", reason: str = "UNKNOWN", alert: bool = False):
            super().__init__(message)
            self.code = code
            self.reason = reason
            self.alert = alert


@dataclass
class CmdResult:
    cmd: List[str]
    returncode: int
    stdout: str
    stderr: str


def run_cmd(
    cmd: List[str],
    *,
    check: bool = True,
    capture: bool = True,
    text: bool = True,
    encoding: Optional[str] = None,
    errors: Optional[str] = None,
    timeout_s: Optional[int] = None,
    env: Optional[Mapping[str, str]] = None,
    cwd: Optional[str | Path] = None,
    rich_logs: bool = False,
    input_text: Optional[str] = None,
) -> CmdResult:
    if rich_logs:
        log_step(f"Running: {' '.join(cmd)}", "pending")
    use_spinner = sys.stdout.isatty()
    if rich_logs and use_spinner:
        rich_progress.start(f"{' '.join(cmd)}")
    decode_errors = errors if errors is not None else "replace"
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=capture,
            text=text,
            encoding=encoding if text else None,
            errors=decode_errors if text else None,
            timeout=timeout_s,
            env=dict(env) if env is not None else None,
            cwd=str(cwd) if cwd is not None else None,
            input=input_text,
        )
    except FileNotFoundError:
        if rich_logs and use_spinner:
            rich_progress.stop(f"Command not found: {cmd[0]}")
        if rich_logs:
            log_step(f"Command not found: {cmd[0]}", "error")
        raise PlatformError(
            f"Command not found: {cmd[0]}",
            code="E_CMD_NOT_FOUND",
            reason=cmd[0],
        )
    except subprocess.TimeoutExpired:
        if rich_logs and use_spinner:
            rich_progress.stop(f"Command timed out: {' '.join(cmd)}")
        if rich_logs:
            log_step(f"Command timed out: {' '.join(cmd)}", "error")
        raise PlatformError(
            f"Command timed out: {' '.join(cmd)}",
            code="E_CMD_TIMEOUT",
            reason="timeout",
        )
    finally:
        if rich_logs and use_spinner:
            rich_progress.stop(f"{' '.join(cmd)} done")

    res = CmdResult(
        cmd=cmd,
        returncode=proc.returncode,
        stdout=(proc.stdout or "").strip(),
        stderr=(proc.stderr or "").strip(),
    )

    if check and res.returncode != 0:
        if rich_logs:
            log_step(f"Command failed ({res.returncode}): {' '.join(cmd)}", "error")
        raise PlatformError(
            f"Command failed ({res.returncode}): {' '.join(cmd)}\n{res.stderr}",
            code="E_CMD_FAILED",
            reason="nonzero_exit",
        )

    if rich_logs:
        log_step(f"Finished: {' '.join(cmd)}", "ok")
    return res
