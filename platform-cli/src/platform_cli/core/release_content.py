# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from platform_cli.core.errors import PlatformError
from platform_cli.core.team_context import get_selected_team
from platform_cli.core.sync_providers import (
    DEFAULT_MANIFEST_ASSET as PROVIDER_DEFAULT_MANIFEST_ASSET,
    DEFAULT_MARKETPLACE_BRANCH,
    DEFAULT_PROVIDER as PROVIDER_DEFAULT,
    MARKETPLACE_PROVIDER,
    NormalizedPackageManifest,
    get_provider,
)
from platform_cli.core.sync_targets import (
    DEFAULT_TARGET_TYPE as TARGETS_DEFAULT_TARGET_TYPE,
    get_target_handler,
)
from platform_cli.exec.runner import run_cmd
from platform_cli.state.store import get_tool_state, load_state, update_tool_state


DEFAULT_MANIFEST_ASSET = PROVIDER_DEFAULT_MANIFEST_ASSET
DEFAULT_INDEX_ASSET = "content-index.json"
DEFAULT_INDEX_REPO = "gh-org-data-platform/dp-tools-local-setup"
DEFAULT_INDEX_TAG = "content-index-latest"
DEFAULT_INDEX_REPO_ENV = "GHDP_SYNC_INDEX_REPO"
DEFAULT_INDEX_TAG_ENV = "GHDP_SYNC_INDEX_TAG"
DEFAULT_INDEX_ASSET_ENV = "GHDP_SYNC_INDEX_ASSET"
DEFAULT_PROVIDER = PROVIDER_DEFAULT
DEFAULT_PACKAGE_TYPE = "file_bundle"
DEFAULT_TARGET_TYPE = TARGETS_DEFAULT_TARGET_TYPE
DEFAULT_SCOPE_KIND = "global"
REPO_SCOPE_KIND = "repo"
REPO_ROOT_KEY = "repo_root"
GHDP_USER_ROOT_KEY = "ghdp_user_root"
LEGACY_GHDP_ROOT_KEY = "ghdp_root"
DEFAULT_MARKETPLACE_SOURCE_NAME = "skill_marketplace"
DEFAULT_MARKETPLACE_REPO = "gh-org-data-platform/gh-dp-data-platform-skill-marketplace"
MANAGED_ALLOWLIST_CAPABILITY = "marketplace-skill-allowlist"
MANAGED_ALLOWLIST_FILENAME = "capability-allowlist.managed.json"
MANAGED_TEAM_POLICY_CAPABILITY = "ghdp-team-policy"
MANAGED_TEAM_POLICY_FILENAME = "team-policy.managed.json"
REPO_ALLOWLIST_FILENAMES = ("capability-allowlist.json", MANAGED_ALLOWLIST_FILENAME)
CATEGORY_TABLEAU_DRIVERS = "tableau_drivers"
CATEGORY_CLAUDE_SKILLS = "claude_skills"
CATEGORY_CLAUDE_PLUGINS = "claude_plugins"
CATEGORY_CODEX_SKILLS = "codex_skills"
CATEGORY_CODEX_PLUGINS = "codex_plugins"
CATEGORY_GHDP_POLICY = "ghdp_policy"
CATEGORY_GHDP_MANAGED_MISC = "ghdp_managed_misc"
MARKETPLACE_TARGETS = {
    "claude": {
        "skill": {
            "target_type": "claude_skills",
            "target_root_key": "claude_skills_root",
            "category": CATEGORY_CLAUDE_SKILLS,
            "install_unit_type": "skill",
        },
        "plugin": {
            "target_type": "claude_plugins",
            "target_root_key": "claude_plugins_root",
            "category": CATEGORY_CLAUDE_PLUGINS,
            "install_unit_type": "plugin",
        },
    },
    "codex": {
        "skill": {
            "target_type": "codex_skills",
            "target_root_key": "codex_skills_root",
            "category": CATEGORY_CODEX_SKILLS,
            "install_unit_type": "skill",
        },
        "plugin": {
            "target_type": "codex_plugins",
            "target_root_key": "codex_plugins_root",
            "category": CATEGORY_CODEX_PLUGINS,
            "install_unit_type": "plugin",
        },
    },
}
DURABLE_INSTALL_STATE_SOURCES = frozenset({"release", "existing"})


# Asset-backed recovery stays here; command layers can present status through
# LiveStatus or GenerationProgressReporter without importing rich_ui here.
def _sync_retry_hint(capability: str) -> str:
    capability = str(capability).strip()
    return (
        f"Try `ghdp sync run --capability {capability}` to refresh the synced content and "
        f"`ghdp sync repair --capability {capability}` to restore tracked files."
    )


def _normalize_scope_kind(scope_kind: str | None) -> str:
    normalized = str(scope_kind or DEFAULT_SCOPE_KIND).strip().lower()
    return normalized or DEFAULT_SCOPE_KIND


def _normalize_scope_ref(scope_kind: str | None, scope_ref: str | None) -> str:
    normalized_kind = _normalize_scope_kind(scope_kind)
    if normalized_kind == DEFAULT_SCOPE_KIND:
        return ""
    return str(scope_ref or "").strip()


def _state_key(capability: str, scope_kind: str = DEFAULT_SCOPE_KIND, scope_ref: str | None = None) -> str:
    normalized_kind = _normalize_scope_kind(scope_kind)
    normalized_ref = _normalize_scope_ref(normalized_kind, scope_ref)
    if normalized_kind == DEFAULT_SCOPE_KIND:
        return f"content:{capability}"
    if not normalized_ref:
        raise PlatformError(
            f"Sync scope '{normalized_kind}' requires a scope reference.",
            code="E_SYNC_SCOPE_INVALID",
            reason=normalized_kind,
        )
    scope_hash = hashlib.sha1(f"{normalized_kind}:{normalized_ref}".encode("utf-8")).hexdigest()[:12]
    return f"content:{capability}:{normalized_kind}:{scope_hash}"


def _content_hash(entries: list[tuple[str, bytes]]) -> str:
    h = hashlib.sha256()
    for rel, data in entries:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(data)
        h.update(b"\0")
    return h.hexdigest()


def _resolve_index_source_value(explicit: str | None, env_name: str, fallback: str) -> str:
    if explicit is not None:
        value = str(explicit).strip()
        if value:
            return value
    env_value = os.environ.get(env_name, "").strip()
    return env_value or fallback


def _resolve_index_source(
    repo: str | None = None,
    tag: str | None = None,
    asset_name: str | None = None,
) -> tuple[str, str, str]:
    return (
        _resolve_index_source_value(repo, DEFAULT_INDEX_REPO_ENV, DEFAULT_INDEX_REPO),
        _resolve_index_source_value(tag, DEFAULT_INDEX_TAG_ENV, DEFAULT_INDEX_TAG),
        _resolve_index_source_value(asset_name, DEFAULT_INDEX_ASSET_ENV, DEFAULT_INDEX_ASSET),
    )


def _normalize_rel_paths(items: object) -> list[str]:
    if not isinstance(items, list):
        return []
    normalized: list[str] = []
    for item in items:
        value = str(item).strip()
        if value:
            normalized.append(value)
    return normalized


def _infer_category(*, capability: str, target_type: str) -> str:
    if capability in {MANAGED_ALLOWLIST_CAPABILITY, MANAGED_TEAM_POLICY_CAPABILITY}:
        return CATEGORY_GHDP_POLICY
    if target_type == "tableau_drivers":
        return CATEGORY_TABLEAU_DRIVERS
    if target_type == "claude_skills":
        return CATEGORY_CLAUDE_SKILLS
    if target_type == "claude_plugins":
        return CATEGORY_CLAUDE_PLUGINS
    if target_type == "codex_skills":
        return CATEGORY_CODEX_SKILLS
    if target_type == "codex_plugins":
        return CATEGORY_CODEX_PLUGINS
    return CATEGORY_GHDP_MANAGED_MISC


def _normalize_index_entry(item: dict[str, Any]) -> dict[str, Any]:
    capability = str(item.get("capability", "")).strip()
    version = str(item.get("version", "")).strip()
    provider = str(item.get("provider", DEFAULT_PROVIDER)).strip() or DEFAULT_PROVIDER
    package_type = str(item.get("package_type", DEFAULT_PACKAGE_TYPE)).strip() or DEFAULT_PACKAGE_TYPE
    target_type = str(item.get("target_type", DEFAULT_TARGET_TYPE)).strip() or DEFAULT_TARGET_TYPE
    category = str(item.get("category", "")).strip() or _infer_category(capability=capability, target_type=target_type)
    source = item.get("source")
    source_payload = dict(source) if isinstance(source, dict) else {}
    repo = str(source_payload.get("repo", item.get("repo", ""))).strip()
    tag = str(source_payload.get("tag", item.get("tag", ""))).strip()
    manifest_asset = str(source_payload.get("manifest_asset", item.get("manifest_asset", DEFAULT_MANIFEST_ASSET))).strip()
    manifest_asset = manifest_asset or DEFAULT_MANIFEST_ASSET
    source_payload.setdefault("repo", repo)
    source_payload.setdefault("tag", tag)
    source_payload.setdefault("manifest_asset", manifest_asset)
    policy = item.get("policy")
    policy_payload = dict(policy) if isinstance(policy, dict) else {}
    allow_update_existing_files = bool(policy_payload.get("allow_update_existing_files", item.get("allow_update_existing_files", True)))
    allow_new_files_on_update = bool(policy_payload.get("allow_new_files_on_update", item.get("allow_new_files_on_update", False)))
    allow_install_if_missing = bool(policy_payload.get("allow_install_if_missing", item.get("allow_install_if_missing", False)))
    min_cli_version = str(policy_payload.get("min_cli_version", item.get("min_cli_version", ""))).strip()
    recovery_hint = str(item.get("recovery_hint", "")).strip()
    if not capability or not version:
        raise PlatformError(
            "Content index capability entry is missing required fields.",
            code="E_RELEASE_CONTENT_INDEX_INVALID",
            reason="capability_fields",
        )
    if provider == DEFAULT_PROVIDER and (not tag or not repo):
        raise PlatformError(
            "GitHub release capability entries require repo and tag source fields.",
            code="E_RELEASE_CONTENT_INDEX_INVALID",
            reason="provider_source_fields",
        )
    return {
        "capability": capability,
        "version": version,
        "provider": provider,
        "source": source_payload,
        "package_type": package_type,
        "target_type": target_type,
        "category": category,
        "tag": tag,
        "repo": repo,
        "manifest_asset": manifest_asset,
        "policy": {
            "allow_update_existing_files": allow_update_existing_files,
            "allow_new_files_on_update": allow_new_files_on_update,
            "allow_install_if_missing": allow_install_if_missing,
            "min_cli_version": min_cli_version,
        },
        "allow_update_existing_files": allow_update_existing_files,
        "allow_new_files_on_update": allow_new_files_on_update,
        "allow_install_if_missing": allow_install_if_missing,
        "min_cli_version": min_cli_version,
        "recovery_hint": recovery_hint,
    }

def _has_durable_recorded_install(state: dict[str, Any], tracked_files: list[str] | None = None) -> bool:
    normalized_tracked_files = tracked_files if tracked_files is not None else _normalize_rel_paths(state.get("files", []))
    install_path = str(state.get("install_path", "")).strip()
    source = str(state.get("source", "")).strip()
    return bool(install_path and normalized_tracked_files and source in DURABLE_INSTALL_STATE_SOURCES)


def _validate_index(payload: object) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise PlatformError(
            "Content index root must be a JSON object.",
            code="E_RELEASE_CONTENT_INDEX_INVALID",
            reason="index",
        )

    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, list):
        raise PlatformError(
            "Content index is missing capabilities.",
            code="E_RELEASE_CONTENT_INDEX_INVALID",
            reason="capabilities",
        )

    normalized: list[dict[str, Any]] = []
    for item in capabilities:
        if not isinstance(item, dict):
            raise PlatformError(
                "Content index capability entries must be objects.",
                code="E_RELEASE_CONTENT_INDEX_INVALID",
                reason="capability",
            )
        normalized.append(_normalize_index_entry(item))

    return normalized


def _load_allowlist_payload_from_path(path: Path, *, reason: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as e:
        raise PlatformError(
            f"Failed to parse sync allowlist '{path}': {e}",
            code="E_SYNC_POLICY_INVALID",
            reason=reason,
        )
    if not isinstance(payload, dict):
        raise PlatformError(
            f"Sync allowlist '{path}' must be a JSON object.",
            code="E_SYNC_POLICY_INVALID",
            reason=reason,
        )
    return payload


def _load_allowlist_payload_from_bytes(raw_payload: bytes, *, reason: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw_payload.decode("utf-8-sig"))
    except Exception as e:
        raise PlatformError(
            f"Failed to parse sync allowlist payload: {e}",
            code="E_SYNC_POLICY_INVALID",
            reason=reason,
        )
    if not isinstance(payload, dict):
        raise PlatformError(
            "Sync allowlist payload must be a JSON object.",
            code="E_SYNC_POLICY_INVALID",
            reason=reason,
        )
    return payload


def _load_managed_policy_payload(
    capabilities: list[dict[str, Any]],
    *,
    capability_name: str,
    filename: str,
    reason_prefix: str,
) -> dict[str, Any]:
    entry = next((item for item in capabilities if str(item["capability"]) == capability_name), None)
    if entry is None:
        return {}

    package = _load_provider_package(entry)
    selected_rel_path = next(
        (item["target_path"] for item in package.files if Path(item["target_path"]).name == filename),
        package.files[0]["target_path"] if package.files else "",
    )
    if not selected_rel_path:
        raise PlatformError(
            f"Managed policy '{capability_name}' manifest contains no policy payload file.",
            code="E_SYNC_POLICY_INVALID",
            reason=f"{reason_prefix}_missing_file",
        )
    downloaded = _download_manifest_entries(
        entry=entry,
        file_specs=package.files,
        selected_paths={selected_rel_path},
    )
    if len(downloaded) != 1:
        raise PlatformError(
            f"Managed policy '{capability_name}' could not be resolved from its manifest.",
            code="E_SYNC_POLICY_INVALID",
            reason=f"{reason_prefix}_download",
        )
    _, raw_payload = downloaded[0]
    return _load_allowlist_payload_from_bytes(raw_payload, reason=f"{reason_prefix}_parse")


def _repo_allowlist_path() -> Path | None:
    current = Path.cwd().resolve()
    repo_root: Path | None = None
    for base in (current, *current.parents):
        if (base / ".git").exists():
            repo_root = base
            break
    if repo_root is None:
        return None

    for base in (current, *current.parents):
        ghdp_dir = base / ".ghdp"
        if not ghdp_dir.exists():
            if base == repo_root:
                break
            continue
        for filename in REPO_ALLOWLIST_FILENAMES:
            candidate = ghdp_dir / filename
            if candidate.exists() and candidate.is_file():
                return candidate
        if base == repo_root:
            break
    return None


def _load_managed_allowlist_policy(capabilities: list[dict[str, Any]]) -> dict[str, Any]:
    repo_policy_path = _repo_allowlist_path()
    if repo_policy_path is not None:
        return _load_allowlist_payload_from_path(repo_policy_path, reason="repo_allowlist_parse")

    return _load_managed_policy_payload(
        capabilities,
        capability_name=MANAGED_ALLOWLIST_CAPABILITY,
        filename=MANAGED_ALLOWLIST_FILENAME,
        reason_prefix="allowlist",
    )


def _load_managed_team_policy(capabilities: list[dict[str, Any]]) -> dict[str, Any]:
    return _load_managed_policy_payload(
        capabilities,
        capability_name=MANAGED_TEAM_POLICY_CAPABILITY,
        filename=MANAGED_TEAM_POLICY_FILENAME,
        reason_prefix="team_policy",
    )


def _normalize_string_set(items: object) -> set[str]:
    if not isinstance(items, list):
        return set()
    return {str(item).strip() for item in items if str(item).strip()}


def _resolve_team_policy(capabilities: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    payload = _load_managed_team_policy(capabilities)
    teams = payload.get("teams")
    if not isinstance(teams, dict):
        return "", "none", {}

    selected = get_selected_team()
    if selected:
        selected_policy = teams.get(selected)
        if isinstance(selected_policy, dict):
            return selected, "config", selected_policy

    default_policy = teams.get("default")
    if isinstance(default_policy, dict):
        return "default", "default", default_policy

    return selected, "config_unmatched" if selected else "none", {}


def _filter_capabilities_for_team(
    capabilities: list[dict[str, Any]],
    *,
    team_policy: dict[str, Any],
) -> list[dict[str, Any]]:
    if not team_policy:
        return list(capabilities)

    allow_categories = _normalize_string_set(team_policy.get("allow_categories"))
    allow_capabilities = _normalize_string_set(team_policy.get("allow_capabilities"))
    deny_categories = _normalize_string_set(team_policy.get("deny_categories"))
    deny_capabilities = _normalize_string_set(team_policy.get("deny_capabilities"))
    allow_rules_present = bool(allow_categories or allow_capabilities)
    kept: list[dict[str, Any]] = []

    for item in capabilities:
        capability_name = str(item.get("capability", "")).strip()
        category = str(item.get("category", CATEGORY_GHDP_MANAGED_MISC)).strip() or CATEGORY_GHDP_MANAGED_MISC
        if capability_name in {MANAGED_ALLOWLIST_CAPABILITY, MANAGED_TEAM_POLICY_CAPABILITY} or category == CATEGORY_GHDP_POLICY:
            kept.append(item)
            continue

        allowed = True
        if allow_rules_present:
            allowed = capability_name in allow_capabilities or category in allow_categories
        if not allowed:
            continue
        if capability_name in deny_capabilities or category in deny_categories:
            continue
        kept.append(item)

    return kept


def _unique_paths(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        normalized = item.replace("\\", "/").strip().strip("/")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _safe_capability_fragment(value: str) -> str:
    chars = []
    for ch in value.lower():
        chars.append(ch if ch.isalnum() else "-")
    return "-".join(part for part in "".join(chars).split("-") if part)


def _marketplace_capability_id(
    *,
    target_name: str,
    install_unit_type: str,
    rel_path: str,
    plugin_name: str | None = None,
) -> str:
    parts = [part for part in rel_path.replace("\\", "/").split("/") if part]
    if install_unit_type == "plugin":
        suffix = f"plugin-{plugin_name or (parts[-1] if parts else rel_path)}"
    elif len(parts) >= 2 and parts[0] == "skills":
        suffix = f"skill-{parts[-1]}"
    elif len(parts) >= 4 and parts[0] == "plugins" and parts[2] == "skills":
        suffix = f"plugin-{parts[1]}-{parts[-1]}"
    else:
        suffix = "-".join(parts)
    return _safe_capability_fragment(f"marketplace-{target_name}-{suffix}")


def _marketplace_target_settings(target_name: str, *, install_unit_type: str = "skill") -> dict[str, str]:
    target_settings = MARKETPLACE_TARGETS.get(target_name)
    if target_settings is None:
        raise PlatformError(
            f"Unsupported marketplace sync target '{target_name}' in allowlist policy.",
            code="E_SYNC_POLICY_INVALID",
            reason=target_name,
        )
    settings = target_settings.get(install_unit_type)
    if settings is None:
        raise PlatformError(
            f"Marketplace sync target '{target_name}' does not support install unit '{install_unit_type}'.",
            code="E_SYNC_POLICY_INVALID",
            reason=target_name,
        )
    return settings


def _marketplace_source_policy(policy: dict[str, Any]) -> dict[str, Any]:
    sources = policy.get("sources")
    if not isinstance(sources, dict):
        return {}
    source_policy = sources.get(DEFAULT_MARKETPLACE_SOURCE_NAME)
    if source_policy is None:
        return {}
    if not isinstance(source_policy, dict):
        raise PlatformError(
            "Marketplace sync allowlist source config must be a JSON object.",
            code="E_SYNC_POLICY_INVALID",
            reason="marketplace_source",
        )
    return source_policy


def _marketplace_target_policies(source_policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    targets = source_policy.get("targets")
    if isinstance(targets, dict):
        target_policies = targets
    else:
        target_policies = {name: source_policy[name] for name in MARKETPLACE_TARGETS if isinstance(source_policy.get(name), dict)}
    normalized: dict[str, dict[str, Any]] = {}
    for name, value in target_policies.items():
        if not isinstance(value, dict):
            raise PlatformError(
                f"Marketplace sync target policy '{name}' must be a JSON object.",
                code="E_SYNC_POLICY_INVALID",
                reason=name,
            )
        normalized[name] = value
    return normalized


def _marketplace_explicit_entries(target_name: str, target_policy: dict[str, Any]) -> list[dict[str, str]] | None:
    if "entries" not in target_policy:
        return None
    raw_entries = target_policy.get("entries")
    if raw_entries is None:
        return []
    if not isinstance(raw_entries, list):
        raise PlatformError(
            f"Marketplace sync target policy '{target_name}.entries' must be a JSON array.",
            code="E_SYNC_POLICY_INVALID",
            reason=f"{target_name}_entries",
        )
    normalized: list[dict[str, str]] = []
    for index, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, dict):
            raise PlatformError(
                f"Marketplace sync entry '{target_name}[{index}]' must be a JSON object.",
                code="E_SYNC_POLICY_INVALID",
                reason=f"{target_name}_entry",
            )
        capability = str(raw_entry.get("capability", "")).strip()
        install_unit_type = str(raw_entry.get("install_unit_type", "")).strip().lower()
        source_path = str(raw_entry.get("source_path", "")).strip().replace("\\", "/").strip("/")
        target_type = str(raw_entry.get("target_type", "")).strip()
        target_root_key = str(raw_entry.get("target_root_key", "")).strip()
        target_subdir = str(raw_entry.get("target_subdir", "")).strip()
        category = str(raw_entry.get("category", "")).strip()
        missing = [
            field_name
            for field_name, field_value in (
                ("capability", capability),
                ("install_unit_type", install_unit_type),
                ("source_path", source_path),
                ("target_type", target_type),
                ("target_root_key", target_root_key),
                ("target_subdir", target_subdir),
                ("category", category),
            )
            if not field_value
        ]
        if missing:
            raise PlatformError(
                f"Marketplace sync entry '{target_name}[{index}]' is missing required field(s): {', '.join(missing)}.",
                code="E_SYNC_POLICY_INVALID",
                reason=f"{target_name}_entry_fields",
            )
        normalized.append(
            {
                "capability": capability,
                "install_unit_type": install_unit_type,
                "source_path": source_path,
                "target_type": target_type,
                "target_root_key": target_root_key,
                "target_subdir": target_subdir,
                "category": category,
            }
        )
    return normalized


def _build_marketplace_index_entry(
    *,
    capability: str,
    version: str,
    base_source: dict[str, str],
    source_with_commit: dict[str, str],
    install_unit_type: str,
    source_path: str,
    target_type: str,
    target_root_key: str,
    target_subdir: str,
    category: str,
) -> dict[str, Any]:
    source_payload: dict[str, str] = {
        **source_with_commit,
        "capability": capability,
        "source_path": source_path,
        "install_unit_type": install_unit_type,
        "target_root_key": target_root_key,
        "target_subdir": target_subdir,
    }
    if install_unit_type == "plugin":
        source_payload["plugin_path"] = source_path
    else:
        source_payload["skill_path"] = source_path
    return _normalize_index_entry(
        {
            "capability": capability,
            "version": version,
            "provider": MARKETPLACE_PROVIDER,
            "source": source_payload,
            "package_type": DEFAULT_PACKAGE_TYPE,
            "target_type": target_type,
            "category": category,
            "repo": base_source["repo"],
            "tag": version,
            "policy": {
                "allow_update_existing_files": True,
                "allow_new_files_on_update": False,
                "allow_install_if_missing": True,
            },
        }
    )


def _build_marketplace_entries(capabilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    policy = _load_managed_allowlist_policy(capabilities)
    source_policy = _marketplace_source_policy(policy)
    if not source_policy:
        return []

    provider = get_provider(MARKETPLACE_PROVIDER, run_cmd_impl=run_cmd)
    branch = str(source_policy.get("branch", DEFAULT_MARKETPLACE_BRANCH)).strip() or DEFAULT_MARKETPLACE_BRANCH
    base_source = {
        "repo": str(source_policy.get("repo", DEFAULT_MARKETPLACE_REPO)).strip(),
        "repo_path": str(source_policy.get("repo_path", "")).strip(),
        "branch": branch,
        "manifest_asset": DEFAULT_MANIFEST_ASSET,
    }
    commit = provider.resolve_version(source=base_source)
    source_with_commit = {**base_source, "commit": commit}

    target_entries: list[dict[str, Any]] = []
    for target_name, target_policy in _marketplace_target_policies(source_policy).items():
        explicit_entries = _marketplace_explicit_entries(target_name, target_policy)
        if explicit_entries is not None:
            for explicit_entry in explicit_entries:
                target_entries.append(
                    _build_marketplace_index_entry(
                        capability=explicit_entry["capability"],
                        version=commit,
                        base_source=base_source,
                        source_with_commit=source_with_commit,
                        install_unit_type=explicit_entry["install_unit_type"],
                        source_path=explicit_entry["source_path"],
                        target_type=explicit_entry["target_type"],
                        target_root_key=explicit_entry["target_root_key"],
                        target_subdir=explicit_entry["target_subdir"],
                        category=explicit_entry["category"],
                    )
                )
            continue

        skill_settings = _marketplace_target_settings(target_name, install_unit_type="skill")
        skill_paths: list[str] = []
        for skill in _normalize_rel_paths(target_policy.get("skills", [])):
            skill_paths.append(skill if "/" in skill else f"skills/{skill}")
        skill_paths.extend(_normalize_rel_paths(target_policy.get("skill_paths", [])))
        for skill_path in _unique_paths(skill_paths):
            capability = _marketplace_capability_id(
                target_name=target_name,
                install_unit_type="skill",
                rel_path=skill_path,
            )
            target_entries.append(
                _build_marketplace_index_entry(
                    capability=capability,
                    version=commit,
                    base_source=base_source,
                    source_with_commit=source_with_commit,
                    install_unit_type=skill_settings["install_unit_type"],
                    source_path=skill_path,
                    target_type=skill_settings["target_type"],
                    target_root_key=skill_settings["target_root_key"],
                    target_subdir=Path(skill_path).name,
                    category=skill_settings["category"],
                )
            )
        plugin_names = _normalize_rel_paths(target_policy.get("plugins", []))
        if not plugin_names:
            continue

        plugin_settings = _marketplace_target_settings(target_name, install_unit_type="plugin")
        if plugin_settings["install_unit_type"] == "plugin":
            for plugin_name in plugin_names:
                plugin_path = f"plugins/{plugin_name}"
                capability = _marketplace_capability_id(
                    target_name=target_name,
                    install_unit_type="plugin",
                    rel_path=plugin_path,
                    plugin_name=plugin_name,
                )
                target_entries.append(
                    _build_marketplace_index_entry(
                        capability=capability,
                        version=commit,
                        base_source=base_source,
                        source_with_commit=source_with_commit,
                        install_unit_type=plugin_settings["install_unit_type"],
                        source_path=plugin_path,
                        target_type=plugin_settings["target_type"],
                        target_root_key=plugin_settings["target_root_key"],
                        target_subdir=plugin_name,
                        category=plugin_settings["category"],
                    )
                )
            continue

        list_plugin_skill_paths = getattr(provider, "list_plugin_skill_paths", None)
        if not callable(list_plugin_skill_paths):
            raise PlatformError(
                "Marketplace sync provider does not support plugin skill resolution.",
                code="E_SYNC_PROVIDER_UNSUPPORTED",
                reason=MARKETPLACE_PROVIDER,
            )
        plugin_skill_settings = _marketplace_target_settings(target_name, install_unit_type="plugin")
        expanded_skill_paths: list[str] = []
        for plugin_name in plugin_names:
            expanded_skill_paths.extend(list_plugin_skill_paths(source=source_with_commit, plugin_name=plugin_name))
        for skill_path in _unique_paths(expanded_skill_paths):
            capability = _marketplace_capability_id(
                target_name=target_name,
                install_unit_type="skill",
                rel_path=skill_path,
            )
            target_entries.append(
                _build_marketplace_index_entry(
                    capability=capability,
                    version=commit,
                    base_source=base_source,
                    source_with_commit=source_with_commit,
                    install_unit_type=plugin_skill_settings["install_unit_type"],
                    source_path=skill_path,
                    target_type=plugin_skill_settings["target_type"],
                    target_root_key=plugin_skill_settings["target_root_key"],
                    target_subdir=Path(skill_path).name,
                    category=plugin_skill_settings["category"],
                )
            )
    return target_entries


def _load_json_release_asset(*, repo: str, tag: str, asset_name: str) -> object:
    with tempfile.TemporaryDirectory(prefix="ghdp_release_content_") as tmpdir:
        download_dir = Path(tmpdir)
        asset_path = _download_provider_asset(
            entry={
                "provider": DEFAULT_PROVIDER,
                "source": {"repo": repo, "tag": tag, "manifest_asset": asset_name},
                "repo": repo,
                "tag": tag,
                "manifest_asset": asset_name,
            },
            asset_name=asset_name,
            download_dir=download_dir,
        )
        try:
            return json.loads(asset_path.read_text(encoding="utf-8-sig"))
        except Exception as e:
            raise PlatformError(
                f"Failed to parse JSON asset '{asset_name}': {e}",
                code="E_RELEASE_CONTENT_MANIFEST_INVALID",
                reason=asset_name,
            )


def _download_provider_asset(*, entry: dict[str, Any], asset_name: str, download_dir: Path) -> Path:
    provider = get_provider(str(entry.get("provider", DEFAULT_PROVIDER)).strip() or DEFAULT_PROVIDER, run_cmd_impl=run_cmd)
    return provider.download_asset(source=_provider_source(entry), asset_name=asset_name, download_dir=download_dir)


def _provider_source(entry: dict[str, Any]) -> dict[str, Any]:
    source = entry.get("source")
    if isinstance(source, dict):
        payload = dict(source)
    else:
        payload = {}
    payload.setdefault("repo", str(entry.get("repo", "")).strip())
    payload.setdefault("tag", str(entry.get("tag", "")).strip())
    payload.setdefault("manifest_asset", str(entry.get("manifest_asset", DEFAULT_MANIFEST_ASSET)).strip() or DEFAULT_MANIFEST_ASSET)
    return payload


def _load_provider_package(entry: dict[str, Any]) -> NormalizedPackageManifest:
    provider = get_provider(str(entry.get("provider", DEFAULT_PROVIDER)).strip() or DEFAULT_PROVIDER, run_cmd_impl=run_cmd)
    return provider.load_package_manifest(source=_provider_source(entry))


def _resolve_target_root(
    *,
    entry: dict[str, Any],
    package: NormalizedPackageManifest,
    resolve_root_key: Callable[[str], Path],
) -> Path:
    target_type = str(entry.get("target_type", DEFAULT_TARGET_TYPE)).strip() or DEFAULT_TARGET_TYPE
    handler = get_target_handler(target_type)
    return handler.resolve_install_root(
        root_key=package.target_root_key,
        target_subdir=package.target_subdir,
        resolve_root_key=resolve_root_key,
    )


def _default_resolve_root_key(root_key: str) -> Path:
    if root_key in {GHDP_USER_ROOT_KEY, LEGACY_GHDP_ROOT_KEY}:
        return Path.home() / ".ghdp"
    if root_key == "codex_skills_root":
        return Path.home() / ".codex" / "skills"
    if root_key == "codex_plugins_root":
        return Path.home() / ".codex" / "plugins"
    if root_key == "claude_skills_root":
        return Path.home() / ".claude" / "skills"
    if root_key == "claude_plugins_root":
        return Path.home() / ".claude" / "plugins"
    if root_key == "tableau_drivers_root":
        if sys.platform.startswith("darwin"):
            return Path.home() / "Library" / "Tableau" / "Drivers"
        if sys.platform.startswith("linux"):
            return Path("/opt/tableau/drivers")
        if sys.platform.startswith("win"):
            return Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tableau" / "Drivers"
    raise PlatformError(
        f"Unsupported sync target root key: {root_key}",
        code="E_SYNC_ROOT_KEY_UNSUPPORTED",
        reason=root_key,
    )


def build_sync_root_resolver(*, repo_root: Path | None = None) -> Callable[[str], Path]:
    resolved_repo_root = repo_root.expanduser().resolve() if repo_root is not None else None

    def _resolve(root_key: str) -> Path:
        if root_key == REPO_ROOT_KEY:
            if resolved_repo_root is None:
                raise PlatformError(
                    "Sync target root key 'repo_root' requires an explicit repo root.",
                    code="E_SYNC_ROOT_KEY_REQUIRES_REPO",
                    reason=root_key,
                )
            return resolved_repo_root
        return _default_resolve_root_key(root_key)

    return _resolve


def _load_content_entries(
    *,
    entry: dict[str, Any],
    resolve_root_key: Callable[[str], Path],
) -> tuple[NormalizedPackageManifest, Path, list[tuple[str, bytes]]]:
    package = _load_provider_package(entry)
    with tempfile.TemporaryDirectory(prefix="ghdp_release_content_") as tmpdir:
        download_dir = Path(tmpdir)
        entries: list[tuple[str, bytes]] = []
        for item in package.files:
            asset_path = _download_provider_asset(entry=entry, asset_name=item["asset_name"], download_dir=download_dir)
            entries.append((item["target_path"], asset_path.read_bytes()))
    target_root = _resolve_target_root(entry=entry, package=package, resolve_root_key=resolve_root_key)
    return package, target_root, entries


def _installed_entries(target_root: Path, rel_paths: list[str]) -> list[tuple[str, bytes]] | None:
    entries: list[tuple[str, bytes]] = []
    for rel in rel_paths:
        src = target_root / Path(rel)
        if not src.exists():
            return None
        entries.append((rel, src.read_bytes()))
    return entries


def _missing_rel_paths(target_root: Path, rel_paths: list[str]) -> list[str]:
    missing: list[str] = []
    for rel in rel_paths:
        if not (target_root / Path(rel)).exists():
            missing.append(rel)
    return missing


def _list_local_rel_paths(target_root: Path) -> list[str]:
    if not target_root.exists():
        return []
    return sorted(str(path.relative_to(target_root)).replace("\\", "/") for path in target_root.rglob("*") if path.is_file())


def _write_entries(target_root: Path, entries: list[tuple[str, bytes]]) -> int:
    target_root.mkdir(parents=True, exist_ok=True)
    updated = 0
    for rel, data in entries:
        dest = target_root / Path(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        old = dest.read_bytes() if dest.exists() else None
        if old != data:
            dest.write_bytes(data)
            updated += 1
    return updated


def _tracked_content_states(
    *,
    scope_kind: str = DEFAULT_SCOPE_KIND,
    scope_ref: str | None = None,
) -> dict[str, dict[str, Any]]:
    state = load_state()
    tools = state.get("tools", {})
    if not isinstance(tools, dict):
        return {}
    expected_kind = _normalize_scope_kind(scope_kind)
    expected_ref = _normalize_scope_ref(expected_kind, scope_ref)
    tracked: dict[str, dict[str, Any]] = {}
    for key, value in tools.items():
        if isinstance(key, str) and key.startswith("content:") and isinstance(value, dict):
            value_kind = _normalize_scope_kind(value.get("scope_kind", DEFAULT_SCOPE_KIND))
            value_ref = _normalize_scope_ref(value_kind, value.get("scope_ref"))
            if value_kind != expected_kind:
                continue
            if expected_kind != DEFAULT_SCOPE_KIND and value_ref != expected_ref:
                continue
            capability = str(value.get("capability", "")).strip()
            if not capability:
                parts = key.split(":")
                capability = parts[1] if len(parts) > 1 else ""
            if capability:
                tracked[capability] = value
    return tracked


def _content_status_from_state(capability: str, state: dict[str, Any]) -> tuple[str, Path | None, list[str], list[str]]:
    install_path = str(state.get("install_path", "")).strip()
    tracked_files = _normalize_rel_paths(state.get("files", []))
    if not install_path:
        return "unknown", None, tracked_files, tracked_files
    target_root = Path(install_path).expanduser()
    missing_files = _missing_rel_paths(target_root, tracked_files)
    source = str(state.get("source", "")).strip()
    if not tracked_files:
        return "tracked_empty", target_root, tracked_files, missing_files
    tracked_local_files = [rel for rel in tracked_files if rel not in set(missing_files)]
    if not tracked_local_files and not _has_durable_recorded_install(state, tracked_files):
        return "not_installed", target_root, tracked_files, missing_files
    if missing_files:
        return "partial", target_root, tracked_files, missing_files
    if source == "detected":
        return "detected", target_root, tracked_files, []
    return "tracked", target_root, tracked_files, []


def _build_state_patch(
    *,
    capability: str,
    item: dict[str, Any],
    source: str,
    scope_kind: str = DEFAULT_SCOPE_KIND,
    scope_ref: str | None = None,
    detected_local_files: list[str] | None = None,
    record_release_details: bool = True,
) -> dict[str, Any]:
    patch: dict[str, Any] = {
        "capability": capability,
        "provider": str(item.get("provider", DEFAULT_PROVIDER)).strip() or DEFAULT_PROVIDER,
        "provider_source": dict(item.get("source", {})) if isinstance(item.get("source"), dict) else {},
        "package_type": str(item.get("package_type", DEFAULT_PACKAGE_TYPE)).strip() or DEFAULT_PACKAGE_TYPE,
        "target_type": str(item.get("target_type", DEFAULT_TARGET_TYPE)).strip() or DEFAULT_TARGET_TYPE,
        "category": str(item.get("category", CATEGORY_GHDP_MANAGED_MISC)).strip() or CATEGORY_GHDP_MANAGED_MISC,
        "policy": dict(item.get("policy", {})) if isinstance(item.get("policy"), dict) else {},
        "install_path": str(item.get("install_path", "")).strip(),
        "files": list(item.get("tracked_files", [])),
        "detected_local_files": list(detected_local_files if detected_local_files is not None else item.get("extra_local_files", [])),
        "last_verified_at": int(time.time()),
        "last_status": "ok",
        "source": source,
        "scope_kind": _normalize_scope_kind(scope_kind),
        "scope_ref": _normalize_scope_ref(scope_kind, scope_ref),
    }
    if record_release_details:
        patch.update(
            {
                "repo": str(item.get("repo", "")).strip(),
                "tag": str(item.get("latest_tag", item.get("tag", ""))).strip(),
                "version": str(item.get("latest_version", item.get("version", ""))).strip(),
                "manifest_asset": str(item.get("manifest_asset", DEFAULT_MANIFEST_ASSET)).strip() or DEFAULT_MANIFEST_ASSET,
            }
        )
    content_hash = item.get("content_hash")
    if isinstance(content_hash, str) and content_hash.strip():
        patch["content_hash"] = content_hash.strip()
    return patch


def _recovery_mode(
    *,
    installed: bool,
    local_status: str,
    allow_install_if_missing: bool,
) -> tuple[bool, str, str]:
    if not installed:
        if local_status == "scope_required":
            return False, "blocked", "install_target_unresolvable"
        if allow_install_if_missing:
            return True, "bootstrap", ""
        return False, "blocked", "install_if_missing_disabled"
    if local_status in {"partial", "tracked_empty"}:
        return False, "repair", ""
    return False, "none", ""


def _scan_capability(
    entry: dict[str, Any],
    state: dict[str, Any],
    *,
    resolve_root_key: Callable[[str], Path],
) -> dict[str, Any]:
    package = _load_provider_package(entry)
    capability_name = str(entry["capability"])
    if package.capability != capability_name:
        raise PlatformError(
            f"Content index capability '{capability_name}' did not match manifest capability '{package.capability}'.",
            code="E_RELEASE_CONTENT_MANIFEST_INVALID",
            reason="capability_mismatch",
        )

    saved_install_path = str(state.get("install_path", "")).strip()
    tracked_files = _normalize_rel_paths(state.get("files", [])) or package.rel_paths
    if saved_install_path:
        target_root = Path(saved_install_path).expanduser()
    else:
        try:
            target_root = _resolve_target_root(entry=entry, package=package, resolve_root_key=resolve_root_key)
        except PlatformError as e:
            if getattr(e, "code", "") in {"E_SYNC_ROOT_KEY_REQUIRES_REPO", "E_SYNC_ROOT_KEY_UNSUPPORTED"} and package.target_root_key == REPO_ROOT_KEY:
                return {
                    "installed": False,
                    "local_status": "scope_required",
                    "bootstrap_allowed": False,
                    "recovery_mode": "blocked",
                    "recovery_detail": "install_target_unresolvable",
                    "install_path": "",
                    "tracked_files": tracked_files,
                    "tracked_local_files": [],
                    "extra_local_files": [],
                    "missing_local_files": tracked_files,
                    "local_version": str(state.get("version", "")).strip(),
                    "local_tag": str(state.get("tag", "")).strip(),
                    "update_available": False,
                    "updatable_files": [],
                    "ignored_new_files": [],
                    "missing_from_latest_manifest": [],
                    "content_hash": "",
                }
            raise
    local_files = _list_local_rel_paths(target_root)
    local_file_set = set(local_files)
    missing_local_files = _missing_rel_paths(target_root, tracked_files)
    tracked_file_set = set(tracked_files)
    tracked_local_files = sorted(rel for rel in tracked_files if rel in local_file_set)
    extra_local_files = sorted(rel for rel in local_files if rel not in tracked_file_set)
    saved_tag = str(state.get("tag", "")).strip()
    saved_version = str(state.get("version", "")).strip()
    has_recorded_install = _has_durable_recorded_install(state, tracked_files)
    detected_any = bool(tracked_local_files) or has_recorded_install

    if not detected_any:
        bootstrap_allowed, recovery_mode, recovery_detail = _recovery_mode(
            installed=False,
            local_status="not_installed",
            allow_install_if_missing=bool(entry.get("allow_install_if_missing", False)),
        )
        return {
            "installed": False,
            "local_status": "not_installed",
            "bootstrap_allowed": bootstrap_allowed,
            "recovery_mode": recovery_mode,
            "recovery_detail": recovery_detail,
            "install_path": str(target_root),
            "tracked_files": tracked_files,
            "tracked_local_files": [],
            "extra_local_files": extra_local_files,
            "missing_local_files": tracked_files,
            "local_version": saved_version,
            "local_tag": saved_tag,
            "update_available": False,
            "updatable_files": [],
            "ignored_new_files": [],
            "missing_from_latest_manifest": [],
            "content_hash": "",
        }

    local_entries = _installed_entries(target_root, tracked_files) if not missing_local_files else None
    remote_tracked_paths = {rel for rel in tracked_files if rel in set(package.rel_paths)}
    latest_entries = _download_manifest_entries(
        entry=entry,
        file_specs=package.files,
        selected_paths=remote_tracked_paths,
    )
    latest_map = {rel: data for rel, data in latest_entries}
    updatable_files = sorted(
        rel
        for rel in tracked_files
        if rel in latest_map and (target_root / Path(rel)).exists() and (target_root / Path(rel)).read_bytes() != latest_map[rel]
    )

    local_status = "detected"
    if missing_local_files:
        local_status = "partial"
    elif local_entries is not None and _content_hash(local_entries) == _content_hash(latest_entries):
        local_status = "detected_current"
    bootstrap_allowed, recovery_mode, recovery_detail = _recovery_mode(
        installed=detected_any,
        local_status=local_status,
        allow_install_if_missing=bool(entry.get("allow_install_if_missing", False)),
    )

    return {
        "installed": detected_any,
        "local_status": local_status,
        "bootstrap_allowed": bootstrap_allowed,
        "recovery_mode": recovery_mode,
        "recovery_detail": recovery_detail,
        "install_path": str(target_root),
        "tracked_files": tracked_files,
        "tracked_local_files": tracked_local_files,
        "extra_local_files": extra_local_files,
        "missing_local_files": missing_local_files,
        "local_version": saved_version,
        "local_tag": saved_tag,
        "update_available": bool(saved_tag != str(entry["tag"]) or saved_version != str(entry["version"]) or updatable_files),
        "updatable_files": updatable_files,
        "ignored_new_files": [],
        "missing_from_latest_manifest": [],
        "content_hash": _content_hash(local_entries) if local_entries is not None else "",
    }


def scan_content_inventory(
    *,
    repo: str | None = None,
    tag: str | None = None,
    asset_name: str | None = None,
    capability: str | None = None,
    persist: bool = False,
    scope_kind: str = DEFAULT_SCOPE_KIND,
    scope_ref: str | None = None,
    resolve_root_key: Callable[[str], Path] | None = None,
) -> dict[str, Any]:
    effective_resolve_root_key = resolve_root_key or _default_resolve_root_key
    repo, tag, asset_name = _resolve_index_source(repo, tag, asset_name)
    index = fetch_content_index(repo=repo, tag=tag, asset_name=asset_name)
    tracked = _tracked_content_states(scope_kind=scope_kind, scope_ref=scope_ref)
    scanned: list[dict[str, Any]] = []

    for entry in index["capabilities"]:
        capability_name = str(entry["capability"])
        if capability and capability != capability_name:
            continue
        state = tracked.get(capability_name, {})
        item = _scan_capability(entry, state, resolve_root_key=effective_resolve_root_key)
        item.update(
            {
                "capability": capability_name,
                "repo": str(entry["repo"]),
                "provider": str(entry["provider"]),
                "source": dict(entry["source"]),
                "package_type": str(entry["package_type"]),
                "target_type": str(entry["target_type"]),
                "category": str(entry.get("category", CATEGORY_GHDP_MANAGED_MISC)),
                "policy": dict(entry["policy"]),
                "manifest_asset": str(entry["manifest_asset"]),
                "latest_version": str(entry["version"]),
                "latest_tag": str(entry["tag"]),
                "allow_update_existing_files": bool(entry["allow_update_existing_files"]),
                "allow_new_files_on_update": bool(entry["allow_new_files_on_update"]),
                "allow_install_if_missing": bool(entry.get("allow_install_if_missing", False)),
                "recovery_hint": str(entry.get("recovery_hint", "")).strip(),
                "bootstrap_allowed": bool(item.get("bootstrap_allowed", False)),
                "recovery_mode": str(item.get("recovery_mode", "none")),
                "recovery_detail": str(item.get("recovery_detail", "")),
            }
        )
        if persist and item["installed"]:
            existing_state = tracked.get(capability_name, {})
            record_release_details = str(existing_state.get("source", "")).strip() in {"release", "existing"}
            patch = _build_state_patch(
                capability=capability_name,
                item=item,
                source=str(existing_state.get("source", "")).strip() or "detected",
                scope_kind=scope_kind,
                scope_ref=scope_ref,
                detected_local_files=list(item["extra_local_files"]),
                record_release_details=record_release_details,
            )
            if existing_state.get("installed_at"):
                patch["installed_at"] = existing_state.get("installed_at")
            update_tool_state(_state_key(capability_name, scope_kind, scope_ref), patch)
        scanned.append(item)

    return {
        "repo": index["repo"],
        "tag": index["tag"],
        "asset_name": index["asset_name"],
        "active_team": str(index.get("active_team", "")).strip(),
        "active_team_source": str(index.get("active_team_source", "")).strip(),
        "team_policy_loaded": bool(index.get("team_policy_loaded", False)),
        "capabilities": scanned,
    }


def fetch_content_index(
    *,
    repo: str | None = None,
    tag: str | None = None,
    asset_name: str | None = None,
) -> dict[str, Any]:
    repo, tag, asset_name = _resolve_index_source(repo, tag, asset_name)
    payload = _load_json_release_asset(repo=repo, tag=tag, asset_name=asset_name)
    capabilities = _validate_index(payload)
    capabilities.extend(_build_marketplace_entries(capabilities))
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in capabilities:
        capability_name = str(item["capability"])
        if capability_name in seen:
            raise PlatformError(
                f"Duplicate sync capability id '{capability_name}' was generated in the content index.",
                code="E_RELEASE_CONTENT_INDEX_INVALID",
                reason="duplicate_capability",
            )
        seen.add(capability_name)
        deduped.append(item)
    active_team, active_team_source, team_policy = _resolve_team_policy(deduped)
    filtered = _filter_capabilities_for_team(deduped, team_policy=team_policy)
    return {
        "repo": repo,
        "tag": tag,
        "asset_name": asset_name,
        "active_team": active_team,
        "active_team_source": active_team_source,
        "team_policy_loaded": bool(team_policy),
        "capabilities": filtered,
    }


def list_sync_status(
    *,
    repo: str | None = None,
    tag: str | None = None,
    asset_name: str | None = None,
    capability: str | None = None,
    scope_kind: str = DEFAULT_SCOPE_KIND,
    scope_ref: str | None = None,
    resolve_root_key: Callable[[str], Path] | None = None,
) -> dict[str, Any]:
    effective_resolve_root_key = resolve_root_key or _default_resolve_root_key
    index = fetch_content_index(repo=repo, tag=tag, asset_name=asset_name)
    tracked = _tracked_content_states(scope_kind=scope_kind, scope_ref=scope_ref)
    scanned = {
        str(item["capability"]): item
        for item in scan_content_inventory(
            repo=repo,
            tag=tag,
            asset_name=asset_name,
            capability=capability,
            scope_kind=scope_kind,
            scope_ref=scope_ref,
            resolve_root_key=effective_resolve_root_key,
        )["capabilities"]
    }
    statuses: list[dict[str, Any]] = []

    for entry in index["capabilities"]:
        capability_name = str(entry["capability"])
        if capability and capability != capability_name:
            continue
        state = tracked.get(capability_name, {})
        scanned_item = scanned.get(capability_name, {})
        installed = bool(scanned_item.get("installed"))
        local_version = str(state.get("version", "")).strip()
        local_tag = str(state.get("tag", "")).strip()
        local_status = str(scanned_item.get("local_status", "not_installed"))
        install_path = str(scanned_item.get("install_path", "")).strip()
        tracked_files = list(scanned_item.get("tracked_files", []))
        tracked_local_files = list(scanned_item.get("tracked_local_files", []))
        extra_local_files = list(scanned_item.get("extra_local_files", []))
        missing_local_files = list(scanned_item.get("missing_local_files", []))
        update_available = bool(scanned_item.get("update_available", False))
        updatable_files = list(scanned_item.get("updatable_files", []))
        ignored_new_files: list[str] = []
        missing_from_latest_manifest: list[str] = []
        if installed:
            package = _load_provider_package(entry)
            if package.capability != capability_name:
                raise PlatformError(
                    f"Content index capability '{capability_name}' did not match manifest capability '{package.capability}'.",
                    code="E_RELEASE_CONTENT_MANIFEST_INVALID",
                    reason="capability_mismatch",
                )
            remote_paths = package.rel_paths
            remote_set = set(remote_paths)
            local_set = set(tracked_files)
            ignored_new_files = sorted(remote_set - local_set)
            missing_from_latest_manifest = sorted(local_set - remote_set)

        statuses.append(
            {
                "capability": capability_name,
                "installed": installed,
                "local_status": local_status,
                "install_path": install_path,
                "local_version": local_version,
                "local_tag": local_tag,
                "latest_version": str(entry["version"]),
                "latest_tag": str(entry["tag"]),
                "provider": str(entry["provider"]),
                "source": dict(entry["source"]),
                "package_type": str(entry["package_type"]),
                "target_type": str(entry["target_type"]),
                "category": str(entry.get("category", CATEGORY_GHDP_MANAGED_MISC)),
                "policy": dict(entry["policy"]),
                "repo": str(entry["repo"]),
                "manifest_asset": str(entry["manifest_asset"]),
                "tracked_files": tracked_files,
                "tracked_local_files": tracked_local_files,
                "extra_local_files": extra_local_files,
                "missing_local_files": missing_local_files,
                "update_available": update_available,
                "updatable_files": updatable_files,
                "ignored_new_files": ignored_new_files,
                "missing_from_latest_manifest": missing_from_latest_manifest,
                "allow_update_existing_files": bool(entry["allow_update_existing_files"]),
                "allow_new_files_on_update": bool(entry["allow_new_files_on_update"]),
                "allow_install_if_missing": bool(entry["allow_install_if_missing"]),
                "recovery_hint": str(entry.get("recovery_hint", "")).strip(),
                "bootstrap_allowed": bool(scanned_item.get("bootstrap_allowed", False)),
                "recovery_mode": str(scanned_item.get("recovery_mode", "none")),
                "recovery_detail": str(scanned_item.get("recovery_detail", "")),
            }
        )

    return {
        "repo": index["repo"],
        "tag": index["tag"],
        "asset_name": index["asset_name"],
        "active_team": str(index.get("active_team", "")).strip(),
        "active_team_source": str(index.get("active_team_source", "")).strip(),
        "team_policy_loaded": bool(index.get("team_policy_loaded", False)),
        "capabilities": statuses,
    }


def preview_content_updates(
    *,
    repo: str | None = None,
    tag: str | None = None,
    asset_name: str | None = None,
    capability: str | None = None,
    scope_kind: str = DEFAULT_SCOPE_KIND,
    scope_ref: str | None = None,
    resolve_root_key: Callable[[str], Path] | None = None,
) -> dict[str, Any]:
    status = list_sync_status(
        repo=repo,
        tag=tag,
        asset_name=asset_name,
        capability=capability,
        scope_kind=scope_kind,
        scope_ref=scope_ref,
        resolve_root_key=resolve_root_key,
    )
    previews: list[dict[str, Any]] = []
    for item in status["capabilities"]:
        action = "none"
        if item["recovery_mode"] == "bootstrap":
            action = "install"
        elif item["recovery_mode"] == "repair":
            action = "repair"
        elif item["update_available"] and item["updatable_files"] and not item["missing_from_latest_manifest"]:
            action = "update"
        elif item["missing_from_latest_manifest"] or item["recovery_mode"] == "blocked":
            action = "blocked"
        preview = dict(item)
        preview["action"] = action
        previews.append(preview)
    status["capabilities"] = previews
    return status


def summarize_sync_categories(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in items:
        category = str(item.get("category", CATEGORY_GHDP_MANAGED_MISC)).strip() or CATEGORY_GHDP_MANAGED_MISC
        summary = grouped.setdefault(
            category,
            {
                "category": category,
                "installs": 0,
                "repairs": 0,
                "updates": 0,
                "blocked": 0,
                "none": 0,
                "capabilities": [],
            },
        )
        action = str(item.get("action", "none")).strip() or "none"
        if action == "install":
            summary["installs"] += 1
        elif action == "repair":
            summary["repairs"] += 1
        elif action == "update":
            summary["updates"] += 1
        elif action == "blocked":
            summary["blocked"] += 1
        else:
            summary["none"] += 1
        summary["capabilities"].append(str(item.get("capability", "")))

    preferred_order = [
        CATEGORY_GHDP_POLICY,
        CATEGORY_CLAUDE_PLUGINS,
        CATEGORY_CLAUDE_SKILLS,
        CATEGORY_CODEX_PLUGINS,
        CATEGORY_CODEX_SKILLS,
        CATEGORY_TABLEAU_DRIVERS,
        CATEGORY_GHDP_MANAGED_MISC,
    ]
    order_index = {name: index for index, name in enumerate(preferred_order)}
    return sorted(grouped.values(), key=lambda item: (order_index.get(str(item["category"]), len(order_index)), str(item["category"])))


def run_sync_actions(
    *,
    repo: str | None = None,
    tag: str | None = None,
    asset_name: str | None = None,
    capability: str | None = None,
    apply: bool = False,
    scope_kind: str = DEFAULT_SCOPE_KIND,
    scope_ref: str | None = None,
    resolve_root_key: Callable[[str], Path] | None = None,
) -> dict[str, Any]:
    effective_resolve_root_key = resolve_root_key or _default_resolve_root_key
    scan = scan_content_inventory(
        repo=repo,
        tag=tag,
        asset_name=asset_name,
        capability=capability,
        persist=True,
        scope_kind=scope_kind,
        scope_ref=scope_ref,
        resolve_root_key=effective_resolve_root_key,
    )
    preview = preview_content_updates(
        repo=repo,
        tag=tag,
        asset_name=asset_name,
        capability=capability,
        scope_kind=scope_kind,
        scope_ref=scope_ref,
        resolve_root_key=effective_resolve_root_key,
    )
    installs = [item for item in preview["capabilities"] if item["action"] in {"bootstrap", "install"}]
    repairs = [item for item in preview["capabilities"] if item["action"] == "repair"]
    updates = [item for item in preview["capabilities"] if item["action"] == "update"]
    blocked = [item for item in preview["capabilities"] if item["action"] == "blocked"]
    results = {"installs": [], "repairs": [], "updates": []}
    effective_preview = preview

    if apply:
        for item in repairs:
            results["repairs"].append(
                repair_content(
                    str(item["capability"]),
                    repo=repo,
                    tag=tag,
                    asset_name=asset_name,
                    scope_kind=scope_kind,
                    scope_ref=scope_ref,
                    resolve_root_key=effective_resolve_root_key,
                )
            )
        for item in installs:
            results["installs"].append(
                install_content_capability(
                    str(item["capability"]),
                    repo=repo,
                    tag=tag,
                    asset_name=asset_name,
                    scope_kind=scope_kind,
                    scope_ref=scope_ref,
                    resolve_root_key=effective_resolve_root_key,
                )
            )
        effective_preview = preview_content_updates(
            repo=repo,
            tag=tag,
            asset_name=asset_name,
            capability=capability,
            scope_kind=scope_kind,
            scope_ref=scope_ref,
            resolve_root_key=effective_resolve_root_key,
        )
        updates = [item for item in effective_preview["capabilities"] if item["action"] == "update"]
        blocked = [item for item in effective_preview["capabilities"] if item["action"] == "blocked"]
        for item in updates:
            results["updates"].append(
                apply_content_update(
                    str(item["capability"]),
                    repo=repo,
                    tag=tag,
                    asset_name=asset_name,
                    scope_kind=scope_kind,
                    scope_ref=scope_ref,
                    resolve_root_key=effective_resolve_root_key,
                )
            )

    return {
        "scan": scan,
        "preview": effective_preview,
        "active_team": str(preview.get("active_team", "")).strip(),
        "active_team_source": str(preview.get("active_team_source", "")).strip(),
        "team_policy_loaded": bool(preview.get("team_policy_loaded", False)),
        "installs": installs,
        "repairs": repairs,
        "updates": updates,
        "blocked": blocked,
        "results": results,
    }


def _download_manifest_entries(
    *,
    entry: dict[str, Any],
    file_specs: list[dict[str, str]],
    selected_paths: set[str],
) -> list[tuple[str, bytes]]:
    with tempfile.TemporaryDirectory(prefix="ghdp_release_content_") as tmpdir:
        download_dir = Path(tmpdir)
        entries: list[tuple[str, bytes]] = []
        for item in file_specs:
            target_path = item["target_path"]
            if target_path not in selected_paths:
                continue
            asset_path = _download_provider_asset(entry=entry, asset_name=item["asset_name"], download_dir=download_dir)
            entries.append((target_path, asset_path.read_bytes()))
        return entries


def install_content_capability(
    capability: str,
    *,
    repo: str | None = None,
    tag: str | None = None,
    asset_name: str | None = None,
    scope_kind: str = DEFAULT_SCOPE_KIND,
    scope_ref: str | None = None,
    resolve_root_key: Callable[[str], Path] | None = None,
) -> dict[str, Any]:
    index = fetch_content_index(repo=repo, tag=tag, asset_name=asset_name)
    entry = next((item for item in index["capabilities"] if str(item["capability"]) == capability), None)
    if entry is None:
        raise PlatformError(
            f"Capability '{capability}' is not present in the content index.",
            code="E_SYNC_CAPABILITY_NOT_FOUND",
            reason=capability,
        )
    if not bool(entry["allow_install_if_missing"]):
        raise PlatformError(
            f"Capability '{capability}' is not approved for install through GHDP sync.",
            code="E_SYNC_UPDATE_NOT_ALLOWED",
            reason=capability,
        )

    result = install_content_entry(
        entry,
        scope_kind=scope_kind,
        scope_ref=scope_ref,
        resolve_root_key=resolve_root_key or _default_resolve_root_key,
    )
    return {
        "capability": capability,
        "installed_count": int(result.get("updated_count", 0)),
        "target_path": str(result.get("target_path", "")),
        "latest_version": str(result.get("content_version", "")),
        "source": str(result.get("source", "")),
    }


def apply_content_update(
    capability: str,
    *,
    repo: str | None = None,
    tag: str | None = None,
    asset_name: str | None = None,
    scope_kind: str = DEFAULT_SCOPE_KIND,
    scope_ref: str | None = None,
    resolve_root_key: Callable[[str], Path] | None = None,
) -> dict[str, Any]:
    preview = preview_content_updates(
        repo=repo,
        tag=tag,
        asset_name=asset_name,
        capability=capability,
        scope_kind=scope_kind,
        scope_ref=scope_ref,
        resolve_root_key=resolve_root_key,
    )
    if not preview["capabilities"]:
        raise PlatformError(
            f"Capability '{capability}' is not present in the content index.",
            code="E_SYNC_CAPABILITY_NOT_FOUND",
            reason=capability,
        )
    item = preview["capabilities"][0]
    if not item["installed"]:
        raise PlatformError(
            f"Capability '{capability}' is not installed locally.",
            code="E_SYNC_NOT_INSTALLED",
            reason=capability,
        )
    if not item["allow_update_existing_files"]:
        raise PlatformError(
            f"Capability '{capability}' does not allow updating existing files.",
            code="E_SYNC_UPDATE_NOT_ALLOWED",
            reason=capability,
        )
    if item["missing_from_latest_manifest"]:
        raise PlatformError(
            f"Capability '{capability}' has tracked files missing from the latest manifest. {_sync_retry_hint(capability)}",
            code="E_SYNC_UPDATE_BLOCKED",
            reason=capability,
        )
    if not item["updatable_files"]:
        return {
            "capability": capability,
            "updated_count": 0,
            "ignored_new_files": item["ignored_new_files"],
            "latest_tag": item["latest_tag"],
            "latest_version": item["latest_version"],
            "message": "No eligible tracked files were available for update.",
        }

    package = _load_provider_package(item)
    if package.capability != capability:
        raise PlatformError(
            f"Manifest capability '{package.capability}' did not match expected capability '{capability}'.",
            code="E_RELEASE_CONTENT_MANIFEST_INVALID",
            reason="capability_mismatch",
        )

    target_root = Path(str(item["install_path"])).expanduser()
    entries = _download_manifest_entries(
        entry=item,
        file_specs=package.files,
        selected_paths=set(item["updatable_files"]),
    )
    updated = _write_entries(target_root, entries)
    tracked_files = list(item["tracked_files"])
    local_files = _list_local_rel_paths(target_root)
    extra_local_files = sorted(rel for rel in local_files if rel not in set(tracked_files))
    content_hash_entries = _installed_entries(target_root, tracked_files) or entries
    update_tool_state(
        _state_key(capability, scope_kind, scope_ref),
        _build_state_patch(
            capability=capability,
            item={
                **item,
                "latest_version": package.version,
                "content_hash": _content_hash(content_hash_entries),
                "category": str(item.get("category", CATEGORY_GHDP_MANAGED_MISC)).strip() or CATEGORY_GHDP_MANAGED_MISC,
            },
            source="release",
            scope_kind=scope_kind,
            scope_ref=scope_ref,
            detected_local_files=extra_local_files,
        ),
    )
    return {
        "capability": capability,
        "updated_count": updated,
        "updated_files": item["updatable_files"],
        "ignored_new_files": item["ignored_new_files"],
        "latest_tag": item["latest_tag"],
        "latest_version": package.version,
    }


def _bootstrap_install_content(
    capability: str,
    *,
    item: dict[str, Any],
    scope_kind: str,
    scope_ref: str | None,
    resolve_root_key: Callable[[str], Path] | None,
) -> dict[str, Any]:
    if not item.get("bootstrap_allowed"):
        raise PlatformError(
            f"Capability '{capability}' is missing locally and bootstrap recovery is not allowed.",
            code="E_SYNC_BOOTSTRAP_NOT_ALLOWED",
            reason=capability,
        )
    if not item.get("install_path"):
        raise PlatformError(
            f"Capability '{capability}' does not have a resolvable install path for this scope.",
            code="E_SYNC_ROOT_KEY_UNRESOLVABLE",
            reason=capability,
        )
    install_result = install_content_entry(
        {
            "capability": capability,
            "provider": str(item.get("provider", DEFAULT_PROVIDER)).strip() or DEFAULT_PROVIDER,
            "source": dict(item.get("source", {})) if isinstance(item.get("source"), dict) else {},
            "repo": str(item.get("repo", "")).strip(),
            "tag": str(item.get("latest_tag", item.get("tag", ""))).strip(),
            "manifest_asset": str(item.get("manifest_asset", DEFAULT_MANIFEST_ASSET)).strip() or DEFAULT_MANIFEST_ASSET,
            "package_type": str(item.get("package_type", DEFAULT_PACKAGE_TYPE)).strip() or DEFAULT_PACKAGE_TYPE,
            "target_type": str(item.get("target_type", DEFAULT_TARGET_TYPE)).strip() or DEFAULT_TARGET_TYPE,
            "policy": dict(item.get("policy", {})) if isinstance(item.get("policy"), dict) else {},
        },
        resolve_root_key=resolve_root_key or _default_resolve_root_key,
        scope_kind=scope_kind,
        scope_ref=scope_ref,
    )
    return {
        **install_result,
        "local_status": "bootstrapped",
        "repaired_count": int(install_result.get("file_count", 0)),
        "repaired_files": list(item.get("tracked_files", [])),
    }


def _raise_missing_local_recovery_error(capability: str, item: dict[str, Any]) -> None:
    detail = str(item.get("recovery_detail", "")).strip()
    if detail == "install_target_unresolvable":
        raise PlatformError(
            f"Capability '{capability}' cannot be bootstrap-installed because its install target is not resolvable for this scope. "
            f"{_sync_retry_hint(capability)}",
            code="E_SYNC_ROOT_KEY_UNRESOLVABLE",
            reason=capability,
        )
    if detail == "install_if_missing_disabled":
        raise PlatformError(
            f"Capability '{capability}' is missing locally and its content policy does not allow install-if-missing recovery. "
            f"{_sync_retry_hint(capability)}",
            code="E_SYNC_BOOTSTRAP_NOT_ALLOWED",
            reason=capability,
        )
    raise PlatformError(
        f"Capability '{capability}' is not installed locally. {_sync_retry_hint(capability)}",
        code="E_SYNC_NOT_INSTALLED",
        reason=capability,
    )


def repair_content(
    capability: str,
    *,
    manifest_asset: str | None = None,
    repo: str | None = None,
    tag: str | None = None,
    asset_name: str | None = None,
    scope_kind: str = DEFAULT_SCOPE_KIND,
    scope_ref: str | None = None,
    resolve_root_key: Callable[[str], Path] | None = None,
) -> dict[str, Any]:
    effective_resolve_root_key = resolve_root_key or _default_resolve_root_key
    state = get_tool_state(_state_key(capability, scope_kind, scope_ref))
    if state and str(state.get("provider", DEFAULT_PROVIDER)).strip() == MARKETPLACE_PROVIDER:
        allowed_entry = next(
            (
                item
                for item in fetch_content_index(repo=repo, tag=tag, asset_name=asset_name)["capabilities"]
                if str(item["capability"]) == capability
            ),
            None,
        )
        if allowed_entry is None or not bool(allowed_entry["allow_install_if_missing"]):
            raise PlatformError(
                f"Capability '{capability}' is no longer allowlisted for marketplace sync repair.",
                code="E_SYNC_POLICY_BLOCKED",
                reason=capability,
            )
    if state:
        install_status, target_root, tracked_files, missing_local_files = _content_status_from_state(capability, state)
        if install_status == "not_installed":
            state = {}
        else:
            if target_root is None:
                raise PlatformError(
                    f"Capability '{capability}' is missing a valid install path in state.",
                    code="E_SYNC_REPAIR_FAILED",
                    reason=capability,
                )
            if not missing_local_files:
                return {
                    "capability": capability,
                    "repaired_count": 0,
                    "repaired_files": [],
                    "local_status": install_status,
                }

            repo = str(state.get("repo", "")).strip()
            tag = str(state.get("tag", "")).strip()
            manifest_name = manifest_asset or str(state.get("manifest_asset", "")).strip() or DEFAULT_MANIFEST_ASSET
            provider = str(state.get("provider", DEFAULT_PROVIDER)).strip() or DEFAULT_PROVIDER
            provider_source = (
                dict(state.get("provider_source", {}))
                if isinstance(state.get("provider_source"), dict)
                else {"repo": repo, "tag": tag, "manifest_asset": manifest_name}
            )
            if repo and tag:
                package = _load_provider_package(
                    {
                        "provider": provider,
                        "repo": repo,
                        "tag": tag,
                        "manifest_asset": manifest_name,
                        "source": provider_source,
                        "target_type": str(state.get("target_type", DEFAULT_TARGET_TYPE)).strip() or DEFAULT_TARGET_TYPE,
                    }
                )
            else:
                preview = preview_content_updates(
                    repo=repo or None,
                    tag=tag or None,
                    asset_name=asset_name or None,
                    capability=capability,
                    scope_kind=scope_kind,
                    scope_ref=scope_ref,
                    resolve_root_key=resolve_root_key,
                )
                if not preview["capabilities"]:
                    raise PlatformError(
                        f"Capability '{capability}' is not present in the content index.",
                        code="E_SYNC_CAPABILITY_NOT_FOUND",
                        reason=capability,
                    )
                item = preview["capabilities"][0]
                repo = str(item["repo"])
                tag = str(item["latest_tag"])
                manifest_name = manifest_asset or str(item["manifest_asset"]) or DEFAULT_MANIFEST_ASSET
                package = _load_provider_package(item)
    if not state:
        preview = preview_content_updates(
            repo=repo,
            tag=tag,
            asset_name=asset_name,
            capability=capability,
            scope_kind=scope_kind,
            scope_ref=scope_ref,
            resolve_root_key=effective_resolve_root_key,
        )
        if not preview["capabilities"]:
            raise PlatformError(
                f"Capability '{capability}' is not present in the content index.",
                code="E_SYNC_CAPABILITY_NOT_FOUND",
                reason=capability,
            )
        item = preview["capabilities"][0]
        if item.get("recovery_mode") == "bootstrap":
            return _bootstrap_install_content(
                capability,
                item=item,
                scope_kind=scope_kind,
                scope_ref=scope_ref,
                resolve_root_key=effective_resolve_root_key,
            )
        if not item["installed"]:
            _raise_missing_local_recovery_error(capability, item)
        target_root = Path(str(item["install_path"])).expanduser()
        tracked_files = list(item["tracked_files"])
        missing_local_files = list(item["missing_local_files"])
        if not missing_local_files:
            return {
                "capability": capability,
                "repaired_count": 0,
                "repaired_files": [],
                "local_status": str(item["local_status"]),
            }
        repo = str(item["repo"])
        tag = str(item["latest_tag"])
        manifest_name = manifest_asset or str(item["manifest_asset"]) or DEFAULT_MANIFEST_ASSET
        package = _load_provider_package(item)
    if package.capability != capability:
        raise PlatformError(
            f"Manifest capability '{package.capability}' did not match expected capability '{capability}'.",
            code="E_RELEASE_CONTENT_MANIFEST_INVALID",
            reason="capability_mismatch",
        )

    remote_paths = set(package.rel_paths)
    if any(rel not in remote_paths for rel in tracked_files):
        raise PlatformError(
            f"Capability '{capability}' has tracked files missing from its recorded manifest. {_sync_retry_hint(capability)}",
            code="E_SYNC_REPAIR_FAILED",
            reason=capability,
        )

    entries = _download_manifest_entries(
        entry={
            "provider": str(state.get("provider", DEFAULT_PROVIDER)).strip() or DEFAULT_PROVIDER if state else DEFAULT_PROVIDER,
            "repo": repo,
            "tag": tag,
            "manifest_asset": manifest_name,
            "source": dict(state.get("provider_source", {})) if state and isinstance(state.get("provider_source"), dict) else {
                "repo": repo,
                "tag": tag,
                "manifest_asset": manifest_name,
            },
            "package_type": str(state.get("package_type", DEFAULT_PACKAGE_TYPE)).strip() if state else DEFAULT_PACKAGE_TYPE,
            "target_type": str(state.get("target_type", DEFAULT_TARGET_TYPE)).strip() if state else DEFAULT_TARGET_TYPE,
            "policy": dict(state.get("policy", {})) if state and isinstance(state.get("policy"), dict) else {},
        },
        file_specs=package.files,
        selected_paths=set(missing_local_files),
    )
    repaired = _write_entries(target_root, entries)
    content_hash_entries = _installed_entries(target_root, tracked_files) or entries
    extra_local_files = sorted(rel for rel in _list_local_rel_paths(target_root) if rel not in set(tracked_files))
    update_tool_state(
        _state_key(capability, scope_kind, scope_ref),
        _build_state_patch(
            capability=capability,
            item={
                "provider": str(state.get("provider", DEFAULT_PROVIDER)).strip() or DEFAULT_PROVIDER if state else DEFAULT_PROVIDER,
                "source": dict(state.get("provider_source", {})) if state and isinstance(state.get("provider_source"), dict) else {
                    "repo": repo,
                    "tag": tag,
                    "manifest_asset": manifest_name,
                },
                "package_type": str(state.get("package_type", DEFAULT_PACKAGE_TYPE)).strip() if state else DEFAULT_PACKAGE_TYPE,
                "target_type": str(state.get("target_type", DEFAULT_TARGET_TYPE)).strip() if state else DEFAULT_TARGET_TYPE,
                "category": str(state.get("category", CATEGORY_GHDP_MANAGED_MISC)).strip() if state else CATEGORY_GHDP_MANAGED_MISC,
                "policy": dict(state.get("policy", {})) if state and isinstance(state.get("policy"), dict) else {},
                "repo": repo,
                "latest_tag": tag,
                "latest_version": package.version,
                "manifest_asset": manifest_name,
                "install_path": str(target_root),
                "tracked_files": tracked_files,
                "content_hash": _content_hash(content_hash_entries),
            },
            source="existing",
            scope_kind=scope_kind,
            scope_ref=scope_ref,
            detected_local_files=extra_local_files,
        ),
    )
    return {
        "capability": capability,
        "repaired_count": repaired,
        "repaired_files": missing_local_files,
        "local_status": "repaired",
    }


def install_content_entry(
    entry: dict[str, Any],
    *,
    scope_kind: str = DEFAULT_SCOPE_KIND,
    scope_ref: str | None = None,
    resolve_root_key: Callable[[str], Path] = _default_resolve_root_key,
) -> dict[str, object]:
    expected_capability = str(entry.get("capability", "")).strip()
    if not expected_capability:
        raise PlatformError(
            "Managed content install requires a capability id.",
            code="E_BAD_ARGS",
            reason="capability",
        )

    state_name = _state_key(expected_capability, scope_kind, scope_ref)
    package, target_root, entries = _load_content_entries(entry=entry, resolve_root_key=resolve_root_key)
    if package.capability != expected_capability:
        raise PlatformError(
            f"Managed content capability '{package.capability}' did not match expected capability '{expected_capability}'.",
            code="E_RELEASE_CONTENT_MANIFEST_INVALID",
            reason="capability_mismatch",
        )

    saved_state = get_tool_state(state_name)
    expected_rel_paths = [rel for rel, _ in entries]
    saved_target = str(saved_state.get("install_path", "")).strip()
    installed_target_root = Path(saved_target).expanduser() if saved_target else target_root
    existing = _installed_entries(installed_target_root, expected_rel_paths)
    now = int(time.time())
    provider = str(entry.get("provider", DEFAULT_PROVIDER)).strip() or DEFAULT_PROVIDER
    source = _provider_source(entry)
    package_type = str(entry.get("package_type", DEFAULT_PACKAGE_TYPE)).strip() or DEFAULT_PACKAGE_TYPE
    target_type = str(entry.get("target_type", DEFAULT_TARGET_TYPE)).strip() or DEFAULT_TARGET_TYPE
    category = str(entry.get("category", CATEGORY_GHDP_MANAGED_MISC)).strip() or CATEGORY_GHDP_MANAGED_MISC
    policy = dict(entry.get("policy", {})) if isinstance(entry.get("policy"), dict) else {}

    if existing is not None:
        update_tool_state(
            state_name,
            {
                "capability": expected_capability,
                "provider": provider,
                "provider_source": source,
                "package_type": package_type,
                "target_type": target_type,
                "category": category,
                "policy": policy,
                "repo": str(source.get("repo", "")).strip(),
                "tag": str(source.get("tag", "")).strip(),
                "version": package.version,
                "manifest_asset": str(source.get("manifest_asset", DEFAULT_MANIFEST_ASSET)).strip() or DEFAULT_MANIFEST_ASSET,
                "install_path": str(installed_target_root),
                "files": expected_rel_paths,
                "content_hash": _content_hash(existing),
                "last_verified_at": now,
                "last_status": "ok",
                "source": "existing",
                "scope_kind": _normalize_scope_kind(scope_kind),
                "scope_ref": _normalize_scope_ref(scope_kind, scope_ref),
            },
        )
        return {
            "capability": expected_capability,
            "target_path": str(installed_target_root),
            "file_count": len(existing),
            "updated_count": 0,
            "content_hash": _content_hash(existing),
            "synced_at": now,
            "source": "existing",
            "release_repo": str(source.get("repo", "")).strip(),
            "release_tag": str(source.get("tag", "")).strip(),
            "content_version": package.version,
        }

    updated = _write_entries(target_root, entries)
    update_tool_state(
        state_name,
        {
            **_build_state_patch(
                capability=expected_capability,
                item={
                    "provider": provider,
                    "source": source,
                    "package_type": package_type,
                    "target_type": target_type,
                    "category": category,
                    "policy": policy,
                    "repo": str(source.get("repo", "")).strip(),
                    "latest_tag": str(source.get("tag", "")).strip(),
                    "latest_version": package.version,
                    "manifest_asset": str(source.get("manifest_asset", DEFAULT_MANIFEST_ASSET)).strip() or DEFAULT_MANIFEST_ASSET,
                    "install_path": str(target_root),
                    "tracked_files": expected_rel_paths,
                    "content_hash": _content_hash(entries),
                    "extra_local_files": [],
                },
                source="release",
                scope_kind=scope_kind,
                scope_ref=scope_ref,
            ),
            "installed_at": now,
        },
    )
    return {
        "capability": expected_capability,
        "target_path": str(target_root),
        "file_count": len(entries),
        "updated_count": updated,
        "content_hash": _content_hash(entries),
        "synced_at": now,
        "source": "release",
        "release_repo": str(source.get("repo", "")).strip(),
        "release_tag": str(source.get("tag", "")).strip(),
        "content_version": package.version,
    }


def install_release_content(
    *,
    capability: str,
    repo: str,
    tag: str,
    resolve_root_key: Callable[[str], Path],
    manifest_asset: str = DEFAULT_MANIFEST_ASSET,
) -> dict[str, object]:
    expected_capability = capability
    now = int(time.time())
    existing_result: dict[str, object] | None = None
    state_name = _state_key(capability)
    saved_state = get_tool_state(state_name)
    if str(saved_state.get("repo", "")).strip() == repo and str(saved_state.get("tag", "")).strip() == tag:
        saved_target = str(saved_state.get("install_path", "")).strip()
        saved_files = _normalize_rel_paths(saved_state.get("files", []))
        if saved_target and saved_files:
            installed_target_root = Path(saved_target).expanduser()
            existing = _installed_entries(installed_target_root, saved_files)
            if existing is not None:
                update_tool_state(
                    state_name,
                    {
                        "provider": DEFAULT_PROVIDER,
                        "provider_source": {
                            "repo": repo,
                            "tag": tag,
                            "manifest_asset": manifest_asset,
                        },
                        "package_type": DEFAULT_PACKAGE_TYPE,
                        "target_type": str(saved_state.get("target_type", DEFAULT_TARGET_TYPE)).strip() or DEFAULT_TARGET_TYPE,
                        "category": str(
                            saved_state.get(
                                "category",
                                _infer_category(
                                    capability=capability,
                                    target_type=str(saved_state.get("target_type", DEFAULT_TARGET_TYPE)).strip() or DEFAULT_TARGET_TYPE,
                                ),
                            )
                        ).strip()
                        or CATEGORY_GHDP_MANAGED_MISC,
                        "policy": {},
                        "last_verified_at": now,
                        "last_status": "ok",
                        "source": "existing",
                        "content_hash": _content_hash(existing),
                        "manifest_asset": manifest_asset,
                    },
                )
                existing_result = {
                    "capability": capability,
                    "target_path": str(installed_target_root),
                    "file_count": len(existing),
                    "updated_count": 0,
                    "content_hash": _content_hash(existing),
                    "synced_at": now,
                    "source": "existing",
                    "release_repo": repo,
                    "release_tag": tag,
                    "content_version": str(saved_state.get("version", "")).strip(),
                }

    if existing_result is not None:
        return existing_result

    result = install_content_entry(
        {
            "capability": expected_capability,
            "provider": DEFAULT_PROVIDER,
            "source": {
                "repo": repo,
                "tag": tag,
                "manifest_asset": manifest_asset,
            },
            "repo": repo,
            "tag": tag,
            "manifest_asset": manifest_asset,
            "package_type": DEFAULT_PACKAGE_TYPE,
            "target_type": DEFAULT_TARGET_TYPE,
            "policy": {},
        },
        resolve_root_key=resolve_root_key,
    )
    if str(result.get("capability", "")).strip() != expected_capability:
        raise PlatformError(
            f"Release content manifest capability '{result.get('capability', '')}' did not match expected capability '{expected_capability}'.",
            code="E_RELEASE_CONTENT_MANIFEST_INVALID",
            reason="capability_mismatch",
        )
    return result
