from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import platform_cli as platform_cli_pkg

from platform_cli.core.errors import PlatformError
from platform_cli.manifests.load import load_release_policy


@dataclass(frozen=True)
class ReleaseRuntime:
    channel: str
    policy_source: str


@dataclass(frozen=True)
class ReleaseGateDecision:
    command_name: str
    channel: str
    policy_source: str
    status: str
    preview_capability: str
    message: str


def normalize_release_channel(raw_channel: str | None = None) -> str:
    raw = str(raw_channel if raw_channel is not None else platform_cli_pkg.__channel__).strip().lower()
    if raw in {"beta", "prerelease"}:
        return "prerelease"
    return "stable"


def _load_policy() -> tuple[dict[str, Any], str]:
    payload, source = load_release_policy()
    if not isinstance(payload, dict):
        raise PlatformError(
            "Release policy must be a JSON object.",
            code="E_RELEASE_POLICY_INVALID",
            reason="release_policy_root",
        )
    return payload, source


def release_runtime() -> ReleaseRuntime:
    _, source = _load_policy()
    return ReleaseRuntime(channel=normalize_release_channel(), policy_source=source)


def release_gate_ci_bypass_active() -> bool:
    forced = str(os.getenv("GHDP_RELEASE_GATE_CI_BYPASS", "") or "").strip().lower()
    if forced in {"1", "true", "yes", "on"}:
        return True
    if str(os.getenv("GITHUB_ACTIONS", "") or "").strip().lower() == "true":
        return True
    if str(os.getenv("CI", "") or "").strip().lower() == "true":
        return True
    if os.getenv("JENKINS_URL", "") and os.getenv("BUILD_TAG", "").startswith("jenkins-"):
        return True
    return False


def _command_rule(policy: dict[str, Any], command_name: str, channel: str) -> dict[str, Any] | None:
    channels = policy.get("channels", {})
    if not isinstance(channels, dict):
        raise PlatformError(
            "Release policy channels must be an object.",
            code="E_RELEASE_POLICY_INVALID",
            reason="channels",
        )

    channel_payload = channels.get(channel)
    if not isinstance(channel_payload, dict):
        default_channel = str(policy.get("default_channel", "stable") or "stable").strip().lower() or "stable"
        channel_payload = channels.get(default_channel, {})
    if not isinstance(channel_payload, dict):
        return None

    blocked = channel_payload.get("blocked_commands", {})
    if not isinstance(blocked, dict):
        raise PlatformError(
            "Release policy blocked_commands must be an object.",
            code="E_RELEASE_POLICY_INVALID",
            reason="blocked_commands",
        )

    rule = blocked.get(command_name)
    return rule if isinstance(rule, dict) else None


def evaluate_release_gate(
    command_name: str,
    *,
    preview_capability: str | None = None,
    allow_admin_bypass: bool = True,
    allow_ci_bypass: bool = False,
    team: str | None = None,
) -> ReleaseGateDecision:
    policy, source = _load_policy()
    channel = normalize_release_channel()
    label = str(command_name or "").strip()
    if not label:
        raise PlatformError(
            "Release-gated command is missing a canonical command name.",
            code="E_RELEASE_POLICY_INVALID",
            reason="command_name",
        )

    rule = _command_rule(policy, label, channel)
    if not rule:
        return ReleaseGateDecision(
            command_name=label,
            channel=channel,
            policy_source=source,
            status="allowed",
            preview_capability="",
            message="",
        )

    configured_preview = str(rule.get("preview_capability", "") or "").strip()
    required_preview = str(preview_capability or configured_preview).strip()
    configured_message = str(rule.get("message", "") or "").strip()

    if allow_ci_bypass and release_gate_ci_bypass_active():
        return ReleaseGateDecision(
            command_name=label,
            channel=channel,
            policy_source=source,
            status="ci_bypass",
            preview_capability=required_preview,
            message="",
        )

    from platform_cli.core.access import resolve_access_context

    access_ctx = resolve_access_context(team=team, interactive=True)
    admin_bypass = allow_admin_bypass and access_ctx.base_persona == "admin" and access_ctx.active_mode == "admin"
    if admin_bypass:
        return ReleaseGateDecision(
            command_name=label,
            channel=channel,
            policy_source=source,
            status="admin_bypass",
            preview_capability=required_preview,
            message="",
        )

    if required_preview and required_preview in access_ctx.capabilities:
        return ReleaseGateDecision(
            command_name=label,
            channel=channel,
            policy_source=source,
            status="preview_capability",
            preview_capability=required_preview,
            message="",
        )

    support_contact = access_ctx.support_contact or "platform team"
    guidance = (
        f" Ask {support_contact} for temporary preview access, then run 'ghdp access token'."
        if required_preview
        else f" Contact {support_contact} if you need access to this preview command."
    )
    message = configured_message or "This command is not available in the stable GHDP release."
    return ReleaseGateDecision(
        command_name=label,
        channel=channel,
        policy_source=source,
        status="blocked",
        preview_capability=required_preview,
        message=f"{message}{guidance}",
    )


def ensure_release_allowed(
    command_name: str,
    *,
    preview_capability: str | None = None,
    allow_admin_bypass: bool = True,
    allow_ci_bypass: bool = False,
    team: str | None = None,
) -> None:
    decision = evaluate_release_gate(
        command_name,
        preview_capability=preview_capability,
        allow_admin_bypass=allow_admin_bypass,
        allow_ci_bypass=allow_ci_bypass,
        team=team,
    )
    if decision.status in {"allowed", "admin_bypass", "preview_capability", "ci_bypass"}:
        return

    raise PlatformError(
        decision.message,
        code="E_RELEASE_CHANNEL_BLOCKED",
        reason=decision.command_name,
    )
