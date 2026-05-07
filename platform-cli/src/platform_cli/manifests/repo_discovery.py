"""Discovery logic for data-product repo structure."""
# NOTE: Architectural rules in ARCHITECTURE.md – do not refactor cross-layer.

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

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


from platform_cli.manifests.repo_structure import (
    RepoStructure,
    AppConfig,
    InfraStackConfig,
)
from platform_cli.manifests.validate import validate_apps_config, validate_infra_config


def discover_repo_structure(repo_root: Path) -> Optional[RepoStructure]:
    """
    Detect if repo uses target structure (apps/ and/or infra/).

    Returns:
        RepoStructure if target structure detected (either apps.json or infra.json)
        None if legacy structure (neither apps.json nor infra.json)

    Raises:
        PlatformError if structure detected but validation fails
    """
    apps_json = repo_root / "apps" / "apps.json"
    infra_json = repo_root / "infra" / "infra.json"

    # Check if target structure exists (either manifest is sufficient)
    if not apps_json.exists() and not infra_json.exists():
        return None  # Legacy structure

    apps = []
    stacks = []
    infra_templates_version = ""

    # Load apps config if present
    if apps_json.exists():
        apps_validation = validate_apps_config(apps_json)
        if not apps_validation["valid"]:
            raise PlatformError(
                f"Invalid apps.json: {apps_validation['errors']}",
                code="E_MANIFEST_INVALID",
                reason="apps.json",
            )

        with open(apps_json) as f:
            apps_data = json.load(f)

        apps = [
            AppConfig(
                path=app["path"],
                tools=app.get("tools", []),
                docker_details=app.get("docker_details"),
            )
            for app in apps_data["apps"]
        ]

    # Load infra config if present
    if infra_json.exists():
        infra_validation = validate_infra_config(infra_json)
        if not infra_validation["valid"]:
            raise PlatformError(
                f"Invalid infra.json: {infra_validation['errors']}",
                code="E_MANIFEST_INVALID",
                reason="infra.json",
            )

        with open(infra_json) as f:
            infra_data = json.load(f)

        infra_templates_version = infra_data.get("infra_templates_version", "")

        stacks = [
            InfraStackConfig(
                id=stack["id"],
                path=stack["path"],
                description=stack.get("description", ""),
                deployment_order=int(stack.get("deployment_order", 999)),
            )
            for stack in infra_data["stacks"]
        ]

    repo = RepoStructure(
        repo_root=str(repo_root),
        apps=apps,
        infra_stacks=stacks,
        infra_templates_version=infra_templates_version,
    )

    # Validate structure consistency
    errors = repo.validate_structure()
    if errors:
        raise PlatformError(
            f"Invalid repo structure: {', '.join(errors)}",
            code="E_MANIFEST_INVALID",
            reason="structure_validation",
        )

    return repo
