# NOTE: Architectural rules in ARCHITECTURE.md -- do not refactor cross-layer.
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer

from platform_cli.tools.terraform.tf_common import (
    build_plan_vars,
    build_runtime,
    confirm_or_fail,
    ensure_env_allowed,
    resolve_planfile,
    run_init_sequence,
    run_validate,
    top_plan_resources,
)
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.decorators import command_meta, feature_flag, requires_capability, requires_clean_git, tracked_command
from platform_cli.core.errors import PlatformError
from platform_cli.core.live_status import command_status
from platform_cli.tools.terraform import (
    enforce_guardrails,
    terraform_apply,
    terraform_plan,
    terraform_show_json,
    terraform_workspace_select_dev,
)


def register(app: typer.Typer) -> None:
    @app.command("tf-apply")
    @command_meta(
        name="tf-apply",
        category="terraform",
        description="Apply Terraform plan with mandatory plan inspection guardrails.",
        tags=["terraform", "apply"],
    )
    @feature_flag("features.terraform_local")
    @tracked_command("tf-apply")
    @requires_capability("local.lifecycle", team_kwarg=None)
    @requires_clean_git()
    def tf_apply(
        env: str = typer.Option("dev", "--env", "-e", help="Environment (local policy allows dev only)."),
        account: Optional[str] = typer.Option(None, "--account", help="Terraform var account (defaults to repo name)."),
        backend_account: Optional[str] = typer.Option(None, "--backend-account", help="Override account used to select backend bucket template."),
        terraform_component: Optional[str] = typer.Option(None, "--terraform-component", help="Optional component suffix for backend key/file naming."),
        commit_id: Optional[str] = typer.Option(None, "--commit-id", help="Terraform var commit_id (defaults to git SHA)."),
        planfile: Optional[str] = typer.Option(None, "--planfile", help="Existing plan file to apply. If omitted, plan is generated first."),
        tf_root: Optional[str] = typer.Option(None, "--tf-root", help="Terraform root path (default from policy)."),
        backend_config_file: Optional[str] = typer.Option(None, "--backend-config-file", help="Existing backend properties file path."),
        backend_bucket: Optional[str] = typer.Option(None, "--backend-bucket", help="Backend S3 bucket (when generating backend config)."),
        backend_key: Optional[str] = typer.Option(None, "--backend-key", help="Backend state key (when generating backend config)."),
        aws_profile: Optional[str] = typer.Option(None, "--aws-profile", help="AWS profile for terraform commands."),
        aws_region: Optional[str] = typer.Option(None, "--aws-region", help="AWS region override (defaults to policy region)."),
        aws_login: bool = typer.Option(False, "--aws-login", help="Attempt `aws sso login` when auth preflight fails."),
        refresh_deps: bool = typer.Option(False, "--refresh-deps", help="Delete and reclone policy dependencies when auto-planning."),
        yes: bool = typer.Option(False, "--yes", "--auto-approve", help="Skip confirmation prompt and auto-approve apply."),
    ) -> None:
        status = command_status("tf-apply")
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
            if planfile:
                plan_path = resolve_planfile(runtime.tf_root, env, planfile)
                if not plan_path.exists():
                    raise PlatformError(
                        f"Plan file not found: {plan_path}",
                        code="E_TF_PLANFILE_NOT_FOUND",
                        reason=str(plan_path),
                    )
                workspace = terraform_workspace_select_dev(runtime.tf_root, runtime.env_vars, stream=True)
                backend_key_used = "<external-planfile>"
            else:
                _, backend_key_used, workspace = run_init_sequence(
                    runtime,
                    env=env,
                    account=account,
                    backend_account=backend_account,
                    terraform_component=terraform_component,
                    backend_config_file=backend_config_file,
                    backend_bucket=backend_bucket,
                    backend_key=backend_key,
                    refresh_deps=refresh_deps,
                    stream_terraform=True,
                )
                run_validate(runtime, stream_terraform=True)

                plan_path = resolve_planfile(runtime.tf_root, env, None)
                plan_vars = build_plan_vars(
                    tf_root=runtime.tf_root,
                    env=env,
                    account=account,
                    commit_id=commit_id,
                    non_interactive=bool(cli_ctx.non_interactive),
                )
                terraform_plan(runtime.tf_root, plan_path, runtime.env_vars, plan_vars, stream=True)

            status.update("planning")
            plan_json = terraform_show_json(runtime.tf_root, plan_path, runtime.env_vars)
            summary = enforce_guardrails(plan_json, runtime.policy, env=env)

            status.finish()
            typer.echo(f"policy source:  {runtime.policy_source}")
            typer.echo(f"tf_root:        {runtime.tf_root}")
            typer.echo(f"backend key:    {backend_key_used}")
            typer.echo(f"workspace:      {workspace}")
            typer.echo(f"plan file:      {plan_path}")
            typer.echo(
                "plan summary:   "
                f"create={summary.creates} "
                f"update={summary.updates} "
                f"replace={summary.replacements} "
                f"delete={summary.deletes} "
                f"no-op={summary.no_ops} "
            )

            top = top_plan_resources(summary)
            if top:
                typer.echo(f"top resources:  {', '.join(top)}")

            if summary.replacements > 0:
                warn_sample = ", ".join(summary.replacement_resources[:5])
                typer.echo(f"WARNING:        replacement actions detected ({summary.replacements}). {warn_sample}")

            confirm_or_fail(yes)
            terraform_apply(runtime.tf_root, plan_path, runtime.env_vars, auto_approve=yes, stream=True)

            status.update("finalizing")
        finally:
            status.finish()
        typer.echo("status:         tf-apply completed")
