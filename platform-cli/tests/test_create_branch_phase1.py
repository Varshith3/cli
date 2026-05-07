from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from platform_cli.commands import create_branch as create_branch_command
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.errors import PlatformError
from platform_cli.core.github_auth import managed_install_token_path
from platform_cli.tools import create_branch_service
from platform_cli.tools.create_branch_policy import load_create_branch_policy
from platform_cli.tools.create_branch_service import BranchCreateRequest
from platform_cli.tools.create_branch_workflow_adapter import write_branch_outputs_if_supported


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    cli_ctx.non_interactive = False
    cli_ctx.json = False
    return tmp_path


def test_parse_shorthand_with_prefix() -> None:
    policy = load_create_branch_policy()
    parsed = create_branch_service._parse_branch_shorthand(
        policy,
        "feature/EPPE-6654-ENHANCEMENT-branch-orchestration",
    )
    assert parsed == {
        "ticket": "EPPE-6654",
        "branch_type": "ENHANCEMENT",
        "slug": "branch-orchestration",
    }


def test_command_layer_prompts_for_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = iter(["EPPE-7000", "technical", "branch cleanup"])
    monkeypatch.setattr(create_branch_command, "get_bool", lambda key, default=False: True)
    monkeypatch.setattr(create_branch_command.typer, "prompt", lambda *_args, **_kwargs: next(prompts))

    resolved = create_branch_command._resolve_cli_inputs(
        branch=None,
        branch_type=None,
        ticket=None,
        slug=None,
        repo=None,
        provider="auto",
        intent_mode="auto",
        intent_text=None,
    )

    assert resolved == (None, "technical", "EPPE-7000", "branch cleanup", None, "auto", None)


def test_service_layer_requires_missing_inputs() -> None:
    with pytest.raises(PlatformError) as exc:
        create_branch_service._resolve_branch_inputs(
            policy=load_create_branch_policy(),
            branch=None,
            branch_type=None,
            ticket=None,
            slug=None,
        )

    assert exc.value.code == "E_TICKET_MISSING"


def test_create_branch_dry_run_with_provided_intent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    prompt_path = tmp_path / "prompt.txt"

    monkeypatch.setattr(create_branch_service, "_ensure_gh_available", lambda: None)
    monkeypatch.setattr(create_branch_service, "_ensure_gh_authenticated", lambda: None)
    monkeypatch.setattr(create_branch_service, "_resolve_repo", lambda repo: "owner/repo")
    monkeypatch.setattr(create_branch_service, "_resolve_default_branch", lambda repo: "develop")
    monkeypatch.setattr(create_branch_service, "validate_jira_ticket", lambda ticket, mode: create_branch_service.JiraValidationResult(ticket=ticket, found=True))
    monkeypatch.setattr(
        create_branch_service,
        "fetch_jira_context",
        lambda ticket, mode: {"summary": "Summary", "description": "Description"},
    )

    result = create_branch_service.create_branch(
        BranchCreateRequest(
            ticket="EPPE-7087",
            branch_type="technical",
            slug="cli-release-management-integration",
            intent_mode="provided",
            intent_text="Ship the branch flow.",
            dry_run=True,
            local_checkout=False,
            persist_intent=False,
            intent_prompt_file=str(prompt_path),
        )
    )

    assert result.branch_created is False
    assert result.dry_run is True
    assert result.branch_name == "feature/EPPE-7087-TECHNICAL-cli-release-management-integration"
    assert result.intent_provider == "provided"
    assert prompt_path.exists()
    assert "Branch name: feature/EPPE-7087-TECHNICAL-cli-release-management-integration" in prompt_path.read_text(encoding="utf-8")


def test_create_branch_persists_and_commits_intent_when_requested(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)
    target_path = repo_root / ".ghdp" / "frbr" / "intent.json"

    monkeypatch.setattr(create_branch_service, "_ensure_gh_available", lambda: None)
    monkeypatch.setattr(create_branch_service, "_ensure_gh_authenticated", lambda: None)
    monkeypatch.setattr(create_branch_service, "_resolve_repo", lambda repo: "owner/repo")
    monkeypatch.setattr(create_branch_service, "_resolve_default_branch", lambda repo: "main")
    monkeypatch.setattr(create_branch_service, "_create_remote_branch", lambda **kwargs: None)
    monkeypatch.setattr(create_branch_service, "validate_jira_ticket", lambda ticket, mode: create_branch_service.JiraValidationResult(ticket=ticket, found=True))
    monkeypatch.setattr(
        create_branch_service,
        "fetch_jira_context",
        lambda ticket, mode: {"summary": "Summary", "description": "Description"},
    )
    monkeypatch.setattr(
        create_branch_service,
        "checkout_remote_branch_if_safe",
        lambda repo, branch_name: create_branch_service.CheckoutResult(True, "ok", repo_root=repo_root),
    )
    monkeypatch.setattr(
        create_branch_service,
        "persist_repo_intent",
        lambda **kwargs: target_path,
    )
    monkeypatch.setattr(create_branch_service, "_commit_intent_file", lambda **kwargs: True)

    result = create_branch_service.create_branch(
        BranchCreateRequest(
            ticket="EPPE-7087",
            branch_type="technical",
            slug="cli-release-management-integration",
            intent_mode="provided",
            intent_text="Persist this intent.",
            commit_intent=True,
        )
    )

    assert result.branch_created is True
    assert result.intent_saved is True
    assert result.intent_committed is True
    assert result.intent_path.endswith(".ghdp\\frbr\\intent.json") or result.intent_path.endswith(".ghdp/frbr/intent.json")


def test_write_branch_outputs_if_supported(tmp_path: Path) -> None:
    output_path = tmp_path / "github_output.txt"
    result = create_branch_service.BranchCreateResult(
        repo="owner/repo",
        ticket="EPPE-7087",
        branch_type="TECHNICAL",
        slug="cli-release-management-integration",
        base_branch="develop",
        branch_name="feature/EPPE-7087-TECHNICAL-cli-release-management-integration",
        request_id="20260413T100000Z-1",
        branch_created=True,
        dry_run=False,
        jira_validated=True,
        jira_warning="",
        jira_comment_posted=True,
        intent_provider="provided",
        intent_generated=False,
        intent_saved=True,
        intent_committed=True,
        intent_path="C:/repo/.ghdp/frbr/intent.json",
        local_checkout_message="ok",
    )

    assert write_branch_outputs_if_supported(result, explicit_path=str(output_path)) is True
    payload = output_path.read_text(encoding="utf-8")
    assert "branch_name=feature/EPPE-7087-TECHNICAL-cli-release-management-integration" in payload
    assert "intent_committed=true" in payload


def test_gh_run_cmd_injects_managed_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GHDP_MANAGED_INSTALL", "1")
    token_file = managed_install_token_path()
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text("managed-token-123\n", encoding="utf-8")

    captured = {}

    def _fake_run(cmd, check=True, cwd=None, env=None, **kwargs):  # type: ignore[no-untyped-def]
        captured["cmd"] = list(cmd)
        captured["env"] = dict(env or {})
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(create_branch_service, "run_cmd", _fake_run)

    create_branch_service._gh_run_cmd(["gh", "auth", "status"], check=False)

    assert captured["cmd"] == ["gh", "auth", "status"]
    assert captured["env"]["GH_TOKEN"] == "managed-token-123"
    assert captured["env"]["GITHUB_TOKEN"] == "managed-token-123"
