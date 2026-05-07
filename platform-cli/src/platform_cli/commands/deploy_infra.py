# NOTE: Architectural rules in ARCHITECTURE.md -- do not refactor cross-layer.
# src/platform_cli/commands/deploy_infra.py
"""
Command: ghdp deploy --env <env> [--stack <name>] [--commit-id <hash>] [--plan-only]

Deploy infrastructure stacks from infra.json using Terraform.
Validates that app builds exist for the target git hash before deploying.
Follows Jenkins pipeline flow: download deps -> init -> workspace -> validate -> plan -> apply.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer
from rich import print

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.decorators import command_meta, feature_flag, requires_capability, requires_clean_git, tracked_command
from platform_cli.core.errors import PlatformError
from platform_cli.core.live_status import command_status
from platform_cli.manifests.load import get_all_valid_environments, get_local_allowed_envs
from platform_cli.manifests.repo_discovery import discover_repo_structure
from platform_cli.tools.terraform.infra_deployer import (
    deploy_infra_stack,
    ensure_git_url_env,
    resolve_deploy_commit,
)


def register(app: typer.Typer) -> None:
    @app.command("deploy")
    @command_meta(
        name="deploy",
        category="data-product",
        description="Deploy infrastructure stacks from infra.json",
        tags=["data-product", "deploy", "infra", "terraform"],
    )
    @feature_flag("features.terraform_local")
    @tracked_command("deploy")
    @requires_capability("local.lifecycle", team_kwarg=None)
    @requires_clean_git()
    def deploy_cmd(
        env: str = typer.Option(
            ..., "--env", "-e", help="Environment to deploy (dev, qa, prod)"
        ),
        stack_name: Optional[str] = typer.Option(
            None, "--stack", "-s", help="Stack name/ID from infra.json (deploys all if not specified)"
        ),
        commit_id: Optional[str] = typer.Option(
            None, "--commit-id", "-c", help="Git commit hash to deploy (defaults to current HEAD)"
        ),
        plan_only: bool = typer.Option(
            False, "--plan-only", help="Only run terraform plan (do not apply)"
        ),
        refresh_deps: bool = typer.Option(
            False, "--refresh-deps", help="Delete and reclone terraform dependencies"
        ),
        target: Optional[List[str]] = typer.Option(
            None, "--target", "-t", help="Terraform target(s) to deploy (e.g., module.apps_upload). Can be repeated."
        ),
        yes: bool = typer.Option(
            False, "--yes", "--auto-approve", help="Skip confirmation and auto-approve apply"
        ),
    ) -> None:
        """
        Deploy infrastructure stacks from infra.json using Terraform.

        Before running Terraform, validates that all apps have been built
        for the target git commit. Use --commit-id to deploy a specific commit,
        or omit it to use the current HEAD.

        Follows Jenkins pipeline flow:
          1. Validate app builds exist for the target commit
          2. Download dependencies (infra-templates)
          3. terraform init with backend config
          4. terraform workspace select
          5. terraform validate
          6. terraform plan
          7. terraform apply (unless --plan-only)

        Default stack uses the same S3 state file as the legacy terraform/ folder.
        If --stack is not specified, deploys all stacks in infra.json.
        """
        status = command_status("deploy")
        try:
            status.update("validating")
            valid_envs = get_all_valid_environments()
            if env not in valid_envs:
                raise PlatformError(
                    f"Invalid environment: {env}",
                    code="E_INVALID_ENV",
                    reason=f"Must be one of: {', '.join(valid_envs)}",
                )

            # Local-only env restriction - Jenkins can deploy to any environment.
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
                    "Target structure not found. Run in repo with infra/",
                    code="E_STRUCTURE_INVALID",
                    reason="No infra.json found",
                )

            if not repo.infra_stacks:
                raise PlatformError(
                    "No infrastructure stacks found in infra.json",
                    code="E_NO_STACKS",
                    reason="infra.json has no stacks",
                )

            if not repo.infra_templates_version:
                raise PlatformError(
                    "Missing 'infra_templates_version' in infra.json",
                    code="E_MISSING_INFRA_TEMPLATES_VERSION",
                    reason="infra.json must specify infra_templates_version",
                )

            ensure_git_url_env(repo_root)
            target_hash = resolve_deploy_commit(repo_root, commit_id)

            if repo.apps:
                from platform_cli.tools.app.deploy_validator import validate_builds_for_hash

                build_info = validate_builds_for_hash(repo.apps, target_hash, repo_root)
                wheel_count = sum(1 for info in build_info.values() if info.get("wheel"))
                jar_count = sum(1 for info in build_info.values() if info.get("jar"))
            else:
                build_info = {}
                wheel_count = 0
                jar_count = 0

            deploy_context = {
                "verbose": cli_ctx.verbose,
                "quiet": cli_ctx.quiet,
                "refresh_deps": refresh_deps,
                "auto_approve": yes,
                "targets": target or [],
            }

            if stack_name:
                stack = repo.get_infra_stack(stack_name)
                if not stack:
                    available = [s.id for s in repo.infra_stacks]
                    raise PlatformError(
                        f"Stack '{stack_name}' not found in infra.json",
                        code="E_STACK_NOT_FOUND",
                        reason=f"Available stacks: {', '.join(available)}",
                    )
                stacks_to_deploy = [stack]
            else:
                stacks_to_deploy = sorted(repo.infra_stacks, key=lambda s: s.deployment_order)

            status.finish()
            if commit_id:
                print(f"Deploying commit: {commit_id} (resolved: {target_hash})")
            else:
                print(f"Deploying current HEAD: {target_hash}")

            if build_info:
                print(
                    f"  Validated {len(build_info)} app(s) for hash {target_hash} "
                    f"({wheel_count} wheel(s), {jar_count} JAR(s))"
                )

            if len(stacks_to_deploy) > 1:
                order_info = ", ".join(f"{s.id}({s.deployment_order})" for s in stacks_to_deploy)
                print(f"Deploying all stacks ({len(stacks_to_deploy)}) to {env} in order: {order_info}")

            action = "Planning" if plan_only else "Deploying"
            failed_stacks = []
            for stack in stacks_to_deploy:
                try:
                    print(f"{action} stack '{stack.id}' to {env}...")
                    result = deploy_infra_stack(
                        stack=stack,
                        env=env,
                        plan_only=plan_only,
                        context=deploy_context,
                        repo_root=repo_root,
                        infra_templates_version=repo.infra_templates_version,
                    )
                    result_status = result["status"]
                    print(f"  Stack '{stack.id}': {result_status}")
                    print(f"  Plan file: {result['plan_file']}")
                except Exception as exc:
                    failed_stacks.append((stack.id, str(exc)))
                    print(f"  Failed stack '{stack.id}': {exc}")

            if len(stacks_to_deploy) > 1:
                status.update("finalizing")
                status.finish()
                success_count = len(stacks_to_deploy) - len(failed_stacks)
                print(f"\nDeploy summary: {success_count}/{len(stacks_to_deploy)} successful")

            if failed_stacks:
                if len(stacks_to_deploy) > 1:
                    print("\nFailed stacks:")
                    for stack_id, error in failed_stacks:
                        print(f"  - {stack_id}: {error}")
                raise PlatformError(
                    f"{len(failed_stacks)} stack(s) failed to deploy",
                    code="E_DEPLOY_FAILED",
                    reason="See errors above",
                )
        finally:
            status.finish()
