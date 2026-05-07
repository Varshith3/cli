from __future__ import annotations

import typer

from platform_cli.commands._access_common import (
    activate_token_flow,
    clear_active_token_flow,
    clear_session_flow,
    print_access_view,
    print_session_view,
    print_token_status,
)
from platform_cli.core.decorators import command_meta, requires_capability, requires_release_gate, tracked_command
from platform_cli.core.github_auth import inspect_github_auth

app = typer.Typer(help="Inspect or manage GHDP access state.", no_args_is_help=True)
token_app = typer.Typer(
    help="Activate a temporary access token. `ghdp access token` is the primary activation command.",
    invoke_without_command=True,
    no_args_is_help=False,
)


def register(root_app: typer.Typer) -> None:
    app.add_typer(token_app, name="token")
    root_app.add_typer(app, name="access")


@app.command("status")
@tracked_command("access status")
@command_meta(
    name="access status",
    category="access",
    description="Show current actor identity, active mode, team context, token state, and release channel.",
    tags=["access", "identity", "token", "release"],
)
def access_status() -> None:
    print_access_view()


@app.command("auth-mode")
@tracked_command("access auth-mode")
@command_meta(
    name="access auth-mode",
    category="access",
    description="Show install flavor, managed-auth mode, and effective GitHub auth source.",
    tags=["access", "github", "auth", "mode"],
)
def access_auth_mode() -> None:
    state = inspect_github_auth()
    typer.echo(f"install_flavor: {state.install_flavor}")
    typer.echo(f"auth_mode: {state.auth_mode}")
    typer.echo(f"managed_auth: {state.managed_auth_status}")
    typer.echo(f"github_auth_source: {state.effective_github_auth_source}")


@app.command("inspect")
@tracked_command("access inspect")
@requires_capability("platform.internal", team_kwarg=None)
@requires_release_gate(command_name="access inspect", allow_admin_bypass=False, team_kwarg=None)
@command_meta(
    name="access inspect",
    category="access",
    description="Inspect local access-session state and resolved access context for support/debugging.",
    tags=["access", "session", "support"],
)
def access_inspect() -> None:
    print_session_view()


@app.command("reset")
@tracked_command("access reset")
@requires_capability("platform.internal", team_kwarg=None)
@requires_release_gate(command_name="access reset", allow_admin_bypass=False, team_kwarg=None)
@command_meta(
    name="access reset",
    category="access",
    description="Clear remembered actor, active token, and assumed-team state.",
    tags=["access", "session", "support"],
)
def access_reset() -> None:
    clear_session_flow()


@app.command("clear")
@tracked_command("access clear")
@command_meta(
    name="access clear",
    category="access",
    description="Clear the locally stored access token.",
    tags=["access", "token"],
)
def access_clear() -> None:
    clear_active_token_flow()


@token_app.callback(invoke_without_command=True)
@tracked_command("access token")
@command_meta(
    name="access token",
    category="access",
    description="Prompt for and activate a temporary access token locally.",
    tags=["access", "token"],
)
def access_token_root(
    ctx: typer.Context,
    token: str = typer.Option("", "--token", help="Access token. If omitted, GHDP prompts interactively."),
) -> None:
    if ctx.invoked_subcommand:
        return
    activate_token_flow(token)


@token_app.command("activate", hidden=True)
@tracked_command("access token activate")
def access_token_activate_compat(
    token: str = typer.Option("", "--token", help="Access token. If omitted, GHDP prompts interactively."),
) -> None:
    activate_token_flow(token)


@token_app.command("status", hidden=True)
@tracked_command("access token status")
def access_token_status_compat() -> None:
    print_token_status()


@token_app.command("clear", hidden=True)
@tracked_command("access token clear")
def access_token_clear_compat() -> None:
    clear_active_token_flow()


@app.command("view", hidden=True)
@tracked_command("access view")
def access_view_compat() -> None:
    print_access_view()


@app.command("session", hidden=True)
@tracked_command("access session")
def access_session_compat() -> None:
    access_inspect()
