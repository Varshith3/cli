# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
# src/platform_cli/commands/init_workspace.py
"""
Command: ghdp init [--app <name>]

Initialize local workspace dependencies for data-product repositories:
- app dependencies (uv sync for Python, maven dependency prefetch for Scala)
- infra dependencies (.dependencies refresh for each stack in infra.json)
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich import print

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.decorators import command_meta, requires_capability, tracked_command
from platform_cli.core.errors import PlatformError
from platform_cli.manifests.repo_discovery import discover_repo_structure
from platform_cli.tools.workspace_initializer import init_workspace


def register(app: typer.Typer) -> None:
    @app.command("init")
    @command_meta(
        name="init",
        category="data-product",
        description="Initialize local app/infra dependencies for a data-product repo",
        tags=["data-product", "init", "apps", "infra", "uv", "maven", "terraform"],
    )
    @tracked_command("init")
    @requires_capability("local.lifecycle", team_kwarg=None)
    def init_cmd(
        app_name: str = typer.Option(
            None,
            "--app",
            "--app-name",
            "-a",
            help="Application path from apps.json (initializes all apps when omitted)",
        ),
    ) -> None:
        repo_root = Path.cwd()
        repo = discover_repo_structure(repo_root)
        if not repo:
            raise PlatformError(
                "Target structure not found. Run in repo with apps/ and/or infra/",
                code="E_STRUCTURE_INVALID",
                reason="No apps.json or infra.json found",
            )

        init_context = {
            "verbose": cli_ctx.verbose,
            "quiet": cli_ctx.quiet,
        }

        result = init_workspace(
            repo=repo,
            repo_root=repo_root,
            app_name=app_name,
            context=init_context,
        )

        if result.initialized_apps:
            print(f"Initialized app dependencies ({len(result.initialized_apps)}): {', '.join(result.initialized_apps)}")
        else:
            print("Initialized app dependencies: none")

        if result.refreshed_stacks:
            print(f"Refreshed infra dependencies ({len(result.refreshed_stacks)}): {', '.join(result.refreshed_stacks)}")
        else:
            print("Refreshed infra dependencies: none")
