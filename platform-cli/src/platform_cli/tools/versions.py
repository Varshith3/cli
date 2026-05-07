# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from packaging.version import Version, InvalidVersion

_VER_RE = re.compile(r"(\d+(?:\.\d+){1,3})")  # captures 1.2 or 1.2.3 or 1.2.3.4


@dataclass(frozen=True)
class VersionCheck:
    raw: str
    parsed: Optional[str]
    ok: Optional[bool]          # None = no policy or unknown version
    op: Optional[str]
    required: Optional[str]


def extract_version(raw: str) -> Optional[str]:
    if not raw:
        return None
    m = _VER_RE.search(raw)
    return m.group(1) if m else None


def _parse(v: str) -> Optional[Version]:
    try:
        return Version(v)
    except (InvalidVersion, TypeError):
        return None


def check_version_req(raw_detected: str, req: Optional[Dict[str, Any]]) -> VersionCheck:
    """
    req format (toolset.json):
      {"op": ">=", "version": "2.0.0"}
      {"op": "==", "version": "1.107.1"}
    """
    parsed_str = extract_version(raw_detected)
    if not req:
        return VersionCheck(raw=raw_detected, parsed=parsed_str, ok=None, op=None, required=None)

    op = (req.get("op") or ">=").strip()
    required = (req.get("version") or "").strip()
    if not parsed_str or not required:
        return VersionCheck(raw=raw_detected, parsed=parsed_str, ok=None, op=op, required=required or None)

    dv = _parse(parsed_str)
    rv = _parse(required)
    if not dv or not rv:
        return VersionCheck(raw=raw_detected, parsed=parsed_str, ok=None, op=op, required=required)

    if op == "==":
        ok = (dv == rv)
    elif op == ">=":
        ok = (dv >= rv)
    elif op == "<=":
        ok = (dv <= rv)
    else:
        ok = None  # unsupported op for v0.0.1

    return VersionCheck(raw=raw_detected, parsed=parsed_str, ok=ok, op=op, required=required)
