# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
# src/platform_cli/commands/build_app.py
"""
Command: ghdp build [--app <name>]

Build application artifacts using language-native tooling (uv for Python, maven for Scala).
Version is determined by git branch: main/master → release, other branches → snapshot.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich import print

from platform_cli.core.decorators import tracked_command, command_meta, requires_capability
from platform_cli.core.errors import PlatformError
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.manifests.repo_discovery import discover_repo_structure
from platform_cli.tools.app.builder import build_app


def register(app: typer.Typer) -> None:
    @app.command("build")
    @command_meta(
        name="build",
        category="data-product",
        description="Build application artifacts using language-native tooling",
        tags=["data-product", "build", "apps", "python", "scala", "docker"],
    )
    @tracked_command("build")
    @requires_capability("local.lifecycle", team_kwarg=None)
    def build_app_cmd(
        app_name: str = typer.Option(
            None, "--app", "-a", help="Application path from apps.json (builds all if not specified)"
        ),
    ) -> None:
        """
        Build application artifact using language-native tooling.

        Discovers app configuration from apps.json and builds using the
        appropriate builder based on app type (python -> uv, scala -> maven).
        If the app lists 'docker' in its tools, a Docker image is also built.

        Version is determined by git branch:
        - main/master → release version (from pyproject.toml/pom.xml)
        - other branches → snapshot version (auto-incremented with dev suffix)

        If --app is not specified, builds all apps in apps.json.
        """
        # Discover structure (delegate to manifest layer)
        repo_root = Path.cwd()
        repo = discover_repo_structure(repo_root)

        if not repo:
            raise PlatformError(
                "Target structure not found. Run in repo with apps/ and infra/",
                code="E_STRUCTURE_INVALID",
                reason="No apps.json or infra.json found",
            )

        # Build context
        build_context = {
            "verbose": cli_ctx.verbose,
            "quiet": cli_ctx.quiet,
        }

        # Determine which apps to build
        if app_name:
            # Build specific app (lookup by path)
            app_config = repo.get_app(app_name)
            if not app_config:
                available_apps = [a.path for a in repo.apps]
                raise PlatformError(
                    f"App '{app_name}' not found in apps.json",
                    code="E_APP_NOT_FOUND",
                    reason=f"Available apps: {', '.join(available_apps)}",
                )
            apps_to_build = [app_config]
        else:
            # Build all apps
            apps_to_build = repo.apps
            print(f"Building all apps ({len(apps_to_build)})...")

        # Build each app
        failed_apps = []
        for app_config in apps_to_build:
            try:
                print(f"  Building {app_config.path} ({app_config.type})...")
                result = build_app(
                    app=app_config,
                    context=build_context,
                    repo_root=repo_root,
                )
                print(f"  Built {app_config.path}: {result.artifact_path}")
                if result.docker_image:
                    print(f"  Docker image: {result.docker_image}")
            except Exception as e:
                failed_apps.append((app_config.path, str(e)))
                print(f"  [red]Failed to build {app_config.path}: {e}[/red]")

        # Report summary
        if len(apps_to_build) > 1:
            success_count = len(apps_to_build) - len(failed_apps)
            print(f"\nBuild summary: {success_count}/{len(apps_to_build)} successful")
            if failed_apps:
                print("\n[red]Failed apps:[/red]")
                for app_name, error in failed_apps:
                    print(f"  • {app_name}: {error}")
                raise PlatformError(
                    f"{len(failed_apps)} app(s) failed to build",
                    code="E_BUILD_FAILED",
                    reason="See errors above",
                )
