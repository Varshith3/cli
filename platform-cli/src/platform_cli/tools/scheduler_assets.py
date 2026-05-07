from __future__ import annotations

import json
from pathlib import Path
import shutil

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
from platform_cli.manifests import scheduler as scheduler_manifest


SCHEDULER_CAPABILITY = "background-scheduler"
SCHEDULER_SOURCE_STATE_FILE = ".asset-source.json"


def _index_repo() -> str:
    value = str(get_value("sync.index.repo", DEFAULT_INDEX_REPO) or "").strip()
    return value or DEFAULT_INDEX_REPO


def _index_tag() -> str:
    value = str(get_value("sync.index.tag", DEFAULT_INDEX_TAG) or "").strip()
    return value or DEFAULT_INDEX_TAG


def _index_asset_name() -> str:
    value = str(get_value("sync.index.asset", DEFAULT_INDEX_ASSET) or "").strip()
    return value or DEFAULT_INDEX_ASSET


def scheduler_assets_root() -> Path:
    return scheduler_manifest.installed_capability_root()


def scheduler_assets_present() -> bool:
    ready, _ = scheduler_manifest.installed_scheduler_assets_status()
    return ready


def _reset_invalid_scheduler_assets() -> None:
    root = scheduler_assets_root().resolve()
    if not root.exists():
        return
    allowed_parent = (Path.home() / ".ghdp" / "capabilities").resolve()
    if allowed_parent not in root.parents:
        raise PlatformError(
            f"Refusing to reset scheduler assets outside the expected GHDP capability root: '{root}'.",
            code="E_SCHEDULER_ASSET_SYNC_FAILED",
            reason=str(root),
        )
    shutil.rmtree(root)


def _scheduler_preview_item(*, resolver, index_repo: str, index_tag: str, index_asset: str) -> dict[str, object] | None:
    preview = preview_content_updates(
        repo=index_repo,
        tag=index_tag,
        asset_name=index_asset,
        capability=SCHEDULER_CAPABILITY,
        scope_kind=DEFAULT_SCOPE_KIND,
        scope_ref="",
        resolve_root_key=resolver,
    )
    items = list(preview.get("capabilities", []))
    if not items:
        return None
    item = items[0]
    if not isinstance(item, dict):
        return None
    return item


def _run_scheduler_sync_pre_hook(*, resolver, index_repo: str, index_tag: str, index_asset: str) -> dict[str, object]:
    result = run_sync_actions(
        repo=index_repo,
        tag=index_tag,
        asset_name=index_asset,
        capability=SCHEDULER_CAPABILITY,
        apply=True,
        scope_kind=DEFAULT_SCOPE_KIND,
        scope_ref="",
        resolve_root_key=resolver,
    )
    preview_items = list(result.get("preview", {}).get("capabilities", []))
    preview_item = preview_items[0] if preview_items else {}
    return {
        "action": "sync",
        "latest_tag": str(preview_item.get("latest_tag", "")).strip(),
        "latest_version": str(preview_item.get("latest_version", "")).strip(),
        "sync_result": dict(result.get("results", {})),
    }


def describe_scheduler_asset_source(resolution: dict[str, object]) -> str:
    source_kind = str(resolution.get("source_kind", "synced")).strip().lower() or "synced"
    materialization_state = str(resolution.get("materialization_state", "cached")).strip().lower() or "cached"
    if source_kind == "packaged":
        if materialization_state == "installed":
            return "using packaged emergency bootstrap scheduler assets"
        return "using cached packaged emergency bootstrap scheduler assets"
    if materialization_state == "installed":
        return "using freshly synced scheduler assets"
    return "using cached synced scheduler assets"


def should_surface_scheduler_asset_source(resolution: dict[str, object], *, verbose: bool = False) -> bool:
    return verbose or bool(resolution.get("fallback_active"))


def _source_state_path(capability_root: Path) -> Path:
    return capability_root / SCHEDULER_SOURCE_STATE_FILE


def _read_source_state(capability_root: Path) -> dict[str, str]:
    state_path = _source_state_path(capability_root)
    if not state_path.exists():
        return {"source_kind": "synced"}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {"source_kind": "synced"}
    if not isinstance(payload, dict):
        return {"source_kind": "synced"}
    source_kind = str(payload.get("source_kind", "synced")).strip().lower() or "synced"
    if source_kind not in {"synced", "packaged"}:
        source_kind = "synced"
    return {"source_kind": source_kind}


def _write_source_state(capability_root: Path, *, source_kind: str) -> None:
    capability_root.mkdir(parents=True, exist_ok=True)
    _source_state_path(capability_root).write_text(
        json.dumps({"source_kind": source_kind}, indent=2) + "\n",
        encoding="utf-8",
    )


def _build_resolution(
    *,
    capability_root: Path,
    source_kind: str,
    materialization_state: str,
    latest_tag: str = "",
    latest_version: str = "",
    sync_result: dict[str, object] | None = None,
) -> dict[str, object]:
    resolution = {
        "capability": SCHEDULER_CAPABILITY,
        "target_path": str(capability_root),
        "source_kind": source_kind,
        "materialization_state": materialization_state,
        "fallback_active": source_kind == "packaged",
        "latest_tag": latest_tag.strip(),
        "latest_version": latest_version.strip(),
        "sync_result": dict(sync_result or {}),
    }
    resolution["source_explanation"] = describe_scheduler_asset_source(resolution)
    return resolution


def _copy_tree_contents(*, source_root: Path, target_root: Path) -> None:
    target_root.mkdir(parents=True, exist_ok=True)
    for item in source_root.iterdir():
        destination = target_root / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(item, destination)


def _materialize_packaged_scheduler_assets(*, capability_root: Path) -> None:
    packaged_root = scheduler_manifest.packaged_bootstrap_root()
    ready, reason = scheduler_manifest.scheduler_assets_status(capability_root=packaged_root)
    if not ready:
        raise PlatformError(
            "Packaged scheduler bootstrap assets are not valid. Repair the bundled scheduler resources.",
            code="E_SCHEDULER_ASSET_SYNC_FAILED",
            reason=reason,
        )
    if capability_root.exists():
        _reset_invalid_scheduler_assets()
    _copy_tree_contents(source_root=packaged_root, target_root=capability_root)
    _write_source_state(capability_root, source_kind="packaged")


def _cached_resolution(
    *,
    capability_root: Path,
    sync_result: dict[str, object] | None = None,
) -> dict[str, object]:
    source_kind = _read_source_state(capability_root).get("source_kind", "synced")
    return _build_resolution(
        capability_root=capability_root,
        source_kind=source_kind,
        materialization_state="cached",
        sync_result=sync_result,
    )


def _packaged_fallback_resolution(
    *,
    capability_root: Path,
    sync_result: dict[str, object] | None = None,
) -> dict[str, object]:
    _materialize_packaged_scheduler_assets(capability_root=capability_root)
    return _build_resolution(
        capability_root=capability_root,
        source_kind="packaged",
        materialization_state="installed",
        sync_result=sync_result,
    )


def ensure_scheduler_assets_synced() -> dict[str, object]:
    resolver = build_sync_root_resolver()
    index_repo = _index_repo()
    index_tag = _index_tag()
    index_asset = _index_asset_name()
    capability_root = scheduler_assets_root()
    ready, status_reason = scheduler_manifest.installed_scheduler_assets_status()

    try:
        preview_item = _scheduler_preview_item(
            resolver=resolver,
            index_repo=index_repo,
            index_tag=index_tag,
            index_asset=index_asset,
        )
    except PlatformError as e:
        if ready:
            return _cached_resolution(
                capability_root=capability_root,
                sync_result={
                    "action": "warning",
                    "message": str(e),
                    "code": str(e.code or ""),
                },
            )
        return _packaged_fallback_resolution(
            capability_root=capability_root,
            sync_result={
                "action": "fallback",
                "message": str(e),
                "code": str(e.code or ""),
            },
        )

    try:
        if not preview_item:
            if ready:
                return _cached_resolution(
                    capability_root=capability_root,
                    sync_result={
                        "action": "warning",
                        "message": f"Capability '{SCHEDULER_CAPABILITY}' was not found in the sync content index.",
                        "code": "E_SYNC_CAPABILITY_NOT_FOUND",
                    },
                )
            return _packaged_fallback_resolution(
                capability_root=capability_root,
                sync_result={
                    "action": "fallback",
                    "message": f"Capability '{SCHEDULER_CAPABILITY}' was not found in the sync content index.",
                    "code": "E_SYNC_CAPABILITY_NOT_FOUND",
                },
            )
        action = str(preview_item.get("action", "none")).strip() or "none"
        if ready and action == "none":
            resolution = _cached_resolution(capability_root=capability_root)
            resolution["latest_tag"] = str(preview_item.get("latest_tag", "")).strip()
            resolution["latest_version"] = str(preview_item.get("latest_version", "")).strip()
            return resolution
        if action == "blocked":
            if ready:
                return _cached_resolution(
                    capability_root=capability_root,
                    sync_result={
                        "action": "warning",
                        "message": f"Scheduler capability '{SCHEDULER_CAPABILITY}' has blocked sync updates.",
                        "code": "E_SYNC_UPDATE_BLOCKED",
                    },
                )
            return _packaged_fallback_resolution(
                capability_root=capability_root,
                sync_result={
                    "action": "fallback",
                    "message": f"Scheduler capability '{SCHEDULER_CAPABILITY}' has blocked sync updates.",
                    "code": "E_SYNC_UPDATE_BLOCKED",
                },
            )
        if not ready:
            _reset_invalid_scheduler_assets()
        refresh = _run_scheduler_sync_pre_hook(
            resolver=resolver,
            index_repo=index_repo,
            index_tag=index_tag,
            index_asset=index_asset,
        )
    except PlatformError as e:
        if ready:
            return _cached_resolution(
                capability_root=capability_root,
                sync_result={
                    "action": "warning",
                    "message": str(e),
                    "code": str(e.code or ""),
                },
            )
        return _packaged_fallback_resolution(
            capability_root=capability_root,
            sync_result={
                "action": "fallback",
                "message": str(e),
                "code": str(e.code or ""),
            },
        )

    ready, status_reason = scheduler_manifest.installed_scheduler_assets_status()
    if not ready:
        raise PlatformError(
            "Scheduler capability assets are installed but incomplete. Run `ghdp sync run --capability background-scheduler` "
            "or repair the synced scheduler content.",
            code="E_SCHEDULER_ASSET_SYNC_FAILED",
            reason=status_reason,
        )
    _write_source_state(capability_root, source_kind="synced")

    return {
        **_build_resolution(
            capability_root=capability_root,
            source_kind="synced",
            materialization_state="installed",
        ),
        "latest_tag": str(refresh.get("latest_tag", "")).strip(),
        "latest_version": str(refresh.get("latest_version", "")).strip(),
        "sync_result": dict(refresh.get("sync_result", {})) if isinstance(refresh.get("sync_result"), dict) else refresh,
    }
