from __future__ import annotations

from pathlib import Path

import pytest

from platform_cli.core.errors import PlatformError
from platform_cli.manifests.orchestrate_load import load_orchestrate_json_file
from platform_cli.manifests.orchestrate_stage_load import load_stage_contract
from platform_cli.manifests.orchestrate_validate import (
    validate_orchestrate_agent_contract,
    validate_orchestrate_manifest,
    validate_orchestrate_stage_contract,
)
from orchestrate_stage_seed import seed_stage_contracts


def test_load_orchestrate_json_file_requires_object(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(PlatformError) as exc:
        load_orchestrate_json_file(path)

    assert exc.value.code == "E_ORCHESTRATE_CONTRACT_INVALID"


def test_validate_orchestrate_manifest_reports_missing_schema_and_entries() -> None:
    entries, messages = validate_orchestrate_manifest({}, collection_key="agents", source=".ghdp/agents/manifest.json")

    assert entries == []
    assert "schema_version is missing." in messages
    assert ".ghdp/agents/manifest.json does not define any 'agents'." in messages


def test_validate_orchestrate_agent_contract_reports_missing_access_lists() -> None:
    messages = validate_orchestrate_agent_contract(
        {
            "schema_version": "1.0",
            "id": "implementation",
            "role": "delivery_worker",
            "can_block": True,
            "can_retry": True,
            "approval_mode": "on_anomaly",
        },
        source=".ghdp/agents/implementation.json",
    )

    assert ".ghdp/agents/implementation.json field 'allowed_skills' is not a list." in messages
    assert ".ghdp/agents/implementation.json field 'allowed_plugins' is not a list." in messages


def test_validate_orchestrate_stage_contract_reports_missing_core_fields() -> None:
    messages = validate_orchestrate_stage_contract(
        {
            "schema_version": "1.0",
            "messages": {"completed": "done"},
        },
        source=".ghdp/orchestrate/stages/stage11_implementation.json",
    )

    assert ".ghdp/orchestrate/stages/stage11_implementation.json is missing id." in messages
    assert ".ghdp/orchestrate/stages/stage11_implementation.json is missing title." in messages
    assert ".ghdp/orchestrate/stages/stage11_implementation.json is missing owner_agent." in messages


def test_load_stage_contract_reads_repo_stage_recipe(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    seed_stage_contracts(repo_root)

    payload = load_stage_contract(stage_id="stage13_qa_scenario_design", repo_root=repo_root)

    assert payload["id"] == "stage13_qa_scenario_design"
    assert payload["owner_agent"] == "qa-scenario-design"
