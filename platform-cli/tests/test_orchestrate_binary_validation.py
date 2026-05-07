from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from platform_cli.cli import app
from platform_cli.exec.runner import CmdResult
from platform_cli.tools.orchestrate_binary_validation import run_packaged_artifact_validation_stage
from platform_cli.tools.orchestrate_coverage import run_coverage_stage
from platform_cli.tools.orchestrate_test_execution import run_developer_test_execution_stage
from test_orchestrate_coverage import _seed_and_run_to_stage14


runner = CliRunner()


def _seed_and_run_to_stage16(repo_root: Path, monkeypatch) -> None:
    _seed_and_run_to_stage14(repo_root, monkeypatch)
    (repo_root / "platform-cli").mkdir(exist_ok=True)
    (repo_root / "platform-cli" / "pyproject.toml").write_text("[project]\nname = 'ghdp'\nversion = '0.0.0'\n", encoding="utf-8")
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
    run_developer_test_execution_stage(repo_root=repo_root)


def test_binary_validation_stage_records_packaged_smoke_result(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_and_run_to_stage16(repo_root, monkeypatch)
    monkeypatch.setattr(
        "platform_cli.tools.orchestrate_binary_validation._run_packaged_validation",
        lambda **_: {
            "version": CmdResult(cmd=["ghdp", "--version"], returncode=0, stdout="GHDP :: Guardant Dev Platform CLI v0.0.0 -- Beta Version", stderr=""),
            "status": CmdResult(cmd=["ghdp", "--json", "orchestrate", "status"], returncode=0, stdout='{"contract_ready": true}', stderr=""),
        },
    )

    result = run_packaged_artifact_validation_stage(repo_root=repo_root)
    rerun = run_packaged_artifact_validation_stage(repo_root=repo_root)

    assert result.current_stage == "stage17_packaged_artifact_validation"
    assert result.validation_agent == "binary-validation"
    assert result.package_root.endswith("platform-cli")
    assert result.installed_cli_version
    assert rerun.current_stage == "stage17_packaged_artifact_validation"

    run_root = Path(result.branch_runtime_root) / "runs" / result.active_run_key
    assert (run_root / "binary_validation_prompt.md").exists()
    assert (run_root / "binary_validation_bindings.json").exists()
    assert (run_root / "artifact_validation_result.md").exists()
    assert (run_root / "artifact_validation_summary.md").exists()


def test_orchestrate_binary_validation_cli_reports_version(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_and_run_to_stage16(repo_root, monkeypatch)
    monkeypatch.setattr(
        "platform_cli.tools.orchestrate_binary_validation._run_packaged_validation",
        lambda **_: {
            "version": CmdResult(cmd=["ghdp", "--version"], returncode=0, stdout="GHDP :: Guardant Dev Platform CLI v0.0.0 -- Beta Version", stderr=""),
            "status": CmdResult(cmd=["ghdp", "--json", "orchestrate", "status"], returncode=0, stdout='{"contract_ready": true}', stderr=""),
        },
    )

    result = runner.invoke(app, ["orchestrate", "binary-validate", "--repo-root", str(repo_root)])

    assert result.exit_code == 0
    assert "current_stage         : stage17_packaged_artifact_validation" in result.output
    assert "validation_agent      : binary-validation" in result.output
    assert "installed_cli_version" in result.output
