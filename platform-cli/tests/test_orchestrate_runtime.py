from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from platform_cli.cli import app
from platform_cli.manifests.orchestrate_policy_load import load_orchestrate_policy
from orchestrate_stage_seed import seed_stage_contracts
from platform_cli.tools.orchestrate_runtime import (
    handoff_orchestrate_run,
    resume_orchestrate_run,
    start_orchestrate_run,
)


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
                    }
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
    (repo_root / ".ghdp" / "skills" / "manifest.json").write_text(
        json.dumps({"schema_version": "1.0", "skills": [{"id": "traceability-and-resume"}]}, indent=2) + "\n",
        encoding="utf-8",
    )
    (repo_root / ".ghdp" / "plugins" / "manifest.json").write_text(
        json.dumps({"schema_version": "1.0", "plugins": [{"id": "native-memory-filesystem"}]}, indent=2) + "\n",
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
    ):
        (repo_root / rel_path).write_text("# stub\n", encoding="utf-8")
    seed_stage_contracts(repo_root)

    (repo_root / ".ghdp" / "frbr" / "intent.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "ticket_key": "EPPE-7391",
                "branch_name": "feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_load_orchestrate_policy_packaged_default() -> None:
    policy, source = load_orchestrate_policy()

    assert policy["runtime"]["default_execution_mode"] == "auto"
    assert source.startswith("packaged:")


def test_start_orchestrate_run_bootstraps_branch_runtime(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_orchestrate_contract(repo_root)
    branch_name = "feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory"
    monkeypatch.setattr("platform_cli.tools.orchestrate_runtime.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_contract.current_branch_name", lambda _repo_root: branch_name)

    result = start_orchestrate_run(repo_root=repo_root)

    assert result.created_new_run is True
    assert result.status == "in_progress"
    assert (Path(result.branch_runtime_root) / "branch_state.json").exists()
    assert (Path(result.branch_runtime_root) / "runs" / result.active_run_key / "run_state.json").exists()


def test_resume_and_handoff_update_active_run(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_orchestrate_contract(repo_root)
    branch_name = "feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory"
    monkeypatch.setattr("platform_cli.tools.orchestrate_runtime.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_contract.current_branch_name", lambda _repo_root: branch_name)

    start = start_orchestrate_run(repo_root=repo_root)
    resumed = resume_orchestrate_run(repo_root=repo_root)
    handed_off = handoff_orchestrate_run(
        repo_root=repo_root,
        summary="Stage B core runtime slice implemented.",
        next_action="Review runtime behavior and continue into the next orchestrator stage.",
    )

    assert resumed.active_run_key == start.active_run_key
    assert handed_off.status == "paused"
    handoff_text = (Path(start.branch_runtime_root) / "handoff.md").read_text(encoding="utf-8")
    assert "Stage B core runtime slice implemented." in handoff_text
    assert "Review runtime behavior and continue into the next orchestrator stage." in handoff_text


def test_orchestrate_start_cli_reports_created_run(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_orchestrate_contract(repo_root)
    branch_name = "feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory"
    monkeypatch.setattr("platform_cli.tools.orchestrate_runtime.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_contract.current_branch_name", lambda _repo_root: branch_name)

    result = runner.invoke(app, ["orchestrate", "start", "--repo-root", str(repo_root)])

    assert result.exit_code == 0
    assert "created_new_run       : True" in result.output
