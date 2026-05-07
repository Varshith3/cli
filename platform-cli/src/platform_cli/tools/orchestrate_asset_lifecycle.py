from __future__ import annotations

import base64
import json
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from platform_cli.core.errors import PlatformError
from platform_cli.core.github_auth import gh_subprocess_env
from platform_cli.core.release_content import build_sync_root_resolver, list_sync_status
from platform_cli.core.repo_roots import resolve_repo_root
from platform_cli.exec.runner import run_cmd
from platform_cli.manifests.orchestrate_load import load_orchestrate_json_file
from platform_cli.orchestrate_kernel.runtime_support import (
    ActiveRunContext,
    iso_now,
    resolve_active_run_context,
    update_poa_section,
    upsert_decisions,
    write_handoff,
    write_json,
    write_markdown,
    write_resume_context,
)
from platform_cli.tools.orchestrate_contract import load_agent_contract


_ASSET_PLUGIN_PATH = Path(".ghdp/plugins/asset-lifecycle-sync/plugin.json")
_ALLOWLIST_PATH = Path(".ghdp/capability-allowlist.json")
_CONTENT_INDEX_PATH = Path("platform-cli/release-assets/content_index/content-index.json")
_RELEASE_CATALOG_ROOT = Path("platform-cli/release-assets/catalog")
_POA_ASSET_BEGIN = "<!-- GHDP:BEGIN ASSET_LIFECYCLE -->"
_POA_ASSET_END = "<!-- GHDP:END ASSET_LIFECYCLE -->"
_SUPPORTED_OPERATIONS = {"inventory", "create", "revise", "update_versioned_asset", "remove"}
_SUPPORTED_PROVIDER_FAMILIES = {"github_release", "marketplace_repo"}
_TOOLSET_CODEX_TARGET = "toolset_codex_version"
_CONTENT_INDEX_RELEASE_TAG = "content-index-latest"
_CONTENT_INDEX_RELEASE_ASSET = "content-index.json"


@dataclass
class OrchestrateAssetLifecycleResult:
    repo_root: str
    branch_name: str
    ticket_key: str
    active_run_key: str
    action: str
    status: str
    operation: str
    asset_target: str
    capability_id: str
    provider_family: str
    source_files: List[str]
    changed_files: List[str]
    changed_teams: List[str]
    release_implications: List[str]
    inventory_count: int
    bundle_contract_path: str
    built_bundle_dir: str
    published: bool
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_asset_lifecycle(
    *,
    repo_root: Path | None = None,
    operation: str = "inventory",
    asset_target: str = "",
    new_version: str = "",
    payload_file: Path | None = None,
    provider_family: str = "",
    publish: bool = False,
) -> OrchestrateAssetLifecycleResult:
    normalized_operation = str(operation).strip().lower()
    if normalized_operation not in _SUPPORTED_OPERATIONS:
        raise PlatformError(
            f"Unsupported asset lifecycle operation '{operation}'.",
            code="E_ASSET_LIFECYCLE_OPERATION_UNSUPPORTED",
            reason=normalized_operation,
        )

    resolved_root = resolve_repo_root(repo_root)
    plugin_contract = _load_asset_plugin_contract(resolved_root)
    agent_contract = load_agent_contract(agent_id="asset-lifecycle", repo_root=resolved_root)
    context = _resolve_context_if_available(resolved_root)
    inventory = _capability_inventory(resolved_root)
    payload = _load_payload_file(payload_file)

    source_files: List[str] = []
    changed_files: List[str] = []
    changed_teams: List[str] = []
    capability_id = str(asset_target).strip()
    resolved_provider = str(provider_family).strip().lower()
    release_implications: List[str] = []
    bundle_contract_path = ""
    built_bundle_dir = ""
    published = False
    message = "Asset inventory collected and no files were changed."
    status = "completed"

    if normalized_operation == "inventory":
        release_implications = ["Inventory-only path: no asset files were changed."]
    elif asset_target == _TOOLSET_CODEX_TARGET and normalized_operation in {"revise", "update_versioned_asset"}:
        normalized_version = _normalize_semver(new_version)
        changed_files, changed_teams = _revise_toolset_codex_version(resolved_root, normalized_version)
        source_files = list(changed_files)
        resolved_provider = "github_release"
        capability_id = "ghdp-team-toolset"
        release_implications = _special_release_implications(normalized_version)
        message = f"Revised the Codex minimum version to {normalized_version} across the team toolset assets."
    else:
        target = _resolve_capability_target(
            repo_root=resolved_root,
            asset_target=asset_target,
            provider_family=resolved_provider,
            operation=normalized_operation,
            payload=payload,
        )
        resolved_provider = target["provider_family"]
        capability_id = target["capability_id"]
        source_files = list(target["source_files"])
        changed_files, changed_teams, release_implications, message, bundle_contract_path, built_bundle_dir, published = _apply_capability_operation(
            repo_root=resolved_root,
            target=target,
            operation=normalized_operation,
            new_version=new_version,
            payload=payload,
            publish=publish,
        )

    _persist_asset_artifacts(
        context=context,
        operation=normalized_operation,
        asset_target=asset_target or "(inventory)",
        inventory=inventory,
        changed_files=changed_files,
        changed_teams=changed_teams,
        source_files=source_files,
        release_implications=release_implications,
        plugin_contract=plugin_contract,
        agent_contract=agent_contract,
        message=message,
    )

    return OrchestrateAssetLifecycleResult(
        repo_root=str(resolved_root),
        branch_name=context.branch_name if context else "",
        ticket_key=context.ticket_key if context else "",
        active_run_key=context.active_run_key if context else "",
        action="asset_lifecycle",
        status=status,
        operation=normalized_operation,
        asset_target=asset_target or "",
        capability_id=capability_id,
        provider_family=resolved_provider,
        source_files=source_files,
        changed_files=changed_files,
        changed_teams=changed_teams,
        release_implications=release_implications,
        inventory_count=len(inventory),
        bundle_contract_path=bundle_contract_path,
        built_bundle_dir=built_bundle_dir,
        published=published,
        message=message,
    )


def _load_asset_plugin_contract(repo_root: Path) -> Dict[str, Any]:
    path = repo_root / _ASSET_PLUGIN_PATH
    if not path.exists():
        raise PlatformError(
            "Asset lifecycle plugin contract is missing from .ghdp/plugins.",
            code="E_ASSET_LIFECYCLE_PLUGIN_MISSING",
            reason=_ASSET_PLUGIN_PATH.as_posix(),
        )
    return load_orchestrate_json_file(path)


def _resolve_context_if_available(repo_root: Path) -> ActiveRunContext | None:
    try:
        return resolve_active_run_context(repo_root=repo_root)
    except PlatformError:
        return None


def _load_payload_file(payload_file: Path | None) -> Dict[str, Any]:
    if payload_file is None:
        return {}
    payload_path = Path(payload_file).expanduser()
    if not payload_path.exists():
        raise PlatformError(
            f"Asset lifecycle payload file '{payload_path}' does not exist.",
            code="E_ASSET_LIFECYCLE_BAD_ARGS",
            reason="payload_file_missing",
        )
    payload = json.loads(payload_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise PlatformError(
            f"Asset lifecycle payload file '{payload_path}' must contain a JSON object.",
            code="E_ASSET_LIFECYCLE_BAD_ARGS",
            reason="payload_file_invalid",
        )
    return payload


def _resolve_capability_target(
    *,
    repo_root: Path,
    asset_target: str,
    provider_family: str,
    operation: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    target_name = str(asset_target).strip() or str(payload.get("capability", "")).strip()
    requested_provider = str(provider_family).strip().lower() or str(payload.get("provider_family", "")).strip().lower()
    content_index = _load_content_index(repo_root)
    release_entry = _find_release_capability(content_index, target_name)
    allowlist = _load_repo_allowlist(repo_root)
    marketplace_lookup = _find_marketplace_capability(allowlist, target_name)

    if release_entry and marketplace_lookup:
        raise PlatformError(
            f"Asset target '{target_name}' is ambiguous across provider families. Pass --provider-family explicitly.",
            code="E_ASSET_LIFECYCLE_TARGET_AMBIGUOUS",
            reason=target_name,
        )

    if release_entry and (not requested_provider or requested_provider == "github_release"):
        return {
            "provider_family": "github_release",
            "capability_id": target_name,
            "content_index": content_index,
            "release_entry": release_entry,
            "source_files": [_CONTENT_INDEX_PATH.as_posix()],
        }
    if marketplace_lookup and (not requested_provider or requested_provider == "marketplace_repo"):
        source_name, target_name_group, entry_index, entry_payload = marketplace_lookup
        return {
            "provider_family": "marketplace_repo",
            "capability_id": str(entry_payload.get("capability", "")).strip(),
            "allowlist": allowlist,
            "source_name": source_name,
            "target_name": target_name_group,
            "entry_index": entry_index,
            "entry_payload": dict(entry_payload),
            "source_files": [_ALLOWLIST_PATH.as_posix()],
        }

    if operation == "create":
        requested = requested_provider or str(payload.get("provider_family", "")).strip().lower()
        if requested not in _SUPPORTED_PROVIDER_FAMILIES:
            raise PlatformError(
                "Asset lifecycle create requires --provider-family github_release or marketplace_repo.",
                code="E_ASSET_LIFECYCLE_BAD_ARGS",
                reason="provider_family",
            )
        if requested == "github_release":
            return {
                "provider_family": "github_release",
                "capability_id": str(payload.get("capability", "")).strip() or target_name,
                "content_index": content_index,
                "source_files": [_CONTENT_INDEX_PATH.as_posix()],
            }
        return {
            "provider_family": "marketplace_repo",
            "capability_id": str(payload.get("capability", "")).strip() or target_name,
            "allowlist": allowlist,
            "source_name": str(payload.get("source_name", "skill_marketplace")).strip() or "skill_marketplace",
            "target_name": str(payload.get("target_name", "")).strip(),
            "source_files": [_ALLOWLIST_PATH.as_posix()],
        }

    raise PlatformError(
        f"Asset target '{target_name or '(missing)'}' is not defined in the asset lifecycle sources.",
        code="E_ASSET_LIFECYCLE_TARGET_MISSING",
        reason=target_name or requested_provider,
    )


def _load_content_index(repo_root: Path) -> Dict[str, Any]:
    path = repo_root / _CONTENT_INDEX_PATH
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _load_repo_allowlist(repo_root: Path) -> Dict[str, Any]:
    path = repo_root / _ALLOWLIST_PATH
    if not path.exists():
        raise PlatformError(
            "Repo-owned marketplace allowlist is missing. Expected .ghdp/capability-allowlist.json.",
            code="E_ASSET_LIFECYCLE_TARGET_MISSING",
            reason=_ALLOWLIST_PATH.as_posix(),
        )
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _find_release_capability(content_index: Dict[str, Any], capability_id: str) -> Dict[str, Any] | None:
    capabilities = content_index.get("capabilities", [])
    if not isinstance(capabilities, list):
        return None
    for item in capabilities:
        if isinstance(item, dict) and str(item.get("capability", "")).strip() == capability_id:
            return item
    return None


def _find_marketplace_capability(allowlist: Dict[str, Any], capability_id: str) -> tuple[str, str, int, Dict[str, Any]] | None:
    sources = allowlist.get("sources", {})
    if not isinstance(sources, dict):
        return None
    for source_name, source_payload in sources.items():
        if not isinstance(source_payload, dict):
            continue
        targets = source_payload.get("targets", {})
        if not isinstance(targets, dict):
            continue
        for target_name, target_payload in targets.items():
            if not isinstance(target_payload, dict):
                continue
            entries = target_payload.get("entries", [])
            if not isinstance(entries, list):
                continue
            for index, entry_payload in enumerate(entries):
                if isinstance(entry_payload, dict) and str(entry_payload.get("capability", "")).strip() == capability_id:
                    return source_name, target_name, index, entry_payload
    return None


def _apply_capability_operation(
    *,
    repo_root: Path,
    target: Dict[str, Any],
    operation: str,
    new_version: str,
    payload: Dict[str, Any],
    publish: bool,
) -> tuple[List[str], List[str], List[str], str, str, str, bool]:
    provider_family = str(target["provider_family"])
    if provider_family == "github_release":
        return _apply_release_backed_operation(
            repo_root=repo_root,
            target=target,
            operation=operation,
            new_version=new_version,
            payload=payload,
            publish=publish,
        )
    changed_files, changed_teams, implications, message = _apply_marketplace_operation(
        repo_root=repo_root,
        target=target,
        operation=operation,
        payload=payload,
    )
    return changed_files, changed_teams, implications, message, "", "", False


def _apply_release_backed_operation(
    *,
    repo_root: Path,
    target: Dict[str, Any],
    operation: str,
    new_version: str,
    payload: Dict[str, Any],
    publish: bool,
) -> tuple[List[str], List[str], List[str], str, str, str, bool]:
    content_index = target["content_index"]
    capabilities = content_index.get("capabilities", [])
    if not isinstance(capabilities, list):
        raise PlatformError(
            "Content index must contain a capabilities array.",
            code="E_ASSET_LIFECYCLE_INVALID_ASSET",
            reason="content_index_capabilities",
        )

    path = repo_root / _CONTENT_INDEX_PATH
    capability_id = str(target["capability_id"]).strip()
    release_entry = target.get("release_entry")
    contract = _load_or_infer_release_contract(
        repo_root=repo_root,
        capability_id=capability_id,
        release_entry=release_entry if isinstance(release_entry, dict) else None,
        payload=payload,
        operation=operation,
    )
    bundle_contract_rel = _release_contract_rel_path(capability_id)
    built_bundle_dir = ""
    published_now = False
    changed_files: List[str] = []
    changed_source_files: List[str] = []
    if operation == "create":
        contract = _merge_release_contract(contract, payload=payload, fallback_capability=capability_id, version_override="")
        changed_source_files = _materialize_contract_source_files(repo_root=repo_root, contract=contract)
        _write_release_contract(repo_root=repo_root, capability_id=contract["capability"], contract=contract)
        new_entry = _build_release_entry_from_contract(contract)
        if _find_release_capability(content_index, new_entry["capability"]):
            raise PlatformError(
                f"Release-backed capability '{new_entry['capability']}' already exists in content-index.json.",
                code="E_ASSET_LIFECYCLE_TARGET_EXISTS",
                reason=new_entry["capability"],
            )
        capabilities.append(new_entry)
        _write_json_payload(path, content_index)
        changed_files = [_CONTENT_INDEX_PATH.as_posix(), bundle_contract_rel, *changed_source_files]
        built_bundle_dir = _build_release_bundle(repo_root=repo_root, contract=contract)
        implications = _release_implications_for_entry(new_entry, operation=operation, built_bundle_dir=built_bundle_dir, published=publish)
        if publish:
            _publish_release_bundle(contract=contract, bundle_dir=Path(built_bundle_dir))
            _publish_content_index(repo_root=repo_root, release_repo=str(contract["repo"]).strip())
            published_now = True
        return (
            _dedupe_paths(changed_files),
            [],
            implications,
            f"Created release-backed capability '{new_entry['capability']}' with a publishable asset bundle.",
            bundle_contract_rel,
            built_bundle_dir,
            published_now,
        )

    if not isinstance(release_entry, dict):
        raise PlatformError(
            f"Release-backed capability '{capability_id}' was not found in content-index.json.",
            code="E_ASSET_LIFECYCLE_TARGET_MISSING",
            reason=capability_id,
        )

    if operation == "remove":
        capabilities[:] = [item for item in capabilities if not (isinstance(item, dict) and str(item.get("capability", "")).strip() == capability_id)]
        _write_json_payload(path, content_index)
        _delete_release_contract(repo_root=repo_root, capability_id=capability_id)
        implications = [
            "The capability was removed from the active content index.",
            "Any future sync visibility now depends on removing or superseding the published release artifacts separately.",
        ]
        if publish:
            release_repo = str(contract.get("repo", release_entry.get("repo", ""))).strip()
            if release_repo:
                _publish_content_index(repo_root=repo_root, release_repo=release_repo)
                published_now = True
        return (
            [_CONTENT_INDEX_PATH.as_posix(), bundle_contract_rel],
            [],
            implications,
            f"Removed release-backed capability '{capability_id}' from content-index.json.",
            bundle_contract_rel,
            "",
            published_now,
        )

    if operation == "update_versioned_asset":
        normalized_version = _normalize_semver(new_version)
        contract = _merge_release_contract(contract, payload=payload, fallback_capability=capability_id, version_override=normalized_version)
        changed_source_files = _materialize_contract_source_files(repo_root=repo_root, contract=contract)
        _write_release_contract(repo_root=repo_root, capability_id=capability_id, contract=contract)
        normalized_entry = _build_release_entry_from_contract(contract)
        release_entry.clear()
        release_entry.update(normalized_entry)
        _write_json_payload(path, content_index)
        changed_files = [_CONTENT_INDEX_PATH.as_posix(), bundle_contract_rel, *changed_source_files]
        built_bundle_dir = _build_release_bundle(repo_root=repo_root, contract=contract)
        implications = _release_implications_for_entry(release_entry, operation=operation, built_bundle_dir=built_bundle_dir, published=publish)
        if publish:
            _publish_release_bundle(contract=contract, bundle_dir=Path(built_bundle_dir))
            _publish_content_index(repo_root=repo_root, release_repo=str(contract["repo"]).strip())
            published_now = True
        return (
            _dedupe_paths(changed_files),
            [],
            implications,
            f"Updated release-backed capability '{capability_id}' to version {normalized_version}.",
            bundle_contract_rel,
            built_bundle_dir,
            published_now,
        )

    contract = _merge_release_contract(contract, payload=payload, fallback_capability=capability_id, version_override="")
    changed_source_files = _materialize_contract_source_files(repo_root=repo_root, contract=contract)
    _write_release_contract(repo_root=repo_root, capability_id=capability_id, contract=contract)
    normalized_entry = _build_release_entry_from_contract(contract)
    release_entry.clear()
    release_entry.update(normalized_entry)
    _write_json_payload(path, content_index)
    built_bundle_dir = _build_release_bundle(repo_root=repo_root, contract=contract)
    changed_files = [_CONTENT_INDEX_PATH.as_posix(), bundle_contract_rel, *changed_source_files]
    implications = _release_implications_for_entry(release_entry, operation=operation, built_bundle_dir=built_bundle_dir, published=publish)
    if publish:
        _publish_release_bundle(contract=contract, bundle_dir=Path(built_bundle_dir))
        _publish_content_index(repo_root=repo_root, release_repo=str(contract["repo"]).strip())
        published_now = True
    return (
        _dedupe_paths(changed_files),
        [],
        implications,
        f"Revised release-backed capability '{capability_id}' and rebuilt its publishable asset bundle.",
        bundle_contract_rel,
        built_bundle_dir,
        published_now,
    )


def _load_or_infer_release_contract(
    *,
    repo_root: Path,
    capability_id: str,
    release_entry: Dict[str, Any] | None,
    payload: Dict[str, Any],
    operation: str,
) -> Dict[str, Any]:
    contract_path = repo_root / _release_contract_rel_path(capability_id)
    if contract_path.exists():
        return load_orchestrate_json_file(contract_path)
    if release_entry:
        inferred = _infer_known_release_contract(repo_root=repo_root, capability_id=capability_id, release_entry=release_entry)
        if inferred:
            return inferred
    if operation == "create":
        return _empty_release_contract(capability_id, payload)
    if payload and payload.get("files"):
        return _empty_release_contract(capability_id, payload)
    raise PlatformError(
        f"Release-backed capability '{capability_id}' has no repo-side asset bundle contract yet. Provide bundle details in --payload-file or add a catalog contract first.",
        code="E_ASSET_LIFECYCLE_CONTRACT_MISSING",
        reason=capability_id,
    )


def _empty_release_contract(capability_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    capability = str(payload.get("capability", "")).strip() or capability_id
    return {
        "schema_version": "1.0",
        "provider_family": "github_release",
        "capability": capability,
        "repo": str(payload.get("repo", "")).strip(),
        "version": str(payload.get("version", "")).strip(),
        "tag": str(payload.get("tag", "")).strip(),
        "manifest_asset": str(payload.get("manifest_asset", "content-manifest.json")).strip() or "content-manifest.json",
        "package_type": str(payload.get("package_type", "file_bundle")).strip() or "file_bundle",
        "target_type": str(payload.get("target_type", "filesystem")).strip() or "filesystem",
        "category": str(payload.get("category", "general")).strip() or "general",
        "release_asset_dir": str(payload.get("release_asset_dir", _slugify_capability(capability))).strip() or _slugify_capability(capability),
        "policy": {
            "allow_update_existing_files": bool(payload.get("allow_update_existing_files", True)),
            "allow_new_files_on_update": bool(payload.get("allow_new_files_on_update", False)),
            "allow_install_if_missing": bool(payload.get("allow_install_if_missing", False)),
            "min_cli_version": str(payload.get("min_cli_version", "0.1.0")).strip() or "0.1.0",
        },
        "bundle": {
            "target_root_key": str(payload.get("target_root_key", "")).strip(),
            "target_subdir": str(payload.get("target_subdir", "")).strip(),
            "files": list(payload.get("files", [])) if isinstance(payload.get("files"), list) else [],
        },
    }


def _infer_known_release_contract(*, repo_root: Path, capability_id: str, release_entry: Dict[str, Any]) -> Dict[str, Any] | None:
    common = {
        "schema_version": "1.0",
        "provider_family": "github_release",
        "capability": capability_id,
        "repo": str(release_entry.get("repo", "")).strip(),
        "version": str(release_entry.get("version", "")).strip(),
        "tag": str(release_entry.get("tag", "")).strip(),
        "manifest_asset": str(release_entry.get("manifest_asset", "content-manifest.json")).strip() or "content-manifest.json",
        "package_type": "file_bundle",
        "target_type": str(release_entry.get("target_type", "filesystem")).strip() or "filesystem",
        "category": str(release_entry.get("category", "general")).strip() or "general",
        "policy": {
            "allow_update_existing_files": bool(release_entry.get("allow_update_existing_files", True)),
            "allow_new_files_on_update": bool(release_entry.get("allow_new_files_on_update", False)),
            "allow_install_if_missing": bool(release_entry.get("allow_install_if_missing", False)),
            "min_cli_version": str(release_entry.get("min_cli_version", "0.1.0")).strip() or "0.1.0",
        },
    }
    if capability_id == "ghdp-team-toolset":
        return {
            **common,
            "release_asset_dir": "team_toolset",
            "bundle": {
                "target_root_key": "ghdp_user_root",
                "target_subdir": "policies",
                "files": [
                    {
                        "source_path": "platform-cli/release-assets/team_toolset/toolset.json",
                        "asset_name": "toolset.json",
                        "target_path": "team-toolset.managed.json",
                    }
                ],
            },
        }
    if capability_id == "repo-ready-assets":
        repo_ready_root = repo_root / "platform-cli" / "release-assets" / "repo_ready"
        files = []
        if repo_ready_root.exists():
            for source_path in sorted(path for path in repo_ready_root.rglob("*") if path.is_file()):
                rel = source_path.relative_to(repo_ready_root).as_posix()
                files.append(
                    {
                        "source_path": str(source_path.relative_to(repo_root)).replace("\\", "/"),
                        "asset_name": "__".join(Path(rel).parts),
                        "target_path": rel,
                    }
                )
        return {
            **common,
            "release_asset_dir": "repo_ready",
            "bundle": {
                "target_root_key": "ghdp_user_root",
                "target_subdir": "repo_ready/base",
                "files": files,
            },
        }
    if capability_id == "claude-athena-workgroup-map":
        return {
            **common,
            "release_asset_dir": "claude_athena_workgroup_map",
            "bundle": {
                "target_root_key": "ghdp_user_root",
                "target_subdir": "policies",
                "files": [
                    {
                        "source_path": "platform-cli/src/platform_cli/resources/claude/athena-workgroup-map.json",
                        "asset_name": "athena-workgroup-map.json",
                        "target_path": "claude-athena-workgroup-map.managed.json",
                    }
                ],
            },
        }
    if capability_id == "background-scheduler":
        return {
            **common,
            "release_asset_dir": "background_scheduler",
            "bundle": {
                "target_root_key": "ghdp_user_root",
                "target_subdir": "capabilities/background-scheduler",
                "files": [
                    {
                        "source_path": "platform-cli/src/platform_cli/resources/scheduler/bootstrap/capability.json",
                        "asset_name": "capability.json",
                        "target_path": "capability.json",
                    },
                    {
                        "source_path": "platform-cli/src/platform_cli/resources/scheduler/bootstrap/defaults.json",
                        "asset_name": "defaults.json",
                        "target_path": "defaults.json",
                    },
                    {
                        "source_path": "platform-cli/src/platform_cli/resources/scheduler/bootstrap/tasks.json",
                        "asset_name": "tasks.json",
                        "target_path": "tasks.json",
                    },
                ],
            },
        }
    if capability_id == "ghdp-admin-policy":
        return {
            **common,
            "release_asset_dir": "ghdp_admin_policy",
            "bundle": {
                "target_root_key": "ghdp_user_root",
                "target_subdir": "policies",
                "files": [
                    {
                        "source_path": "platform-cli/src/platform_cli/resources/policy/access_policy.json",
                        "asset_name": "access_policy.json",
                        "target_path": "access_policy.json",
                    },
                    {
                        "source_path": "platform-cli/src/platform_cli/resources/policy/team-policy.managed.json",
                        "asset_name": "team-policy.managed.json",
                        "target_path": "team-policy.managed.json",
                    },
                ],
            },
        }
    if capability_id == "marketplace-skill-allowlist":
        return {
            **common,
            "release_asset_dir": "marketplace_skill_allowlist",
            "bundle": {
                "target_root_key": "ghdp_user_root",
                "target_subdir": "policies",
                "files": [
                    {
                        "source_path": ".ghdp/capability-allowlist.json",
                        "asset_name": "capability-allowlist.managed.json",
                        "target_path": "capability-allowlist.managed.json",
                    }
                ],
            },
        }
    return None


def _merge_release_contract(
    contract: Dict[str, Any],
    *,
    payload: Dict[str, Any],
    fallback_capability: str,
    version_override: str,
) -> Dict[str, Any]:
    merged = json.loads(json.dumps(contract))
    merged["capability"] = str(payload.get("capability", merged.get("capability", fallback_capability))).strip() or fallback_capability
    raw_version = version_override or str(payload.get("version", merged.get("version", ""))).strip()
    merged["version"] = _normalize_semver(raw_version)
    merged["repo"] = str(payload.get("repo", merged.get("repo", ""))).strip()
    if not merged["repo"]:
        raise PlatformError(
            "Release-backed asset lifecycle requires a repo owner/name for publication.",
            code="E_ASSET_LIFECYCLE_BAD_ARGS",
            reason="release_repo",
        )
    existing_tag = str(merged.get("tag", "")).strip()
    payload_tag = str(payload.get("tag", "")).strip()
    merged["tag"] = payload_tag or _retag_for_version(existing_tag or f"{_slugify_capability(merged['capability'])}-v{merged['version']}", merged["version"])
    merged["manifest_asset"] = str(payload.get("manifest_asset", merged.get("manifest_asset", "content-manifest.json"))).strip() or "content-manifest.json"
    merged["package_type"] = str(payload.get("package_type", merged.get("package_type", "file_bundle"))).strip() or "file_bundle"
    merged["target_type"] = str(payload.get("target_type", merged.get("target_type", "filesystem"))).strip() or "filesystem"
    merged["category"] = str(payload.get("category", merged.get("category", "general"))).strip() or "general"
    merged["release_asset_dir"] = str(payload.get("release_asset_dir", merged.get("release_asset_dir", _slugify_capability(merged["capability"])))).strip() or _slugify_capability(merged["capability"])
    policy = dict(merged.get("policy", {}))
    for field_name in ("allow_update_existing_files", "allow_new_files_on_update", "allow_install_if_missing"):
        if field_name in payload:
            policy[field_name] = bool(payload[field_name])
    policy["allow_update_existing_files"] = bool(policy.get("allow_update_existing_files", True))
    policy["allow_new_files_on_update"] = bool(policy.get("allow_new_files_on_update", False))
    policy["allow_install_if_missing"] = bool(policy.get("allow_install_if_missing", False))
    policy["min_cli_version"] = str(payload.get("min_cli_version", policy.get("min_cli_version", "0.1.0"))).strip() or "0.1.0"
    merged["policy"] = policy

    bundle = dict(merged.get("bundle", {}))
    bundle["target_root_key"] = str(payload.get("target_root_key", bundle.get("target_root_key", ""))).strip()
    bundle["target_subdir"] = str(payload.get("target_subdir", bundle.get("target_subdir", ""))).strip()
    if "files" in payload:
        if not isinstance(payload["files"], list):
            raise PlatformError(
                "Release-backed asset bundle payload field 'files' must be a list.",
                code="E_ASSET_LIFECYCLE_BAD_ARGS",
                reason="bundle_files",
            )
        bundle["files"] = payload["files"]
    files = bundle.get("files", [])
    if not bundle["target_root_key"] or not bundle["target_subdir"] or not isinstance(files, list) or not files:
        raise PlatformError(
            "Release-backed asset lifecycle requires bundle target_root_key, target_subdir, and at least one file mapping.",
            code="E_ASSET_LIFECYCLE_BAD_ARGS",
            reason="release_bundle",
        )
    merged["bundle"] = bundle
    return merged


def _release_contract_rel_path(capability_id: str) -> str:
    return (_RELEASE_CATALOG_ROOT / f"{_slugify_capability(capability_id)}.json").as_posix()


def _write_release_contract(*, repo_root: Path, capability_id: str, contract: Dict[str, Any]) -> None:
    _write_json_payload(repo_root / _release_contract_rel_path(capability_id), contract)


def _delete_release_contract(*, repo_root: Path, capability_id: str) -> None:
    path = repo_root / _release_contract_rel_path(capability_id)
    if path.exists():
        path.unlink()


def _slugify_capability(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    return normalized or "asset"


def _materialize_contract_source_files(*, repo_root: Path, contract: Dict[str, Any]) -> List[str]:
    changed: List[str] = []
    files = contract.get("bundle", {}).get("files", [])
    if not isinstance(files, list):
        return changed
    for item in files:
        if not isinstance(item, dict):
            raise PlatformError(
                "Release-backed asset bundle file entries must be objects.",
                code="E_ASSET_LIFECYCLE_BAD_ARGS",
                reason="bundle_file_entry",
            )
        source_path = str(item.get("source_path", "")).strip().replace("\\", "/")
        if not source_path:
            raise PlatformError(
                "Release-backed asset bundle file entries require source_path.",
                code="E_ASSET_LIFECYCLE_BAD_ARGS",
                reason="bundle_source_path",
            )
        repo_path = (repo_root / source_path).resolve()
        if repo_root.resolve() not in repo_path.parents and repo_path != repo_root.resolve():
            raise PlatformError(
                f"Release-backed asset source path '{source_path}' escapes the repo root.",
                code="E_ASSET_LIFECYCLE_BAD_ARGS",
                reason="bundle_source_path_escape",
            )
        if "inline_json" in item:
            repo_path.parent.mkdir(parents=True, exist_ok=True)
            new_text = json.dumps(item["inline_json"], indent=2) + "\n"
            old_text = repo_path.read_text(encoding="utf-8-sig") if repo_path.exists() else None
            if old_text != new_text:
                repo_path.write_text(new_text, encoding="utf-8")
                changed.append(source_path)
        elif "inline_text" in item:
            repo_path.parent.mkdir(parents=True, exist_ok=True)
            new_text = str(item.get("inline_text", ""))
            old_text = repo_path.read_text(encoding="utf-8-sig") if repo_path.exists() else None
            if old_text != new_text:
                repo_path.write_text(new_text, encoding="utf-8")
                changed.append(source_path)
        elif "inline_base64" in item:
            repo_path.parent.mkdir(parents=True, exist_ok=True)
            new_bytes = base64.b64decode(str(item.get("inline_base64", "")))
            old_bytes = repo_path.read_bytes() if repo_path.exists() else None
            if old_bytes != new_bytes:
                repo_path.write_bytes(new_bytes)
                changed.append(source_path)
        if not repo_path.exists():
            raise PlatformError(
                f"Release-backed asset source file '{source_path}' does not exist.",
                code="E_ASSET_LIFECYCLE_TARGET_MISSING",
                reason=source_path,
            )
    return changed


def _build_release_entry_from_contract(contract: Dict[str, Any]) -> Dict[str, Any]:
    capability = str(contract.get("capability", "")).strip()
    version = _normalize_semver(str(contract.get("version", "")).strip())
    repo = str(contract.get("repo", "")).strip()
    tag = str(contract.get("tag", "")).strip()
    manifest_asset = str(contract.get("manifest_asset", "content-manifest.json")).strip() or "content-manifest.json"
    target_type = str(contract.get("target_type", "filesystem")).strip() or "filesystem"
    category = str(contract.get("category", "general")).strip() or "general"
    package_type = str(contract.get("package_type", "file_bundle")).strip() or "file_bundle"
    if not capability or not repo or not tag:
        raise PlatformError(
            "Release-backed asset contract is missing capability, repo, or tag metadata.",
            code="E_ASSET_LIFECYCLE_BAD_ARGS",
            reason="release_contract",
        )
    policy = dict(contract.get("policy", {}))
    return {
        "capability": capability,
        "version": version,
        "provider": "github_release",
        "source": {
            "repo": repo,
            "tag": tag,
            "manifest_asset": manifest_asset,
        },
        "repo": repo,
        "tag": tag,
        "manifest_asset": manifest_asset,
        "package_type": package_type,
        "target_type": target_type,
        "category": category,
        "policy": {
            "allow_update_existing_files": bool(policy.get("allow_update_existing_files", True)),
            "allow_new_files_on_update": bool(policy.get("allow_new_files_on_update", False)),
            "allow_install_if_missing": bool(policy.get("allow_install_if_missing", False)),
            "min_cli_version": str(policy.get("min_cli_version", "0.1.0")).strip() or "0.1.0",
        },
        "allow_update_existing_files": bool(policy.get("allow_update_existing_files", True)),
        "allow_new_files_on_update": bool(policy.get("allow_new_files_on_update", False)),
        "allow_install_if_missing": bool(policy.get("allow_install_if_missing", False)),
        "min_cli_version": str(policy.get("min_cli_version", "0.1.0")).strip() or "0.1.0",
    }


def _build_release_bundle(*, repo_root: Path, contract: Dict[str, Any]) -> str:
    bundle = dict(contract.get("bundle", {}))
    files = bundle.get("files", [])
    if not isinstance(files, list) or not files:
        raise PlatformError(
            "Release-backed asset bundle has no files to publish.",
            code="E_ASSET_LIFECYCLE_BAD_ARGS",
            reason="bundle_files",
        )
    release_asset_dir = str(contract.get("release_asset_dir", _slugify_capability(str(contract.get("capability", ""))))).strip() or _slugify_capability(str(contract.get("capability", "")))
    output_dir = repo_root / "platform-cli" / "dist" / "asset_lifecycle" / str(contract.get("tag", "bundle"))
    if output_dir.exists():
        for item in output_dir.rglob("*"):
            if item.is_file():
                item.unlink()
        for item in sorted((p for p in output_dir.rglob("*") if p.is_dir()), reverse=True):
            if item.exists():
                item.rmdir()
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_files: List[Dict[str, str]] = []
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise PlatformError(
                "Release-backed asset bundle file entries must be objects.",
                code="E_ASSET_LIFECYCLE_BAD_ARGS",
                reason="bundle_file_entry",
            )
        source_path = str(item.get("source_path", "")).strip().replace("\\", "/")
        target_path = str(item.get("target_path", "")).strip().replace("\\", "/")
        if not source_path or not target_path:
            raise PlatformError(
                "Release-backed asset bundle file entries require source_path and target_path.",
                code="E_ASSET_LIFECYCLE_BAD_ARGS",
                reason="bundle_file_mapping",
            )
        asset_name = str(item.get("asset_name", "")).strip() or Path(source_path).name or f"asset_{index}"
        source_file = repo_root / source_path
        if not source_file.exists():
            raise PlatformError(
                f"Release-backed asset source file '{source_path}' does not exist.",
                code="E_ASSET_LIFECYCLE_TARGET_MISSING",
                reason=source_path,
            )
        (output_dir / asset_name).write_bytes(source_file.read_bytes())
        manifest_files.append({"asset_name": asset_name, "target_path": target_path})

    manifest = {
        "capability": str(contract.get("capability", "")).strip(),
        "version": _normalize_semver(str(contract.get("version", "")).strip()),
        "tag": str(contract.get("tag", "")).strip(),
        "target_root_key": str(bundle.get("target_root_key", "")).strip(),
        "target_subdir": str(bundle.get("target_subdir", "")).strip(),
        "release_asset_dir": release_asset_dir,
        "files": manifest_files,
    }
    (output_dir / "content-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return output_dir.as_posix()


def _retag_for_version(existing_tag: str, new_version: str) -> str:
    match = re.search(r"v?\d+\.\d+\.\d+", existing_tag)
    replacement = f"v{new_version}"
    if match:
        return existing_tag[: match.start()] + replacement + existing_tag[match.end() :]
    return replacement


def _release_implications_for_entry(entry: Dict[str, Any], *, operation: str, built_bundle_dir: str, published: bool) -> List[str]:
    capability = str(entry.get("capability", "")).strip()
    return [
        f"Release-backed capability: `{capability}`.",
        f"Content index source updated at `{_CONTENT_INDEX_PATH.as_posix()}`.",
        f"Publishable release bundle built at `{built_bundle_dir}`." if built_bundle_dir else "No bundle directory was built.",
        "If payload files or content-manifest.json changed, the matching GitHub release asset set must be published together.",
        "Any active content-index-latest publication must be refreshed after this repo-side change.",
        "This run published the release bundle and refreshed content-index-latest." if published else "This run prepared the bundle locally; publish it with --publish when you are ready.",
        f"Operation type: `{operation}`.",
    ]


def _publish_release_bundle(*, contract: Dict[str, Any], bundle_dir: Path) -> None:
    repo = str(contract.get("repo", "")).strip()
    tag = str(contract.get("tag", "")).strip()
    if not repo or not tag:
        raise PlatformError(
            "Release-backed asset publication requires repo and tag metadata.",
            code="E_ASSET_LIFECYCLE_BAD_ARGS",
            reason="release_publish",
        )
    notes = f"GHDP asset lifecycle publication for {contract.get('capability', '')}."
    with tempfile.TemporaryDirectory(prefix="ghdp_asset_release_") as tmpdir:
        notes_path = Path(tmpdir) / "notes.md"
        notes_path.write_text(notes + "\n", encoding="utf-8")
        view = run_cmd(["gh", "release", "view", tag, "--repo", repo], check=False, capture=True, env=gh_subprocess_env())
        if view.returncode != 0:
            run_cmd(
                [
                    "gh",
                    "release",
                    "create",
                    tag,
                    "--repo",
                    repo,
                    "--title",
                    tag,
                    "--notes-file",
                    str(notes_path),
                ],
                check=True,
                capture=True,
                env=gh_subprocess_env(),
            )
        asset_paths = [str(path) for path in sorted(bundle_dir.iterdir()) if path.is_file()]
        run_cmd(
            ["gh", "release", "upload", tag, "--repo", repo, *asset_paths, "--clobber"],
            check=True,
            capture=True,
            env=gh_subprocess_env(),
        )


def _publish_content_index(*, repo_root: Path, release_repo: str) -> None:
    content_index_path = repo_root / _CONTENT_INDEX_PATH
    if not content_index_path.exists():
        raise PlatformError(
            "Content index source is missing and cannot be refreshed.",
            code="E_ASSET_LIFECYCLE_TARGET_MISSING",
            reason=_CONTENT_INDEX_PATH.as_posix(),
        )
    with tempfile.TemporaryDirectory(prefix="ghdp_content_index_release_") as tmpdir:
        temp_dir = Path(tmpdir)
        temp_asset = temp_dir / _CONTENT_INDEX_RELEASE_ASSET
        temp_asset.write_text(content_index_path.read_text(encoding="utf-8-sig"), encoding="utf-8")
        view = run_cmd(
            ["gh", "release", "view", _CONTENT_INDEX_RELEASE_TAG, "--repo", release_repo],
            check=False,
            capture=True,
            env=gh_subprocess_env(),
        )
        if view.returncode != 0:
            notes_path = temp_dir / "notes.md"
            notes_path.write_text("GHDP content index refresh.\n", encoding="utf-8")
            run_cmd(
                [
                    "gh",
                    "release",
                    "create",
                    _CONTENT_INDEX_RELEASE_TAG,
                    "--repo",
                    release_repo,
                    "--title",
                    _CONTENT_INDEX_RELEASE_TAG,
                    "--notes-file",
                    str(notes_path),
                ],
                check=True,
                capture=True,
                env=gh_subprocess_env(),
            )
        run_cmd(
            ["gh", "release", "upload", _CONTENT_INDEX_RELEASE_TAG, "--repo", release_repo, str(temp_asset), "--clobber"],
            check=True,
            capture=True,
            env=gh_subprocess_env(),
        )


def _dedupe_paths(values: Sequence[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for item in values:
        normalized = str(item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _apply_marketplace_operation(
    *,
    repo_root: Path,
    target: Dict[str, Any],
    operation: str,
    payload: Dict[str, Any],
) -> tuple[List[str], List[str], List[str], str]:
    allowlist = target["allowlist"]
    sources = allowlist.get("sources", {})
    if not isinstance(sources, dict):
        raise PlatformError(
            "Capability allowlist must contain a sources object.",
            code="E_ASSET_LIFECYCLE_INVALID_ASSET",
            reason="allowlist_sources",
        )
    path = repo_root / _ALLOWLIST_PATH
    source_name = str(target.get("source_name", "skill_marketplace")).strip() or "skill_marketplace"
    source_payload = sources.setdefault(source_name, {})
    if not isinstance(source_payload, dict):
        raise PlatformError(
            f"Capability allowlist source '{source_name}' must be an object.",
            code="E_ASSET_LIFECYCLE_INVALID_ASSET",
            reason=source_name,
        )
    targets = source_payload.setdefault("targets", {})
    if not isinstance(targets, dict):
        source_payload["targets"] = {}
        targets = source_payload["targets"]

    capability_id = str(target["capability_id"]).strip()
    if operation == "create":
        entry = _build_marketplace_entry_from_payload(payload, fallback_capability=capability_id)
        target_name = str(payload.get("target_name", "")).strip()
        if not target_name:
            raise PlatformError(
                "Marketplace capability create requires payload field target_name (for example codex or claude).",
                code="E_ASSET_LIFECYCLE_BAD_ARGS",
                reason="marketplace_create_payload",
            )
        target_payload = targets.setdefault(target_name, {})
        if not isinstance(target_payload, dict):
            raise PlatformError(
                f"Capability allowlist target '{target_name}' must be an object.",
                code="E_ASSET_LIFECYCLE_INVALID_ASSET",
                reason=target_name,
            )
        entries = target_payload.setdefault("entries", [])
        if not isinstance(entries, list):
            target_payload["entries"] = []
            entries = target_payload["entries"]
        if _find_marketplace_capability(allowlist, entry["capability"]):
            raise PlatformError(
                f"Marketplace capability '{entry['capability']}' already exists in the allowlist.",
                code="E_ASSET_LIFECYCLE_TARGET_EXISTS",
                reason=entry["capability"],
            )
        entries.append(entry)
        _write_json_payload(path, allowlist)
        implications = _marketplace_implications(entry, operation=operation)
        return ([_ALLOWLIST_PATH.as_posix()], [], implications, f"Created marketplace capability '{entry['capability']}' in the repo allowlist.")

    entry_index = target.get("entry_index")
    target_name = str(target.get("target_name", "")).strip()
    target_payload = targets.get(target_name)
    entries = target_payload.get("entries", []) if isinstance(target_payload, dict) else []
    if not isinstance(entries, list) or not isinstance(entry_index, int) or entry_index >= len(entries):
        raise PlatformError(
            f"Marketplace capability '{capability_id}' was not found in the repo allowlist.",
            code="E_ASSET_LIFECYCLE_TARGET_MISSING",
            reason=capability_id,
        )

    entry = entries[entry_index]
    if operation == "remove":
        del entries[entry_index]
        _write_json_payload(path, allowlist)
        implications = [
            f"Marketplace capability `{capability_id}` was removed from `{_ALLOWLIST_PATH.as_posix()}`.",
            "Sync visibility now depends on this repo-owned allowlist instead of the previously generated remote policy.",
        ]
        return ([_ALLOWLIST_PATH.as_posix()], [], implications, f"Removed marketplace capability '{capability_id}' from the repo allowlist.")

    if operation == "update_versioned_asset":
        raise PlatformError(
            "Marketplace capability versions are commit-derived. Use --operation revise with a payload that changes source repo/branch/path metadata instead.",
            code="E_ASSET_LIFECYCLE_OPERATION_UNSUPPORTED",
            reason=capability_id,
        )

    new_target_name = str(payload.get("target_name", target_name)).strip() or target_name
    revised = _build_marketplace_entry_from_payload({**entry, **payload}, fallback_capability=capability_id)
    if new_target_name != target_name:
        del entries[entry_index]
        destination_target = targets.setdefault(new_target_name, {})
        if not isinstance(destination_target, dict):
            raise PlatformError(
                f"Capability allowlist target '{new_target_name}' must be an object.",
                code="E_ASSET_LIFECYCLE_INVALID_ASSET",
                reason=new_target_name,
            )
        destination_entries = destination_target.setdefault("entries", [])
        if not isinstance(destination_entries, list):
            destination_target["entries"] = []
            destination_entries = destination_target["entries"]
        destination_entries.append(revised)
    else:
        entries[entry_index] = revised
    _write_json_payload(path, allowlist)
    implications = _marketplace_implications(revised, operation=operation)
    return ([_ALLOWLIST_PATH.as_posix()], [], implications, f"Revised marketplace capability '{capability_id}' in the repo allowlist.")


def _build_marketplace_entry_from_payload(payload: Dict[str, Any], *, fallback_capability: str) -> Dict[str, Any]:
    entry = {
        "capability": str(payload.get("capability", "")).strip() or fallback_capability,
        "install_unit_type": str(payload.get("install_unit_type", "")).strip().lower(),
        "source_path": str(payload.get("source_path", "")).strip().replace("\\", "/").strip("/"),
        "target_type": str(payload.get("target_type", "")).strip(),
        "target_root_key": str(payload.get("target_root_key", "")).strip(),
        "target_subdir": str(payload.get("target_subdir", "")).strip(),
        "category": str(payload.get("category", "")).strip(),
    }
    missing = [name for name, value in entry.items() if not value]
    if missing:
        raise PlatformError(
            f"Marketplace capability payload is missing required field(s): {', '.join(missing)}.",
            code="E_ASSET_LIFECYCLE_BAD_ARGS",
            reason="marketplace_payload",
        )
    return entry


def _marketplace_implications(entry: Dict[str, Any], *, operation: str) -> List[str]:
    capability = str(entry.get("capability", "")).strip()
    return [
        f"Marketplace capability: `{capability}`.",
        f"Repo-owned allowlist source updated at `{_ALLOWLIST_PATH.as_posix()}`.",
        "The effective installed version remains commit-derived from the configured marketplace repo/branch.",
        "If target repo or branch metadata changed, the next sync inventory will resolve a new commit SHA automatically.",
        f"Operation type: `{operation}`.",
    ]


def _capability_inventory(repo_root: Path) -> List[Dict[str, Any]]:
    resolver = build_sync_root_resolver(repo_root=repo_root)
    status = list_sync_status(resolve_root_key=resolver, scope_kind="repo", scope_ref=str(repo_root))
    inventory: List[Dict[str, Any]] = []
    for item in status["capabilities"]:
        inventory.append(
            {
                "capability": str(item.get("capability", "")).strip(),
                "provider": str(item.get("provider", "")).strip(),
                "target_type": str(item.get("target_type", "")).strip(),
                "category": str(item.get("category", "")).strip(),
                "latest_tag": str(item.get("latest_tag", "")).strip(),
                "allow_install_if_missing": bool(item.get("allow_install_if_missing", False)),
                "allow_update_existing_files": bool(item.get("allow_update_existing_files", False)),
                "allow_new_files_on_update": bool(item.get("allow_new_files_on_update", False)),
            }
        )
    return inventory


def _normalize_semver(value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise PlatformError(
            "Asset lifecycle version revision requires --new-version.",
            code="E_ASSET_LIFECYCLE_BAD_ARGS",
            reason="new_version",
        )
    parts = normalized.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise PlatformError(
            f"Version '{value}' is not a semantic version in x.y.z form.",
            code="E_ASSET_LIFECYCLE_BAD_ARGS",
            reason=normalized,
        )
    return normalized


def _revise_toolset_codex_version(repo_root: Path, version: str) -> tuple[List[str], List[str]]:
    relative_paths = [
        Path("platform-cli/src/platform_cli/resources/manifests/toolset.json"),
        Path("platform-cli/release-assets/team_toolset/toolset.json"),
    ]
    changed_files: List[str] = []
    changed_teams: set[str] = set()
    for rel_path in relative_paths:
        path = repo_root / rel_path
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        teams = payload.get("teams", {})
        if not isinstance(teams, dict):
            raise PlatformError(
                f"Toolset asset '{rel_path.as_posix()}' does not contain a valid teams object.",
                code="E_ASSET_LIFECYCLE_INVALID_ASSET",
                reason=rel_path.as_posix(),
            )
        file_changed = False
        for team_name, team_payload in teams.items():
            if not isinstance(team_payload, dict):
                continue
            tools = team_payload.get("tools", {})
            if not isinstance(tools, dict):
                continue
            codex_payload = tools.get("codex")
            if not isinstance(codex_payload, dict):
                continue
            if str(codex_payload.get("version", "")).strip() != version:
                codex_payload["version"] = version
                file_changed = True
                changed_teams.add(str(team_name))
        if file_changed:
            _write_json_payload(path, payload)
            changed_files.append(rel_path.as_posix())
    if not changed_files:
        changed_files = [item.as_posix() for item in relative_paths]
        for rel_path in relative_paths:
            payload = json.loads((repo_root / rel_path).read_text(encoding="utf-8-sig"))
            changed_teams.update(payload.get("teams", {}).keys() if isinstance(payload.get("teams", {}), dict) else [])
    return changed_files, sorted(changed_teams)


def _special_release_implications(version: str) -> List[str]:
    return [
        "The packaged and release-backed team toolset sources were revised together.",
        f"The Codex minimum version is now `{version}` across all current team entries.",
        "If the managed `ghdp-team-toolset` capability is republished, the packaged and synced sources should remain ownership-aligned.",
    ]


def _persist_asset_artifacts(
    *,
    context: ActiveRunContext | None,
    operation: str,
    asset_target: str,
    inventory: Sequence[Dict[str, Any]],
    changed_files: Sequence[str],
    changed_teams: Sequence[str],
    source_files: Sequence[str],
    release_implications: Sequence[str],
    plugin_contract: Dict[str, Any],
    agent_contract: Dict[str, Any],
    message: str,
) -> None:
    if context is None:
        return

    inventory_payload = {
        "schema_version": "1.0",
        "operation": operation,
        "asset_target": asset_target,
        "inventory": list(inventory),
    }
    write_json(context.run_root / "asset_inventory.json", inventory_payload)
    write_markdown(
        context.run_root / "asset_operation_plan.md",
        [
            "# Asset Operation Plan",
            "",
            f"- Operation: `{operation}`",
            f"- Asset target: `{asset_target}`",
            f"- Agent: `{agent_contract.get('id', 'asset-lifecycle')}`",
            "",
            "## Allowed Skills",
            *[f"- `{item}`" for item in agent_contract.get("allowed_skills", [])],
            "",
            "## Allowed Plugins",
            *[f"- `{item}`" for item in agent_contract.get("allowed_plugins", [])],
            "",
            "## Known Target Contract",
            *[f"- `{item}`" for item in plugin_contract.get("setup_contract", [])],
            "",
            "## Source Files",
            *([f"- `{item}`" for item in source_files] or ["- `(none)`"]),
            "",
            "## Planned File Touches",
            *([f"- `{item}`" for item in changed_files] or ["- `(inventory only)`"]),
            "",
        ],
    )
    write_json(
        context.run_root / "asset_operation_result.json",
        {
            "schema_version": "1.0",
            "operation": operation,
            "asset_target": asset_target,
            "source_files": list(source_files),
            "changed_files": list(changed_files),
            "changed_teams": list(changed_teams),
            "release_implications": list(release_implications),
            "message": message,
            "recorded_at": iso_now(),
        },
    )
    update_poa_section(
        context.poa_path,
        begin_marker=_POA_ASSET_BEGIN,
        end_marker=_POA_ASSET_END,
        lines=[
            "## Asset Lifecycle",
            "",
            f"- Operation: `{operation}`",
            f"- Asset target: `{asset_target}`",
            f"- Source files: `{len(source_files)}`",
            f"- Changed files: `{len(changed_files)}`",
            f"- Changed teams: `{len(changed_teams)}`",
            "",
            "### Release Implications",
            *[f"- {item}" for item in release_implications],
        ],
    )
    upsert_decisions(
        context.decisions_path,
        decisions=[
            {
                "id": f"asset_lifecycle_{asset_target}_{operation}",
                "decision": message,
                "status": "accepted",
                "source": "asset_lifecycle",
            }
        ],
    )
    context.branch_state["last_updated_at"] = iso_now()
    context.branch_state["last_updated_by"] = "codex"
    write_json(context.branch_state_path, context.branch_state)
    write_handoff(
        context.handoff_path,
        summary=message,
        next_action="Continue with broader SDLC only if code, behavior, or release work beyond this asset operation is still required.",
        status=str(context.branch_state.get("status", "paused")).strip() or "paused",
        at=iso_now(),
    )
    write_resume_context(
        context.resume_context_path,
        active_run_key=context.active_run_key,
        current_stage=str(context.branch_state.get("current_stage", "")).strip() or "asset_lifecycle",
        next_action="Review asset_operation_result.json and continue with broader SDLC only if still needed.",
        notes=[
            f"Asset operation: {operation}.",
            f"Asset target: {asset_target}.",
            f"Source files: {len(source_files)}.",
            f"Changed files: {len(changed_files)}.",
            f"Changed teams: {len(changed_teams)}.",
        ],
    )


def _write_json_payload(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
