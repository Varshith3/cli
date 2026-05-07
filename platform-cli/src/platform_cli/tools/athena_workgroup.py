from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import typer

from platform_cli.core.config import delete_value, get_value, set_value
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.errors import PlatformError
from platform_cli.manifests.load import load_claude_athena_workgroup_map
from platform_cli.tools.aws_sso import run_aws_cli


ATHENA_WORKGROUP_KEY = "claude.athena_workgroup"
ASSUMED_ROLE_ARN_RE = re.compile(r"^arn:aws[a-zA-Z-]*:sts::(?P<account_id>\d{12}):assumed-role/(?P<role_name>[^/]+)/.+$")


@dataclass(frozen=True)
class AthenaWorkgroupResolution:
    workgroup: str
    source: str  # env | derived | config | prompt | deferred
    aws_profile: str
    account_id: str
    role_name: str
    mapping_source: str
    fallback_active: bool
    persisted: bool
    configured: bool
    detail_message: str


@dataclass(frozen=True)
class AthenaIdentity:
    account_id: str
    role_name: str
    arn: str


def _normalize_nonempty(value: object) -> str:
    return str(value or "").strip()


def _emit_status(status_printer: Optional[Callable[[str], None]], message: str) -> None:
    if status_printer is None:
        return
    status_printer(message)


def _parse_role_name_from_arn(arn: str) -> str:
    match = ASSUMED_ROLE_ARN_RE.match(_normalize_nonempty(arn))
    if not match:
        return ""

    role_name = _normalize_nonempty(match.group("role_name"))
    if role_name.startswith("AWSReservedSSO_"):
        remainder = role_name[len("AWSReservedSSO_") :]
        if "_" in remainder:
            derived = _normalize_nonempty(remainder.rsplit("_", 1)[0])
            if derived:
                return derived
    return role_name


def _load_athena_identity(aws_profile: str) -> Optional[AthenaIdentity]:
    try:
        result = run_aws_cli(
            ["sts", "get-caller-identity", "--profile", aws_profile, "--output", "json"],
            capture=True,
            check=True,
        )
    except Exception:
        return None

    raw = _normalize_nonempty(getattr(result, "stdout", ""))
    if not raw:
        return None

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None

    account_id = _normalize_nonempty(payload.get("Account"))
    arn = _normalize_nonempty(payload.get("Arn"))
    role_name = _parse_role_name_from_arn(arn)
    if not account_id or not role_name:
        return None
    return AthenaIdentity(account_id=account_id, role_name=role_name, arn=arn)


def _lookup_mapped_workgroup(mappings: List[Dict[str, str]], *, account_id: str, role_name: str) -> str:
    for entry in mappings:
        if _normalize_nonempty(entry.get("account_id")) != account_id:
            continue
        if _normalize_nonempty(entry.get("role_name")) != role_name:
            continue
        return _normalize_nonempty(entry.get("athena_workgroup"))
    return ""


def set_saved_athena_workgroup(workgroup: str) -> str:
    normalized = _normalize_nonempty(workgroup)
    if not normalized:
        raise PlatformError(
            "Athena workgroup cannot be blank.",
            code="E_CLAUDE_ATHENA_WORKGROUP_INVALID",
            reason="claude",
        )
    set_value(ATHENA_WORKGROUP_KEY, normalized)
    return normalized


def clear_saved_athena_workgroup() -> None:
    delete_value(ATHENA_WORKGROUP_KEY)


def get_saved_athena_workgroup() -> str:
    return _normalize_nonempty(get_value(ATHENA_WORKGROUP_KEY, ""))


def _deferred_resolution(
    *,
    aws_profile: str,
    account_id: str = "",
    role_name: str = "",
    mapping_source: str = "",
    fallback_active: bool = False,
    persisted: bool = False,
    detail_message: str,
) -> AthenaWorkgroupResolution:
    return AthenaWorkgroupResolution(
        workgroup="",
        source="deferred",
        aws_profile=aws_profile,
        account_id=account_id,
        role_name=role_name,
        mapping_source=mapping_source,
        fallback_active=fallback_active,
        persisted=persisted,
        configured=False,
        detail_message=detail_message,
    )


def _prompt_for_athena_workgroup(
    *,
    aws_profile: str,
    identity: Optional[AthenaIdentity],
    mapping_source: str,
    fallback_active: bool,
    mapping_issue: str = "",
    status_printer: Optional[Callable[[str], None]] = None,
) -> AthenaWorkgroupResolution:
    if mapping_issue:
        _emit_status(status_printer, "Claude Athena mapping is unavailable; prompting for manual entry or skip...")
    else:
        _emit_status(status_printer, "No Athena mapping match found; prompting for manual entry or skip...")
    typer.echo("")
    typer.echo("Claude Code needs an Athena workgroup to configure Bedrock/Athena helpers.")
    if mapping_issue:
        typer.echo("GHDP could not load the internal Athena workgroup mapping.")
        typer.echo(mapping_issue)
    else:
        typer.echo("GHDP could not resolve one automatically from your current AWS identity or saved config.")
    if identity is not None and not mapping_issue:
        typer.echo(f"Current AWS identity: account {identity.account_id}, role {identity.role_name}")
    typer.echo("")
    if not typer.confirm("Enter an Athena workgroup now?", default=True):
        set_value(ATHENA_WORKGROUP_KEY, "")
        detail = "Claude Athena workgroup was skipped for now. Configure it later with `ghdp config claude-athena-workgroup --value <name>`."
        if identity is not None:
            detail = (
                "No Claude Athena workgroup mapping matched AWS identity "
                f"(account {identity.account_id}, role {identity.role_name}); skipped for now."
            )
        if mapping_issue:
            detail = (
                "Claude Athena workgroup mapping was unavailable; skipped for now. "
                "Configure it later with `ghdp config claude-athena-workgroup --value <name>`."
            )
        return _deferred_resolution(
            aws_profile=aws_profile,
            account_id=identity.account_id if identity else "",
            role_name=identity.role_name if identity else "",
            mapping_source=mapping_source,
            fallback_active=fallback_active if identity is not None else False,
            persisted=True,
            detail_message=detail,
        )
    typer.echo("Enter the Athena workgroup to use for Claude on this machine.")
    typer.echo("")
    workgroup = set_saved_athena_workgroup(typer.prompt("DP_AWS_ATHENA_WORKGROUP"))
    detail = "Saved the prompted Athena workgroup to GHDP config for future Claude installs."
    if identity is not None:
        detail = (
            "No Claude Athena workgroup mapping matched AWS identity "
            f"(account {identity.account_id}, role {identity.role_name}); prompted and saved the value you entered."
        )
    if mapping_issue:
        detail = "Claude Athena workgroup mapping was unavailable; prompted and saved the value you entered."
    return AthenaWorkgroupResolution(
        workgroup=workgroup,
        source="prompt",
        aws_profile=aws_profile,
        account_id=identity.account_id if identity else "",
        role_name=identity.role_name if identity else "",
        mapping_source=mapping_source,
        fallback_active=fallback_active if identity is not None else False,
        persisted=True,
        configured=True,
        detail_message=detail,
    )


def resolve_athena_workgroup(
    *,
    aws_profile: str,
    status_printer: Optional[Callable[[str], None]] = None,
) -> AthenaWorkgroupResolution:
    _emit_status(status_printer, "Resolving Claude Athena workgroup...")
    _emit_status(status_printer, "Checking for DP_AWS_ATHENA_WORKGROUP override...")
    env_val = _normalize_nonempty(os.environ.get("DP_AWS_ATHENA_WORKGROUP"))
    if env_val:
        _emit_status(status_printer, "Using Athena workgroup from environment override.")
        return AthenaWorkgroupResolution(
            workgroup=env_val,
            source="env",
            aws_profile=aws_profile,
            account_id="",
            role_name="",
            mapping_source="",
            fallback_active=False,
            persisted=False,
            configured=True,
            detail_message="Resolved Athena workgroup from the DP_AWS_ATHENA_WORKGROUP environment override.",
        )

    mappings: List[Dict[str, str]] = []
    mapping_source = ""
    fallback_active = False
    mapping_issue = ""
    _emit_status(status_printer, "Loading Claude Athena workgroup mapping...")
    try:
        mappings, mapping_source, fallback_active = load_claude_athena_workgroup_map()
    except PlatformError as exc:
        mapping_issue = str(exc)
        _emit_status(status_printer, "Claude Athena workgroup mapping is unavailable; checking saved config next...")

    _emit_status(status_printer, f"Checking AWS identity for profile '{aws_profile}'...")
    identity = _load_athena_identity(aws_profile)
    if identity is not None:
        mapped = _lookup_mapped_workgroup(mappings, account_id=identity.account_id, role_name=identity.role_name)
        if mapped:
            set_saved_athena_workgroup(mapped)
            if fallback_active:
                _emit_status(status_printer, "Resolved Athena workgroup from AWS identity using packaged fallback mapping.")
            else:
                _emit_status(status_printer, "Resolved Athena workgroup from AWS identity mapping.")
            detail = (
                "Resolved Athena workgroup from AWS identity "
                f"(account {identity.account_id}, role {identity.role_name}) and saved it to GHDP config"
            )
            if fallback_active:
                detail += " using the packaged backup mapping."
            else:
                detail += "."
            return AthenaWorkgroupResolution(
                workgroup=mapped,
                source="derived",
                aws_profile=aws_profile,
                account_id=identity.account_id,
                role_name=identity.role_name,
                mapping_source=mapping_source,
                fallback_active=fallback_active,
                persisted=True,
                configured=True,
                detail_message=detail,
            )

    _emit_status(status_printer, "Checking saved Claude Athena workgroup config...")
    cfg_val = get_saved_athena_workgroup()
    if cfg_val:
        _emit_status(status_printer, "Using saved Claude Athena workgroup from GHDP config.")
        detail = "Resolved Athena workgroup from saved GHDP config."
        if identity is not None:
            detail = (
                "No Claude Athena workgroup mapping matched AWS identity "
                f"(account {identity.account_id}, role {identity.role_name}); reused saved GHDP config."
            )
        elif mapping_issue:
            detail = "Claude Athena workgroup mapping was unavailable; reused saved GHDP config."
        return AthenaWorkgroupResolution(
            workgroup=cfg_val,
            source="config",
            aws_profile=aws_profile,
            account_id=identity.account_id if identity else "",
            role_name=identity.role_name if identity else "",
            mapping_source=mapping_source,
            fallback_active=fallback_active if identity is not None else False,
            persisted=False,
            configured=True,
            detail_message=detail,
        )

    if bool(cli_ctx.non_interactive):
        _emit_status(
            status_printer,
            "No Athena workgroup could be resolved automatically and prompting is unavailable in non-interactive mode.",
        )
        reason_bits: List[str] = []
        if mapping_issue:
            reason_bits.append("internal Athena workgroup mapping was unavailable")
        elif identity is not None:
            reason_bits.append(
                f"no internal mapping matched account_id={identity.account_id} role_name={identity.role_name}"
            )
        else:
            reason_bits.append("AWS identity could not be derived from an assumed-role STS ARN")
        return _deferred_resolution(
            aws_profile=aws_profile,
            account_id=identity.account_id if identity else "",
            role_name=identity.role_name if identity else "",
            mapping_source=mapping_source,
            fallback_active=fallback_active if identity is not None else False,
            persisted=False,
            detail_message=(
                "Claude Athena workgroup could not be derived automatically and no saved config exists; "
                + "; ".join(reason_bits)
                + ". Configure it later with `ghdp config claude-athena-workgroup --value <name>`."
            ),
        )

    return _prompt_for_athena_workgroup(
        aws_profile=aws_profile,
        identity=identity,
        mapping_source=mapping_source,
        fallback_active=fallback_active,
        mapping_issue=mapping_issue,
        status_printer=status_printer,
    )
