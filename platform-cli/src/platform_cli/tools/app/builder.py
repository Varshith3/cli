"""Orchestrates application build workflow."""
# NOTE: Architectural rules in ARCHITECTURE.md – do not refactor cross-layer.

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass
class BuildResult:
    """Result of an app build operation."""

    artifact_path: str
    docker_image: Optional[str] = None


def build_app(
    app: Any,  # AppConfig from repo_structure
    context: Dict[str, Any],
    repo_root: Path,
) -> BuildResult:
    """
    Main entry point for building an app by language type.

    Routes to the appropriate builder based on app.type:
      - python -> uv sync + uv build
      - scala  -> mvn package

    Version is determined by git branch (snapshot vs release), not environment.
    If 'docker' is in app.tools, also builds a Docker image.
    """
    app_dir = repo_root / "apps" / app.path

    if not app_dir.exists():
        raise PlatformError(
            f"App directory not found: {app_dir}",
            code="E_APP_DIR_NOT_FOUND",
            reason=str(app_dir),
        )

    if app.type == "python":
        from platform_cli.tools.app.python_builder import build_python_app

        artifact = build_python_app(app, context, repo_root)
    elif app.type == "scala":
        from platform_cli.tools.app.scala_builder import build_scala_app

        artifact = build_scala_app(app, context, repo_root)
    else:
        raise PlatformError(
            f"Unknown app type: {app.type}",
            code="E_APP_TYPE_UNKNOWN",
            reason=app.type,
        )

    # Docker build if requested via tools list
    docker_image = None
    if app.needs_docker:
        from platform_cli.tools.app.docker_builder import build_docker_image

        docker_image = build_docker_image(app, context, repo_root)

    return BuildResult(artifact_path=artifact, docker_image=docker_image)
