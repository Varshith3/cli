# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
from __future__ import annotations

import json
import os
from base64 import b64encode
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from typing import Any

from platform_cli.core.errors import PlatformError
from platform_cli.exec.runner import run_cmd


@dataclass(frozen=True)
class JiraValidationResult:
    ticket: str
    found: bool
    warning: str = ""


def validate_jira_ticket(ticket: str, *, mode: str) -> JiraValidationResult:
    if mode == "skip":
        return JiraValidationResult(ticket=ticket, found=False, warning="")

    try:
        res = run_cmd(["acli", "jira", "workitem", "view", ticket, "--json"], check=False)
    except PlatformError as e:
        if getattr(e, "code", "") == "E_CMD_NOT_FOUND":
            return _validate_via_rest_or_warn(ticket=ticket, mode=mode, fallback_warning="Atlassian CLI (acli) is not available")
        raise

    if res.returncode != 0:
        output = (res.stderr or res.stdout or "").strip()
        low = output.lower()
        if "auth" in low or "login" in low or "token" in low:
            return _validate_via_rest_or_warn(ticket=ticket, mode=mode, fallback_warning="Atlassian CLI authentication is not valid")
        return _validate_via_rest_or_warn(
            ticket=ticket,
            mode=mode,
            fallback_warning=f"Jira ticket '{ticket}' was not found via Atlassian CLI.",
        )

    return JiraValidationResult(ticket=ticket, found=True)


def fetch_jira_context(ticket: str, *, mode: str) -> dict[str, str]:
    try:
        res = run_cmd(["acli", "jira", "workitem", "view", ticket, "--json"], check=False)
    except PlatformError:
        return _fetch_jira_context_via_rest(ticket) or {"summary": "", "description": ""}

    if res.returncode != 0:
        if mode == "enforce":
            validate_jira_ticket(ticket, mode=mode)
        return _fetch_jira_context_via_rest(ticket) or {"summary": "", "description": ""}

    try:
        payload = json.loads(res.stdout or "{}")
    except Exception:
        return _fetch_jira_context_via_rest(ticket) or {"summary": "", "description": ""}

    return _extract_context(payload)


def comment_on_jira_ticket(ticket: str, body: str) -> None:
    config = _jira_rest_config()
    if config is None:
        raise PlatformError(
            "Jira commenting requires JIRA_URL, JIRA_USER, and JIRA_TOKEN.",
            code="E_JIRA_COMMENT_CONFIG_MISSING",
            reason="jira_comment",
        )
    payload = {"body": body}
    _jira_rest_request(
        method="POST",
        url=f"{config['base_url']}/rest/api/3/issue/{ticket}/comment",
        username=config["user"],
        token=config["token"],
        body=json.dumps(payload).encode("utf-8"),
        content_type="application/json",
    )


def _extract_context(payload: Any) -> dict[str, str]:
    title = _first_nonempty(
        _pick(payload, "fields.summary"),
        _pick(payload, "summary"),
        _pick(payload, "title"),
        _pick(payload, "data.fields.summary"),
        _pick(payload, "data.summary"),
    )
    description_raw = _first_nonempty(
        _pick(payload, "fields.description"),
        _pick(payload, "description"),
        _pick(payload, "data.fields.description"),
        _pick(payload, "data.description"),
    )
    return {
        "summary": _stringify_text(title),
        "description": _stringify_text(description_raw),
    }


def _validate_via_rest_or_warn(*, ticket: str, mode: str, fallback_warning: str) -> JiraValidationResult:
    context = _fetch_jira_context_via_rest(ticket)
    if context is not None:
        return JiraValidationResult(ticket=ticket, found=True)
    return _handle_validation_failure(
        ticket,
        mode=mode,
        warning=f"{fallback_warning}; skipping Jira validation.",
        code="E_JIRA_TICKET_NOT_FOUND" if mode == "enforce" else None,
    )


def _fetch_jira_context_via_rest(ticket: str) -> dict[str, str] | None:
    config = _jira_rest_config()
    if config is None:
        return None
    try:
        raw = _jira_rest_request(
            method="GET",
            url=f"{config['base_url']}/rest/api/3/issue/{ticket}",
            username=config["user"],
            token=config["token"],
            body=None,
            content_type="application/json",
        )
    except PlatformError:
        return None

    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    return _extract_context(payload)


def _jira_rest_config() -> dict[str, str] | None:
    base_url = str(os.getenv("JIRA_URL", "") or "").strip().rstrip("/")
    user = str(os.getenv("JIRA_USER", "") or "").strip()
    token = str(os.getenv("JIRA_TOKEN", "") or "").strip()
    if not base_url or not user or not token:
        return None
    return {"base_url": base_url, "user": user, "token": token}


def _jira_rest_request(
    *,
    method: str,
    url: str,
    username: str,
    token: str,
    body: bytes | None,
    content_type: str,
) -> bytes:
    auth = b64encode(f"{username}:{token}".encode("utf-8")).decode("ascii")
    request = Request(url, data=body, method=method)
    request.add_header("Accept", "application/json")
    request.add_header("Authorization", f"Basic {auth}")
    if body is not None:
        request.add_header("Content-Type", content_type)

    try:
        with urlopen(request) as response:
            return response.read()
    except HTTPError as exc:
        if exc.code == 404:
            raise PlatformError(
                "Jira ticket was not found via REST API.",
                code="E_JIRA_TICKET_NOT_FOUND",
                reason="jira",
            ) from exc
        if exc.code in {401, 403}:
            raise PlatformError(
                "Jira REST authentication is not valid.",
                code="E_JIRA_AUTH_REQUIRED",
                reason="jira",
            ) from exc
        raise PlatformError(
            f"Jira REST request failed with HTTP {exc.code}.",
            code="E_JIRA_REQUEST_FAILED",
            reason="jira",
        ) from exc
    except URLError as exc:
        raise PlatformError(
            f"Jira REST request failed: {exc.reason}",
            code="E_JIRA_REQUEST_FAILED",
            reason="jira",
        ) from exc


def _handle_validation_failure(
    ticket: str,
    *,
    mode: str,
    warning: str,
    code: str | None,
) -> JiraValidationResult:
    if mode == "enforce":
        raise PlatformError(warning, code=code or "E_JIRA_VALIDATION_FAILED", reason="jira")
    return JiraValidationResult(ticket=ticket, found=False, warning=warning)


def _pick(obj: Any, path: str) -> Any:
    current = obj
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _first_nonempty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return ""


def _stringify_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_stringify_text(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        if value.get("type") == "text":
            return str(value.get("text") or "").strip()
        content = value.get("content")
        if isinstance(content, list):
            parts = [_stringify_text(item) for item in content]
            return "\n".join(part for part in parts if part).strip()
        for key in ("text", "value"):
            if isinstance(value.get(key), str) and str(value.get(key)).strip():
                return str(value.get(key)).strip()
    return str(value).strip()
