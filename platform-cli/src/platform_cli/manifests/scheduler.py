from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from platform_cli.core.errors import PlatformError


SCHEDULER_CAPABILITY_INSTALL_ROOT_REL_PATH = Path(".ghdp") / "capabilities" / "scheduler"
SCHEDULER_CAPABILITY_FILE_NAME = "capability.json"
SCHEDULER_DEFAULTS_FILE_NAME = "defaults.json"
SCHEDULER_TASKS_FILE_NAME = "tasks.json"
SCHEDULER_CAPABILITY_ID = "background-scheduler"
SCHEDULER_CAPABILITY_SCHEMA_VERSION = "1.0"
SCHEDULER_TASK_SCHEMA_VERSION = "1.0"
SCHEDULER_SUPPORTED_PLATFORMS = ("windows", "darwin", "linux")
SCHEDULER_TASK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
SCHEDULER_PACKAGED_BOOTSTRAP_REL_PATH = Path("resources") / "scheduler" / "bootstrap"


@dataclass(frozen=True)
class ScheduleTaskDefinition:
    task_id: str
    description: str
    enabled: bool
    required: bool
    platforms: tuple[str, ...]
    command: dict[str, Any]
    trigger: dict[str, Any]
    execution: dict[str, Any]
    conditions: dict[str, Any]
    run_context: dict[str, Any]
    source_path: Path


def load_scheduler_tasks(task_id: str | None = None, *, capability_root: Path | None = None) -> list[ScheduleTaskDefinition]:
    capability_root = capability_root or installed_capability_root()
    capability = _load_json(capability_root / SCHEDULER_CAPABILITY_FILE_NAME)
    defaults = _load_json(capability_root / SCHEDULER_DEFAULTS_FILE_NAME)
    capability_meta = _validate_capability(capability, capability_root=capability_root)
    default_values = _validate_defaults(defaults, capability_root=capability_root)
    task_sources = _load_task_sources(capability_root=capability_root, capability_meta=capability_meta)
    task_sources = _merge_required_packaged_task_sources(task_sources, capability_root=capability_root)

    normalized_task_id = normalize_task_id(task_id) if task_id else None
    definitions: list[ScheduleTaskDefinition] = []
    for raw_task, source_path in task_sources:
        merged = _deep_merge(default_values, raw_task)
        definition = _validate_task_definition(merged, source_path=source_path)
        if normalized_task_id is not None and definition.task_id != normalized_task_id:
            continue
        definitions.append(definition)

    definitions.sort(key=lambda item: item.task_id)
    return definitions


def scheduler_assets_status(*, capability_root: Path | None = None) -> tuple[bool, str]:
    capability_root = capability_root or installed_capability_root()
    if not capability_root.exists() or not capability_root.is_dir():
        return False, "missing_install_root"
    try:
        capability = _load_json(capability_root / SCHEDULER_CAPABILITY_FILE_NAME)
        defaults = _load_json(capability_root / SCHEDULER_DEFAULTS_FILE_NAME)
        capability_meta = _validate_capability(capability, capability_root=capability_root)
        _validate_defaults(defaults, capability_root=capability_root)
        _load_task_sources(capability_root=capability_root, capability_meta=capability_meta)
    except PlatformError as exc:
        return False, str(exc.code or "invalid")
    return True, "ok"


def installed_scheduler_assets_status() -> tuple[bool, str]:
    return scheduler_assets_status(capability_root=installed_capability_root())


def installed_capability_root() -> Path:
    return Path.home() / SCHEDULER_CAPABILITY_INSTALL_ROOT_REL_PATH


def packaged_bootstrap_root() -> Path:
    return Path(__file__).resolve().parents[1] / SCHEDULER_PACKAGED_BOOTSTRAP_REL_PATH


def normalize_task_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    if not SCHEDULER_TASK_ID_RE.match(normalized):
        raise PlatformError(
            "Scheduler task ids must be 2-63 chars of lowercase letters, numbers, or hyphens.",
            code="E_SCHEDULER_TASK_ID_INVALID",
            reason=value or "task_id",
        )
    return normalized


def _validate_task_definition(
    payload: dict[str, Any],
    *,
    source_path: Path,
) -> ScheduleTaskDefinition:
    schema_version = str(payload.get("schema_version", SCHEDULER_TASK_SCHEMA_VERSION)).strip() or SCHEDULER_TASK_SCHEMA_VERSION
    if schema_version != SCHEDULER_TASK_SCHEMA_VERSION:
        raise PlatformError(
            f"Scheduler task '{source_path}' must declare schema_version='{SCHEDULER_TASK_SCHEMA_VERSION}'.",
            code="E_SCHEDULER_CAPABILITY_INVALID",
            reason=str(source_path),
        )
    task_id = normalize_task_id(str(payload.get("id", "")).strip())
    description = str(payload.get("description", "")).strip() or f"Scheduler task '{task_id}'"
    enabled = _require_bool(payload, "enabled", default=True, ctx=task_id)
    required = _require_bool(payload, "required", default=False, ctx=task_id)
    platforms = _normalize_platforms(payload.get("platforms", list(SCHEDULER_SUPPORTED_PLATFORMS)))

    command = payload.get("command", {})
    if not isinstance(command, dict):
        raise PlatformError(
            f"Scheduler task '{task_id}' command must be an object.",
            code="E_SCHEDULER_COMMAND_INVALID",
            reason=task_id,
        )
    command_type = str(command.get("type", "")).strip().lower()
    if command_type != "ghdp":
        raise PlatformError(
            f"Scheduler task '{task_id}' must declare command.type='ghdp'.",
            code="E_SCHEDULER_COMMAND_INVALID",
            reason=task_id,
        )
    command_args = command.get("args", [])
    if not isinstance(command_args, list) or not command_args or not all(isinstance(item, str) and item.strip() for item in command_args):
        raise PlatformError(
            f"Scheduler task '{task_id}' must declare a non-empty command.args string array.",
            code="E_SCHEDULER_COMMAND_INVALID",
            reason=task_id,
        )
    normalized_args = [str(item).strip() for item in command_args]
    if normalized_args[0] == "schedule":
        raise PlatformError(
            f"Scheduler task '{task_id}' cannot recursively invoke `ghdp schedule ...`.",
            code="E_SCHEDULER_COMMAND_INVALID",
            reason=task_id,
        )

    trigger = _validate_trigger(payload.get("trigger", {}), task_id=task_id)
    execution = _validate_execution(payload.get("execution", {}), task_id=task_id)
    conditions = _validate_conditions(payload.get("conditions", {}), task_id=task_id)
    run_context = _validate_run_context(payload.get("run_context", {}), task_id=task_id)

    return ScheduleTaskDefinition(
        task_id=task_id,
        description=description,
        enabled=enabled,
        required=required,
        platforms=platforms,
        command={"type": command_type, "args": normalized_args},
        trigger=trigger,
        execution=execution,
        conditions=conditions,
        run_context=run_context,
        source_path=source_path,
    )


def _validate_capability(payload: object, *, capability_root: Path) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PlatformError(
            f"Scheduler capability '{capability_root}' must contain a JSON object.",
            code="E_SCHEDULER_CAPABILITY_INVALID",
            reason=str(capability_root),
        )
    schema_version = str(payload.get("schema_version", "")).strip()
    capability_id = str(payload.get("capability_id", "")).strip()
    if not schema_version or not capability_id:
        raise PlatformError(
            f"Scheduler capability '{capability_root}' is missing required metadata.",
            code="E_SCHEDULER_CAPABILITY_INVALID",
            reason=str(capability_root),
        )
    if capability_id != SCHEDULER_CAPABILITY_ID:
        raise PlatformError(
            f"Scheduler capability '{capability_root}' must declare capability_id='{SCHEDULER_CAPABILITY_ID}'.",
            code="E_SCHEDULER_CAPABILITY_INVALID",
            reason=str(capability_root),
        )
    if schema_version != SCHEDULER_CAPABILITY_SCHEMA_VERSION:
        raise PlatformError(
            f"Scheduler capability '{capability_root}' must declare schema_version='{SCHEDULER_CAPABILITY_SCHEMA_VERSION}'.",
            code="E_SCHEDULER_CAPABILITY_INVALID",
            reason=str(capability_root),
        )
    return payload


def _load_task_sources(*, capability_root: Path, capability_meta: dict[str, Any]) -> list[tuple[dict[str, Any], Path]]:
    task_file_name = str(capability_meta.get("tasks_file", SCHEDULER_TASKS_FILE_NAME)).strip() or SCHEDULER_TASKS_FILE_NAME
    task_file = capability_root / task_file_name
    if not task_file.exists():
        raise PlatformError(
            (
                "Scheduler capability is missing its canonical tasks file. "
                f"Expected '{task_file}'. Re-sync the scheduler capability to restore it."
            ),
            code="E_SCHEDULER_CAPABILITY_INVALID",
            reason=str(capability_root),
        )
    return [(payload, task_file) for payload in _load_tasks_file(task_file)]


def _merge_required_packaged_task_sources(
    task_sources: list[tuple[dict[str, Any], Path]],
    *,
    capability_root: Path,
) -> list[tuple[dict[str, Any], Path]]:
    if capability_root.resolve() == packaged_bootstrap_root().resolve():
        return task_sources

    existing_ids = {
        normalize_task_id(str(payload.get("id", "")).strip())
        for payload, _source_path in task_sources
        if str(payload.get("id", "")).strip()
    }
    packaged_root = packaged_bootstrap_root()
    packaged_capability = _load_json(packaged_root / SCHEDULER_CAPABILITY_FILE_NAME)
    packaged_defaults = _load_json(packaged_root / SCHEDULER_DEFAULTS_FILE_NAME)
    packaged_meta = _validate_capability(packaged_capability, capability_root=packaged_root)
    packaged_default_values = _validate_defaults(packaged_defaults, capability_root=packaged_root)
    packaged_sources = _load_task_sources(capability_root=packaged_root, capability_meta=packaged_meta)

    merged = list(task_sources)
    for raw_task, source_path in packaged_sources:
        merged_task = _deep_merge(packaged_default_values, raw_task)
        task_id = normalize_task_id(str(merged_task.get("id", "")).strip())
        if task_id in existing_ids:
            continue
        if not bool(merged_task.get("required", False)):
            continue
        merged.append((raw_task, source_path))
    return merged


def _load_tasks_file(path: Path) -> list[dict[str, Any]]:
    payload = _load_json(path)
    schema_version = str(payload.get("schema_version", "")).strip()
    capability_id = str(payload.get("capability_id", "")).strip()
    if schema_version != SCHEDULER_CAPABILITY_SCHEMA_VERSION:
        raise PlatformError(
            f"Scheduler tasks file '{path}' must declare schema_version='{SCHEDULER_CAPABILITY_SCHEMA_VERSION}'.",
            code="E_SCHEDULER_CAPABILITY_INVALID",
            reason=str(path),
        )
    if capability_id != SCHEDULER_CAPABILITY_ID:
        raise PlatformError(
            f"Scheduler tasks file '{path}' must declare capability_id='{SCHEDULER_CAPABILITY_ID}'.",
            code="E_SCHEDULER_CAPABILITY_INVALID",
            reason=str(path),
        )
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        raise PlatformError(
            f"Scheduler tasks file '{path}' must contain a tasks array.",
            code="E_SCHEDULER_CAPABILITY_INVALID",
            reason=str(path),
        )
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(tasks):
        if not isinstance(item, dict):
            raise PlatformError(
                f"Scheduler tasks file '{path}' task #{index + 1} must be an object.",
                code="E_SCHEDULER_CAPABILITY_INVALID",
                reason=str(path),
            )
        normalized.append(dict(item))
    return normalized


def _validate_defaults(payload: object, *, capability_root: Path) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PlatformError(
            f"Scheduler defaults '{capability_root}' must contain a JSON object.",
            code="E_SCHEDULER_CAPABILITY_INVALID",
            reason=str(capability_root),
        )
    defaults = payload.get("defaults")
    if not isinstance(defaults, dict):
        raise PlatformError(
            f"Scheduler defaults '{capability_root}' must contain a defaults object.",
            code="E_SCHEDULER_CAPABILITY_INVALID",
            reason=str(capability_root),
        )
    schema_version = str(payload.get("schema_version", "")).strip()
    if schema_version != SCHEDULER_CAPABILITY_SCHEMA_VERSION:
        raise PlatformError(
            f"Scheduler defaults '{capability_root}' must declare schema_version='{SCHEDULER_CAPABILITY_SCHEMA_VERSION}'.",
            code="E_SCHEDULER_CAPABILITY_INVALID",
            reason=str(capability_root),
        )
    capability_id = str(payload.get("capability_id", "")).strip()
    if capability_id != SCHEDULER_CAPABILITY_ID:
        raise PlatformError(
            f"Scheduler defaults '{capability_root}' must declare capability_id='{SCHEDULER_CAPABILITY_ID}'.",
            code="E_SCHEDULER_CAPABILITY_INVALID",
            reason=str(capability_root),
        )

    normalized = dict(defaults)
    normalized.setdefault("enabled", True)
    normalized.setdefault("required", False)
    normalized["platforms"] = _normalize_platforms(normalized.get("platforms", list(SCHEDULER_SUPPORTED_PLATFORMS)))

    trigger = normalized.get("trigger", {})
    if not isinstance(trigger, dict):
        raise PlatformError(
            f"Scheduler defaults '{capability_root}' trigger must be an object.",
            code="E_SCHEDULER_TRIGGER_INVALID",
            reason=str(capability_root),
        )
    trigger = dict(trigger)
    trigger.setdefault("type", "interval")
    trigger.setdefault("minutes", 60)
    trigger.setdefault("random_delay_minutes", 5)
    normalized["trigger"] = trigger

    execution = normalized.get("execution", {})
    if not isinstance(execution, dict):
        raise PlatformError(
            f"Scheduler defaults '{capability_root}' execution must be an object.",
            code="E_SCHEDULER_POLICY_INVALID",
            reason=str(capability_root),
        )
    execution = dict(execution)
    execution.setdefault("timeout_minutes", 15)
    execution.setdefault("pre_run_delay_minutes", 0)
    execution.setdefault("overlap_policy", "skip")
    execution.setdefault("catch_up_after_missed_run", True)
    retry = execution.get("retry_on_failure", {})
    if not isinstance(retry, dict):
        raise PlatformError(
            f"Scheduler defaults '{capability_root}' execution.retry_on_failure must be an object.",
            code="E_SCHEDULER_POLICY_INVALID",
            reason=str(capability_root),
        )
    retry = dict(retry)
    retry.setdefault("enabled", True)
    retry.setdefault("minutes", 15)
    retry.setdefault("max_attempts", 3)
    execution["retry_on_failure"] = retry
    normalized["execution"] = execution

    conditions = normalized.get("conditions", {})
    if not isinstance(conditions, dict):
        raise PlatformError(
            f"Scheduler defaults '{capability_root}' conditions must be an object.",
            code="E_SCHEDULER_POLICY_INVALID",
            reason=str(capability_root),
        )
    conditions = dict(conditions)
    conditions.setdefault("require_network", False)
    conditions.setdefault("allow_on_battery", True)
    conditions.setdefault("stop_on_battery", False)
    conditions.setdefault("idle_only", False)
    conditions.setdefault("wake_machine", False)
    normalized["conditions"] = conditions

    run_context = normalized.get("run_context", {})
    if not isinstance(run_context, dict):
        raise PlatformError(
            f"Scheduler defaults '{capability_root}' run_context must be an object.",
            code="E_SCHEDULER_POLICY_INVALID",
            reason=str(capability_root),
        )
    run_context = dict(run_context)
    run_context.setdefault("mode", "user_session")
    run_context.setdefault("elevated", False)
    run_context.setdefault("hidden", False)
    normalized["run_context"] = run_context

    return normalized


def _validate_trigger(payload: object, *, task_id: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PlatformError(
            f"Scheduler task '{task_id}' trigger must be an object.",
            code="E_SCHEDULER_TRIGGER_INVALID",
            reason=task_id,
        )
    trigger_type = str(payload.get("type", "interval")).strip().lower() or "interval"
    if trigger_type != "interval":
        raise PlatformError(
            f"Scheduler task '{task_id}' only supports trigger.type='interval' in phase 1.",
            code="E_SCHEDULER_TRIGGER_INVALID",
            reason=task_id,
        )
    minutes = int(payload.get("minutes", 0) or 0)
    random_delay_minutes = int(payload.get("random_delay_minutes", 0) or 0)
    if minutes < 1:
        raise PlatformError(
            f"Scheduler task '{task_id}' trigger.minutes must be >= 1.",
            code="E_SCHEDULER_TRIGGER_INVALID",
            reason=task_id,
        )
    if random_delay_minutes < 0:
        raise PlatformError(
            f"Scheduler task '{task_id}' trigger.random_delay_minutes must be >= 0.",
            code="E_SCHEDULER_TRIGGER_INVALID",
            reason=task_id,
        )
    return {
        "type": trigger_type,
        "minutes": minutes,
        "random_delay_minutes": random_delay_minutes,
    }


def _validate_execution(payload: object, *, task_id: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PlatformError(
            f"Scheduler task '{task_id}' execution must be an object.",
            code="E_SCHEDULER_POLICY_INVALID",
            reason=task_id,
        )
    timeout_minutes = int(payload.get("timeout_minutes", 0) or 0)
    if timeout_minutes < 1:
        raise PlatformError(
            f"Scheduler task '{task_id}' execution.timeout_minutes must be >= 1.",
            code="E_SCHEDULER_POLICY_INVALID",
            reason=task_id,
        )
    pre_run_delay_minutes = int(payload.get("pre_run_delay_minutes", 0) or 0)
    if pre_run_delay_minutes < 0:
        raise PlatformError(
            f"Scheduler task '{task_id}' execution.pre_run_delay_minutes must be zero or greater.",
            code="E_SCHEDULER_POLICY_INVALID",
            reason=task_id,
        )
    overlap_policy = str(payload.get("overlap_policy", "skip")).strip().lower() or "skip"
    if overlap_policy not in {"skip", "queue", "parallel"}:
        raise PlatformError(
            f"Scheduler task '{task_id}' execution.overlap_policy must be one of skip, queue, parallel.",
            code="E_SCHEDULER_POLICY_INVALID",
            reason=task_id,
        )
    retry = payload.get("retry_on_failure", {})
    if not isinstance(retry, dict):
        raise PlatformError(
            f"Scheduler task '{task_id}' execution.retry_on_failure must be an object.",
            code="E_SCHEDULER_POLICY_INVALID",
            reason=task_id,
        )
    retry_minutes = int(retry.get("minutes", 0) or 0)
    retry_attempts = int(retry.get("max_attempts", 0) or 0)
    if retry_minutes < 0 or retry_attempts < 0:
        raise PlatformError(
            f"Scheduler task '{task_id}' execution.retry_on_failure values must be zero or greater.",
            code="E_SCHEDULER_POLICY_INVALID",
            reason=task_id,
        )
    return {
        "timeout_minutes": timeout_minutes,
        "pre_run_delay_minutes": pre_run_delay_minutes,
        "overlap_policy": overlap_policy,
        "catch_up_after_missed_run": _require_bool(payload, "catch_up_after_missed_run", default=True, ctx=task_id),
        "retry_on_failure": {
            "enabled": _require_bool(retry, "enabled", default=True, ctx=task_id),
            "minutes": retry_minutes,
            "max_attempts": retry_attempts,
        },
    }


def _validate_conditions(payload: object, *, task_id: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PlatformError(
            f"Scheduler task '{task_id}' conditions must be an object.",
            code="E_SCHEDULER_POLICY_INVALID",
            reason=task_id,
        )
    return {
        "require_network": _require_bool(payload, "require_network", default=False, ctx=task_id),
        "allow_on_battery": _require_bool(payload, "allow_on_battery", default=True, ctx=task_id),
        "stop_on_battery": _require_bool(payload, "stop_on_battery", default=False, ctx=task_id),
        "idle_only": _require_bool(payload, "idle_only", default=False, ctx=task_id),
        "wake_machine": _require_bool(payload, "wake_machine", default=False, ctx=task_id),
    }


def _validate_run_context(payload: object, *, task_id: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PlatformError(
            f"Scheduler task '{task_id}' run_context must be an object.",
            code="E_SCHEDULER_POLICY_INVALID",
            reason=task_id,
        )
    mode = str(payload.get("mode", "user_session")).strip().lower() or "user_session"
    if mode not in {"user_session", "service", "headless"}:
        raise PlatformError(
            f"Scheduler task '{task_id}' run_context.mode must be one of: user_session, service, headless.",
            code="E_SCHEDULER_POLICY_INVALID",
            reason=task_id,
        )
    return {
        "mode": mode,
        "elevated": _require_bool(payload, "elevated", default=False, ctx=task_id),
        "hidden": _require_bool(payload, "hidden", default=False, ctx=task_id),
    }


def _normalize_platforms(payload: object) -> tuple[str, ...]:
    if payload is None:
        return SCHEDULER_SUPPORTED_PLATFORMS
    if isinstance(payload, (str, bytes)) or not isinstance(payload, (list, tuple)):
        raise PlatformError(
            "Scheduler platforms must be a list.",
            code="E_SCHEDULER_PLATFORM_INVALID",
            reason="platforms",
        )
    normalized: list[str] = []
    for item in payload:
        platform = str(item).strip().lower()
        if not platform:
            continue
        if platform not in SCHEDULER_SUPPORTED_PLATFORMS:
            raise PlatformError(
                f"Unsupported scheduler platform '{platform}'.",
                code="E_SCHEDULER_PLATFORM_INVALID",
                reason=platform,
            )
        if platform not in normalized:
            normalized.append(platform)
    if not normalized:
        return SCHEDULER_SUPPORTED_PLATFORMS
    return tuple(normalized)


def _require_bool(payload: dict[str, Any], key: str, *, default: bool, ctx: str) -> bool:
    if key not in payload:
        return default
    value = payload[key]
    if isinstance(value, bool):
        return value
    raise PlatformError(
        f"Scheduler task '{ctx}' field '{key}' must be a boolean.",
        code="E_SCHEDULER_POLICY_INVALID",
        reason=ctx,
    )


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise PlatformError(
            f"Scheduler capability file '{path}' was not found. Sync the scheduler capability assets first.",
            code="E_SCHEDULER_CAPABILITY_MISSING",
            reason=str(path),
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as e:
        raise PlatformError(
            f"Failed to parse scheduler capability file '{path}': {e}",
            code="E_SCHEDULER_CAPABILITY_INVALID",
            reason=str(path),
        )
    if not isinstance(payload, dict):
        raise PlatformError(
            f"Scheduler capability file '{path}' must contain a JSON object.",
            code="E_SCHEDULER_CAPABILITY_INVALID",
            reason=str(path),
        )
    return payload


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(dict(result[key]), dict(value))
        else:
            result[key] = value
    return result
