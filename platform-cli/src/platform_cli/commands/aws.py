"""AWS helper commands.

NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
"""
from __future__ import annotations

from typing import Optional

import typer

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.decorators import command_meta, tracked_command
from platform_cli.tools.aws_profile import (
    get_global_active_profile,
    get_repo_active_profile,
    list_configured_aws_profiles,
    profile_exists,
    resolve_aws_profile,
    set_active_profile,
)
from platform_cli.tools.aws_sso import (
    AwsSsoDefaults,
    DEFAULTS,
    aws_sso_login,
    aws_sso_token_status,
    ensure_sso_configured,
    is_sso_configured,
)

app = typer.Typer(help="AWS helper commands (SSO setup/login).", no_args_is_help=True)
profile_app = typer.Typer(help="Manage AWS profile selection for GHDP.", no_args_is_help=True)
token_app = typer.Typer(help="AWS SSO token commands.", no_args_is_help=True)


def register(root_app: typer.Typer) -> None:
    root_app.add_typer(app, name="aws")
    app.add_typer(profile_app, name="profile")
    app.add_typer(token_app, name="token")


@app.command("sso")
@tracked_command("aws sso")
@command_meta(
    name="aws sso",
    category="aws",
    description="Configure AWS SSO profile.",
    tags=["aws", "sso"],
)
def sso_setup(
    profile: Optional[str] = typer.Option(None, "--profile", help="AWS CLI profile name"),
    sso_session_name: str = typer.Option(DEFAULTS.sso_session_name, "--sso-session-name"),
    sso_start_url: str = typer.Option(DEFAULTS.sso_start_url, "--sso-start-url"),
    sso_region: str = typer.Option(DEFAULTS.sso_region, "--sso-region"),
    cli_default_region: str = typer.Option(DEFAULTS.cli_default_region, "--default-region"),
    cli_default_output: str = typer.Option(DEFAULTS.cli_default_output, "--default-output"),
) -> None:
    """One-time AWS SSO configuration (runs `aws configure sso`)."""
    resolved = resolve_aws_profile(
        explicit_profile=profile,
        prompt_if_unresolved=True,
        prompt_when_flag_missing=(profile is None),
        persist_prompt_scope="global",
    )
    selected_profile = resolved.profile

    defaults = AwsSsoDefaults(
        sso_session_name=sso_session_name,
        sso_start_url=sso_start_url,
        sso_region=sso_region,
        profile_name=selected_profile,
        cli_default_region=cli_default_region,
        cli_default_output=cli_default_output,
    )
    ensure_sso_configured(profile=selected_profile, defaults=defaults)
    typer.echo(f"AWS SSO profile configured: '{selected_profile}' (source={resolved.source})")


@app.command("login")
@tracked_command("aws login")
@command_meta(
    name="aws login",
    category="aws",
    description="Log in to AWS SSO.",
    tags=["aws", "login"],
)
def login(profile: Optional[str] = typer.Option(None, "--profile", help="AWS CLI profile name")) -> None:
    """Login/refresh AWS SSO session (runs `aws sso login`)."""
    resolved = resolve_aws_profile(
        explicit_profile=profile,
        prompt_if_unresolved=True,
        prompt_when_flag_missing=(profile is None),
        persist_prompt_scope="global",
    )
    aws_sso_login(profile=resolved.profile)
    typer.echo(f"AWS SSO login succeeded for profile '{resolved.profile}' (source={resolved.source})")


@app.command("status")
@tracked_command("aws status")
@command_meta(
    name="aws status",
    category="aws",
    description="AWS profile + token status.",
    tags=["aws", "sso", "status"],
)
def status(profile: Optional[str] = typer.Option(None, "--profile", help="AWS CLI profile name")) -> None:
    """Show profile resolution, SSO config status, and token status."""
    resolved = resolve_aws_profile(
        explicit_profile=profile,
        prompt_if_unresolved=False,
        prompt_when_flag_missing=(profile is None),
        persist_prompt_scope="global",
    )
    selected_profile = resolved.profile

    cfg_ok = is_sso_configured(selected_profile)
    token_state, token_detail = aws_sso_token_status(selected_profile)

    typer.echo(f"profile: {selected_profile} (source={resolved.source})")
    typer.echo(f"sso_configured: {'yes' if cfg_ok else 'no'}")
    typer.echo(f"token: {token_state}")
    if token_state != "valid" and token_detail:
        typer.echo(f"token_detail: {token_detail[:220]}")


@profile_app.command("list")
@tracked_command("aws profile list")
@command_meta(
    name="aws profile list",
    category="aws",
    description="List configured AWS profiles from ~/.aws/config.",
    tags=["aws", "profile"],
)
def profile_list() -> None:
    profiles = list_configured_aws_profiles()
    repo_profile = get_repo_active_profile()
    global_profile = get_global_active_profile()

    if not profiles:
        typer.echo("No AWS profiles found in ~/.aws/config")
        return

    typer.echo("Configured AWS profiles:")
    for p in profiles:
        marks = []
        if p == repo_profile:
            marks.append("repo-active")
        if p == global_profile:
            marks.append("global-active")
        suffix = f" [{', '.join(marks)}]" if marks else ""
        typer.echo(f"- {p}{suffix}")


@profile_app.command("use")
@tracked_command("aws profile use")
@command_meta(
    name="aws profile use",
    category="aws",
    description="Set active AWS profile for GHDP.",
    tags=["aws", "profile"],
)
def profile_use(
    profile: str = typer.Option(..., "--profile", help="Profile to activate"),
    scope: str = typer.Option("global", "--scope", help="Scope: global or repo"),
) -> None:
    profile_norm = profile.strip()
    known_profiles = list_configured_aws_profiles()
    known = profile_exists(profile_norm, known_profiles)
    if known_profiles and not known:
        if bool(cli_ctx.non_interactive):
            raise typer.BadParameter(
                f"Profile '{profile_norm}' was not found in local AWS config. "
                "Run `ghdp aws profile list` and pick an existing profile."
            )
        proceed = typer.confirm(
            f"Profile '{profile_norm}' is not in discovered local AWS profiles. Continue anyway?",
            default=False,
        )
        if not proceed:
            raise typer.BadParameter("Aborted by user.")

    scope_norm = (scope or "global").strip().lower()
    if scope_norm not in {"global", "repo"}:
        raise typer.BadParameter("--scope must be one of: global, repo")

    try:
        saved_scope = set_active_profile(profile_norm, scope=scope_norm)
    except ValueError as e:
        raise typer.BadParameter(str(e))

    typer.echo(f"Active AWS profile set to '{profile_norm}' (scope={saved_scope})")


@profile_app.command("current")
@tracked_command("aws profile current")
@command_meta(
    name="aws profile current",
    category="aws",
    description="Show effective AWS profile and source.",
    tags=["aws", "profile"],
)
def profile_current(profile: Optional[str] = typer.Option(None, "--profile", help="Optional override")) -> None:
    resolved = resolve_aws_profile(
        explicit_profile=profile,
        prompt_if_unresolved=False,
        prompt_when_flag_missing=(profile is None),
        persist_prompt_scope="global",
    )
    typer.echo(f"profile: {resolved.profile}")
    typer.echo(f"source: {resolved.source}")


@token_app.command("status")
@tracked_command("aws token status")
@command_meta(
    name="aws token status",
    category="aws",
    description="Check AWS SSO token status for a profile.",
    tags=["aws", "token"],
)
def token_status(profile: Optional[str] = typer.Option(None, "--profile", help="AWS CLI profile name")) -> None:
    resolved = resolve_aws_profile(
        explicit_profile=profile,
        prompt_if_unresolved=(profile is None),
        prompt_when_flag_missing=(profile is None),
        persist_prompt_scope="global",
        allow_default_fallback=False,
    )
    token_state, token_detail = aws_sso_token_status(resolved.profile)
    typer.echo(f"profile: {resolved.profile} (source={resolved.source})")
    typer.echo(f"token: {token_state}")
    if token_state != "valid" and token_detail:
        typer.echo(f"detail: {token_detail[:220]}")


@token_app.command("refresh")
@tracked_command("aws token refresh")
@command_meta(
    name="aws token refresh",
    category="aws",
    description="Refresh AWS SSO token for a profile.",
    tags=["aws", "token"],
)
def token_refresh(
    profile: Optional[str] = typer.Option(None, "--profile", help="AWS CLI profile name"),
    if_expired: bool = typer.Option(False, "--if-expired", help="Only refresh when token is invalid"),
) -> None:
    resolved = resolve_aws_profile(
        explicit_profile=profile,
        prompt_if_unresolved=(profile is None),
        prompt_when_flag_missing=(profile is None),
        persist_prompt_scope="global",
        allow_default_fallback=False,
    )

    if if_expired:
        token_state, _ = aws_sso_token_status(resolved.profile)
        if token_state == "valid":
            typer.echo(f"Token already valid for profile '{resolved.profile}'. Skipping refresh.")
            return

    aws_sso_login(profile=resolved.profile)
    typer.echo(f"Token refresh complete for profile '{resolved.profile}' (source={resolved.source})")

