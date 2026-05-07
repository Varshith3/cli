"""Runtime data models for data-product repo structure."""
# NOTE: Architectural rules in ARCHITECTURE.md – do not refactor cross-layer.

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any


# Tool → type inference mapping
TOOL_TYPE_MAP: Dict[str, str] = {
    "uv": "python",
    "maven": "scala",
}


def _infer_type_from_tools(tools: List[str]) -> str:
    """Infer app type from tools list. uv→python, maven→scala."""
    for tool in tools:
        if tool in TOOL_TYPE_MAP:
            return TOOL_TYPE_MAP[tool]
    return "unknown"


@dataclass
class AppConfig:
    """Configuration for a single application.

    Identified by path (folder under apps/).
    Type inferred from tools (uv→python, maven→scala).
    Docker details only present when docker is in tools.
    """

    path: str  # relative path under apps/ — also used as app identifier
    tools: List[str] = field(default_factory=list)
    docker_details: Optional[Dict[str, str]] = None  # {ecr_repository, component}

    @property
    def type(self) -> str:
        """Infer type from tools: uv→python, maven→scala."""
        return _infer_type_from_tools(self.tools)

    @property
    def needs_docker(self) -> bool:
        """Whether this app produces a Docker image."""
        return "docker" in self.tools

    @property
    def ecr_repository(self) -> str:
        """ECR repository template from docker_details."""
        if self.docker_details:
            return self.docker_details.get("ecr_repository", "")
        return ""

    @property
    def component(self) -> str:
        """Docker component name for image tagging (from docker_details).

        Matches Jenkins 'component' variable. e.g., 'historical-load'
        Falls back to app path if component is missing or empty.
        """
        if self.docker_details and self.docker_details.get("component"):
            return self.docker_details["component"]
        return self.path


@dataclass
class InfraStackConfig:
    """Configuration for an infrastructure stack."""

    id: str
    path: str
    description: str = ""
    deployment_order: int = 999


@dataclass
class RepoStructure:
    """Represents target data-product repository structure."""

    repo_root: str
    apps: List[AppConfig]
    infra_stacks: List[InfraStackConfig]
    infra_templates_version: str = ""

    def get_app(self, path: str) -> Optional[AppConfig]:
        """Find app by path."""
        for app in self.apps:
            if app.path == path:
                return app
        return None

    def get_infra_stack(self, stack_id: str) -> Optional[InfraStackConfig]:
        """Find infra stack by ID."""
        for stack in self.infra_stacks:
            if stack.id == stack_id:
                return stack
        return None

    def validate_structure(self) -> List[str]:
        """Validate the repo structure consistency."""
        errors = []
        for app in self.apps:
            if app.type == "unknown":
                errors.append(
                    f"App at '{app.path}' has no recognized build tool in tools={app.tools}. Expected uv or maven."
                )
            if app.needs_docker and not app.docker_details:
                errors.append(
                    f"App at '{app.path}' has docker in tools but no docker_details specified."
                )
        # Validate default stack exists if there are stacks
        if self.infra_stacks:
            ids = [s.id for s in self.infra_stacks]
            if "default" not in ids:
                errors.append(
                    "infra.json must contain a stack with id='default'"
                )
        return errors
