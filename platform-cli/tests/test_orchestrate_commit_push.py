from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from platform_cli.cli import app
from platform_cli.exec.runner import run_cmd
from orchestrate_stage_seed import seed_stage_contracts
from platform_cli.tools.orchestrate_commit_push import run_commit_push_stage
from platform_cli.tools.orchestrate_execution import run_execution_prep
from platform_cli.tools.orchestrate_front_door import run_front_door_gates
from platform_cli.tools.orchestrate_implementation import run_implementation_stage
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

    agent_contracts = {
        "orchestrator": {
            "schema_version": "1.0",
            "id": "orchestrator",
            "role": "control_plane",
            "stages_owned": ["stage0_trigger"],
            "allowed_skills": ["traceability-and-resume"],
            "allowed_plugins": ["native-memory-filesystem", "provider-codex", "provider-claude"],
            "produces_artifacts": ["branch_state.json"],
            "approval_mode": "policy_driven",
            "can_block": True,
            "can_retry": True,
            "prompt_contract": ["Own runtime state and keep branch artifacts in sync."],
        },
        "implementation": {
            "schema_version": "1.0",
            "id": "implementation",
            "role": "delivery_worker",
            "stages_owned": ["stage11_implementation"],
            "allowed_skills": ["traceability-and-resume"],
            "allowed_plugins": ["provider-codex", "provider-claude", "native-memory-filesystem"],
            "produces_artifacts": ["implementation_summary"],
            "approval_mode": "on_anomaly",
            "can_block": True,
            "can_retry": True,
            "prompt_contract": ["Apply the plan without breaking prior stage artifacts."],
        },
        "qa-scenario-design": {
            "schema_version": "1.0",
            "id": "qa-scenario-design",
            "role": "scenario_designer",
            "stages_owned": ["stage13_qa_scenario_design"],
            "allowed_skills": ["qa-scenario-generation"],
            "allowed_plugins": ["provider-codex", "provider-claude"],
            "produces_artifacts": ["qa_scenarios"],
            "approval_mode": "policy_driven",
            "can_block": False,
            "can_retry": True,
            "prompt_contract": ["Design acceptance-linked scenarios, not generic smoke checks."],
        },
        "regression-validation": {
            "schema_version": "1.0",
            "id": "regression-validation",
            "role": "behavior_guard",
            "stages_owned": ["stage14_touched_scope_regression"],
            "allowed_skills": ["touched-scope-regression"],
            "allowed_plugins": ["provider-codex", "provider-claude"],
            "produces_artifacts": ["regression_results"],
            "approval_mode": "on_failure",
            "can_block": True,
            "can_retry": True,
            "prompt_contract": ["Protect already-working touched behavior before expanding scope."],
        },
        "test-coverage-authoring": {
            "schema_version": "1.0",
            "id": "test-coverage-authoring",
            "role": "coverage_expander",
            "stages_owned": ["stage15_new_test_coverage"],
            "allowed_skills": ["test-coverage-authoring"],
            "allowed_plugins": ["provider-codex", "provider-claude"],
            "produces_artifacts": ["coverage_plan"],
            "approval_mode": "policy_driven",
            "can_block": True,
            "can_retry": True,
            "prompt_contract": ["Add focused tests for changed behavior only."],
        },
        "developer-test-execution": {
            "schema_version": "1.0",
            "id": "developer-test-execution",
            "role": "validation_executor",
            "stages_owned": ["stage16_developer_test_execution"],
            "allowed_skills": ["developer-test-execution"],
            "allowed_plugins": ["native-memory-filesystem"],
            "produces_artifacts": ["test_execution_log"],
            "approval_mode": "on_failure",
            "can_block": True,
            "can_retry": True,
            "prompt_contract": ["Respect local resource locks when executing test flows."],
        },
        "binary-validation": {
            "schema_version": "1.0",
            "id": "binary-validation",
            "role": "artifact_validator",
            "stages_owned": ["stage20_packaged_artifact_validation"],
            "allowed_skills": ["isolated-binary-validation"],
            "allowed_plugins": ["github-release-gh"],
            "produces_artifacts": ["artifact_validation_result"],
            "approval_mode": "on_failure",
            "can_block": True,
            "can_retry": True,
            "prompt_contract": ["Validate the packaged artifact, not only dev-mode behavior."],
        },
        "release-readiness": {
            "schema_version": "1.0",
            "id": "release-readiness",
            "role": "go_no_go_reviewer",
            "stages_owned": ["stage18_release_readiness"],
            "allowed_skills": ["architecture-compliance", "traceability-and-resume"],
            "allowed_plugins": ["provider-codex", "provider-claude", "native-memory-filesystem"],
            "produces_artifacts": ["release_readiness_summary"],
            "approval_mode": "always_for_prerelease",
            "can_block": True,
            "can_retry": True,
            "prompt_contract": ["Block release progression when traceability or readiness is weak."],
        },
        "release-prerelease": {
            "schema_version": "1.0",
            "id": "release-prerelease",
            "role": "release_operator",
            "stages_owned": ["stage19_prerelease_creation"],
            "allowed_skills": ["release-and-pr", "stable-release-notes-assembly"],
            "allowed_plugins": ["github-release-gh", "jenkins-mcp"],
            "produces_artifacts": ["prerelease_link"],
            "approval_mode": "always",
            "can_block": True,
            "can_retry": True,
            "prompt_contract": ["Create prerelease outputs only after readiness is clear."],
        },
        "pr-external-integration": {
            "schema_version": "1.0",
            "id": "pr-external-integration",
            "role": "external_communicator",
            "stages_owned": ["stage21_pr_external_integration"],
            "allowed_skills": ["release-and-pr", "jira-acli-integration"],
            "allowed_plugins": ["jenkins-mcp", "jira-acli", "github-release-gh"],
            "produces_artifacts": ["pr_link", "jira_update_summary"],
            "approval_mode": "always_for_pr_creation",
            "can_block": True,
            "can_retry": True,
            "prompt_contract": ["Use only approved external integration paths."],
        },
        "traceability-historian": {
            "schema_version": "1.0",
            "id": "traceability-historian",
            "role": "execution_historian",
            "stages_owned": ["stage22_traceability_capture"],
            "allowed_skills": ["traceability-and-resume"],
            "allowed_plugins": ["native-memory-filesystem"],
            "produces_artifacts": ["decisions.json", "resume_context.md"],
            "approval_mode": "policy_driven",
            "can_block": False,
            "can_retry": True,
            "prompt_contract": ["Keep the run resumable by another human or agent."],
        },
    }
    (repo_root / ".ghdp" / "agents" / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "agents": [
                    {
                        "id": agent_id,
                        "role": payload["role"],
                        "summary": payload["prompt_contract"][0],
                        "contract_path": f".ghdp/agents/{agent_id}.json",
                    }
                    for agent_id, payload in agent_contracts.items()
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    for agent_id, payload in agent_contracts.items():
        (repo_root / ".ghdp" / "agents" / f"{agent_id}.json").write_text(
            json.dumps(payload, indent=2) + "\n",
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

    (repo_root / "platform-cli" / "src" / "platform_cli" / "manifests" / "orchestrate_validate.py").write_text("# stub\n", encoding="utf-8")
    (repo_root / "platform-cli" / "src" / "platform_cli" / "tools" / "orchestrate_front_door.py").write_text("# stub\n", encoding="utf-8")

    for skill_id in (
        "traceability-and-resume",
        "qa-scenario-generation",
        "touched-scope-regression",
        "test-coverage-authoring",
        "developer-test-execution",
        "isolated-binary-validation",
        "architecture-compliance",
        "release-and-pr",
        "jira-acli-integration",
        "stable-release-notes-assembly",
        "folder-backed-shared-memory",
    ):
        skill_root = repo_root / ".ghdp" / "skills" / skill_id
        skill_root.mkdir(parents=True, exist_ok=True)
        (skill_root / "SKILL.md").write_text(f"# {skill_id}\n", encoding="utf-8")

    for plugin_id in (
        "provider-codex",
        "provider-claude",
        "native-memory-filesystem",
        "github-release-gh",
        "jenkins-mcp",
        "jira-acli",
    ):
        plugin_root = repo_root / ".ghdp" / "plugins" / plugin_id
        plugin_root.mkdir(parents=True, exist_ok=True)
        (plugin_root / "plugin.json").write_text(
            json.dumps({"schema_version": "1.0", "id": plugin_id, "executor": "test", "login_required": False, "setup_contract": ["stub"]}, indent=2) + "\n",
            encoding="utf-8",
        )


def test_commit_push_stage_commits_and_pushes_branch(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    branch_name = "feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory"
    remote_root = tmp_path / "remote.git"

    run_cmd(["git", "init", "-b", branch_name], cwd=repo_root, check=True)
    run_cmd(["git", "init", "--bare", str(remote_root)], cwd=tmp_path, check=True)

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

    monkeypatch.setattr("platform_cli.tools.orchestrate_runtime.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_front_door.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_review.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_execution.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_implementation.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_commit_push.current_branch_name", lambda _repo_root: branch_name)

    start_orchestrate_run(repo_root=repo_root)
    run_front_door_gates(repo_root=repo_root)
    run_review_layer(repo_root=repo_root)
    run_execution_prep(repo_root=repo_root)
    run_implementation_stage(repo_root=repo_root)

    result = run_commit_push_stage(repo_root=repo_root)

    assert result.current_stage == "stage12_commit_push"
    assert result.pushed is True
    assert result.remote_name == "origin"
    assert result.files_committed

    remote_sha = run_cmd(["git", "--git-dir", str(remote_root), "rev-parse", branch_name], cwd=tmp_path, check=True).stdout.strip()
    assert remote_sha == result.head_sha
    run_root = Path(result.branch_runtime_root) / "runs" / result.active_run_key
    assert (run_root / "commit_summary.md").exists()
    assert (run_root / "commit_payload.json").exists()
    assert run_cmd(["git", "status", "--short"], cwd=repo_root, check=True).stdout.strip() == ""


def test_orchestrate_commit_push_cli_reports_head_sha(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    branch_name = "feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory"
    remote_root = tmp_path / "remote.git"

    run_cmd(["git", "init", "-b", branch_name], cwd=repo_root, check=True)
    run_cmd(["git", "init", "--bare", str(remote_root)], cwd=tmp_path, check=True)

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

    monkeypatch.setattr("platform_cli.tools.orchestrate_runtime.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_front_door.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_review.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_execution.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_implementation.current_branch_name", lambda _repo_root: branch_name)
    monkeypatch.setattr("platform_cli.tools.orchestrate_commit_push.current_branch_name", lambda _repo_root: branch_name)

    start_orchestrate_run(repo_root=repo_root)
    run_front_door_gates(repo_root=repo_root)
    run_review_layer(repo_root=repo_root)
    run_execution_prep(repo_root=repo_root)
    run_implementation_stage(repo_root=repo_root)

    result = runner.invoke(app, ["orchestrate", "commit-push", "--repo-root", str(repo_root)])

    assert result.exit_code == 0
    assert "current_stage         : stage12_commit_push" in result.output
    assert "head_sha              :" in result.output
