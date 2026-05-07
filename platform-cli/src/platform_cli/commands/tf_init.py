# NOTE: Architectural rules in ARCHITECTURE.md -- do not refactor cross-layer.
# src/platform_cli/commands/tf_init.py
from __future__ import annotations

from typing import Optional

import typer

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.decorators import command_meta, feature_flag, requires_capability, requires_clean_git, tracked_command
from platform_cli.core.live_status import command_status
from platform_cli.tools.terraform.tf_common import build_runtime, ensure_env_allowed, run_init_sequence


def register(app: typer.Typer) -> None:
        @app.command("tf-init")
        @command_meta(
            name="tf-init",
            category="terraform",
            description="Initialize Terraform backend/workspace locally (Phase 1).",
            tags=["terraform", "init"],
        )
        @feature_flag("features.terraform_local")
        @tracked_command("tf-init")
        @requires_capability("local.lifecycle", team_kwarg=None)
        @requires_clean_git()
        def tf_init(
            env: str = typer.Option("dev", "--env", "-e", help="Environment (local policy allows dev only)."),
            account: Optional[str] = typer.Option(None, "--account", help="Backend/account hint for Jenkins-style backend config generation."),
            backend_account: Optional[str] = typer.Option(None, "--backend-account", help="Override account used to select backend bucket template."),
            terraform_component: Optional[str] = typer.Option(None, "--terraform-component", help="Optional component suffix for backend key/file naming."),
            tf_root: Optional[str] = typer.Option(None, "--tf-root", help="Terraform root path (default from policy)."),
            backend_config_file: Optional[str] = typer.Option(None, "--backend-config-file", help="Existing backend properties file path."),
            backend_bucket: Optional[str] = typer.Option(None, "--backend-bucket", help="Backend S3 bucket (when generating backend config)."),
            backend_key: Optional[str] = typer.Option(None, "--backend-key", help="Backend state key (when generating backend config)."),
            aws_profile: Optional[str] = typer.Option(None, "--aws-profile", help="AWS profile for terraform commands."),
            aws_region: Optional[str] = typer.Option(None, "--aws-region", help="AWS region override (defaults to policy region)."),
            aws_login: bool = typer.Option(False, "--aws-login", help="Attempt `aws sso login` when auth preflight fails."),
            refresh_deps: bool = typer.Option(False, "--refresh-deps", help="Delete and reclone policy dependencies."),
        ) -> None:
            status = command_status("tf-init")
            try:
                status.update("validating")
                runtime = build_runtime(
                    tf_root_override=tf_root,
                    aws_profile=aws_profile,
                    aws_region_override=aws_region,
                    aws_login=aws_login,
                    non_interactive=bool(cli_ctx.non_interactive),
                )
                ensure_env_allowed(runtime.policy, env)

                status.finish()
                backend_cfg, backend_key_used, workspace = run_init_sequence(
                    runtime,
                    env=env,
                    account=account,
                    backend_account=backend_account,
                    terraform_component=terraform_component,
                    backend_config_file=backend_config_file,
                    backend_bucket=backend_bucket,
                    backend_key=backend_key,
                    refresh_deps=refresh_deps,
                    rich_logs=True,
                )

                status.update("finalizing")
            finally:
                status.finish()

            typer.echo(f"policy source:  {runtime.policy_source}")
            typer.echo(f"tf_root:        {runtime.tf_root}")
            typer.echo(f"backend config: {backend_cfg}")
            typer.echo(f"backend key:    {backend_key_used or '<unknown>'}")
            typer.echo(f"workspace:      {workspace}")
            typer.echo(f"env:            {env}")
            typer.echo("status:         tf-init completed")
