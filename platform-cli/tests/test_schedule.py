from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from platform_cli.cli import app
from platform_cli.commands import schedule as schedule_commands
from platform_cli.manifests import scheduler as scheduler_manifest
from platform_cli.state.store import get_tool_state
from platform_cli.tools import scheduler, scheduler_assets, scheduler_windows


runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_scheduler_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home_root = tmp_path / "scheduler-home"
    home_root.mkdir(parents=True, exist_ok=True)
    schedule_root = home_root / ".ghdp" / "schedule"
    monkeypatch.setenv("HOME", str(home_root))
    monkeypatch.setenv("USERPROFILE", str(home_root))
    monkeypatch.setattr(scheduler, "USER_SCHEDULE_ROOT", schedule_root)
    monkeypatch.setattr(scheduler, "WRAPPERS_DIR", schedule_root / "wrappers")
    monkeypatch.setattr(scheduler, "LOGS_DIR", schedule_root / "logs")
    monkeypatch.setattr(scheduler, "LOCKS_DIR", schedule_root / "locks")
    return home_root


def _seed_scheduler_capability() -> Path:
    capability_root = scheduler_manifest.installed_capability_root()
    capability_root.mkdir(parents=True, exist_ok=True)

    (capability_root / "capability.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_by": "ghdp",
                "capability_id": "background-scheduler",
                "display_name": "Background Scheduler",
                "description": "Synced scheduler capability definitions for GHDP background jobs.",
                "tasks_file": "tasks.json",
                "defaults_file": "defaults.json",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (capability_root / "defaults.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_by": "ghdp",
                "capability_id": "background-scheduler",
                "defaults": {
                    "enabled": True,
                    "required": False,
                    "platforms": ["windows", "darwin", "linux"],
                    "command": {"type": "ghdp"},
                    "trigger": {"type": "interval", "minutes": 60, "random_delay_minutes": 5},
                    "execution": {
                        "timeout_minutes": 15,
                        "overlap_policy": "skip",
                        "catch_up_after_missed_run": True,
                        "retry_on_failure": {"enabled": True, "minutes": 15, "max_attempts": 3},
                    },
                    "conditions": {
                        "require_network": False,
                        "allow_on_battery": True,
                        "stop_on_battery": False,
                        "idle_only": False,
                        "wake_machine": False,
                    },
                    "run_context": {"mode": "user_session", "elevated": False, "hidden": False},
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (capability_root / "tasks.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "generated_by": "ghdp",
                "capability_id": "background-scheduler",
                "tasks": [
                    {
                        "schema_version": "1.0",
                        "id": "background-sync",
                        "description": "Background sync",
                        "command": {"args": ["sync", "run", "--auto-approve"]},
                        "trigger": {"minutes": 45},
                        "platforms": ["windows"],
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return capability_root


def _state_key_for(task_id: str) -> str:
    task = scheduler._resolve_tasks(scope="user", task_id=task_id, ensure_synced=False)[0]
    return scheduler._state_key(task)


def test_prompt_schedule_action_accepts_index_and_name(monkeypatch: pytest.MonkeyPatch) -> None:
    raw_values = iter(["1", "run"])
    monkeypatch.setattr(schedule_commands.typer, "prompt", lambda *args, **kwargs: next(raw_values))

    assert schedule_commands._prompt_schedule_action(default="list") == "list"
    assert schedule_commands._prompt_schedule_action(default="list") == "run"


def test_guided_schedule_action_apply_uses_prompt_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    prompts = iter(["",])
    confirms = iter([False, True])

    monkeypatch.setattr(schedule_commands.typer, "prompt", lambda *args, **kwargs: next(prompts))
    monkeypatch.setattr(
        schedule_commands.typer,
        "confirm",
        lambda *args, **kwargs: next(confirms),
    )
    monkeypatch.setattr(
        schedule_commands,
        "schedule_apply",
        lambda *, task_id, auto_approve, dry_run: captured.update(
            task_id=task_id, auto_approve=auto_approve, dry_run=dry_run
        ),
    )

    schedule_commands._run_guided_schedule_action("apply")

    assert captured == {"task_id": None, "auto_approve": True, "dry_run": False}


def test_list_schedule_jobs_reports_missing_windows_task(monkeypatch: pytest.MonkeyPatch) -> None:
    capability_root = _seed_scheduler_capability()

    monkeypatch.setattr(scheduler, "_current_platform_name", lambda: "windows")
    monkeypatch.setattr(scheduler_windows, "provider_supported", lambda: True)
    monkeypatch.setattr(
        scheduler_assets,
        "ensure_scheduler_assets_synced",
        lambda: {"target_path": str(capability_root)},
    )
    monkeypatch.setattr(
        scheduler_windows,
        "query_task",
        lambda task_name: scheduler_windows.WindowsTaskObservation(exists=False, task_name=task_name),
    )

    items = scheduler.list_schedule_jobs(scope="user", job_id="background-sync")

    assert len(items) == 1
    assert items[0]["status"] == "missing"
    assert items[0]["action"] == "apply"
    assert items[0]["provider"] == "windows_task_scheduler"


def test_schedule_root_interactive_bare_list_uses_menu_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        scheduler,
        "list_schedule_jobs",
        lambda scope, job_id: [
            {
                "task_id": "sync-run-background",
                "status": "ok",
                "action": "none",
                "interval_minutes": 1440,
                "ghdp_args": ["sync", "run", "--auto-approve"],
                "provider": "windows_task_scheduler",
                "health_status": "fresh",
                "last_run_at": "2026-04-15T09:12:22Z",
                "artifact_path": "C:\\Users\\Hi\\.ghdp\\capabilities\\scheduler\\tasks.json",
            }
        ],
    )

    result = runner.invoke(app, ["schedule"], input="1\n\n")

    assert result.exit_code == 0
    assert "available schedule commands:" in result.stdout
    assert "sync-run-background status=ok action=none every=1440m" in result.stdout


def test_schedule_root_non_interactive_prints_help_without_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        schedule_commands.typer,
        "prompt",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("prompt should not be called")),
    )
    monkeypatch.setattr(
        schedule_commands.typer,
        "confirm",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("confirm should not be called")),
    )

    result = runner.invoke(app, ["--non-interactive", "schedule"])

    assert result.exit_code == 0
    assert "available schedule commands:" not in result.stdout
    assert "Manage GHDP scheduled background jobs." in result.stdout


def test_schedule_list_subcommand_remains_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        scheduler,
        "list_schedule_jobs",
        lambda scope, job_id: [
            {
                "task_id": "sync-run-background",
                "status": "ok",
                "action": "none",
                "interval_minutes": 1440,
                "ghdp_args": ["sync", "run", "--auto-approve"],
                "provider": "windows_task_scheduler",
                "health_status": "fresh",
                "last_run_at": "2026-04-15T09:12:22Z",
                "artifact_path": "C:\\Users\\Hi\\.ghdp\\capabilities\\scheduler\\tasks.json",
            }
        ],
    )

    result = runner.invoke(app, ["schedule", "list"])

    assert result.exit_code == 0
    assert "available schedule commands:" not in result.stdout
    assert "sync-run-background status=ok action=none every=1440m" in result.stdout


def test_apply_schedule_jobs_writes_wrapper_and_updates_state(monkeypatch: pytest.MonkeyPatch) -> None:
    capability_root = _seed_scheduler_capability()
    applied_specs: list[scheduler_windows.WindowsTaskSpec] = []

    monkeypatch.setattr(scheduler, "_current_platform_name", lambda: "windows")
    monkeypatch.setattr(scheduler_windows, "provider_supported", lambda: True)
    monkeypatch.setattr(scheduler, "_resolve_current_ghdp_executable", lambda: "C:\\ghdpeppe7270u7.exe")
    monkeypatch.setattr(
        scheduler_assets,
        "ensure_scheduler_assets_synced",
        lambda: {"target_path": str(capability_root)},
    )
    def _query_task(task_name: str) -> scheduler_windows.WindowsTaskObservation:
        if not applied_specs:
            return scheduler_windows.WindowsTaskObservation(exists=False, task_name=task_name)
        spec = applied_specs[0]
        return scheduler_windows.WindowsTaskObservation(
            exists=True,
            task_name=task_name,
            description=spec.description,
            interval_minutes=spec.interval_minutes,
            random_delay_minutes=spec.random_delay_minutes,
            command="powershell.exe",
            arguments=f'-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{spec.wrapper_path}"',
            allow_on_battery=spec.allow_on_battery,
            stop_on_battery=spec.stop_on_battery,
            require_network=spec.require_network,
            wake_machine=spec.wake_machine,
            start_when_available=spec.start_when_available,
            execution_time_limit_minutes=spec.execution_time_limit_minutes,
            multiple_instances_policy="IgnoreNew",
            restart_count=spec.restart_count,
            restart_interval_minutes=spec.restart_interval_minutes,
            hidden=spec.hidden,
        )

    monkeypatch.setattr(scheduler_windows, "query_task", _query_task)
    monkeypatch.setattr(scheduler_windows, "apply_task", lambda spec: applied_specs.append(spec))

    results = scheduler.apply_schedule_jobs(scope="user", job_id="background-sync")

    assert len(results) == 1
    assert applied_specs[0].interval_minutes == 45
    assert "managed_by=ghdp" in applied_specs[0].description
    assert "task_id=background-sync" in applied_specs[0].description
    wrapper_path = Path(results[0]["wrapper_path"])
    wrapper_body = wrapper_path.read_text(encoding="utf-8")
    assert "$ghdpCommand = 'C:\\ghdpeppe7270u7.exe'" in wrapper_body
    assert 'ghdp-scheduler-' in wrapper_body
    assert 'Copy-Item -LiteralPath $ghdpCommand -Destination $runner -Force' in wrapper_body
    assert "Start-Process -FilePath $runner" in wrapper_body
    assert "-WindowStyle Hidden -Wait -PassThru" in wrapper_body
    assert "'--task-id', 'background-sync'" in wrapper_body
    state = get_tool_state(_state_key_for("background-sync"))
    assert state["registration_status"] == "applied"
    assert state["task_name"] == results[0]["task_name"]


def test_apply_schedule_jobs_writes_windows_pre_run_delay_for_schedule_apply_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability_root = _seed_scheduler_capability()
    tasks_path = capability_root / "tasks.json"
    payload = json.loads(tasks_path.read_text(encoding="utf-8"))
    payload["tasks"].append(
        {
            "schema_version": "1.0",
            "id": "schedule-apply-background",
            "description": "Reconcile scheduler jobs",
            "required": True,
            "trigger": {"minutes": 45},
            "execution": {"pre_run_delay_minutes": 60},
            "command": {"args": ["_schedule-apply-background"]},
            "platforms": ["windows"],
        }
    )
    tasks_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    applied_specs: list[scheduler_windows.WindowsTaskSpec] = []

    monkeypatch.setattr(scheduler, "_current_platform_name", lambda: "windows")
    monkeypatch.setattr(scheduler_windows, "provider_supported", lambda: True)
    monkeypatch.setattr(scheduler, "_resolve_current_ghdp_executable", lambda: "C:\\ghdp.exe")
    monkeypatch.setattr(
        scheduler_assets,
        "ensure_scheduler_assets_synced",
        lambda: {"target_path": str(capability_root)},
    )
    def _query_task(task_name: str) -> scheduler_windows.WindowsTaskObservation:
        if not applied_specs:
            return scheduler_windows.WindowsTaskObservation(exists=False, task_name=task_name)
        spec = applied_specs[0]
        return scheduler_windows.WindowsTaskObservation(
            exists=True,
            task_name=task_name,
            description=spec.description,
            interval_minutes=spec.interval_minutes,
            random_delay_minutes=spec.random_delay_minutes,
            command="powershell.exe",
            arguments=f'-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{spec.wrapper_path}"',
            allow_on_battery=spec.allow_on_battery,
            stop_on_battery=spec.stop_on_battery,
            require_network=spec.require_network,
            wake_machine=spec.wake_machine,
            start_when_available=spec.start_when_available,
            execution_time_limit_minutes=spec.execution_time_limit_minutes,
            multiple_instances_policy="IgnoreNew",
            restart_count=spec.restart_count,
            restart_interval_minutes=spec.restart_interval_minutes,
            hidden=spec.hidden,
        )

    monkeypatch.setattr(scheduler_windows, "query_task", _query_task)
    monkeypatch.setattr(scheduler_windows, "apply_task", lambda spec: applied_specs.append(spec))

    results = scheduler.apply_schedule_jobs(scope="user", job_id="schedule-apply-background")

    assert len(results) == 1
    wrapper_path = Path(results[0]["wrapper_path"])
    wrapper_body = wrapper_path.read_text(encoding="utf-8")
    assert "Start-Sleep -Seconds 3600" in wrapper_body
    assert "Start-Process -FilePath $runner" in wrapper_body
    assert "-WindowStyle Hidden -Wait -PassThru" in wrapper_body
    assert "'--task-id', 'schedule-apply-background'" in wrapper_body


def test_run_scheduled_job_records_log_and_state(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_scheduler_capability()
    captured: dict[str, object] = {}

    monkeypatch.setattr(scheduler, "_resolve_current_ghdp_executable", lambda: "ghdp")

    def _fake_run_cmd(cmd, **kwargs):
        captured["cwd"] = kwargs.get("cwd")
        return SimpleNamespace(returncode=0, stdout="sync ok", stderr="", cmd=cmd)

    monkeypatch.setattr(scheduler, "run_cmd", _fake_run_cmd)

    result = scheduler.run_scheduled_job(job_id="background-sync")

    assert result["task_id"] == "background-sync"
    assert captured["cwd"] is None
    state = get_tool_state(_state_key_for("background-sync"))
    assert state["last_status"] == "ok"
    log_path = Path(state["last_log_path"])
    assert log_path.exists()
    assert "sync ok" in log_path.read_text(encoding="utf-8")


def test_run_scheduled_job_failure_updates_state_and_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_scheduler_capability()

    monkeypatch.setattr(scheduler, "_resolve_current_ghdp_executable", lambda: "ghdp")
    monkeypatch.setattr(
        scheduler,
        "run_cmd",
        lambda cmd, **kwargs: SimpleNamespace(returncode=7, stdout="", stderr="boom", cmd=cmd),
    )

    with pytest.raises(scheduler.PlatformError) as exc_info:
        scheduler.run_scheduled_job(job_id="background-sync")

    assert exc_info.value.code == "E_SCHEDULE_JOB_FAILED"
    state = get_tool_state(_state_key_for("background-sync"))
    assert state["last_status"] == "error"
    assert Path(state["last_log_path"]).exists()


def test_resolve_current_ghdp_executable_keeps_suffixed_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = tmp_path / "ghdp-eppe7270.exe"
    launcher.write_text("", encoding="utf-8")

    monkeypatch.setattr(scheduler.sys, "argv", [str(launcher)])

    assert scheduler._resolve_current_ghdp_executable() == str(launcher.resolve())


def test_resolve_current_ghdp_executable_discovers_suffixed_launcher_from_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher_dir = tmp_path / "bin"
    launcher_dir.mkdir()
    launcher = launcher_dir / "ghdp-eppe7270.exe"
    launcher.write_text("", encoding="utf-8")

    monkeypatch.setattr(scheduler.sys, "argv", ["ghdp"])
    monkeypatch.setenv("PATH", str(launcher_dir))

    assert scheduler._resolve_current_ghdp_executable() == str(launcher.resolve())


def test_resolve_current_ghdp_executable_accepts_pipx_suffix_without_dash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = tmp_path / "ghdpeppe7270u7.exe"
    launcher.write_text("", encoding="utf-8")

    monkeypatch.setattr(scheduler.sys, "argv", [str(launcher)])

    assert scheduler._resolve_current_ghdp_executable() == str(launcher.resolve())


def test_list_schedule_jobs_marks_non_target_platform_as_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    capability_root = _seed_scheduler_capability()

    monkeypatch.setattr(scheduler, "_current_platform_name", lambda: "linux")
    monkeypatch.setattr(
        scheduler_assets,
        "ensure_scheduler_assets_synced",
        lambda: {"target_path": str(capability_root)},
    )

    items = scheduler.list_schedule_jobs(scope="user", job_id="background-sync")

    assert items[0]["status"] == "platform_skipped"
    assert items[0]["action"] == "none"


def test_remove_schedule_jobs_marks_state_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    capability_root = _seed_scheduler_capability()
    applied_specs: list[scheduler_windows.WindowsTaskSpec] = []

    monkeypatch.setattr(scheduler, "_current_platform_name", lambda: "windows")
    monkeypatch.setattr(scheduler_windows, "provider_supported", lambda: True)
    monkeypatch.setattr(scheduler, "_resolve_current_ghdp_executable", lambda: "C:\\ghdp.exe")
    monkeypatch.setattr(
        scheduler_assets,
        "ensure_scheduler_assets_synced",
        lambda: {"target_path": str(capability_root)},
    )
    def _query_task(task_name: str) -> scheduler_windows.WindowsTaskObservation:
        if not applied_specs:
            return scheduler_windows.WindowsTaskObservation(exists=False, task_name=task_name)
        spec = applied_specs[0]
        return scheduler_windows.WindowsTaskObservation(
            exists=True,
            task_name=task_name,
            description=spec.description,
            interval_minutes=spec.interval_minutes,
            random_delay_minutes=spec.random_delay_minutes,
            command="powershell.exe",
            arguments=f'-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{spec.wrapper_path}"',
            allow_on_battery=spec.allow_on_battery,
            stop_on_battery=spec.stop_on_battery,
            require_network=spec.require_network,
            wake_machine=spec.wake_machine,
            start_when_available=spec.start_when_available,
            execution_time_limit_minutes=spec.execution_time_limit_minutes,
            multiple_instances_policy="IgnoreNew",
            restart_count=spec.restart_count,
            restart_interval_minutes=spec.restart_interval_minutes,
            hidden=spec.hidden,
        )

    monkeypatch.setattr(scheduler_windows, "query_task", _query_task)
    monkeypatch.setattr(scheduler_windows, "apply_task", lambda spec: applied_specs.append(spec))
    monkeypatch.setattr(scheduler_windows, "remove_task", lambda task_name: None)

    scheduler.apply_schedule_jobs(scope="user", job_id="background-sync")

    results = scheduler.remove_schedule_jobs(scope="user", job_id="background-sync")

    assert len(results) == 1
    state = get_tool_state(_state_key_for("background-sync"))
    assert state["registration_status"] == "removed"
    assert not Path(results[0]["wrapper_path"]).exists()


def test_bare_schedule_interactive_routes_to_list(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        schedule_commands,
        "schedule_list",
        lambda task_id=None: captured.setdefault("task_id", task_id),
    )

    result = runner.invoke(app, ["schedule"], input="1\n\n")

    assert result.exit_code == 0
    assert captured["task_id"] is None
    assert "available schedule commands:" in result.output


def test_bare_schedule_interactive_routes_to_apply_with_prompted_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_apply(*, task_id, auto_approve, dry_run):
        captured.update(
            {
                "task_id": task_id,
                "auto_approve": auto_approve,
                "dry_run": dry_run,
            }
        )

    monkeypatch.setattr(schedule_commands, "schedule_apply", _fake_apply)

    result = runner.invoke(app, ["schedule"], input="3\nbackground-sync\ny\n")

    assert result.exit_code == 0
    assert captured == {
        "task_id": "background-sync",
        "auto_approve": False,
        "dry_run": True,
    }


def test_bare_schedule_non_interactive_prints_help_without_prompting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        schedule_commands,
        "_print_schedule_action_menu",
        lambda: (_ for _ in ()).throw(AssertionError("interactive menu should not render in non-interactive mode")),
    )

    result = runner.invoke(app, ["--non-interactive", "schedule"])

    assert result.exit_code == 0
    assert "Manage GHDP scheduled background jobs." in result.output


def test_list_schedule_jobs_marks_missing_wrapper_as_drifted(monkeypatch: pytest.MonkeyPatch) -> None:
    capability_root = _seed_scheduler_capability()
    applied_specs: list[scheduler_windows.WindowsTaskSpec] = []

    monkeypatch.setattr(scheduler, "_current_platform_name", lambda: "windows")
    monkeypatch.setattr(scheduler_windows, "provider_supported", lambda: True)
    monkeypatch.setattr(scheduler, "_resolve_current_ghdp_executable", lambda: "C:\\ghdp.exe")
    monkeypatch.setattr(
        scheduler_assets,
        "ensure_scheduler_assets_synced",
        lambda: {"target_path": str(capability_root)},
    )
    def _query_task(task_name: str) -> scheduler_windows.WindowsTaskObservation:
        if not applied_specs:
            return scheduler_windows.WindowsTaskObservation(exists=False, task_name=task_name)
        spec = applied_specs[0]
        return scheduler_windows.WindowsTaskObservation(
            exists=True,
            task_name=task_name,
            description=spec.description,
            interval_minutes=spec.interval_minutes,
            random_delay_minutes=spec.random_delay_minutes,
            command="powershell.exe",
            arguments=f'-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{spec.wrapper_path}"',
            allow_on_battery=spec.allow_on_battery,
            stop_on_battery=spec.stop_on_battery,
            require_network=spec.require_network,
            wake_machine=spec.wake_machine,
            start_when_available=spec.start_when_available,
            execution_time_limit_minutes=spec.execution_time_limit_minutes,
            multiple_instances_policy="IgnoreNew",
            restart_count=spec.restart_count,
            restart_interval_minutes=spec.restart_interval_minutes,
            hidden=spec.hidden,
        )

    monkeypatch.setattr(scheduler_windows, "query_task", _query_task)
    monkeypatch.setattr(scheduler_windows, "apply_task", lambda spec: applied_specs.append(spec))

    results = scheduler.apply_schedule_jobs(scope="user", job_id="background-sync")
    wrapper_path = Path(results[0]["wrapper_path"])
    wrapper_path.unlink()

    monkeypatch.setattr(
        scheduler_windows,
        "query_task",
        lambda task_name: scheduler_windows.WindowsTaskObservation(
            exists=True,
            task_name=task_name,
            description=applied_specs[0].description,
            interval_minutes=45,
            random_delay_minutes=5,
            command="powershell.exe",
            arguments=f'-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{wrapper_path}"',
            allow_on_battery=True,
            stop_on_battery=False,
            require_network=False,
            wake_machine=False,
            start_when_available=True,
            execution_time_limit_minutes=15,
            multiple_instances_policy="IgnoreNew",
            restart_count=3,
            restart_interval_minutes=15,
            hidden=False,
        ),
    )

    items = scheduler.list_schedule_jobs(scope="user", job_id="background-sync")

    assert items[0]["status"] == "drifted"
    assert items[0]["action"] == "repair"


def test_list_schedule_jobs_marks_unsupported_phase1_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    capability_root = _seed_scheduler_capability()
    tasks_path = capability_root / "tasks.json"
    payload = json.loads(tasks_path.read_text(encoding="utf-8"))
    payload["tasks"][0]["conditions"] = {"idle_only": True}
    tasks_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(scheduler, "_current_platform_name", lambda: "windows")
    monkeypatch.setattr(scheduler_windows, "provider_supported", lambda: True)
    monkeypatch.setattr(
        scheduler_assets,
        "ensure_scheduler_assets_synced",
        lambda: {"target_path": str(capability_root)},
    )

    items = scheduler.list_schedule_jobs(scope="user", job_id="background-sync")

    assert items[0]["status"] == "policy_unsupported"
    assert items[0]["policy_error"] == "conditions.idle_only"


def test_schedule_list_command_auto_syncs_assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    capability_root = _seed_scheduler_capability()
    calls: list[bool] = []

    monkeypatch.setattr(scheduler, "_current_platform_name", lambda: "linux")
    monkeypatch.chdir(outside)

    def _fake_sync():
        calls.append(True)
        return {"target_path": str(capability_root)}

    monkeypatch.setattr(scheduler_assets, "ensure_scheduler_assets_synced", _fake_sync)

    result = runner.invoke(app, ["schedule", "list"])

    assert result.exit_code == 0
    assert calls == [True]


def test_schedule_apply_non_interactive_requires_auto_approve(monkeypatch: pytest.MonkeyPatch) -> None:
    capability_root = _seed_scheduler_capability()

    monkeypatch.setattr(scheduler, "_current_platform_name", lambda: "windows")
    monkeypatch.setattr(scheduler_windows, "provider_supported", lambda: True)
    monkeypatch.setattr(
        scheduler_assets,
        "ensure_scheduler_assets_synced",
        lambda: {"target_path": str(capability_root)},
    )
    monkeypatch.setattr(
        scheduler_windows,
        "query_task",
        lambda task_name: scheduler_windows.WindowsTaskObservation(exists=False, task_name=task_name),
    )

    result = runner.invoke(
        app,
        ["--non-interactive", "schedule", "apply"],
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, scheduler.PlatformError)
    assert result.exception.code == "E_SCHEDULE_CONFIRM_REQUIRED"


def test_schedule_apply_reports_policy_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    capability_root = _seed_scheduler_capability()

    monkeypatch.setattr(scheduler, "_current_platform_name", lambda: "windows")
    monkeypatch.setattr(scheduler_windows, "provider_supported", lambda: True)
    monkeypatch.setattr(
        scheduler_assets,
        "ensure_scheduler_assets_synced",
        lambda: {"target_path": str(capability_root)},
    )

    tasks_path = capability_root / "tasks.json"
    payload = json.loads(tasks_path.read_text(encoding="utf-8"))
    payload["tasks"][0]["conditions"] = {"idle_only": True}
    tasks_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    result = runner.invoke(app, ["schedule", "apply", "--task-id", "background-sync", "--auto-approve"])

    assert result.exit_code == 1
    assert isinstance(result.exception, scheduler.PlatformError)
    assert result.exception.code == "E_SCHEDULE_POLICY_UNSUPPORTED"


def test_schedule_apply_prints_trust_summary_from_observed_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        scheduler,
        "preview_schedule_operation",
        lambda **kwargs: {
            "items": [
                {
                    "task_id": "sync-run-background",
                    "action": "apply",
                    "provider": "windows_task_scheduler",
                },
                {
                    "task_id": "version-change-latest-stable",
                    "action": "apply",
                    "provider": "windows_task_scheduler",
                },
            ],
            "readiness": {"blockers": [], "warnings": []},
        },
    )
    monkeypatch.setattr(
        scheduler,
        "apply_schedule_jobs",
        lambda **kwargs: [
            {"task_id": "sync-run-background", "task_name": "GHDP-background-scheduler-sync-run-background"},
            {"task_id": "version-change-latest-stable", "task_name": "GHDP-background-scheduler-version-change-latest-stable"},
        ],
    )
    monkeypatch.setattr(
        scheduler,
        "build_schedule_apply_trust_summary",
        lambda **kwargs: {
            "items": [
                {
                    "asset_source_kind": "packaged",
                    "asset_materialization_state": "installed",
                    "asset_fallback_active": True,
                    "asset_source_explanation": "using packaged emergency bootstrap scheduler assets",
                }
            ],
            "active_items": [
                {
                    "task_id": "sync-run-background",
                    "interval_minutes": 1440,
                    "provider": "windows_task_scheduler",
                },
                {
                    "task_id": "version-change-latest-stable",
                    "interval_minutes": 60,
                    "provider": "windows_task_scheduler",
                },
            ],
            "logs_path": "C:\\scheduler-logs",
            "auto_update_item": {
                "task_id": "version-change-latest-stable",
                "interval_minutes": 60,
                "provider": "windows_task_scheduler",
            },
        },
    )

    result = runner.invoke(app, ["schedule", "apply", "--auto-approve"])

    assert result.exit_code == 0
    assert "Applied sync-run-background as GHDP-background-scheduler-sync-run-background" in result.stdout
    assert "Changed: 2 task(s)" in result.stdout
    assert "Active: sync-run-background every 1440m, version-change-latest-stable every 60m" in result.stdout
    assert "Logs: C:\\scheduler-logs" in result.stdout
    assert "Auto-update: version-change-latest-stable will check for the latest stable release every 60m." in result.stdout
    assert "asset source: using packaged emergency bootstrap scheduler assets" in result.stdout


def test_schedule_check_surfaces_asset_source_only_when_fallback_is_active(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        scheduler,
        "list_schedule_jobs",
        lambda **kwargs: [
            {
                "task_id": "sync-run-background",
                "status": "ok",
                "action": "none",
                "policy_error": "",
                "asset_source_kind": "packaged",
                "asset_materialization_state": "cached",
                "asset_fallback_active": True,
                "asset_source_explanation": "using cached packaged emergency bootstrap scheduler assets",
            }
        ],
    )

    result = runner.invoke(app, ["schedule", "check"])

    assert result.exit_code == 0
    assert "asset source: using cached packaged emergency bootstrap scheduler assets" in result.stdout


def test_schedule_run_job_command_uses_user_scoped_installed_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    _seed_scheduler_capability()

    monkeypatch.chdir(outside)
    monkeypatch.setattr(scheduler, "_resolve_current_ghdp_executable", lambda: "ghdp")
    monkeypatch.setattr(
        scheduler,
        "run_cmd",
        lambda cmd, **kwargs: SimpleNamespace(returncode=0, stdout="sync ok", stderr="", cmd=cmd),
    )

    result = runner.invoke(
        app,
        ["schedule", "run-job", "--task-id", "background-sync"],
    )

    assert result.exit_code == 0
    assert "Executed schedule task background-sync" in result.stdout


def test_schedule_run_command_auto_syncs_and_executes_task(monkeypatch: pytest.MonkeyPatch) -> None:
    capability_root = _seed_scheduler_capability()
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        scheduler_assets,
        "ensure_scheduler_assets_synced",
        lambda: {"target_path": str(capability_root)},
    )
    monkeypatch.setattr(scheduler, "_resolve_current_ghdp_executable", lambda: "ghdp")

    def _fake_run_cmd(cmd, **kwargs):
        captured["cwd"] = kwargs.get("cwd")
        return SimpleNamespace(returncode=0, stdout="sync ok", stderr="", cmd=cmd)

    monkeypatch.setattr(scheduler, "run_cmd", _fake_run_cmd)

    result = runner.invoke(app, ["schedule", "run", "--task-id", "background-sync"])

    assert result.exit_code == 0
    assert "Executed background-sync exit_code=0" in result.stdout
    assert captured["cwd"] is None


def test_hidden_post_install_scheduler_setup_command_reports_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        scheduler,
        "ensure_post_install_scheduler_setup",
        lambda **kwargs: {
            "action": "completed",
            "planned": [{"task_id": "schedule-apply-background"}],
            "applied": [{"task_id": "schedule-apply-background"}],
        },
    )

    result = runner.invoke(app, ["_post-install-scheduler-setup"])

    assert result.exit_code == 0
    assert "scheduler setup: completed (1 task(s) updated)" in result.stdout


def test_scheduler_runtime_path_prefix_includes_homebrew_and_user_bin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home_root = tmp_path / "home"
    home_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home_root))
    monkeypatch.setenv("USERPROFILE", str(home_root))

    prefix = scheduler._scheduler_runtime_path_prefix()

    assert prefix.startswith(str(home_root / ".local" / "bin"))
    assert "/opt/homebrew/bin" in prefix
    assert "/usr/local/bin" in prefix
    assert prefix.endswith("/sbin")


def test_write_wrapper_posix_bootstraps_runtime_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_scheduler_capability()
    task = scheduler._resolve_tasks(scope="user", task_id="background-sync", ensure_synced=False)[0]
    monkeypatch.setattr(scheduler, "_resolve_current_ghdp_executable", lambda: "/tmp/ghdp")

    wrapper_path = scheduler._write_wrapper(task, provider="launchd")
    wrapper_body = wrapper_path.read_text(encoding="utf-8")

    assert scheduler.WRAPPER_SCHEMA_MARKER in wrapper_body
    assert 'PATH="' in wrapper_body
    assert "/opt/homebrew/bin" in wrapper_body
    assert "/usr/local/bin" in wrapper_body
    assert "${PATH:-}" in wrapper_body


def test_list_schedule_jobs_marks_legacy_wrapper_schema_as_drifted(monkeypatch: pytest.MonkeyPatch) -> None:
    capability_root = _seed_scheduler_capability()
    applied_specs: list[scheduler_windows.WindowsTaskSpec] = []

    monkeypatch.setattr(scheduler, "_current_platform_name", lambda: "windows")
    monkeypatch.setattr(scheduler_windows, "provider_supported", lambda: True)
    monkeypatch.setattr(scheduler, "_resolve_current_ghdp_executable", lambda: "C:\\ghdp.exe")
    monkeypatch.setattr(
        scheduler_assets,
        "ensure_scheduler_assets_synced",
        lambda: {"target_path": str(capability_root)},
    )

    def _query_task(task_name: str) -> scheduler_windows.WindowsTaskObservation:
        if not applied_specs:
            return scheduler_windows.WindowsTaskObservation(exists=False, task_name=task_name)
        spec = applied_specs[0]
        return scheduler_windows.WindowsTaskObservation(
            exists=True,
            task_name=task_name,
            description=spec.description,
            interval_minutes=spec.interval_minutes,
            random_delay_minutes=spec.random_delay_minutes,
            command="powershell.exe",
            arguments=f'-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{spec.wrapper_path}"',
            allow_on_battery=spec.allow_on_battery,
            stop_on_battery=spec.stop_on_battery,
            require_network=spec.require_network,
            wake_machine=spec.wake_machine,
            start_when_available=spec.start_when_available,
            execution_time_limit_minutes=spec.execution_time_limit_minutes,
            multiple_instances_policy="IgnoreNew",
            restart_count=spec.restart_count,
            restart_interval_minutes=spec.restart_interval_minutes,
            hidden=spec.hidden,
        )

    monkeypatch.setattr(scheduler_windows, "query_task", _query_task)
    monkeypatch.setattr(scheduler_windows, "apply_task", lambda spec: applied_specs.append(spec))

    results = scheduler.apply_schedule_jobs(scope="user", job_id="background-sync")
    wrapper_path = Path(results[0]["wrapper_path"])
    wrapper_lines = [
        line for line in wrapper_path.read_text(encoding="utf-8").splitlines() if scheduler.WRAPPER_SCHEMA_MARKER not in line
    ]
    wrapper_path.write_text("\n".join(wrapper_lines) + "\n", encoding="utf-8")

    items = scheduler.list_schedule_jobs(scope="user", job_id="background-sync")

    assert items[0]["status"] == "drifted"
    assert items[0]["action"] == "repair"
