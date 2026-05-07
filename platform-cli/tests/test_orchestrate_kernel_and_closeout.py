from __future__ import annotations

import json
from pathlib import Path

from platform_cli.manifests.orchestrate_kernel_load import load_kernel_contract, load_scenario_contract, load_topology_contract
from platform_cli.orchestrate_kernel.provider_adapters import resolve_provider_adapter
from platform_cli.orchestrate_kernel.stage19b_published_prerelease_retest import run_published_prerelease_retest_stage
from platform_cli.orchestrate_kernel.stage20_release_notes import run_release_notes_refresh_stage
from platform_cli.orchestrate_kernel.stage21_pr_external import run_pr_external_integration_stage
from platform_cli.orchestrate_kernel.stage22_traceability import run_traceability_capture_stage
from platform_cli.orchestrate_kernel.subagents import plan_execution_waves, run_subagent_scenario
from platform_cli.tools.orchestrate_prerelease import run_prerelease_stage
from platform_cli.tools.orchestrate_release_readiness import run_release_readiness_stage
from platform_cli.tools.orchestrate_binary_validation import run_packaged_artifact_validation_stage
from platform_cli.exec.runner import CmdResult
from test_orchestrate_binary_validation import _seed_and_run_to_stage16


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


def test_kernel_contract_loads_from_repo_contracts() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    kernel = load_kernel_contract(repo_root=repo_root)
    topology = load_topology_contract(repo_root=repo_root)
    scenario = load_scenario_contract(scenario_id="new_feature_subagent_smoke", repo_root=repo_root)

    assert kernel["scheduler_mode"] == "repo_contract_driven"
    assert topology["default_execution_mode"] == "sequential"
    assert scenario["provider_plugin"] == "provider-vscode-codex"


def test_provider_adapter_resolves_vscode_host_to_headless_codex(monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(
        "platform_cli.orchestrate_kernel.provider_adapters.detect_provider_statuses",
        lambda refresh=False: {
            "codex": type("Status", (), {"available": True, "detail": "ok"})(),
            "claude": type("Status", (), {"available": False, "detail": "missing"})(),
        },
    )

    result = resolve_provider_adapter(provider_plugin_id="provider-vscode-codex", repo_root=repo_root)

    assert result.requested_host == "vscode_codex"
    assert result.effective_plugin == "provider-codex"
    assert result.effective_provider == "codex"
    assert result.model == "gpt-5.4"
    assert result.fallback_used is True


def test_release_notes_refresh_stage_updates_stage20_and_context(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_and_run_to_stage18(repo_root, monkeypatch)
    monkeypatch.setattr("platform_cli.orchestrate_kernel.stage20_release_notes._commit_and_push_notes", lambda *_args, **_kwargs: "abc123")

    result = run_release_notes_refresh_stage(repo_root=repo_root)

    assert result.current_stage == "stage20_release_notes_refresh"
    assert result.freshness_commit == "abc123"
    assert (repo_root / ".github" / "release-notes" / "notes.md").exists()


def test_pr_external_and_historian_stages_complete_with_stubbed_integrations(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _seed_and_run_to_stage18(repo_root, monkeypatch)
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
    agents_manifest = json.loads((repo_root / ".ghdp" / "agents" / "manifest.json").read_text(encoding="utf-8"))
    agents_manifest["agents"].append(
        {
            "id": "published-prerelease-validation",
            "role": "published_artifact_validator",
            "summary": "Validate published prerelease assets.",
            "contract_path": ".ghdp/agents/published-prerelease-validation.json",
        }
    )
    (repo_root / ".ghdp" / "agents" / "manifest.json").write_text(json.dumps(agents_manifest, indent=2) + "\n", encoding="utf-8")
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
    skills_manifest = json.loads((repo_root / ".ghdp" / "skills" / "manifest.json").read_text(encoding="utf-8"))
    skills_manifest["skills"].append({"id": "published-prerelease-retest"})
    (repo_root / ".ghdp" / "skills" / "manifest.json").write_text(json.dumps(skills_manifest, indent=2) + "\n", encoding="utf-8")
    (repo_root / ".ghdp" / "orchestrate" / "audit-export.json").write_text(
        json.dumps({"schema_version": "1.0", "destination_mode": "local", "local": {"output_dir": "tmp/orchestrate-audit-exports"}, "aws_s3": {"enabled": False, "bucket": "", "prefix": "", "region": "", "profile": ""}}, indent=2) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("platform_cli.orchestrate_kernel.stage19b_published_prerelease_retest._download_release_asset", lambda **_: repo_root / "downloaded-ghdp.exe")
    monkeypatch.setattr("platform_cli.orchestrate_kernel.stage19b_published_prerelease_retest._validate_downloaded_asset", lambda **_: {"version": CmdResult(cmd=["ghdp.exe", "--version"], returncode=0, stdout="ghdp 0.0.0", stderr=""), "status": CmdResult(cmd=["ghdp.exe", "--json", "orchestrate", "status"], returncode=0, stdout='{"contract_ready": true}', stderr="")})
    (repo_root / "downloaded-ghdp.exe").write_text("stub", encoding="utf-8")
    run_published_prerelease_retest_stage(repo_root=repo_root)
    monkeypatch.setattr("platform_cli.orchestrate_kernel.stage21_pr_external._validate_branch_hygiene", lambda _context: {"status": "completed", "develop_sha": "abc", "merge_base": "abc", "merge_commits": []})
    monkeypatch.setattr("platform_cli.orchestrate_kernel.stage21_pr_external._ensure_pr", lambda _context: "https://github.com/example/repo/pull/1")
    monkeypatch.setattr("platform_cli.orchestrate_kernel.stage21_pr_external._comment_prerelease_on_pr", lambda **_: "posted via gh")
    monkeypatch.setattr("platform_cli.orchestrate_kernel.stage21_pr_external._comment_on_jira", lambda *_args, **_kwargs: "posted via acli")
    monkeypatch.setattr("platform_cli.orchestrate_kernel.runtime_support.run_cmd", lambda *args, **kwargs: CmdResult(cmd=["aws"], returncode=0, stdout="", stderr=""))

    pr_result = run_pr_external_integration_stage(repo_root=repo_root)
    historian_result = run_traceability_capture_stage(repo_root=repo_root)

    assert pr_result.current_stage == "stage21_pr_external_integration"
    assert pr_result.pr_link.endswith("/1")
    assert historian_result.current_stage == "stage22_traceability_capture"
    export_dir = repo_root / "tmp" / "orchestrate-audit-exports"
    assert any(export_dir.glob("*.json"))


def test_subagent_scenario_dry_run_builds_packets(monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(
        "platform_cli.orchestrate_kernel.provider_adapters.detect_provider_statuses",
        lambda refresh=False: {
            "codex": type("Status", (), {"available": True, "detail": "ok"})(),
            "claude": type("Status", (), {"available": False, "detail": "missing"})(),
        },
    )

    result = run_subagent_scenario(scenario_id="new_feature_subagent_smoke", repo_root=repo_root, execute_provider=False)

    assert result.scenario_id == "new_feature_subagent_smoke"
    assert result.effective_plugin == "provider-codex"
    assert result.executed is False
    assert len(result.packets) >= 2
    assert result.execution_waves[0][0] == "ticket-intake"
    assert result.packets[0].produces_artifacts


def test_plan_execution_waves_honors_repo_parallel_groups() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    topology = load_topology_contract(repo_root=repo_root)

    waves = plan_execution_waves(
        requested_agents=[
            "blueprint-planner",
            "ticket-intake",
            "work-type-classifier",
            "autonomy-assessor",
            "context-capability-discovery",
            "parallel-work-awareness",
            "architecture-review",
            "ux-dx-review",
        ],
        topology=topology,
    )

    assert waves[0] == [
        "ticket-intake",
        "work-type-classifier",
        "autonomy-assessor",
        "context-capability-discovery",
        "parallel-work-awareness",
    ]
    assert waves[1] == ["blueprint-planner"]
    assert waves[2] == ["architecture-review", "ux-dx-review"]


def test_subagent_scenario_execution_passes_repo_model_to_provider(monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    captured: dict[str, str | None] = {}
    monkeypatch.setattr(
        "platform_cli.orchestrate_kernel.provider_adapters.detect_provider_statuses",
        lambda refresh=False: {
            "codex": type("Status", (), {"available": True, "detail": "ok"})(),
            "claude": type("Status", (), {"available": False, "detail": "missing"})(),
        },
    )
    monkeypatch.setattr(
        "platform_cli.orchestrate_kernel.subagents.detect_provider_statuses",
        lambda refresh=False: {
            "codex": type("Status", (), {"available": True, "detail": "ok"})(),
            "claude": type("Status", (), {"available": False, "detail": "missing"})(),
        },
    )

    def _fake_generate_text(*, provider, statuses, prompt, model=None, heartbeat=None):
        captured["provider"] = provider
        captured["model"] = model
        return "ok"

    monkeypatch.setattr("platform_cli.orchestrate_kernel.subagents.generate_text", _fake_generate_text)

    result = run_subagent_scenario(scenario_id="new_feature_subagent_smoke", repo_root=repo_root, execute_provider=True)

    assert result.executed is True
    assert result.effective_plugin == "provider-codex"
    assert captured["provider"] == "codex"
    assert captured["model"] == "gpt-5.4"
