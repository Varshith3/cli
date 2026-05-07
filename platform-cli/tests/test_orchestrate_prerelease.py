from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from typer.testing import CliRunner

from platform_cli.cli import app
from platform_cli.core.errors import PlatformError
from platform_cli.exec.runner import CmdResult
from platform_cli.tools.orchestrate_binary_validation import run_packaged_artifact_validation_stage
from platform_cli.tools.orchestrate_prerelease import run_prerelease_stage
from platform_cli.tools.orchestrate_release_readiness import run_release_readiness_stage
from test_orchestrate_binary_validation import _seed_and_run_to_stage16


runner = CliRunner()


@dataclass
class _FakePlan:
    tag: str = "v0.0.0-test"
    repo_name_with_owner: str = "gh-org-data-platform/dp-tools-local-setup"

    def to_dict(self) -> dict[str, object]:
        return {"tag": self.tag, "repo_name_with_owner": self.repo_name_with_owner}


def _seed_and_run_to_stage18(repo_root: Path, monkeypatch) -> None:
    _seed_and_run_to_stage16(repo_root, monkeypatch)
    monkeypatch.setattr(
        "platform_cli.tools.orchestrate_binary_validation._run_packaged_validation",
        lambda **_: {
            "version": CmdResult(cmd=["ghdp", "--version"], returncode=0, stdout="ghdp 0.0.0 (beta)", stderr=""),
            "status": CmdResult(cmd=["ghdp", "--json", "orchestrate", "status"], returncode=0, stdout='{"contract_ready": true}', stderr=""),
        },
    )
    run_packaged_artifact_validation_stage(repo_root=repo_root)
    run_release_readiness_stage(repo_root=repo_root)


def test_prerelease_stage_records_blocked_release_engine_reason(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_and_run_to_stage18(repo_root, monkeypatch)
    monkeypatch.setattr("platform_cli.tools.orchestrate_prerelease.plan_binaries_release", lambda **_: _FakePlan())
    monkeypatch.setattr(
        "platform_cli.tools.orchestrate_prerelease.ensure_binaries_release",
        lambda _plan: (_ for _ in ()).throw(PlatformError("notes stale", code="E_RELEASE_NOTES_STALE", reason="release_notes")),
    )

    result = run_prerelease_stage(repo_root=repo_root)

    assert result.current_stage == "stage19_prerelease_creation"
    assert result.prerelease_agent == "release-prerelease"
    assert result.status == "blocked"
    assert result.blocked_reason.startswith("E_RELEASE_NOTES_STALE")


def test_orchestrate_prerelease_cli_reports_blocked_reason(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_and_run_to_stage18(repo_root, monkeypatch)
    monkeypatch.setattr("platform_cli.tools.orchestrate_prerelease.plan_binaries_release", lambda **_: _FakePlan())
    monkeypatch.setattr(
        "platform_cli.tools.orchestrate_prerelease.ensure_binaries_release",
        lambda _plan: (_ for _ in ()).throw(PlatformError("notes stale", code="E_RELEASE_NOTES_STALE", reason="release_notes")),
    )

    result = runner.invoke(app, ["orchestrate", "prerelease", "--repo-root", str(repo_root)])

    assert result.exit_code == 0
    assert "current_stage         : stage19_prerelease_creation" in result.output
    assert "blocked_reason        : E_RELEASE_NOTES_STALE:release_notes" in result.output
