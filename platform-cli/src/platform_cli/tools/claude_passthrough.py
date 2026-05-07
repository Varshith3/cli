# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

import os
from typing import List, Optional

import typer

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.exec.runner import run_cmd
from platform_cli.tools.athena_workgroup import resolve_athena_workgroup
from platform_cli.tools.aws_profile import (
    AwsProfileResolution,
    prompt_aws_profile_choice,
    resolve_aws_profile,
)
from platform_cli.tools.aws_sso import (
    aws_sso_login,
    aws_sso_token_status,
    ensure_sso_configured,
)


def _show_claude_launch_profile(profile: str, source: str) -> None:
    typer.echo(f"Claude launch AWS profile: {profile} (source={source})")


def _resolve_claude_launch_profile(
    *,
    explicit_profile: Optional[str] = None,
    choose_profile: bool = False,
) -> AwsProfileResolution:
    resolved = resolve_aws_profile(
        explicit_profile=explicit_profile,
        prompt_if_unresolved=False,
        prompt_when_flag_missing=False,
        persist_prompt_scope="global",
    )

    if bool(cli_ctx.non_interactive):
        _show_claude_launch_profile(resolved.profile, resolved.source)
        return resolved

    if choose_profile:
        chosen = prompt_aws_profile_choice(default_profile=resolved.profile)
        final = AwsProfileResolution(profile=chosen, source="prompt", repo_key=resolved.repo_key)
        _show_claude_launch_profile(final.profile, final.source)
        return final

    if explicit_profile:
        _show_claude_launch_profile(resolved.profile, resolved.source)
        return resolved

    keep_current = typer.confirm(
        f"Use AWS profile '{resolved.profile}' for this Claude launch?",
        default=True,
    )
    if keep_current:
        _show_claude_launch_profile(resolved.profile, resolved.source)
        return resolved

    chosen = prompt_aws_profile_choice(default_profile=resolved.profile)
    final = AwsProfileResolution(profile=chosen, source="prompt", repo_key=resolved.repo_key)
    _show_claude_launch_profile(final.profile, final.source)
    return final


def _ensure_claude_launch_aws_ready(profile: str) -> None:
    ensure_sso_configured(profile=profile)

    token_state, _token_detail = aws_sso_token_status(profile)
    if token_state == "valid":
        typer.echo(f"AWS SSO token is valid for profile '{profile}'. Skipping login.")
        return

    typer.echo(f"AWS SSO token is missing or expired for profile '{profile}'. Running login...")
    aws_sso_login(profile=profile)


def run_claude_passthrough(args: Optional[List[str]] = None) -> int:
    """
    Execute claude CLI with passthrough arguments.
    Returns claude process exit code.
    """
    forwarded = list(args or [])

    res = run_cmd(["claude", *forwarded], check=False, capture=False)
    return int(res.returncode or 0)


def run_claude_launch(
    args: Optional[List[str]] = None,
    *,
    explicit_profile: Optional[str] = None,
    choose_profile: bool = False,
) -> int:
    """
    Execute Claude CLI with a launch-time AWS profile resolution step.

    The selected profile is applied only to the launched Claude process and is
    not persisted back into GHDP config or shell state.
    """
    forwarded = list(args or [])
    resolved = _resolve_claude_launch_profile(
        explicit_profile=explicit_profile,
        choose_profile=choose_profile,
    )
    _ensure_claude_launch_aws_ready(resolved.profile)
    env = dict(os.environ)
    env["CLAUDE_CODE_USE_BEDROCK"] = "1"
    env["AWS_REGION"] = "us-west-2"
    env["AWS_PROFILE"] = resolved.profile
    workgroup_resolution = resolve_athena_workgroup(aws_profile=resolved.profile)
    if workgroup_resolution.workgroup:
        env["DP_AWS_ATHENA_WORKGROUP"] = workgroup_resolution.workgroup
    else:
        env.pop("DP_AWS_ATHENA_WORKGROUP", None)
    res = run_cmd(["claude", *forwarded], check=False, capture=False, env=env)
    return int(res.returncode or 0)
