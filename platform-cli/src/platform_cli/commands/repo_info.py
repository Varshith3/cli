# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
# src/platform_cli/commands/repo_info.py
"""
Command: ghdp repo info

Display data-product repository structure information (apps + infra stacks).
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich import print

from platform_cli.core.decorators import tracked_command, command_meta, requires_capability, requires_release_gate
from platform_cli.core.errors import PlatformError
from platform_cli.manifests.repo_discovery import discover_repo_structure
from platform_cli.tools.repo_info.formatter import format_repo_info


def register(app: typer.Typer) -> None:
    @app.command("repo-info")
    @command_meta(
        name="repo-info",
        category="data-product",
        description="Display repository structure information (apps + infra stacks)",
        tags=["data-product", "repo", "info"],
    )
    @tracked_command("repo-info")
    @requires_capability("platform.internal", team_kwarg=None)
    @requires_release_gate(command_name="repo-info", allow_admin_bypass=False, team_kwarg=None)
    def repo_info(
        format: str = typer.Option(
            "text",
            "--format",
            help="Output format (text or json)"
        ),
    ) -> None:
        """
        Display data-product repository structure information.
        
        Shows apps and infrastructure stacks from apps.json and infra.json.
        """
        # Discover structure (delegate to manifest layer)
        repo_root = Path.cwd()
        repo = discover_repo_structure(repo_root)
        
        if not repo:
            raise PlatformError(
                "Target structure not found. Repository uses legacy structure (code/, terraform/). "
                "To migrate: create apps/ with apps.json and infra/ with infra.json.",
                code="E_STRUCTURE_INVALID",
                reason="No apps.json or infra.json found",
            )
        
        # Format and display (delegate to tools layer)
        output = format_repo_info(repo, format=format)
        print(output)
