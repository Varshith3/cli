from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from platform_cli.cli import app
from platform_cli.tools.orchestrate_contract import (
    inspect_orchestrate_contract,
    load_agent_contract,
    runtime_branch_folder_name,
    slugify_branch_name,
)


runner = CliRunner()


def test_slugify_branch_name_matches_branch_folder_convention() -> None:
    assert (
        slugify_branch_name(
            "feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory"
        )
        == "feature-eppe-7391-technical-agentic-framework-foundation-skills-plugins-subagents-native-memory"
    )


def test_runtime_branch_folder_name_compacts_when_path_is_too_long(tmp_path: Path) -> None:
    repo_root = tmp_path / ("a" * 80)
    repo_root.mkdir(parents=True)
    branch_name = "feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory"

    folder = runtime_branch_folder_name(repo_root, branch_name)

    assert folder != slugify_branch_name(branch_name)
    assert folder.startswith("eppe-7391-")


def test_inspect_orchestrate_contract_reports_current_repo_ready() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    status = inspect_orchestrate_contract(repo_root=repo_root)

    assert status.repo_contract_ready is True
    assert status.branch_runtime_ready is True
    assert status.contract_ready is True
    assert status.ticket_key == "EPPE-7391"
    assert status.agents_count == 21
    assert status.skills_count == 27
    assert status.plugins_count == 11
    assert status.memory_partition_count == 2
    assert status.active_run_key == "20260504-223548-ist__codex__stage-a"
    assert status.branch_runtime_mode == "active"


def test_orchestrate_status_json_reports_contract_counts() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    result = runner.invoke(app, ["--json", "orchestrate", "status", "--repo-root", str(repo_root)])

    assert result.exit_code == 0
    assert '"agents_count": 21' in result.output
    assert '"skills_count": 27' in result.output
    assert '"plugins_count": 11' in result.output
    assert '"contract_ready": true' in result.output.lower()


def test_load_agent_contract_reads_explicit_skill_and_plugin_access() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    contract = load_agent_contract(agent_id="pr-external-integration", repo_root=repo_root)

    assert contract["allowed_skills"] == ["release-and-pr", "jira-acli-integration", "pr-branch-hygiene", "pr-prerelease-commentary"]
    assert contract["allowed_plugins"] == ["jira-acli", "github-pr-gh", "github-release-gh"]


def test_inspect_orchestrate_contract_flags_missing_contract_files(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()

    status = inspect_orchestrate_contract(repo_root=tmp_path)

    assert status.contract_ready is False
    assert ".ghdp/agents/manifest.json" in status.missing
    assert ".ghdp/skills/manifest.json" in status.missing
