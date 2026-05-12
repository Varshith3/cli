from __future__ import annotations

from types import SimpleNamespace

import pytest

from platform_cli.commands import tools as tools_cmd
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.errors import PlatformError
from platform_cli.state.store import update_tool_state
from platform_cli.tools import service as tool_service
from platform_cli.tools.service import ToolOnboardingStatus, ToolRuntimeSpec, install_tool


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    cli_ctx.non_interactive = True
    cli_ctx.verbose = False
    cli_ctx.quiet = False
    cli_ctx.json = False
    return tmp_path


def _spec(name: str) -> ToolRuntimeSpec:
    return ToolRuntimeSpec(
        name=name,
        display_name=name.upper(),
        detect_cmd=["detect"],
        version_cmd=["version"],
        install_cmd=["install"],
        upgrade_cmd=["upgrade"],
        uninstall_cmd=["uninstall"],
        version_req=None,
    )


def test_install_tool_returns_action_required_when_codex_login_is_deferred(isolated_home, monkeypatch) -> None:
    monkeypatch.setattr("platform_cli.tools.service.detect_tool", lambda spec: (True, "1.0.0"))

    def _fake_codex_bootstrap() -> None:
        update_tool_state(
            "codex",
            {
                "codex_login_state": "deferred",
                "codex_login_status": "Waiting for interactive login",
            },
        )

    monkeypatch.setattr("platform_cli.tools.service.maybe_bootstrap_codex_after_install", _fake_codex_bootstrap)
    monkeypatch.setattr(
        "platform_cli.tools.service.sync_user_global_agent_config",
        lambda tool_name: SimpleNamespace(action="updated", path=f"/tmp/{tool_name}.md"),
    )

    result = install_tool(_spec("codex"), dry_run=False, upgrade=False, adopt_existing=False)

    assert result.status == "action_required"
    assert result.short_status == "Installed, but Codex login still needs interactive setup"
    assert "ghdp tools install --tool codex" in result.next_action


def test_install_tool_returns_action_required_when_jira_auth_needs_interactive(isolated_home, monkeypatch) -> None:
    monkeypatch.setattr("platform_cli.tools.service.detect_tool", lambda spec: (True, "1.0.0"))
    monkeypatch.setattr(
        "platform_cli.tools.service.maybe_bootstrap_jira_after_install",
        lambda: (_ for _ in ()).throw(
            PlatformError(
                "Atlassian CLI (acli) is installed but Jira authentication is not set up yet.",
                code="E_JIRA_AUTH_NEEDS_INTERACTIVE",
                reason="jira_auth",
            )
        ),
    )

    result = install_tool(_spec("acli"), dry_run=False, upgrade=False, adopt_existing=False)

    assert result.status == "action_required"
    assert result.short_status == "Installed, but Jira authentication still needs interactive setup"
    assert "ghdp tools install --tool acli" in result.next_action


def test_install_tool_returns_action_required_when_github_auth_needs_interactive(isolated_home, monkeypatch) -> None:
    monkeypatch.setattr("platform_cli.tools.service.detect_tool", lambda spec: (True, "2.90.0"))
    monkeypatch.setattr(
        "platform_cli.tools.service.maybe_bootstrap_github_after_install",
        lambda **_kwargs: (_ for _ in ()).throw(
            PlatformError(
                "GitHub CLI is installed but not authenticated yet.",
                code="E_GH_AUTH_NEEDS_INTERACTIVE",
                reason="gh_auth",
            )
        ),
    )

    result = install_tool(_spec("gh"), dry_run=False, upgrade=False, adopt_existing=False)

    assert result.status == "action_required"
    assert result.short_status == "Installed, but GitHub CLI authentication still needs interactive setup"
    assert "ghdp tools install --tool gh" in result.next_action


def test_detect_tool_details_treats_exit_code_one_as_not_installed(isolated_home, monkeypatch) -> None:
    spec = _spec("claude")

    def _fake_run(cmd, check=False, capture=True, **_kwargs):
        if cmd == spec.detect_cmd:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(tool_service, "run_cmd", _fake_run)
    monkeypatch.setattr(tool_service, "_active_path_and_version", lambda _spec: ("", ""))
    monkeypatch.setattr(tool_service, "_darwin_app_present", lambda _spec: False)
    monkeypatch.setattr(tool_service, "_darwin_app_version", lambda _spec: "")
    monkeypatch.setattr(tool_service, "_resolve_and_persist_ownership", lambda _spec: SimpleNamespace(effective_owner="ghdp"))

    result = tool_service.detect_tool_details(spec)

    assert result.installed_any is False
    assert result.status == "not_installed"
    assert result.code == ""


def test_install_tool_streams_claude_installer_output(isolated_home, monkeypatch) -> None:
    spec = _spec("claude")
    events: list[str] = []
    detect_results = iter([(False, ""), (True, "2.1.118")])
    observed: dict[str, object] = {}

    monkeypatch.setattr("platform_cli.tools.service.detect_tool", lambda _spec: next(detect_results))
    monkeypatch.setattr(
        "platform_cli.tools.service._resolve_and_persist_ownership",
        lambda _spec: SimpleNamespace(effective_owner="ghdp"),
    )
    monkeypatch.setattr(
        "platform_cli.tools.service._run_tool_cmd",
        lambda cmd, **kwargs: observed.setdefault("stream", kwargs.get("stream")),
    )
    monkeypatch.setattr(
        "platform_cli.tools.service._post_install_onboarding_status",
        lambda _spec, **_kwargs: ToolOnboardingStatus("claude", "CLAUDE", "ready", "Installed and ready"),
    )
    monkeypatch.setattr(
        "platform_cli.tools.service._policy_check",
        lambda _version, _req: SimpleNamespace(ok=True, op=None, required=None, parsed="2.1.118"),
    )

    result = install_tool(spec, dry_run=False, upgrade=False, adopt_existing=False, status_printer=events.append)

    assert result.status == "ready"
    assert observed["stream"] is True


def test_install_tool_treats_post_install_policy_miss_as_follow_up(isolated_home, monkeypatch) -> None:
    spec = ToolRuntimeSpec(
        name="claude",
        display_name="CLAUDE",
        detect_cmd=["detect"],
        version_cmd=["version"],
        install_cmd=["install"],
        upgrade_cmd=["upgrade"],
        uninstall_cmd=["uninstall"],
        version_req={"op": ">=", "version": "2.1.114"},
    )
    detect_results = iter([(False, ""), (True, "2.1.109")])

    def _fake_detect(_spec):
        installed, version = next(detect_results)
        if installed:
            update_tool_state(
                _spec.name,
                {
                    "policy_got": version,
                    "managed_version": version,
                    "detected_version": version,
                },
            )
        return installed, version

    monkeypatch.setattr("platform_cli.tools.service.detect_tool", _fake_detect)
    monkeypatch.setattr(
        "platform_cli.tools.service._resolve_and_persist_ownership",
        lambda _spec: SimpleNamespace(effective_owner="ghdp"),
    )
    monkeypatch.setattr("platform_cli.tools.service._run_tool_cmd", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "platform_cli.tools.service._policy_check",
        lambda _version, _req: SimpleNamespace(ok=False, op=">=", required="2.1.114", parsed="2.1.109"),
    )
    onboarding_called = {"value": False}
    monkeypatch.setattr(
        "platform_cli.tools.service._post_install_onboarding_status",
        lambda *_args, **_kwargs: onboarding_called.__setitem__("value", True)
        or ToolOnboardingStatus("claude", "CLAUDE", "ready", "Installed and ready"),
    )

    result = install_tool(spec, dry_run=False, upgrade=False, adopt_existing=False)

    assert result.status == "action_required"
    assert result.short_status == "Out of policy; upgrade required"
    assert result.phase == "install"
    assert "ghdp tools install --tool claude --upgrade" in result.next_action
    assert result.detail_hint == "required >=2.1.114, got 2.1.109"
    assert onboarding_called["value"] is True


def test_tools_install_groups_summary_and_clears_live_status_before_durable_output(monkeypatch) -> None:
    events: list[tuple[str, str | None]] = []
    specs = [_spec("awscli"), _spec("codex"), _spec("uv"), _spec("acli")]

    class _FakeStatus:
        def start(self, message: str) -> None:
            events.append(("status:start", message))

        def update(self, message: str) -> None:
            events.append(("status:update", message))

        def finish(self, message: str | None = None) -> None:
            events.append(("status:finish", message))

    def _fake_install(spec: ToolRuntimeSpec, **_kwargs) -> ToolOnboardingStatus:
        if spec.name == "awscli":
            return ToolOnboardingStatus(spec.name, spec.display_name, "ready", "Installed and authenticated")
        if spec.name == "codex":
            return ToolOnboardingStatus(
                spec.name,
                spec.display_name,
                "action_required",
                "Installed, but Codex login still needs interactive setup",
                next_action="Rerun `ghdp tools install --tool codex` in an interactive terminal.",
            )
        if spec.name == "uv":
            return ToolOnboardingStatus(spec.name, spec.display_name, "skipped", "Already installed and user-managed")
        raise PlatformError("Global agent config post-install step failed", code="E_AGENT_CONFIG_POST_INSTALL_FAILED", reason="acli")

    monkeypatch.setattr("platform_cli.core.access.ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(tools_cmd, "command_status", lambda _command: _FakeStatus())
    monkeypatch.setattr(
        tools_cmd,
        "_load_manifests_with_team_toolset_resolution",
        lambda refresh_toolset=False: ({}, {}, {"toolset": "pkg", "registry": "pkg"}, {"local_status": "current"}),
    )
    monkeypatch.setattr(tools_cmd, "_resolve_effective_team", lambda *_args, **_kwargs: "platform")
    monkeypatch.setattr(tools_cmd, "resolve_team_tools", lambda *_args, **_kwargs: specs)
    monkeypatch.setattr(tools_cmd, "detect_tool", lambda _spec: (False, ""))
    monkeypatch.setattr(tools_cmd, "install_tool", _fake_install)
    monkeypatch.setattr(tools_cmd.typer, "echo", lambda message="": events.append(("echo", str(message))))

    with pytest.raises(SystemExit) as exit_info:
        tools_cmd.tools_install(team=None, tool=None, all=True, upgrade=False, dry_run=False, refresh_toolset=False)

    assert exit_info.value.code == 1
    first_install_echo = events.index(("echo", "-> install awscli"))
    assert events[first_install_echo - 1] == ("status:finish", None)
    assert ("status:update", "Finalizing install summary...") in events
    assert ("echo", "tools install summary:") in events
    assert ("echo", "ready:") in events
    assert ("echo", "  - awscli: Installed and authenticated") in events
    assert ("echo", "next:") in events
    assert ("echo", "  - codex: Installed, but Codex login still needs interactive setup") in events
    assert ("echo", "failed:") in events
    assert ("echo", "  - acli: install: Global agent config post-install step failed") in events
    assert ("echo", "skipped:") not in events
    assert events[-1] == ("echo", "install finished with failures")


def test_claude_ready_summary_mentions_packaged_backup_mapping(isolated_home, monkeypatch) -> None:
    spec = _spec("claude")

    def _fake_bootstrap(**_kwargs) -> None:
        update_tool_state(
            "claude",
            {
                "claude_athena_workgroup_source": "derived",
                "claude_athena_workgroup_mapping_fallback_active": True,
            },
        )

    monkeypatch.setattr("platform_cli.tools.service.maybe_bootstrap_claude_after_install", _fake_bootstrap)

    result = tool_service._run_claude_post_step(spec)

    assert result.status == "ready"
    assert result.short_status == "Installed and ready (Athena workgroup derived via packaged backup mapping)"


def test_claude_deferred_summary_stays_action_required_when_workgroup_is_skipped(isolated_home, monkeypatch) -> None:
    spec = _spec("claude")

    def _fake_bootstrap(**_kwargs) -> None:
        update_tool_state(
            "claude",
            {
                "claude_athena_workgroup_source": "deferred",
                "claude_athena_workgroup_detail": "Skipped for now.",
            },
        )

    monkeypatch.setattr("platform_cli.tools.service.maybe_bootstrap_claude_after_install", _fake_bootstrap)

    result = tool_service._run_claude_post_step(spec)

    assert result.status == "action_required"
    assert result.short_status == "Installed, but Claude Athena workgroup is not configured yet"
    assert "ghdp config claude-athena-workgroup --value <workgroup>" in result.next_action


def test_tools_install_only_allows_claude_same_session_launch_for_explicit_claude(monkeypatch) -> None:
    specs = [_spec("claude")]
    observed: list[bool] = []

    monkeypatch.setattr("platform_cli.core.access.ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        tools_cmd,
        "_load_manifests_with_team_toolset_resolution",
        lambda refresh_toolset=False: (
            {"schema_version": "0.0.1", "teams": {"data_platform": {"tools": {}}}},
            {"schema_version": "1.0", "tools": {}},
            {"toolset": "managed:/tmp/team-toolset.managed.json", "registry": "pkg:platform_cli/resources/manifests/tool-registry.json"},
            {"local_status": "current"},
        ),
    )
    monkeypatch.setattr(tools_cmd, "_resolve_effective_team", lambda *_args, **_kwargs: "data_platform")
    monkeypatch.setattr(tools_cmd, "resolve_team_tools", lambda *_args, **_kwargs: specs)
    monkeypatch.setattr(tools_cmd, "detect_tool", lambda _spec: (False, ""))
    monkeypatch.setattr(
        tools_cmd,
        "install_tool",
        lambda _spec, **_kwargs: (
            observed.append(bool(getattr(cli_ctx, "claude_launch_same_session", True)))
            or ToolOnboardingStatus("claude", "CLAUDE", "ready", "Installed and ready")
        ),
    )
    monkeypatch.setattr(
        tools_cmd.scheduler_tools,
        "scheduler_initialization_status",
        lambda **_kwargs: {"supported": True, "initialized": True},
    )
    monkeypatch.setattr(tools_cmd.typer, "echo", lambda message="": None)

    cli_ctx.claude_launch_same_session = True
    tools_cmd.tools_install(team=None, tool=None, all=True, upgrade=False, dry_run=False, refresh_toolset=False)
    tools_cmd.tools_install(team=None, tool="claude", all=False, upgrade=False, dry_run=False, refresh_toolset=False)

    assert observed == [False, True]
    assert cli_ctx.claude_launch_same_session is True


def test_tools_install_runs_best_effort_scheduler_setup_after_successful_install_all(monkeypatch) -> None:
    events: list[tuple[str, str | None]] = []
    specs = [_spec("awscli")]

    class _FakeStatus:
        def __init__(self, command: str) -> None:
            self.command = command

        def start(self, message: str) -> None:
            events.append((f"{self.command}:status:start", message))

        def update(self, message: str) -> None:
            events.append((f"{self.command}:status:update", message))

        def finish(self, message: str | None = None) -> None:
            events.append((f"{self.command}:status:finish", message))

    monkeypatch.setattr("platform_cli.core.access.ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(tools_cmd, "command_status", lambda command: _FakeStatus(command))
    monkeypatch.setattr(
        tools_cmd,
        "_load_manifests_with_team_toolset_resolution",
        lambda refresh_toolset=False: ({}, {}, {"toolset": "pkg"}, {"local_status": "current"}),
    )
    monkeypatch.setattr(tools_cmd, "_resolve_effective_team", lambda *_args, **_kwargs: "data_platform")
    monkeypatch.setattr(tools_cmd, "resolve_team_tools", lambda *_args, **_kwargs: specs)
    monkeypatch.setattr(tools_cmd, "detect_tool", lambda _spec: (False, ""))
    monkeypatch.setattr(
        tools_cmd,
        "install_tool",
        lambda _spec, **_kwargs: ToolOnboardingStatus("awscli", "AWSCLI", "ready", "Installed and authenticated"),
    )
    monkeypatch.setattr(
        tools_cmd.scheduler_tools,
        "scheduler_initialization_status",
        lambda **_kwargs: {"supported": True, "initialized": False},
    )
    monkeypatch.setattr(
        tools_cmd.scheduler_tools,
        "ensure_post_install_scheduler_setup",
        lambda **_kwargs: (
            _kwargs["status_printer"]("Syncing scheduler job definitions..."),
            _kwargs["status_printer"]("Building scheduler apply preview..."),
            _kwargs["status_printer"]("Loaded 1 scheduler job definition(s)..."),
            _kwargs["status_printer"]("Applying 1 scheduled task change(s)..."),
            _kwargs["status_printer"]("Finalizing scheduler setup..."),
            {"planned": [{"task_id": "schedule-apply-background"}], "applied": [{"task_id": "schedule-apply-background"}]},
        )[-1],
    )
    monkeypatch.setattr(tools_cmd.typer, "echo", lambda message="": events.append(("echo", str(message))))

    tools_cmd.tools_install(team=None, tool=None, all=True, upgrade=False, dry_run=False, refresh_toolset=False)

    assert ("schedule:status:start", "Checking scheduler initialization...") in events
    assert ("schedule:status:update", "Syncing scheduler job definitions...") in events
    assert ("schedule:status:update", "Building scheduler apply preview...") in events
    assert ("schedule:status:update", "Loaded 1 scheduler job definition(s)...") in events
    assert ("schedule:status:update", "Applying 1 scheduled task change(s)...") in events
    assert ("schedule:status:update", "Finalizing scheduler setup...") in events
    assert ("echo", "scheduler setup: initialized (1 task(s) updated)") in events
    assert events[-1] == ("echo", "install finished")


def test_load_manifests_with_team_toolset_resolution_falls_back_to_packaged_when_sync_fails(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        tools_cmd,
        "ensure_team_toolset_available",
        lambda **_kwargs: (_ for _ in ()).throw(
            PlatformError("Command not found: gh", code="E_CMD_NOT_FOUND", reason="gh")
        ),
    )
    monkeypatch.setattr(
        tools_cmd,
        "load_manifests",
        lambda: (
            {"schema_version": "0.0.1", "teams": {"data_platform": {"tools": {}}}},
            {"schema_version": "1.0", "tools": {}},
            {
                "toolset": "pkg:platform_cli/resources/manifests/toolset.json",
                "registry": "pkg:platform_cli/resources/manifests/tool-registry.json",
            },
        ),
    )
    monkeypatch.setattr(tools_cmd.typer, "echo", lambda message="": events.append(str(message)))

    toolset, registry, sources, result = tools_cmd._load_manifests_with_team_toolset_resolution()

    assert toolset["schema_version"] == "0.0.1"
    assert registry["schema_version"] == "1.0"
    assert result["local_status"] == "fallback"
    assert sources["toolset"].startswith("pkg:")
    assert any("using packaged fallback" in item.lower() for item in events)


def test_tools_install_prioritizes_gh_and_refreshes_managed_toolset_once(monkeypatch) -> None:
    specs = [_spec("awscli"), _spec("gh"), _spec("codex")]
    install_order: list[str] = []
    refresh_calls: list[bool] = []

    monkeypatch.setattr("platform_cli.core.access.ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(tools_cmd, "command_status", lambda _command: SimpleNamespace(start=lambda _m: None, update=lambda _m: None, finish=lambda _m=None: None))
    monkeypatch.setattr(
        tools_cmd,
        "_load_manifests_with_team_toolset_resolution",
        lambda refresh_toolset=False: (
            {"schema_version": "0.0.1", "teams": {"data_platform": {"tools": {}}}},
            {"schema_version": "1.0", "tools": {}},
            {"toolset": "pkg:platform_cli/resources/manifests/toolset.json", "registry": "pkg:platform_cli/resources/manifests/tool-registry.json"},
            {"local_status": "fallback"},
        ),
    )
    monkeypatch.setattr(tools_cmd, "_resolve_effective_team", lambda *_args, **_kwargs: "data_platform")
    monkeypatch.setattr(tools_cmd, "resolve_team_tools", lambda *_args, **_kwargs: specs)
    monkeypatch.setattr(tools_cmd, "detect_tool", lambda _spec: (False, ""))
    monkeypatch.setattr(
        tools_cmd,
        "install_tool",
        lambda spec, **_kwargs: install_order.append(spec.name) or ToolOnboardingStatus(spec.name, spec.display_name, "ready", "Installed"),
    )
    monkeypatch.setattr(
        tools_cmd,
        "_refresh_toolset_after_gh_install",
        lambda **_kwargs: refresh_calls.append(True) or (
            [_spec("gh"), _spec("awscli"), _spec("codex")],
            "managed:/tmp/team-toolset.managed.json",
            [],
        ),
    )
    monkeypatch.setattr(
        tools_cmd.scheduler_tools,
        "scheduler_initialization_status",
        lambda **_kwargs: {"supported": True, "initialized": True},
    )
    monkeypatch.setattr(tools_cmd.typer, "echo", lambda message="": None)

    tools_cmd.tools_install(team=None, tool=None, all=True, upgrade=False, dry_run=False, refresh_toolset=False)

    assert install_order == ["gh", "awscli", "codex"]
    assert refresh_calls == [True]


def test_tools_install_reports_preflight_manifest_failure_in_summary(monkeypatch) -> None:
    events: list[str] = []

    monkeypatch.setattr("platform_cli.core.access.ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        tools_cmd,
        "_load_manifests_with_team_toolset_resolution",
        lambda refresh_toolset=False: (_ for _ in ()).throw(RuntimeError("boom manifests")),
    )
    monkeypatch.setattr(tools_cmd.typer, "echo", lambda message="": events.append(str(message)))
    monkeypatch.setattr(
        tools_cmd,
        "command_status",
        lambda _command: SimpleNamespace(start=lambda _m: None, update=lambda _m: None, finish=lambda _m=None: None),
    )

    with pytest.raises(SystemExit) as exit_info:
        tools_cmd.tools_install(team=None, tool=None, all=True, upgrade=False, dry_run=False, refresh_toolset=False)

    assert exit_info.value.code == 1
    assert "tools install summary:" in events
    assert "issues:" in events
    assert "  - session: issue in preflight -> manifest_load" in events
    assert "  rerun with `ghdp tools install --debug-install` for full diagnostics." in events
    assert events[-1] == "install finished with failures"


def test_tools_install_surfaces_detection_failures_and_continues(monkeypatch) -> None:
    events: list[str] = []
    specs = [_spec("git"), _spec("uv")]
    install_order: list[str] = []

    monkeypatch.setattr("platform_cli.core.access.ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        tools_cmd,
        "_load_manifests_with_team_toolset_resolution",
        lambda refresh_toolset=False: (
            {"schema_version": "0.0.1", "teams": {"data_platform": {"tools": {}}}},
            {"schema_version": "1.0", "tools": {}},
            {"toolset": "managed:/tmp/team-toolset.managed.json", "registry": "pkg"},
            {"local_status": "current"},
        ),
    )
    monkeypatch.setattr(tools_cmd, "_resolve_effective_team", lambda *_args, **_kwargs: "data_platform")
    monkeypatch.setattr(tools_cmd, "resolve_team_tools", lambda *_args, **_kwargs: specs)

    def _fake_detect(spec: ToolRuntimeSpec):
        if spec.name == "git":
            update_tool_state(
                "git",
                {
                    "detection_status": "detect_cmd_failed",
                    "detection_error": "git detect exploded",
                    "detection_error_code": "E_TOOL_DETECT_FAILED",
                },
            )
            return (False, "")
        update_tool_state("uv", {"detection_status": "not_installed"})
        return (False, "")

    monkeypatch.setattr(tools_cmd, "detect_tool", _fake_detect)
    monkeypatch.setattr(
        tools_cmd,
        "install_tool",
        lambda spec, **_kwargs: install_order.append(spec.name) or ToolOnboardingStatus(spec.name, spec.display_name, "ready", "Installed"),
    )
    monkeypatch.setattr(
        tools_cmd.scheduler_tools,
        "scheduler_initialization_status",
        lambda **_kwargs: {"supported": True, "initialized": True},
    )
    monkeypatch.setattr(
        tools_cmd,
        "command_status",
        lambda _command: SimpleNamespace(start=lambda _m: None, update=lambda _m: None, finish=lambda _m=None: None),
    )
    monkeypatch.setattr(tools_cmd.typer, "echo", lambda message="": events.append(str(message)))

    tools_cmd.tools_install(team=None, tool=None, all=True, upgrade=False, dry_run=False, refresh_toolset=False)

    assert install_order == ["git", "uv"]
    assert "issues:" not in events
    assert "no issues." in events
    assert events[-1] == "install finished"


def test_tools_install_suppresses_stale_detection_issue_after_successful_install(monkeypatch) -> None:
    events: list[str] = []
    specs = [_spec("claude")]

    monkeypatch.setattr("platform_cli.core.access.ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        tools_cmd,
        "_load_manifests_with_team_toolset_resolution",
        lambda refresh_toolset=False: (
            {"schema_version": "0.0.1", "teams": {"data_platform": {"tools": {}}}},
            {"schema_version": "1.0", "tools": {}},
            {"toolset": "managed:/tmp/team-toolset.managed.json", "registry": "pkg"},
            {"local_status": "current"},
        ),
    )
    monkeypatch.setattr(tools_cmd, "_resolve_effective_team", lambda *_args, **_kwargs: "data_platform")
    monkeypatch.setattr(tools_cmd, "resolve_team_tools", lambda *_args, **_kwargs: specs)

    def _fake_detect(spec: ToolRuntimeSpec):
        update_tool_state(
            spec.name,
            {
                "detection_status": "detect_cmd_failed",
                "detection_error": "claude detect exploded",
                "detection_error_code": "E_TOOL_DETECT_FAILED",
            },
        )
        return False, ""

    def _fake_install(spec: ToolRuntimeSpec, **_kwargs):
        update_tool_state(
            spec.name,
            {
                "detection_status": "installed",
                "detection_error": "",
                "detection_error_code": "",
                "detected": True,
                "managed_version": "2.1.118",
            },
        )
        return ToolOnboardingStatus(spec.name, spec.display_name, "ready", "Installed and ready")

    monkeypatch.setattr(tools_cmd, "detect_tool", _fake_detect)
    monkeypatch.setattr(tools_cmd, "install_tool", _fake_install)
    monkeypatch.setattr(
        tools_cmd.scheduler_tools,
        "scheduler_initialization_status",
        lambda **_kwargs: {"supported": True, "initialized": True},
    )
    monkeypatch.setattr(
        tools_cmd,
        "command_status",
        lambda _command: SimpleNamespace(start=lambda _m: None, update=lambda _m: None, finish=lambda _m=None: None),
    )
    monkeypatch.setattr(tools_cmd.typer, "echo", lambda message="": events.append(str(message)))

    tools_cmd.tools_install(team=None, tool="claude", all=False, upgrade=False, dry_run=False, refresh_toolset=False)

    assert "issues:" not in events
    assert all("Detection command failed before install" not in item for item in events)
    assert events[-1] == "install finished"


def test_tools_install_reports_post_gh_refresh_failure_without_crashing(monkeypatch) -> None:
    events: list[str] = []
    specs = [_spec("gh"), _spec("codex")]

    monkeypatch.setattr("platform_cli.core.access.ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        tools_cmd,
        "_load_manifests_with_team_toolset_resolution",
        lambda refresh_toolset=False: (
            {"schema_version": "0.0.1", "teams": {"data_platform": {"tools": {}}}},
            {"schema_version": "1.0", "tools": {}},
            {"toolset": "pkg:platform_cli/resources/manifests/toolset.json", "registry": "pkg"},
            {"local_status": "fallback"},
        ),
    )
    monkeypatch.setattr(tools_cmd, "_resolve_effective_team", lambda *_args, **_kwargs: "data_platform")
    monkeypatch.setattr(tools_cmd, "resolve_team_tools", lambda *_args, **_kwargs: specs)
    monkeypatch.setattr(
        tools_cmd,
        "detect_tool",
        lambda spec: (update_tool_state(spec.name, {"detection_status": "not_installed"}), (False, ""))[-1],
    )
    monkeypatch.setattr(
        tools_cmd,
        "install_tool",
        lambda spec, **_kwargs: ToolOnboardingStatus(spec.name, spec.display_name, "ready", "Installed"),
    )
    monkeypatch.setattr(
        tools_cmd,
        "_refresh_toolset_after_gh_install",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("refresh blew up")),
    )
    monkeypatch.setattr(
        tools_cmd,
        "command_status",
        lambda _command: SimpleNamespace(start=lambda _m: None, update=lambda _m: None, finish=lambda _m=None: None),
    )
    monkeypatch.setattr(tools_cmd.typer, "echo", lambda message="": events.append(str(message)))

    tools_cmd.tools_install(team=None, tool=None, all=True, upgrade=False, dry_run=False, refresh_toolset=False)

    assert "issues:" not in events
    assert "no issues." in events
    assert events[-1] == "install finished"


def test_tools_install_hides_preflight_toolset_issue_when_gh_refresh_succeeds(monkeypatch) -> None:
    events: list[str] = []
    specs = [_spec("gh"), _spec("codex")]

    monkeypatch.setattr("platform_cli.core.access.ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        tools_cmd,
        "_load_manifests_with_team_toolset_resolution",
        lambda refresh_toolset=False: (
            {"schema_version": "0.0.1", "teams": {"data_platform": {"tools": {}}}},
            {"schema_version": "1.0", "tools": {}},
            {"toolset": "pkg:platform_cli/resources/manifests/toolset.json", "registry": "pkg"},
            {"local_status": "fallback"},
        ),
    )
    monkeypatch.setattr(tools_cmd, "_resolve_effective_team", lambda *_args, **_kwargs: "data_platform")
    monkeypatch.setattr(tools_cmd, "resolve_team_tools", lambda *_args, **_kwargs: specs)
    monkeypatch.setattr(
        tools_cmd,
        "detect_tool",
        lambda spec: (update_tool_state(spec.name, {"detection_status": "not_installed"}), (False, ""))[-1],
    )
    monkeypatch.setattr(
        tools_cmd,
        "install_tool",
        lambda spec, **_kwargs: ToolOnboardingStatus(spec.name, spec.display_name, "ready", "Installed"),
    )
    monkeypatch.setattr(
        tools_cmd,
        "_refresh_toolset_after_gh_install",
        lambda **kwargs: (kwargs["selected_specs"], "managed:/tmp/team-toolset.managed.json", []),
    )
    monkeypatch.setattr(
        tools_cmd.scheduler_tools,
        "scheduler_initialization_status",
        lambda **_kwargs: {"supported": True, "initialized": True},
    )
    monkeypatch.setattr(
        tools_cmd,
        "command_status",
        lambda _command: SimpleNamespace(start=lambda _m: None, update=lambda _m: None, finish=lambda _m=None: None),
    )
    monkeypatch.setattr(tools_cmd.typer, "echo", lambda message="": events.append(str(message)))

    tools_cmd.tools_install(team=None, tool=None, all=True, upgrade=False, dry_run=False, refresh_toolset=False)

    assert "issues:" not in events
    assert "  - session: issue in preflight -> toolset" not in events
    assert events[-1] == "install finished"


def test_detect_tool_details_windows_treats_active_path_as_installed_when_winget_misses(monkeypatch) -> None:
    spec = ToolRuntimeSpec(
        name="gh",
        display_name="GitHub CLI",
        detect_cmd=["detect"],
        version_cmd=["version"],
        install_cmd=["install"],
        upgrade_cmd=["upgrade"],
        uninstall_cmd=["uninstall"],
        version_req=None,
        winget_id="GitHub.cli",
        bin_name="gh",
    )

    monkeypatch.setattr(tool_service.sys, "platform", "win32")
    monkeypatch.setattr(tool_service, "_winget_installed_and_version", lambda _wid: (False, ""))
    monkeypatch.setattr(tool_service, "_active_path_and_version", lambda _spec: (r"C:\\Users\\Hi\\bin\\gh.exe", "2.80.0"))
    monkeypatch.setattr(tool_service, "_darwin_app_present", lambda _spec: False)
    monkeypatch.setattr(tool_service, "_darwin_app_version", lambda _spec: "")
    monkeypatch.setattr(tool_service, "update_tool_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tool_service, "_resolve_and_persist_ownership", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        tool_service,
        "_policy_check",
        lambda _version, _req: SimpleNamespace(ok=True, op=None, required=None, parsed="2.80.0"),
    )

    result = tool_service.detect_tool_details(spec)

    assert result.installed_any is True
    assert result.status == "installed"
    assert result.code == ""


def test_tools_install_falls_back_to_direct_tool_when_team_resolution_fails(monkeypatch) -> None:
    events: list[str] = []
    install_order: list[str] = []

    monkeypatch.setattr("platform_cli.core.access.ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        tools_cmd,
        "_load_manifests_with_team_toolset_resolution",
        lambda refresh_toolset=False: (
            {"schema_version": "0.0.1", "teams": {"data_platform": {"tools": {"gh": {}}}}},
            {"schema_version": "1.0", "tools": {"gh": {}}},
            {"toolset": "managed:/tmp/team-toolset.managed.json", "registry": "pkg"},
            {"local_status": "current"},
        ),
    )
    monkeypatch.setattr(
        tools_cmd,
        "_resolve_effective_team",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PlatformError("team missing", code="E_TEAM_INVALID", reason="team")),
    )
    monkeypatch.setattr(tools_cmd, "build_tool_runtime_spec", lambda *_args, **_kwargs: _spec("gh"))
    monkeypatch.setattr(
        tools_cmd,
        "detect_tool",
        lambda _spec: (update_tool_state("gh", {"detection_status": "not_installed"}), (False, ""))[-1],
    )
    monkeypatch.setattr(
        tools_cmd,
        "install_tool",
        lambda spec, **_kwargs: install_order.append(spec.name) or ToolOnboardingStatus(spec.name, spec.display_name, "ready", "Installed"),
    )
    monkeypatch.setattr(
        tools_cmd.scheduler_tools,
        "scheduler_initialization_status",
        lambda **_kwargs: {"supported": True, "initialized": True},
    )
    monkeypatch.setattr(
        tools_cmd,
        "command_status",
        lambda _command: SimpleNamespace(start=lambda _m: None, update=lambda _m: None, finish=lambda _m=None: None),
    )
    monkeypatch.setattr(tools_cmd.typer, "echo", lambda message="": events.append(str(message)))

    tools_cmd.tools_install(team="missing", tool="gh", all=False, upgrade=False, dry_run=False, refresh_toolset=False)

    assert install_order == ["gh"]
    assert "issues:" not in events
    assert "no issues." in events
    assert events[-1] == "install finished"


def test_refresh_toolset_after_gh_install_reports_reresolve_issue(monkeypatch) -> None:
    selected_specs = [_spec("gh"), _spec("codex")]

    monkeypatch.setattr(
        tools_cmd,
        "ensure_team_toolset_available",
        lambda force_refresh=False: {"local_status": "synced", "sync_result": {}},
    )
    monkeypatch.setattr(
        tools_cmd,
        "load_manifests",
        lambda: (
            {"schema_version": "0.0.1", "teams": {"data_platform": {"tools": {"gh": {}, "codex": {}}}}},
            {"schema_version": "1.0", "tools": {"gh": {}, "codex": {}}},
            {"toolset": "managed:/tmp/team-toolset.managed.json", "registry": "pkg"},
        ),
    )
    monkeypatch.setattr(
        tools_cmd,
        "resolve_team_tools",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            PlatformError("refreshed team toolset is invalid", code="E_MANIFEST_INVALID", reason="toolset")
        ),
    )

    refreshed_specs, toolset_source, issues = tools_cmd._refresh_toolset_after_gh_install(
        selected_team="data_platform",
        install_all=True,
        active_toolset_source="pkg:platform_cli/resources/manifests/toolset.json",
        selected_specs=selected_specs,
    )

    assert refreshed_specs == selected_specs
    assert toolset_source == "pkg:platform_cli/resources/manifests/toolset.json"
    assert len(issues) == 1
    assert issues[0].phase == "refresh.re_resolve"
    assert issues[0].outcome == "warning"


def test_tools_install_bootstraps_gh_for_packaged_data_ops_team_before_sync_dependent_tools(monkeypatch) -> None:
    specs = [_spec("awscli"), _spec("codex"), _spec("claude")]
    install_order: list[str] = []
    refresh_calls: list[bool] = []

    monkeypatch.setattr("platform_cli.core.access.ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(tools_cmd, "command_status", lambda _command: SimpleNamespace(start=lambda _m: None, update=lambda _m: None, finish=lambda _m=None: None))
    monkeypatch.setattr(
        tools_cmd,
        "_load_manifests_with_team_toolset_resolution",
        lambda refresh_toolset=False: (
            {
                "schema_version": "0.0.1",
                "teams": {
                    "data_ops": {"tools": {"awscli": {}, "codex": {}, "claude": {}}},
                    "data_platform": {"tools": {"gh": {"op": ">=", "version": "2.89.0"}}},
                },
            },
            {"schema_version": "1.0", "tools": {"gh": {}}},
            {"toolset": "pkg:platform_cli/resources/manifests/toolset.json", "registry": "pkg:platform_cli/resources/manifests/tool-registry.json"},
            {"local_status": "fallback"},
        ),
    )
    monkeypatch.setattr(tools_cmd, "_resolve_effective_team", lambda *_args, **_kwargs: "data_ops")
    monkeypatch.setattr(tools_cmd, "resolve_team_tools", lambda *_args, **_kwargs: specs)
    monkeypatch.setattr(tools_cmd, "build_tool_runtime_spec", lambda *_args, **_kwargs: _spec("gh"))
    monkeypatch.setattr(tools_cmd, "detect_tool", lambda _spec: (False, ""))
    monkeypatch.setattr(
        tools_cmd,
        "install_tool",
        lambda spec, **_kwargs: install_order.append(spec.name) or ToolOnboardingStatus(spec.name, spec.display_name, "ready", "Installed"),
    )
    monkeypatch.setattr(
        tools_cmd,
        "_refresh_toolset_after_gh_install",
        lambda **_kwargs: refresh_calls.append(True) or (_kwargs["selected_specs"], "managed:/tmp/team-toolset.managed.json", []),
    )
    monkeypatch.setattr(
        tools_cmd.scheduler_tools,
        "scheduler_initialization_status",
        lambda **_kwargs: {"supported": True, "initialized": True},
    )
    monkeypatch.setattr(tools_cmd.typer, "echo", lambda message="": None)

    tools_cmd.tools_install(team=None, tool=None, all=True, upgrade=False, dry_run=False, refresh_toolset=False)

    assert install_order == ["gh", "awscli", "codex", "claude"]
    assert refresh_calls == [True]


def test_maybe_inject_gh_bootstrap_spec_is_idempotent_when_gh_already_present(monkeypatch) -> None:
    specs = [_spec("gh"), _spec("codex")]

    monkeypatch.setattr(
        tools_cmd,
        "build_tool_runtime_spec",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("gh should not be rebuilt")),
    )

    selected = tools_cmd._maybe_inject_gh_bootstrap_spec(
        specs,
        install_all=True,
        started_from_fallback=True,
        toolset={"teams": {"data_ops": {"tools": {"gh": {}, "codex": {}}}}},
        registry={"tools": {"gh": {}}},
        toolset_source="pkg:platform_cli/resources/manifests/toolset.json",
    )

    assert [spec.name for spec in selected] == ["gh", "codex"]


def test_build_tool_runtime_spec_preserves_platform_specific_install_metadata(monkeypatch) -> None:
    registry = {
        "tools": {
            "gh": {
                "display_name": "GitHub CLI",
                "detect_cmd": ["gh", "--version"],
                "version_cmd": ["gh", "--version"],
                "bin": "gh",
                "manager": "multi",
                "brew": {"formula": "gh"},
                "winget": {"id": "GitHub.cli"},
                "choco": {"package": "gh"},
                "platforms": {
                    "darwin": {
                        "install": ["brew", "install", "gh"],
                        "upgrade": ["brew", "upgrade", "gh"],
                        "uninstall": ["brew", "uninstall", "gh"],
                    },
                    "linux": {
                        "install": ["sudo", "apt-get", "install", "-y", "gh"],
                        "upgrade": ["sudo", "apt-get", "install", "-y", "--only-upgrade", "gh"],
                        "uninstall": ["sudo", "apt-get", "remove", "-y", "gh"],
                    },
                    "windows": {
                        "install": ["winget", "install", "--id", "GitHub.cli"],
                        "upgrade": ["winget", "upgrade", "--id", "GitHub.cli"],
                        "uninstall": ["winget", "uninstall", "--name", "GitHub CLI"],
                    },
                },
            }
        }
    }

    monkeypatch.setattr(tool_service, "current_platform_key", lambda: "linux")
    linux_spec = tool_service.build_tool_runtime_spec("gh", registry)
    assert linux_spec.install_cmd == ["sudo", "apt-get", "install", "-y", "gh"]
    assert linux_spec.upgrade_cmd == ["sudo", "apt-get", "install", "-y", "--only-upgrade", "gh"]
    assert linux_spec.uninstall_cmd == ["sudo", "apt-get", "remove", "-y", "gh"]

    monkeypatch.setattr(tool_service, "current_platform_key", lambda: "windows")
    windows_spec = tool_service.build_tool_runtime_spec("gh", registry)
    assert windows_spec.install_cmd == ["winget", "install", "--id", "GitHub.cli"]
    assert windows_spec.upgrade_cmd == ["winget", "upgrade", "--id", "GitHub.cli"]
    assert windows_spec.uninstall_cmd == ["winget", "uninstall", "--name", "GitHub CLI"]
    assert windows_spec.winget_id == "GitHub.cli"
    assert windows_spec.choco_package == "gh"


def test_tools_install_shows_live_status_during_gh_auth_and_refresh(monkeypatch) -> None:
    events: list[tuple[str, str | None]] = []
    specs = [_spec("gh"), _spec("awscli")]

    class _FakeStatus:
        def __init__(self, command: str) -> None:
            self.command = command

        def start(self, message: str) -> None:
            events.append((f"{self.command}:status:start", message))

        def update(self, message: str) -> None:
            events.append((f"{self.command}:status:update", message))

        def finish(self, message: str | None = None) -> None:
            events.append((f"{self.command}:status:finish", message))

    monkeypatch.setattr("platform_cli.core.access.ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(tools_cmd, "command_status", lambda command: _FakeStatus(command))
    monkeypatch.setattr(
        tools_cmd,
        "_load_manifests_with_team_toolset_resolution",
        lambda refresh_toolset=False: (
            {"schema_version": "0.0.1", "teams": {"data_platform": {"tools": {}}}},
            {"schema_version": "1.0", "tools": {}},
            {"toolset": "pkg:platform_cli/resources/manifests/toolset.json", "registry": "pkg:platform_cli/resources/manifests/tool-registry.json"},
            {"local_status": "fallback"},
        ),
    )
    monkeypatch.setattr(tools_cmd, "_resolve_effective_team", lambda *_args, **_kwargs: "data_platform")
    monkeypatch.setattr(tools_cmd, "resolve_team_tools", lambda *_args, **_kwargs: specs)
    monkeypatch.setattr(tools_cmd, "detect_tool", lambda _spec: (False, ""))

    def _fake_install(spec, **kwargs):
        status_printer = kwargs.get("status_printer")
        if spec.name == "gh" and status_printer is not None:
            status_printer("Opening GitHub CLI login...")
            status_printer("Verifying GitHub CLI authentication...")
        return ToolOnboardingStatus(spec.name, spec.display_name, "ready", "Installed")

    monkeypatch.setattr(tools_cmd, "install_tool", _fake_install)
    monkeypatch.setattr(
        tools_cmd,
        "_refresh_toolset_after_gh_install",
        lambda **kwargs: (
            kwargs["status_printer"]("Refreshing managed team toolset..."),
            kwargs["status_printer"]("Reloading managed team and tool definitions..."),
            (kwargs["selected_specs"], "managed:/tmp/team-toolset.managed.json", []),
        )[-1],
    )
    monkeypatch.setattr(
        tools_cmd.scheduler_tools,
        "scheduler_initialization_status",
        lambda **_kwargs: {"supported": True, "initialized": True},
    )
    monkeypatch.setattr(tools_cmd.typer, "echo", lambda message="": events.append(("echo", str(message))))

    tools_cmd.tools_install(team=None, tool=None, all=True, upgrade=False, dry_run=False, refresh_toolset=False)

    assert ("tools:status:update", "Opening GitHub CLI login...") in events
    assert ("tools:status:update", "Verifying GitHub CLI authentication...") in events
    assert ("tools:status:update", "Refreshing managed team toolset...") in events
    assert ("tools:status:update", "Reloading managed team and tool definitions...") in events


def test_tools_install_out_of_policy_skip_advances_once(monkeypatch) -> None:
    git_spec = _spec("git")
    git_spec = ToolRuntimeSpec(
        name=git_spec.name,
        display_name=git_spec.display_name,
        detect_cmd=git_spec.detect_cmd,
        version_cmd=git_spec.version_cmd,
        install_cmd=git_spec.install_cmd,
        upgrade_cmd=git_spec.upgrade_cmd,
        uninstall_cmd=git_spec.uninstall_cmd,
        version_req={"op": ">=", "version": "2.53.0"},
    )
    specs = [_spec("gh"), git_spec, _spec("awscli")]
    install_order: list[str] = []
    detect_calls: list[str] = []

    monkeypatch.setattr("platform_cli.core.access.ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(tools_cmd, "command_status", lambda _command: SimpleNamespace(start=lambda _m: None, update=lambda _m: None, finish=lambda _m=None: None))
    monkeypatch.setattr(
        tools_cmd,
        "_load_manifests_with_team_toolset_resolution",
        lambda refresh_toolset=False: (
            {"schema_version": "0.0.1", "teams": {"data_platform": {"tools": {}}}},
            {"schema_version": "1.0", "tools": {}},
            {"toolset": "pkg:platform_cli/resources/manifests/toolset.json", "registry": "pkg:platform_cli/resources/manifests/tool-registry.json"},
            {"local_status": "fallback"},
        ),
    )
    monkeypatch.setattr(tools_cmd, "_resolve_effective_team", lambda *_args, **_kwargs: "data_platform")
    monkeypatch.setattr(tools_cmd, "resolve_team_tools", lambda *_args, **_kwargs: specs)

    def _fake_detect(spec):
        detect_calls.append(spec.name)
        if spec.name == "git":
            return True, "2.52.0"
        return False, ""

    monkeypatch.setattr(tools_cmd, "detect_tool", _fake_detect)
    monkeypatch.setattr(
        tools_cmd,
        "install_tool",
        lambda spec, **_kwargs: install_order.append(spec.name) or ToolOnboardingStatus(spec.name, spec.display_name, "ready", "Installed"),
    )
    monkeypatch.setattr(
        tools_cmd,
        "_refresh_toolset_after_gh_install",
        lambda **_kwargs: (_kwargs["selected_specs"], _kwargs["active_toolset_source"], []),
    )
    monkeypatch.setattr(
        tools_cmd.scheduler_tools,
        "scheduler_initialization_status",
        lambda **_kwargs: {"supported": True, "initialized": True},
    )
    monkeypatch.setattr(tools_cmd.typer, "echo", lambda message="": None)

    tools_cmd.tools_install(team=None, tool=None, all=True, upgrade=False, dry_run=False, refresh_toolset=False)

    assert install_order == ["gh", "awscli"]
    assert detect_calls.count("git") == 1


def test_tools_install_compact_summary_shows_out_of_policy_under_next_section(monkeypatch) -> None:
    events: list[str] = []
    git_spec = ToolRuntimeSpec(
        name="git",
        display_name="GIT",
        detect_cmd=["detect"],
        version_cmd=["version"],
        install_cmd=["install"],
        upgrade_cmd=["upgrade"],
        uninstall_cmd=["uninstall"],
        version_req={"op": ">=", "version": "2.53.0"},
    )
    uv_spec = ToolRuntimeSpec(
        name="uv",
        display_name="UV",
        detect_cmd=["detect"],
        version_cmd=["version"],
        install_cmd=["install"],
        upgrade_cmd=["upgrade"],
        uninstall_cmd=["uninstall"],
        version_req={"op": ">=", "version": "0.6.0"},
    )
    gh_spec = _spec("gh")
    specs = [gh_spec, git_spec, uv_spec]

    monkeypatch.setattr("platform_cli.core.access.ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        tools_cmd,
        "command_status",
        lambda _command: SimpleNamespace(start=lambda _m: None, update=lambda _m: None, finish=lambda _m=None: None),
    )
    monkeypatch.setattr(
        tools_cmd,
        "_load_manifests_with_team_toolset_resolution",
        lambda refresh_toolset=False: (
            {"schema_version": "0.0.1", "teams": {"data_platform": {"tools": {}}}},
            {"schema_version": "1.0", "tools": {}},
            {"toolset": "pkg:platform_cli/resources/manifests/toolset.json", "registry": "pkg"},
            {"local_status": "fallback"},
        ),
    )
    monkeypatch.setattr(tools_cmd, "_resolve_effective_team", lambda *_args, **_kwargs: "data_platform")
    monkeypatch.setattr(tools_cmd, "resolve_team_tools", lambda *_args, **_kwargs: specs)

    def _fake_detect(spec):
        if spec.name in ("git", "uv"):
            return True, "0.1.0"
        return False, ""

    monkeypatch.setattr(tools_cmd, "detect_tool", _fake_detect)
    monkeypatch.setattr(
        tools_cmd,
        "install_tool",
        lambda spec, **_kwargs: ToolOnboardingStatus(spec.name, spec.display_name, "already_ready", "Already ready"),
    )
    monkeypatch.setattr(
        tools_cmd,
        "_refresh_toolset_after_gh_install",
        lambda **_kwargs: (_kwargs["selected_specs"], _kwargs["active_toolset_source"], []),
    )
    monkeypatch.setattr(
        tools_cmd.scheduler_tools,
        "scheduler_initialization_status",
        lambda **_kwargs: {"supported": True, "initialized": True},
    )
    monkeypatch.setattr(tools_cmd.typer, "echo", lambda message="": events.append(str(message)))

    tools_cmd.tools_install(team=None, tool=None, all=True, upgrade=False, dry_run=False, refresh_toolset=False)

    assert "no issues." not in events
    assert "next:" in events
    next_idx = events.index("next:")
    assert "  - git: Installed (out of policy)" in events[next_idx:]
    assert "  - uv: Installed (out of policy)" in events[next_idx:]
    assert any("ghdp tools install --tool git --upgrade" in e for e in events)
    assert any("ghdp tools install --tool uv --upgrade" in e for e in events)
    assert events[-1] == "install finished"


def test_tools_install_scheduler_setup_falls_back_to_manual_recovery(monkeypatch) -> None:
    events: list[tuple[str, str | None]] = []
    specs = [_spec("awscli")]

    class _FakeStatus:
        def __init__(self, command: str) -> None:
            self.command = command

        def start(self, message: str) -> None:
            events.append((f"{self.command}:status:start", message))

        def update(self, message: str) -> None:
            events.append((f"{self.command}:status:update", message))

        def finish(self, message: str | None = None) -> None:
            events.append((f"{self.command}:status:finish", message))

    monkeypatch.setattr("platform_cli.core.access.ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(tools_cmd, "command_status", lambda command: _FakeStatus(command))
    monkeypatch.setattr(
        tools_cmd,
        "_load_manifests_with_team_toolset_resolution",
        lambda refresh_toolset=False: ({}, {}, {"toolset": "pkg"}, {"local_status": "current"}),
    )
    monkeypatch.setattr(tools_cmd, "_resolve_effective_team", lambda *_args, **_kwargs: "data_platform")
    monkeypatch.setattr(tools_cmd, "resolve_team_tools", lambda *_args, **_kwargs: specs)
    monkeypatch.setattr(tools_cmd, "detect_tool", lambda _spec: (False, ""))
    monkeypatch.setattr(
        tools_cmd,
        "install_tool",
        lambda _spec, **_kwargs: ToolOnboardingStatus("awscli", "AWSCLI", "ready", "Installed and authenticated"),
    )
    monkeypatch.setattr(
        tools_cmd.scheduler_tools,
        "scheduler_initialization_status",
        lambda **_kwargs: {"supported": True, "initialized": False},
    )
    monkeypatch.setattr(
        tools_cmd.scheduler_tools,
        "ensure_post_install_scheduler_setup",
        lambda **_kwargs: (_ for _ in ()).throw(PlatformError("gh is not configured", code="E_GH_NOT_READY", reason="gh")),
    )
    monkeypatch.setattr(tools_cmd.typer, "echo", lambda message="": events.append(("echo", str(message))))

    tools_cmd.tools_install(team=None, tool=None, all=True, upgrade=False, dry_run=False, refresh_toolset=False)

    assert ("schedule:status:start", "Checking scheduler initialization...") in events
    assert ("echo", "warning: scheduler setup could not be completed automatically.") in events
    assert ("echo", "  next: run `ghdp schedule apply`") in events
    assert events[-1] == ("echo", "install finished")


def test_tools_install_skips_scheduler_setup_when_already_initialized(monkeypatch) -> None:
    events: list[tuple[str, str | None]] = []
    specs = [_spec("awscli")]

    class _FakeStatus:
        def __init__(self, command: str) -> None:
            self.command = command

        def start(self, message: str) -> None:
            events.append((f"{self.command}:status:start", message))

        def update(self, message: str) -> None:
            events.append((f"{self.command}:status:update", message))

        def finish(self, message: str | None = None) -> None:
            events.append((f"{self.command}:status:finish", message))

    monkeypatch.setattr("platform_cli.core.access.ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(tools_cmd, "command_status", lambda command: _FakeStatus(command))
    monkeypatch.setattr(
        tools_cmd,
        "_load_manifests_with_team_toolset_resolution",
        lambda refresh_toolset=False: ({}, {}, {"toolset": "pkg"}, {"local_status": "current"}),
    )
    monkeypatch.setattr(tools_cmd, "_resolve_effective_team", lambda *_args, **_kwargs: "data_platform")
    monkeypatch.setattr(tools_cmd, "resolve_team_tools", lambda *_args, **_kwargs: specs)
    monkeypatch.setattr(tools_cmd, "detect_tool", lambda _spec: (False, ""))
    monkeypatch.setattr(
        tools_cmd,
        "install_tool",
        lambda _spec, **_kwargs: ToolOnboardingStatus("awscli", "AWSCLI", "ready", "Installed and authenticated"),
    )
    monkeypatch.setattr(
        tools_cmd.scheduler_tools,
        "scheduler_initialization_status",
        lambda **_kwargs: {"supported": True, "initialized": True},
    )
    monkeypatch.setattr(
        tools_cmd.scheduler_tools,
        "ensure_post_install_scheduler_setup",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("scheduler setup should not run")),
    )
    monkeypatch.setattr(tools_cmd.typer, "echo", lambda message="": events.append(("echo", str(message))))

    tools_cmd.tools_install(team=None, tool=None, all=True, upgrade=False, dry_run=False, refresh_toolset=False)

    assert ("schedule:status:start", "Checking scheduler initialization...") in events
    assert ("echo", "install finished") == events[-1]
    assert ("echo", "scheduler setup: initialized (1 task(s) updated)") not in events
