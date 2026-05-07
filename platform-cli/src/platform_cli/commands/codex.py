# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

from typing import List

import typer

from platform_cli.core.decorators import command_meta, tracked_command
from platform_cli.tools.codex_passthrough import run_codex_passthrough


def register(app: typer.Typer) -> None:
    @app.command(
        "codex",
        context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    )
    @tracked_command("codex")
    @command_meta(
        name="codex",
        category="tools",
        description="Pass through commands to the Codex CLI.",
        tags=["codex", "passthrough"],
    )
    def codex_passthrough(ctx: typer.Context) -> None:
        """
        Run Codex CLI through GHDP.
        Example: ghdp codex login status
        """
        args: List[str] = list(ctx.args or [])
        exit_code = run_codex_passthrough(args)
        raise typer.Exit(exit_code)
