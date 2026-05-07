from __future__ import annotations

from collections.abc import Callable

import pytest

from platform_cli.commands import schedule as schedule_commands
from platform_cli.commands import sync as sync_commands
from platform_cli.core import access as access_core
from platform_cli.core.context import ctx as cli_ctx


@pytest.fixture(autouse=True)
def _reset_cli_ctx() -> None:
    cli_ctx.non_interactive = False
    cli_ctx.quiet = False
    cli_ctx.json = False


class _RecorderStatus:
    def __init__(self, events: list[tuple[str, str | None]], prefix: str) -> None:
        events.append(("status:init", prefix))
        self._events = events

    def update(self, message: str) -> None:
        self._events.append(("status:update", message))

    def finish(self, message: str | None = None) -> None:
        self._events.append(("status:finish", message))


def _fake_status_factory(events: list[tuple[str, str | None]]) -> Callable[[str], _RecorderStatus]:
    return lambda command: _RecorderStatus(events, f"[{command}]")


def _echo_recorder(events: list[tuple[str, str | None]]) -> Callable[[str], None]:
    return lambda message="": events.append(("echo", str(message)))


def _approval_recorder(events: list[tuple[str, str | None]], label: str) -> Callable[..., None]:
    def _record(*args, **kwargs) -> None:
        events.append((label, str(kwargs.get("operation"))))

    return _record


def _call_index(events: list[tuple[str, str | None]], needle: tuple[str, str | None]) -> int:
    for index, event in enumerate(events):
        if event == needle:
            return index
    raise AssertionError(f"Event not found: {needle!r}\n{events!r}")


def test_sync_update_clears_status_before_preview_rows_and_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, str | None]] = []

    monkeypatch.setattr(access_core, "ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(sync_commands, "command_status", _fake_status_factory(events))
    monkeypatch.setattr(sync_commands.typer, "echo", _echo_recorder(events))
    monkeypatch.setattr(
        sync_commands,
        "_require_approval",
        lambda auto_approve, *, operation, prompt: events.append(("approval", operation)),
    )
    monkeypatch.setattr(
        sync_commands,
        "preview_content_updates",
        lambda **kwargs: {
            "capabilities": [
                {
                    "capability": "cap-a",
                    "action": "update",
                    "local_version": "1.0",
                    "latest_version": "1.1",
                    "updatable_files": ["a.txt"],
                    "ignored_new_files": [],
                }
            ]
        },
    )
    monkeypatch.setattr(
        sync_commands,
        "apply_content_update",
        lambda capability, **kwargs: {"updated_count": 2, "latest_version": "1.1"},
    )

    sync_commands.sync_update(capability=None, auto_approve=False, repo_root=None)

    assert _call_index(events, ("status:finish", None)) < _call_index(events, ("echo", "cap-a: 1.0 -> 1.1"))
    assert _call_index(events, ("echo", "cap-a: 1.0 -> 1.1")) < _call_index(events, ("approval", "sync update"))
    assert events[-1] == ("status:finish", "Update complete. Capabilities updated: 1")


def test_sync_repair_clears_status_before_preview_rows_and_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, str | None]] = []

    monkeypatch.setattr(access_core, "ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(sync_commands, "command_status", _fake_status_factory(events))
    monkeypatch.setattr(sync_commands.typer, "echo", _echo_recorder(events))
    monkeypatch.setattr(
        sync_commands,
        "_require_approval",
        lambda auto_approve, *, operation, prompt: events.append(("approval", operation)),
    )
    monkeypatch.setattr(
        sync_commands,
        "preview_content_updates",
        lambda **kwargs: {
            "capabilities": [
                {
                    "capability": "cap-b",
                    "action": "repair",
                    "local_version": "1.0",
                    "latest_version": "1.0",
                    "missing_local_files": ["b.txt"],
                    "updatable_files": [],
                    "ignored_new_files": [],
                }
            ]
        },
    )
    monkeypatch.setattr(
        sync_commands,
        "repair_content",
        lambda capability, **kwargs: {"repaired_count": 1},
    )

    sync_commands.sync_repair(capability=None, auto_approve=False, repo_root=None)

    assert _call_index(events, ("status:finish", None)) < _call_index(events, ("echo", "cap-b: repair files b.txt"))
    assert _call_index(events, ("echo", "cap-b: repair files b.txt")) < _call_index(events, ("approval", "sync repair"))
    assert events[-1] == ("status:finish", "Repair complete. Capabilities repaired: 1")


def test_sync_run_clears_status_before_preview_rows_and_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, str | None]] = []

    monkeypatch.setattr(access_core, "ensure_capability", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        sync_commands,
        "_sync_policy",
        lambda: access_core.SyncCapabilityPolicy(
            context=access_core.AccessContext(
                actor="hi",
                identity_status="ok",
                actor_source="gh",
                base_persona="non-admin",
                persona="non-admin",
                active_mode="non-admin",
                admin_users_source="missing",
                selected_team="data_platform",
                effective_team="data_platform",
                assumed_team="",
                team_locked=True,
                token_status="missing",
                token_source="missing",
                token_scope="",
                token_team="",
                token_expires_at=0,
                capabilities=(),
                policy_source="pkg",
                release_channel="prerelease",
                release_policy_source="pkg",
                support_contact="support",
            ),
            restricted=True,
            allow_configured=True,
            allowed_capabilities=("cap-c", "cap-d"),
            denied_capabilities=(),
        ),
    )
    monkeypatch.setattr(sync_commands, "command_status", _fake_status_factory(events))
    monkeypatch.setattr(sync_commands.typer, "echo", _echo_recorder(events))
    monkeypatch.setattr(
        sync_commands,
        "_require_approval",
        lambda auto_approve, *, operation, prompt: events.append(("approval", operation)),
    )
    monkeypatch.setattr(
        sync_commands,
        "run_sync_actions",
        lambda **kwargs: {
            "preview": {
                "capabilities": [
                    {
                        "capability": "cap-c",
                        "action": "repair",
                        "local_version": "1.0",
                        "latest_version": "1.1",
                        "missing_local_files": ["c.txt"],
                        "updatable_files": [],
                        "ignored_new_files": [],
                    }
                ]
            },
            "repairs": [
                {
                    "capability": "cap-c",
                    "action": "repair",
                    "missing_local_files": ["c.txt"],
                }
            ],
            "updates": [
                {
                    "capability": "cap-d",
                    "local_version": "2.0",
                    "latest_version": "2.1",
                    "updatable_files": ["d.txt"],
                    "ignored_new_files": [],
                }
            ],
            "results": {
                "repairs": [
                    {
                        "capability": "cap-c",
                        "action": "repair",
                        "missing_local_files": ["c.txt"],
                        "repaired_count": 1,
                    }
                ],
                "updates": [
                    {
                        "capability": "cap-d",
                        "local_version": "2.0",
                        "latest_version": "2.1",
                        "updatable_files": ["d.txt"],
                        "ignored_new_files": [],
                        "updated_count": 1,
                    }
                ],
            },
            "blocked": [],
        },
    )
    monkeypatch.setattr(
        sync_commands,
        "repair_content",
        lambda capability, **kwargs: {"repaired_count": 1},
    )
    monkeypatch.setattr(
        sync_commands,
        "apply_content_update",
        lambda capability, **kwargs: {"updated_count": 1, "latest_version": "2.1"},
    )

    sync_commands.sync_run(capability=None, auto_approve=False, repo_root=None)

    assert _call_index(events, ("status:finish", None)) < _call_index(events, ("echo", "cap-c: repair files c.txt"))
    assert _call_index(events, ("echo", "[sync] context: mode=non-admin team=data_platform policy=restricted")) < _call_index(
        events, ("echo", "cap-c: repair files c.txt")
    )
    assert _call_index(events, ("echo", "cap-c: repair files c.txt")) < _call_index(events, ("approval", "sync run"))
    assert events[-1] == (
        "status:finish",
        "Run complete. bootstraps applied: 0; repairs applied: 1; updates applied: 1",
    )


@pytest.mark.parametrize(
    ("command_name", "runner", "preview_payload", "result_payload", "approval_label", "preview_line", "result_line"),
    [
        (
            "schedule apply",
            schedule_commands.schedule_apply,
            {
                "items": [
                    {"task_id": "task-a", "action": "apply", "provider": "windows_task_scheduler"}
                ],
                "readiness": {"blockers": [], "warnings": []},
            },
            [{"task_id": "task-a", "task_name": "Task A"}],
            "schedule apply",
            "task-a -> apply (windows_task_scheduler)",
            "Applied task-a as Task A",
        ),
        (
            "schedule repair",
            schedule_commands.schedule_repair,
            {
                "items": [
                    {"task_id": "task-b", "status": "missing", "provider": "windows_task_scheduler"}
                ],
                "readiness": {"blockers": [], "warnings": []},
            },
            [{"task_id": "task-b", "task_name": "Task B"}],
            "schedule repair",
            "task-b -> repair (windows_task_scheduler)",
            "Repaired task-b as Task B",
        ),
    ],
)
def test_schedule_mutations_clear_status_before_preview_rows_and_prompt(
    monkeypatch: pytest.MonkeyPatch,
    command_name: str,
    runner: Callable[..., None],
    preview_payload: dict[str, object],
    result_payload: list[dict[str, str]],
    approval_label: str,
    preview_line: str,
    result_line: str,
) -> None:
    events: list[tuple[str, str | None]] = []

    monkeypatch.setattr(schedule_commands, "command_status", _fake_status_factory(events))
    monkeypatch.setattr(schedule_commands.typer, "echo", _echo_recorder(events))
    monkeypatch.setattr(
        schedule_commands,
        "_require_auto_approve",
        lambda auto_approve, *, operation, prompt: events.append(("approval", operation)),
    )
    monkeypatch.setattr(
        schedule_commands.scheduler,
        "preview_schedule_operation",
        lambda **kwargs: preview_payload,
    )
    monkeypatch.setattr(
        schedule_commands.scheduler,
        "apply_schedule_jobs" if command_name.endswith("apply") else "repair_schedule_jobs",
        lambda **kwargs: result_payload,
    )
    monkeypatch.setattr(
        schedule_commands.scheduler,
        "build_schedule_apply_trust_summary",
        lambda **kwargs: {
            "items": [],
            "active_items": [],
            "logs_path": "/tmp/logs",
            "auto_update_item": None,
        },
    )

    runner(task_id=None, auto_approve=False, dry_run=False)

    assert _call_index(events, ("status:finish", None)) < _call_index(events, ("echo", preview_line))
    assert _call_index(events, ("echo", preview_line)) < _call_index(events, ("approval", approval_label))
    assert (
        "status:finish",
        "Schedule apply complete." if command_name.endswith("apply") else "Schedule repair complete. Tasks repaired: 1",
    ) in events
    assert result_line in [event[1] for event in events if event[0] == "echo"]
