from __future__ import annotations

import importlib.resources as pkg_resources
import json
from dataclasses import dataclass

from platform_cli.core.errors import PlatformError


@dataclass(frozen=True)
class CreateBranchPolicy:
    branch_prefix: str
    intent_repo_path: str
    allowed_types: tuple[str, ...]
    type_aliases: dict[str, str]
    jira_comment_template: str


def load_create_branch_policy() -> CreateBranchPolicy:
    try:
        raw = (
            pkg_resources.files("platform_cli.resources")
            / "policy"
            / "create-branch.json"
        ).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PlatformError(
            "Missing create-branch policy resource: policy/create-branch.json",
            code="E_CREATE_BRANCH_POLICY_MISSING",
            reason="create_branch_policy",
        )

    try:
        payload = json.loads(raw)
    except Exception as e:
        raise PlatformError(
            f"Failed to parse create-branch policy: {e}",
            code="E_CREATE_BRANCH_POLICY_INVALID",
            reason="create_branch_policy",
        )

    branch_types = payload.get("branch_types") if isinstance(payload, dict) else None
    jira = payload.get("jira") if isinstance(payload, dict) else None
    if not isinstance(branch_types, list) or not isinstance(jira, dict):
        raise PlatformError(
            "Create-branch policy is missing branch_types or jira sections.",
            code="E_CREATE_BRANCH_POLICY_INVALID",
            reason="create_branch_policy",
        )

    allowed_types: list[str] = []
    aliases: dict[str, str] = {}
    for item in branch_types:
        if not isinstance(item, dict):
            continue
        canonical = str(item.get("canonical") or "").strip().upper()
        if not canonical:
            continue
        allowed_types.append(canonical)
        aliases[canonical.lower()] = canonical
        for alias in item.get("aliases", []):
            alias_key = str(alias or "").strip().lower()
            if alias_key:
                aliases[alias_key] = canonical

    if not allowed_types:
        raise PlatformError(
            "Create-branch policy does not define any branch types.",
            code="E_CREATE_BRANCH_POLICY_INVALID",
            reason="branch_types",
        )

    comment_template = str(jira.get("comment_template") or "").strip()
    if not comment_template:
        raise PlatformError(
            "Create-branch policy does not define a Jira comment template.",
            code="E_CREATE_BRANCH_POLICY_INVALID",
            reason="jira_comment_template",
        )

    return CreateBranchPolicy(
        branch_prefix=str(payload.get("branch_prefix") or "feature").strip() or "feature",
        intent_repo_path=str(payload.get("intent_repo_path") or ".ghdp/frbr/intent.json").strip()
        or ".ghdp/frbr/intent.json",
        allowed_types=tuple(allowed_types),
        type_aliases=aliases,
        jira_comment_template=comment_template,
    )
