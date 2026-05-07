from __future__ import annotations

import sys

from platform_cli.exec.runner import PlatformError, run_cmd


def copy_text(value: str) -> tuple[bool, str]:
    """
    Best-effort clipboard copy without adding a new dependency.

    Returns:
        (ok, detail) where detail is either the tool used or the failure reason.
    """
    text = str(value or "")
    if not text:
        return False, "empty"

    candidates: list[list[str]]
    if sys.platform.startswith("win"):
        candidates = [["clip"]]
    elif sys.platform.startswith("darwin"):
        candidates = [["pbcopy"]]
    else:
        candidates = [["wl-copy"], ["xclip", "-selection", "clipboard"]]

    last_reason = "clipboard_unavailable"
    for cmd in candidates:
        try:
            run_cmd(cmd, check=True, capture=True, input_text=text)
            return True, cmd[0]
        except PlatformError as exc:
            if exc.code == "E_CMD_NOT_FOUND":
                last_reason = f"{cmd[0]}_missing"
                continue
            last_reason = exc.reason or exc.code or "clipboard_failed"
        except Exception:
            last_reason = f"{cmd[0]}_failed"

    return False, last_reason
