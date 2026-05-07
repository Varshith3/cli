from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from platform_cli.core.errors import PlatformError
from platform_cli.exec.runner import run_cmd

MANAGED_INSTALL_MARKER_NAME = "managed-install"
INSTALL_STATE_NAME = "install-state.json"
INSTALL_MODE_MANAGED = "managed"
INSTALL_MODE_STANDARD = "standard"
MANAGED_AUTH_DIR_NAME = "managed-auth"
MANAGED_AUTH_TOKEN_NAME = "github-token"
MANAGED_AUTH_POLICY_NAME = "policy.json"
MANAGED_AUTH_BUNDLE_ENV = "GHDP_MANAGED_AUTH_BUNDLE_PATH"
AUTH_MODE_MANAGED_LOCKED = "managed_locked"
AUTH_MODE_PERSONAL_ALLOWED = "personal_allowed"
AUTH_MODE_VALUES = {AUTH_MODE_MANAGED_LOCKED, AUTH_MODE_PERSONAL_ALLOWED}


@dataclass(frozen=True)
class GithubAuthState:
    install_flavor: str
    managed_auth_status: str
    managed_token_present: bool
    auth_mode: str
    effective_github_auth_source: str


@dataclass(frozen=True)
class GithubAuthModeState:
    mode: str
    source: str
    managed: bool
    policy_valid: bool
    changed_at: str
    changed_by: str
    reason: str


def managed_install_marker_path() -> Path:
    return Path.home() / ".ghdp" / MANAGED_INSTALL_MARKER_NAME


def managed_install_marker_exists() -> bool:
    return managed_install_marker_path().exists()


def install_state_path() -> Path:
    return Path.home() / ".ghdp" / INSTALL_STATE_NAME


def _read_install_state_payload() -> dict[str, object] | None:
    path = install_state_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def install_state_mode() -> str:
    payload = _read_install_state_payload()
    if payload is None:
        return ""
    mode = str(payload.get("install_mode", "") or "").strip().lower()
    return mode if mode in {INSTALL_MODE_MANAGED, INSTALL_MODE_STANDARD} else ""


def is_managed_install() -> bool:
    # Runtime trust should come from persisted install state, not mutable env vars.
    mode = install_state_mode()
    if mode == INSTALL_MODE_MANAGED:
        return True
    if mode == INSTALL_MODE_STANDARD:
        return False
    # Backward compatibility for legacy installs.
    return managed_install_marker_exists()


def managed_auth_dir_path() -> Path:
    return Path.home() / ".ghdp" / MANAGED_AUTH_DIR_NAME


def managed_auth_token_path() -> Path:
    return managed_auth_dir_path() / MANAGED_AUTH_TOKEN_NAME


def managed_auth_policy_path() -> Path:
    return managed_auth_dir_path() / MANAGED_AUTH_POLICY_NAME


def managed_install_token_path() -> Path:
    # Legacy compatibility path helper.
    return managed_auth_token_path()


def direct_github_token() -> str:
    return (
        (os.getenv("GHDP_TOKEN") or "").strip()
        or (os.getenv("GH_TOKEN") or "").strip()
        or (os.getenv("GITHUB_TOKEN") or "").strip()
    )


def managed_auth_bundle_path() -> Path | None:
    raw = (os.getenv(MANAGED_AUTH_BUNDLE_ENV) or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def _extract_token_from_text(raw_text: str) -> str:
    raw = (raw_text or "").replace("\ufeff", "").strip()
    if not raw:
        return ""

    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
        except Exception:
            return ""
        if isinstance(payload, dict):
            for key in ("token", "github_token", "github_pat", "pat", "GHDP_TOKEN", "GITHUB_TOKEN"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    for line in raw.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        if entry.startswith("export "):
            entry = entry[len("export ") :].strip()
        if "=" not in entry:
            return entry.strip()
        key, value = entry.split("=", 1)
        normalized_key = key.strip()
        if normalized_key in {"token", "github_token", "github_pat", "pat", "GHDP_TOKEN", "GITHUB_TOKEN"}:
            return value.strip().strip('"').strip("'")

    return raw


def read_managed_auth_bundle(bundle_path: Path | None = None) -> str:
    path = bundle_path or managed_auth_bundle_path()
    if path is None or not path.exists():
        return ""
    try:
        return _extract_token_from_text(path.read_text(encoding="utf-8"))
    except Exception:
        return ""


def read_managed_github_token() -> str:
    # Legacy compatibility helper; managed runtime no longer relies on this file.
    token_path = managed_auth_token_path()
    if not token_path.exists():
        return ""
    try:
        return _extract_token_from_text(token_path.read_text(encoding="utf-8"))
    except Exception:
        return ""


def managed_local_github_token() -> str:
    # Legacy compatibility helper; no longer used for managed auth source-of-truth.
    return read_managed_auth_bundle() or read_managed_github_token()


def _build_meta_attr(name: str) -> str:
    try:
        from platform_cli import _build_meta  # type: ignore

        value = getattr(_build_meta, name, "")
    except Exception:
        return ""
    return str(value or "").strip()


def build_install_flavor() -> str:
    return _build_meta_attr("BUILD_INSTALL_FLAVOR").lower()


def managed_embedded_github_token() -> str:
    return _build_meta_attr("BUILD_MANAGED_GITHUB_TOKEN")


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _resolve_auth_mode_from_payload(payload: object) -> GithubAuthModeState | None:
    if not isinstance(payload, dict):
        return None
    mode = str(payload.get("mode", "") or "").strip()
    if mode not in AUTH_MODE_VALUES:
        return None
    return GithubAuthModeState(
        mode=mode,
        source="managed-policy",
        managed=True,
        policy_valid=True,
        changed_at=str(payload.get("changed_at", "") or "").strip(),
        changed_by=str(payload.get("changed_by", "") or "").strip(),
        reason=str(payload.get("reason", "") or "").strip(),
    )


def _read_managed_auth_policy_payload() -> dict[str, object] | None:
    path = managed_auth_policy_path()
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def resolve_github_auth_mode(*, managed_install: bool | None = None) -> GithubAuthModeState:
    managed = managed_install if managed_install is not None else is_managed_install()
    if not managed:
        return GithubAuthModeState(
            mode=AUTH_MODE_PERSONAL_ALLOWED,
            source="standard-install-default",
            managed=False,
            policy_valid=True,
            changed_at="",
            changed_by="",
            reason="",
        )

    payload = _read_managed_auth_policy_payload()
    resolved = _resolve_auth_mode_from_payload(payload)
    if resolved is not None:
        return resolved

    return GithubAuthModeState(
        mode=AUTH_MODE_MANAGED_LOCKED,
        source="managed-failsafe",
        managed=True,
        policy_valid=False,
        changed_at="",
        changed_by="",
        reason="missing_or_invalid_policy",
    )


def set_github_auth_mode(
    mode: str,
    *,
    actor: str,
    reason: str,
    policy_path: Path | None = None,
) -> GithubAuthModeState:
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in AUTH_MODE_VALUES:
        raise PlatformError(
            f"Unsupported auth mode '{mode}'. Allowed: {AUTH_MODE_MANAGED_LOCKED}, {AUTH_MODE_PERSONAL_ALLOWED}.",
            code="E_GH_AUTH_MODE_INVALID",
            reason="auth_mode",
        )
    path = policy_path or managed_auth_policy_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "mode": normalized_mode,
        "changed_at": _now_utc(),
        "changed_by": str(actor or "").strip(),
        "reason": str(reason or "").strip(),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except Exception:
        pass
    return resolve_github_auth_mode(managed_install=True)


def _managed_runtime_token_allowed(*, managed: bool, mode: GithubAuthModeState) -> bool:
    if not managed:
        return False
    if build_install_flavor() != INSTALL_MODE_MANAGED:
        return False
    return mode.mode == AUTH_MODE_MANAGED_LOCKED


def managed_install_token() -> str:
    managed = is_managed_install()
    mode = resolve_github_auth_mode(managed_install=managed)
    personal = direct_github_token()

    if not managed:
        return personal

    if _managed_runtime_token_allowed(managed=managed, mode=mode):
        return managed_embedded_github_token()

    if mode.mode == AUTH_MODE_PERSONAL_ALLOWED:
        return personal

    return ""


def write_managed_github_token(token: str, *, token_path: Path | None = None) -> Path:
    # Legacy helper retained for compatibility; managed auth now comes from embedded binary token.
    value = (token or "").strip()
    if not value:
        raise PlatformError(
            "Managed GitHub token is required.",
            code="E_MANAGED_GITHUB_TOKEN_REQUIRED",
            reason="github_auth",
        )
    path = token_path or managed_auth_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except Exception:
        pass
    return path


def persist_managed_github_token_from_source(
    *,
    token: str | None = None,
    bundle_path: Path | None = None,
) -> Path | None:
    # Managed token persistence from sidecar is deprecated.
    _ = token
    _ = bundle_path
    return None


def gh_subprocess_env(base_env: Mapping[str, str] | None = None) -> dict[str, str]:
    env: dict[str, str] = dict(base_env) if base_env is not None else dict(os.environ)
    token = managed_install_token()
    if token:
        env["GHDP_TOKEN"] = token
        env["GH_TOKEN"] = token
        env["GITHUB_TOKEN"] = token
    else:
        env.pop("GHDP_TOKEN", None)
        env.pop("GH_TOKEN", None)
        env.pop("GITHUB_TOKEN", None)
    return env


def gh_auth_ready() -> bool:
    mode = resolve_github_auth_mode()
    token = managed_install_token()
    if token:
        return True
    if mode.managed and mode.mode == AUTH_MODE_MANAGED_LOCKED:
        return False

    try:
        res = run_cmd(["gh", "auth", "status"], check=False, env=gh_subprocess_env())
    except Exception:
        return False
    return res.returncode == 0


def inspect_github_auth() -> GithubAuthState:
    mode = resolve_github_auth_mode()
    managed = is_managed_install()
    managed_token = bool(managed_embedded_github_token()) and build_install_flavor() == INSTALL_MODE_MANAGED
    token = managed_install_token()

    if token and managed and mode.mode == AUTH_MODE_MANAGED_LOCKED:
        source = "managed_embedded"
    elif token:
        source = "personal_env_or_cli"
    elif managed and mode.mode == AUTH_MODE_MANAGED_LOCKED:
        source = "managed_state_missing"
    else:
        source = "none"

    if managed:
        return GithubAuthState(
            install_flavor="managed",
            managed_auth_status="configured" if managed_token else "missing",
            managed_token_present=managed_token,
            auth_mode=mode.mode,
            effective_github_auth_source=source,
        )
    return GithubAuthState(
        install_flavor="standard tech",
        managed_auth_status="not applicable",
        managed_token_present=bool(token),
        auth_mode=mode.mode,
        effective_github_auth_source=source,
    )


def load_managed_github_auth_into_env() -> int:
    # No-op by design: managed token should only be injected per subprocess call.
    return 0


def github_cli_env(*, managed_install: bool | None = None) -> dict[str, str]:
    use_managed = managed_install if managed_install is not None else is_managed_install()
    if use_managed:
        token = managed_install_token()
    else:
        token = direct_github_token()

    if not token:
        return {}

    return {
        "GHDP_TOKEN": token,
        "GH_TOKEN": token,
        "GITHUB_TOKEN": token,
    }


def _is_truthy(value: str | None) -> bool:
    raw = (value or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}

