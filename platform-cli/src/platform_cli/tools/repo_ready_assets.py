# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from platform_cli.core.errors import PlatformError
from platform_cli.core.release_content import (
    build_sync_root_resolver,
    DEFAULT_SCOPE_KIND,
    install_release_content,
)

REPO_READY_ASSET_CAPABILITY = "repo-ready-assets"
DEFAULT_RELEASE_REPO = "gh-org-data-platform/dp-tools-local-setup"
DEFAULT_RELEASE_TAG = "repo-ready-assets-v1.0.0"
DEFAULT_MANIFEST_ASSET = "content-manifest.json"
REPO_READY_BASE_SUBDIR = Path("repo_ready") / "base"


@dataclass(frozen=True)
class RepoReadyAssetScope:
    name: str
    root: Path


def global_repo_ready_asset_root() -> Path:
    return Path.home() / ".ghdp" / REPO_READY_BASE_SUBDIR


def repo_ready_asset_scopes(*, repo_root: Path | None = None) -> List[RepoReadyAssetScope]:
    # Future expansion point:
    # - category overlays under ~/.ghdp/repo_ready/categories/<category>
    # - repo overlays under ~/.ghdp/repo_ready/repos/<repo-name>
    return [RepoReadyAssetScope(name="base_global", root=global_repo_ready_asset_root())]


def _release_repo() -> str:
    return (os.getenv("GHDP_REPO_READY_ASSET_RELEASE_REPO") or DEFAULT_RELEASE_REPO).strip()


def _release_tag() -> str:
    return (os.getenv("GHDP_REPO_READY_ASSET_RELEASE_TAG") or DEFAULT_RELEASE_TAG).strip()


def _manifest_asset_name() -> str:
    return (os.getenv("GHDP_REPO_READY_ASSET_MANIFEST_ASSET") or DEFAULT_MANIFEST_ASSET).strip()


def ensure_repo_ready_assets_synced(repo_root: Path | None = None) -> Dict[str, object]:
    try:
        return install_release_content(
            capability=REPO_READY_ASSET_CAPABILITY,
            repo=_release_repo(),
            tag=_release_tag(),
            manifest_asset=_manifest_asset_name(),
            resolve_root_key=build_sync_root_resolver(),
            scope_kind=DEFAULT_SCOPE_KIND,
            scope_ref="",
        )
    except PlatformError as e:
        raise PlatformError(
            str(e),
            code="E_REPO_READY_ASSET_SYNC_FAILED",
            reason="repo_ready_assets",
        )
