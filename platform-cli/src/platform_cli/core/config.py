# NOTE: Architectural rules in ARCHITECTURE.md â€” do not refactor cross-layer.
# src/platform_cli/core/config.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from platform_cli.manifests.load import load_config_defaults
from platform_cli.manifests.validate import validate_config_defaults


def _config_dir() -> Path:
    return Path.home() / ".ghdp"


def _config_path() -> Path:
    return _config_dir() / "config.json"

# Minimal hard fallback if resource defaults are missing/corrupt.
_FALLBACK_DEFAULT_CONFIG: Dict[str, Any] = {
    "telemetry.enabled": True,
    "updates.enabled": True,
    "git.strict_clean": True,
    "precommit.mode": "off",
    "features.terraform_local": True,
    "confirm.dangerous": True,
    "aws.active_profile": "",
    "team.selected": "",
    "claude.athena_workgroup": "",
    "jenkins.okta_email": "",
    "repo.ai.provider": "auto",
    "repo.ai.refresh_on_missing": True,
    "branch.create.prompt_for_missing": True,
    "branch.create.jira_check_mode": "warn",
    "branch.ai.provider": "auto",
    "branch.ai.refresh_on_missing": True,
    "branch.intent.enabled": True,
    "branch.intent.include_description": True,
    "branch.intent.prompt_if_no_ai": True,
    "branch.intent.repo_path": ".ghdp/frbr/intent.json",
}


def _load_manifest_defaults() -> Dict[str, Any]:
    """
    Load + validate config defaults from resources/manifests/config-defaults.json
    through the manifests layer. Falls back to minimal in-code defaults.
    """
    try:
        cfg, _ = load_config_defaults()
        validate_config_defaults(cfg)
        defaults = cfg.get("defaults", {})
        if isinstance(defaults, dict):
            return dict(defaults)
    except Exception:
        pass

    return _FALLBACK_DEFAULT_CONFIG.copy()


def _ensure_dir() -> None:
    _config_dir().mkdir(parents=True, exist_ok=True)


def _load_raw() -> Dict[str, Any]:
    """
    Load config from disk and overlay on defaults.

    If file is missing or broken, fall back to defaults.
    """
    defaults = _load_manifest_defaults()

    config_path = _config_path()
    if not config_path.exists():
        return defaults

    try:
        with config_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return defaults

    if not isinstance(data, dict):
        return defaults

    merged = defaults
    merged.update(data)
    return merged


def _save_raw(data: Dict[str, Any]) -> None:
    _ensure_dir()
    with _config_path().open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def get_value(key: str, default: Any = None) -> Any:
    """
    Fetch a config value. Keys are flat strings like
    'telemetry.enabled', 'git.strict_clean', or 'features.terraform_local'.
    """
    data = _load_raw()
    return data.get(key, default)


def set_value(key: str, value: Any) -> None:
    """
    Update a config value and write to disk.
    """
    data = _load_raw()
    data[key] = value
    _save_raw(data)


def set_value_guarded(key: str, value: Any) -> None:
    """
    Update a governed config value after enforcing phase-0 access policy.
    """
    from platform_cli.core.access import enforce_config_write

    enforce_config_write(key, value)
    set_value(key, value)

def delete_value(key: str) -> None:
    """
    Remove a config value if present and write the updated config to disk.
    """
    data = _load_raw()
    if key in data:
        del data[key]
        _save_raw(data)


def get_bool(key: str, default: bool = False) -> bool:
    """
    Convenience boolean getter with forgiving coercion.
    """
    val = get_value(key, default)

    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        low = val.lower()
        if low in {"true", "1", "yes", "on"}:
            return True
        if low in {"false", "0", "no", "off"}:
            return False
    if isinstance(val, (int, float)):
        return bool(val)

    return default


def get_config_snapshot() -> Dict[str, Any]:
    """
    Merged view (defaults + overrides) used by `ghdp config show`.
    """
    return _load_raw()
