# NOTE: Architectural rules in ARCHITECTURE.md -- do not refactor cross-layer.
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from platform_cli.core.errors import PlatformError
from platform_cli.exec.runner import run_cmd

# Interactive workspace check/fix for 'dev' workspace
from rich.prompt import Confirm
import typer

def check_and_fix_workspace_dev(tf_root, env_vars, **kwargs):
    """
    Ensure the current Terraform workspace is 'dev'.
    If not, prompt the user to switch or create it interactively.
    Returns True if workspace is 'dev' or successfully switched, False otherwise.
    """
    resolved_tf_root = tf_root
    if not resolved_tf_root:
        # Keep precheck behavior aligned with runtime root resolution:
        # --tf-root > policy.default_tf_root > cwd fallback.
        try:
            from platform_cli.manifests.load import load_terraform_local_policy

            policy, _ = load_terraform_local_policy()
            validate_local_policy(policy)
            resolved_tf_root = resolve_tf_root(None, policy)
        except Exception:
            resolved_tf_root = Path.cwd().resolve()

    if not isinstance(resolved_tf_root, Path):
        root = Path(str(resolved_tf_root)).expanduser()
        if not root.is_absolute():
            root = (Path.cwd().resolve() / root).resolve()
        resolved_tf_root = root

    try:
        # Get current workspace
        res = _run_terraform(resolved_tf_root, ["workspace", "show"], env_vars, op="workspace_show")
        current = (res or "").strip()
    except Exception:
        current = None
    if current == "dev":
        return True
    typer.echo(f"Current Terraform workspace: {current!r}")
    if not Confirm.ask("Switch to 'dev' workspace? (create if missing)", default=True):
        return False
    try:
        _run_terraform(resolved_tf_root, ["workspace", "select", "dev"], env_vars, op="workspace_select")
    except Exception:
        _run_terraform(resolved_tf_root, ["workspace", "new", "dev"], env_vars, op="workspace_new")
        _run_terraform(resolved_tf_root, ["workspace", "select", "dev"], env_vars, op="workspace_select")
    typer.echo("Switched to workspace: 'dev'")
    return True

def _as_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def validate_local_policy(policy: Dict[str, Any]) -> None:
    if not isinstance(policy, dict):
        raise PlatformError(
            "Terraform local policy must be a JSON object.",
            code="E_TF_POLICY_INVALID",
            reason="policy_root",
        )

    required = ["allowed_envs", "default_tf_root", "default_region", "dependencies", "backend"]
    for key in required:
        if key not in policy:
            raise PlatformError(
                f"Terraform local policy missing required key '{key}'.",
                code="E_TF_POLICY_INVALID",
                reason=f"missing:{key}",
            )

    if not isinstance(policy.get("allowed_envs"), list):
        raise PlatformError(
            "Terraform local policy allowed_envs must be an array.",
            code="E_TF_POLICY_INVALID",
            reason="allowed_envs",
        )

    if not isinstance(policy.get("dependencies"), list):
        raise PlatformError(
            "Terraform local policy dependencies must be an array.",
            code="E_TF_POLICY_INVALID",
            reason="dependencies",
        )

    if not isinstance(policy.get("backend"), dict):
        raise PlatformError(
            "Terraform local policy backend must be an object.",
            code="E_TF_POLICY_INVALID",
            reason="backend",
        )


@dataclass
class PlanSummary:
    creates: int = 0
    updates: int = 0
    replacements: int = 0
    deletes: int = 0
    no_ops: int = 0
    create_resources: List[str] = field(default_factory=list)
    update_resources: List[str] = field(default_factory=list)
    replacement_resources: List[str] = field(default_factory=list)
    delete_resources: List[str] = field(default_factory=list)
    no_op_resources: List[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.creates + self.updates + self.replacements + self.deletes + self.no_ops


def _raise_terraform_error(op: str, cmd: List[str], stderr_or_stdout: str) -> None:
    snippet = (stderr_or_stdout or "").strip()
    if len(snippet) > 1200:
        snippet = snippet[:1200] + "..."

    hint = ""
    if "state lock" in snippet.lower() or "acquiring the state lock" in snippet.lower():
        hint = (
            "\nHint: Terraform state appears locked. Verify no concurrent run is active, "
            "then release/unlock only with your team's approved process."
        )

    raise PlatformError(
        f"Terraform {op} failed while running: {' '.join(cmd)}\n{snippet}{hint}",
        code=f"E_TF_{op.upper()}_FAILED",
        reason=op,
    )


def _run_terraform(
    tf_root: Path,
    args: List[str],
    env_vars: Dict[str, str],
    *,
    op: str,
    rich_logs: bool = False,
    stream: bool = False,
) -> str:
    cmd = ["terraform", *args]
    res = run_cmd(
        cmd,
        check=False,
        capture=not stream,
        cwd=str(tf_root),
        env=env_vars,
        rich_logs=rich_logs,
    )
    if res.returncode != 0:
        _raise_terraform_error(op, cmd, res.stderr or res.stdout)
    return res.stdout or ""


def resolve_tf_root(tf_root_override: Optional[str], policy: Dict[str, Any], repo_root: Optional[Path] = None) -> Path:
    base = repo_root or Path.cwd().resolve()
    configured = _as_str(tf_root_override) or _as_str(policy.get("default_tf_root")) or "./"
    root = Path(configured).expanduser()
    if not root.is_absolute():
        root = (base / root).resolve()

    if not root.exists() or not root.is_dir():
        raise PlatformError(
            f"Terraform root directory does not exist: {root}",
            code="E_TF_ROOT_NOT_FOUND",
            reason=str(root),
        )
    return root


def ensure_deps(tf_root: Path, dependencies: Iterable[Dict[str, Any]], refresh_deps: bool = False, rich_logs: bool = False) -> List[Path]:
    # Match Jenkins behavior where dependencies are created in the terraform working directory.
    deps_root = tf_root.resolve() / ".dependencies"
    deps_root.mkdir(parents=True, exist_ok=True)

    # Inject git credentials as process-scoped env vars (GIT_CONFIG_COUNT/KEY/VALUE, git 2.31+).
    # This avoids writing to ~/.gitconfig entirely — Jenkins native auth is never disturbed,
    # no cleanup is needed, and retained agents stay clean across builds.
    # GIT_CREDS_USR/GIT_CREDS_PSW are always present in Jenkins via credentials() binding.
    _git_env: Optional[Dict[str, str]] = None
    _usr = os.environ.get("GIT_CREDS_USR") or os.environ.get("GIT_USER")
    _psw = os.environ.get("GIT_CREDS_PSW") or os.environ.get("GIT_TOKEN")
    if _usr and _psw:
        _git_env = {
            **os.environ,
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": '!f() { echo "username=${GIT_CREDS_USR:-${GIT_USER}}"; echo "password=${GIT_CREDS_PSW:-${GIT_TOKEN}}"; }; f',
        }

    targets: List[Path] = []
    for dep in dependencies:
        name = _as_str(dep.get("name"))
        git_url = _as_str(dep.get("git_url"))
        ref = _as_str(dep.get("ref"))

        if not name or not git_url:
            raise PlatformError(
                "Dependency definition must include name and git_url.",
                code="E_TF_DEPENDENCY_INVALID",
                reason="dependency",
            )

        target = deps_root / name
        if refresh_deps and target.exists():
            shutil.rmtree(target)

        if target.exists():
            if not (target / ".git").exists():
                raise PlatformError(
                    f"Dependency directory exists but is not a git repo: {target}",
                    code="E_TF_DEPENDENCY_INVALID",
                    reason=str(target),
                )
            run_cmd(["git", "-C", str(target), "fetch", "--tags", "--prune", "origin"], check=True, rich_logs=rich_logs, env=_git_env)
        else:
            run_cmd(["git", "clone", git_url, str(target)], check=True, rich_logs=rich_logs, env=_git_env)

        if ref:
            run_cmd(["git", "-C", str(target), "checkout", ref], check=True, rich_logs=rich_logs)

        targets.append(target)

    return targets


def _extract_backend_key(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.lower().startswith("key") and "=" in s:
                return s.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def ensure_backend_config(
    tf_root: Path,
    *,
    backend_config_file: Optional[str],
    bucket: Optional[str],
    key: Optional[str],
    region: str,
    output_filename: Optional[str] = None,
    use_lockfile: bool = True,
) -> Path:
    if backend_config_file:
        candidate = Path(backend_config_file).expanduser()
        if not candidate.is_absolute():
            tf_local = (tf_root / candidate).resolve()
            candidate = tf_local if tf_local.exists() else (Path.cwd().resolve() / candidate).resolve()

        if not candidate.exists():
            raise PlatformError(
                f"Backend config file not found: {candidate}",
                code="E_TF_BACKEND_CONFIG_NOT_FOUND",
                reason=str(candidate),
            )
        return candidate

    b = _as_str(bucket)
    k = _as_str(key)
    r = _as_str(region)

    if not b or not k:
        raise PlatformError(
            "Backend config requires bucket and key when --backend-config-file is not provided.",
            code="E_TF_BACKEND_CONFIG_MISSING",
            reason="bucket_or_key",
        )

    if not r:
        raise PlatformError(
            "Backend config requires region.",
            code="E_TF_BACKEND_CONFIG_MISSING",
            reason="region",
        )

    out_name = _as_str(output_filename) or "state-lock-config.properties"
    out = tf_root / out_name
    lock_literal = "true" if use_lockfile else "false"
    content = (
        f'bucket = "{b}"\n'
        f'region = "{r}"\n'
        f'key = "{k}"\n'
        f'use_lockfile = {lock_literal}\n'
    )
    out.write_text(content, encoding="utf-8")
    return out


def _build_plan_arg(tf_root: Path, planfile: Path) -> str:
    if planfile.parent.resolve() == tf_root.resolve():
        return planfile.name
    return str(planfile)


def preflight_aws_auth(
    *,
    aws_profile: Optional[str],
    aws_region: Optional[str],
    try_login: bool = False,
    non_interactive: bool = False,
) -> Dict[str, str]:
    env_vars = dict(os.environ)

    if aws_profile:
        env_vars["AWS_PROFILE"] = aws_profile
    if aws_region:
        env_vars["AWS_REGION"] = aws_region
        env_vars["AWS_DEFAULT_REGION"] = aws_region

    sts = run_cmd(["aws", "sts", "get-caller-identity"], check=False, capture=True, env=env_vars)
    if sts.returncode == 0:
        return env_vars

    if try_login and aws_profile and not non_interactive:
        run_cmd(["aws", "sso", "login", "--profile", aws_profile], check=True)
        sts2 = run_cmd(["aws", "sts", "get-caller-identity"], check=False, capture=True, env=env_vars)
        if sts2.returncode == 0:
            return env_vars

    raise PlatformError(
        "AWS authentication check failed. Run `aws sso login --profile <profile>` and retry.",
        code="E_AWS_AUTH_REQUIRED",
        reason="sts_get_caller_identity",
    )


def terraform_init(
    tf_root: Path,
    backend_config_file: Path,
    env_vars: Dict[str, str],
    rich_logs: bool = False,
    stream: bool = False,
) -> None:
    _run_terraform(
        tf_root,
        [
            "init",
            "-upgrade",
            "-input=false",
            f"-backend-config={str(backend_config_file)}",
            "-reconfigure",
            "-input=false",
        ],
        env_vars,
        op="init",
        rich_logs=rich_logs,
        stream=stream,
    )


def terraform_workspace_select_dev(tf_root: Path, env_vars: Dict[str, str], stream: bool = False) -> str:
    select = run_cmd(
        ["terraform", "workspace", "select", "dev"],
        check=False,
        capture=not stream,
        cwd=str(tf_root),
        env=env_vars,
    )
    if select.returncode == 0:
        return "dev"

    _run_terraform(tf_root, ["workspace", "new", "dev"], env_vars, op="workspace_new", stream=stream)
    _run_terraform(tf_root, ["workspace", "select", "dev"], env_vars, op="workspace_select", stream=stream)
    return "dev"


def terraform_validate(tf_root: Path, env_vars: Dict[str, str], stream: bool = False) -> None:
    _run_terraform(tf_root, ["validate"], env_vars, op="validate", stream=stream)


def terraform_fmt(tf_root: Path, env_vars: Dict[str, str], recursive: bool = True) -> None:
    args = ["fmt"]
    if recursive:
        args.append("-recursive")
    _run_terraform(tf_root, args, env_vars, op="fmt")


def terraform_plan(
    tf_root: Path,
    out_planfile: Path,
    env_vars: Dict[str, str],
    var_values: Optional[Dict[str, str]] = None,
    stream: bool = False,
) -> Path:
    plan_arg = _build_plan_arg(tf_root, out_planfile)
    args = ["plan", f"-out={plan_arg}"]

    for key, value in (var_values or {}).items():
        if value is None:
            continue
        args.extend(["-var", f"{key}={value}"])

    _run_terraform(tf_root, args, env_vars, op="plan", stream=stream)
    return out_planfile


def terraform_show_json(tf_root: Path, planfile: Path, env_vars: Dict[str, str]) -> Dict[str, Any]:
    plan_arg = _build_plan_arg(tf_root, planfile)
    stdout = _run_terraform(tf_root, ["show", "-json", plan_arg], env_vars, op="show")

    try:
        parsed = json.loads(stdout or "{}")
    except json.JSONDecodeError as exc:
        raise PlatformError(
            f"Could not parse terraform show JSON: {exc}",
            code="E_TF_PLAN_JSON_INVALID",
            reason="json_decode",
        )

    if not isinstance(parsed, dict):
        raise PlatformError(
            "terraform show output is not a JSON object.",
            code="E_TF_PLAN_JSON_INVALID",
            reason="invalid_root_type",
        )

    return parsed


def _action_kind(actions: Iterable[str]) -> str:
    normalized = [str(a).strip().lower() for a in actions]
    kinds = set(normalized)

    if kinds == {"create"}:
        return "create"
    if kinds == {"update"}:
        return "update"
    if kinds == {"delete"}:
        return "delete"
    if kinds == {"create", "delete"}:
        return "replace"
    if kinds == {"no-op"}:
        return "no-op"

    if "delete" in kinds and "create" in kinds:
        return "replace"
    if "delete" in kinds:
        return "delete"
    if "create" in kinds:
        return "create"
    if "update" in kinds:
        return "update"
    return "no-op"


def summarize_plan(plan_json: Dict[str, Any]) -> PlanSummary:
    summary = PlanSummary()

    resource_changes = plan_json.get("resource_changes", [])
    if not isinstance(resource_changes, list):
        return summary

    for change_obj in resource_changes:
        if not isinstance(change_obj, dict):
            continue

        address = _as_str(change_obj.get("address")) or "<unknown>"
        change = change_obj.get("change", {})
        if not isinstance(change, dict):
            continue

        actions = change.get("actions", [])
        if not isinstance(actions, list):
            continue

        kind = _action_kind(actions)
        if kind == "create":
            summary.creates += 1
            summary.create_resources.append(address)
        elif kind == "update":
            summary.updates += 1
            summary.update_resources.append(address)
        elif kind == "delete":
            summary.deletes += 1
            summary.delete_resources.append(address)
        elif kind == "replace":
            summary.replacements += 1
            summary.replacement_resources.append(address)
        else:
            summary.no_ops += 1
            summary.no_op_resources.append(address)

    return summary


def enforce_guardrails(plan_json: Dict[str, Any], policy: Dict[str, Any], *, env: str) -> PlanSummary:
    allowed_envs = {str(v).strip().lower() for v in policy.get("allowed_envs", [])}
    env_key = _as_str(env).lower()
    if env_key not in allowed_envs:
        raise PlatformError(
            f"Local terraform is blocked for env '{env}'. Allowed envs: {', '.join(sorted(allowed_envs)) or 'none'}.",
            code="E_TF_POLICY_DENY",
            reason="env_not_allowed",
        )
    summary = summarize_plan(plan_json)

    # Load allowed actions from policy, default to only create/update/replace allowed
    allowed_actions = set(
        str(a).strip().lower() for a in policy.get("allowed_actions", ["create", "update", "replace"])
    )
    violations = []
    if "delete" not in allowed_actions and summary.deletes > 0:
        violations.append(f"delete ({summary.deletes})")
    if "replace" not in allowed_actions and summary.replacements > 0:
        violations.append(f"replace ({summary.replacements})")
    if "create" not in allowed_actions and summary.creates > 0:
        violations.append(f"create ({summary.creates})")
    if "update" not in allowed_actions and summary.updates > 0:
        violations.append(f"update ({summary.updates})")
    if violations:
        raise PlatformError(
            f"Plan contains disallowed actions: {', '.join(violations)}. See policy.allowed_actions.",
            code="E_TF_POLICY_DENY",
            reason="action_not_allowed",
        )
    return summary


def terraform_apply(
    tf_root: Path,
    planfile: Path,
    env_vars: Dict[str, str],
    auto_approve: bool = False,
    stream: bool = False,
) -> None:
    plan_arg = _build_plan_arg(tf_root, planfile)
    args = ["apply"]
    if auto_approve:
        args.append("-auto-approve")
    args.append(plan_arg)

    _run_terraform(tf_root, args, env_vars, op="apply", stream=stream)


def backend_key_from_config(path: Path) -> str:
    return _extract_backend_key(path)
