from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from platform_cli.cli import app
from orchestrate_stage_seed import seed_stage_contracts
from platform_cli.tools.orchestrate_front_door import run_front_door_gates
from platform_cli.tools.orchestrate_review import run_review_layer
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
    (repo_root / "platform-cli" / "src" / "platform_cli" / "manifests").mkdir(parents=True, exist_ok=True)
    (repo_root / "platform-cli" / "src" / "platform_cli" / "tools").mkdir(parents=True, exist_ok=True)

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
        (repo_root / rel_path).parent.mkdir(parents=True, exist_ok=True)
        (repo_root / rel_path).write_text("# stub\n", encoding="utf-8")

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

    (repo_root / "platform-cli" / "src" / "platform_cli" / "manifests" / "orchestrate_validate.py").write_text(
        "# stub\n",
        encoding="utf-8",
    )
    (repo_root / "platform-cli" / "src" / "platform_cli" / "tools" / "orchestrate_front_door.py").write_text(
        "# stub\n",
        encoding="utf-8",
    )
    seed_stage_contracts(repo_root)


def test_review_layer_accepts_stage_c_plan_and_updates_runtime(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_orchestrate_contract(repo_root)
    branch_name = "feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory"
    monkeypatch.setattr("platform_cli.tools.orchestrate_runtime.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_front_door.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_review.current_branch_name", lambda _repo_root: branch_name)

    start_orchestrate_run(repo_root=repo_root)
    run_front_door_gates(repo_root=repo_root)
    result = run_review_layer(repo_root=repo_root)

    assert result.blocking_findings == 0
    assert result.status == "paused"
    assert result.current_stage == "stage_d_review_layer"
    assert any(item.startswith("ACCEPTED:") for item in result.architecture_findings)
    assert any(item.startswith("ACCEPTED:") for item in result.uxdx_findings)

    runtime_root = Path(result.branch_runtime_root)
    poa_text = (runtime_root / "poa.md").read_text(encoding="utf-8")
    assert "## Stage D Review Findings" in poa_text


def test_orchestrate_review_cli_reports_review_summary(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_orchestrate_contract(repo_root)
    branch_name = "feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory"
    monkeypatch.setattr("platform_cli.tools.orchestrate_runtime.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_front_door.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_review.current_branch_name", lambda _repo_root: branch_name)

    start_orchestrate_run(repo_root=repo_root)
    run_front_door_gates(repo_root=repo_root)
    result = runner.invoke(app, ["orchestrate", "review", "--repo-root", str(repo_root)])

    assert result.exit_code == 0
    assert "current_stage         : stage_d_review_layer" in result.output
    assert "blocking_findings     : 0" in result.output
