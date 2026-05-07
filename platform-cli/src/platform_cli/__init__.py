# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

from importlib import metadata as importlib_metadata

__app_name__ = "ghdp"
_DEFAULT_VERSION = "0.0.0"
_DEFAULT_CHANNEL = "beta"  # or "beta" / "stable"


def _resolve_runtime_version() -> str:
    # Build-time injected metadata has highest priority for release binaries.
    try:
        from ._build_meta import BUILD_VERSION  # type: ignore

        v = str(BUILD_VERSION or "").strip()
        if v:
            return v
    except Exception:
        pass

    # Installed package metadata keeps local installs aligned with pyproject version.
    try:
        v = str(importlib_metadata.version("ghdp") or "").strip()
        if v:
            return v
    except Exception:
        pass

    return _DEFAULT_VERSION


def _resolve_runtime_channel() -> str:
    try:
        from ._build_meta import BUILD_CHANNEL  # type: ignore

        c = str(BUILD_CHANNEL or "").strip().lower()
        if c in {"stable", "beta"}:
            return c
    except Exception:
        pass

    return _DEFAULT_CHANNEL


__version__ = _resolve_runtime_version()
__channel__ = _resolve_runtime_channel()
