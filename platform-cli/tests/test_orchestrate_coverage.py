from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from orchestrate_stage_seed import seed_stage_contracts
from platform_cli.cli import app
from platform_cli.exec.runner import run_cmd
from platform_cli.tools.orchestrate_commit_push import run_commit_push_stage
from platform_cli.tools.orchestrate_coverage import run_coverage_stage
from platform_cli.tools.orchestrate_execution import run_execution_prep
from platform_cli.tools.orchestrate_front_door import run_front_door_gates
from platform_cli.tools.orchestrate_implementation import run_implementation_stage
from platform_cli.tools.orchestrate_qa import run_qa_scenario_stage
from platform_cli.tools.orchestrate_regression import run_regression_stage
from platform_cli.tools.orchestrate_review import run_review_layer
from platform_cli.tools.orchestrate_runtime import start_orchestrate_run


runner = CliRunner()


def _seed_orchestrate_contract(repo_root: Path) -> None:
    (repo_root / ".git").mkdir(exist_ok=True)
    for rel in (
        ".ghdp/agents",
        ".ghdp/skills",
        ".ghdp/plugins",
        ".ghdp/memory",
        ".ghdp/orchestrate",
        ".ghdp/frbr",
        "platform-cli/src/platform_cli/manifests",
        "platform-cli/src/platform_cli/tools",
    ):
        (repo_root / rel).mkdir(parents=True, exist_ok=True)

    from test_orchestrate_regression import _seed_orchestrate_contract as seed_contract

    seed_contract(repo_root)


def _wire_branch(monkeypatch) -> str:
    branch_name = "feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory"
    monkeypatch.setattr("platform_cli.tools.orchestrate_runtime.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_front_door.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_review.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_execution.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_implementation.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_commit_push.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_qa.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_regression.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_coverage.current_branch_name", lambda _repo_root: branch_name)
    return branch_name


def _seed_and_run_to_stage14(repo_root: Path, monkeypatch) -> None:
    branch_name = _wire_branch(monkeypatch)
    remote_root = repo_root.parent / "remote.git"
    run_cmd(["git", "init", "-b", branch_name], cwd=repo_root, check=True)
    run_cmd(["git", "init", "--bare", str(remote_root)], cwd=repo_root.parent, check=True)
    _seed_orchestrate_contract(repo_root)
    run_cmd(["git", "add", "-A"], cwd=repo_root, check=True)
    run_cmd(
        [
            "git",
            "-c",
            "user.name=Seed User",
            "-c",
            "user.email=seed@example.com",
            "commit",
            "-m",
            "Seed orchestrate repo",
        ],
        cwd=repo_root,
        check=True,
    )
    run_cmd(["git", "remote", "add", "origin", str(remote_root)], cwd=repo_root, check=True)
    run_cmd(["git", "push", "-u", "origin", branch_name], cwd=repo_root, check=True)

    start_orchestrate_run(repo_root=repo_root)
    run_front_door_gates(repo_root=repo_root)
    run_review_layer(repo_root=repo_root)
    run_execution_prep(repo_root=repo_root)
    run_implementation_stage(repo_root=repo_root)
    run_commit_push_stage(repo_root=repo_root)
    run_qa_scenario_stage(repo_root=repo_root)
    run_regression_stage(repo_root=repo_root)


def test_coverage_stage_generates_repo_backed_backlog(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_and_run_to_stage14(repo_root, monkeypatch)

    result = run_coverage_stage(repo_root=repo_root)

    assert result.current_stage == "stage15_new_test_coverage"
    assert result.coverage_agent == "test-coverage-authoring"
    assert result.authored_test_count >= 1
    assert "test-coverage-authoring" in result.allowed_skills

    run_root = Path(result.branch_runtime_root) / "runs" / result.active_run_key
    assert (run_root / "coverage_prompt.md").exists()
    assert (run_root / "coverage_bindings.json").exists()
    assert (run_root / "coverage_backlog.md").exists()
    assert (run_root / "coverage_summary.md").exists()


def test_orchestrate_coverage_cli_reports_authored_tests(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_and_run_to_stage14(repo_root, monkeypatch)

    result = runner.invoke(app, ["orchestrate", "coverage", "--repo-root", str(repo_root)])

    assert result.exit_code == 0
    assert "current_stage         : stage15_new_test_coverage" in result.output
    assert "coverage_agent        : test-coverage-authoring" in result.output
    assert "authored_test_count" in result.output
