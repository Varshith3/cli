from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from platform_cli.cli import app
from platform_cli.exec.runner import CmdResult
from platform_cli.tools.orchestrate_binary_validation import run_packaged_artifact_validation_stage
from platform_cli.tools.orchestrate_release_readiness import run_release_readiness_stage
from test_orchestrate_binary_validation import _seed_and_run_to_stage16


runner = CliRunner()


def _seed_and_run_to_stage17(repo_root: Path, monkeypatch) -> None:
    _seed_and_run_to_stage16(repo_root, monkeypatch)
    monkeypatch.setattr(
        "platform_cli.tools.orchestrate_binary_validation._run_packaged_validation",
        lambda **_: {
            "version": CmdResult(cmd=["ghdp", "--version"], returncode=0, stdout="ghdp 0.0.0 (beta)", stderr=""),
            "status": CmdResult(cmd=["ghdp", "--json", "orchestrate", "status"], returncode=0, stdout='{"contract_ready": true}', stderr=""),
        },
    )
    run_packaged_artifact_validation_stage(repo_root=repo_root)


def test_release_readiness_stage_accepts_complete_evidence(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_and_run_to_stage17(repo_root, monkeypatch)

    result = run_release_readiness_stage(repo_root=repo_root)

    assert result.current_stage == "stage18_release_readiness"
    assert result.readiness_agent == "release-readiness"
    assert result.status == "paused"
    assert not result.blocking_findings

    run_root = Path(result.branch_runtime_root) / "runs" / result.active_run_key
    assert (run_root / "release_readiness_prompt.md").exists()
    assert (run_root / "release_readiness_bindings.json").exists()
    assert (run_root / "release_readiness_summary.md").exists()


def test_orchestrate_release_readiness_cli_reports_go_no_go(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_and_run_to_stage17(repo_root, monkeypatch)

    result = runner.invoke(app, ["orchestrate", "release-readiness", "--repo-root", str(repo_root)])

    assert result.exit_code == 0
    assert "current_stage         : stage18_release_readiness" in result.output
    assert "readiness_agent       : release-readiness" in result.output
