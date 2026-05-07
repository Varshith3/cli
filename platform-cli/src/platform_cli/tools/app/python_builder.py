"""Python application building using uv."""
# NOTE: Architectural rules in ARCHITECTURE.md – do not refactor cross-layer.

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, Dict

try:
    from platform_cli.core.errors import PlatformError  # type: ignore
except Exception:  # pragma: no cover

    class PlatformError(RuntimeError):
        def __init__(
            self,
            message: str,
            code: str = "E_INTERNAL",
            reason: str = "UNKNOWN",
            alert: bool = False,
        ):
            super().__init__(message)
            self.code = code
            self.reason = reason
            self.alert = alert


from platform_cli.exec.runner import run_cmd


def _uses_dynamic_versioning(pyproject_path: Path) -> bool:
    """Check if pyproject.toml uses dynamic = ["version"] with hatch version path."""
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    return "version" in data.get("project", {}).get("dynamic", [])


def _get_hatch_version_path(pyproject_path: Path) -> str:
    """Read [tool.hatch.version] path from pyproject.toml."""
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    return data.get("tool", {}).get("hatch", {}).get("version", {}).get("path", "_version.py")


def _inject_version(pyproject_path: Path, version: str) -> None:
    """
    Inject version into the build system.

    Supports two modes:
    - Dynamic versioning: writes _version.py at the path specified in [tool.hatch.version]
    - Static versioning: updates version = "..." in pyproject.toml directly
    """
    if _uses_dynamic_versioning(pyproject_path):
        # Write _version.py for hatchling to read
        version_file = pyproject_path.parent / _get_hatch_version_path(pyproject_path)
        version_file.parent.mkdir(parents=True, exist_ok=True)
        version_file.write_text(f'__version__ = "{version}"\n')
    else:
        # Update static version in pyproject.toml
        content = pyproject_path.read_text()
        content = re.sub(
            r'^version\s*=\s*["\'][^"\']+["\']',
            f'version = "{version}"',
            content,
            count=1,
            flags=re.MULTILINE,
        )
        pyproject_path.write_text(content)


def build_python_app(
    app: Any,  # AppConfig
    context: Dict[str, Any],
    repo_root: Path,
) -> str:
    """
    Build Python app using uv (sync dependencies + build wheel/sdist).

    Expects a pyproject.toml in apps/<app.path>/.
    Version is derived from git tags (next minor from latest release tag).

    Args:
        app: AppConfig instance (type=python)
        context: Build context
        repo_root: Root path of data-product repo

    Returns:
        Path to built artifact (dist/ directory)
    """
    app_dir = repo_root / "apps" / app.path

    # Validate pyproject.toml exists
    pyproject_path = app_dir / "pyproject.toml"
    if not pyproject_path.exists():
        raise PlatformError(
            f"pyproject.toml not found in {app_dir}",
            code="E_PYPROJECT_NOT_FOUND",
            reason=str(app_dir),
        )

    # Resolve version from git tags (fully automatic, no developer input)
    from platform_cli.tools.app.version_manager import resolve_version
    dynamic_version, mode = resolve_version(repo_root, "python")

    # Inject _version.py for this app and any sibling editable deps it references.
    # Only touch apps that this app actually depends on (via path deps in pyproject.toml),
    # not all siblings — avoids unintended side effects during single-app builds.
    _inject_version(pyproject_path, dynamic_version)
    apps_dir = repo_root / "apps"
    with open(pyproject_path, "rb") as f:
        pyproject_data = tomllib.load(f)
    # Check [tool.uv.sources] for path-based sibling deps
    uv_sources = pyproject_data.get("tool", {}).get("uv", {}).get("sources", {})
    for _dep_name, source_cfg in uv_sources.items():
        if isinstance(source_cfg, dict) and "path" in source_cfg:
            dep_pyproject = (app_dir / source_cfg["path"] / "pyproject.toml").resolve()
            if dep_pyproject.exists() and _uses_dynamic_versioning(dep_pyproject):
                _inject_version(dep_pyproject, dynamic_version)

    print(f"  Version: {dynamic_version} (mode: {mode})")

    # Clean dist/ directory to avoid conflicts with old wheels
    dist_dir = app_dir / "dist"
    if dist_dir.exists():
        for file in dist_dir.glob("*"):
            file.unlink()

    # Check uv is available
    uv_check = run_cmd(["uv", "--version"], check=False, capture=True)
    if uv_check.returncode != 0:
        raise PlatformError(
            "uv not found. Install via: ghdp tools install",
            code="E_UV_NOT_FOUND",
            reason="uv_missing",
        )

    # Sync dependencies (creates/updates .venv)
    print(f"  Syncing dependencies for {app.path}...")
    sync_result = run_cmd(
        ["uv", "sync"],
        cwd=str(app_dir),
        check=False,
    )
    if sync_result.returncode != 0:
        raise PlatformError(
            f"uv sync failed: {sync_result.stderr}",
            code="E_UV_SYNC_FAILED",
            reason=app.path,
        )

    # Build wheel/sdist
    print(f"  Building {app.path}...")
    build_result = run_cmd(
        ["uv", "build"],
        cwd=str(app_dir),
        check=False,
    )
    if build_result.returncode != 0:
        raise PlatformError(
            f"uv build failed: {build_result.stderr}",
            code="E_UV_BUILD_FAILED",
            reason=app.path,
        )

    return str(dist_dir)
