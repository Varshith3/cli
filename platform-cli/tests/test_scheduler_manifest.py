from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


scheduler_manifest = importlib.import_module("platform_cli.manifests.scheduler")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home_root = tmp_path / "home"
    home_root.mkdir()
    monkeypatch.setenv("HOME", str(home_root))
    monkeypatch.setenv("USERPROFILE", str(home_root))
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
                        "id": "sync-run-background",
                        "description": "Reconcile GHDP-managed synced content on a background interval.",
                        "required": True,
                        "trigger": {"minutes": 360},
                        "conditions": {"require_network": True},
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


def test_load_scheduler_tasks_reads_user_scoped_installed_capability() -> None:
    _seed_scheduler_capability()

    tasks = scheduler_manifest.load_scheduler_tasks(task_id="sync-run-background")

    assert len(tasks) == 1
    task = tasks[0]
    assert isinstance(task, scheduler_manifest.ScheduleTaskDefinition)
    assert task.task_id == "sync-run-background"
    assert task.required is True
    assert task.platforms == ("windows", "darwin", "linux")
    assert task.command == {"type": "ghdp", "args": ["sync", "run", "--auto-approve"]}
    assert task.trigger == {"type": "interval", "minutes": 360, "random_delay_minutes": 5}
    assert task.execution["timeout_minutes"] == 15
    assert task.conditions["require_network"] is True
    assert task.run_context["mode"] == "user_session"


def test_load_scheduler_tasks_rejects_invalid_platforms() -> None:
    capability_root = _seed_scheduler_capability()

    tasks_path = capability_root / "tasks.json"
    payload = json.loads(tasks_path.read_text(encoding="utf-8"))
    payload["tasks"].append(
        {
            "schema_version": "1.0",
            "id": "bad-task",
            "description": "Bad task",
            "platforms": ["windows", "plan9"],
            "command": {"type": "ghdp", "args": ["sync", "check"]},
            "trigger": {"type": "interval", "minutes": 30},
        }
    )
    tasks_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(scheduler_manifest.PlatformError) as exc_info:
        scheduler_manifest.load_scheduler_tasks(task_id="bad-task")

    assert exc_info.value.code == "E_SCHEDULER_PLATFORM_INVALID"


def test_installed_capability_root_uses_user_home(isolated_home: Path) -> None:
    assert scheduler_manifest.installed_capability_root() == isolated_home / ".ghdp" / "capabilities" / "scheduler"


def test_load_scheduler_tasks_rejects_recursive_schedule_command() -> None:
    capability_root = _seed_scheduler_capability()

    tasks_path = capability_root / "tasks.json"
    payload = json.loads(tasks_path.read_text(encoding="utf-8"))
    payload["tasks"].append(
        {
            "schema_version": "1.0",
            "id": "recursive-task",
            "description": "Recursive task",
            "command": {"type": "ghdp", "args": ["schedule", "list"]},
            "trigger": {"type": "interval", "minutes": 30},
        }
    )
    tasks_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(scheduler_manifest.PlatformError) as exc_info:
        scheduler_manifest.load_scheduler_tasks(task_id="recursive-task")

    assert exc_info.value.code == "E_SCHEDULER_COMMAND_INVALID"


def test_load_scheduler_tasks_requires_canonical_tasks_file() -> None:
    capability_root = _seed_scheduler_capability()
    (capability_root / "tasks.json").unlink()

    with pytest.raises(scheduler_manifest.PlatformError) as exc_info:
        scheduler_manifest.load_scheduler_tasks(task_id="sync-run-background")

    assert exc_info.value.code == "E_SCHEDULER_CAPABILITY_INVALID"


def test_installed_scheduler_assets_status_reports_incomplete_install() -> None:
    capability_root = scheduler_manifest.installed_capability_root()
    capability_root.mkdir(parents=True, exist_ok=True)
    (capability_root / "tasks.json").write_text("{}", encoding="utf-8")

    ready, reason = scheduler_manifest.installed_scheduler_assets_status()

    assert ready is False
    assert reason == "E_SCHEDULER_CAPABILITY_MISSING"


def test_packaged_bootstrap_assets_load_minimal_first_stable_subset() -> None:
    tasks = scheduler_manifest.load_scheduler_tasks(capability_root=scheduler_manifest.packaged_bootstrap_root())

    assert {task.task_id for task in tasks} == {
        "schedule-apply-background",
        "sync-run-background",
        "version-change-latest-stable",
    }


def test_packaged_bootstrap_assets_match_scheduler_contract_subset() -> None:
    capability_root = _seed_scheduler_capability()
    tasks_path = capability_root / "tasks.json"
    payload = json.loads(tasks_path.read_text(encoding="utf-8"))
    payload["tasks"].append(
        {
            "schema_version": "1.0",
            "id": "version-change-latest-stable",
            "description": "Auto-update GHDP to the latest stable release",
            "required": True,
            "trigger": {"minutes": 60},
            "conditions": {"require_network": True},
            "command": {"args": ["version", "change", "--latest-stable", "--method", "auto"]},
        }
    )
    tasks_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    ready, reason = scheduler_manifest.scheduler_assets_status(
        capability_root=scheduler_manifest.packaged_bootstrap_root()
    )

    assert ready is True, reason
    packaged_tasks = scheduler_manifest.load_scheduler_tasks(capability_root=scheduler_manifest.packaged_bootstrap_root())
    synced_tasks = scheduler_manifest.load_scheduler_tasks(capability_root=capability_root)
    assert {task.task_id for task in packaged_tasks} <= {task.task_id for task in synced_tasks}


def test_installed_scheduler_tasks_merge_missing_required_packaged_tasks() -> None:
    _seed_scheduler_capability()

    tasks = scheduler_manifest.load_scheduler_tasks()

    task_ids = {task.task_id for task in tasks}
    assert "sync-run-background" in task_ids
    assert "version-change-latest-stable" in task_ids
    assert "schedule-apply-background" in task_ids
