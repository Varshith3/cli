from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from platform_cli.cli import app
from platform_cli.exec.runner import CmdResult
from platform_cli.orchestrate_kernel.stage19b_published_prerelease_retest import run_published_prerelease_retest_stage
from platform_cli.orchestrate_kernel.stage20_release_notes import run_release_notes_refresh_stage
from platform_cli.orchestrate_kernel.stage21_pr_external import run_pr_external_integration_stage
from platform_cli.orchestrate_kernel.stage22_traceability import run_traceability_capture_stage
from platform_cli.tools.orchestrate_contract import inspect_orchestrate_contract
from platform_cli.tools.orchestrate_merge_hygiene import run_finalize_merge_hygiene, run_verify_merge_hygiene
from platform_cli.tools.orchestrate_prerelease import run_prerelease_stage
from test_orchestrate_kernel_and_closeout import _seed_and_run_to_stage18


runner = CliRunner()


def _seed_and_run_to_stage22(repo_root: Path, monkeypatch) -> None:
    _seed_and_run_to_stage18(repo_root, monkeypatch)
    agents_manifest_path = repo_root / ".ghdp" / "agents" / "manifest.json"
    agents_manifest = json.loads(agents_manifest_path.read_text(encoding="utf-8"))
    if not any(item.get("id") == "published-prerelease-validation" for item in agents_manifest.get("agents", [])):
        agents_manifest["agents"].append(
            {
                "id": "published-prerelease-validation",
                "role": "published_artifact_validator",
                "summary": "Validate published prerelease assets.",
                "contract_path": ".ghdp/agents/published-prerelease-validation.json",
            }
        )
        agents_manifest_path.write_text(json.dumps(agents_manifest, indent=2) + "\n", encoding="utf-8")
        (repo_root / ".ghdp" / "agents" / "published-prerelease-validation.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "id": "published-prerelease-validation",
                    "role": "published_artifact_validator",
                    "stages_owned": ["stage19b_published_prerelease_retest"],
                    "allowed_skills": ["published-prerelease-retest"],
                    "allowed_plugins": ["github-release-gh"],
                    "produces_artifacts": ["published_prerelease_validation"],
                    "approval_mode": "always",
                    "can_block": True,
                    "can_retry": True,
                    "prompt_contract": ["Validate the actual published prerelease artifact before PR progression."],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    skills_manifest_path = repo_root / ".ghdp" / "skills" / "manifest.json"
    skills_manifest = json.loads(skills_manifest_path.read_text(encoding="utf-8"))
    if not any(item.get("id") == "published-prerelease-retest" for item in skills_manifest.get("skills", [])):
        skills_manifest["skills"].append({"id": "published-prerelease-retest"})
        skills_manifest_path.write_text(json.dumps(skills_manifest, indent=2) + "\n", encoding="utf-8")
    (repo_root / ".ghdp" / "orchestrate" / "audit-export.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "destination_mode": "local",
                "local": {"output_dir": "tmp/orchestrate-audit-exports"},
                "aws_s3": {"enabled": False, "bucket": "", "prefix": "", "region": "", "profile": ""},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("platform_cli.orchestrate_kernel.stage20_release_notes._commit_and_push_notes", lambda *_args, **_kwargs: "abc123")
    run_release_notes_refresh_stage(repo_root=repo_root)
    monkeypatch.setattr(
        "platform_cli.tools.orchestrate_prerelease.plan_binaries_release",
        lambda **_: type(
            "Plan",
            (),
            {
                "tag": "v0.0.0-test",
                "repo_name_with_owner": "gh/test",
                "to_dict": lambda self: {
                    "tag": "v0.0.0-test",
                    "repo_name_with_owner": "gh/test",
                    "build_target": {"asset": "ghdp-windows-amd64.exe"},
                },
            },
        )(),
    )
    monkeypatch.setattr("platform_cli.tools.orchestrate_prerelease.ensure_binaries_release", lambda _plan: {"tag": "v0.0.0-test"})
    run_prerelease_stage(repo_root=repo_root)
    monkeypatch.setattr("platform_cli.orchestrate_kernel.stage19b_published_prerelease_retest._download_release_asset", lambda **_: repo_root / "downloaded-ghdp.exe")
    monkeypatch.setattr(
        "platform_cli.orchestrate_kernel.stage19b_published_prerelease_retest._validate_downloaded_asset",
        lambda **_: {
            "version": CmdResult(cmd=["ghdp.exe", "--version"], returncode=0, stdout="ghdp 0.0.0", stderr=""),
            "status": CmdResult(cmd=["ghdp.exe", "--json", "orchestrate", "status"], returncode=0, stdout='{"contract_ready": true}', stderr=""),
        },
    )
    (repo_root / "downloaded-ghdp.exe").write_text("stub", encoding="utf-8")
    run_published_prerelease_retest_stage(repo_root=repo_root)
    monkeypatch.setattr("platform_cli.orchestrate_kernel.stage21_pr_external._validate_branch_hygiene", lambda _context: {"status": "completed", "develop_sha": "abc", "merge_base": "abc", "merge_commits": []})
    monkeypatch.setattr("platform_cli.orchestrate_kernel.stage21_pr_external._ensure_pr", lambda _context: "https://github.com/example/repo/pull/1")
    monkeypatch.setattr("platform_cli.orchestrate_kernel.stage21_pr_external._comment_prerelease_on_pr", lambda **_: "posted via gh")
    monkeypatch.setattr("platform_cli.orchestrate_kernel.stage21_pr_external._comment_on_jira", lambda *_args, **_kwargs: "posted via acli")
    run_pr_external_integration_stage(repo_root=repo_root)
    run_traceability_capture_stage(repo_root=repo_root)


def test_finalize_prunes_runtime_and_promotes_memory(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_and_run_to_stage22(repo_root, monkeypatch)

    result = run_finalize_merge_hygiene(repo_root=repo_root)

    assert result.runtime_removed is True
    runtime_root = repo_root / ".ghdp" / "orchestrate" / "branches" / result.branch_slug
    assert runtime_root.exists() is False
    assert Path(result.memory_summary_path).exists()
    receipt = json.loads(Path(result.memory_receipt_path).read_text(encoding="utf-8"))
    assert receipt["runtime_pruned"] is True
    assert Path(result.archive_path).exists()

    verify = run_verify_merge_hygiene(repo_root=repo_root)
    assert verify.merge_safe is True
    status = inspect_orchestrate_contract(repo_root=repo_root)
    assert status.contract_ready is True
    assert status.branch_runtime_mode == "finalized"
    assert status.active_run_key == result.active_run_key


def test_verify_merge_hygiene_fails_before_finalize(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_and_run_to_stage22(repo_root, monkeypatch)

    result = runner.invoke(app, ["orchestrate", "verify-merge-hygiene", "--repo-root", str(repo_root)])

    assert result.exit_code != 0
    assert result.exception is not None
    assert "Branch is not merge-hygienic yet" in str(result.exception)


def test_finalize_and_verify_cli_surface(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_and_run_to_stage22(repo_root, monkeypatch)

    finalize_result = runner.invoke(app, ["orchestrate", "finalize", "--repo-root", str(repo_root)])
    verify_result = runner.invoke(app, ["orchestrate", "verify-merge-hygiene", "--repo-root", str(repo_root)])

    assert finalize_result.exit_code == 0
    assert "runtime_removed       : True" in finalize_result.output
    assert verify_result.exit_code == 0
    assert "merge_safe            : True" in verify_result.output
