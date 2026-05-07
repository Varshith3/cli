# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

from platform_cli.core.release_content import install_release_content
from platform_cli.core.errors import PlatformError


SKILL_NAME = "aws-readonly-runbook"
DEFAULT_RELEASE_REPO = "gh-org-data-platform/dp-tools-local-setup"
DEFAULT_RELEASE_TAG = "codex-skills-aws-v1.0.0"
DEFAULT_MANIFEST_ASSET = "content-manifest.json"


def _codex_skills_root() -> Path:
    return Path.home() / ".codex" / "skills"


def _release_repo() -> str:
    return (os.getenv("GHDP_CODEX_SKILL_RELEASE_REPO") or DEFAULT_RELEASE_REPO).strip()


def _release_tag() -> str:
    return (os.getenv("GHDP_CODEX_SKILL_RELEASE_TAG") or DEFAULT_RELEASE_TAG).strip()


def _manifest_asset_name() -> str:
    return (os.getenv("GHDP_CODEX_SKILL_MANIFEST_ASSET") or DEFAULT_MANIFEST_ASSET).strip()


def _resolve_root_key(root_key: str) -> Path:
    if root_key == "codex_skills_root":
        return _codex_skills_root()
    raise PlatformError(
        f"Unsupported Codex skill target root key: {root_key}",
        code="E_CODEX_SKILL_SYNC_FAILED",
        reason="codex_skill_sync",
    )


def sync_aws_readonly_skill() -> Dict[str, object]:
    """
    Sync the AWS runbook Codex skill into the global Codex skills directory
    using only the release-backed content path.
    """
    try:
        result = install_release_content(
            capability="codex-skills-aws",
            repo=_release_repo(),
            tag=_release_tag(),
            manifest_asset=_manifest_asset_name(),
            resolve_root_key=_resolve_root_key,
        )
        result["skill_name"] = SKILL_NAME
        return result
    except PlatformError as e:
        raise PlatformError(
            str(e),
            code="E_CODEX_SKILL_SYNC_FAILED",
            reason="codex_skill_sync",
        )
