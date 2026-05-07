from __future__ import annotations
import json
from pathlib import Path

from typer.testing import CliRunner

from platform_cli.cli import app
from platform_cli.core import access
from platform_cli.tools.ai_provider import ProviderStatus
from platform_cli.tools.repo_ready_adapters import RepoReadyAdapterSyncResult
from platform_cli.tools.repo_ready import assess_repo_ready
from platform_cli.tools.repo_ready_generation import RepoReadyDraftResult


runner = CliRunner()


def _grant_repo_admin(monkeypatch, home: Path) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    policy_dir = home / ".ghdp" / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "team-policy.managed.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "managed_by": "ghdp",
                "admin_users": ["repo-admin"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        access,
        "run_cmd",
        lambda *_args, **_kwargs: type("R", (), {"returncode": 0, "stdout": "repo-admin", "stderr": ""})(),
    )


def _finalize_required_repo_files(repo_dir: Path, *, enabled_tools: list[str]) -> None:
    (repo_dir / ".ghdp" / "config.yaml").write_text(
        f"""
schema_version: "1.0"
repo:
  name: sample-repo
  type: infrastructure
  traits:
    - cli
classification:
  source: inferred
  evidence:
    - platform-cli/src
risk:
  tier: medium
execution:
  mode: sandbox-only
enabled:
  tools: {json.dumps(enabled_tools)}
  teams: []
  subagents: false
metadata:
  managed_by: ghdp
  template_version: "1.0.0"
""".strip(),
        encoding="utf-8",
    )
    (repo_dir / ".ghdp" / "runbook.yaml").write_text(
        """
schema_version: "1.0"
commands:
  build:
    - cmd: python -m build
  test:
    - cmd: python -m pytest -q
  lint: []
  format: []
  start: []
  dev: []
entrypoints: []
services: []
env_vars: []
notes:
  status: ready
metadata:
  managed_by: ghdp
  template_version: "1.0.0"
""".strip(),
        encoding="utf-8",
    )


def test_report_marks_missing_required_files(tmp_path: Path) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()

    result = assess_repo_ready(mode="report", repo_root=repo_dir)

    assert result.compliant is False
    assert ".ghdp/config.yaml" in result.missing_required
    assert ".github/workflows/ghdp-agent-policy.yml" in result.missing_required
    assert ".ghdp/architecture.md" in result.recommended_missing


def test_fix_scaffolds_phase1_files(tmp_path: Path) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()

    result = assess_repo_ready(mode="fix", repo_root=repo_dir)

    assert result.compliant is False
    assert ".ghdp/config.yaml" in result.created
    assert ".ghdp/guardrails.yaml" in result.created
    assert ".ghdp/lock.yaml" in result.created
    assert ".ghdp/runbook.yaml" in result.created
    assert ".ghdp/architecture.md" in result.created
    assert ".github/workflows/ghdp-agent-policy.yml" in result.created
    assert ".ghdp/config.yaml" in result.pending_required
    assert ".ghdp/runbook.yaml" in result.pending_required
    assert ".ghdp/architecture.md" in result.pending_recommended

    verify_result = assess_repo_ready(mode="verify", repo_root=repo_dir)
    assert verify_result.compliant is False
    assert verify_result.invalid_required == []
    assert verify_result.missing_required == []
    assert ".ghdp/config.yaml" in verify_result.pending_required
    assert ".ghdp/runbook.yaml" in verify_result.pending_required


def test_report_flags_invalid_existing_config(tmp_path: Path) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    assess_repo_ready(mode="fix", repo_root=repo_dir)

    (repo_dir / ".ghdp" / "config.yaml").write_text(
        """
schema_version: "1.0"
repo:
  name: sample-repo
  type: nope
  traits: []
classification:
  source: explicit
  evidence: []
risk:
  tier: medium
execution:
  mode: sandbox-only
enabled:
  tools: []
  teams: []
  subagents: false
metadata:
  managed_by: ghdp
  template_version: "1.0.0"
""".strip(),
        encoding="utf-8",
    )

    result = assess_repo_ready(mode="report", repo_root=repo_dir)

    assert result.compliant is False
    assert ".ghdp/config.yaml" in result.invalid_required


def test_verify_command_fails_when_required_files_missing(tmp_path: Path) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()

    res = runner.invoke(app, ["repo", "ready", "--verify", "--repo-root", str(repo_dir)])

    assert res.exit_code == 1
    assert ".ghdp/config.yaml" in res.output
    assert "missing required files: .ghdp/config.yaml" in str(res.exception)


def test_verify_command_fails_when_required_adapters_are_missing(tmp_path: Path) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    assess_repo_ready(mode="fix", repo_root=repo_dir)
    _finalize_required_repo_files(repo_dir, enabled_tools=["codex"])

    res = runner.invoke(app, ["repo", "ready", "--verify", "--repo-root", str(repo_dir)])

    assert res.exit_code == 1
    assert "missing required adapters:" in res.output
    assert "AGENTS.md" in res.output
    assert ".codex/config.toml" in res.output
    assert "missing required adapters: AGENTS.md, .codex/config.toml" in str(res.exception)


def test_repo_root_command_guides_user_without_retyping(tmp_path: Path, monkeypatch) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    _grant_repo_admin(monkeypatch, tmp_path)

    monkeypatch.setattr("platform_cli.tools.repo_ready.resolve_repo_root", lambda explicit_repo_root=None: repo_dir)
    monkeypatch.setattr(
        "platform_cli.commands.repo.select_provider",
        lambda **kwargs: (
            "manual",
            {
                "codex": ProviderStatus("codex", False, "", False, "missing"),
                "claude": ProviderStatus("claude", False, "", False, "missing"),
            },
        ),
    )
    monkeypatch.chdir(repo_dir)

    res = runner.invoke(app, ["repo"], input="fix\n")

    assert res.exit_code == 0
    assert "available repo commands:" in res.output
    assert "1. report" in res.output
    assert "2. fix" in res.output
    assert "5. help" not in res.output
    assert "6. exit" not in res.output
    assert "Choose the next repo action" in res.output
    assert (repo_dir / ".ghdp" / "config.yaml").exists()


def test_repo_ready_command_guides_user_without_flags(tmp_path: Path, monkeypatch) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    _grant_repo_admin(monkeypatch, tmp_path)

    monkeypatch.setattr(
        "platform_cli.commands.repo.select_provider",
        lambda **kwargs: (
            "manual",
            {
                "codex": ProviderStatus("codex", False, "", False, "missing"),
                "claude": ProviderStatus("claude", False, "", False, "missing"),
            },
        ),
    )

    res = runner.invoke(app, ["repo", "ready", "--repo-root", str(repo_dir)], input="2\n")

    assert res.exit_code == 0
    assert "available repo commands:" in res.output
    assert "1. report" in res.output
    assert "2. fix" in res.output
    assert "5. help" not in res.output
    assert "6. exit" not in res.output
    assert "Choose the next repo action" in res.output
    assert res.output.index("available repo commands:") < res.output.index("mode: fix")
    assert (repo_dir / ".ghdp" / "config.yaml").exists()


def test_repo_root_command_requires_subcommand_in_non_interactive_mode(tmp_path: Path, monkeypatch) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()

    monkeypatch.setattr("platform_cli.tools.repo_ready.resolve_repo_root", lambda explicit_repo_root=None: repo_dir)
    monkeypatch.chdir(repo_dir)

    res = runner.invoke(app, ["--non-interactive", "repo"])

    assert res.exit_code == 1
    assert str(res.exception) == "No repo action was provided. Use `ghdp repo ready --report`, `ghdp repo ready --fix`, or `ghdp repo ready --verify`."


def test_repo_fix_offers_ai_generation_when_provider_available(tmp_path: Path, monkeypatch) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    _grant_repo_admin(monkeypatch, tmp_path)

    captured = {}

    monkeypatch.setattr(
        "platform_cli.commands.repo.select_provider",
        lambda **kwargs: (
            "codex",
            {
                "codex": ProviderStatus("codex", True, "codex", True, "ok"),
                "claude": ProviderStatus("claude", False, "", False, "missing"),
            },
        ),
    )
    monkeypatch.setattr("platform_cli.commands.repo.typer.confirm", lambda *args, **kwargs: True)

    def _fake_generate(**kwargs):
        captured["targets"] = list(kwargs["targets"])
        return RepoReadyDraftResult(provider="codex", generated=list(kwargs["targets"]))

    monkeypatch.setattr("platform_cli.commands.repo.generate_repo_ready_drafts", _fake_generate)
    monkeypatch.setattr(
        "platform_cli.commands.repo.sync_repo_local_adapters",
        lambda **kwargs: RepoReadyAdapterSyncResult(generated=[]),
    )

    res = runner.invoke(app, ["repo", "fix", "--repo-root", str(repo_dir)])

    assert res.exit_code == 0
    assert captured["targets"] == [".ghdp/config.yaml", ".ghdp/runbook.yaml", ".ghdp/architecture.md"]
    assert "[repo] Assessing readiness and scaffolding base files..." in res.output
    assert "[repo] Selecting AI provider and planning draft generation..." in res.output
    assert "[repo] Generating suggested drafts..." in res.output
    assert "[repo] Syncing repo-local adapters..." in res.output
    assert "[repo] Rechecking repo readiness..." in res.output
    assert "suggested drafts via codex:" in res.output


def test_repo_fix_generates_repo_local_adapter_placeholders_when_ai_unavailable(tmp_path: Path, monkeypatch) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    _grant_repo_admin(monkeypatch, tmp_path)

    monkeypatch.setattr(
        "platform_cli.commands.repo.select_provider",
        lambda **kwargs: (
            "manual",
            {
                "codex": ProviderStatus("codex", False, "", False, "missing"),
                "claude": ProviderStatus("claude", False, "", False, "missing"),
            },
        ),
    )

    res = runner.invoke(app, ["repo", "fix", "--repo-root", str(repo_dir)])

    assert res.exit_code == 0
    assert (repo_dir / "CLAUDE.md").exists()
    assert (repo_dir / "AGENTS.md").exists()
    assert (repo_dir / ".claude" / "settings.json").exists()
    assert (repo_dir / ".codex" / "config.toml").exists()
    assert (repo_dir / ".mcp.json").exists()
    assert "repo-local adapters:" in res.output


def test_repo_ready_report_writes_readiness_json(tmp_path: Path) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()

    res = runner.invoke(app, ["repo", "ready", "--report", "--repo-root", str(repo_dir)])

    assert res.exit_code == 0
    readiness_path = repo_dir / ".ghdp" / "readiness.json"
    assert readiness_path.exists()
    payload = json.loads(readiness_path.read_text(encoding="utf-8"))
    assert payload["mode"] == "report"
    assert payload["summary"] == "not_ready"
    assert payload["files"]


def test_feature_branch_fix_scaffolds_intent_when_missing(tmp_path: Path, monkeypatch) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()

    monkeypatch.setattr(
        "platform_cli.tools.repo_ready.current_branch_name",
        lambda repo_root: "feature/EPPE-1234-TECHNICAL-sample",
    )

    result = assess_repo_ready(mode="fix", repo_root=repo_dir)

    assert ".ghdp/frbr/intent.json" in result.created
    assert ".ghdp/frbr/intent.json" in result.pending_recommended
    intent_payload = json.loads((repo_dir / ".ghdp" / "frbr" / "intent.json").read_text(encoding="utf-8"))
    assert intent_payload["branch_name"] == "feature/EPPE-1234-TECHNICAL-sample"
    assert intent_payload["ticket_key"] == "EPPE-1234"
    assert intent_payload["status"] == "pending-user-review"


def test_feature_branch_report_flags_stale_inherited_intent(tmp_path: Path, monkeypatch) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()

    monkeypatch.setattr(
        "platform_cli.tools.repo_ready.current_branch_name",
        lambda repo_root: "feature/EPPE-1234-TECHNICAL-sample",
    )

    assess_repo_ready(mode="fix", repo_root=repo_dir)
    (repo_dir / ".ghdp" / "frbr" / "intent.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_by": "ghdp",
                "source": "branch_create_generated",
                "repo_name": "sample-repo",
                "branch_name": "feature/EPPE-9999-TECHNICAL-other-work",
                "ticket_key": "EPPE-9999",
                "intent": "Keep the old branch intent content.",
                "summary": "Already reviewed summary",
                "provider": "codex",
                "generated_at": "2026-04-01T00:00:00Z",
                "status": "ready",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    result = assess_repo_ready(mode="report", repo_root=repo_dir)

    assert ".ghdp/frbr/intent.json" in result.pending_recommended
    intent_file = next(item for item in result.files if item.rel_path == ".ghdp/frbr/intent.json")
    assert any("intent.branch_name targets" in message for message in intent_file.messages)
    assert any("intent.ticket_key targets" in message for message in intent_file.messages)


def test_repo_accept_clears_review_markers(tmp_path: Path, monkeypatch) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    _grant_repo_admin(monkeypatch, tmp_path)
    assess_repo_ready(mode="fix", repo_root=repo_dir)

    (repo_dir / ".ghdp" / "config.yaml").write_text(
        """
schema_version: "1.0"
repo:
  name: sample-repo
  type: backend
  traits: []
classification:
  source: explicit
  evidence: []
risk:
  tier: medium
execution:
  mode: sandbox-only
enabled:
  tools: []
  teams: []
  subagents: false
metadata:
  managed_by: ghdp
  template_version: "1.0.0"
  review_status: suggested
""".strip(),
        encoding="utf-8",
    )
    (repo_dir / ".ghdp" / "runbook.yaml").write_text(
        """
schema_version: "1.0"
commands:
  build:
    - cmd: python -m build
  test:
    - cmd: python -m pytest -q
  lint: []
  format: []
  start: []
  dev: []
entrypoints: []
services: []
env_vars: []
notes:
  status: suggested-review-required
metadata:
  managed_by: ghdp
  template_version: "1.0.0"
""".strip(),
        encoding="utf-8",
    )
    (repo_dir / ".ghdp" / "architecture.md").write_text(
        "<!-- GHDP review_status: suggested -->\n\n# GHDP Architecture\n\n## Module Map\n\n- sample\n\n## Key Entry Points\n\n- cli\n\n## Critical Flows\n\n- flow\n\n## Validation\n\n- Refer to `.ghdp/runbook.yaml`.\n\n## Ownership\n\n- needs confirmation\n\n## Do Not Touch\n\n- .ghdp\n\n## Open Questions\n\n- none\n",
        encoding="utf-8",
    )

    res = runner.invoke(app, ["repo", "accept", "--repo-root", str(repo_dir)])

    assert res.exit_code == 0
    report = assess_repo_ready(mode="report", repo_root=repo_dir)
    assert ".ghdp/config.yaml" not in report.pending_required
    assert ".ghdp/runbook.yaml" not in report.pending_required
    assert ".ghdp/architecture.md" not in report.pending_recommended


def test_repo_ready_fix_jenkins_contract_creates_repo_local_contract(tmp_path: Path) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    (repo_dir / "Jenkinsfile").write_text(
        """
pipeline {
    parameters {
        booleanParam(name: 'APPLY', defaultValue: false, description: 'Apply infra')
    }
}
""".strip(),
        encoding="utf-8",
    )

    res = runner.invoke(app, ["repo", "ready", "--fix-jenkins-contract", "--repo-root", str(repo_dir)])

    assert res.exit_code == 0
    assert "jenkins contract:" in res.output
    assert (repo_dir / ".ghdp" / "ci" / "jenkins_contract.json").exists()


def test_repo_fix_refreshes_jenkins_contract_when_repo_has_jenkinsfile(tmp_path: Path, monkeypatch) -> None:
    repo_dir = tmp_path / "sample-repo"
    repo_dir.mkdir()
    (repo_dir / "Jenkinsfile").write_text(
        """
pipeline {
    parameters {
        booleanParam(name: 'APPLY', defaultValue: false, description: 'Apply infra')
    }
}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "platform_cli.commands.repo.select_provider",
        lambda **kwargs: (
            "manual",
            {
                "codex": ProviderStatus("codex", False, "", False, "missing"),
                "claude": ProviderStatus("claude", False, "", False, "missing"),
            },
        ),
    )

    res = runner.invoke(app, ["repo", "fix", "--repo-root", str(repo_dir)])

    assert res.exit_code == 0
    assert "jenkins contract:" in res.output
    assert (repo_dir / ".ghdp" / "ci" / "jenkins_contract.json").exists()
