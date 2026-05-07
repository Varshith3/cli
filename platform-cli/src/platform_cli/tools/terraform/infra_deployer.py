"""Deploy infrastructure stacks from infra.json using Terraform."""
# NOTE: Architectural rules in ARCHITECTURE.md – do not refactor cross-layer.
# Follows Jenkins pipeline flow: downloadDependencies -> initWorkspace -> validateAndPlan -> terraformApply

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from platform_cli.core.errors import PlatformError  # type: ignore
except Exception:  # pragma: no cover

    class PlatformError(RuntimeError):
        def __init__(
            self,
            message: str,
            code: str = "E_INTERNAL",
            reason: str = "UNKNOWN",
            alert: bool = False,
        ):
            super().__init__(message)
            self.code = code
            self.reason = reason
            self.alert = alert


from platform_cli.exec.runner import run_cmd
from platform_cli.tools.git_repo import get_short_commit_hash, get_repo_name
from platform_cli.tools.terraform.terraform_runner import (
    ensure_backend_config,
    ensure_deps,
)


def ensure_git_url_env(repo_root: Path) -> None:
    """Set GIT_URL env var from git remote if not already set.

    Terraform modules use data.external.repo_name which reads $GIT_URL for tagging.
    """
    if not os.environ.get("GIT_URL"):
        _git_remote = run_cmd(
            ["git", "config", "--get", "remote.origin.url"],
            check=False, capture=True, cwd=str(repo_root),
        )
        if _git_remote.returncode == 0 and _git_remote.stdout.strip():
            os.environ["GIT_URL"] = _git_remote.stdout.strip()


def resolve_deploy_commit(
    repo_root: Path, commit_id: Optional[str] = None
) -> str:
    """Resolve git commit hash for deployment.

    If commit_id is provided, resolves it. Otherwise uses current HEAD.

    Returns:
        Short git commit hash.

    Raises:
        PlatformError if hash cannot be determined.
    """
    if commit_id:
        from platform_cli.tools.git_repo import resolve_short_hash
        target_hash = resolve_short_hash(commit_id, repo_root)
        if target_hash == "unknown":
            raise PlatformError(
                f"Could not resolve commit '{commit_id}' in this repository",
                code="E_INVALID_COMMIT",
                reason=commit_id,
            )
        return target_hash

    target_hash = get_short_commit_hash(repo_root)
    if target_hash == "unknown":
        raise PlatformError(
            "Could not determine current git commit hash",
            code="E_GIT_HASH_UNKNOWN",
            reason="git_rev_parse_failed",
        )
    return target_hash


def _workspace_select_or_create(tf_root: Path, workspace: str) -> None:
    """Select terraform workspace, creating it if it doesn't exist."""
    ws_result = run_cmd(
        ["terraform", "workspace", "select", workspace],
        cwd=str(tf_root),
        check=False,
        capture=True,
    )
    if ws_result.returncode != 0:
        run_cmd(
            ["terraform", "workspace", "new", workspace],
            cwd=str(tf_root), check=False, capture=True,
        )
        ws_select = run_cmd(
            ["terraform", "workspace", "select", workspace],
            cwd=str(tf_root), check=False, capture=True,
        )
        if ws_select.returncode != 0:
            raise PlatformError(
                f"Failed to select workspace {workspace}",
                code="E_TF_WORKSPACE_FAILED",
                reason=workspace,
            )


def _resolve_account() -> str:
    """Resolve current AWS account alias (dpnp or dpp) from STS identity.

    Uses account-environments manifest for account ID → alias mapping.
    """
    from platform_cli.manifests.load import get_account_alias_by_id

    result = run_cmd(
        ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
        check=False,
        capture=True,
    )
    if result.returncode != 0:
        raise PlatformError(
            f"Failed to get AWS account: {result.stderr}",
            code="E_AWS_AUTH_FAILED",
            reason="sts",
        )

    account_id = result.stdout.strip()
    account = get_account_alias_by_id(account_id)
    if not account:
        raise PlatformError(
            f"Unknown AWS account ID: {account_id}. Not found in account-environments manifest.",
            code="E_UNKNOWN_ACCOUNT",
            reason=account_id,
        )
    return account


def _get_state_key(repo_name: str, stack_id: str) -> str:
    """
    Get S3 state key for a stack.

    Default stack uses same key as Jenkins (backward compatible):
        {repo_name}/terraform.tfstate
    Non-default stacks get their own key:
        {repo_name}/{stack_id}/terraform.tfstate
    """
    if stack_id == "default":
        return f"{repo_name}/terraform.tfstate"
    return f"{repo_name}/{stack_id}/terraform.tfstate"


def deploy_infra_stack(
    stack: Any,  # InfraStackConfig
    env: str,
    plan_only: bool,
    context: Dict[str, Any],
    repo_root: Path,
    infra_templates_version: str = "",
    account_override: str = "",
) -> Dict[str, Any]:
    """
    Deploy a single infra stack following Jenkins pipeline flow:
      1. Download dependencies (git clone infra-templates from manifest repo)
      2. Generate backend config & terraform init
      3. terraform workspace select {env}
      4. terraform validate
      5. terraform plan -out={env}_tfplan -var env/account/commit_id
      6. terraform apply (unless plan_only)

    Args:
        stack: InfraStackConfig from infra.json
        env: Environment (dev, qa, prod)
        plan_only: If True, stop after plan
        context: Deploy context (refresh_deps, etc.)
        repo_root: Root path of data-product repo
        infra_templates_version: Git ref for infra-templates (branch/tag)
        account_override: Override account alias (dpnp/dpp)

    Returns:
        Dict with status and details
    """
    tf_root = repo_root / "infra" / stack.path
    if not tf_root.exists():
        raise PlatformError(
            f"Stack directory not found: {tf_root}",
            code="E_STACK_DIR_NOT_FOUND",
            reason=str(tf_root),
        )

    # Resolve account and load config from manifest
    from platform_cli.manifests.load import get_aws_region, get_state_bucket

    account = account_override or _resolve_account()
    try:
        state_bucket = get_state_bucket(account)
    except PlatformError:
        raise PlatformError(
            f"Unknown account: {account}. Not found in account-environments manifest.",
            code="E_UNKNOWN_ACCOUNT",
            reason=account,
        )

    region = get_aws_region()
    repo_name = get_repo_name()
    commit_id = get_short_commit_hash(repo_root)
    state_key = _get_state_key(repo_name, stack.id)

    print(f"  Account:    {account}")
    print(f"  State:      s3://{state_bucket}/{state_key}")
    print(f"  Workspace:  {env}")
    print(f"  TF root:    {tf_root}")

    # Step 1: Download dependencies (matches Jenkins downloadDependencies)
    # Full git URL from account-environments manifest, version from infra.json
    from platform_cli.manifests.load import get_infra_templates_repo
    templates_repo = get_infra_templates_repo()
    # templates_repo can be "org/repo" shorthand or full URL
    if templates_repo.startswith("https://") or templates_repo.startswith("http://"):
        git_url = templates_repo if templates_repo.endswith(".git") else f"{templates_repo}.git"
        dep_name = templates_repo.rstrip("/").split("/")[-1].removesuffix(".git")
    else:
        dep_name = templates_repo.rstrip("/").split("/")[-1]
        git_url = f"https://github.com/{templates_repo}.git"
    print(f"  Downloading dependencies ({dep_name}@{infra_templates_version})...")
    ensure_deps(
        tf_root,
        dependencies=[
            {
                "name": dep_name,
                "git_url": git_url,
                "ref": infra_templates_version,
            }
        ],
        refresh_deps=context.get("refresh_deps", False),
    )

    # Step 2: Generate backend config (matches Jenkins state-lock-config-module-{account}.properties)
    print(f"  Generating backend config...")
    backend_config_path = ensure_backend_config(
        tf_root,
        backend_config_file=None,
        bucket=state_bucket,
        key=state_key,
        region=region,
        output_filename=f"state-lock-config-module-{account}.properties",
    )

    # Step 3: terraform init (matches Jenkins: terraform init -input=false -backend-config=...)
    print(f"  Running terraform init...")
    init_result = run_cmd(
        [
            "terraform", "init",
            "-input=false",
            f"-backend-config={backend_config_path.name}",
        ],
        cwd=str(tf_root),
        check=False,
        capture=False,
    )
    if init_result.returncode != 0:
        raise PlatformError(
            f"terraform init failed",
            code="E_TF_INIT_FAILED",
            reason="init",
        )

    # Step 4: terraform workspace select (matches Jenkins: select or create+select)
    print(f"  Selecting workspace: {env}")
    _workspace_select_or_create(tf_root, env)

    # Step 5: terraform validate (matches Jenkins validateAndPlan)
    print(f"  Validating...")
    validate_result = run_cmd(
        ["terraform", "validate"],
        cwd=str(tf_root),
        check=False,
        capture=False,
    )
    if validate_result.returncode != 0:
        raise PlatformError(
            f"terraform validate failed",
            code="E_TF_VALIDATE_FAILED",
            reason="validate",
        )

    # Step 6: terraform plan (matches Jenkins: plan -out={env}_tfplan -var env -var account -var commit_id)
    plan_file = f"{env}_tfplan"
    print(f"  Planning (commit_id={commit_id})...")
    plan_cmd = [
        "terraform", "plan",
        f"-out={plan_file}",
        f"-var=env={env}",
        f"-var=account={account}",
        f"-var=commit_id={commit_id}",
    ]
    # Add -target flags if specified (for targeted deployments)
    for t in context.get("targets", []):
        plan_cmd.append(f"-target={t}")
    plan_result = run_cmd(
        plan_cmd,
        cwd=str(tf_root),
        check=False,
        capture=False,
    )
    if plan_result.returncode != 0:
        raise PlatformError(
            f"terraform plan failed",
            code="E_TF_PLAN_FAILED",
            reason="plan",
        )

    # Step 6b: Show plan summary (matches guardrails in tf-plan/tf-apply/tf-deploy)
    plan_summary = None
    try:
        from platform_cli.tools.terraform.terraform_runner import summarize_plan
        import json
        show_result = run_cmd(
            ["terraform", "show", "-json", plan_file],
            cwd=str(tf_root), check=False, capture=True,
        )
        if show_result.returncode == 0 and show_result.stdout:
            plan_json = json.loads(show_result.stdout)
            plan_summary = summarize_plan(plan_json)
            print(f"  Plan summary: create={plan_summary.creates} update={plan_summary.updates} "
                  f"replace={plan_summary.replacements} delete={plan_summary.deletes}")
            if plan_summary.deletes > 0:
                print(f"  WARNING: plan contains {plan_summary.deletes} delete(s): "
                      f"{', '.join(plan_summary.delete_resources[:5])}")
    except Exception:
        pass  # Best-effort summary display

    result = {
        "status": "planned",
        "plan_file": str(tf_root / plan_file),
        "state_key": state_key,
        "account": account,
        "workspace": env,
    }

    if plan_only:
        return result

    # Step 7: Confirm before apply (unless auto-approved)
    auto_approve = context.get("auto_approve", False)
    if not auto_approve:
        import typer
        if not typer.confirm("Plan complete. Proceed with terraform apply?", default=False):
            raise PlatformError(
                "Apply cancelled by user.",
                code="E_TF_APPLY_CANCELLED",
                reason="user_declined",
            )

    # Step 8: terraform apply (matches Jenkins: terraform apply {env}_tfplan)
    print(f"  Applying...")
    apply_result = run_cmd(
        ["terraform", "apply", plan_file],
        cwd=str(tf_root),
        check=False,
        capture=False,
    )
    if apply_result.returncode != 0:
        raise PlatformError(
            f"terraform apply failed",
            code="E_TF_APPLY_FAILED",
            reason="apply",
        )

    result["status"] = "applied"
    return result
