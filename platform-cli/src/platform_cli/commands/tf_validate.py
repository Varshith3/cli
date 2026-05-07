# NOTE: Architectural rules in ARCHITECTURE.md -- do not refactor cross-layer.
from __future__ import annotations

from typing import Optional

import typer

from platform_cli.tools.terraform.tf_common import build_runtime_without_auth, run_validate
from platform_cli.core.decorators import command_meta, feature_flag, requires_capability, requires_clean_git, tracked_command
from platform_cli.core.output import print_header


def register(app: typer.Typer) -> None:
    @app.command("tf-validate")
    @command_meta(
        name="tf-validate",
        category="terraform",
        description="Run terraform validate in the resolved terraform root.",
        tags=["terraform", "validate"],
    )
    @feature_flag("features.terraform_local")
    @tracked_command("tf-validate")
    @requires_capability("local.lifecycle", team_kwarg=None)
    @requires_clean_git()
    def tf_validate(
        tf_root: Optional[str] = typer.Option(None, "--tf-root", help="Terraform root path (default from policy)."),
    ) -> None:

        runtime = build_runtime_without_auth(tf_root_override=tf_root)
        run_validate(runtime)

        typer.echo(f"policy source:  {runtime.policy_source}")
        typer.echo(f"tf_root:        {runtime.tf_root}")
        typer.echo("status:         tf-validate completed")
