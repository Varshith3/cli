from __future__ import annotations

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from platform_cli.cli import app
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.tools.aws_profile import AwsProfileResolution
from platform_cli.tools.athena_workgroup import AthenaWorkgroupResolution
from platform_cli.tools.claude_passthrough import run_claude_launch


runner = CliRunner()


@pytest.fixture
def reset_cli_ctx():
    cli_ctx.non_interactive = False
    cli_ctx.verbose = False
    cli_ctx.quiet = False
    cli_ctx.json = False
    yield
    cli_ctx.non_interactive = False
    cli_ctx.verbose = False
    cli_ctx.quiet = False
    cli_ctx.json = False


def test_claude_passthrough_remains_plain(monkeypatch, reset_cli_ctx) -> None:
    captured: dict[str, object] = {}

    def _fake_run(args):
        captured["args"] = list(args)
        return 7

    monkeypatch.setattr("platform_cli.commands.claude.run_claude_passthrough", _fake_run)

    result = runner.invoke(app, ["claude", "--version"])

    assert result.exit_code == 7
    assert captured["args"] == ["--version"]


def test_claude_launch_uses_resolved_profile_without_prompt(monkeypatch, reset_cli_ctx) -> None:
    monkeypatch.setattr(
        "platform_cli.tools.claude_passthrough.resolve_aws_profile",
        lambda **_kwargs: AwsProfileResolution(profile="platform-dev", source="global", repo_key="repo"),
    )
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        "platform_cli.tools.claude_passthrough.typer.confirm",
        lambda *args, **kwargs: observed.setdefault("confirm", True),
    )
    monkeypatch.setattr(
        "platform_cli.tools.claude_passthrough.prompt_aws_profile_choice",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not prompt when keeping resolved profile")),
    )
    monkeypatch.setattr(
        "platform_cli.tools.claude_passthrough.resolve_athena_workgroup",
        lambda aws_profile: AthenaWorkgroupResolution(
            workgroup="wg-platform",
            source="config",
            aws_profile=aws_profile,
            account_id="",
            role_name="",
            mapping_source="",
            fallback_active=False,
            persisted=False,
            configured=True,
            detail_message="resolved",
        ),
    )
    monkeypatch.setattr("platform_cli.tools.claude_passthrough.ensure_sso_configured", lambda profile: observed.setdefault("configured", profile))
    monkeypatch.setattr("platform_cli.tools.claude_passthrough.aws_sso_token_status", lambda profile: ("valid", ""))
    monkeypatch.setattr(
        "platform_cli.tools.claude_passthrough.aws_sso_login",
        lambda profile: (_ for _ in ()).throw(AssertionError("valid token should skip login")),
    )

    captured: dict[str, object] = {}

    def _fake_run(cmd, check=False, capture=False, env=None, **_kwargs):
        captured["cmd"] = list(cmd)
        captured["env"] = dict(env or {})
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("platform_cli.tools.claude_passthrough.run_cmd", _fake_run)

    exit_code = run_claude_launch(["--help"])

    assert exit_code == 0
    assert captured["cmd"] == ["claude", "--help"]
    assert captured["env"]["AWS_PROFILE"] == "platform-dev"
    assert captured["env"]["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert captured["env"]["AWS_REGION"] == "us-west-2"
    assert captured["env"]["DP_AWS_ATHENA_WORKGROUP"] == "wg-platform"
    assert observed["confirm"] is True
    assert observed["configured"] == "platform-dev"


def test_claude_launch_prompts_when_resolution_is_default_and_user_switches(monkeypatch, reset_cli_ctx) -> None:
    monkeypatch.setattr(
        "platform_cli.tools.claude_passthrough.resolve_aws_profile",
        lambda **_kwargs: AwsProfileResolution(profile="default", source="default", repo_key="repo"),
    )
    monkeypatch.setattr("platform_cli.tools.claude_passthrough.typer.confirm", lambda *args, **kwargs: False)
    monkeypatch.setattr("platform_cli.tools.claude_passthrough.prompt_aws_profile_choice", lambda **_kwargs: "aws-mcp")
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        "platform_cli.tools.claude_passthrough.resolve_athena_workgroup",
        lambda aws_profile: AthenaWorkgroupResolution(
            workgroup="",
            source="deferred",
            aws_profile=aws_profile,
            account_id="",
            role_name="",
            mapping_source="",
            fallback_active=False,
            persisted=False,
            configured=False,
            detail_message="deferred",
        ),
    )
    monkeypatch.setattr("platform_cli.tools.claude_passthrough.ensure_sso_configured", lambda profile: observed.setdefault("configured", profile))
    monkeypatch.setattr("platform_cli.tools.claude_passthrough.aws_sso_token_status", lambda profile: ("invalid", "expired"))
    monkeypatch.setattr("platform_cli.tools.claude_passthrough.aws_sso_login", lambda profile: observed.setdefault("login", profile))

    captured: dict[str, object] = {}

    def _fake_run(cmd, check=False, capture=False, env=None, **_kwargs):
        captured["cmd"] = list(cmd)
        captured["env"] = dict(env or {})
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("platform_cli.tools.claude_passthrough.run_cmd", _fake_run)

    exit_code = run_claude_launch(["doctor"])

    assert exit_code == 0
    assert captured["cmd"] == ["claude", "doctor"]
    assert captured["env"]["AWS_PROFILE"] == "aws-mcp"
    assert "DP_AWS_ATHENA_WORKGROUP" not in captured["env"]
    assert observed["configured"] == "aws-mcp"
    assert observed["login"] == "aws-mcp"


def test_claude_launch_choose_profile_forces_picker(monkeypatch, reset_cli_ctx) -> None:
    monkeypatch.setattr(
        "platform_cli.tools.claude_passthrough.resolve_aws_profile",
        lambda **_kwargs: AwsProfileResolution(profile="repo-profile", source="repo", repo_key="repo"),
    )
    monkeypatch.setattr("platform_cli.tools.claude_passthrough.prompt_aws_profile_choice", lambda **_kwargs: "picked-profile")
    monkeypatch.setattr(
        "platform_cli.tools.claude_passthrough.resolve_athena_workgroup",
        lambda aws_profile: AthenaWorkgroupResolution(
            workgroup="wg-picked",
            source="config",
            aws_profile=aws_profile,
            account_id="",
            role_name="",
            mapping_source="",
            fallback_active=False,
            persisted=False,
            configured=True,
            detail_message="resolved",
        ),
    )
    monkeypatch.setattr(
        "platform_cli.tools.claude_passthrough.typer.confirm",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("choose-profile should bypass confirm")),
    )
    monkeypatch.setattr("platform_cli.tools.claude_passthrough.ensure_sso_configured", lambda profile: None)
    monkeypatch.setattr("platform_cli.tools.claude_passthrough.aws_sso_token_status", lambda profile: ("valid", ""))
    monkeypatch.setattr(
        "platform_cli.tools.claude_passthrough.aws_sso_login",
        lambda profile: (_ for _ in ()).throw(AssertionError("valid token should skip login")),
    )

    captured: dict[str, object] = {}

    def _fake_run(cmd, check=False, capture=False, env=None, **_kwargs):
        captured["env"] = dict(env or {})
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("platform_cli.tools.claude_passthrough.run_cmd", _fake_run)

    exit_code = run_claude_launch([], choose_profile=True)

    assert exit_code == 0
    assert captured["env"]["AWS_PROFILE"] == "picked-profile"
    assert captured["env"]["DP_AWS_ATHENA_WORKGROUP"] == "wg-picked"


def test_claude_launch_explicit_profile_skips_confirmation(monkeypatch, reset_cli_ctx) -> None:
    monkeypatch.setattr(
        "platform_cli.tools.claude_passthrough.resolve_aws_profile",
        lambda **_kwargs: AwsProfileResolution(profile="aws-explicit", source="flag", repo_key="repo"),
    )
    monkeypatch.setattr(
        "platform_cli.tools.claude_passthrough.typer.confirm",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("explicit profile should bypass confirm")),
    )
    monkeypatch.setattr(
        "platform_cli.tools.claude_passthrough.prompt_aws_profile_choice",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("explicit profile should bypass picker")),
    )
    monkeypatch.setattr(
        "platform_cli.tools.claude_passthrough.resolve_athena_workgroup",
        lambda aws_profile: AthenaWorkgroupResolution(
            workgroup="wg-explicit",
            source="config",
            aws_profile=aws_profile,
            account_id="",
            role_name="",
            mapping_source="",
            fallback_active=False,
            persisted=False,
            configured=True,
            detail_message="resolved",
        ),
    )
    monkeypatch.setattr("platform_cli.tools.claude_passthrough.ensure_sso_configured", lambda profile: None)
    monkeypatch.setattr("platform_cli.tools.claude_passthrough.aws_sso_token_status", lambda profile: ("valid", ""))
    monkeypatch.setattr(
        "platform_cli.tools.claude_passthrough.aws_sso_login",
        lambda profile: (_ for _ in ()).throw(AssertionError("valid token should skip login")),
    )

    captured: dict[str, object] = {}

    def _fake_run(cmd, check=False, capture=False, env=None, **_kwargs):
        captured["env"] = dict(env or {})
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("platform_cli.tools.claude_passthrough.run_cmd", _fake_run)

    exit_code = run_claude_launch(["--help"], explicit_profile="aws-explicit")

    assert exit_code == 0
    assert captured["env"]["AWS_PROFILE"] == "aws-explicit"


def test_claude_launch_command_forwards_profile_and_args(monkeypatch, reset_cli_ctx) -> None:
    captured: dict[str, object] = {}

    def _fake_launch(args, *, explicit_profile=None, choose_profile=False):
        captured["args"] = list(args)
        captured["profile"] = explicit_profile
        captured["choose"] = choose_profile
        return 0

    monkeypatch.setattr("platform_cli.commands.claude.run_claude_launch", _fake_launch)

    result = runner.invoke(app, ["claude-launch", "--profile", "aws-mcp", "--choose-profile", "--", "--help"])

    assert result.exit_code == 0
    assert captured["args"] == ["--help"]
    assert captured["profile"] == "aws-mcp"
    assert captured["choose"] is True
