from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import typer
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.errors import PlatformError
from platform_cli.core.github_auth import gh_subprocess_env
from platform_cli.exec.runner import run_cmd
from platform_cli.manifests.load import (
    load_access_policy,
    load_optional_team_policy,
    load_packaged_team_sync_policy_fallback,
    preferred_user_access_policy_path,
)
from platform_cli.core.release_policy import release_runtime
from platform_cli.state.access_session import (
    append_access_event,
    get_active_token as get_state_active_token,
    get_assumed_team,
    get_remembered_actor,
    set_remembered_actor,
)
from platform_cli.state.admin_signer import (
    default_admin_signer_paths,
    has_local_signer,
    read_private_key_pem,
    read_signer_metadata,
    write_private_key_pem,
    write_signer_metadata,
)


RETURN_FROM_ASSUMED_TEAM = "admin.mode.return"
TOKEN_SCOPE_USER = "user"
TOKEN_SCOPE_TEAM = "team"
TOKEN_SCOPE_USER_TEAM = "user_team"
TOKEN_SCOPES = {TOKEN_SCOPE_USER, TOKEN_SCOPE_TEAM, TOKEN_SCOPE_USER_TEAM}
TOKEN_VERSION = 2
TOKEN_ALGORITHM_ED25519 = "ed25519"


@dataclass(frozen=True)
class ActorResolution:
    login: str
    status: str
    source: str


@dataclass(frozen=True)
class TokenClaims:
    actor: str
    capabilities: tuple[str, ...]
    team: str
    scope: str
    issued_at: int
    expires_at: int
    raw: str


@dataclass(frozen=True)
class TokenEvaluation:
    status: str
    claims: Optional[TokenClaims]
    message: str = ""


@dataclass(frozen=True)
class AccessContext:
    actor: str
    identity_status: str
    actor_source: str
    base_persona: str
    persona: str
    active_mode: str
    admin_users_source: str
    selected_team: str
    effective_team: str
    assumed_team: str
    team_locked: bool
    token_status: str
    token_source: str
    token_scope: str
    token_team: str
    token_expires_at: int
    capabilities: tuple[str, ...]
    policy_source: str
    release_channel: str
    release_policy_source: str
    support_contact: str


@dataclass(frozen=True)
class SyncCapabilityPolicy:
    context: AccessContext
    restricted: bool
    allow_configured: bool
    allowed_capabilities: tuple[str, ...]
    denied_capabilities: tuple[str, ...]


@dataclass(frozen=True)
class CapabilityDecision:
    capability: str
    command_name: str
    status: str
    message: str
    code: str
    reason: str
    context: Optional[AccessContext]


@dataclass(frozen=True)
class _RuntimeAccess:
    context: AccessContext
    actor: ActorResolution
    token_eval: TokenEvaluation
    capabilities: tuple[str, ...]


def _current_selected_team() -> str:
    from platform_cli.core.config import get_value

    return str(get_value("team.selected", "") or "").strip()


def _load_policy() -> tuple[dict[str, Any], str]:
    policy, source = load_access_policy()
    if not isinstance(policy, dict):
        raise PlatformError(
            "Access policy must be a JSON object.",
            code="E_ACCESS_POLICY_INVALID",
            reason="policy_root",
        )
    return policy, source


def _load_team_policy() -> tuple[dict[str, Any] | None, str]:
    payload, source = load_optional_team_policy()
    if payload is not None and not isinstance(payload, dict):
        raise PlatformError(
            "Team policy must be a JSON object.",
            code="E_ACCESS_POLICY_INVALID",
            reason="team_policy_root",
        )
    return payload, source


def _merge_team_sync_fallback(
    payload: dict[str, Any] | None,
    fallback_payload: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, bool]:
    if not isinstance(fallback_payload, dict):
        return payload, False
    fallback_teams = fallback_payload.get("teams")
    if not isinstance(fallback_teams, dict):
        raise PlatformError(
            "Packaged sync policy fallback must contain a teams object.",
            code="E_SYNC_POLICY_INVALID",
            reason="fallback_team_policy_root",
        )

    if payload is None:
        return fallback_payload, True

    merged = json.loads(json.dumps(payload))
    merged_teams = merged.get("teams")
    if not isinstance(merged_teams, dict):
        merged_teams = {}
        merged["teams"] = merged_teams

    changed = False
    for team_name, fallback_team_payload in fallback_teams.items():
        if not isinstance(fallback_team_payload, dict):
            continue
        fallback_sync = fallback_team_payload.get("sync")
        if not isinstance(fallback_sync, dict):
            continue
        existing_team_payload = merged_teams.get(team_name)
        if not isinstance(existing_team_payload, dict):
            merged_teams[team_name] = {"sync": dict(fallback_sync)}
            changed = True
            continue
        existing_sync = existing_team_payload.get("sync")
        if not isinstance(existing_sync, dict):
            existing_team_payload["sync"] = dict(fallback_sync)
            changed = True

    return merged, changed


def _load_team_sync_policy() -> tuple[dict[str, Any] | None, str]:
    payload, source = _load_team_policy()
    fallback_payload, fallback_source = load_packaged_team_sync_policy_fallback()
    merged, fallback_applied = _merge_team_sync_fallback(payload, fallback_payload)
    if merged is None:
        return None, source
    if fallback_applied and fallback_source != "missing":
        return merged, f"{source}+{fallback_source}"
    return merged, source


def _persona_capabilities(policy: dict[str, Any], persona: str) -> set[str]:
    personas = policy.get("personas", {})
    if not isinstance(personas, dict):
        raise PlatformError(
            "Access policy is missing personas.",
            code="E_ACCESS_POLICY_INVALID",
            reason="personas",
        )
    key = "admin" if persona == "admin" else "non_admin"
    payload = personas.get(key, {})
    capabilities = payload.get("capabilities", []) if isinstance(payload, dict) else []
    if not isinstance(capabilities, list):
        raise PlatformError(
            "Access policy persona capabilities must be a list.",
            code="E_ACCESS_POLICY_INVALID",
            reason="persona_capabilities",
        )
    return {str(item).strip() for item in capabilities if str(item).strip()}


def classify_token_scope(target_actor: str | None = None, team: str | None = None) -> str:
    actor_value = str(target_actor or "").strip()
    team_value = str(team or "").strip()
    if actor_value and team_value:
        return TOKEN_SCOPE_USER_TEAM
    if actor_value:
        return TOKEN_SCOPE_USER
    if team_value:
        return TOKEN_SCOPE_TEAM
    return ""


def _token_scope_from_payload(payload: dict[str, Any]) -> tuple[str, str]:
    token_actor = str(payload.get("actor", "") or "").strip()
    token_team = str(payload.get("team", "") or "").strip()
    inferred_scope = classify_token_scope(token_actor, token_team)
    explicit_scope = str(payload.get("scope", "") or "").strip()
    if explicit_scope:
        if explicit_scope not in TOKEN_SCOPES:
            return "", "Admin token scope is invalid."
        if not inferred_scope:
            return "", "Admin token scope does not match its claims."
        if explicit_scope != inferred_scope:
            return "", "Admin token scope does not match its claims."
        return explicit_scope, ""
    if not inferred_scope:
        return "", "Admin token must be scoped to a user, a team, or both."
    return inferred_scope, ""


def _token_allowed_capabilities(policy: dict[str, Any], *, scope: str | None = None) -> set[str]:
    token = policy.get("token", {})
    if not isinstance(token, dict):
        token = {}

    values = token.get("allowed_capabilities", [])
    if values is None:
        values = []
    if not isinstance(values, list):
        raise PlatformError(
            "Access policy token allowed_capabilities must be a list.",
            code="E_ACCESS_POLICY_INVALID",
            reason="token_allowed_capabilities",
        )
    legacy = {str(item).strip() for item in values if str(item).strip()}

    scoped = token.get("allowed_capabilities_by_scope", {})
    if scoped is None:
        scoped = {}
    if scoped and not isinstance(scoped, dict):
        raise PlatformError(
            "Access policy token allowed_capabilities_by_scope must be an object.",
            code="E_ACCESS_POLICY_INVALID",
            reason="token_allowed_capabilities_by_scope",
        )

    if not scope:
        known = set(legacy)
        if isinstance(scoped, dict):
            for scope_name, scope_values in scoped.items():
                if not isinstance(scope_values, list):
                    raise PlatformError(
                        "Access policy token scoped allowed_capabilities must be lists.",
                        code="E_ACCESS_POLICY_INVALID",
                        reason=f"token_allowed_capabilities_by_scope.{scope_name}",
                    )
                known.update(str(item).strip() for item in scope_values if str(item).strip())
        return known

    if isinstance(scoped, dict) and scope in scoped:
        scope_values = scoped.get(scope, [])
        if not isinstance(scope_values, list):
            raise PlatformError(
                "Access policy token scoped allowed_capabilities must be lists.",
                code="E_ACCESS_POLICY_INVALID",
                reason=f"token_allowed_capabilities_by_scope.{scope}",
            )
        return {str(item).strip() for item in scope_values if str(item).strip()}
    return legacy


def _policy_text(payload: dict[str, Any] | None, *paths: tuple[str, ...]) -> str:
    if not isinstance(payload, dict):
        return ""
    for path in paths:
        current: Any = payload
        ok = True
        for part in path:
            if not isinstance(current, dict):
                ok = False
                break
            current = current.get(part)
        if ok and isinstance(current, str):
            value = current.strip()
            if value:
                return value
    return ""


def _support_contact(policy: dict[str, Any], team_policy: dict[str, Any] | None) -> str:
    team_value = _policy_text(
        team_policy,
        ("access", "support_contact"),
        ("support", "access_contact"),
        ("support_contact",),
    )
    if team_value:
        return team_value
    policy_value = _policy_text(
        policy,
        ("help", "support_contact"),
        ("help", "access_support_contact"),
    )
    return policy_value or "platform team"


def list_known_capabilities() -> list[str]:
    policy, _ = _load_policy()
    known = _persona_capabilities(policy, "non-admin")
    known.update(_persona_capabilities(policy, "admin"))
    known.update(_token_allowed_capabilities(policy))
    return sorted(known)


def token_default_ttl_minutes(*, scope: str | None = None) -> int:
    policy, _ = _load_policy()
    token = policy.get("token", {})
    if not isinstance(token, dict):
        return 60
    key = "default_team_only_ttl_minutes" if scope == TOKEN_SCOPE_TEAM else "default_ttl_minutes"
    fallback = int(token.get("default_ttl_minutes", 60) or 60)
    value = int(token.get(key, fallback) or fallback)
    return value if value > 0 else 60


def token_max_ttl_minutes(*, scope: str | None = None) -> int:
    policy, _ = _load_policy()
    token = policy.get("token", {})
    if not isinstance(token, dict):
        return 480
    key = "max_team_only_ttl_minutes" if scope == TOKEN_SCOPE_TEAM else "max_ttl_minutes"
    fallback = int(token.get("max_ttl_minutes", 480) or 480)
    value = int(token.get(key, fallback) or fallback)
    return value if value > 0 else 480


def _admin_users_from_payload(payload: dict[str, Any] | None) -> set[str]:
    if not isinstance(payload, dict):
        return set()

    direct = payload.get("admin_users")
    if isinstance(direct, list):
        return {str(item).strip() for item in direct if str(item).strip()}

    direct = payload.get("admins")
    if isinstance(direct, list):
        return {str(item).strip() for item in direct if str(item).strip()}

    access_block = payload.get("access")
    if isinstance(access_block, dict):
        nested = access_block.get("admin_users")
        if isinstance(nested, list):
            return {str(item).strip() for item in nested if str(item).strip()}

    return set()


def _policy_string_list(payload: dict[str, Any], key: str, *, reason: str) -> tuple[bool, set[str]]:
    if key not in payload:
        return False, set()
    values = payload.get(key, [])
    if values in (None, ""):
        return True, set()
    if not isinstance(values, list):
        raise PlatformError(
            f"Team policy field '{reason}' must be a list.",
            code="E_ACCESS_POLICY_INVALID",
            reason=reason,
        )
    return True, {str(item).strip() for item in values if str(item).strip()}


def _team_capability_rules(payload: dict[str, Any] | None, team: str | None) -> tuple[set[str], set[str]]:
    team_name = str(team or "").strip()
    if not isinstance(payload, dict) or not team_name:
        return set(), set()

    teams = payload.get("teams")
    if not isinstance(teams, dict):
        return set(), set()

    team_payload = teams.get(team_name)
    if not isinstance(team_payload, dict):
        return set(), set()

    allow_values = team_payload.get("allow_capabilities", [])
    deny_values = team_payload.get("deny_capabilities", [])
    allow = {str(item).strip() for item in allow_values if str(item).strip()} if isinstance(allow_values, list) else set()
    deny = {str(item).strip() for item in deny_values if str(item).strip()} if isinstance(deny_values, list) else set()
    return allow, deny


def _team_sync_capability_rules(payload: dict[str, Any] | None, team: str | None) -> tuple[bool, bool, set[str], set[str]]:
    team_name = str(team or "").strip()
    if not isinstance(payload, dict) or not team_name:
        return False, False, set(), set()

    teams = payload.get("teams")
    if not isinstance(teams, dict):
        return False, False, set(), set()

    team_payload = teams.get(team_name)
    if not isinstance(team_payload, dict):
        return False, False, set(), set()

    sync_payload = team_payload.get("sync")
    if sync_payload not in (None, ""):
        if not isinstance(sync_payload, dict):
            raise PlatformError(
                f"Team policy field 'teams.{team_name}.sync' must be an object.",
                code="E_ACCESS_POLICY_INVALID",
                reason=f"teams.{team_name}.sync",
            )
        allow_configured, allow = _policy_string_list(
            sync_payload,
            "allow_capabilities",
            reason=f"teams.{team_name}.sync.allow_capabilities",
        )
        deny_configured, deny = _policy_string_list(
            sync_payload,
            "deny_capabilities",
            reason=f"teams.{team_name}.sync.deny_capabilities",
        )
        if allow_configured or deny_configured:
            return allow_configured, deny_configured, allow, deny

    allow_configured, allow = _policy_string_list(
        team_payload,
        "allow_sync_capabilities",
        reason=f"teams.{team_name}.allow_sync_capabilities",
    )
    deny_configured, deny = _policy_string_list(
        team_payload,
        "deny_sync_capabilities",
        reason=f"teams.{team_name}.deny_sync_capabilities",
    )
    return allow_configured, deny_configured, allow, deny


def _admin_users() -> tuple[set[str], str]:
    policy_payload, policy_source = _load_team_policy()
    policy_users = _admin_users_from_payload(policy_payload)
    if policy_users:
        return policy_users, policy_source
    return set(), "missing"


def _prompt_for_actor() -> str:
    if bool(getattr(cli_ctx, "non_interactive", False)):
        return ""
    try:
        from platform_cli.tools.ci_environment import is_jenkins_pipeline
        if is_jenkins_pipeline():
            return ""
    except Exception:
        pass
    value = str(
        typer.prompt(
            "GH username or Guardant ID (stored for GHDP access checks)",
            default="",
            show_default=False,
        )
    ).strip()
    return value


def _resolve_effective_team_name_from_runtime(
    requested_team: str | None,
    *,
    actor: ActorResolution,
    selected_team: str,
    assumed_team: str,
) -> str:
    requested = str(requested_team or "").strip()
    if requested:
        return requested

    if assumed_team:
        return assumed_team

    raw_token, _ = _active_token_raw()
    token_preview = evaluate_token(raw_token, actor=actor.login, team=None, enforce_team_scope=False)
    if token_preview.status in {"active", "expired"} and token_preview.claims and token_preview.claims.team:
        return token_preview.claims.team

    return selected_team


def resolve_actor(*, interactive: bool = False, persist_remembered: bool = True) -> ActorResolution:
    gh_status = "gh_unauthenticated"
    try:
        res = run_cmd(["gh", "api", "user", "-q", ".login"], check=False, env=gh_subprocess_env())
    except PlatformError as exc:
        if exc.code == "E_CMD_NOT_FOUND":
            gh_status = "gh_missing"
        else:
            gh_status = "gh_unavailable"
    else:
        login = (res.stdout or "").strip()
        if res.returncode == 0 and login:
            if persist_remembered:
                try:
                    if get_remembered_actor() != login:
                        set_remembered_actor(login)
                except Exception:
                    pass
            return ActorResolution(login=login, status="resolved", source="gh")

    remembered = get_remembered_actor().strip()
    if remembered:
        return ActorResolution(login=remembered, status="remembered", source="state:remembered_actor")

    if interactive and not bool(getattr(cli_ctx, "non_interactive", False)):
        prompted = _prompt_for_actor()
        if prompted:
            if persist_remembered:
                set_remembered_actor(prompted)
                append_access_event("actor.remembered", {"actor": prompted, "source": "prompt"})
            return ActorResolution(login=prompted, status="prompted", source="prompt")
        return ActorResolution(login="", status="prompt_empty", source="prompt")

    return ActorResolution(login="", status=gh_status, source="gh")


def persona_for_actor(actor: ActorResolution) -> str:
    admin_users, _ = _admin_users()
    if actor.login and actor.login in admin_users:
        return "admin"
    return "non-admin"


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(raw: str) -> bytes:
    value = str(raw or "").strip()
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _token_payload(policy: dict[str, Any]) -> dict[str, Any]:
    payload = policy.get("token", {})
    if not isinstance(payload, dict):
        raise PlatformError(
            "Access policy token settings must be an object.",
            code="E_ACCESS_POLICY_INVALID",
            reason="token",
        )
    return payload


def _token_signing_payload(policy: dict[str, Any]) -> dict[str, Any]:
    token_payload = _token_payload(policy)
    signing = token_payload.get("signing", {})
    if not isinstance(signing, dict):
        raise PlatformError(
            "Access policy token.signing must be an object.",
            code="E_ACCESS_POLICY_INVALID",
            reason="token.signing",
        )
    return signing


def _policy_active_signer(policy: dict[str, Any]) -> tuple[str, str]:
    signing = _token_signing_payload(policy)
    algorithm = str(signing.get("algorithm", "") or "").strip() or TOKEN_ALGORITHM_ED25519
    key_id = str(signing.get("active_key_id", "") or "").strip()
    return algorithm, key_id


def _policy_verification_records(policy: dict[str, Any]) -> list[dict[str, str]]:
    signing = _token_signing_payload(policy)
    values = signing.get("verification_keys", [])
    if values in (None, ""):
        return []
    if not isinstance(values, list):
        raise PlatformError(
            "Access policy token.signing.verification_keys must be a list.",
            code="E_ACCESS_POLICY_INVALID",
            reason="token.signing.verification_keys",
        )
    records: list[dict[str, str]] = []
    for item in values:
        if not isinstance(item, dict):
            raise PlatformError(
                "Access policy token.signing.verification_keys entries must be objects.",
                code="E_ACCESS_POLICY_INVALID",
                reason="token.signing.verification_keys",
            )
        key_id = str(item.get("key_id", "") or "").strip()
        algorithm = str(item.get("algorithm", "") or "").strip() or TOKEN_ALGORITHM_ED25519
        public_key_pem = str(item.get("public_key_pem", "") or "").strip()
        status = str(item.get("status", "") or "").strip() or "active"
        if key_id and public_key_pem:
            records.append(
                {
                    "key_id": key_id,
                    "algorithm": algorithm,
                    "public_key_pem": public_key_pem,
                    "status": status,
                }
            )
    return records


def _local_signer_record() -> dict[str, str]:
    metadata = read_signer_metadata()
    if not metadata:
        return {}
    key_id = str(metadata.get("key_id", "") or "").strip()
    algorithm = str(metadata.get("algorithm", "") or "").strip() or TOKEN_ALGORITHM_ED25519
    public_key_pem = str(metadata.get("public_key_pem", "") or "").strip()
    if key_id and public_key_pem:
        return {
            "key_id": key_id,
            "algorithm": algorithm,
            "public_key_pem": public_key_pem,
            "status": "local",
        }
    return {}


def _verification_records(policy: dict[str, Any]) -> list[dict[str, str]]:
    records = _policy_verification_records(policy)
    local = _local_signer_record()
    if local and not any(item["key_id"] == local["key_id"] for item in records):
        records.append(local)
    return records


def _verification_public_key(policy: dict[str, Any], *, key_id: str, algorithm: str) -> Ed25519PublicKey:
    normalized_key_id = str(key_id or "").strip()
    normalized_algorithm = str(algorithm or "").strip() or TOKEN_ALGORITHM_ED25519
    if normalized_algorithm != TOKEN_ALGORITHM_ED25519:
        raise PlatformError(
            f"Unsupported token signing algorithm '{normalized_algorithm}'.",
            code="E_ACCESS_POLICY_INVALID",
            reason="token.signing.algorithm",
        )

    for record in _verification_records(policy):
        if record.get("key_id") != normalized_key_id:
            continue
        if record.get("algorithm", TOKEN_ALGORITHM_ED25519) != normalized_algorithm:
            continue
        try:
            return serialization.load_pem_public_key(record["public_key_pem"].encode("utf-8"))  # type: ignore[return-value]
        except Exception as exc:
            raise PlatformError(
                f"Failed to load verification key '{normalized_key_id}': {exc}",
                code="E_ACCESS_POLICY_INVALID",
                reason="token.signing.verification_keys",
            )

    raise PlatformError(
        f"No verification key is configured for signer '{normalized_key_id}'.",
        code="E_ADMIN_SIGNER_VERIFIER_MISSING",
        reason=normalized_key_id,
    )


def _load_local_signing_key(*, required: bool) -> tuple[str, str, Ed25519PrivateKey, str]:
    metadata = read_signer_metadata()
    private_key_pem = read_private_key_pem()
    if not metadata or not private_key_pem:
        if required:
            paths = default_admin_signer_paths()
            raise PlatformError(
                "Admin signer material is missing. Run 'ghdp admin signer setup' before minting tokens.",
                code="E_ADMIN_SIGNER_MISSING",
                reason=str(paths.signer_dir),
            )
        return "", "", None, ""  # type: ignore[return-value]

    key_id = str(metadata.get("key_id", "") or "").strip()
    algorithm = str(metadata.get("algorithm", "") or "").strip() or TOKEN_ALGORITHM_ED25519
    public_key_pem = str(metadata.get("public_key_pem", "") or "").strip()
    if not key_id or algorithm != TOKEN_ALGORITHM_ED25519:
        raise PlatformError(
            "Admin signer metadata is invalid.",
            code="E_ADMIN_SIGNER_INVALID",
            reason="admin_signer_metadata",
        )
    try:
        private_key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    except Exception as exc:
        raise PlatformError(
            f"Failed to load admin signer private key: {exc}",
            code="E_ADMIN_SIGNER_INVALID",
            reason="admin_signer_private_key",
        )
    if not isinstance(private_key, Ed25519PrivateKey):
        raise PlatformError(
            "Admin signer private key must be an Ed25519 key.",
            code="E_ADMIN_SIGNER_INVALID",
            reason="admin_signer_private_key",
        )
    return key_id, algorithm, private_key, public_key_pem


def _serialize_public_key(public_key: Ed25519PublicKey) -> str:
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


def _serialize_private_key(private_key: Ed25519PrivateKey) -> str:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def _update_local_access_policy_for_signer(*, key_id: str, algorithm: str, public_key_pem: str) -> str:
    policy, _ = _load_policy()
    payload = json.loads(json.dumps(policy))
    token_payload = payload.setdefault("token", {})
    signing = token_payload.setdefault("signing", {})
    if not isinstance(signing, dict):
        signing = {}
        token_payload["signing"] = signing
    values = signing.get("verification_keys", [])
    verification_keys = [item for item in values if isinstance(item, dict) and str(item.get("key_id", "") or "").strip() != key_id]
    verification_keys.append(
        {
            "key_id": key_id,
            "algorithm": algorithm,
            "public_key_pem": public_key_pem,
            "status": "active",
        }
    )
    signing["format"] = "ghdp.sig.v2"
    signing["algorithm"] = algorithm
    signing["active_key_id"] = key_id
    signing["verification_keys"] = verification_keys
    token_payload.pop("secret_source", None)

    path = preferred_user_access_policy_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return str(path)


def setup_local_signer(
    *,
    key_id: str,
    overwrite: bool = False,
    update_local_policy: bool = True,
) -> dict[str, str]:
    normalized_key_id = str(key_id or "").strip()
    if not normalized_key_id:
        raise PlatformError(
            "Signer key id is required.",
            code="E_ADMIN_SIGNER_INVALID",
            reason="key_id",
        )
    if has_local_signer() and not overwrite:
        paths = default_admin_signer_paths()
        raise PlatformError(
            "Admin signer material already exists. Re-run with --overwrite to replace it.",
            code="E_ADMIN_SIGNER_EXISTS",
            reason=str(paths.signer_dir),
        )

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    public_key_pem = _serialize_public_key(public_key)
    private_key_pem = _serialize_private_key(private_key)
    metadata = {
        "key_id": normalized_key_id,
        "algorithm": TOKEN_ALGORITHM_ED25519,
        "public_key_pem": public_key_pem,
        "created_at": int(time.time()),
    }
    write_private_key_pem(private_key_pem)
    write_signer_metadata(metadata)
    policy_path = ""
    if update_local_policy:
        policy_path = _update_local_access_policy_for_signer(
            key_id=normalized_key_id,
            algorithm=TOKEN_ALGORITHM_ED25519,
            public_key_pem=public_key_pem,
        )
    return {
        "key_id": normalized_key_id,
        "algorithm": TOKEN_ALGORITHM_ED25519,
        "public_key_pem": public_key_pem,
        "policy_path": policy_path,
        "private_key_path": str(default_admin_signer_paths().private_key_path),
        "metadata_path": str(default_admin_signer_paths().metadata_path),
    }


def signer_status() -> dict[str, str]:
    policy, _ = _load_policy()
    paths = default_admin_signer_paths()
    metadata = read_signer_metadata()
    key_id = str(metadata.get("key_id", "") or "").strip()
    algorithm = str(metadata.get("algorithm", "") or "").strip() or TOKEN_ALGORITHM_ED25519
    policy_algorithm, active_key_id = _policy_active_signer(policy)
    in_policy = bool(key_id) and any(item.get("key_id") == key_id for item in _policy_verification_records(policy))
    return {
        "present": "yes" if has_local_signer() else "no",
        "key_id": key_id,
        "algorithm": algorithm,
        "private_key_path": str(paths.private_key_path),
        "metadata_path": str(paths.metadata_path),
        "policy_active_key_id": active_key_id,
        "policy_algorithm": policy_algorithm,
        "policy_has_local_key": "yes" if in_policy else "no",
    }


def list_token_capability_catalog(*, scope: str | None = None) -> list[dict[str, str]]:
    policy, _ = _load_policy()
    token_payload = _token_payload(policy)
    catalog_raw = token_payload.get("capability_catalog", {})
    catalog = catalog_raw if isinstance(catalog_raw, dict) else {}
    allowed = _token_allowed_capabilities(policy, scope=scope)
    items: list[dict[str, str]] = []
    for capability in sorted(allowed):
        meta = catalog.get(capability, {})
        meta_dict = meta if isinstance(meta, dict) else {}
        items.append(
            {
                "capability": capability,
                "key": str(meta_dict.get("key", capability.replace(".", "_")) or capability.replace(".", "_")).strip(),
                "label": str(meta_dict.get("label", capability) or capability).strip(),
                "description": str(meta_dict.get("description", "") or "").strip(),
                "group": str(meta_dict.get("group", "Other") or "Other").strip(),
                "order": str(meta_dict.get("order", 9999) or 9999),
            }
        )
    return sorted(items, key=lambda item: (int(item["order"]), item["group"].lower(), item["label"].lower()))


def _active_token_raw() -> tuple[str, str]:
    state_token = get_state_active_token().strip()
    if state_token:
        return state_token, "state:access_session"
    return "", "missing"


def issue_token(
    *,
    target_actor: str | None,
    capabilities: list[str],
    ttl_minutes: int,
    team: str | None = None,
) -> str:
    policy, _ = _load_policy()
    requested = [str(item).strip() for item in capabilities if str(item).strip()]
    if not requested:
        raise PlatformError(
            "At least one capability is required to issue an admin token.",
            code="E_ADMIN_TOKEN_CAPABILITIES_REQUIRED",
            reason="admin_token",
        )

    actor = str(target_actor or "").strip()
    normalized_team = str(team or "").strip()
    scope = classify_token_scope(actor, normalized_team)
    if not scope:
        raise PlatformError(
            "At least one token scope is required. Provide --for-user, --team, or both.",
            code="E_ADMIN_TOKEN_SCOPE_REQUIRED",
            reason="admin_token",
        )

    allowed = _token_allowed_capabilities(policy, scope=scope)
    invalid = sorted({item for item in requested if item not in allowed})
    if invalid:
        raise PlatformError(
            f"Unsupported token capabilities for scope '{scope}': {', '.join(invalid)}",
            code="E_ADMIN_TOKEN_CAPABILITY_INVALID",
            reason="admin_token",
        )

    ttl = int(ttl_minutes or token_default_ttl_minutes(scope=scope))
    max_ttl = token_max_ttl_minutes(scope=scope)
    if ttl <= 0 or ttl > max_ttl:
        raise PlatformError(
            f"Token ttl must be between 1 and {max_ttl} minutes.",
            code="E_ADMIN_TOKEN_TTL_INVALID",
            reason="admin_token",
        )

    key_id, algorithm, private_key, _ = _load_local_signing_key(required=True)
    now = int(time.time())
    payload = {
        "v": TOKEN_VERSION,
        "alg": algorithm,
        "kid": key_id,
        "scope": scope,
        "actor": actor,
        "capabilities": sorted(set(requested)),
        "team": normalized_team,
        "issued_at": now,
        "expires_at": now + (ttl * 60),
    }
    payload_raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = private_key.sign(payload_raw)
    return f"{_b64url_encode(payload_raw)}.{_b64url_encode(signature)}"


def evaluate_token(
    raw_token: str | None,
    *,
    actor: str,
    team: str | None = None,
    enforce_team_scope: bool = True,
) -> TokenEvaluation:
    token_raw = str(raw_token or "").strip()
    if not token_raw:
        return TokenEvaluation(status="missing", claims=None)

    policy, _ = _load_policy()

    try:
        payload_part, signature_part = token_raw.split(".", 1)
        payload_raw = _b64url_decode(payload_part)
        signature_raw = _b64url_decode(signature_part)
    except Exception:
        return TokenEvaluation(status="malformed", claims=None, message="Admin token format is invalid.")

    try:
        payload = json.loads(payload_raw.decode("utf-8"))
    except Exception:
        return TokenEvaluation(status="malformed", claims=None, message="Admin token payload is invalid JSON.")

    if not isinstance(payload, dict):
        return TokenEvaluation(status="malformed", claims=None, message="Admin token payload is invalid.")

    version = int(payload.get("v", 1) or 1)
    algorithm = str(payload.get("alg", "") or "").strip()
    key_id = str(payload.get("kid", "") or "").strip()
    if version < TOKEN_VERSION or not algorithm or not key_id:
        return TokenEvaluation(
            status="legacy_reissue_required",
            claims=None,
            message="Legacy admin token format is no longer accepted. Ask platform team for a fresh token.",
        )

    token_actor = str(payload.get("actor", "") or "").strip()
    capabilities = payload.get("capabilities", [])
    if not isinstance(capabilities, list):
        return TokenEvaluation(status="malformed", claims=None, message="Admin token capabilities are invalid.")

    token_team = str(payload.get("team", "") or "").strip()
    scope, scope_error = _token_scope_from_payload(payload)
    if not scope:
        return TokenEvaluation(status="malformed", claims=None, message=scope_error or "Admin token scope is invalid.")
    expires_at = int(payload.get("expires_at", 0) or 0)
    claims = TokenClaims(
        actor=token_actor,
        capabilities=tuple(sorted({str(item).strip() for item in capabilities if str(item).strip()})),
        team=token_team,
        scope=scope,
        issued_at=int(payload.get("issued_at", 0) or 0),
        expires_at=expires_at,
        raw=token_raw,
    )

    try:
        public_key = _verification_public_key(policy, key_id=key_id, algorithm=algorithm)
    except PlatformError as exc:
        return TokenEvaluation(
            status="unknown_signer",
            claims=claims,
            message=exc.message if hasattr(exc, "message") else str(exc),
        )

    try:
        public_key.verify(signature_raw, payload_raw)
    except InvalidSignature:
        return TokenEvaluation(status="invalid_signature", claims=None, message="Admin token signature is invalid.")
    except Exception:
        return TokenEvaluation(status="invalid_signature", claims=None, message="Admin token signature is invalid.")

    now = int(time.time())
    if expires_at <= now:
        return TokenEvaluation(
            status="expired",
            claims=claims,
            message=f"Admin token expired at {expires_at}.",
        )

    if scope in {TOKEN_SCOPE_USER, TOKEN_SCOPE_USER_TEAM}:
        if not actor:
            return TokenEvaluation(
                status="identity_required",
                claims=claims,
                message="GitHub identity could not be confirmed for admin token evaluation.",
            )
        if token_actor != actor:
            return TokenEvaluation(
                status="actor_mismatch",
                claims=claims,
                message=f"Admin token is for GitHub user '{token_actor}', not '{actor}'.",
            )

    if enforce_team_scope and scope in {TOKEN_SCOPE_TEAM, TOKEN_SCOPE_USER_TEAM} and token_team and team and token_team != team:
        return TokenEvaluation(
            status="team_mismatch",
            claims=claims,
            message=f"Admin token is restricted to team '{token_team}'.",
        )

    return TokenEvaluation(status="active", claims=claims)


def _value_matches(value: Any, candidate: Any) -> bool:
    if isinstance(value, bool) or isinstance(candidate, bool):
        return value is candidate
    return value == candidate


def resolve_effective_team_name(
    team: str | None = None,
    *,
    interactive: bool = False,
    persist_remembered: bool = True,
) -> str:
    actor = resolve_actor(interactive=interactive, persist_remembered=persist_remembered)
    base_persona = persona_for_actor(actor)
    selected_team = _current_selected_team()
    assumed_team = get_assumed_team().strip() if base_persona == "admin" else ""
    return _resolve_effective_team_name_from_runtime(
        team,
        actor=actor,
        selected_team=selected_team,
        assumed_team=assumed_team,
    )


def _resolve_runtime(
    *,
    team: str | None = None,
    interactive: bool = True,
    persist_remembered: bool = True,
) -> _RuntimeAccess:
    policy, policy_source = _load_policy()
    release_info = release_runtime()
    team_policy, _ = _load_team_sync_policy()
    admin_users, admin_users_source = _admin_users()
    actor = resolve_actor(interactive=interactive, persist_remembered=persist_remembered)
    base_persona = "admin" if actor.login and actor.login in admin_users else "non-admin"
    selected_team = _current_selected_team()
    assumed_team = get_assumed_team().strip() if base_persona == "admin" else ""
    effective_team = _resolve_effective_team_name_from_runtime(
        team,
        actor=actor,
        selected_team=selected_team,
        assumed_team=assumed_team,
    )

    raw_token, token_source = _active_token_raw()
    token_eval = evaluate_token(raw_token, actor=actor.login, team=effective_team or None)

    persona = base_persona
    active_mode = "admin" if base_persona == "admin" else "non-admin"
    if base_persona == "admin" and assumed_team:
        persona = "non-admin"
        active_mode = "assumed-team"
    elif token_eval.status == "active" and token_eval.claims and token_eval.claims.team:
        active_mode = "token-team"

    capabilities = _persona_capabilities(policy, persona)
    if persona != "admin":
        allow_capabilities, deny_capabilities = _team_capability_rules(team_policy, effective_team)
        capabilities.update(allow_capabilities)
        capabilities.difference_update(deny_capabilities)

    if token_eval.status == "active" and token_eval.claims:
        capabilities.update(token_eval.claims.capabilities)

    if base_persona == "admin" and assumed_team:
        capabilities.add(RETURN_FROM_ASSUMED_TEAM)

    support_contact = _support_contact(policy, team_policy)
    context = AccessContext(
        actor=actor.login,
        identity_status=actor.status,
        actor_source=actor.source,
        base_persona=base_persona,
        persona=persona,
        active_mode=active_mode,
        admin_users_source=admin_users_source,
        selected_team=selected_team,
        effective_team=effective_team,
        assumed_team=assumed_team,
        team_locked=bool(effective_team) and persona != "admin",
        token_status=token_eval.status,
        token_source=token_source,
        token_scope=token_eval.claims.scope if token_eval.claims else "",
        token_team=token_eval.claims.team if token_eval.claims else "",
        token_expires_at=token_eval.claims.expires_at if token_eval.claims else 0,
        capabilities=tuple(sorted(capabilities)),
        policy_source=policy_source,
        release_channel=release_info.channel,
        release_policy_source=release_info.policy_source,
        support_contact=support_contact,
    )
    return _RuntimeAccess(
        context=context,
        actor=actor,
        token_eval=token_eval,
        capabilities=tuple(sorted(capabilities)),
    )


def enforce_config_write(key: str, value: Any) -> None:
    policy, _ = _load_policy()
    config_rules = policy.get("config_rules", {})
    if not isinstance(config_rules, dict):
        return

    rule = config_rules.get(key)
    if not isinstance(rule, dict):
        return

    admin_only_values = rule.get("admin_only_values", [])
    user_safe_values = rule.get("user_safe_values", [])

    if isinstance(admin_only_values, list) and any(_value_matches(value, candidate) for candidate in admin_only_values):
        ensure_capability("config.admin_write", command_name=f"config:{key}")
        return

    if isinstance(user_safe_values, list) and any(_value_matches(value, candidate) for candidate in user_safe_values):
        ensure_capability("config.user_safe_write", command_name=f"config:{key}")


def effective_capabilities(
    *,
    team: str | None = None,
    interactive: bool = True,
    persist_remembered: bool = True,
) -> tuple[set[str], ActorResolution, TokenEvaluation, str]:
    runtime = _resolve_runtime(team=team, interactive=interactive, persist_remembered=persist_remembered)
    return set(runtime.capabilities), runtime.actor, runtime.token_eval, runtime.context.policy_source


def resolve_access_context(
    *,
    team: str | None = None,
    interactive: bool = True,
    persist_remembered: bool = True,
) -> AccessContext:
    return _resolve_runtime(team=team, interactive=interactive, persist_remembered=persist_remembered).context


def evaluate_sync_capability_access(
    capabilities: Iterable[str],
    *,
    team: str | None = None,
    interactive: bool = False,
    persist_remembered: bool = True,
) -> tuple[set[str], dict[str, str], AccessContext]:
    runtime = _resolve_runtime(team=team, interactive=interactive, persist_remembered=persist_remembered)
    context = runtime.context
    normalized = [str(item).strip() for item in capabilities if str(item).strip()]
    if not normalized:
        return set(), {}, context

    # Full admin mode stays unrestricted; team-scoped contexts honor team sync policy.
    if context.active_mode == "admin" or not context.effective_team:
        return set(normalized), {}, context

    team_policy, _ = _load_team_sync_policy()
    allow_configured, _deny_configured, allow, deny = _team_sync_capability_rules(team_policy, context.effective_team)
    allowed: set[str] = set()
    blocked: dict[str, str] = {}
    for capability_name in normalized:
        if capability_name in deny:
            blocked[capability_name] = (
                f"blocked by team sync policy for '{context.effective_team}' via deny_capabilities"
            )
            continue
        if allow_configured and capability_name not in allow:
            blocked[capability_name] = (
                f"blocked by team sync policy for '{context.effective_team}'; capability is not in allow_capabilities"
            )
            continue
        allowed.add(capability_name)
    return allowed, blocked, context


def resolve_sync_capability_policy(
    *,
    team: str | None = None,
    interactive: bool = True,
    persist_remembered: bool = True,
) -> SyncCapabilityPolicy:
    runtime = _resolve_runtime(team=team, interactive=interactive, persist_remembered=persist_remembered)
    if runtime.context.active_mode == "admin" or not runtime.context.effective_team:
        return SyncCapabilityPolicy(
            context=runtime.context,
            restricted=False,
            allow_configured=False,
            allowed_capabilities=(),
            denied_capabilities=(),
        )

    team_policy, _ = _load_team_sync_policy()
    allow_configured, deny_configured, allow, deny = _team_sync_capability_rules(
        team_policy,
        runtime.context.effective_team,
    )
    return SyncCapabilityPolicy(
        context=runtime.context,
        restricted=allow_configured or deny_configured,
        allow_configured=allow_configured,
        allowed_capabilities=tuple(sorted(allow)),
        denied_capabilities=tuple(sorted(deny)),
    )


def ensure_admin_principal(*, command_name: str) -> ActorResolution:
    actor = resolve_actor(interactive=True)
    if not actor.login:
        raise PlatformError(
            "GitHub identity could not be confirmed. Run 'gh auth login' or provide your GitHub login when prompted.",
            code="E_ACTOR_IDENTITY_REQUIRED",
            reason=actor.status,
        )
    if persona_for_actor(actor) != "admin":
        raise PlatformError(
            f"Access denied. '{command_name}' requires an admin identity.",
            code="E_ACCESS_DENIED",
            reason="admin.identity",
        )
    return actor


def _access_guidance(capability: str, ctx: AccessContext) -> str:
    policy, _ = _load_policy()
    hints: list[str] = []

    if ctx.base_persona == "admin" and ctx.active_mode == "assumed-team":
        hints.append("Run 'ghdp admin return' to restore full admin mode.")
    elif capability in _token_allowed_capabilities(policy):
        hints.append(
            f"Ask {ctx.support_contact} for a temporary access token, then run 'ghdp access token'."
        )
    else:
        hints.append(f"Contact {ctx.support_contact} if you need this capability.")

    return " ".join(hints).strip()


def evaluate_capability_requirement(
    capability: str,
    *,
    team: str | None = None,
    command_name: str | None = None,
    interactive: bool = True,
) -> CapabilityDecision:
    eval_team = resolve_effective_team_name(team)
    label = command_name or capability
    runtime = _resolve_runtime(team=eval_team, interactive=False)
    if capability in runtime.capabilities:
        return CapabilityDecision(
            capability=capability,
            command_name=label,
            status="allowed",
            message="",
            code="",
            reason=capability,
            context=runtime.context,
        )

    if interactive and not runtime.actor.login:
        prompted_runtime = _resolve_runtime(team=eval_team, interactive=True)
        if capability in prompted_runtime.capabilities:
            return CapabilityDecision(
                capability=capability,
                command_name=label,
                status="allowed",
                message="",
                code="",
                reason=capability,
                context=prompted_runtime.context,
            )
        runtime = prompted_runtime

    actor = runtime.actor
    if not actor.login:
        return CapabilityDecision(
            capability=capability,
            command_name=label,
            status="identity_required",
            message=(
                "GitHub identity could not be confirmed. Run 'gh auth login' or provide your GitHub login when prompted."
            ),
            code="E_ACTOR_IDENTITY_REQUIRED",
            reason=actor.status,
            context=runtime.context,
        )

    detail = runtime.token_eval.message if runtime.token_eval.status not in {"missing", "active"} else ""
    guidance = _access_guidance(capability, runtime.context)
    message = f"Access denied. '{label}' requires capability '{capability}'."
    if detail:
        message += f" {detail}"
    if guidance:
        message += f" {guidance}"
    return CapabilityDecision(
        capability=capability,
        command_name=label,
        status="denied",
        message=message,
        code="E_ACCESS_DENIED",
        reason=capability,
        context=runtime.context,
    )


def ensure_capability(capability: str, *, team: str | None = None, command_name: str | None = None) -> None:
    decision = evaluate_capability_requirement(
        capability,
        team=team,
        command_name=command_name,
        interactive=True,
    )
    if decision.status == "allowed":
        return
    raise PlatformError(
        decision.message,
        code=decision.code,
        reason=decision.reason,
    )


def ensure_team_selection_allowed(*, current_team: str, target_team: str) -> None:
    current = str(current_team or "").strip()
    target = str(target_team or "").strip()
    if not current or not target or current == target:
        return
    ensure_capability("team.switch", team=target, command_name="team use")
