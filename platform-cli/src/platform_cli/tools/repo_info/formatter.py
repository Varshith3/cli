"""Format repo structure for display."""
# NOTE: Architectural rules in ARCHITECTURE.md – do not refactor cross-layer.

from __future__ import annotations

import json
from typing import Any

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


def format_repo_info(repo: Any, format: str = "text") -> str:
    """
    Format repo structure information.

    Args:
        repo: RepoStructure instance
        format: Output format ("text" or "json")

    Returns:
        Formatted string representation
    """
    if format == "json":
        return json.dumps(
            {
                "apps": [
                    {
                        "path": app.path,
                        "type": app.type,
                        "tools": app.tools,
                        "docker_details": app.docker_details,
                    }
                    for app in repo.apps
                ],
                "infra_stacks": [
                    {
                        "id": stack.id,
                        "path": stack.path,
                        "deployment_order": stack.deployment_order,
                    }
                    for stack in repo.infra_stacks
                ],
            },
            indent=2,
        )

    # Text format
    lines = []
    lines.append("Repository Structure")
    lines.append("")

    if repo.apps:
        lines.append(f"Apps ({len(repo.apps)}):")
        for app in repo.apps:
            tools_str = ", ".join(app.tools) if app.tools else "none"
            docker_str = f" | docker: {app.component}" if app.needs_docker else ""
            lines.append(f"  - {app.path} ({app.type}) (tools: {tools_str}){docker_str}")
        lines.append("")

    if repo.infra_stacks:
        lines.append(f"Infrastructure Stacks ({len(repo.infra_stacks)}):")
        for stack in sorted(repo.infra_stacks, key=lambda s: s.deployment_order):
            lines.append(f"  - {stack.id} ({stack.path}) (order: {stack.deployment_order})")

    return "\n".join(lines)
