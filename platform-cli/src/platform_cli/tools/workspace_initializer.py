"""Workspace initialization for data-product repositories."""
# NOTE: Architectural rules in ARCHITECTURE.md – do not refactor cross-layer.

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from platform_cli.core.errors import PlatformError
from platform_cli.exec.runner import run_cmd
from platform_cli.manifests.load import get_infra_templates_repo
from platform_cli.tools.terraform.terraform_runner import ensure_deps


@dataclass
class InitResult:
    """Result summary for `ghdp init`."""

    initialized_apps: List[str] = field(default_factory=list)
    refreshed_stacks: List[str] = field(default_factory=list)


def _build_infra_templates_dep(templates_repo: str, ref: str) -> Dict[str, str]:
    if templates_repo.startswith(("https://", "http://")):
        git_url = templates_repo if templates_repo.endswith(".git") else f"{templates_repo}.git"
        dep_name = templates_repo.rstrip("/").split("/")[-1].removesuffix(".git")
    else:
        dep_name = templates_repo.rstrip("/").split("/")[-1]
        git_url = f"https://github.com/{templates_repo}.git"
    return {"name": dep_name, "git_url": git_url, "ref": ref}


def _validate_infra_templates_ref(git_url: str, ref: str) -> None:
    """Fail fast when infra_templates_version does not resolve to a branch/tag."""
    check = run_cmd(
        ["git", "ls-remote", "--exit-code", "--heads", "--tags", git_url, ref],
        check=False,
        capture=True,
    )
    if check.returncode != 0:
        raise PlatformError(
            f"infra_templates_version '{ref}' was not found in {git_url}.",
            code="E_INVALID_INFRA_TEMPLATES_VERSION",
            reason=ref,
        )


def _init_python_app(app: Any, app_dir: Path) -> None:
    pyproject_path = app_dir / "pyproject.toml"
    if not pyproject_path.exists():
        raise PlatformError(
            f"pyproject.toml not found in {app_dir}",
            code="E_PYPROJECT_NOT_FOUND",
            reason=str(app_dir),
        )

    uv_check = run_cmd(["uv", "--version"], check=False, capture=True)
    if uv_check.returncode != 0:
        raise PlatformError(
            "uv not found. Install via: ghdp tools install",
            code="E_UV_NOT_FOUND",
            reason="uv_missing",
        )

    sync_result = run_cmd(["uv", "sync"], cwd=str(app_dir), check=False)
    if sync_result.returncode != 0:
        raise PlatformError(
            f"uv sync failed: {sync_result.stderr}",
            code="E_UV_SYNC_FAILED",
            reason=app.path,
        )


def _init_scala_app(app: Any, app_dir: Path) -> None:
    pom_path = app_dir / "pom.xml"
    if not pom_path.exists():
        raise PlatformError(
            f"pom.xml not found in {app_dir}",
            code="E_POM_NOT_FOUND",
            reason=str(app_dir),
        )

    mvn_check = run_cmd(["mvn", "--version"], check=False, capture=True)
    if mvn_check.returncode != 0:
        raise PlatformError(
            "Maven (mvn) not found. Install Maven to initialize Scala apps.",
            code="E_MVN_NOT_FOUND",
            reason="mvn_missing",
        )

    sync_result = run_cmd(
        ["mvn", "dependency:go-offline", "-DskipTests"],
        cwd=str(app_dir),
        check=False,
    )
    if sync_result.returncode != 0:
        raise PlatformError(
            f"Maven dependency sync failed: {sync_result.stderr}",
            code="E_MVN_SYNC_FAILED",
            reason=app.path,
        )


def _init_app_dependencies(app: Any, repo_root: Path) -> None:
    app_dir = repo_root / "apps" / app.path
    if not app_dir.exists():
        raise PlatformError(
            f"App directory not found: {app_dir}",
            code="E_APP_DIR_NOT_FOUND",
            reason=str(app_dir),
        )

    if app.type == "python":
        _init_python_app(app, app_dir)
        return
    if app.type == "scala":
        _init_scala_app(app, app_dir)
        return

    raise PlatformError(
        f"Unknown app type: {app.type}",
        code="E_APP_TYPE_UNKNOWN",
        reason=app.type,
    )


def init_workspace(
    *,
    repo: Any,  # RepoStructure
    repo_root: Path,
    app_name: str | None,
    context: Dict[str, Any],
) -> InitResult:
    """Initialize app and infra dependencies for current data-product repo."""
    result = InitResult()

    if app_name:
        app = repo.get_app(app_name)
        if not app:
            available_apps = [a.path for a in repo.apps]
            raise PlatformError(
                f"App '{app_name}' not found in apps.json",
                code="E_APP_NOT_FOUND",
                reason=f"Available apps: {', '.join(available_apps)}",
            )
        apps_to_init = [app]
    else:
        apps_to_init = list(repo.apps)

    for app in apps_to_init:
        _init_app_dependencies(app, repo_root)
        result.initialized_apps.append(app.path)

    if repo.infra_stacks:
        infra_templates_version = (repo.infra_templates_version or "").strip()
        if not infra_templates_version:
            raise PlatformError(
                "Missing 'infra_templates_version' in infra.json",
                code="E_MISSING_INFRA_TEMPLATES_VERSION",
                reason="infra.json must specify infra_templates_version",
            )

        templates_repo = get_infra_templates_repo()
        dep = _build_infra_templates_dep(templates_repo, infra_templates_version)
        _validate_infra_templates_ref(dep["git_url"], infra_templates_version)

        stacks = sorted(repo.infra_stacks, key=lambda s: s.deployment_order)
        for stack in stacks:
            tf_root = repo_root / "infra" / stack.path
            if not tf_root.exists():
                raise PlatformError(
                    f"Stack directory not found: {tf_root}",
                    code="E_STACK_DIR_NOT_FOUND",
                    reason=str(tf_root),
                )
            ensure_deps(
                tf_root,
                dependencies=[dep],
                refresh_deps=True,
                rich_logs=bool(context.get("verbose", False)),
            )
            result.refreshed_stacks.append(stack.id)

    if not result.initialized_apps and not result.refreshed_stacks:
        raise PlatformError(
            "Nothing to initialize. Add app(s) to apps.json and/or stack(s) to infra.json.",
            code="E_NOTHING_TO_INIT",
            reason="no_apps_or_stacks",
        )

    return result
