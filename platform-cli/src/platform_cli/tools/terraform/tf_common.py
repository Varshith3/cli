# NOTE: Architectural rules in ARCHITECTURE.md -- do not refactor cross-layer.
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

from platform_cli.core.errors import PlatformError
from platform_cli.manifests.load import load_terraform_local_policy
from platform_cli.tools.git_repo import get_commit_sha, get_repo_name
from platform_cli.tools.terraform.terraform_runner import _run_terraform
import typer
from platform_cli.tools.terraform import (
    backend_key_from_config,
    ensure_backend_config,
    ensure_deps,
    preflight_aws_auth,
    resolve_tf_root,
    terraform_init,
    terraform_validate,
    terraform_workspace_select_dev,
    validate_local_policy,
)

def terraform_workspace_select(tf_root: Path, env_vars: Dict[str, str], workspace_name: str) -> str:
    try:
        _run_terraform(tf_root, ["workspace", "select", workspace_name], env_vars, op="workspace_select")
        typer.echo(f"Switched to workspace: {workspace_name}")
    except PlatformError:
        # Workspace doesn't exist — create it then select
        _run_terraform(tf_root, ["workspace", "new", workspace_name], env_vars, op="workspace_new")
        _run_terraform(tf_root, ["workspace", "select", workspace_name], env_vars, op="workspace_select")
        typer.echo(f"Created and switched to workspace: {workspace_name}")
    return workspace_name



@dataclass
class TerraformRuntime:
    policy: Dict[str, Any]
    policy_source: str
    tf_root: Path
    env_vars: Dict[str, str]
    region: str


def ensure_env_allowed(policy: Dict[str, Any], env: str) -> None:
    allowed = {str(v).strip().lower() for v in policy.get("allowed_envs", [])}
    env_key = (env or "").strip().lower()
    if env_key not in allowed:
        raise PlatformError(
            f"Local terraform is blocked for env '{env}'. Allowed envs: {', '.join(sorted(allowed)) or 'none'}.",
            code="E_TF_POLICY_DENY",
            reason="env_not_allowed",
        )


def load_runtime_policy() -> Tuple[Dict[str, Any], str]:
    policy, source = load_terraform_local_policy()
    validate_local_policy(policy)
    return policy, source


def resolve_region(policy: Dict[str, Any], override: Optional[str]) -> str:
    region = (override or "").strip() or str(policy.get("default_region") or "").strip()
    if not region:
        raise PlatformError(
            "AWS region was not provided and policy default_region is empty.",
            code="E_TF_REGION_MISSING",
            reason="region",
        )
    return region


def resolve_planfile(tf_root: Path, env: str, planfile: Optional[str]) -> Path:
    if planfile:
        p = Path(planfile).expanduser()
        if not p.is_absolute():
            p = (tf_root / p).resolve()
        return p

    safe_env = (env or "dev").strip().lower() or "dev"
    return (tf_root / f"{safe_env}_tfplan").resolve()


def resolve_account(account: Optional[str]) -> str:
    return (account or "").strip() or get_repo_name(default="unknown")


def resolve_commit_id(commit_id: Optional[str]) -> str:
    return (commit_id or "").strip() or get_commit_sha(default="unknown")


_TF_VAR_BLOCK_RE = re.compile(r'variable\s+"(?P<name>[^"]+)"\s*{', re.IGNORECASE)


def discover_declared_tf_variables(tf_root: Path) -> Set[str]:
    declared: Set[str] = set()
    for path in tf_root.glob("*.tf"):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for m in _TF_VAR_BLOCK_RE.finditer(text):
            name = (m.group("name") or "").strip()
            if name:
                declared.add(name)
    return declared


def _resolve_commit_id_for_plan(
    *,
    explicit_commit_id: Optional[str],
    non_interactive: bool,
) -> Optional[str]:
    explicit = (explicit_commit_id or "").strip()
    if explicit:
        return explicit

    detected = get_commit_sha(default="").strip()
    if detected:
        return detected

    if non_interactive:
        return None

    if typer.confirm(
        "Could not detect git commit SHA. Proceed without commit_id?",
        default=True,
    ):
        return None

    typed = typer.prompt("Enter commit_id", default="", show_default=False).strip()
    if not typed:
        raise PlatformError(
            "commit_id is required because you chose not to continue without it.",
            code="E_COMMIT_ID_REQUIRED",
            reason="missing_commit_id",
        )
    return typed


def build_plan_vars(
    *,
    tf_root: Path,
    env: str,
    account: Optional[str],
    commit_id: Optional[str],
    non_interactive: bool,
) -> Dict[str, str]:
    declared = discover_declared_tf_variables(tf_root)
    vars_out: Dict[str, str] = {}

    if "env" in declared:
        vars_out["env"] = env
    if "account" in declared:
        vars_out["account"] = resolve_account(account)
    if "commit_id" in declared:
        resolved_commit = _resolve_commit_id_for_plan(
            explicit_commit_id=commit_id,
            non_interactive=non_interactive,
        )
        if resolved_commit:
            vars_out["commit_id"] = resolved_commit

    return vars_out


def _as_non_empty(value: Optional[str]) -> str:
    return (value or "").strip()


def _resolve_backend_account(
    policy: Dict[str, Any],
    *,
    backend_account: Optional[str],
    account: Optional[str],
) -> str:
    for candidate in (
        _as_non_empty(backend_account),
        _as_non_empty(account),
        _as_non_empty(str(policy.get("default_backend_account") or "")),
    ):
        if candidate:
            return candidate
    return ""


def _resolve_backend_mode(policy: Dict[str, Any], *, env: Optional[str]) -> str:
    backend_policy = policy.get("backend", {}) if isinstance(policy.get("backend"), dict) else {}
    configured = _as_non_empty(str(backend_policy.get("default_mode") or ""))
    if configured in {"module", "standard"}:
        return configured
    return "module" if _as_non_empty(env) else "standard"


def _build_backend_template_defaults(
    policy: Dict[str, Any],
    *,
    env: Optional[str],
    account: Optional[str],
    backend_account: Optional[str],
    terraform_component: Optional[str],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    templates = policy.get("backend_templates", {}) if isinstance(policy.get("backend_templates"), dict) else {}
    accounts = templates.get("accounts", {}) if isinstance(templates.get("accounts"), dict) else {}

    account_key = _resolve_backend_account(policy, backend_account=backend_account, account=account).lower()
    if not account_key:
        return None, None, None

    account_cfg = accounts.get(account_key, {}) if isinstance(accounts.get(account_key), dict) else {}
    if not account_cfg:
        return None, None, None

    mode = _resolve_backend_mode(policy, env=env)
    bucket_key = "module_bucket" if mode == "module" else "standard_bucket"
    bucket = _as_non_empty(str(account_cfg.get(bucket_key) or ""))
    if not bucket:
        return None, None, None

    repo_short_name = get_repo_name(default="unknown")
    component = _as_non_empty(terraform_component)
    if component:
        repo_short_name = f"{repo_short_name}-{component}"

    key = f"{repo_short_name}/terraform.tfstate"
    stem = f"state-lock-config-module-{account_key}" if mode == "module" else f"state-lock-config-{account_key}"
    if component:
        stem = f"{stem}-{component}"
    filename = f"{stem}.properties"
    return bucket, key, filename


def build_runtime(
    *,
    tf_root_override: Optional[str],
    aws_profile: Optional[str],
    aws_region_override: Optional[str],
    aws_login: bool,
    non_interactive: bool,
) -> TerraformRuntime:
    policy, source = load_runtime_policy()
    tf_root = resolve_tf_root(tf_root_override, policy)
    region = resolve_region(policy, aws_region_override)
    env_vars = preflight_aws_auth(
        aws_profile=aws_profile,
        aws_region=region,
        try_login=aws_login,
        non_interactive=non_interactive,
    )

    return TerraformRuntime(
        policy=policy,
        policy_source=source,
        tf_root=tf_root,
        env_vars=env_vars,
        region=region,
    )


def build_runtime_without_auth(*, tf_root_override: Optional[str]) -> TerraformRuntime:
    policy, source = load_runtime_policy()
    tf_root = resolve_tf_root(tf_root_override, policy)
    return TerraformRuntime(
        policy=policy,
        policy_source=source,
        tf_root=tf_root,
        env_vars=dict(os.environ),
        region=str(policy.get("default_region") or ""),
    )


def run_init_sequence(
    runtime: TerraformRuntime,
    *,
    env: Optional[str],
    account: Optional[str],
    backend_account: Optional[str],
    terraform_component: Optional[str],
    backend_config_file: Optional[str],
    backend_bucket: Optional[str],
    backend_key: Optional[str],
    refresh_deps: bool,
    rich_logs: bool = True,
    stream_terraform: bool = False,
) -> Tuple[Path, str, str]:
    ensure_deps(runtime.tf_root, runtime.policy.get("dependencies", []), refresh_deps=refresh_deps, rich_logs=rich_logs)

    backend_policy = runtime.policy.get("backend", {}) if isinstance(runtime.policy.get("backend"), dict) else {}
    use_lockfile = bool(backend_policy.get("use_lockfile", True))
    output_filename: Optional[str] = None

    has_explicit_backend_values = bool(_as_non_empty(backend_bucket) or _as_non_empty(backend_key))
    if not _as_non_empty(backend_config_file) and not has_explicit_backend_values:
        tmpl_bucket, tmpl_key, tmpl_filename = _build_backend_template_defaults(
            runtime.policy,
            env=env,
            account=account,
            backend_account=backend_account,
            terraform_component=terraform_component,
        )
        if tmpl_bucket and tmpl_key:
            backend_bucket = tmpl_bucket
            backend_key = tmpl_key
            output_filename = tmpl_filename

    backend_cfg = ensure_backend_config(
        runtime.tf_root,
        backend_config_file=backend_config_file,
        bucket=backend_bucket,
        key=backend_key,
        region=runtime.region,
        output_filename=output_filename,
        use_lockfile=use_lockfile,
    )
    backend_key_resolved = backend_key_from_config(backend_cfg)

    terraform_init(
        runtime.tf_root,
        backend_cfg,
        runtime.env_vars,
        rich_logs=rich_logs,
        stream=stream_terraform,
    )
    workspace = terraform_workspace_select_dev(runtime.tf_root, runtime.env_vars, stream=stream_terraform)
    return backend_cfg, backend_key_resolved, workspace


def run_validate(runtime: TerraformRuntime, *, stream_terraform: bool = False) -> None:
    terraform_validate(runtime.tf_root, runtime.env_vars, stream=stream_terraform)


def top_plan_resources(summary: Any, limit: int = 8):
    """Extract top changed resources from a PlanSummary for display."""
    from platform_cli.tools.terraform.terraform_runner import PlanSummary
    ordered = (
        summary.create_resources
        + summary.update_resources
        + summary.replacement_resources
        + summary.delete_resources
    )
    out = []
    for item in ordered:
        if item not in out:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def confirm_or_fail(yes: bool, *, prompt: str = "Proceed with terraform apply?") -> None:
    """Confirm terraform apply or raise PlatformError.

    In non-interactive mode, requires --yes/--auto-approve.
    """
    if yes:
        return

    from platform_cli.core.context import ctx as cli_ctx
    if bool(cli_ctx.non_interactive):
        raise PlatformError(
            "Non-interactive mode requires --yes/--auto-approve.",
            code="E_CONFIRM_REQUIRED",
            reason="non_interactive",
        )

    if not typer.confirm(prompt, default=False):
        raise PlatformError(
            "Operation cancelled by user.",
            code="E_CANCELLED",
            reason="user_declined_apply",
        )
