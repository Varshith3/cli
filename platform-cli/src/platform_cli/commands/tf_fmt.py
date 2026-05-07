# NOTE: Architectural rules in ARCHITECTURE.md -- do not refactor cross-layer.
from __future__ import annotations

from typing import Optional

import typer

from platform_cli.tools.terraform.tf_common import build_runtime_without_auth
from platform_cli.core.decorators import command_meta, feature_flag, requires_capability, requires_clean_git, tracked_command
from platform_cli.core.output import print_header
from platform_cli.tools.terraform import terraform_fmt


def register(app: typer.Typer) -> None:
    @app.command("tf-fmt")
    @command_meta(
        name="tf-fmt",
        category="terraform",
        description="Run terraform fmt for local terraform root.",
        tags=["terraform", "fmt"],
    )
    @feature_flag("features.terraform_local")
    @tracked_command("tf-fmt")
    @requires_capability("local.lifecycle", team_kwarg=None)
    @requires_clean_git()
    def tf_fmt(
        tf_root: Optional[str] = typer.Option(None, "--tf-root", help="Terraform root path (default from policy)."),
        recursive: bool = typer.Option(True, "--recursive/--no-recursive", help="Use terraform fmt -recursive."),
    ) -> None:

        runtime = build_runtime_without_auth(tf_root_override=tf_root)
        terraform_fmt(runtime.tf_root, runtime.env_vars, recursive=recursive)

        typer.echo(f"policy source:  {runtime.policy_source}")
        typer.echo(f"tf_root:        {runtime.tf_root}")
        typer.echo("status:         tf-fmt completed")
