# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

from typing import List, Optional

import typer

from platform_cli.core.decorators import command_meta, tracked_command
from platform_cli.tools.claude_passthrough import run_claude_launch, run_claude_passthrough


def register(app: typer.Typer) -> None:
    @app.command(
        "claude",
        context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    )
    @tracked_command("claude")
    @command_meta(
        name="claude",
        category="tools",
        description="Pass through commands to the Claude Code CLI.",
        tags=["claude", "passthrough"],
    )
    def claude_passthrough(ctx: typer.Context) -> None:
        """
        Run Claude CLI through GHDP.
        Example: ghdp claude
        """
        args: List[str] = list(ctx.args or [])
        exit_code = run_claude_passthrough(args)
        raise typer.Exit(exit_code)

    @app.command(
        "claude-launch",
        context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    )
    @tracked_command("claude-launch")
    @command_meta(
        name="claude-launch",
        category="tools",
        description="Launch Claude Code with a session-only AWS profile selection step.",
        tags=["claude", "launch", "aws", "profile"],
    )
    def claude_launch(
        ctx: typer.Context,
        profile: Optional[str] = typer.Option(None, "--profile", help="AWS profile override for this Claude launch only."),
        choose_profile: bool = typer.Option(
            False,
            "--choose-profile",
            help="Prompt to pick a different AWS profile for this Claude launch.",
        ),
    ) -> None:
        """
        Launch Claude through GHDP with AWS profile resolution for this session.
        Example: ghdp claude-launch -- --help
        """
        args: List[str] = list(ctx.args or [])
        exit_code = run_claude_launch(
            args,
            explicit_profile=profile,
            choose_profile=choose_profile,
        )
        raise typer.Exit(exit_code)
