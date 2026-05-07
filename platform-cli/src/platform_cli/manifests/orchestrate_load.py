# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from platform_cli.core.errors import PlatformError


def load_orchestrate_json_file(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PlatformError(
            f"Required orchestrate contract file not found: {path}",
            code="E_ORCHESTRATE_CONTRACT_MISSING",
            reason=str(path),
        )
    except json.JSONDecodeError as exc:
        raise PlatformError(
            f"Invalid JSON in orchestrate contract file {path}: {exc}",
            code="E_ORCHESTRATE_CONTRACT_INVALID",
            reason=str(path),
        )

    if not isinstance(payload, dict):
        raise PlatformError(
            f"Expected a JSON object in orchestrate contract file {path}",
            code="E_ORCHESTRATE_CONTRACT_INVALID",
            reason=str(path),
        )

    return payload
