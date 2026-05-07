from __future__ import annotations

import os
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
from platform_cli.manifests.load import (
    CLAUDE_ATHENA_WORKGROUP_MAP_ENV_KEY,
    preferred_managed_claude_athena_workgroup_map_path,
    preferred_user_claude_athena_workgroup_map_path,
)


CLAUDE_ATHENA_WORKGROUP_CAPABILITY = "claude-athena-workgroup-map"


def _index_repo() -> str:
    value = str(get_value("sync.index.repo", DEFAULT_INDEX_REPO) or "").strip()
    return value or DEFAULT_INDEX_REPO


def _index_tag() -> str:
    value = str(get_value("sync.index.tag", DEFAULT_INDEX_TAG) or "").strip()
    return value or DEFAULT_INDEX_TAG


def _index_asset_name() -> str:
    value = str(get_value("sync.index.asset", DEFAULT_INDEX_ASSET) or "").strip()
    return value or DEFAULT_INDEX_ASSET


def managed_claude_athena_workgroup_map_path() -> Path:
    return preferred_managed_claude_athena_workgroup_map_path()


def user_claude_athena_workgroup_map_path() -> Path:
    return preferred_user_claude_athena_workgroup_map_path()


def sync_claude_athena_workgroup_map(*, fail_on_error: bool = False) -> dict[str, object]:
    resolver = build_sync_root_resolver()
    index_repo = _index_repo()
    index_tag = _index_tag()
    index_asset = _index_asset_name()
    managed_path = managed_claude_athena_workgroup_map_path()

    try:
        preview = preview_content_updates(
            repo=index_repo,
            tag=index_tag,
            asset_name=index_asset,
            capability=CLAUDE_ATHENA_WORKGROUP_CAPABILITY,
            scope_kind=DEFAULT_SCOPE_KIND,
            scope_ref="",
            resolve_root_key=resolver,
        )
        items = list(preview.get("capabilities", []))
        if not items:
            result = {
                "capability": CLAUDE_ATHENA_WORKGROUP_CAPABILITY,
                "target_path": str(managed_path),
                "local_status": "missing_capability",
                "sync_result": {},
                "used_cached": managed_path.exists(),
            }
            if fail_on_error:
                raise PlatformError(
                    "Claude Athena workgroup map sync could not find the capability in the content index.",
                    code="E_CLAUDE_ATHENA_WORKGROUP_CAPABILITY_NOT_FOUND",
                    reason=CLAUDE_ATHENA_WORKGROUP_CAPABILITY,
                )
            return result

        item = items[0] if isinstance(items[0], dict) else {}
        action = str(item.get("action", "none")).strip() or "none"
        if action in {"none", "blocked"}:
            result = {
                "capability": CLAUDE_ATHENA_WORKGROUP_CAPABILITY,
                "target_path": str(managed_path),
                "local_status": "current" if action == "none" and managed_path.exists() else ("blocked" if action == "blocked" else "fallback"),
                "latest_tag": str(item.get("latest_tag", "")).strip(),
                "latest_version": str(item.get("latest_version", "")).strip(),
                "sync_result": {},
                "used_cached": managed_path.exists(),
            }
            if fail_on_error and not managed_path.exists():
                raise PlatformError(
                    "Claude Athena workgroup map sync completed, but no managed mapping file is available locally.",
                    code="E_CLAUDE_ATHENA_WORKGROUP_MAP_UNAVAILABLE",
                    reason=str(managed_path),
                )
            return result

        applied = run_sync_actions(
            repo=index_repo,
            tag=index_tag,
            asset_name=index_asset,
            capability=CLAUDE_ATHENA_WORKGROUP_CAPABILITY,
            apply=True,
            scope_kind=DEFAULT_SCOPE_KIND,
            scope_ref="",
            resolve_root_key=resolver,
        )
        applied_items = list(applied.get("preview", {}).get("capabilities", []))
        applied_item = applied_items[0] if applied_items else {}
        result = {
            "capability": CLAUDE_ATHENA_WORKGROUP_CAPABILITY,
            "target_path": str(managed_path),
            "local_status": "synced" if managed_path.exists() else "fallback",
            "latest_tag": str(applied_item.get("latest_tag", item.get("latest_tag", ""))).strip(),
            "latest_version": str(applied_item.get("latest_version", item.get("latest_version", ""))).strip(),
            "sync_result": dict(applied.get("results", {})),
            "used_cached": managed_path.exists(),
        }
        if fail_on_error and not managed_path.exists():
            raise PlatformError(
                "Claude Athena workgroup map sync finished without producing the managed mapping file.",
                code="E_CLAUDE_ATHENA_WORKGROUP_MAP_UNAVAILABLE",
                reason=str(managed_path),
            )
        return result
    except Exception as exc:
        if fail_on_error:
            if isinstance(exc, PlatformError):
                raise
            raise PlatformError(
                f"Claude Athena workgroup map sync failed: {exc}",
                code="E_CLAUDE_ATHENA_WORKGROUP_MAP_SYNC_FAILED",
                reason=CLAUDE_ATHENA_WORKGROUP_CAPABILITY,
            ) from exc
        return {
            "capability": CLAUDE_ATHENA_WORKGROUP_CAPABILITY,
            "target_path": str(managed_path),
            "local_status": "warning" if managed_path.exists() else "fallback",
            "sync_result": {
                "action": "warning",
                "message": str(exc),
            },
            "used_cached": managed_path.exists(),
        }


def ensure_claude_athena_workgroup_map_available(*, force_refresh: bool = False) -> dict[str, object]:
    env_override = str(os.environ.get(CLAUDE_ATHENA_WORKGROUP_MAP_ENV_KEY, "") or "").strip()
    if env_override:
        return {
            "capability": CLAUDE_ATHENA_WORKGROUP_CAPABILITY,
            "target_path": env_override,
            "local_status": "override",
            "sync_result": {},
            "used_cached": False,
        }

    user_path = user_claude_athena_workgroup_map_path()
    if user_path.exists():
        return {
            "capability": CLAUDE_ATHENA_WORKGROUP_CAPABILITY,
            "target_path": str(user_path),
            "local_status": "override",
            "sync_result": {},
            "used_cached": False,
        }

    managed_path = managed_claude_athena_workgroup_map_path()
    if managed_path.exists() and not force_refresh:
        return {
            "capability": CLAUDE_ATHENA_WORKGROUP_CAPABILITY,
            "target_path": str(managed_path),
            "local_status": "cached",
            "sync_result": {},
            "used_cached": True,
        }

    return sync_claude_athena_workgroup_map(fail_on_error=False)
