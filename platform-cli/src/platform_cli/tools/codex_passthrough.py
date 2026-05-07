# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

from typing import List, Optional

from platform_cli.exec.runner import run_cmd


def run_codex_passthrough(args: Optional[List[str]] = None) -> int:
    """
    Execute codex CLI with passthrough arguments.
    Returns codex process exit code.
    """
    forwarded = list(args or []) or ""

    res = run_cmd(["codex", *forwarded], check=False, capture=False)
    return int(res.returncode or 0)
