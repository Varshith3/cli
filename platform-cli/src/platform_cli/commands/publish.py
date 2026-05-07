# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
# src/platform_cli/commands/publish.py
"""
Command: ghdp publish [--env <env>] [--app <name>]

Publish application artifacts to CodeArtifact (Python packages) and ECR (Docker images).
CodeArtifact repo selection is branch-based (main → release, other → snapshot).
The --env flag is optional and only affects Docker's {component}-{env} tag.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich import print

from platform_cli.core.decorators import tracked_command, command_meta, requires_capability
from platform_cli.core.errors import PlatformError
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.manifests.load import get_all_valid_environments, get_local_allowed_envs
from platform_cli.tools.app.codeartifact_publisher import publish_app
from platform_cli.manifests.repo_discovery import discover_repo_structure


def register(app: typer.Typer) -> None:
    @app.command("publish")
    @command_meta(
        name="publish",
        category="data-product",
        description="Publish app artifacts to CodeArtifact and/or ECR",
        tags=["data-product", "publish", "apps", "codeartifact", "ecr", "docker"],
    )
    @tracked_command("publish")
    @requires_capability("publish.execute", team_kwarg=None)
    def publish_cmd(
        env: Optional[str] = typer.Option(
            None, "--env", "-e", help="Environment for Docker env tag (optional)"
        ),
        app_name: str = typer.Option(
            None, "--app", "-a", help="Application path from apps.json (publishes all if not specified)"
        ),
    ) -> None:
        """
        Publish application artifacts to CodeArtifact and/or ECR.

        CodeArtifact repo is selected by git branch (main → release, other → snapshot).
        For Python apps: publishes wheel to CodeArtifact.
        If the app lists 'docker' in its tools: also pushes Docker image to ECR.

        The --env flag is optional. When provided, an additional Docker tag
        {component}-{env} is pushed. Without --env, only version and latest tags are pushed.

        If --app is not specified, publishes all apps in apps.json.
        """
        # Validate env if provided
        if env is not None:
            valid_envs = get_all_valid_environments()
            if env not in valid_envs:
                raise PlatformError(
                    f"Invalid environment: {env}",
                    code="E_INVALID_ENV",
                    reason=f"Must be one of: {', '.join(valid_envs)}",
                )

            # Local-only env restriction — Jenkins can publish to any environment
            from platform_cli.tools.ci_environment import is_jenkins_pipeline
            if not is_jenkins_pipeline():
                local_envs = get_local_allowed_envs()
                if local_envs and env not in local_envs:
                    raise PlatformError(
                        f"Environment '{env}' is not allowed for local operations",
                        code="E_ENV_NOT_ALLOWED_LOCAL",
                        reason=f"Local allowed envs: {', '.join(local_envs)}. Other envs must be deployed via CI/CD.",
                    )

        repo_root = Path.cwd()
        repo = discover_repo_structure(repo_root)

        if not repo:
            raise PlatformError(
                "Target structure not found. Run in repo with apps/ and infra/",
                code="E_STRUCTURE_INVALID",
                reason="No apps.json or infra.json found",
            )

        publish_context = {
            "verbose": cli_ctx.verbose,
            "quiet": cli_ctx.quiet,
        }

        # Determine which apps to publish
        if app_name:
            # Publish specific app
            app_config = repo.get_app(app_name)
            if not app_config:
                available_apps = [a.path for a in repo.apps]
                raise PlatformError(
                    f"App '{app_name}' not found in apps.json",
                    code="E_APP_NOT_FOUND",
                    reason=f"Available apps: {', '.join(available_apps)}",
                )
            apps_to_publish = [app_config]
        else:
            # Publish all apps
            apps_to_publish = repo.apps
            print(f"Publishing all apps ({len(apps_to_publish)})...")

        # Publish each app
        failed_apps = []
        for app_config in apps_to_publish:
            try:
                print(f"  Publishing {app_config.path} ({app_config.type})...")
                result = publish_app(
                    app=app_config,
                    context=publish_context,
                    repo_root=repo_root,
                    env=env,
                )
                if result.get("codeartifact_uri"):
                    print(f"  Published to CodeArtifact: {result['codeartifact_uri']}")
                if result.get("ecr_uri"):
                    print(f"  Pushed to ECR: {result['ecr_uri']}")
            except Exception as e:
                failed_apps.append((app_config.path, str(e)))
                print(f"  [red]Failed to publish {app_config.path}: {e}[/red]")

        # Report summary
        if len(apps_to_publish) > 1:
            success_count = len(apps_to_publish) - len(failed_apps)
            print(f"\nPublish summary: {success_count}/{len(apps_to_publish)} successful")
            if failed_apps:
                print("\n[red]Failed apps:[/red]")
                for app_name, error in failed_apps:
                    print(f"  • {app_name}: {error}")
                raise PlatformError(
                    f"{len(failed_apps)} app(s) failed to publish",
                    code="E_PUBLISH_FAILED",
                    reason="See errors above",
                )
