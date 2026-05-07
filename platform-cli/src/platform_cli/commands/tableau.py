# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
"""Tableau helper commands."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.decorators import command_meta, requires_capability, tracked_command
from platform_cli.tools.aws_profile import prompt_aws_profile_choice, resolve_aws_profile, set_active_profile
from platform_cli.tools.aws_sso import DEFAULTS
from platform_cli.tools.tableau import (
    ensure_initialized_for_login,
    init as tableau_init,
    refresh_credentials_for_tableau,
)

app = typer.Typer(help="Tableau helper commands", no_args_is_help=True)

def register(root_app: typer.Typer) -> None:
    root_app.add_typer(app, name="tableau")


    @app.command("init")
    @tracked_command("tableau init")
    @requires_capability("tableau.use", team_kwarg=None)
    @command_meta(
        name="tableau init",
        category="tableau",
        description="Initialize Tableau Athena setup (drivers + Mac properties).",
        tags=["tableau", "init", "aws"],
    )
    def init(
        download_dir: Path | None = typer.Option(
            None,
            "--download-dir",
            help="Directory containing Athena JDBC jar files (optional local override).",
        ),
        drivers_dir: Path | None = typer.Option(
            None,
            "--drivers-dir",
            help="Override Tableau Drivers directory.",
        ),
        dry_run: bool = typer.Option(
            False,
            "--dry-run",
            help="Show planned actions without writing files.",
        ),
        force: bool = typer.Option(
            False,
            "--force",
            help="Overwrite existing jar files.",
        ),
    ) -> None:
        """Initialize Tableau Athena setup (Confluence Step 1 + Step 2)."""
        result = tableau_init(
            download_dir=download_dir,
            drivers_dir=drivers_dir,
            dry_run=dry_run,
            force=force,
        )
        for line in result["messages"]:
            typer.echo(line)

    @app.command("login")
    @tracked_command("tableau login")
    @requires_capability("tableau.use", team_kwarg=None)
    @command_meta(
        name="tableau login",
        category="tableau",
        description="Run Tableau login.",
        tags=["tableau", "login", "aws"],
    )
    def login(
        profile: Optional[str] = typer.Option(
            None,
            "--profile",
            help="AWS SSO profile name. If omitted, GHDP will let you pick or enter one.",
        ),
    ) -> None:
        """Authenticate for Tableau workflows."""
        if profile:
            selected_profile = profile
        elif bool(cli_ctx.non_interactive):
            resolved = resolve_aws_profile(explicit_profile=None, prompt_if_unresolved=False)
            selected_profile = resolved.profile
        else:
            selected_profile = prompt_aws_profile_choice(default_profile=DEFAULTS.profile_name)
            set_active_profile(selected_profile, scope="global")

        # Pre-hook: run one-time Tableau init if not yet initialized.
        pre = ensure_initialized_for_login()
        for line in pre.get("messages", []):
            typer.echo(line)

        # Then refresh AWS session and sync temporary credentials for Tableau.
        result = refresh_credentials_for_tableau(profile=selected_profile)
        for line in result.get("messages", []):
            typer.echo(line)
