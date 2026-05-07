"""Branch-based version management for app builds."""
# NOTE: Architectural rules in ARCHITECTURE.md – do not refactor cross-layer.

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Tuple

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


from platform_cli.tools.git_repo import (
    get_latest_release_tag,
    get_short_commit_hash,
    is_main_branch,
    parse_semver,
)


def resolve_version(
    repo_root: Path,
    app_type: str,
) -> Tuple[str, str]:
    """
    Resolve the build version from git tags only — no developer input needed.

    Version is derived entirely from the latest git release tag:
    - Next minor version is used as the base (e.g., v2.34.0 → 2.35.0)

    Branch-based logic:
    - main/master → release: {major}.{minor+1}.0
    - any other branch → snapshot:
        Python: {major}.{minor+1}.0.dev{yyyymmdd}+{githash}
        Scala:  {major}.{minor+1}.0-{githash}-SNAPSHOT

    Args:
        repo_root: Repository root for git operations
        app_type: "python" or "scala"

    Returns:
        Tuple of (final_version, mode) where mode is "snapshot" or "release"
    """
    last_release = get_latest_release_tag(repo_root)
    on_main = is_main_branch(repo_root)
    major, minor, _patch = parse_semver(last_release)

    # Next minor is the base for both release and snapshot
    next_minor_base = f"{major}.{minor + 1}.0"

    if on_main:
        return next_minor_base, "release"

    # Snapshot mode
    date_str = datetime.now().strftime("%Y%m%d")
    git_hash = get_short_commit_hash(repo_root)

    if app_type == "python":
        return f"{next_minor_base}.dev{date_str}+{git_hash}", "snapshot"
    elif app_type == "scala":
        return f"{next_minor_base}-{git_hash}-SNAPSHOT", "snapshot"
    else:
        raise PlatformError(
            f"Unknown app type '{app_type}' for version resolution. Expected 'python' or 'scala'.",
            code="E_APP_TYPE_UNKNOWN",
            reason=app_type,
        )


def get_codeartifact_mode(repo_root: Path) -> str:
    """Get CodeArtifact repo mode (snapshot/release) based on branch."""
    if is_main_branch(repo_root):
        return "release"
    return "snapshot"
