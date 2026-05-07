from __future__ import annotations

import pytest

from platform_cli.core.errors import PlatformError
from platform_cli.manifests.repo_ready_validate import (
    validate_guardrails_config,
    validate_lock_config,
    validate_repo_config,
    validate_runbook_config,
)


def _vocab() -> dict:
    return {
        "schema_version": "1.0",
        "repo_types": ["unknown", "backend"],
        "traits": ["api", "cli"],
        "risk_tiers": ["low", "medium", "high"],
        "execution_modes": ["local-ok", "sandbox-only", "ci-only"],
        "tools": ["claude", "codex"],
    }


def test_validate_repo_config_rejects_more_than_four_traits() -> None:
    config = {
        "schema_version": "1.0",
        "repo": {"name": "demo", "type": "backend", "traits": ["api", "cli", "api", "cli", "api"]},
        "classification": {"source": "explicit", "evidence": []},
        "risk": {"tier": "medium"},
        "execution": {"mode": "sandbox-only"},
        "enabled": {"tools": [], "teams": [], "subagents": False},
        "metadata": {"managed_by": "ghdp", "template_version": "1.0.0"},
    }

    with pytest.raises(PlatformError) as exc:
        validate_repo_config(config, _vocab())

    assert exc.value.code == "E_REPO_READY_INVALID_VALUE"


def test_validate_guardrails_requires_big_change_approval() -> None:
    guardrails = {
        "schema_version": "1.0",
        "baseline": {"enforced": True},
        "deny": {"folders": [], "patterns": [], "commands": []},
        "skills": {"allow": [], "deny": []},
        "mcp": {"allow": [], "deny": []},
        "approvals": {"confirm_before": []},
        "data_safety": {"mode": "standard", "pii": False, "phi": False},
        "metadata": {"managed_by": "ghdp", "template_version": "1.0.0"},
    }

    with pytest.raises(PlatformError) as exc:
        validate_guardrails_config(guardrails)

    assert exc.value.code == "E_REPO_READY_INVALID_VALUE"


def test_validate_lock_requires_phase1_files() -> None:
    lock_data = {
        "schema_version": "1.0",
        "template_set": "repo_ready",
        "template_version": "1.0.0",
        "applied_at": "2026-03-20T00:00:00+00:00",
        "managed_files": [".ghdp/config.yaml"],
        "approved_by": [],
        "known_bad": {"skills": [], "mcp_servers": []},
    }

    with pytest.raises(PlatformError) as exc:
        validate_lock_config(lock_data, expected_managed_files=[".ghdp/config.yaml", ".ghdp/guardrails.yaml"])

    assert exc.value.code == "E_REPO_READY_INVALID_VALUE"


def test_validate_runbook_requires_command_lists() -> None:
    runbook = {
        "schema_version": "1.0",
        "commands": {
            "build": [],
            "test": [],
            "lint": "ruff check .",
            "format": [],
            "start": [],
            "dev": [],
        },
        "entrypoints": [],
        "services": [],
        "env_vars": [],
        "notes": {"status": "pending-user-review"},
        "metadata": {"managed_by": "ghdp", "template_version": "1.0.0"},
    }

    with pytest.raises(PlatformError) as exc:
        validate_runbook_config(runbook)

    assert exc.value.code == "E_REPO_READY_INVALID_SCHEMA"
