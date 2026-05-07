from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from platform_cli.cli import app
from platform_cli.exec.runner import CmdResult
from platform_cli.tools.orchestrate_coverage import run_coverage_stage
from platform_cli.tools.orchestrate_test_execution import run_developer_test_execution_stage
from test_orchestrate_coverage import _seed_and_run_to_stage14


runner = CliRunner()


def test_test_execution_stage_runs_repo_backed_validation(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_and_run_to_stage14(repo_root, monkeypatch)
    run_coverage_stage(repo_root=repo_root)
    monkeypatch.setattr(
        "platform_cli.tools.orchestrate_test_execution._run_pytest_with_lock",
        lambda **_: CmdResult(
            cmd=["python", "-m", "pytest"],
            returncode=0,
            stdout="5 passed in 0.20s",
            stderr="",
        ),
    )

    result = run_developer_test_execution_stage(repo_root=repo_root)

    assert result.current_stage == "stage16_developer_test_execution"
    assert result.execution_agent == "developer-test-execution"
    assert result.execution_mode == "sequential"
    assert result.executed_tests
    assert not result.failed_tests

    run_root = Path(result.branch_runtime_root) / "runs" / result.active_run_key
    assert (run_root / "test_execution_prompt.md").exists()
    assert (run_root / "test_execution_bindings.json").exists()
    assert (run_root / "test_execution_log.md").exists()
    assert (run_root / "test_execution_summary.md").exists()


def test_orchestrate_test_execution_cli_reports_targets(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_and_run_to_stage14(repo_root, monkeypatch)
    run_coverage_stage(repo_root=repo_root)
    monkeypatch.setattr(
        "platform_cli.tools.orchestrate_test_execution._run_pytest_with_lock",
        lambda **_: CmdResult(
            cmd=["python", "-m", "pytest"],
            returncode=0,
            stdout="5 passed in 0.20s",
            stderr="",
        ),
    )

    result = runner.invoke(app, ["orchestrate", "test-execution", "--repo-root", str(repo_root)])

    assert result.exit_code == 0
    assert "current_stage         : stage16_developer_test_execution" in result.output
    assert "execution_agent       : developer-test-execution" in result.output
    assert "executed_tests:" in result.output
