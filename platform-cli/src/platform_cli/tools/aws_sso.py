# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import os
import re
import shutil
import sys
import time
import typer

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.errors import PlatformError
from platform_cli.exec.runner import run_cmd
from platform_cli.state.store import update_tool_state
from platform_cli.tools.aws_profile import AwsProfileResolution, apply_active_profile_env, resolve_aws_profile


@dataclass(frozen=True)
class AwsSsoDefaults:
    sso_session_name: str = os.getenv("GHDP_AWS_SSO_SESSION_NAME", "")
    sso_start_url: str = os.getenv("GHDP_AWS_SSO_START_URL", "")
    sso_region: str = os.getenv("GHDP_AWS_SSO_REGION", "")
    registration_scopes: str = os.getenv("GHDP_AWS_SSO_REGISTRATION_SCOPES", "sso:account:access")
    profile_name: str = os.getenv("GHDP_AWS_SSO_PROFILE_NAME", "default")
    cli_default_region: str = os.getenv("GHDP_AWS_DEFAULT_REGION", "")
    cli_default_output: str = os.getenv("GHDP_AWS_DEFAULT_OUTPUT", "json")


DEFAULTS = AwsSsoDefaults()


def _ensure_required_aws_defaults(defaults: AwsSsoDefaults) -> None:
    missing: list[str] = []
    if not (defaults.sso_session_name or "").strip():
        missing.append("GHDP_AWS_SSO_SESSION_NAME")
    if not (defaults.sso_start_url or "").strip():
        missing.append("GHDP_AWS_SSO_START_URL")
    if not (defaults.sso_region or "").strip():
        missing.append("GHDP_AWS_SSO_REGION")
    if not (defaults.cli_default_region or "").strip():
        missing.append("GHDP_AWS_DEFAULT_REGION")
    if missing:
        raise PlatformError(
            "Missing required AWS runtime defaults: " + ", ".join(missing),
            code="E_CONFIG_REQUIRED",
            reason="aws_sso",
        )


def _aws_config_path() -> Path:
    return Path.home() / ".aws" / "config"


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except Exception:
        return p.read_text(errors="ignore")


def _extract_section(text: str, section_re: re.Pattern) -> str:
    out = []
    in_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            in_section = bool(section_re.match(line))
            continue
        if in_section:
            out.append(raw)
    return "\n".join(out)


# -------------------------
# Windows PATH / aws.exe resolution helpers
# -------------------------

def _refresh_windows_path_in_process() -> None:
    if not sys.platform.startswith("win"):
        return

    ps = (
        "$m=[Environment]::GetEnvironmentVariable('Path','Machine');"
        "$u=[Environment]::GetEnvironmentVariable('Path','User');"
        "Write-Output ($m + ';' + $u)"
    )
    try:
        res = run_cmd(["powershell", "-NoProfile", "-Command", ps], check=False)
        combined = (res.stdout or "").strip()
        if combined:
            os.environ["PATH"] = combined
    except Exception:
        pass


def _candidate_windows_aws_paths() -> List[Path]:
    candidates: List[Path] = []

    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    pw6432 = os.environ.get("ProgramW6432", pf)

    for base in {pf, pf86, pw6432}:
        candidates.append(Path(base) / "Amazon" / "AWSCLIV2" / "aws.exe")

    lad = os.environ.get("LOCALAPPDATA")
    if lad:
        candidates.append(Path(lad) / "Amazon" / "AWSCLIV2" / "aws.exe")

    return candidates


def _resolve_aws_cmd() -> List[str]:
    p = shutil.which("aws")
    if p:
        return ["aws"]

    if sys.platform.startswith("win"):
        _refresh_windows_path_in_process()

        p2 = shutil.which("aws")
        if p2:
            return ["aws"]

        for c in _candidate_windows_aws_paths():
            if c.exists():
                return [str(c)]

    return ["aws"]


def _run_aws(args: List[str], *, capture: bool = False, check: bool = True):
    cmd = _resolve_aws_cmd() + args
    try:
        return run_cmd(cmd, capture=capture, check=check)
    except PlatformError as e:
        if sys.platform.startswith("win") and e.code == "E_CMD_NOT_FOUND" and e.reason == "aws":
            raise PlatformError(
                "AWS CLI is installed but not discoverable in this session.\n"
                "GHDP could not locate aws.exe. Try reopening PowerShell once, "
                "or ensure AWS CLI v2 is installed under:\n"
                r"  C:\Program Files\Amazon\AWSCLIV2\aws.exe",
                code="E_AWSCLI_NOT_DISCOVERABLE",
                reason="awscli",
            )
        raise


def _run_aws_interactive(args: List[str]) -> None:
    cmd = _resolve_aws_cmd() + args
    if sys.platform.startswith("win"):
        cmd = ["cmd.exe", "/c", *cmd]
    try:
        run_cmd(cmd, capture=False, check=True)
    except PlatformError as e:
        if sys.platform.startswith("win") and "No Windows console found" in str(e):
            raise PlatformError(
                "AWS CLI interactive SSO setup needs a Windows console session. "
                "Run this command from PowerShell or cmd.exe.",
                code="E_AWS_SSO_CONSOLE_REQUIRED",
                reason="aws_sso",
            )
        raise


# -------------------------
# SSO config logic
# -------------------------

def is_sso_configured(profile: str = DEFAULTS.profile_name) -> bool:
    text = _read_text(_aws_config_path())
    if not text.strip():
        return False

    prof = profile.strip()

    if prof == "default":
        section_re = re.compile(r"^\[(default|profile\s+default)\]$", re.IGNORECASE)
    else:
        section_re = re.compile(rf"^\[profile\s+{re.escape(prof)}\]$", re.IGNORECASE)

    body = _extract_section(text, section_re).lower()

    has_role = "sso_role_name" in body
    has_acct = "sso_account_id" in body
    has_new = "sso_session" in body
    has_legacy = ("sso_start_url" in body) and ("sso_region" in body)

    return (has_role and has_acct) and (has_new or has_legacy)


def aws_sso_token_status(profile: str) -> Tuple[str, str]:
    try:
        _run_aws(["sts", "get-caller-identity", "--profile", profile], check=True, capture=True)
        return ("valid", "")
    except Exception as e:
        return ("invalid", str(e))


def ensure_sso_configured(profile: str = DEFAULTS.profile_name, defaults: AwsSsoDefaults = DEFAULTS) -> None:
    if is_sso_configured(profile):
        typer.echo(f"AWS SSO already configured for profile '{profile}'. Skipping wizard.")
        return

    if bool(cli_ctx.non_interactive):
        raise PlatformError(
            "AWS SSO is not configured yet, but GHDP is running in non-interactive mode. "
            "Run `ghdp aws sso` interactively once.",
            code="E_AWS_SSO_NEEDS_INTERACTIVE",
            reason="aws_sso",
        )

    _ensure_required_aws_defaults(defaults)

    typer.echo("")
    typer.echo("AWS SSO setup (one-time) is required for GuardantHealth AWS access.")
    typer.echo("GHDP will run: aws configure sso")
    typer.echo("")
    typer.echo("Use these values when prompted:")
    typer.echo(f"  SSO session name:        {defaults.sso_session_name}")
    typer.echo(f"  SSO start URL:           {defaults.sso_start_url}")
    typer.echo(f"  SSO region:              {defaults.sso_region}")
    typer.echo(f"  SSO registration scopes: {defaults.registration_scopes}")
    typer.echo(f"  Default client Region:   {defaults.cli_default_region}")
    typer.echo(f"  Default output format:   {defaults.cli_default_output}")
    typer.echo(f"  Profile name:            {profile}")
    typer.echo("")
    typer.echo("A browser window will open. Complete Okta/SSO auth and select account/role.")
    typer.echo("")

    _run_aws_interactive(["configure", "sso", "--profile", profile])

    if not is_sso_configured(profile):
        raise PlatformError(
            f"AWS SSO wizard ran but profile '{profile}' still does not look configured. "
            "Check ~/.aws/config and rerun `ghdp aws sso`.",
            code="E_AWS_SSO_CONFIG_INCOMPLETE",
            reason="aws_sso",
        )


def aws_sso_login(profile: str = DEFAULTS.profile_name) -> None:
    _run_aws(["sso", "login", "--profile", profile], capture=False, check=True)


def run_aws_cli(args: List[str], *, capture: bool = False, check: bool = True):
    """Run AWS CLI with GHDP path-discovery behavior."""
    return _run_aws(args, capture=capture, check=check)


def maybe_bootstrap_after_install(profile: Optional[str] = None) -> AwsProfileResolution:
    """
    Post-install step after awscli is installed:
      1) Resolve profile and ensure SSO configured.
      2) Run aws sso login only if token is invalid.
    """
    resolved = resolve_aws_profile(
        explicit_profile=profile,
        prompt_if_unresolved=not bool(cli_ctx.non_interactive),
        persist_prompt_scope="global",
    )
    selected_profile = resolved.profile
    selected_source = resolved.source
    if selected_source in {"repo", "global", "prompt"}:
        apply_active_profile_env(selected_profile, scope="repo" if selected_source == "repo" else "global")

    update_tool_state("awscli", {"aws_sso_post_step_attempted_at": int(time.time())})

    _refresh_windows_path_in_process()

    ensure_sso_configured(profile=selected_profile, defaults=DEFAULTS)

    token_state, token_detail = aws_sso_token_status(selected_profile)
    if token_state == "valid":
        typer.echo(f"AWS SSO token is valid for profile '{selected_profile}'. Skipping login.")
    else:
        typer.echo(f"AWS SSO token is missing or expired for profile '{selected_profile}'. Running login...")
        aws_sso_login(profile=selected_profile)

    now = int(time.time())
    update_tool_state(
        "awscli",
        {
            "aws_sso_configured": True,
            "aws_sso_profile": selected_profile,
            "aws_sso_profile_source": selected_source,
            "aws_sso_last_login_at": now,
            "aws_sso_last_success_profile": selected_profile,
            "aws_sso_last_success_at": now,
            "aws_sso_token_state": token_state,
            "aws_sso_token_detail": token_detail[:500],
        },
    )
    return AwsProfileResolution(profile=selected_profile, source=selected_source, repo_key=resolved.repo_key)
