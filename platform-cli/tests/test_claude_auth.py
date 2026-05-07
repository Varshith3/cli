from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.config import get_value, set_value
from platform_cli.core.errors import PlatformError
from platform_cli.state.store import get_tool_state
from platform_cli.tools.athena_workgroup import AthenaWorkgroupResolution, resolve_athena_workgroup
from platform_cli.tools import claude_auth
from platform_cli.tools.claude_skill_sync import sync_aws_readonly_skill
from platform_cli.tools.service import ToolOnboardingStatus, ToolRuntimeSpec, install_tool


class RunStub:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, check=True, capture=True, text=True, timeout_s=None, env=None, cwd=None):
        self.calls.append(list(cmd))
        if cmd[:1] == ["detect"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="", cmd=cmd)
        if cmd[:1] == ["version"]:
            return SimpleNamespace(returncode=0, stdout="1.2.3", stderr="", cmd=cmd)
        if cmd[:1] == ["install"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="", cmd=cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="", cmd=cmd)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    cli_ctx.non_interactive = False
    cli_ctx.verbose = False
    cli_ctx.quiet = False
    cli_ctx.json = False
    return tmp_path


def test_sync_aws_readonly_skill_uses_generic_release_content(monkeypatch):
    captured = {}

    def _fake_install(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return {
            "capability": "claude-skills-aws",
            "target_path": "/tmp/claude",
            "file_count": 2,
            "updated_count": 2,
            "content_hash": "abc",
            "synced_at": 123,
            "source": "release",
            "release_repo": "owner/repo",
            "release_tag": "v1.0.0",
            "content_version": "1.0.0",
        }

    monkeypatch.setattr("platform_cli.tools.claude_skill_sync.install_release_content", _fake_install)

    result = sync_aws_readonly_skill()

    assert captured["capability"] == "claude-skills-aws"
    assert captured["repo"] == "gh-org-data-platform/dp-tools-local-setup"
    assert captured["tag"] == "claude-skills-aws-v1.0.0"
    assert captured["manifest_asset"] == "content-manifest.json"
    assert result["skill_name"] == "aws-readonly-runbook"
    assert result["source"] == "release"


def test_sync_aws_readonly_skill_raises_on_download_failure(monkeypatch):
    def _failing_install(**kwargs):  # type: ignore[no-untyped-def]
        raise PlatformError("download failed", code="E_RELEASE_CONTENT_DOWNLOAD_FAILED", reason="content-manifest.json")

    monkeypatch.setattr("platform_cli.tools.claude_skill_sync.install_release_content", _failing_install)

    with pytest.raises(PlatformError) as err:
        sync_aws_readonly_skill()

    assert err.value.code == "E_CLAUDE_SKILL_SYNC_FAILED"


def test_resolve_athena_workgroup_prefers_env_without_persisting(isolated_home, monkeypatch):
    monkeypatch.setenv("DP_AWS_ATHENA_WORKGROUP", "wg-env")

    resolution = resolve_athena_workgroup(aws_profile="dp-test")

    assert resolution.workgroup == "wg-env"
    assert resolution.source == "env"
    assert resolution.persisted is False
    assert resolution.configured is True
    assert get_value("claude.athena_workgroup", "") == ""


def test_resolve_athena_workgroup_uses_saved_config_when_mapping_does_not_match(isolated_home, monkeypatch):
    monkeypatch.delenv("DP_AWS_ATHENA_WORKGROUP", raising=False)
    set_value("claude.athena_workgroup", "wg-config")
    monkeypatch.setattr(
        "platform_cli.tools.athena_workgroup.load_claude_athena_workgroup_map",
        lambda: (
            [{"account_id": "626645654318", "role_name": "dp-md-rwe-data-engineer", "athena_workgroup": "wg-derived"}],
            "pkg:claude/athena-workgroup-map.json",
            False,
        ),
    )
    monkeypatch.setattr(
        "platform_cli.tools.athena_workgroup.run_aws_cli",
        lambda *args, **kwargs: SimpleNamespace(
            stdout='{"Account":"617336469044","Arn":"arn:aws:sts::617336469044:assumed-role/AWSReservedSSO_dp-md-rwe-data-engineer_abcd/user@example.com"}'
        ),
    )

    resolution = resolve_athena_workgroup(aws_profile="dp-test")

    assert resolution.workgroup == "wg-config"
    assert resolution.source == "config"
    assert resolution.account_id == "617336469044"
    assert resolution.role_name == "dp-md-rwe-data-engineer"
    assert resolution.configured is True


def test_resolve_athena_workgroup_derives_from_account_and_role_mapping(isolated_home, monkeypatch):
    monkeypatch.delenv("DP_AWS_ATHENA_WORKGROUP", raising=False)
    set_value("claude.athena_workgroup", "wg-config")
    monkeypatch.setattr(
        "platform_cli.tools.athena_workgroup.load_claude_athena_workgroup_map",
        lambda: (
            [{"account_id": "617336469044", "role_name": "dp-md-rwe-data-engineer", "athena_workgroup": "wg-derived"}],
            "pkg:claude/athena-workgroup-map.json",
            False,
        ),
    )
    monkeypatch.setattr(
        "platform_cli.tools.athena_workgroup.run_aws_cli",
        lambda *args, **kwargs: SimpleNamespace(
            stdout='{"Account":"617336469044","Arn":"arn:aws:sts::617336469044:assumed-role/AWSReservedSSO_dp-md-rwe-data-engineer_abcd/user@example.com"}'
        ),
    )

    resolution = resolve_athena_workgroup(aws_profile="dp-test")

    assert resolution.workgroup == "wg-derived"
    assert resolution.source == "derived"
    assert resolution.account_id == "617336469044"
    assert resolution.role_name == "dp-md-rwe-data-engineer"
    assert resolution.persisted is True
    assert resolution.configured is True
    assert get_value("claude.athena_workgroup", "") == "wg-derived"


def test_resolve_athena_workgroup_prompts_and_persists_when_no_other_source_matches(isolated_home, monkeypatch):
    messages: list[str] = []
    monkeypatch.delenv("DP_AWS_ATHENA_WORKGROUP", raising=False)
    monkeypatch.setattr(
        "platform_cli.tools.athena_workgroup.load_claude_athena_workgroup_map",
        lambda: (
            [{"account_id": "626645654318", "role_name": "dp-md-rwe-data-engineer", "athena_workgroup": "wg-derived"}],
            "pkg:claude/athena-workgroup-map.json",
            False,
        ),
    )
    monkeypatch.setattr(
        "platform_cli.tools.athena_workgroup.run_aws_cli",
        lambda *args, **kwargs: SimpleNamespace(
            stdout='{"Account":"617336469044","Arn":"arn:aws:sts::617336469044:assumed-role/AWSReservedSSO_dp-md-rwe-data-engineer_abcd/user@example.com"}'
        ),
    )
    monkeypatch.setattr("platform_cli.tools.athena_workgroup.typer.confirm", lambda *args, **kwargs: True)
    monkeypatch.setattr("platform_cli.tools.athena_workgroup.typer.prompt", lambda *args, **kwargs: "wg-prompt")

    resolution = resolve_athena_workgroup(aws_profile="dp-test", status_printer=messages.append)

    assert resolution.workgroup == "wg-prompt"
    assert resolution.source == "prompt"
    assert resolution.persisted is True
    assert resolution.configured is True
    assert get_value("claude.athena_workgroup", "") == "wg-prompt"
    assert "Resolving Claude Athena workgroup..." in messages
    assert "No Athena mapping match found; prompting for manual entry or skip..." in messages


def test_resolve_athena_workgroup_allows_skip_when_no_other_source_matches(isolated_home, monkeypatch):
    monkeypatch.delenv("DP_AWS_ATHENA_WORKGROUP", raising=False)
    monkeypatch.setattr(
        "platform_cli.tools.athena_workgroup.load_claude_athena_workgroup_map",
        lambda: (
            [{"account_id": "626645654318", "role_name": "dp-md-rwe-data-engineer", "athena_workgroup": "wg-derived"}],
            "pkg:claude/athena-workgroup-map.json",
            False,
        ),
    )
    monkeypatch.setattr(
        "platform_cli.tools.athena_workgroup.run_aws_cli",
        lambda *args, **kwargs: SimpleNamespace(
            stdout='{"Account":"617336469044","Arn":"arn:aws:sts::617336469044:assumed-role/AWSReservedSSO_dp-md-rwe-data-engineer_abcd/user@example.com"}'
        ),
    )
    monkeypatch.setattr("platform_cli.tools.athena_workgroup.typer.confirm", lambda *args, **kwargs: False)

    resolution = resolve_athena_workgroup(aws_profile="dp-test")

    assert resolution.source == "deferred"
    assert resolution.workgroup == ""
    assert resolution.persisted is True
    assert resolution.configured is False
    assert get_value("claude.athena_workgroup", "") == ""


def test_resolve_athena_workgroup_defers_missing_noninteractive(isolated_home, monkeypatch):
    cli_ctx.non_interactive = True
    monkeypatch.delenv("DP_AWS_ATHENA_WORKGROUP", raising=False)
    monkeypatch.setattr(
        "platform_cli.tools.athena_workgroup.load_claude_athena_workgroup_map",
        lambda: ([], "pkg:claude/athena-workgroup-map.json", False),
    )
    monkeypatch.setattr(
        "platform_cli.tools.athena_workgroup.run_aws_cli",
        lambda *args, **kwargs: SimpleNamespace(stdout='{"Account":"617336469044","Arn":"arn:aws:iam::617336469044:user/test"}'),
    )

    resolution = resolve_athena_workgroup(aws_profile="dp-test")

    assert resolution.source == "deferred"
    assert resolution.workgroup == ""
    assert resolution.configured is False
    assert "Configure it later" in resolution.detail_message


def test_resolve_athena_workgroup_allows_saved_config_when_mapping_source_is_invalid(isolated_home, monkeypatch):
    monkeypatch.delenv("DP_AWS_ATHENA_WORKGROUP", raising=False)
    set_value("claude.athena_workgroup", "wg-config")
    monkeypatch.setattr(
        "platform_cli.tools.athena_workgroup.load_claude_athena_workgroup_map",
        lambda: (_ for _ in ()).throw(
            PlatformError(
                "Invalid managed Claude Athena workgroup mapping: bad schema",
                code="E_MANIFEST_INVALID",
                reason="claude-map",
            )
        ),
    )

    resolution = resolve_athena_workgroup(aws_profile="dp-test")

    assert resolution.source == "config"
    assert resolution.workgroup == "wg-config"
    assert "mapping was unavailable" in resolution.detail_message


def test_resolve_athena_workgroup_can_skip_when_mapping_source_is_invalid(isolated_home, monkeypatch):
    monkeypatch.delenv("DP_AWS_ATHENA_WORKGROUP", raising=False)
    monkeypatch.setattr(
        "platform_cli.tools.athena_workgroup.load_claude_athena_workgroup_map",
        lambda: (_ for _ in ()).throw(
            PlatformError(
                "Invalid managed Claude Athena workgroup mapping: bad schema",
                code="E_MANIFEST_INVALID",
                reason="claude-map",
            )
        ),
    )
    monkeypatch.setattr("platform_cli.tools.athena_workgroup.typer.confirm", lambda *args, **kwargs: False)

    resolution = resolve_athena_workgroup(aws_profile="dp-test")

    assert resolution.source == "deferred"
    assert resolution.workgroup == ""
    assert resolution.configured is False
    assert "mapping was unavailable" in resolution.detail_message


def test_windows_persistence_sets_user_env_and_execution_policy(isolated_home, monkeypatch):
    calls = []

    def _fake_run(cmd, check=True, capture=True, text=True, timeout_s=None, env=None, cwd=None):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="", cmd=cmd)

    monkeypatch.setattr("platform_cli.tools.claude_auth.run_cmd", _fake_run)
    monkeypatch.setattr("platform_cli.tools.claude_auth._is_windows", lambda: True)

    claude_auth._ensure_windows_execution_policy()
    claude_auth._persist_windows_user_env("wg-win", "profile-win")

    joined = "\n".join(" ".join(cmd) for cmd in calls)
    assert "Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force" in joined
    assert "SetEnvironmentVariable('CLAUDE_CODE_USE_BEDROCK', '1', 'User')" in joined
    assert "SetEnvironmentVariable('AWS_REGION', 'us-west-2', 'User')" in joined
    assert "SetEnvironmentVariable('DP_AWS_ATHENA_WORKGROUP', 'wg-win', 'User')" in joined


def test_windows_same_session_launch_runs_claude_when_confirmed(isolated_home, monkeypatch):
    calls = []

    def _fake_run(cmd, check=True, capture=True, text=True, timeout_s=None, env=None, cwd=None):
        calls.append((list(cmd), bool(capture), dict(env or {})))
        return SimpleNamespace(returncode=0, stdout="", stderr="", cmd=cmd)

    monkeypatch.setattr("platform_cli.tools.claude_auth.run_cmd", _fake_run)
    monkeypatch.setattr("platform_cli.tools.claude_auth._is_windows", lambda: True)
    monkeypatch.setattr("platform_cli.tools.claude_auth.typer.confirm", lambda *args, **kwargs: True)
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    monkeypatch.setenv("AWS_REGION", "us-west-2")

    claude_auth._maybe_launch_claude_same_session("C:\\Users\\Hi\\.local\\bin\\claude.exe")

    assert calls
    cmd, capture, env = calls[0]
    assert cmd == ["C:\\Users\\Hi\\.local\\bin\\claude.exe"]
    assert capture is False
    assert env.get("CLAUDE_CODE_USE_BEDROCK") == "1"
    assert env.get("AWS_REGION") == "us-west-2"


def test_profile_block_includes_aws_profile_for_darwin(isolated_home, monkeypatch):
    monkeypatch.setattr("platform_cli.tools.claude_auth._is_windows", lambda: False)

    block = claude_auth._profile_block("wg-mac", "profile-mac")

    assert 'export DP_AWS_ATHENA_WORKGROUP="wg-mac"' in block
    assert "AWS_PROFILE" not in block


def test_profile_block_omits_workgroup_when_not_persisting(isolated_home, monkeypatch):
    monkeypatch.setattr("platform_cli.tools.claude_auth._is_windows", lambda: False)

    block = claude_auth._profile_block("wg-mac", "profile-mac", persist_workgroup=False)

    assert "AWS_PROFILE" not in block
    assert "DP_AWS_ATHENA_WORKGROUP" not in block


def test_upsert_profile_block_preserves_existing_workgroup_when_not_persisting(isolated_home, monkeypatch):
    monkeypatch.setattr("platform_cli.tools.claude_auth._is_windows", lambda: False)
    profile_path = isolated_home / ".zshrc"
    profile_path.write_text(
        "# Added by GHDP Claude bootstrap\n"
        "export CLAUDE_CODE_USE_BEDROCK=1\n"
        "export AWS_REGION=us-west-2\n"
        'export AWS_PROFILE="old-profile"\n'
        'export DP_AWS_ATHENA_WORKGROUP="wg-old"\n'
        'export PATH="$HOME/.local/bin:$PATH"\n'
        "# End GHDP Claude bootstrap\n",
        encoding="utf-8",
    )

    claude_auth._upsert_profile_block(
        profile_path,
        "wg-new",
        "profile-new",
        persist_workgroup=False,
        preserve_existing_workgroup=True,
    )

    text = profile_path.read_text(encoding="utf-8")
    assert "AWS_PROFILE" not in text
    assert 'export DP_AWS_ATHENA_WORKGROUP="wg-old"' in text
    assert 'export DP_AWS_ATHENA_WORKGROUP="wg-new"' not in text


def test_upsert_profile_block_clears_existing_workgroup_when_deferred(isolated_home, monkeypatch):
    monkeypatch.setattr("platform_cli.tools.claude_auth._is_windows", lambda: False)
    profile_path = isolated_home / ".zshrc"
    profile_path.write_text(
        "# Added by GHDP Claude bootstrap\n"
        "export CLAUDE_CODE_USE_BEDROCK=1\n"
        "export AWS_REGION=us-west-2\n"
        'export DP_AWS_ATHENA_WORKGROUP="wg-old"\n'
        'export PATH="$HOME/.local/bin:$PATH"\n'
        "# End GHDP Claude bootstrap\n",
        encoding="utf-8",
    )

    claude_auth._upsert_profile_block(profile_path, "", "profile-new", persist_workgroup=False)

    text = profile_path.read_text(encoding="utf-8")
    assert "DP_AWS_ATHENA_WORKGROUP" not in text


def test_sync_saved_claude_workgroup_runtime_sets_profile_and_process_env(isolated_home, monkeypatch):
    monkeypatch.setattr("platform_cli.tools.claude_auth._is_windows", lambda: False)
    monkeypatch.setenv("SHELL", "/bin/zsh")

    profile_path = claude_auth.sync_saved_claude_workgroup_runtime("wg-config-new")

    assert profile_path == isolated_home / ".zshrc"
    text = profile_path.read_text(encoding="utf-8")
    assert 'export DP_AWS_ATHENA_WORKGROUP="wg-config-new"' in text
    assert os.environ["DP_AWS_ATHENA_WORKGROUP"] == "wg-config-new"


def test_sync_saved_claude_workgroup_runtime_clears_profile_and_process_env(isolated_home, monkeypatch):
    monkeypatch.setattr("platform_cli.tools.claude_auth._is_windows", lambda: False)
    monkeypatch.setenv("SHELL", "/bin/zsh")
    profile_path = isolated_home / ".zshrc"
    profile_path.write_text(
        "# Added by GHDP Claude bootstrap\n"
        "export CLAUDE_CODE_USE_BEDROCK=1\n"
        "export AWS_REGION=us-west-2\n"
        'export DP_AWS_ATHENA_WORKGROUP="wg-old"\n'
        'export PATH="$HOME/.local/bin:$PATH"\n'
        "# End GHDP Claude bootstrap\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DP_AWS_ATHENA_WORKGROUP", "wg-old")

    returned_profile = claude_auth.sync_saved_claude_workgroup_runtime("")

    assert returned_profile == profile_path
    text = profile_path.read_text(encoding="utf-8")
    assert "DP_AWS_ATHENA_WORKGROUP" not in text
    assert "DP_AWS_ATHENA_WORKGROUP" not in os.environ


def test_print_unix_reload_hint_mentions_source_command(isolated_home, monkeypatch, capsys):
    monkeypatch.setattr("platform_cli.tools.claude_auth._is_windows", lambda: False)
    monkeypatch.setenv("SHELL", "/bin/zsh")

    claude_auth._print_unix_reload_hint(isolated_home / ".zshrc")
    out = capsys.readouterr().out

    assert "source ~/.zshrc" in out
    assert "open a new Terminal window" in out


def test_install_tool_runs_claude_post_step_when_already_installed(isolated_home, monkeypatch):
    stub = RunStub()
    calls = []

    monkeypatch.setattr("platform_cli.tools.service.run_cmd", stub)
    monkeypatch.setattr(
        "platform_cli.tools.service._run_claude_post_step",
        lambda spec, **_kwargs: (calls.append("claude_post") or ToolOnboardingStatus(spec.name, spec.display_name, "ready", "Installed and ready")),
    )
    cli_ctx.non_interactive = True

    spec = ToolRuntimeSpec(
        name="claude",
        display_name="Claude Code",
        detect_cmd=["detect"],
        version_cmd=["version"],
        install_cmd=["install"],
        upgrade_cmd=None,
        uninstall_cmd=None,
        version_req=None,
    )

    install_tool(spec, dry_run=False, upgrade=False, adopt_existing=False)

    st = get_tool_state("claude")
    assert calls == ["claude_post"]
    assert st["last_status"] == "skipped"


def test_resolve_claude_install_profile_confirms_resolved_profile(isolated_home, monkeypatch, capsys):
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth.resolve_aws_profile",
        lambda **_kwargs: claude_auth.AwsProfileResolution(
            profile="data-engg-md",
            source="global",
            repo_key="repo-key",
        ),
    )
    monkeypatch.setattr("platform_cli.tools.claude_auth.typer.confirm", lambda *args, **kwargs: True)

    resolved = claude_auth._resolve_claude_install_profile()

    assert resolved.profile == "data-engg-md"
    assert resolved.source == "global"
    out = capsys.readouterr().out
    assert "Claude install AWS profile: data-engg-md (source=global)" in out


def test_resolve_claude_install_profile_can_switch_profile(isolated_home, monkeypatch, capsys):
    set_calls = []
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth.resolve_aws_profile",
        lambda **_kwargs: claude_auth.AwsProfileResolution(
            profile="data-engg-md",
            source="global",
            repo_key="repo-key",
        ),
    )
    monkeypatch.setattr("platform_cli.tools.claude_auth.typer.confirm", lambda *args, **kwargs: False)
    monkeypatch.setattr("platform_cli.tools.claude_auth.prompt_aws_profile_choice", lambda **_kwargs: "aws-mcp")
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth.set_active_profile",
        lambda profile, scope="global": (set_calls.append((profile, scope)) or scope),
    )

    resolved = claude_auth._resolve_claude_install_profile()

    assert resolved.profile == "aws-mcp"
    assert resolved.source == "prompt"
    assert set_calls == [("aws-mcp", "global")]
    out = capsys.readouterr().out
    assert "Claude install AWS profile: aws-mcp (source=prompt)" in out


def test_resolve_claude_install_profile_noninteractive_skips_confirm(isolated_home, monkeypatch, capsys):
    cli_ctx.non_interactive = True
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth.resolve_aws_profile",
        lambda **_kwargs: claude_auth.AwsProfileResolution(
            profile="default",
            source="env",
            repo_key="repo-key",
        ),
    )
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth.typer.confirm",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("non-interactive should not confirm")),
    )

    resolved = claude_auth._resolve_claude_install_profile()

    assert resolved.profile == "default"
    assert resolved.source == "env"
    out = capsys.readouterr().out
    assert "Claude install AWS profile: default (source=env)" in out


def test_claude_bootstrap_records_release_sync_metadata(isolated_home, monkeypatch):
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth._resolve_claude_install_profile",
        lambda: claude_auth.AwsProfileResolution(profile="dp-test", source="global", repo_key="repo-key"),
    )
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth.maybe_bootstrap_aws_after_install",
        lambda profile=None: SimpleNamespace(profile=profile or "dp-test"),
    )
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth.ensure_claude_athena_workgroup_map_available",
        lambda: {
            "capability": "claude-athena-workgroup-map",
            "target_path": str(isolated_home / ".ghdp" / "policies" / "claude-athena-workgroup-map.managed.json"),
            "local_status": "cached",
            "latest_tag": "claude-athena-workgroup-map-v1.0.1",
            "latest_version": "1.0.1",
            "sync_result": {},
            "used_cached": True,
        },
    )
    monkeypatch.setattr("platform_cli.tools.claude_auth._profile_path", lambda: isolated_home / ".zshrc")
    monkeypatch.setattr("platform_cli.tools.claude_auth._is_windows", lambda: False)
    monkeypatch.setattr("platform_cli.tools.claude_auth._resolve_claude_exe", lambda: "/usr/local/bin/claude")
    monkeypatch.setattr("platform_cli.tools.claude_auth._claude_version", lambda exe: "1.2.3")
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth._resolve_athena_workgroup",
        lambda aws_profile, **_kwargs: AthenaWorkgroupResolution(
            workgroup="wg-test",
            source="derived",
            aws_profile=aws_profile,
            account_id="617336469044",
            role_name="dp-md-rwe-data-engineer",
            mapping_source="pkg:claude/athena-workgroup-map.json",
            fallback_active=False,
            persisted=True,
            configured=True,
            detail_message="Resolved Athena workgroup from AWS identity.",
        ),
    )
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth.sync_aws_readonly_skill",
        lambda: {
            "skill_name": "aws-readonly-runbook",
            "target_path": str(isolated_home / ".claude" / "skills" / "aws-readonly-runbook"),
            "file_count": 2,
            "updated_count": 1,
            "content_hash": "abc123",
            "synced_at": 111,
            "source": "release",
            "release_repo": "gh-org-data-platform/dp-tools-local-setup",
            "release_tag": "claude-skills-aws-v1.0.0",
            "content_version": "1.0.0",
        },
    )
    monkeypatch.setattr("platform_cli.tools.claude_auth._claude_health_status", lambda exe: (True, "ok"))
    monkeypatch.setattr("platform_cli.tools.claude_auth._print_unix_reload_hint", lambda path: None)
    monkeypatch.setattr("platform_cli.tools.claude_auth._maybe_launch_claude_same_session", lambda exe: None)

    claude_auth.maybe_bootstrap_after_install()

    st = get_tool_state("claude")
    assert st["claude_skill_sync_source"] == "release"
    assert st["claude_skill_sync_release_tag"] == "claude-skills-aws-v1.0.0"
    assert st["claude_skill_sync_content_version"] == "1.0.0"
    assert st["claude_athena_map_local_status"] == "cached"
    assert st["claude_athena_map_latest_tag"] == "claude-athena-workgroup-map-v1.0.1"
    assert st["claude_athena_map_used_cached"] is True
    assert st["claude_athena_workgroup_source"] == "derived"
    assert st["claude_athena_workgroup_account_id"] == "617336469044"
    assert st["claude_athena_workgroup_role_name"] == "dp-md-rwe-data-engineer"


def test_claude_bootstrap_persists_derived_workgroup_to_shell_profile(isolated_home, monkeypatch):
    profile_path = isolated_home / ".zshrc"
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth._resolve_claude_install_profile",
        lambda: claude_auth.AwsProfileResolution(profile="dp-test", source="global", repo_key="repo-key"),
    )
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth.maybe_bootstrap_aws_after_install",
        lambda profile=None: SimpleNamespace(profile=profile or "dp-test"),
    )
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth.ensure_claude_athena_workgroup_map_available",
        lambda: {"local_status": "cached", "sync_result": {}, "used_cached": True},
    )
    monkeypatch.setattr("platform_cli.tools.claude_auth._profile_path", lambda: profile_path)
    monkeypatch.setattr("platform_cli.tools.claude_auth._is_windows", lambda: False)
    monkeypatch.setattr("platform_cli.tools.claude_auth._resolve_claude_exe", lambda: "/usr/local/bin/claude")
    monkeypatch.setattr("platform_cli.tools.claude_auth._claude_version", lambda exe: "1.2.3")
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth._resolve_athena_workgroup",
        lambda aws_profile, **_kwargs: AthenaWorkgroupResolution(
            workgroup="wg-derived",
            source="derived",
            aws_profile=aws_profile,
            account_id="617336469044",
            role_name="dp-md-rwe-data-engineer",
            mapping_source="pkg:claude/athena-workgroup-map.json",
            fallback_active=False,
            persisted=True,
            configured=True,
            detail_message="Resolved Athena workgroup from AWS identity.",
        ),
    )
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth.sync_aws_readonly_skill",
        lambda: {
            "skill_name": "aws-readonly-runbook",
            "target_path": str(isolated_home / ".claude" / "skills" / "aws-readonly-runbook"),
            "file_count": 2,
            "updated_count": 1,
            "content_hash": "abc123",
            "synced_at": 111,
            "source": "release",
            "release_repo": "gh-org-data-platform/dp-tools-local-setup",
            "release_tag": "claude-skills-aws-v1.0.0",
            "content_version": "1.0.0",
        },
    )
    monkeypatch.setattr("platform_cli.tools.claude_auth._claude_health_status", lambda exe: (True, "ok"))
    monkeypatch.setattr("platform_cli.tools.claude_auth._print_unix_reload_hint", lambda path: None)
    monkeypatch.setattr("platform_cli.tools.claude_auth._maybe_launch_claude_same_session", lambda exe: None)

    claude_auth.maybe_bootstrap_after_install()

    text = profile_path.read_text(encoding="utf-8")
    assert "AWS_PROFILE" not in text
    assert 'export DP_AWS_ATHENA_WORKGROUP="wg-derived"' in text
    assert os.environ["DP_AWS_ATHENA_WORKGROUP"] == "wg-derived"
    st = get_tool_state("claude")
    assert st["claude_athena_workgroup_shell_persisted"] is True


def test_claude_bootstrap_keeps_install_deferred_when_workgroup_is_skipped(isolated_home, monkeypatch):
    profile_path = isolated_home / ".zshrc"
    profile_path.write_text(
        "# Added by GHDP Claude bootstrap\n"
        "export CLAUDE_CODE_USE_BEDROCK=1\n"
        "export AWS_REGION=us-west-2\n"
        'export DP_AWS_ATHENA_WORKGROUP="wg-old"\n'
        'export PATH="$HOME/.local/bin:$PATH"\n'
        "# End GHDP Claude bootstrap\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth._resolve_claude_install_profile",
        lambda: claude_auth.AwsProfileResolution(profile="dp-test", source="global", repo_key="repo-key"),
    )
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth.maybe_bootstrap_aws_after_install",
        lambda profile=None: SimpleNamespace(profile=profile or "dp-test"),
    )
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth.ensure_claude_athena_workgroup_map_available",
        lambda: {"local_status": "fallback", "sync_result": {"action": "warning"}, "used_cached": False},
    )
    monkeypatch.setattr("platform_cli.tools.claude_auth._profile_path", lambda: profile_path)
    monkeypatch.setattr("platform_cli.tools.claude_auth._is_windows", lambda: False)
    monkeypatch.setattr("platform_cli.tools.claude_auth._resolve_claude_exe", lambda: "/usr/local/bin/claude")
    monkeypatch.setattr("platform_cli.tools.claude_auth._claude_version", lambda exe: "1.2.3")
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth._resolve_athena_workgroup",
        lambda aws_profile, **_kwargs: AthenaWorkgroupResolution(
            workgroup="",
            source="deferred",
            aws_profile=aws_profile,
            account_id="617336469044",
            role_name="dp-md-rwe-data-engineer",
            mapping_source="managed:/tmp/claude-athena-workgroup-map.managed.json",
            fallback_active=False,
            persisted=True,
            configured=False,
            detail_message="Skipped for now.",
        ),
    )
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth.sync_aws_readonly_skill",
        lambda: {
            "skill_name": "aws-readonly-runbook",
            "target_path": str(isolated_home / ".claude" / "skills" / "aws-readonly-runbook"),
            "file_count": 2,
            "updated_count": 1,
            "content_hash": "abc123",
            "synced_at": 111,
            "source": "release",
            "release_repo": "gh-org-data-platform/dp-tools-local-setup",
            "release_tag": "claude-skills-aws-v1.0.0",
            "content_version": "1.0.0",
        },
    )
    monkeypatch.setattr("platform_cli.tools.claude_auth._claude_health_status", lambda exe: (True, "ok"))
    monkeypatch.setattr("platform_cli.tools.claude_auth._print_unix_reload_hint", lambda path: None)
    monkeypatch.setattr("platform_cli.tools.claude_auth._maybe_launch_claude_same_session", lambda exe: None)

    claude_auth.maybe_bootstrap_after_install()

    text = profile_path.read_text(encoding="utf-8")
    assert "DP_AWS_ATHENA_WORKGROUP" not in text
    assert "DP_AWS_ATHENA_WORKGROUP" not in os.environ
    st = get_tool_state("claude")
    assert st["claude_athena_workgroup_source"] == "deferred"
    assert st["claude_athena_workgroup_configured"] is False
    assert st["claude_athena_workgroup_shell_persisted"] is False


def test_windows_profile_block_uses_safe_join_path_for_claude_bin(isolated_home, monkeypatch):
    monkeypatch.setattr("platform_cli.tools.claude_auth._is_windows", lambda: True)

    block = claude_auth._profile_block("wg-win", "profile-win")

    assert "Join-Path (Join-Path $env:USERPROFILE '.local') 'bin'" in block
    assert ".local\\bin" not in block


def test_claude_bootstrap_emits_progress_updates(isolated_home, monkeypatch):
    messages: list[str] = []

    monkeypatch.setattr(
        "platform_cli.tools.claude_auth._resolve_claude_install_profile",
        lambda: claude_auth.AwsProfileResolution(profile="dp-test", source="global", repo_key="repo-key"),
    )
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth.maybe_bootstrap_aws_after_install",
        lambda profile=None: SimpleNamespace(profile=profile or "dp-test"),
    )
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth.ensure_claude_athena_workgroup_map_available",
        lambda: {"local_status": "cached", "sync_result": {}, "used_cached": True},
    )
    monkeypatch.setattr("platform_cli.tools.claude_auth._profile_path", lambda: isolated_home / ".zshrc")
    monkeypatch.setattr("platform_cli.tools.claude_auth._is_windows", lambda: False)
    monkeypatch.setattr("platform_cli.tools.claude_auth._resolve_claude_exe", lambda: "/usr/local/bin/claude")
    monkeypatch.setattr("platform_cli.tools.claude_auth._claude_version", lambda exe: "1.2.3")
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth._resolve_athena_workgroup",
        lambda aws_profile, **_kwargs: AthenaWorkgroupResolution(
            workgroup="wg-derived",
            source="derived",
            aws_profile=aws_profile,
            account_id="617336469044",
            role_name="dp-md-rwe-data-engineer",
            mapping_source="pkg:claude/athena-workgroup-map.json",
            fallback_active=False,
            persisted=True,
            configured=True,
            detail_message="Resolved Athena workgroup from AWS identity.",
        ),
    )
    monkeypatch.setattr(
        "platform_cli.tools.claude_auth.sync_aws_readonly_skill",
        lambda: {
            "skill_name": "aws-readonly-runbook",
            "target_path": str(isolated_home / ".claude" / "skills" / "aws-readonly-runbook"),
            "file_count": 2,
            "updated_count": 1,
            "content_hash": "abc123",
            "synced_at": 111,
            "source": "release",
            "release_repo": "gh-org-data-platform/dp-tools-local-setup",
            "release_tag": "claude-skills-aws-v1.0.0",
            "content_version": "1.0.0",
        },
    )
    monkeypatch.setattr("platform_cli.tools.claude_auth._claude_health_status", lambda exe: (True, "ok"))
    monkeypatch.setattr("platform_cli.tools.claude_auth._print_unix_reload_hint", lambda path: None)
    monkeypatch.setattr("platform_cli.tools.claude_auth._maybe_launch_claude_same_session", lambda exe: None)

    claude_auth.maybe_bootstrap_after_install(status_printer=messages.append)

    assert "Checking Claude AWS profile..." in messages
    assert "Ensuring AWS SSO is ready for profile 'dp-test'..." in messages
    assert "Using cached Claude Athena workgroup mapping." in messages
    assert "Writing Claude environment and profile settings..." in messages
    assert "Refreshing Claude AWS helper content..." in messages
    assert "Running Claude health check..." in messages
