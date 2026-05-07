from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
from typing import Any, Callable

from platform_cli.core.errors import PlatformError
from platform_cli.exec.runner import run_cmd
from platform_cli.manifests import scheduler as scheduler_manifest
from platform_cli.state.store import get_tool_state, update_tool_state
from platform_cli.tools import scheduler_assets
from platform_cli.tools import scheduler_cron
from platform_cli.tools import scheduler_launchd
from platform_cli.tools import scheduler_windows


CAPABILITY_ID = "background-scheduler"
USER_SCHEDULE_ROOT = Path.home() / ".ghdp" / "schedule"
WRAPPERS_DIR = USER_SCHEDULE_ROOT / "wrappers"
LOGS_DIR = USER_SCHEDULE_ROOT / "logs"
LOCKS_DIR = USER_SCHEDULE_ROOT / "locks"
SUPPORTED_SCOPES = {"user", "all"}
JOB_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
WRAPPER_SCHEMA_VERSION = "2"
WRAPPER_SCHEMA_MARKER = f"GHDP_WRAPPER_SCHEMA={WRAPPER_SCHEMA_VERSION}"
POSIX_SCHEDULER_PATH_ENTRIES = (
    "__HOME_LOCAL_BIN__",  # resolved to ~/.local/bin when wrappers are generated
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
)
AUTO_UPDATE_TASK_ID = "version-change-latest-stable"
StatusPrinter = Callable[[str], None]


def list_schedule_jobs(
    *,
    scope: str,
    repo_root: Path | None = None,
    job_id: str | None = None,
    ensure_synced: bool = True,
) -> list[dict[str, Any]]:
    return _preview_tasks(scope=scope, task_id=job_id, ensure_synced=ensure_synced)


def apply_schedule_jobs(*, scope: str, repo_root: Path | None = None, job_id: str | None = None) -> list[dict[str, Any]]:
    return _reconcile_tasks(scope=scope, task_id=job_id, repair_only=False)


def repair_schedule_jobs(*, scope: str, repo_root: Path | None = None, job_id: str | None = None) -> list[dict[str, Any]]:
    return _reconcile_tasks(scope=scope, task_id=job_id, repair_only=True)


def remove_schedule_jobs(*, scope: str, repo_root: Path | None = None, job_id: str | None = None) -> list[dict[str, Any]]:
    tasks = _resolve_tasks(scope=scope, task_id=job_id, ensure_synced=True)
    removed: list[dict[str, Any]] = []
    for task in tasks:
        provider = _provider_for_task(task)
        provider_module = _provider_module_by_name(provider)
        task_name = _task_name(task)
        if provider_module is not None:
            provider_module.remove_task(task_name)
        wrapper_path = _wrapper_path(task, provider=provider)
        if wrapper_path.exists():
            wrapper_path.unlink()
        update_tool_state(
            _state_key(task),
            {
                "provider": provider,
                "task_name": task_name,
                "wrapper_path": str(wrapper_path),
                "last_reconciled_at": _now_iso(),
                "registration_status": "removed",
            },
        )
        removed.append(
            {
                "task_id": task.task_id,
                "description": task.description,
                "task_name": task_name,
                "wrapper_path": str(wrapper_path),
            }
        )
    return removed


def force_run_schedule_job(*, scope: str, repo_root: Path | None = None, job_id: str) -> dict[str, Any]:
    tasks = _resolve_tasks(scope=scope, task_id=job_id, ensure_synced=True)
    if len(tasks) != 1:
        raise PlatformError(
            f"Expected exactly one schedule task for '{job_id}'.",
            code="E_SCHEDULE_JOB_NOT_FOUND",
            reason=job_id,
        )
    return _execute_task(tasks[0])


def run_scheduled_job(*, job_id: str) -> dict[str, Any]:
    tasks = _resolve_tasks(scope="user", task_id=job_id, ensure_synced=False)
    if len(tasks) != 1:
        raise PlatformError(
            f"Expected exactly one schedule task for '{job_id}'.",
            code="E_SCHEDULE_JOB_NOT_FOUND",
            reason=job_id,
        )
    return _execute_task(tasks[0])


def _execute_task(task: Any) -> dict[str, Any]:
    if not task.enabled:
        raise PlatformError(
            f"Schedule task '{task.task_id}' is disabled.",
            code="E_SCHEDULE_JOB_DISABLED",
            reason=task.task_id,
        )

    ghdp_executable = _resolve_current_ghdp_executable()
    command = [ghdp_executable]
    if "--non-interactive" not in task.command["args"]:
        command.append("--non-interactive")
    command.extend(task.command["args"])
    started_at = _now_iso()
    result = run_cmd(
        command,
        check=False,
        capture=True,
        encoding="utf-8",
        errors="replace",
        cwd=None,
    )
    finished_at = _now_iso()
    log_entry = {
        "task_id": task.task_id,
        "command": command,
        "started_at": started_at,
        "finished_at": finished_at,
        "exit_code": result.returncode,
        "stdout": _truncate(result.stdout),
        "stderr": _truncate(result.stderr),
    }
    log_path = _log_path(task)
    _append_json_line(log_path, log_entry)
    update_tool_state(
        _state_key(task),
        {
            "last_run_at": finished_at,
            "last_exit_code": result.returncode,
            "last_stdout": _truncate(result.stdout),
            "last_stderr": _truncate(result.stderr),
            "last_log_path": str(log_path),
            "last_status": "ok" if result.returncode == 0 else "error",
        },
    )
    if result.returncode != 0:
        raise PlatformError(
            f"Scheduled task '{task.task_id}' failed with exit code {result.returncode}.",
            code="E_SCHEDULE_JOB_FAILED",
            reason=task.task_id,
        )
    return log_entry


def parse_ghdp_args(raw: str) -> list[str]:
    parts = [part.strip() for part in shlex.split(raw or "", posix=True) if part.strip()]
    if not parts:
        raise PlatformError(
            "GHDP arguments cannot be empty.",
            code="E_SCHEDULE_ARGS_REQUIRED",
            reason="ghdp_args",
        )
    if parts[0] == "schedule":
        raise PlatformError(
            "Nested `ghdp schedule ...` commands are not supported as scheduled jobs.",
            code="E_SCHEDULE_ARGS_INVALID",
            reason="schedule_recursion",
        )
    return parts


def normalize_job_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    if not JOB_ID_RE.match(normalized):
        raise PlatformError(
            "Schedule task ids must be 2-63 chars of lowercase letters, numbers, or hyphens.",
            code="E_SCHEDULE_JOB_ID_INVALID",
            reason=value or "task_id",
        )
    return normalized


def current_platform_key() -> str:
    return _current_platform_name()


def preview_schedule_operation(
    *,
    scope: str,
    repo_root: Path | None = None,
    job_id: str | None = None,
    operation: str,
) -> dict[str, Any]:
    items = _preview_tasks(scope=scope, task_id=job_id, ensure_synced=True)
    readiness = _readiness_gate(items, operation=operation)
    actionable_statuses = {"apply": {"missing", "drifted"}, "repair": {"missing", "drifted"}, "remove": {"ok", "drifted", "missing"}}.get(
        operation,
        {"missing", "drifted"},
    )
    planned = [item for item in items if item["status"] in actionable_statuses and item["enabled"]]
    return {
        "operation": operation,
        "items": items,
        "planned": planned,
        "readiness": readiness,
    }


def build_schedule_apply_trust_summary(*, scope: str) -> dict[str, Any]:
    items = list_schedule_jobs(scope=scope)
    active_items = [item for item in items if item["enabled"] and item["status"] == "ok"]
    auto_update_item = next((item for item in active_items if item["task_id"] == AUTO_UPDATE_TASK_ID), None)
    return {
        "items": items,
        "active_items": active_items,
        "logs_path": str(LOGS_DIR),
        "auto_update_item": auto_update_item,
    }


def _emit_status(status_printer: StatusPrinter | None, message: str) -> None:
    if status_printer is None:
        return
    status_printer(message)


def scheduler_initialization_status(*, scope: str = "user") -> dict[str, Any]:
    try:
        items = list_schedule_jobs(scope=scope, ensure_synced=False)
    except PlatformError as exc:
        return {
            "supported": True,
            "initialized": False,
            "reason": "assets_missing",
            "detail": str(exc),
            "items": [],
            "required_items": [],
            "unhealthy_items": [],
        }

    relevant_items = [
        item
        for item in items
        if item["enabled"] and item["provider"] != "unsupported" and item["status"] != "platform_skipped"
    ]
    if not relevant_items:
        return {
            "supported": False,
            "initialized": False,
            "reason": "unsupported",
            "detail": "",
            "items": items,
            "required_items": [],
            "unhealthy_items": [],
        }

    required_items = [item for item in relevant_items if bool(item.get("required"))]
    target_items = required_items or relevant_items
    unhealthy_items = [item for item in target_items if str(item.get("status", "")).strip() != "ok"]
    return {
        "supported": True,
        "initialized": not unhealthy_items,
        "reason": "ok" if not unhealthy_items else "missing_or_drifted",
        "detail": "",
        "items": items,
        "required_items": target_items,
        "unhealthy_items": unhealthy_items,
    }


def ensure_post_install_scheduler_setup(
    *,
    scope: str = "user",
    source: str,
    status_printer: StatusPrinter | None = None,
) -> dict[str, Any]:
    _emit_status(status_printer, "Checking scheduler initialization...")
    status = scheduler_initialization_status(scope=scope)
    if not status["supported"]:
        return {
            "source": source,
            "action": "skipped",
            "reason": str(status["reason"]),
            "performed": False,
            "status": status,
            "planned": [],
            "applied": [],
        }

    if status["initialized"]:
        return {
            "source": source,
            "action": "skipped",
            "reason": "already_current",
            "performed": False,
            "status": status,
            "planned": [],
            "applied": [],
        }

    result = run_background_schedule_apply(scope=scope, status_printer=status_printer)
    planned = list(result["planned"])
    applied = list(result["applied"])
    return {
        "source": source,
        "action": "initialized" if source == "tools_install" else "completed",
        "reason": "updated" if planned else "already_current",
        "performed": bool(planned),
        "status": status,
        "planned": planned,
        "applied": applied,
        "result": result,
    }


def run_background_schedule_apply(
    *,
    scope: str = "user",
    job_id: str | None = None,
    status_printer: StatusPrinter | None = None,
) -> dict[str, Any]:
    _emit_status(status_printer, "Syncing scheduler job definitions...")
    _emit_status(status_printer, "Building scheduler apply preview...")
    preview = preview_schedule_operation(scope=scope, job_id=job_id, operation="apply")
    _emit_status(status_printer, f"Loaded {len(preview['items'])} scheduler job definition(s)...")
    blockers = list(preview["readiness"]["blockers"])
    if blockers:
        names = ", ".join(str(item["task_id"]) for item in blockers)
        reasons = ", ".join(str(item["reason"]) for item in blockers if item["reason"])
        raise PlatformError(
            f"Background schedule apply is blocked by unsupported phase-1 policy for: {names}.",
            code="E_SCHEDULE_POLICY_UNSUPPORTED",
            reason=reasons or names,
        )
    planned = [item for item in preview["items"] if item["action"] in {"apply", "repair"}]
    if planned:
        _emit_status(status_printer, f"Applying {len(planned)} scheduled task change(s)...")
        applied = apply_schedule_jobs(scope=scope, job_id=job_id)
    else:
        _emit_status(status_printer, "No scheduler task changes needed...")
        applied = []
    _emit_status(status_printer, "Finalizing scheduler setup...")
    return {
        "preview": preview,
        "planned": planned,
        "applied": applied,
        "summary": build_schedule_apply_trust_summary(scope=scope),
    }


def _resolve_tasks(*, scope: str, task_id: str | None, ensure_synced: bool) -> list[Any]:
    tasks, _ = _resolve_tasks_with_assets(scope=scope, task_id=task_id, ensure_synced=ensure_synced)
    return tasks


def _resolve_tasks_with_assets(*, scope: str, task_id: str | None, ensure_synced: bool) -> tuple[list[Any], dict[str, Any]]:
    _require_user_scope(scope)
    resolution = _scheduler_capability_resolution(ensure_synced=ensure_synced)
    tasks = list(scheduler_manifest.load_scheduler_tasks(capability_root=Path(str(resolution["target_path"]))))
    if task_id:
        normalized_task_id = normalize_job_id(task_id)
        tasks = [task for task in tasks if task.task_id == normalized_task_id]
    return tasks, resolution


def _preview_tasks(*, scope: str, task_id: str | None, ensure_synced: bool) -> list[dict[str, Any]]:
    tasks, asset_resolution = _resolve_tasks_with_assets(scope=scope, task_id=task_id, ensure_synced=ensure_synced)
    items: list[dict[str, Any]] = []
    current_platform = _current_platform_name()
    for task in tasks:
        provider = _provider_for_task(task)
        provider_module = _provider_module_by_name(provider)
        state = get_tool_state(_state_key(task))
        task_name = _task_name(task)
        wrapper_path = _wrapper_path(task, provider=provider)
        health_status = _health_status(task, state)
        if not task.enabled:
            status = "disabled"
            action = "none"
        elif current_platform not in task.platforms:
            status = "platform_skipped"
            action = "none"
        elif provider == "unsupported" or provider_module is None:
            status = "provider_unsupported"
            action = "none"
        else:
            policy_error = _policy_error(task, provider=provider)
            if policy_error:
                status = "policy_unsupported"
                action = "none"
            else:
                observation = provider_module.query_task(task_name)
                spec = _provider_spec(task, provider)
                if not observation.exists:
                    status = "missing"
                    action = "apply"
                elif not wrapper_path.exists():
                    status = "drifted"
                    action = "repair"
                elif not _wrapper_schema_current(wrapper_path):
                    status = "drifted"
                    action = "repair"
                elif provider_module.task_matches(spec, observation):
                    status = "ok"
                    action = "none"
                else:
                    status = "drifted"
                    action = "repair"

        items.append(
            {
                "task_id": task.task_id,
                "description": task.description,
                "required": task.required,
                "enabled": task.enabled,
                "interval_minutes": int(task.trigger["minutes"]),
                "random_delay_minutes": int(task.trigger["random_delay_minutes"]),
                "platforms": list(task.platforms),
                "ghdp_args": list(task.command["args"]),
                "provider": provider,
                "status": status,
                "action": action,
                "task_name": task_name,
                "wrapper_path": str(wrapper_path),
                "artifact_path": str(task.source_path),
                "asset_source_kind": str(asset_resolution.get("source_kind", "synced")),
                "asset_materialization_state": str(asset_resolution.get("materialization_state", "cached")),
                "asset_fallback_active": bool(asset_resolution.get("fallback_active")),
                "asset_source_explanation": str(asset_resolution.get("source_explanation", "")).strip(),
                "last_run_at": str(state.get("last_run_at", "")).strip(),
                "last_exit_code": state.get("last_exit_code"),
                "health_status": health_status,
                "policy_error": _policy_error(task, provider=provider) or "",
            }
        )
    return items


def _reconcile_tasks(*, scope: str, task_id: str | None, repair_only: bool) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in _preview_tasks(scope=scope, task_id=task_id, ensure_synced=True):
        if item["provider"] == "unsupported" or not item["enabled"]:
            continue
        if item["status"] == "policy_unsupported":
            raise PlatformError(
                f"Schedule task '{item['task_id']}' uses unsupported policy: {item['policy_error']}.",
                code="E_SCHEDULE_POLICY_UNSUPPORTED",
                reason=str(item["task_id"]),
            )
        if item["status"] not in {"missing", "drifted"}:
            continue
        task = _resolve_tasks(scope="user", task_id=str(item["task_id"]), ensure_synced=True)[0]
        provider = str(item["provider"])
        provider_module = _provider_module_by_name(provider)
        if provider_module is None:
            raise PlatformError(
                f"Scheduler provider '{provider}' is not supported on this host.",
                code="E_SCHEDULE_PROVIDER_UNSUPPORTED",
                reason=provider,
            )
        wrapper_path = _write_wrapper(task, provider=provider)
        task_name = _task_name(task)
        spec = _provider_spec(task, provider)
        provider_module.apply_task(spec)
        observation = provider_module.query_task(task_name)
        if not observation.exists or not provider_module.task_matches(spec, observation):
            raise PlatformError(
                f"Scheduled task '{task.task_id}' could not be verified after apply.",
                code="E_SCHEDULE_APPLY_VERIFY_FAILED",
                reason=task.task_id,
            )
        update_tool_state(
            _state_key(task),
            {
                "provider": provider,
                "task_name": task_name,
                "wrapper_path": str(wrapper_path),
                "artifact_path": str(task.source_path),
                "interval_minutes": int(task.trigger["minutes"]),
                "ghdp_args": list(task.command["args"]),
                "last_reconciled_at": _now_iso(),
                "registration_status": "applied",
            },
        )
        results.append(
            {
                "task_id": task.task_id,
                "task_name": task_name,
                "wrapper_path": str(wrapper_path),
                "interval_minutes": int(task.trigger["minutes"]),
            }
        )
    return results


def _provider_spec(task: Any, provider: str) -> object:
    if provider == scheduler_windows.WINDOWS_PROVIDER:
        return scheduler_windows.WindowsTaskSpec(
            task_name=_task_name(task),
            description=_task_description(task),
            interval_minutes=int(task.trigger["minutes"]),
            random_delay_minutes=int(task.trigger["random_delay_minutes"]),
            wrapper_path=_wrapper_path(task, provider=provider),
            allow_on_battery=bool(task.conditions["allow_on_battery"]),
            stop_on_battery=bool(task.conditions["stop_on_battery"]),
            require_network=bool(task.conditions["require_network"]),
            wake_machine=bool(task.conditions["wake_machine"]),
            start_when_available=bool(task.execution["catch_up_after_missed_run"]),
            execution_time_limit_minutes=int(task.execution["timeout_minutes"]),
            multiple_instances_policy=str(task.execution["overlap_policy"]),
            restart_count=int(task.execution["retry_on_failure"]["max_attempts"])
            if bool(task.execution["retry_on_failure"]["enabled"])
            else 0,
            restart_interval_minutes=int(task.execution["retry_on_failure"]["minutes"])
            if bool(task.execution["retry_on_failure"]["enabled"])
            else 0,
            hidden=bool(task.run_context["hidden"]),
        )
    if provider == scheduler_launchd.LAUNCHD_PROVIDER:
        return scheduler_launchd.LaunchdTaskSpec(
            task_name=_task_name(task),
            description=_task_description(task),
            interval_minutes=int(task.trigger["minutes"]),
            wrapper_path=_wrapper_path(task, provider=provider),
            stdout_path=_provider_stdout_path(task, provider=provider),
            stderr_path=_provider_stderr_path(task, provider=provider),
        )
    if provider == scheduler_cron.CRON_PROVIDER:
        return scheduler_cron.CronTaskSpec(
            task_name=_task_name(task),
            description=_task_description(task),
            interval_minutes=int(task.trigger["minutes"]),
            wrapper_path=_wrapper_path(task, provider=provider),
        )
    raise PlatformError(
        f"Unsupported scheduler provider '{provider}'.",
        code="E_SCHEDULE_PROVIDER_UNSUPPORTED",
        reason=provider,
    )


def _provider_module_by_name(provider: str) -> object | None:
    if provider == scheduler_windows.WINDOWS_PROVIDER:
        return scheduler_windows
    if provider == scheduler_launchd.LAUNCHD_PROVIDER:
        return scheduler_launchd
    if provider == scheduler_cron.CRON_PROVIDER:
        return scheduler_cron
    return None


def _require_user_scope(scope: str | None) -> None:
    normalized = str(scope or "user").strip().lower()
    if normalized not in SUPPORTED_SCOPES:
        raise PlatformError(
            "Scheduler tasks are user-scoped. Use scope 'user'.",
            code="E_SCHEDULE_SCOPE_INVALID",
            reason=scope or "scope",
        )


def _scheduler_capability_resolution(*, ensure_synced: bool) -> dict[str, Any]:
    if ensure_synced:
        return scheduler_assets.ensure_scheduler_assets_synced()
    capability_root = scheduler_assets.scheduler_assets_root()
    ready, reason = scheduler_manifest.installed_scheduler_assets_status()
    if ready:
        return {
            "target_path": str(capability_root.resolve()),
            "source_kind": "synced",
            "materialization_state": "cached",
            "fallback_active": False,
            "source_explanation": "using cached synced scheduler assets",
        }
    raise PlatformError(
        (
            f"Scheduler capability assets are not ready at '{capability_root}'. "
            "Run `ghdp sync run --capability background-scheduler` or repair the synced scheduler content."
        ),
        code="E_SCHEDULER_CAPABILITY_MISSING",
        reason=reason,
    )


def _provider_for_task(task: Any) -> str:
    current_platform = _current_platform_name()
    if current_platform == "windows" and current_platform in task.platforms and scheduler_windows.provider_supported():
        return scheduler_windows.WINDOWS_PROVIDER
    if current_platform == "darwin" and current_platform in task.platforms and scheduler_launchd.provider_supported():
        return scheduler_launchd.LAUNCHD_PROVIDER
    if current_platform == "linux" and current_platform in task.platforms and scheduler_cron.provider_supported():
        return scheduler_cron.CRON_PROVIDER
    return "unsupported"


def _current_platform_name() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("darwin"):
        return "darwin"
    if sys.platform.startswith("linux"):
        return "linux"
    return "unknown"


def _policy_error(task: Any, *, provider: str) -> str | None:
    if str(task.run_context["mode"]) != "user_session":
        return "run_context.mode"
    if bool(task.run_context["elevated"]):
        return "run_context.elevated"
    if bool(task.conditions["idle_only"]):
        return "conditions.idle_only"
    if provider == scheduler_cron.CRON_PROVIDER and int(task.trigger["minutes"]) > 1440:
        return "trigger.minutes"
    return None


def _state_key(task: Any) -> str:
    return f"schedule:user:{CAPABILITY_ID}:{task.task_id}"


def _task_description(task: Any) -> str:
    metadata = f"managed_by=ghdp capability={CAPABILITY_ID} task_id={task.task_id}"
    return f"{task.description} [{metadata}]"


def _task_name(task: Any) -> str:
    return f"GHDP-{CAPABILITY_ID}-{task.task_id}"


def _wrapper_path(task: Any, *, provider: str | None = None) -> Path:
    WRAPPERS_DIR.mkdir(parents=True, exist_ok=True)
    resolved_provider = provider or _provider_for_task(task)
    extension = ".ps1" if resolved_provider == scheduler_windows.WINDOWS_PROVIDER else ".sh"
    return WRAPPERS_DIR / f"{_task_name(task)}{extension}"


def _provider_stdout_path(task: Any, *, provider: str) -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR / f"{_task_name(task)}.{provider}.stdout.log"


def _provider_stderr_path(task: Any, *, provider: str) -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR / f"{_task_name(task)}.{provider}.stderr.log"


def _log_path(task: Any) -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR / f"{_task_name(task)}.jsonl"


def _write_wrapper(task: Any, *, provider: str) -> Path:
    wrapper_path = _wrapper_path(task, provider=provider)
    ghdp_executable = _resolve_current_ghdp_executable()
    if provider == scheduler_windows.WINDOWS_PROVIDER:
        pre_run_delay_minutes = int(task.execution.get("pre_run_delay_minutes", 0) or 0)
        lines = [
            f"# {WRAPPER_SCHEMA_MARKER}",
            '$ErrorActionPreference = "Stop"',
            f'$ghdpCommand = {_ps_literal(ghdp_executable)}',
            '$ghdpKnown = Test-Path $ghdpCommand',
            '$resolvedGhdp = ""',
            'if (-not $ghdpKnown) { $resolvedGhdp = (Get-Command "ghdp" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source) }',
            'if ($resolvedGhdp) { $ghdpCommand = $resolvedGhdp }',
            '$runner = $ghdpCommand',
            '$cleanupRunner = $false',
            'if (Test-Path $ghdpCommand) {',
            '  $runner = Join-Path ([IO.Path]::GetTempPath()) ("ghdp-scheduler-" + [guid]::NewGuid().ToString() + [IO.Path]::GetExtension($ghdpCommand))',
            '  Copy-Item -LiteralPath $ghdpCommand -Destination $runner -Force',
            '  $cleanupRunner = $true',
            '}',
            "try {",
        ]
        if pre_run_delay_minutes > 0:
            lines.append(f"  Start-Sleep -Seconds {pre_run_delay_minutes * 60}")
        lines.extend(
            [
                (
                    "  $process = Start-Process -FilePath $runner "
                    f"-ArgumentList @('--non-interactive', 'schedule', 'run-job', '--task-id', {_ps_literal(task.task_id)}) "
                    "-WindowStyle Hidden -Wait -PassThru"
                ),
                "  exit $process.ExitCode",
                "} finally {",
                '  if ($cleanupRunner -and (Test-Path $runner)) { Remove-Item -LiteralPath $runner -Force -ErrorAction SilentlyContinue }',
                "}",
            ]
        )
        wrapper_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return wrapper_path

    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "#!/bin/sh",
        f"# {WRAPPER_SCHEMA_MARKER}",
        "set -eu",
        f'PATH="{_scheduler_runtime_path_prefix()}:${{PATH:-}}"',
        "export PATH",
        f"ghdpCommand={_sh_literal(ghdp_executable)}",
        'if [ ! -x "$ghdpCommand" ]; then',
        '  ghdpCommand="$(command -v ghdp 2>/dev/null || true)"',
        "fi",
        'if [ -z "${ghdpCommand:-}" ]; then',
        '  echo "ghdp executable not found" >&2',
        "  exit 127",
        "fi",
    ]
    random_delay_minutes = int(task.trigger["random_delay_minutes"])
    if random_delay_minutes > 0:
        lines.extend(
            [
                f"maxDelayMinutes={random_delay_minutes}",
                'rawDelay="$(od -An -N2 -tu2 /dev/urandom 2>/dev/null | tr -d \" \")"',
                '[ -n "${rawDelay:-}" ] || rawDelay=0',
                'sleep "$(( (rawDelay % (maxDelayMinutes + 1)) * 60 ))"',
            ]
        )
    pre_run_delay_minutes = int(task.execution.get("pre_run_delay_minutes", 0) or 0)
    if pre_run_delay_minutes > 0:
        lines.append(f'sleep "{pre_run_delay_minutes * 60}"')

    overlap_policy = str(task.execution["overlap_policy"]).strip().lower()
    if overlap_policy in {"skip", "queue"}:
        lock_dir = LOCKS_DIR / _task_name(task)
        lines.append(f"lockDir={_sh_literal(str(lock_dir))}")
        if overlap_policy == "queue":
            lines.extend(
                [
                    'while ! mkdir "$lockDir" 2>/dev/null; do',
                    "  sleep 30",
                    "done",
                ]
            )
        else:
            lines.extend(
                [
                    'if ! mkdir "$lockDir" 2>/dev/null; then',
                    "  exit 0",
                    "fi",
                ]
            )
        lines.append('trap \'rmdir "$lockDir" 2>/dev/null || true\' EXIT HUP INT TERM')

    retry_enabled = bool(task.execution["retry_on_failure"]["enabled"])
    max_attempts = int(task.execution["retry_on_failure"]["max_attempts"]) if retry_enabled else 1
    retry_interval_minutes = int(task.execution["retry_on_failure"]["minutes"]) if retry_enabled else 0
    lines.extend(
        [
            "attempt=1",
            f"maxAttempts={max_attempts}",
            f"retryIntervalSeconds={retry_interval_minutes * 60}",
            "while :; do",
            f"  \"$ghdpCommand\" --non-interactive schedule run-job --task-id {_sh_literal(task.task_id)}",
            "  rc=$?",
            '  if [ "$rc" -eq 0 ] || [ "$attempt" -ge "$maxAttempts" ]; then',
            '    exit "$rc"',
            "  fi",
            '  sleep "$retryIntervalSeconds"',
            "  attempt=$((attempt + 1))",
            "done",
        ]
    )
    wrapper_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    wrapper_path.chmod(0o755)
    return wrapper_path


def _scheduler_runtime_path_prefix() -> str:
    home_local_bin = str(Path.home() / ".local" / "bin")
    ordered_entries = []
    seen: set[str] = set()
    for raw in POSIX_SCHEDULER_PATH_ENTRIES:
        candidate = home_local_bin if raw == "__HOME_LOCAL_BIN__" else str(raw).strip()
        if not candidate:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        ordered_entries.append(candidate)
    return ":".join(ordered_entries)


def _wrapper_schema_current(wrapper_path: Path) -> bool:
    try:
        with wrapper_path.open("r", encoding="utf-8", errors="replace") as handle:
            for _ in range(4):
                line = handle.readline()
                if not line:
                    break
                if WRAPPER_SCHEMA_MARKER in line:
                    return True
    except OSError:
        return False
    return False


def _resolve_current_ghdp_executable() -> str:
    candidate = Path(sys.argv[0]).resolve()
    candidate_name = candidate.name.lower()
    candidate_stem = candidate.stem.lower()
    if candidate.exists() and (
        candidate_stem == "ghdp"
        or candidate_stem.startswith("ghdp")
        or candidate_name == "ghdp"
        or candidate_name.startswith("ghdp")
    ):
        return str(candidate)
    explicit = shutil.which(str(sys.argv[0]))
    if explicit:
        explicit_path = Path(explicit).resolve()
        if _is_ghdp_executable_name(explicit_path):
            return str(explicit_path)
    exact = shutil.which("ghdp")
    if exact:
        exact_path = Path(exact).resolve()
        if _is_ghdp_executable_name(exact_path):
            return str(exact_path)
    discovered = _discover_ghdp_executable_from_path()
    if discovered is not None:
        return str(discovered)
    return "ghdp"


def _is_ghdp_executable_name(path: Path) -> bool:
    stem = path.stem.lower()
    name = path.name.lower()
    return stem == "ghdp" or stem.startswith("ghdp") or name == "ghdp" or name.startswith("ghdp")


def _discover_ghdp_executable_from_path() -> Path | None:
    candidates: list[Path] = []
    patterns = ["ghdp*.exe", "ghdp*"] if sys.platform.startswith("win") else ["ghdp*"]
    for raw_entry in (os.environ.get("PATH") or "").split(os.pathsep):
        entry = raw_entry.strip().strip('"')
        if not entry:
            continue
        directory = Path(entry)
        if not directory.exists() or not directory.is_dir():
            continue
        for pattern in patterns:
            for candidate in sorted(directory.glob(pattern)):
                if candidate.is_file() and _is_ghdp_executable_name(candidate):
                    candidates.append(candidate.resolve())
    if not candidates:
        return None
    unique_candidates: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key not in seen:
            seen.add(key)
            unique_candidates.append(candidate)
    for candidate in unique_candidates:
        if candidate.stem.lower() == "ghdp":
            return candidate
    return unique_candidates[0]


def _ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sh_literal(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _append_json_line(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _truncate(value: str, limit: int = 4000) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _health_status(task: Any, state: dict[str, Any]) -> str:
    last_run_raw = str(state.get("last_run_at", "")).strip()
    if not last_run_raw:
        return "never_run"
    try:
        last_run = datetime.fromisoformat(last_run_raw.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    interval_minutes = int(task.trigger["minutes"])
    threshold = timedelta(minutes=max(interval_minutes * 2, interval_minutes + 30))
    now = datetime.now(timezone.utc)
    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=timezone.utc)
    return "fresh" if now - last_run <= threshold else "stale"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _readiness_gate(items: list[dict[str, Any]], *, operation: str) -> dict[str, list[dict[str, str]]]:
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    for item in items:
        task_id = str(item["task_id"])
        status = str(item["status"])
        if status == "policy_unsupported":
            blockers.append({"task_id": task_id, "reason": str(item["policy_error"] or "unsupported_policy")})
        elif operation in {"apply", "repair"} and status == "provider_unsupported":
            blockers.append({"task_id": task_id, "reason": "provider_unsupported"})
        elif status in {"platform_skipped", "disabled"}:
            warnings.append({"task_id": task_id, "reason": status})
    return {"blockers": blockers, "warnings": warnings}
