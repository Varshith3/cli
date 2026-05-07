# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
from __future__ import annotations

import json
import os
from typing import Any, Optional, Sequence

from platform_cli.core.errors import PlatformError
from platform_cli.exec.runner import run_cmd


def fetch_aws_secret_string(
    secret_id: str,
    *,
    region: Optional[str] = None,
    profile: Optional[str] = None,
) -> str:
    secret_ref = (secret_id or "").strip()
    if not secret_ref:
        raise PlatformError(
            "AWS secret id is required.",
            code="E_SECRET_ID_REQUIRED",
            reason="secret_resolver",
        )

    cmd = ["aws"]
    if profile:
        cmd.extend(["--profile", profile])
    if region:
        cmd.extend(["--region", region])
    cmd.extend(
        [
            "secretsmanager",
            "get-secret-value",
            "--secret-id",
            secret_ref,
            "--query",
            "SecretString",
            "--output",
            "text",
        ]
    )

    try:
        res = run_cmd(cmd, check=True)
    except PlatformError as e:
        raise PlatformError(
            f"Failed to fetch AWS secret '{secret_ref}': {e}",
            code="E_SECRET_FETCH_FAILED",
            reason="secret_resolver",
        )

    secret = (res.stdout or "").strip()
    if not secret or secret == "None":
        raise PlatformError(
            f"AWS secret '{secret_ref}' is empty.",
            code="E_SECRET_EMPTY",
            reason="secret_resolver",
        )
    return secret


def extract_secret_value(secret_raw: str, *, json_keys: Optional[Sequence[str]] = None) -> str:
    raw = (secret_raw or "").strip()
    if not raw:
        return ""

    if not raw.startswith("{"):
        return raw

    try:
        payload: Any = json.loads(raw)
    except Exception:
        return raw

    if not isinstance(payload, dict):
        return raw

    for key in json_keys or ():
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return raw


def resolve_runtime_value(
    env_key: str,
    *,
    default: str = "",
    secret_id_env_key: Optional[str] = None,
    secret_json_key: Optional[str] = None,
    aws_region_env_key: str = "GHDP_AWS_REGION",
) -> str:
    direct = (os.getenv(env_key) or "").strip()
    if direct:
        return direct

    secret_ref_key = (secret_id_env_key or f"{env_key}_SECRET_ID").strip()
    secret_ref = (os.getenv(secret_ref_key) or "").strip()
    if not secret_ref:
        return default

    region = (os.getenv(aws_region_env_key) or "").strip() or None
    secret_raw = fetch_aws_secret_string(secret_ref, region=region)

    if not secret_json_key:
        return extract_secret_value(secret_raw)

    try:
        payload: Any = json.loads(secret_raw)
    except Exception:
        raise PlatformError(
            f"AWS secret '{secret_ref}' is not valid JSON.",
            code="E_SECRET_PARSE_FAILED",
            reason="secret_resolver",
        )

    if not isinstance(payload, dict):
        raise PlatformError(
            f"AWS secret '{secret_ref}' must be a JSON object.",
            code="E_SECRET_PARSE_FAILED",
            reason="secret_resolver",
        )

    value = str(payload.get(secret_json_key, "") or "").strip()
    if not value:
        raise PlatformError(
            f"AWS secret '{secret_ref}' does not contain key '{secret_json_key}'.",
            code="E_SECRET_KEY_MISSING",
            reason="secret_resolver",
        )
    return value
