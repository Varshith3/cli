from __future__ import annotations

import json
from pathlib import Path

import pytest

from platform_cli.manifests import scheduler as scheduler_manifest
from platform_cli.tools import scheduler, scheduler_assets, scheduler_cron, scheduler_launchd


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


def _seed_scheduler_capability(*, platforms: list[str]) -> Path:
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
                    "platforms": platforms,
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
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return capability_root


def test_list_schedule_jobs_uses_launchd_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    capability_root = _seed_scheduler_capability(platforms=["darwin"])
    monkeypatch.setattr(scheduler, "_current_platform_name", lambda: "darwin")
    monkeypatch.setattr(scheduler_launchd, "provider_supported", lambda: True)
    monkeypatch.setattr(
        scheduler_assets,
        "ensure_scheduler_assets_synced",
        lambda: {"target_path": str(capability_root)},
    )
    monkeypatch.setattr(
        scheduler_launchd,
        "query_task",
        lambda task_name: scheduler_launchd.LaunchdTaskObservation(exists=False, task_name=task_name, plist_path=Path("/tmp/test.plist")),
    )

    items = scheduler.list_schedule_jobs(scope="user", job_id="background-sync")

    assert items[0]["provider"] == "launchd"
    assert items[0]["status"] == "missing"


def test_apply_schedule_jobs_writes_unix_wrapper_for_launchd(monkeypatch: pytest.MonkeyPatch) -> None:
    capability_root = _seed_scheduler_capability(platforms=["darwin"])
    applied: list[object] = []

    monkeypatch.setattr(scheduler, "_current_platform_name", lambda: "darwin")
    monkeypatch.setattr(scheduler_launchd, "provider_supported", lambda: True)
    monkeypatch.setattr(scheduler, "_resolve_current_ghdp_executable", lambda: "/usr/local/bin/ghdp")
    monkeypatch.setattr(
        scheduler_assets,
        "ensure_scheduler_assets_synced",
        lambda: {"target_path": str(capability_root)},
    )

    def _query(task_name: str):
        if not applied:
            return scheduler_launchd.LaunchdTaskObservation(exists=False, task_name=task_name, plist_path=Path("/tmp/test.plist"))
        spec = applied[0]
        return scheduler_launchd.LaunchdTaskObservation(
            exists=True,
            task_name=task_name,
            plist_path=Path("/tmp/test.plist"),
            label=spec.task_name,
            interval_minutes=spec.interval_minutes,
            program_arguments=(str(spec.wrapper_path),),
            stdout_path=str(spec.stdout_path),
            stderr_path=str(spec.stderr_path),
            loaded=True,
        )

    monkeypatch.setattr(scheduler_launchd, "query_task", _query)
    monkeypatch.setattr(scheduler_launchd, "apply_task", lambda spec: applied.append(spec))

    results = scheduler.apply_schedule_jobs(scope="user", job_id="background-sync")

    wrapper_path = Path(results[0]["wrapper_path"])
    body = wrapper_path.read_text(encoding="utf-8")
    assert wrapper_path.suffix == ".sh"
    assert "#!/bin/sh" in body
    assert "--task-id 'background-sync'" in body


def test_list_schedule_jobs_uses_cron_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    capability_root = _seed_scheduler_capability(platforms=["linux"])
    monkeypatch.setattr(scheduler, "_current_platform_name", lambda: "linux")
    monkeypatch.setattr(scheduler_cron, "provider_supported", lambda: True)
    monkeypatch.setattr(
        scheduler_assets,
        "ensure_scheduler_assets_synced",
        lambda: {"target_path": str(capability_root)},
    )
    monkeypatch.setattr(
        scheduler_cron,
        "query_task",
        lambda task_name: scheduler_cron.CronTaskObservation(exists=False, task_name=task_name),
    )

    items = scheduler.list_schedule_jobs(scope="user", job_id="background-sync")

    assert items[0]["provider"] == "cron"
    assert items[0]["status"] == "missing"
