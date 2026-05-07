"""Docker image building."""
# NOTE: Architectural rules in ARCHITECTURE.md – do not refactor cross-layer.

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

try:
    from platform_cli.core.errors import PlatformError  # type: ignore
except Exception:  # pragma: no cover
    class PlatformError(RuntimeError):
        def __init__(self, message: str, code: str = "E_INTERNAL", reason: str = "UNKNOWN", alert: bool = False):
            super().__init__(message)
            self.code = code
            self.reason = reason
            self.alert = alert

from platform_cli.exec.runner import run_cmd
from platform_cli.tools.git_repo import get_short_commit_hash


def get_local_docker_tag(app: Any, git_hash: str) -> str:
    """Compute the local Docker image tag matching Jenkins convention.

    This is the single source of truth for local tag format, used by both
    docker_builder (build) and codeartifact_publisher (push).

    Returns:
        Tag string like "careeverywhere-batch-app:batch-app-6e2a75c"
    """
    component = app.component
    tag_version = f"{component}-{git_hash}"
    return f"{app.path}:{tag_version}"


def build_docker_image(
    app: Any,  # AppConfig
    context: Dict[str, Any],
    repo_root: Path,
) -> str:
    """
    Build Docker image for an app that lists 'docker' in its tools.

    Expects a Dockerfile in apps/<app.path>/.
    Follows Jenkins dockerBuild convention exactly:
      localImageName = {image_repo}-{component}
      tag = {component}-{git_short_hash}

    Args:
        app: AppConfig instance (with docker_details for component)
        context: Build context
        repo_root: Root path of data-product repo

    Returns:
        Local docker image tag (name:tag)
    """
    app_dir = repo_root / "apps" / app.path
    dockerfile_path = app_dir / "Dockerfile"

    if not dockerfile_path.exists():
        raise PlatformError(
            f"Dockerfile not found: {dockerfile_path}",
            code="E_DOCKERFILE_NOT_FOUND",
            reason=str(dockerfile_path),
        )

    # Use git short hash (matches Jenkins: VERSION = GIT_COMMIT.take(7))
    git_hash = get_short_commit_hash(repo_root)
    tag = get_local_docker_tag(app, git_hash)

    # Build from apps/ directory to allow access to _shared and other apps
    apps_dir = repo_root / "apps"
    relative_dockerfile = Path(app.path) / "Dockerfile"

    print(f"  Building Docker image {tag}...")
    result = run_cmd(
        ["docker", "build", "--platform", "linux/amd64", "-t", tag, "-f", str(relative_dockerfile), "."],
        cwd=str(apps_dir),
        check=False,
    )

    if result.returncode != 0:
        raise PlatformError(
            f"Docker build failed: {result.stderr}",
            code="E_DOCKER_BUILD_FAILED",
            reason=app.path,
        )

    return tag
