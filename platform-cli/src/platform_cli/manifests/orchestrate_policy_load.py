# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Tuple

from platform_cli.core.errors import PlatformError


_DEFAULT_ORCHESTRATE_POLICY_NAME = "orchestrate_policy.json"


def load_orchestrate_policy() -> Tuple[Dict[str, Any], str]:
    home = Path.home()
    candidates = [
        home / ".ghdp" / "policy" / _DEFAULT_ORCHESTRATE_POLICY_NAME,
        home / ".ghdp" / _DEFAULT_ORCHESTRATE_POLICY_NAME,
    ]
    for candidate in candidates:
        if candidate.exists():
            return _read_json(candidate), f"user:{candidate}"

    env_path = str(os.environ.get("GHDP_ORCHESTRATE_POLICY_PATH", "") or "").strip()
    if env_path:
        candidate = Path(env_path).expanduser()
        if candidate.exists():
            return _read_json(candidate), f"env:GHDP_ORCHESTRATE_POLICY_PATH:{candidate}"

    resource_path = Path(__file__).resolve().parents[1] / "resources" / "policy" / _DEFAULT_ORCHESTRATE_POLICY_NAME
    if resource_path.exists():
        return _read_json(resource_path), f"packaged:{resource_path}"

    raise PlatformError(
        "Orchestrate policy could not be resolved from ~/.ghdp, GHDP_ORCHESTRATE_POLICY_PATH, or packaged resources.",
        code="E_ORCHESTRATE_POLICY_MISSING",
        reason=_DEFAULT_ORCHESTRATE_POLICY_NAME,
    )


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PlatformError(
            f"Orchestrate policy file not found: {path}",
            code="E_ORCHESTRATE_POLICY_MISSING",
            reason=str(path),
        )
    except json.JSONDecodeError as exc:
        raise PlatformError(
            f"Invalid JSON in orchestrate policy {path}: {exc}",
            code="E_ORCHESTRATE_POLICY_INVALID",
            reason=str(path),
        )
    if not isinstance(payload, dict):
        raise PlatformError(
            f"Expected a JSON object in orchestrate policy {path}",
            code="E_ORCHESTRATE_POLICY_INVALID",
            reason=str(path),
        )
    return payload
