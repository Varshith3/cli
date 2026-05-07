from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Dict

from platform_cli.core.errors import PlatformError
from platform_cli.manifests.load import toolset_source_kind
from platform_cli.state.store import get_tool_state, update_tool_state

ALLOWED_OWNERS = frozenset({"ghdp", "user"})
TRUSTED_POLICY_SOURCES = frozenset({"managed", "packaged"})
DEFAULT_OWNER = "ghdp"


@dataclass(frozen=True)
class OwnershipPolicy:
    default_owner: str = DEFAULT_OWNER
    allow_user_override: bool = False
    source_label: str = ""
    source_kind: str = "unknown"
    trusted_source: bool = False
    fingerprint: str = ""


@dataclass(frozen=True)
class OwnershipResolution:
    effective_owner: str
    effective_source: str
    override_owner: str
    allow_user_override: bool
    policy: OwnershipPolicy
    state_patch: Dict[str, Any]


def _now_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _normalize_owner(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in ALLOWED_OWNERS else ""


def ownership_source_label(owner: str) -> str:
    normalized = _normalize_owner(owner)
    if normalized == "ghdp":
        return "managed"
    if normalized == "user":
        return "override"
    return "unknown"


def policy_source_label(policy: OwnershipPolicy) -> str:
    return policy.source_kind if policy.trusted_source else "untrusted"


def _version_requirement(requirement: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not isinstance(requirement, dict):
        return None
    op = requirement.get("op")
    version = requirement.get("version")
    if op is None and version is None:
        return None
    return {"op": op, "version": version}


def build_ownership_policy(requirement: Dict[str, Any] | None, toolset_source: str) -> OwnershipPolicy:
    source_label = str(toolset_source or "").strip()
    source_kind = toolset_source_kind(source_label)
    trusted_source = source_kind in TRUSTED_POLICY_SOURCES
    ownership = requirement.get("ownership", {}) if isinstance(requirement, dict) else {}
    if not isinstance(ownership, dict):
        ownership = {}

    default_owner = _normalize_owner(ownership.get("default_owner")) or DEFAULT_OWNER
    allow_user_override = bool(ownership.get("allow_user_override", False))

    if not trusted_source:
        default_owner = DEFAULT_OWNER
        allow_user_override = False

    payload = {
        "default_owner": default_owner,
        "allow_user_override": allow_user_override,
        "source_kind": source_kind,
        "source_label": source_label,
        "trusted_source": trusted_source,
        "version_req": _version_requirement(requirement),
    }
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return OwnershipPolicy(
        default_owner=default_owner,
        allow_user_override=allow_user_override,
        source_label=source_label,
        source_kind=source_kind,
        trusted_source=trusted_source,
        fingerprint=fingerprint,
    )


def _ownership_state(state: Dict[str, Any]) -> Dict[str, Any]:
    value = state.get("ownership", {})
    return dict(value) if isinstance(value, dict) else {}


def _build_patch(
    state: Dict[str, Any],
    policy: OwnershipPolicy,
    *,
    effective_owner: str,
    effective_source: str,
    override_owner: str,
    override_source: str,
    override_updated_at: str,
    override_cleared_at: str,
    override_last_invalid_owner: str,
) -> Dict[str, Any]:
    existing = _ownership_state(state)
    patch_ownership = {
        "schema_version": "1.0",
        "override_owner": override_owner,
        "override_source": override_source,
        "override_updated_at": override_updated_at,
        "override_cleared_at": override_cleared_at,
        "override_last_invalid_owner": override_last_invalid_owner,
        "policy_default_owner": policy.default_owner,
        "policy_allow_user_override": policy.allow_user_override,
        "policy_source_kind": policy.source_kind,
        "policy_source_label": policy.source_label,
        "policy_trusted_source": policy.trusted_source,
        "policy_fingerprint": policy.fingerprint,
        "effective_source": effective_source,
        "reconciled_at": _now_ts(),
    }
    if existing.get("first_seen_at"):
        patch_ownership["first_seen_at"] = existing["first_seen_at"]
    else:
        patch_ownership["first_seen_at"] = _now_ts()

    return {
        "managed_by": effective_owner,
        "ownership": patch_ownership,
    }


def resolve_ownership_state(state: Dict[str, Any], policy: OwnershipPolicy) -> OwnershipResolution:
    ownership = _ownership_state(state)
    override_owner = _normalize_owner(ownership.get("override_owner"))
    override_source = str(ownership.get("override_source", "") or "").strip()
    override_updated_at = str(ownership.get("override_updated_at", "") or "").strip()
    override_cleared_at = str(ownership.get("override_cleared_at", "") or "").strip()
    override_last_invalid_owner = str(ownership.get("override_last_invalid_owner", "") or "").strip()
    legacy_managed_by = _normalize_owner(state.get("managed_by"))

    effective_owner = policy.default_owner
    effective_source = "policy_default"

    if override_owner == "user":
        if policy.allow_user_override:
            effective_owner = "user"
            effective_source = "override"
        else:
            override_last_invalid_owner = "user"
            override_owner = ""
            override_source = "policy_revoked"
            override_updated_at = ""
            override_cleared_at = _now_ts()
            effective_source = "policy_revoked"
    elif override_owner:
        override_last_invalid_owner = override_owner
        override_owner = ""
        override_source = "invalid_override_removed"
        override_updated_at = ""
        override_cleared_at = _now_ts()
        effective_source = "invalid_override_removed"
    elif legacy_managed_by == "user":
        if policy.allow_user_override:
            override_owner = "user"
            override_source = "legacy_managed_by"
            override_updated_at = _now_ts()
            override_cleared_at = ""
            effective_owner = "user"
            effective_source = "legacy_migration"
        else:
            override_last_invalid_owner = "user"
            override_source = "legacy_revoked"
            override_updated_at = ""
            override_cleared_at = _now_ts()
            effective_source = "legacy_revoked"
    elif legacy_managed_by == "ghdp":
        effective_owner = "ghdp"
        effective_source = "legacy_ghdp"

    patch = _build_patch(
        state,
        policy,
        effective_owner=effective_owner,
        effective_source=effective_source,
        override_owner=override_owner,
        override_source=override_source,
        override_updated_at=override_updated_at,
        override_cleared_at=override_cleared_at,
        override_last_invalid_owner=override_last_invalid_owner,
    )
    return OwnershipResolution(
        effective_owner=effective_owner,
        effective_source=effective_source,
        override_owner=override_owner,
        allow_user_override=policy.allow_user_override,
        policy=policy,
        state_patch=patch,
    )


def reconcile_tool_ownership(tool_name: str, policy: OwnershipPolicy) -> OwnershipResolution:
    state = get_tool_state(tool_name)
    resolution = resolve_ownership_state(state, policy)
    update_tool_state(tool_name, resolution.state_patch)
    return resolution


def format_ownership_compact(resolution: OwnershipResolution) -> str:
    return (
        f" owner='{resolution.effective_owner}'"
        f" owner_source='{ownership_source_label(resolution.effective_owner)}'"
    )


def format_ownership_details(resolution: OwnershipResolution) -> str:
    parts = [
        f"owner='{resolution.effective_owner}'",
        f"owner_source='{ownership_source_label(resolution.effective_owner)}'",
        f"policy_source='{policy_source_label(resolution.policy)}'",
        f"resolution_source='{resolution.effective_source}'",
        f"user_override_allowed={'yes' if resolution.allow_user_override else 'no'}",
    ]
    if resolution.override_owner:
        parts.append(f"override='{resolution.override_owner}'")
    return " ".join(parts)


def set_tool_ownership_override(tool_name: str, policy: OwnershipPolicy, owner: str, *, source: str) -> OwnershipResolution:
    normalized_owner = _normalize_owner(owner)
    if not normalized_owner:
        raise PlatformError(
            f"Unsupported owner '{owner}'. Use 'ghdp' or 'user'.",
            code="E_TOOL_OWNERSHIP_INVALID",
            reason=tool_name,
        )

    if normalized_owner == "user" and not policy.allow_user_override:
        raise PlatformError(
            f"Tool '{tool_name}' does not allow user-managed ownership under the current policy.",
            code="E_TOOL_OWNERSHIP_NOT_ALLOWED",
            reason=tool_name,
        )

    state = get_tool_state(tool_name)
    ownership = _ownership_state(state)
    if normalized_owner == "ghdp":
        ownership["override_owner"] = ""
        ownership["override_source"] = source
        ownership["override_updated_at"] = ""
        ownership["override_cleared_at"] = _now_ts()
    else:
        ownership["override_owner"] = "user"
        ownership["override_source"] = source
        ownership["override_updated_at"] = _now_ts()
        ownership["override_cleared_at"] = ""
    ownership["override_last_invalid_owner"] = ""
    update_tool_state(tool_name, {"ownership": ownership})
    return reconcile_tool_ownership(tool_name, policy)


def clear_tool_ownership_override(tool_name: str, policy: OwnershipPolicy, *, source: str) -> OwnershipResolution:
    state = get_tool_state(tool_name)
    ownership = _ownership_state(state)
    ownership["override_owner"] = ""
    ownership["override_source"] = source
    ownership["override_updated_at"] = ""
    ownership["override_cleared_at"] = _now_ts()
    ownership["override_last_invalid_owner"] = ""
    update_tool_state(tool_name, {"ownership": ownership})
    return reconcile_tool_ownership(tool_name, policy)
