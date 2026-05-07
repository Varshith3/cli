from __future__ import annotations

from pathlib import Path

from platform_cli.core.config import get_value
from platform_cli.core.errors import PlatformError
from platform_cli.core.release_content import (
    DEFAULT_INDEX_ASSET,
    DEFAULT_INDEX_REPO,
    DEFAULT_INDEX_TAG,
    DEFAULT_SCOPE_KIND,
    build_sync_root_resolver,
    preview_content_updates,
    run_sync_actions,
)
from platform_cli.manifests.load import preferred_managed_toolset_path


TEAM_TOOLSET_CAPABILITY = "ghdp-team-toolset"


def _index_repo() -> str:
    value = str(get_value("sync.index.repo", DEFAULT_INDEX_REPO) or "").strip()
    return value or DEFAULT_INDEX_REPO


def _index_tag() -> str:
    value = str(get_value("sync.index.tag", DEFAULT_INDEX_TAG) or "").strip()
    return value or DEFAULT_INDEX_TAG


def _index_asset_name() -> str:
    value = str(get_value("sync.index.asset", DEFAULT_INDEX_ASSET) or "").strip()
    return value or DEFAULT_INDEX_ASSET


def managed_team_toolset_path() -> Path:
    return preferred_managed_toolset_path()


def sync_team_toolset(*, fail_on_error: bool = False) -> dict[str, object]:
    resolver = build_sync_root_resolver()
    index_repo = _index_repo()
    index_tag = _index_tag()
    index_asset = _index_asset_name()
    managed_path = managed_team_toolset_path()

    try:
        preview = preview_content_updates(
            repo=index_repo,
            tag=index_tag,
            asset_name=index_asset,
            capability=TEAM_TOOLSET_CAPABILITY,
            scope_kind=DEFAULT_SCOPE_KIND,
            scope_ref="",
            resolve_root_key=resolver,
        )
        items = list(preview.get("capabilities", []))
        if not items:
            result = {
                "capability": TEAM_TOOLSET_CAPABILITY,
                "target_path": str(managed_path),
                "local_status": "missing_capability",
                "sync_result": {},
                "used_cached": managed_path.exists(),
            }
            if fail_on_error:
                raise PlatformError(
                    "Team toolset sync could not find the 'ghdp-team-toolset' capability in the content index.",
                    code="E_TEAM_TOOLSET_CAPABILITY_NOT_FOUND",
                    reason=TEAM_TOOLSET_CAPABILITY,
                )
            return result

        item = items[0] if isinstance(items[0], dict) else {}
        action = str(item.get("action", "none")).strip() or "none"
        if action in {"none", "blocked"}:
            result = {
                "capability": TEAM_TOOLSET_CAPABILITY,
                "target_path": str(managed_path),
                "local_status": "current" if action == "none" else action,
                "latest_tag": str(item.get("latest_tag", "")).strip(),
                "latest_version": str(item.get("latest_version", "")).strip(),
                "sync_result": {},
                "used_cached": managed_path.exists(),
            }
            if fail_on_error and not managed_path.exists():
                raise PlatformError(
                    "Team toolset sync completed, but no managed team toolset file is available locally.",
                    code="E_TEAM_TOOLSET_UNAVAILABLE",
                    reason=str(managed_path),
                )
            return result

        applied = run_sync_actions(
            repo=index_repo,
            tag=index_tag,
            asset_name=index_asset,
            capability=TEAM_TOOLSET_CAPABILITY,
            apply=True,
            scope_kind=DEFAULT_SCOPE_KIND,
            scope_ref="",
            resolve_root_key=resolver,
        )
        applied_items = list(applied.get("preview", {}).get("capabilities", []))
        applied_item = applied_items[0] if applied_items else {}
        result = {
            "capability": TEAM_TOOLSET_CAPABILITY,
            "target_path": str(managed_path),
            "local_status": "synced",
            "latest_tag": str(applied_item.get("latest_tag", item.get("latest_tag", ""))).strip(),
            "latest_version": str(applied_item.get("latest_version", item.get("latest_version", ""))).strip(),
            "sync_result": dict(applied.get("results", {})),
            "used_cached": managed_path.exists(),
        }
        if fail_on_error and not managed_path.exists():
            raise PlatformError(
                "Team toolset sync finished without producing the managed team toolset file.",
                code="E_TEAM_TOOLSET_UNAVAILABLE",
                reason=str(managed_path),
            )
        return result
    except Exception as exc:
        if fail_on_error:
            if isinstance(exc, PlatformError):
                raise
            raise PlatformError(
                f"Team toolset sync failed: {exc}",
                code="E_TEAM_TOOLSET_SYNC_FAILED",
                reason=TEAM_TOOLSET_CAPABILITY,
            ) from exc
        return {
            "capability": TEAM_TOOLSET_CAPABILITY,
            "target_path": str(managed_path),
            "local_status": "warning" if managed_path.exists() else "fallback",
            "sync_result": {
                "action": "warning",
                "message": str(exc),
            },
            "used_cached": managed_path.exists(),
        }


def ensure_team_toolset_available(*, force_refresh: bool = False) -> dict[str, object]:
    managed_path = managed_team_toolset_path()
    if managed_path.exists() and not force_refresh:
        return {
            "capability": TEAM_TOOLSET_CAPABILITY,
            "target_path": str(managed_path),
            "local_status": "cached",
            "sync_result": {},
            "used_cached": True,
        }

    return sync_team_toolset(fail_on_error=True)


def ensure_team_toolset_synced() -> dict[str, object]:
    return sync_team_toolset()
