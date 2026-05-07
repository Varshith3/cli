from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from platform_cli.core.errors import PlatformError


def load_release_parity_fixture(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PlatformError(
            f"Release parity fixture not found: {path}",
            code="E_RELEASE_PARITY_FIXTURE_MISSING",
            reason=str(path),
        ) from exc
    except json.JSONDecodeError as exc:
        raise PlatformError(
            f"Invalid JSON in release parity fixture {path}: {exc}",
            code="E_RELEASE_PARITY_FIXTURE_INVALID",
            reason=str(path),
        ) from exc


def normalize_release_semantics(
    payload: Mapping[str, Any],
    *,
    include_fields: list[str],
    ignore_fields: list[str] | None = None,
) -> dict[str, Any]:
    ignored = {item for item in (ignore_fields or []) if item}
    normalized: dict[str, Any] = {}
    for field in include_fields:
        if field in ignored:
            continue
        normalized[field] = _normalize_value(payload.get(field))
    return normalized


def compare_release_semantics(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    include_fields: list[str],
    ignore_fields: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    left_norm = normalize_release_semantics(left, include_fields=include_fields, ignore_fields=ignore_fields)
    right_norm = normalize_release_semantics(right, include_fields=include_fields, ignore_fields=ignore_fields)
    diff: dict[str, dict[str, Any]] = {}
    for key in include_fields:
        if key in (ignore_fields or []):
            continue
        if left_norm.get(key) != right_norm.get(key):
            diff[key] = {"left": left_norm.get(key), "right": right_norm.get(key)}
    return diff


def _normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return {key: _normalize_value(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    return value
