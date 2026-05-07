# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

from pathlib import PurePosixPath
from typing import List

import importlib.resources as pkg_resources

from platform_cli.core.errors import PlatformError


def _normalize_rel_path(rel_path: str) -> PurePosixPath:
    p = PurePosixPath(rel_path.strip().replace("\\", "/"))
    if str(p) in ("", ".") or p.is_absolute() or ".." in p.parts:
        raise PlatformError(
            f"Invalid packaged resource path: {rel_path}",
            code="E_PACKAGED_RESOURCE_INVALID_PATH",
            reason="packaged",
        )
    return p


def _resource_root():
    try:
        return pkg_resources.files("platform_cli.resources")
    except ModuleNotFoundError as e:
        raise PlatformError(
            f"Packaged resources package missing: {e}",
            code="E_PACKAGED_RESOURCE_NOT_FOUND",
            reason="platform_cli.resources",
        )


def read_packaged_text(rel_path: str, *, encoding: str = "utf-8") -> str:
    p = _normalize_rel_path(rel_path)
    try:
        return (_resource_root() / str(p)).read_text(encoding=encoding)
    except FileNotFoundError:
        raise PlatformError(
            f"Packaged resource not found: {p}",
            code="E_PACKAGED_RESOURCE_NOT_FOUND",
            reason=str(p),
        )
    except PlatformError:
        raise
    except Exception as e:
        raise PlatformError(
            f"Failed to read packaged resource {p}: {e}",
            code="E_PACKAGED_RESOURCE_READ_FAILED",
            reason=str(p),
        )


def read_packaged_bytes(rel_path: str) -> bytes:
    p = _normalize_rel_path(rel_path)
    try:
        return (_resource_root() / str(p)).read_bytes()
    except FileNotFoundError:
        raise PlatformError(
            f"Packaged resource not found: {p}",
            code="E_PACKAGED_RESOURCE_NOT_FOUND",
            reason=str(p),
        )
    except PlatformError:
        raise
    except Exception as e:
        raise PlatformError(
            f"Failed to read packaged resource {p}: {e}",
            code="E_PACKAGED_RESOURCE_READ_FAILED",
            reason=str(p),
        )


def list_packaged_files(rel_dir: str) -> List[str]:
    base = _normalize_rel_path(rel_dir)
    root = _resource_root() / str(base)
    try:
        entries = list(root.iterdir())
    except FileNotFoundError:
        raise PlatformError(
            f"Packaged resource directory not found: {base}",
            code="E_PACKAGED_RESOURCE_NOT_FOUND",
            reason=str(base),
        )
    except Exception as e:
        raise PlatformError(
            f"Failed to list packaged resource directory {base}: {e}",
            code="E_PACKAGED_RESOURCE_READ_FAILED",
            reason=str(base),
        )

    out: List[str] = []

    def _walk(node, rel_prefix: PurePosixPath) -> None:
        for child in node.iterdir():
            child_rel = rel_prefix / child.name
            if child.is_dir():
                _walk(child, child_rel)
            elif child.is_file():
                out.append(str(child_rel))

    for entry in entries:
        if entry.is_dir():
            _walk(entry, base / entry.name)
        elif entry.is_file():
            out.append(str(base / entry.name))

    out.sort()
    return out
