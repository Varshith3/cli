# NOTE: Architectural rules in ARCHITECTURE.md -- do not refactor cross-layer.
# src/platform_cli/commands/tf_set_workspace.py
from __future__ import annotations

from typing import Optional
import typer
from platform_cli.tools.terraform.tf_common import build_runtime
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.decorators import command_meta, feature_flag, requires_capability, requires_clean_git, tracked_command

def register(app: typer.Typer) -> None:
    @app.command("tf-set-workspace")
    @command_meta(
        name="tf-set-workspace",
        category="terraform",
        description="Select or create a Terraform workspace.",
        tags=["terraform", "workspace"],
    )
    @feature_flag("features.terraform_local")
    @tracked_command("tf-set-workspace")
    @requires_capability("local.lifecycle", team_kwarg=None)
    @requires_clean_git()
    def tf_set_workspace(
        workspace_name: str = typer.Argument(..., help="Workspace name to select or create."),
        tf_root: Optional[str] = typer.Option(None, "--tf-root", help="Terraform root path (default from policy)."),
        aws_profile: Optional[str] = typer.Option(None, "--aws-profile", help="AWS profile for terraform commands."),
        aws_region: Optional[str] = typer.Option(None, "--aws-region", help="AWS region override (defaults to policy region)."),
        aws_login: bool = typer.Option(False, "--aws-login", help="Attempt `aws sso login` when auth preflight fails."),
    ) -> None:
        runtime = build_runtime(
            tf_root_override=tf_root,
            aws_profile=aws_profile,
            aws_region_override=aws_region,
            aws_login=aws_login,
            non_interactive=bool(cli_ctx.non_interactive),
        )
        from platform_cli.tools.terraform.tf_common import terraform_workspace_select
        terraform_workspace_select(runtime.tf_root, runtime.env_vars, workspace_name)
