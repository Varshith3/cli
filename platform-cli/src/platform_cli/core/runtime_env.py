# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
from __future__ import annotations

import os
from pathlib import Path

from platform_cli.core.secure_defaults import load_secure_defaults_into_env

DEFAULT_RUNTIME_ENV_DIR_NAME = ".ghdp"
DEFAULT_RUNTIME_ENV_FILE_NAME = "runtime.env"
REPO_RUNTIME_ENV_NAME = ".env"


def _find_repo_runtime_env(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    nearest_existing_env: Path | None = None
    for candidate in (current, *current.parents):
        env_path = candidate / REPO_RUNTIME_ENV_NAME
        if nearest_existing_env is None and env_path.exists():
            nearest_existing_env = env_path
        git_marker = candidate / ".git"
        if git_marker.exists():
            return nearest_existing_env or env_path
    return None


def _default_runtime_env_path() -> Path:
    return Path.home() / DEFAULT_RUNTIME_ENV_DIR_NAME / DEFAULT_RUNTIME_ENV_FILE_NAME


def runtime_env_path() -> Path:
    override = (os.getenv("GHDP_RUNTIME_ENV_PATH") or "").strip()
    if override:
        return Path(override).expanduser()
    repo_env = _find_repo_runtime_env()
    if repo_env is not None:
        return repo_env
    return _default_runtime_env_path()


def _load_env_file(path: Path, *, override_existing: bool = False) -> int:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0
    except Exception:
        return 0

    loaded = 0
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if (not line) or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        if (not override_existing) and key in os.environ:
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value
        loaded += 1

    return loaded


def load_runtime_env() -> int:
    """
    Load secure installed defaults first, then user runtime overrides.

    Explicit caller-provided env vars keep priority over both sources. User
    runtime overrides are still allowed to override values that came from secure
    installed defaults.
    """
    loaded = 0
    protected_keys = set(os.environ)
    loaded += load_secure_defaults_into_env()
    override_count = 0
    try:
        raw = runtime_env_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        raw = ""
    except Exception:
        raw = ""

    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if (not line) or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in protected_keys:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value
        override_count += 1

    loaded += override_count
    return loaded
