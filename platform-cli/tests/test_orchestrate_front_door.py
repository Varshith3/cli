from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from platform_cli.cli import app
from orchestrate_stage_seed import seed_stage_contracts
from platform_cli.tools.orchestrate_front_door import run_front_door_gates
from platform_cli.tools.orchestrate_runtime import start_orchestrate_run


runner = CliRunner()


def _seed_orchestrate_contract(repo_root: Path) -> None:
    (repo_root / ".git").mkdir()
    (repo_root / ".ghdp" / "agents").mkdir(parents=True, exist_ok=True)
    (repo_root / ".ghdp" / "skills").mkdir(parents=True, exist_ok=True)
    (repo_root / ".ghdp" / "plugins").mkdir(parents=True, exist_ok=True)
    (repo_root / ".ghdp" / "memory").mkdir(parents=True, exist_ok=True)
    (repo_root / ".ghdp" / "orchestrate").mkdir(parents=True, exist_ok=True)
    (repo_root / ".ghdp" / "frbr").mkdir(parents=True, exist_ok=True)

    (repo_root / ".ghdp" / "agents" / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "agents": [
                    {
                        "id": "orchestrator",
                        "role": "control_plane",
                        "summary": "Owns orchestration runtime.",
                        "contract_path": ".ghdp/agents/orchestrator.json",
                    },
                    {
                        "id": "ticket-intake",
                        "role": "input_clarifier",
                        "summary": "Checks sufficiency before planning.",
                        "contract_path": ".ghdp/agents/ticket-intake.json",
                    },
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (repo_root / ".ghdp" / "agents" / "orchestrator.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "id": "orchestrator",
                "role": "control_plane",
                "stages_owned": ["stage0_trigger"],
                "allowed_skills": ["traceability-and-resume"],
                "allowed_plugins": ["native-memory-filesystem"],
                "produces_artifacts": ["branch_state.json"],
                "approval_mode": "policy_driven",
                "can_block": True,
                "can_retry": True,
                "prompt_contract": ["Own runtime state and keep branch artifacts in sync."],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (repo_root / ".ghdp" / "agents" / "ticket-intake.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "id": "ticket-intake",
                "role": "input_clarifier",
                "stages_owned": ["stage2_intake_sufficiency"],
                "allowed_skills": ["ticket-intake-sufficiency"],
                "allowed_plugins": [],
                "produces_artifacts": ["intake_summary"],
                "approval_mode": "on_insufficiency",
                "can_block": True,
                "can_retry": False,
                "prompt_contract": ["Check whether intake is sufficient before planning."],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (repo_root / ".ghdp" / "skills" / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "skills": [
                    {"id": "ticket-intake-sufficiency", "purpose": "Parse intake."},
                    {"id": "traceability-and-resume", "purpose": "Persist orchestration state."},
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (repo_root / ".ghdp" / "plugins" / "manifest.json").write_text(
        json.dumps(
            {"schema_version": "1.0", "plugins": [{"id": "native-memory-filesystem", "summary": "Shared memory."}]},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (repo_root / ".ghdp" / "memory" / "manifest.json").write_text(
        json.dumps({"schema_version": "1.0", "partitions": [{"id": "shared"}]}, indent=2) + "\n",
        encoding="utf-8",
    )
    for rel_path in (
        ".ghdp/agents/AGENTS.md",
        ".ghdp/skills/SKILLS.md",
        ".ghdp/plugins/PLUGINS.md",
        ".ghdp/memory/README.md",
        ".ghdp/orchestrate/README.md",
        ".ghdp/memory/shared/README.md",
        ".ghdp/memory/context/README.md",
    ):
        (repo_root / rel_path).parent.mkdir(parents=True, exist_ok=True)
        (repo_root / rel_path).write_text("# stub\n", encoding="utf-8")
    (repo_root / ".ghdp" / "orchestrate" / "phases.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "single_phase_default": True,
                "max_impacted_areas_per_phase": 5,
                "max_capabilities_per_phase": 4,
                "force_multi_phase_keywords": ["phase"],
                "restart_triggers": {"too_many_impacted_areas": True, "too_many_asset_targets": True},
                "restart_destinations": {"too_many_impacted_areas": "stage_c_front_door_gates", "too_many_asset_targets": "independent_asset_lifecycle"},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    seed_stage_contracts(repo_root)

    (repo_root / ".ghdp" / "frbr" / "intent.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "ticket_key": "EPPE-7391",
                "branch_name": "feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory",
                "summary": "Implement the phase 1 orchestrator foundation for repo-level skills, plugins, agents, and native memory.",
                "intent": (
                    "Scope\n"
                    "- define repo-level orchestrator contracts\n"
                    "- add runtime bootstrap and front-door gates\n"
                    "\n"
                    "Acceptance Criteria\n"
                    "- stage c front-door gates should classify work type and autonomy\n"
                    "- poa should be refreshed with capability matches and impacted areas\n"
                ),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_front_door_gates_refresh_poa_and_runtime_state(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_orchestrate_contract(repo_root)
    branch_name = "feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory"
    monkeypatch.setattr("platform_cli.tools.orchestrate_runtime.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_front_door.current_branch_name", lambda _repo_root: branch_name)

    start = start_orchestrate_run(repo_root=repo_root)
    result = run_front_door_gates(repo_root=repo_root)

    assert start.active_run_key == result.active_run_key
    assert result.work_type == "new_feature"
    assert result.autonomy_level == "semi_autonomous"
    assert result.intake_sufficient is True
    assert result.spec_action == "create_new_spec"
    assert result.phase_mode == "multi_phase"
    assert result.phase_count >= 2
    assert result.status == "paused"

    runtime_root = Path(result.branch_runtime_root)
    branch_state = json.loads((runtime_root / "branch_state.json").read_text(encoding="utf-8"))
    assert branch_state["current_stage"] == "stage_c_front_door_gates"
    assert branch_state["next_action"] == "Run Stage D architecture review and UX/DX review using the refreshed POA."

    poa_text = (runtime_root / "poa.md").read_text(encoding="utf-8")
    assert "## Stage C Front-Door Gate Outputs" in poa_text
    assert "Work type: `new_feature`" in poa_text


def test_orchestrate_front_door_cli_reports_stage_c_result(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_orchestrate_contract(repo_root)
    branch_name = "feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory"
    monkeypatch.setattr("platform_cli.tools.orchestrate_runtime.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_front_door.current_branch_name", lambda _repo_root: branch_name)

    start_orchestrate_run(repo_root=repo_root)
    result = runner.invoke(app, ["orchestrate", "front-door", "--repo-root", str(repo_root)])

    assert result.exit_code == 0
    assert "work_type             : new_feature" in result.output
    assert "current_stage         : stage_c_front_door_gates" in result.output


def test_front_door_routes_asset_only_work_to_asset_lifecycle(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_orchestrate_contract(repo_root)
    branch_name = "feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory"
    monkeypatch.setattr("platform_cli.tools.orchestrate_runtime.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_front_door.current_branch_name", lambda _repo_root: branch_name)

    intent_path = repo_root / ".ghdp" / "frbr" / "intent.json"
    payload = json.loads(intent_path.read_text(encoding="utf-8"))
    payload["summary"] = "Revise the existing team toolset asset to raise the Codex minimum version requirement only."
    payload["intent"] = "Only update the toolset asset and do not expand into broader code implementation."
    intent_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    start_orchestrate_run(repo_root=repo_root)
    result = run_front_door_gates(repo_root=repo_root)

    assert result.delivery_route == "asset_only"
    assert result.asset_operation == "update_versioned_asset"
    assert result.spec_action == "route_asset_lifecycle:update_versioned_asset"
    assert result.restart_recommendation == "independent_asset_lifecycle"
