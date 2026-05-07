from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.models import OptionInfo
from typer.testing import CliRunner

import platform_cli
from platform_cli.cli import app
import platform_cli.core.decorators as decorators_mod
config_cli = importlib.import_module("platform_cli.commands.config_cli")
release = importlib.import_module("platform_cli.commands.release")
release_mcp = importlib.import_module("platform_cli.tools.release_mcp")
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.errors import PlatformError


runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_cli_ctx() -> None:
    cli_ctx.non_interactive = False
    cli_ctx.quiet = False


def test_resolve_feature_branch_uses_current_feature_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "resolve_current_branch_name", lambda: "feature/EPPE-7239-smart-release")
    monkeypatch.setattr(release.typer, "confirm", lambda *_args, **_kwargs: True)

    assert release._resolve_feature_branch(None) == "feature/EPPE-7239-smart-release"


def test_resolve_feature_branch_skips_confirmation_when_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "resolve_current_branch_name", lambda: "feature/EPPE-7239-smart-release")
    monkeypatch.setattr(
        release.typer,
        "confirm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("confirm should not run")),
    )

    assert release._resolve_feature_branch(None, skip_confirm=True) == "feature/EPPE-7239-smart-release"


def test_resolve_feature_branch_rejects_non_feature_branch() -> None:
    with pytest.raises(PlatformError) as exc:
        release._resolve_feature_branch("develop")

    assert exc.value.code == "E_RELEASE_BRANCH_INVALID"


def test_resolve_deploy_on_sqa_defaults_false_without_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        release.typer,
        "confirm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("confirm should not run")),
    )

    assert release._resolve_deploy_on_sqa(None) is False


def test_resolve_deploy_on_sqa_rejects_true() -> None:
    with pytest.raises(PlatformError) as exc:
        release._resolve_deploy_on_sqa(True)

    assert exc.value.code == "E_RELEASE_DEPLOY_ON_SQA_UNSUPPORTED"


def test_resolve_deploy_on_sqa_accepts_explicit_false() -> None:
    assert release._resolve_deploy_on_sqa(False) is False


def test_release_feature_to_dev_uses_live_status_presenter(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, str]] = []

    class _FakeStatus:
        def __init__(self, *, prefix: str) -> None:
            events.append(("init", prefix))

        def update(self, message: str) -> None:
            events.append(("update", message))

        def finish(self, message: str | None = None) -> None:
            events.append(("finish", message or ""))

    monkeypatch.setattr(release, "LiveStatus", _FakeStatus)
    monkeypatch.setattr(release, "_ensure_release_contract", lambda refresh: {"pipeline": {"style": "jenkins"}})
    monkeypatch.setattr(release, "resolve_release_repo", lambda repo: SimpleNamespace(repo_name="sample-repo"))
    monkeypatch.setattr(release, "_resolve_feature_branch", lambda branch, skip_confirm=False: "feature/EPPE-7239-smart-release")
    monkeypatch.setattr(release, "_resolve_deploy_on_sqa", lambda value: False)
    monkeypatch.setattr(
        release,
        "_resolve_release_credentials_interactive",
        lambda require_github_token: (SimpleNamespace(email="user@example.com"), "config"),
    )

    def _fake_execute_with_jenkins_token_refresh(**kwargs):
        runner = kwargs["runner"]
        return runner(
            SimpleNamespace(
                email="user@example.com",
                jenkins_api_token="jenkins-token",
                github_api_token="gh-token",
            )
        )

    monkeypatch.setattr(release, "execute_with_jenkins_token_refresh", _fake_execute_with_jenkins_token_refresh)

    def _fake_run_feature_to_dev(**kwargs):
        kwargs["status_printer"]("Triggering Jenkins job...")
        return SimpleNamespace(
            build_url="https://jenkins.example/job/123",
            pull_request_url="https://github.com/example/repo/pull/1",
            message="done",
        )

    monkeypatch.setattr(release, "run_feature_to_dev", _fake_run_feature_to_dev)

    release.release_feature_to_dev(branch="feature/EPPE-7239-smart-release", yes=True)

    assert events == [
        ("init", "[release]"),
        ("update", "Triggering Jenkins job..."),
        ("finish", ""),
    ]


def test_release_make_release_does_not_use_live_status_presenter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        release,
        "LiveStatus",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("LiveStatus should not be used for make-release")),
    )
    monkeypatch.setattr(
        release,
        "_ensure_release_contract",
        lambda refresh: {"pipeline": {"style": "jenkins"}, "parameter_schema": []},
    )
    monkeypatch.setattr(release, "resolve_release_repo", lambda repo: SimpleNamespace(repo_name="sample-repo"))
    monkeypatch.setattr(release, "_resolve_release_type", lambda release_type: "bugfix")
    monkeypatch.setattr(release, "_resolve_release_parent", lambda parent: "REL-123>Summary")
    monkeypatch.setattr(release, "_collect_make_release_params", lambda **kwargs: {})
    monkeypatch.setattr(
        release,
        "_resolve_release_credentials_interactive",
        lambda require_github_token: (SimpleNamespace(email="user@example.com"), "config"),
    )
    monkeypatch.setattr(
        release,
        "execute_with_jenkins_token_refresh",
        lambda **kwargs: SimpleNamespace(build_url="https://jenkins.example/job/456", pull_request_url="", message="done"),
    )

    release.release_make_release(
        repo=None,
        release_type=None,
        parent=None,
        param=[],
        tested_ok_on_uat=None,
        merge_pr=None,
        tag_release=None,
        deploy=None,
        refresh_jenkins_contract=False,
        yes=True,
    )


def test_resolve_okta_email_interactive_prompts_and_saves(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    monkeypatch.setattr(release, "resolve_okta_email", lambda: "")
    monkeypatch.setattr(release.typer, "prompt", lambda *_args, **_kwargs: "mshyam")
    monkeypatch.setattr(release.typer, "confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(release, "set_value", lambda key, value: captured.update({key: value}))

    assert release._resolve_okta_email_interactive() == "mshyam@guardanthealth.com"
    assert captured["jenkins.okta_email"] == "mshyam@guardanthealth.com"


def test_collect_make_release_params_merges_flags_and_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    cli_ctx.non_interactive = True
    monkeypatch.setattr(release.typer, "confirm", lambda *_args, **_kwargs: False)
    contract = {
        "parameter_schema": [
            {
                "name": "TARGET_WORKSPACE",
                "kind": "choice",
                "required": True,
                "default": None,
                "description": "Infra deployment workspace",
                "choices": [],
            },
            {
                "name": "MAVEN_BUILD",
                "kind": "boolean",
                "required": False,
                "default": False,
                "description": "Build code",
            },
        ]
    }

    params = release._collect_make_release_params(
        raw_params=["APPLICATION_NAME=platform-cli"],
        contract=contract,
        tested_ok_on_uat=True,
        merge_pr=None,
        tag_release=False,
        deploy=None,
    )

    assert params["APPLICATION_NAME"] == "platform-cli"
    assert params["TARGET_WORKSPACE"] == "dev"
    assert params["MAVEN_BUILD"] == "false"
    assert params["TESTED_OK_ON_UAT"] == "true"
    assert params["MERGE_PR"] == "true"
    assert params["TAG_RELEASE"] == "false"
    assert params["DEPLOY"] == "false"


def test_parse_param_requires_key_value() -> None:
    with pytest.raises(PlatformError) as exc:
        release._parse_param("oops")

    assert exc.value.code == "E_RELEASE_PARAM_INVALID"


def test_resolve_jenkins_api_token_interactive_prompts_and_saves(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    monkeypatch.setattr(release, "resolve_jenkins_api_token_with_source", lambda: ("", ""))
    monkeypatch.setattr(release.typer, "prompt", lambda *_args, **_kwargs: "jenkins-token")
    monkeypatch.setattr(release, "set_value", lambda key, value: captured.update({key: value}))
    monkeypatch.setattr(release.typer, "echo", lambda *_args, **_kwargs: None)

    token, source = release._resolve_jenkins_api_token_interactive()

    assert token == "jenkins-token"
    assert source == "prompt"
    assert captured["jenkins.api_token"] == "jenkins-token"


def test_execute_with_jenkins_token_refresh_retries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[str] = []
    monkeypatch.setattr(release_mcp, "is_probable_jenkins_auth_failure", lambda exc: True)

    def _runner(credentials):
        attempts.append(credentials.jenkins_api_token)
        if len(attempts) == 1:
            raise PlatformError("Forbidden", code="E_RELEASE_MCP_HTTP", reason="403")
        return SimpleNamespace(build_url="https://jenkins.example/job/123/", pull_request_url="", message="done")

    result = release_mcp.execute_with_jenkins_token_refresh(
        credentials=release.ReleaseCredentials(
            email="user@example.com",
            jenkins_api_token="old-token",
            github_api_token="gh-token",
        ),
        token_source="config",
        non_interactive=False,
        token_refresher=lambda prompt_text: "new-token",
        runner=_runner,
    )

    assert attempts == ["old-token", "new-token"]
    assert result.message == "done"


def test_execute_with_jenkins_token_refresh_accepts_release_prompt_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []
    prompted: list[str] = []
    saved: dict[str, str] = {}

    monkeypatch.setattr(release_mcp, "is_probable_jenkins_auth_failure", lambda exc: True)
    monkeypatch.setattr(
        release.typer,
        "prompt",
        lambda prompt_text, **_kwargs: prompted.append(prompt_text) or "new-token",
    )
    monkeypatch.setattr(release, "set_value", lambda key, value: saved.update({key: value}))
    monkeypatch.setattr(release.typer, "echo", lambda *_args, **_kwargs: None)

    def _runner(credentials):
        attempts.append(credentials.jenkins_api_token)
        if len(attempts) == 1:
            raise PlatformError("Unauthorized", code="E_RELEASE_JENKINS_HTTP", reason="401")
        return SimpleNamespace(build_url="", pull_request_url="", message="done")

    result = release_mcp.execute_with_jenkins_token_refresh(
        credentials=release.ReleaseCredentials(
            email="user@example.com",
            jenkins_api_token="old-token",
            github_api_token="gh-token",
        ),
        token_source="config",
        non_interactive=False,
        token_refresher=release._prompt_and_store_jenkins_api_token,
        runner=_runner,
    )

    assert attempts == ["old-token", "new-token"]
    assert prompted == ["Stored Jenkins API token appears invalid or expired. Enter a new Jenkins API token"]
    assert saved["jenkins.api_token"] == "new-token"
    assert result.message == "done"


def test_execute_with_jenkins_token_refresh_does_not_retry_for_non_auth_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []
    monkeypatch.setattr(release_mcp, "is_probable_jenkins_auth_failure", lambda exc: False)

    def _runner(credentials):
        attempts.append(credentials.jenkins_api_token)
        raise PlatformError("Timed out", code="E_RELEASE_MCP_TIMEOUT", reason="mcp_timeout")

    with pytest.raises(PlatformError) as exc:
        release_mcp.execute_with_jenkins_token_refresh(
            credentials=release.ReleaseCredentials(
                email="user@example.com",
                jenkins_api_token="old-token",
                github_api_token="gh-token",
            ),
            token_source="config",
            non_interactive=False,
            token_refresher=lambda prompt_text: "new-token",
            runner=_runner,
        )

    assert exc.value.code == "E_RELEASE_MCP_TIMEOUT"
    assert attempts == ["old-token"]


@pytest.mark.parametrize("choice", ["1", "ftd", "feature-to-dev"])
def test_release_root_choice_one_invokes_feature_flow_with_plain_kwargs(
    monkeypatch: pytest.MonkeyPatch,
    choice: str,
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(decorators_mod, "log_usage", lambda **_kwargs: None)
    monkeypatch.setattr(release, "resolve_current_branch_name", lambda: "feature/EPPE-7239-smart-release")
    monkeypatch.setattr(
        release.typer,
        "prompt",
        lambda prompt_text, default="", **_kwargs: choice
        if prompt_text == "Select release flow"
        else (_ for _ in ()).throw(AssertionError(f"unexpected prompt: {prompt_text}")),
    )
    monkeypatch.setattr(
        release.typer,
        "confirm",
        lambda prompt_text, default=False, **_kwargs: {
            "Use the current feature branch 'feature/EPPE-7239-smart-release'?": True,
            "Trigger feature-to-dev in Jenkins?": True,
        }[prompt_text]
        if prompt_text in {
            "Use the current feature branch 'feature/EPPE-7239-smart-release'?",
            "Trigger feature-to-dev in Jenkins?",
        }
        else (_ for _ in ()).throw(AssertionError(f"unexpected confirm: {prompt_text}")),
    )
    monkeypatch.setattr(release, "_ensure_release_contract", lambda refresh: {"pipeline": {"style": "jenkins"}})
    monkeypatch.setattr(release, "_resolve_deploy_on_sqa", lambda value: False)
    monkeypatch.setattr(
        release,
        "_resolve_release_credentials_interactive",
        lambda require_github_token: (SimpleNamespace(email="user@example.com"), "config"),
    )
    monkeypatch.setattr(release, "resolve_release_repo", lambda repo: SimpleNamespace(repo_name="sample-repo"))

    def _fake_run_feature_to_dev(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(build_url="https://example/build", pull_request_url="", message="done")

    monkeypatch.setattr(release, "run_feature_to_dev", _fake_run_feature_to_dev)

    result = runner.invoke(app, ["release"])

    assert result.exit_code == 0
    assert "1. feature-to-dev" in result.output
    assert "2. make-release" in result.output
    assert captured["repo_name"] == "sample-repo"
    assert captured["branch"] == "feature/EPPE-7239-smart-release"
    assert getattr(captured["credentials"], "email") == "user@example.com"
    assert captured["deploy_on_sqa"] is False
    assert all(not isinstance(value, OptionInfo) for value in captured.values())


@pytest.mark.parametrize("choice", ["2", "mr", "make-release"])
def test_release_root_choice_two_invokes_make_release_flow_with_plain_kwargs(
    monkeypatch: pytest.MonkeyPatch,
    choice: str,
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(decorators_mod, "log_usage", lambda **_kwargs: None)
    prompt_answers = {
        "Select release flow": choice,
        "Release type (major, minor, bugfix)": "bugfix",
        "Parent release ticket as KEY>Summary (leave blank if you want Jenkins to choose when supported)": "REL-123>Summary",
    }
    confirm_answers = {
        "Mark release as tested on UAT?": True,
        "Merge the release PR automatically?": False,
        "Tag the release automatically?": True,
        "Deploy as part of this release flow?": False,
        "Add another Jenkins parameter?": False,
        "Trigger make-release in Jenkins?": True,
    }

    monkeypatch.setattr(
        release.typer,
        "prompt",
        lambda prompt_text, default="", **_kwargs: prompt_answers.get(prompt_text)
        if prompt_text in prompt_answers
        else (_ for _ in ()).throw(AssertionError(f"unexpected prompt: {prompt_text}")),
    )
    monkeypatch.setattr(
        release.typer,
        "confirm",
        lambda prompt_text, default=False, **_kwargs: confirm_answers[prompt_text]
        if prompt_text in confirm_answers
        else (_ for _ in ()).throw(AssertionError(f"unexpected confirm: {prompt_text}")),
    )
    monkeypatch.setattr(
        release,
        "_ensure_release_contract",
        lambda refresh: {"pipeline": {"style": "jenkins"}, "parameter_schema": []},
    )
    monkeypatch.setattr(release, "resolve_release_repo", lambda repo: SimpleNamespace(repo_name="sample-repo"))
    monkeypatch.setattr(
        release,
        "_resolve_release_credentials_interactive",
        lambda require_github_token: (SimpleNamespace(email="user@example.com"), "config"),
    )

    def _fake_run_make_release(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(build_url="", pull_request_url="", message="done")

    monkeypatch.setattr(release, "run_make_release", _fake_run_make_release)

    result = runner.invoke(app, ["release"])

    assert result.exit_code == 0
    assert "1. feature-to-dev" in result.output
    assert "2. make-release" in result.output
    assert captured["repo_name"] == "sample-repo"
    assert captured["release_type"] == "bugfix"
    assert captured["parent"] == "REL-123>Summary"
    assert getattr(captured["credentials"], "email") == "user@example.com"
    params = captured["params"]
    assert params == {
        "TESTED_OK_ON_UAT": "true",
        "MERGE_PR": "false",
        "TAG_RELEASE": "true",
        "DEPLOY": "false",
    }
    assert all(not isinstance(value, OptionInfo) for value in captured.values())
    assert all(not isinstance(value, OptionInfo) for value in params.values())


def test_release_root_requires_subcommand_in_non_interactive_mode() -> None:
    res = runner.invoke(app, ["--non-interactive", "release"])

    assert res.exit_code == 1
    assert "No release action was provided." in str(res.exception)


def test_plan_binaries_cli_passes_version_override(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(release, "plan_binaries_release", lambda **kwargs: captured.update(kwargs) or SimpleNamespace(
        to_dict=lambda: {
            "tag": "v1.2.3",
            "version_override": "v1.2.3",
        },
        source_ref="develop",
        repo_name_with_owner="gh-org-data-platform/dp-tools-local-setup",
        tag="v1.2.3",
        install_flavor="standard",
        version_override="v1.2.3",
        latest_stable_tag="v1.2.2",
        prerelease=False,
        draft=False,
        build_target=SimpleNamespace(asset="ghdp-linux-amd64", built_path="dist/ghdp"),
        summary_file=Path("notes.md"),
    ))

    result = runner.invoke(
        app,
        ["release", "plan-binaries", "--source-ref", "develop", "--version-override", "v1.2.3"],
    )

    assert result.exit_code == 0
    assert captured["version_override"] == "v1.2.3"
    assert "version override: v1.2.3" in result.output


def test_prepare_binaries_release_cli_reports_stable_only_override(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = SimpleNamespace(
        source_ref="develop",
        repo_name_with_owner="gh-org-data-platform/dp-tools-local-setup",
        tag="v1.2.3",
        install_flavor="standard",
        version_override="v1.2.3",
        latest_stable_tag="v1.2.2",
        prerelease=False,
        draft=False,
        build_target=SimpleNamespace(asset="ghdp-linux-amd64", built_path="dist/ghdp"),
        summary_file=Path("notes.md"),
    )

    monkeypatch.setattr(release, "plan_binaries_release", lambda **kwargs: plan)
    monkeypatch.setattr(
        release,
        "ensure_binaries_release",
        lambda planned: {
            "tag": planned.tag,
            "release_repo": planned.repo_name_with_owner,
            "source_ref": planned.source_ref,
            "prerelease": planned.prerelease,
            "draft": planned.draft,
        },
    )
    monkeypatch.setattr(release, "write_prepare_outputs_if_supported", lambda planned: False)

    result = runner.invoke(
        app,
        ["release", "prepare-binaries-release", "--source-ref", "develop", "--version-override", "v1.2.3"],
    )

    assert result.exit_code == 0
    assert "version override: v1.2.3" in result.output


def test_prepare_binaries_release_allows_ci_bypass_when_policy_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy_path = tmp_path / "release-policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "default_channel": "stable",
                "channels": {
                    "stable": {
                        "blocked_commands": {
                            "release prepare-binaries-release": {
                                "message": "Blocked outside CI.",
                            }
                        }
                    },
                    "prerelease": {"blocked_commands": {}},
                },
            }
        ),
        encoding="utf-8",
    )
    plan = SimpleNamespace(
        source_ref="develop",
        repo_name_with_owner="gh-org-data-platform/dp-tools-local-setup",
        tag="v1.2.3",
        install_flavor="standard",
        version_override="",
        latest_stable_tag="v1.2.2",
        prerelease=False,
        draft=False,
        build_target=SimpleNamespace(asset="ghdp-linux-amd64", built_path="dist/ghdp"),
        summary_file=Path("notes.md"),
    )

    monkeypatch.setattr(platform_cli, "__channel__", "stable")
    monkeypatch.setattr(release, "plan_binaries_release", lambda **kwargs: plan)
    monkeypatch.setattr(
        release,
        "ensure_binaries_release",
        lambda planned: {
            "tag": planned.tag,
            "release_repo": planned.repo_name_with_owner,
            "source_ref": planned.source_ref,
            "prerelease": planned.prerelease,
            "draft": planned.draft,
        },
    )
    monkeypatch.setattr(release, "write_prepare_outputs_if_supported", lambda planned: False)

    result = runner.invoke(
        app,
        ["release", "prepare-binaries-release", "--source-ref", "develop"],
        env={
            "GHDP_RELEASE_POLICY_PATH": str(policy_path),
            "GITHUB_ACTIONS": "true",
        },
    )

    assert result.exit_code == 0
    assert "Prepared release: v1.2.3" in result.output


def test_config_jenkins_api_token_sets_and_clears_value(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(config_cli, "set_value", lambda key, value: captured.update({"set": (key, value)}))
    monkeypatch.setattr(config_cli, "delete_value", lambda key: captured.update({"delete": key}))

    result = runner.invoke(app, ["config", "jenkins-api-token", "--token", "secret-token"])
    assert result.exit_code == 0
    assert captured["set"] == ("jenkins.api_token", "secret-token")

    captured.clear()
    result = runner.invoke(app, ["config", "jenkins-api-token", "--clear"])
    assert result.exit_code == 0
    assert captured["delete"] == "jenkins.api_token"


def test_display_config_value_masks_jenkins_api_token() -> None:
    assert config_cli._display_config_value("jenkins.api_token", "secret-token") == "'***'"
    assert config_cli._display_config_value("jenkins.api_token", "") == "''"


def test_config_claude_athena_workgroup_sets_clears_and_shows_value(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        config_cli,
        "set_saved_athena_workgroup",
        lambda value: captured.update({"set": value}) or value.strip(),
    )
    monkeypatch.setattr(
        config_cli,
        "clear_saved_athena_workgroup",
        lambda: captured.update({"cleared": True}),
    )
    monkeypatch.setattr(
        config_cli,
        "get_saved_athena_workgroup",
        lambda: "wg-configured",
    )

    result = runner.invoke(app, ["config", "claude-athena-workgroup", "--value", "wg-configured"])
    assert result.exit_code == 0
    assert captured["set"] == "wg-configured"

    result = runner.invoke(app, ["config", "claude-athena-workgroup"])
    assert result.exit_code == 0
    assert "wg-configured" in result.output

    result = runner.invoke(app, ["config", "claude-athena-workgroup", "--clear"])
    assert result.exit_code == 0
    assert captured["cleared"] is True
