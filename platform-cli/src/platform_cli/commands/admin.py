from __future__ import annotations

import typer

from platform_cli.commands._access_common import (
    activate_token_flow,
    clear_active_token_flow,
    collect_token_request_inputs,
    print_access_view,
    print_context,
    print_signer_status,
    print_token_status,
    setup_signer_flow,
    validate_team_name,
)
from platform_cli.core.access import (
    TOKEN_SCOPE_TEAM,
    ensure_capability,
    ensure_admin_principal,
    issue_token,
    resolve_access_context,
)
from platform_cli.core.clipboard import copy_text
from platform_cli.core.decorators import command_meta, requires_capability, requires_release_gate, tracked_command
from platform_cli.core.github_auth import (
    AUTH_MODE_MANAGED_LOCKED,
    AUTH_MODE_PERSONAL_ALLOWED,
    resolve_github_auth_mode,
    set_github_auth_mode,
)
from platform_cli.state.access_session import append_access_event, clear_assumed_team, get_assumed_team, set_assumed_team

app = typer.Typer(help="Admin-only GHDP access operations.", no_args_is_help=True)
token_app = typer.Typer(
    help="Create a temporary admin token. `ghdp admin token` is the primary mint flow.",
    invoke_without_command=True,
    no_args_is_help=False,
)
signer_app = typer.Typer(help="Inspect or provision admin-local signer material.", no_args_is_help=True)


def register(root_app: typer.Typer) -> None:
    app.add_typer(token_app, name="token")
    app.add_typer(signer_app, name="signer")
    root_app.add_typer(app, name="admin")


def _emit_token_result(
    *,
    scope: str,
    normalized_user: str,
    normalized_team: str,
    effective_ttl: int,
    requested_capabilities: list[str],
    token: str,
    show_token: bool,
) -> None:
    copied, clipboard_detail = copy_text(token)
    append_access_event(
        "admin.token_issued",
        {
            "scope": scope,
            "for_user": normalized_user,
            "team": normalized_team,
            "ttl_minutes": effective_ttl,
            "capabilities": sorted(set(requested_capabilities)),
            "clipboard": "copied" if copied else clipboard_detail,
        },
    )

    typer.echo(f"scope: {scope}")
    typer.echo(f"for_user: {normalized_user or '(not restricted)'}")
    typer.echo(f"ttl_minutes: {effective_ttl}")
    typer.echo(f"team: {normalized_team or '(not restricted)'}")
    if scope == TOKEN_SCOPE_TEAM:
        typer.echo("warning: team-only tokens can be reused by any user operating in that team until they expire.")
    typer.echo("capabilities:")
    for item in sorted(set(requested_capabilities)):
        typer.echo(f"  - {item}")
    if copied:
        typer.echo(f"clipboard: copied via {clipboard_detail}")
        if show_token:
            typer.echo(f"token: {token}")
        else:
            typer.echo("token: hidden (already copied to clipboard)")
    else:
        typer.echo(f"clipboard: unavailable ({clipboard_detail})")
        typer.echo(f"token: {token}")


def _mint_token(
    *,
    for_user: str,
    capability: list[str],
    ttl_minutes: int,
    team: str,
    show_token: bool,
    interactive_defaults: bool = True,
) -> None:
    scope, normalized_user, normalized_team, requested_capabilities, effective_ttl = collect_token_request_inputs(
        for_user=for_user,
        capability=capability,
        ttl_minutes=ttl_minutes,
        team=team,
        interactive_defaults=interactive_defaults,
    )
    token = issue_token(
        target_actor=normalized_user or None,
        capabilities=requested_capabilities,
        ttl_minutes=effective_ttl,
        team=normalized_team or None,
    )
    _emit_token_result(
        scope=scope,
        normalized_user=normalized_user,
        normalized_team=normalized_team,
        effective_ttl=effective_ttl,
        requested_capabilities=requested_capabilities,
        token=token,
        show_token=show_token,
    )


@app.command("assume")
@tracked_command("admin assume")
@requires_release_gate()
@command_meta(
    name="admin assume",
    category="admin",
    description="Temporarily operate as one team persona while suppressing admin-only privileges.",
    tags=["admin", "access", "team", "testing"],
)
def admin_assume(
    team: str = typer.Option(..., "--team", help="Team name to assume temporarily."),
) -> None:
    actor = ensure_admin_principal(command_name="admin assume")
    normalized_team = validate_team_name(team)
    current = get_assumed_team().strip()
    if current == normalized_team:
        typer.echo(f"Already assuming team: {normalized_team}")
        return

    set_assumed_team(normalized_team)
    append_access_event("admin.assume_team", {"actor": actor.login, "team": normalized_team})
    typer.echo(f"Admin assume mode enabled for: {normalized_team}")
    typer.echo("Admin-only capabilities are suppressed until you run 'ghdp admin return'.")
    print_context(resolve_access_context(), include_capabilities=False)


@app.command("assume-team", hidden=True)
@tracked_command("admin assume-team")
def admin_assume_team_compat(
    team: str = typer.Option(..., "--team", help="Team name to assume temporarily."),
) -> None:
    admin_assume(team=team)


@app.command("return")
@tracked_command("admin return")
@command_meta(
    name="admin return",
    category="admin",
    description="Exit assumed-team mode and restore full admin behavior.",
    tags=["admin", "access", "team"],
)
def admin_return() -> None:
    actor = ensure_admin_principal(command_name="admin return")
    previous = get_assumed_team().strip()
    if not previous:
        typer.echo("Admin mode is already active.")
        return

    clear_assumed_team()
    append_access_event("admin.return", {"actor": actor.login, "previous_team": previous})
    typer.echo("Admin mode restored.")
    print_context(resolve_access_context(), include_capabilities=False)


@app.command("auth-mode")
@tracked_command("admin auth-mode")
@command_meta(
    name="admin auth-mode",
    category="admin",
    description="Set managed-install GitHub auth mode with admin proof and audit trail.",
    tags=["admin", "github", "auth", "mode"],
)
def admin_auth_mode(
    mode: str = typer.Option(
        ...,
        "--mode",
        help=f"Managed mode: {AUTH_MODE_MANAGED_LOCKED} or {AUTH_MODE_PERSONAL_ALLOWED}.",
    ),
    reason: str = typer.Option(
        "manual-admin-change",
        "--reason",
        help="Reason recorded in the local mode-audit event.",
    ),
) -> None:
    actor = ensure_admin_principal(command_name="admin auth-mode")
    ensure_capability("admin.token.issue", team=None, command_name="admin auth-mode")
    before = resolve_github_auth_mode(managed_install=True)
    after = set_github_auth_mode(mode, actor=actor.login, reason=reason)
    append_access_event(
        "admin.github_auth_mode_changed",
        {
            "from": before.mode,
            "to": after.mode,
            "actor": actor.login,
            "timestamp": after.changed_at,
            "reason": reason,
            "source": after.source,
        },
    )
    typer.echo(f"auth_mode: {after.mode}")
    typer.echo(f"changed_at: {after.changed_at or '(not set)'}")
    typer.echo(f"changed_by: {after.changed_by or actor.login}")
    typer.echo(f"source: {after.source}")


@token_app.callback(invoke_without_command=True)
@tracked_command("admin token")
@command_meta(
    name="admin token",
    category="admin",
    description="Create a signed temporary admin token for a user, a team, or both.",
    tags=["admin", "access", "token"],
)
def admin_token_root(
    ctx: typer.Context,
    for_user: str = typer.Option("", "--for-user", help="Optional GitHub login that can use the token."),
    capability: list[str] = typer.Option([], "--capability", help="Capability to grant. Repeat for multiple values."),
    ttl_minutes: int = typer.Option(0, "--ttl-minutes", help="Token lifetime in minutes."),
    team: str = typer.Option("", "--team", help="Optional team restriction for the token."),
    show_token: bool = typer.Option(False, "--show-token", help="Print the raw token even if clipboard copy works."),
) -> None:
    if ctx.invoked_subcommand:
        return
    ensure_admin_principal(command_name="admin token")
    ensure_capability("admin.token.issue", team=None, command_name="admin token")
    _mint_token(
        for_user=for_user,
        capability=capability,
        ttl_minutes=ttl_minutes,
        team=team,
        show_token=show_token,
        interactive_defaults=True,
    )


@app.command("create-token", hidden=True)
@tracked_command("admin create-token")
@requires_capability("admin.token.issue", team_kwarg=None)
def admin_create_token_compat(
    for_user: str = typer.Option("", "--for-user", help="Optional GitHub login that can use the token."),
    capability: list[str] = typer.Option([], "--capability", help="Capability to grant. Repeat for multiple values."),
    ttl_minutes: int = typer.Option(0, "--ttl-minutes", help="Token lifetime in minutes."),
    team: str = typer.Option("", "--team", help="Optional team restriction for the token."),
    show_token: bool = typer.Option(False, "--show-token", help="Print the raw token even if clipboard copy works."),
) -> None:
    _mint_token(
        for_user=for_user,
        capability=capability,
        ttl_minutes=ttl_minutes,
        team=team,
        show_token=show_token,
        interactive_defaults=False,
    )


@signer_app.command("status")
@tracked_command("admin signer status")
@command_meta(
    name="admin signer status",
    category="admin",
    description="Show local admin signer presence and policy linkage.",
    tags=["admin", "signer", "token"],
)
@requires_capability("admin.token.issue", team_kwarg=None)
def admin_signer_status() -> None:
    print_signer_status()


@signer_app.command("setup")
@tracked_command("admin signer setup")
@command_meta(
    name="admin signer setup",
    category="admin",
    description="Create admin-local signer material and refresh the local access-policy verifier entry.",
    tags=["admin", "signer", "token"],
)
@requires_capability("admin.token.issue", team_kwarg=None)
def admin_signer_setup(
    key_id: str = typer.Option("", "--key-id", help="Signer key id. Defaults interactively when omitted."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace existing signer material."),
    update_local_policy: bool = typer.Option(
        True,
        "--update-local-policy/--no-update-local-policy",
        help="Refresh the local access policy override with the generated public verifier.",
    ),
) -> None:
    setup_signer_flow(key_id=key_id, overwrite=overwrite, update_local_policy=update_local_policy)


@app.command("view", hidden=True)
@tracked_command("admin view")
def admin_view_compat() -> None:
    print_access_view()


@token_app.command("activate", hidden=True)
@tracked_command("admin token activate")
def admin_token_activate_compat(
    token: str = typer.Option("", "--token", help="Access token. If omitted, GHDP prompts interactively."),
) -> None:
    activate_token_flow(token)


@token_app.command("status", hidden=True)
@tracked_command("admin token status")
def admin_token_status_compat() -> None:
    print_token_status()


@token_app.command("clear", hidden=True)
@tracked_command("admin token clear")
def admin_token_clear_compat() -> None:
    clear_active_token_flow()
