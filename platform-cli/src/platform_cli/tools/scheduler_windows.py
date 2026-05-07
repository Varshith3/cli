from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from platform_cli.core.errors import PlatformError
from platform_cli.exec.runner import run_cmd


WINDOWS_PROVIDER = "windows_task_scheduler"
WINDOWS_TRIGGER_DURATION_DAYS = 3650
_TASK_NS = {"task": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
_TASK_COMMAND_LIMIT = 261
_OVERLAP_TO_WINDOWS = {
    "skip": "IgnoreNew",
    "queue": "Queue",
    "parallel": "Parallel",
}


@dataclass(frozen=True)
class WindowsTaskSpec:
    task_name: str
    description: str
    interval_minutes: int
    random_delay_minutes: int
    wrapper_path: Path
    allow_on_battery: bool
    stop_on_battery: bool
    require_network: bool
    wake_machine: bool
    start_when_available: bool
    execution_time_limit_minutes: int
    multiple_instances_policy: str
    restart_count: int
    restart_interval_minutes: int
    hidden: bool


@dataclass(frozen=True)
class WindowsTaskObservation:
    exists: bool
    task_name: str
    description: str = ""
    interval_minutes: int | None = None
    random_delay_minutes: int | None = None
    command: str = ""
    arguments: str = ""
    enabled: bool = True
    allow_on_battery: bool = False
    stop_on_battery: bool = True
    require_network: bool = False
    wake_machine: bool = False
    start_when_available: bool = False
    execution_time_limit_minutes: int | None = None
    multiple_instances_policy: str = ""
    restart_count: int = 0
    restart_interval_minutes: int = 0
    hidden: bool = False


def provider_supported() -> bool:
    return sys.platform.startswith("win")


def build_task_command(wrapper_path: Path) -> tuple[str, str]:
    command = "powershell.exe"
    arguments = f'-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{wrapper_path}"'
    full_command = f"{command} {arguments}"
    if len(full_command) > _TASK_COMMAND_LIMIT:
        raise PlatformError(
            f"Scheduled task command for '{wrapper_path.name}' exceeds the Windows Task Scheduler limit.",
            code="E_SCHEDULE_TASK_COMMAND_TOO_LONG",
            reason=wrapper_path.name,
        )
    return command, arguments


def query_task(task_name: str) -> WindowsTaskObservation:
    res = run_cmd(
        ["schtasks.exe", "/Query", "/TN", task_name, "/XML"],
        check=False,
        capture=True,
        encoding="utf-8",
        errors="replace",
    )
    if res.returncode != 0:
        return WindowsTaskObservation(exists=False, task_name=task_name)

    try:
        root = ET.fromstring(res.stdout)
    except Exception as exc:
        raise PlatformError(
            f"Scheduled task XML for '{task_name}' could not be parsed: {exc}",
            code="E_SCHEDULE_TASK_QUERY_INVALID",
            reason=task_name,
        )

    return WindowsTaskObservation(
        exists=True,
        task_name=task_name,
        description=_find_text(root, ".//task:RegistrationInfo/task:Description"),
        interval_minutes=_parse_duration_minutes(_find_text(root, ".//task:Repetition/task:Interval")),
        random_delay_minutes=_parse_duration_minutes(_find_text(root, ".//task:TimeTrigger/task:RandomDelay")),
        command=_find_text(root, ".//task:Exec/task:Command"),
        arguments=_find_text(root, ".//task:Exec/task:Arguments"),
        enabled=_bool_from_text(_find_text(root, ".//task:Settings/task:Enabled"), default=True),
        allow_on_battery=not _bool_from_text(
            _find_text(root, ".//task:Settings/task:DisallowStartIfOnBatteries"),
            default=True,
        ),
        stop_on_battery=_bool_from_text(
            _find_text(root, ".//task:Settings/task:StopIfGoingOnBatteries"),
            default=True,
        ),
        require_network=_bool_from_text(
            _find_text(root, ".//task:Settings/task:RunOnlyIfNetworkAvailable"),
            default=False,
        ),
        wake_machine=_bool_from_text(
            _find_text(root, ".//task:Settings/task:WakeToRun"),
            default=False,
        ),
        start_when_available=_bool_from_text(
            _find_text(root, ".//task:Settings/task:StartWhenAvailable"),
            default=False,
        ),
        execution_time_limit_minutes=_parse_duration_minutes(
            _find_text(root, ".//task:Settings/task:ExecutionTimeLimit")
        ),
        multiple_instances_policy=_find_text(root, ".//task:Settings/task:MultipleInstancesPolicy"),
        restart_count=_parse_int(_find_text(root, ".//task:Settings/task:RestartOnFailure/task:Count")),
        restart_interval_minutes=_parse_duration_minutes(
            _find_text(root, ".//task:Settings/task:RestartOnFailure/task:Interval")
        )
        or 0,
        hidden=_bool_from_text(_find_text(root, ".//task:Settings/task:Hidden"), default=False),
    )


def task_matches(spec: WindowsTaskSpec, observation: WindowsTaskObservation) -> bool:
    if not observation.exists:
        return False
    if observation.description != spec.description:
        return False
    if observation.interval_minutes != spec.interval_minutes:
        return False
    if (observation.random_delay_minutes or 0) != spec.random_delay_minutes:
        return False
    if Path(observation.command).name.lower() != "powershell.exe":
        return False
    expected_wrapper = str(spec.wrapper_path).replace("/", "\\").lower()
    observed_arguments = observation.arguments.replace("/", "\\").lower()
    if expected_wrapper not in observed_arguments:
        return False
    if observation.allow_on_battery != spec.allow_on_battery:
        return False
    if observation.stop_on_battery != spec.stop_on_battery:
        return False
    if observation.require_network != spec.require_network:
        return False
    if observation.wake_machine != spec.wake_machine:
        return False
    if observation.start_when_available != spec.start_when_available:
        return False
    if observation.execution_time_limit_minutes != spec.execution_time_limit_minutes:
        return False
    if observation.multiple_instances_policy != _windows_overlap_policy(spec.multiple_instances_policy):
        return False
    if observation.restart_count != spec.restart_count:
        return False
    if observation.restart_interval_minutes != spec.restart_interval_minutes:
        return False
    if observation.hidden != spec.hidden:
        return False
    return True


def apply_task(spec: WindowsTaskSpec) -> None:
    if not provider_supported():
        raise PlatformError(
            "Windows Task Scheduler is only supported on Windows hosts.",
            code="E_SCHEDULE_PROVIDER_UNSUPPORTED",
            reason=WINDOWS_PROVIDER,
        )
    if spec.interval_minutes <= 0:
        raise PlatformError(
            "Scheduled task interval must be a positive number of minutes.",
            code="E_SCHEDULE_INTERVAL_INVALID",
            reason=spec.task_name,
        )
    if spec.execution_time_limit_minutes <= 0:
        raise PlatformError(
            "Scheduled task execution timeout must be a positive number of minutes.",
            code="E_SCHEDULE_POLICY_INVALID",
            reason=spec.task_name,
        )
    if spec.random_delay_minutes < 0:
        raise PlatformError(
            "Scheduled task random delay cannot be negative.",
            code="E_SCHEDULE_POLICY_INVALID",
            reason=spec.task_name,
        )
    if spec.restart_count < 0 or spec.restart_interval_minutes < 0:
        raise PlatformError(
            "Scheduled task retry policy cannot use negative values.",
            code="E_SCHEDULE_POLICY_INVALID",
            reason=spec.task_name,
        )

    command, arguments = build_task_command(spec.wrapper_path)
    overlap_policy = _windows_overlap_policy(spec.multiple_instances_policy)
    start_time = (datetime.now() + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S")
    script_lines = [
        "$ErrorActionPreference = 'Stop'",
        f"$action = New-ScheduledTaskAction -Execute {_ps_literal(command)} -Argument {_ps_literal(arguments)}",
        (
            "$trigger = New-ScheduledTaskTrigger -Once "
            f"-At ([datetime]{_ps_literal(start_time)}) "
            f"-RepetitionInterval (New-TimeSpan -Minutes {spec.interval_minutes}) "
            f"-RepetitionDuration (New-TimeSpan -Days {WINDOWS_TRIGGER_DURATION_DAYS})"
        ),
    ]
    if spec.random_delay_minutes > 0:
        script_lines[-1] += f" -RandomDelay (New-TimeSpan -Minutes {spec.random_delay_minutes})"

    settings_cmd = [
        "$settings = New-ScheduledTaskSettingsSet",
        f"-ExecutionTimeLimit (New-TimeSpan -Minutes {spec.execution_time_limit_minutes})",
        f"-MultipleInstances {overlap_policy}",
    ]
    if spec.allow_on_battery:
        settings_cmd.append("-AllowStartIfOnBatteries")
    if not spec.stop_on_battery:
        settings_cmd.append("-DontStopIfGoingOnBatteries")
    if spec.require_network:
        settings_cmd.append("-RunOnlyIfNetworkAvailable")
    if spec.wake_machine:
        settings_cmd.append("-WakeToRun")
    if spec.start_when_available:
        settings_cmd.append("-StartWhenAvailable")
    if spec.hidden:
        settings_cmd.append("-Hidden")
    if spec.restart_count > 0 and spec.restart_interval_minutes > 0:
        settings_cmd.append(f"-RestartCount {spec.restart_count}")
        settings_cmd.append(f"-RestartInterval (New-TimeSpan -Minutes {spec.restart_interval_minutes})")
    script_lines.append(" ".join(settings_cmd))
    script_lines.append(
        "Register-ScheduledTask "
        f"-TaskName {_ps_literal(spec.task_name)} "
        "-Action $action "
        "-Trigger $trigger "
        "-Settings $settings "
        f"-Description {_ps_literal(spec.description)} "
        "-Force | Out-Null"
    )
    run_cmd(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "\n".join(script_lines)],
        check=True,
        capture=True,
        encoding="utf-8",
        errors="replace",
    )


def remove_task(task_name: str) -> None:
    res = run_cmd(
        ["schtasks.exe", "/Delete", "/TN", task_name, "/F"],
        check=False,
        capture=True,
        encoding="utf-8",
        errors="replace",
    )
    if res.returncode != 0:
        return
    if query_task(task_name).exists:
        raise PlatformError(
            f"Scheduled task '{task_name}' could not be removed cleanly.",
            code="E_SCHEDULE_REMOVE_FAILED",
            reason=task_name,
        )


def _windows_overlap_policy(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _OVERLAP_TO_WINDOWS:
        raise PlatformError(
            f"Unsupported Windows overlap policy '{value}'.",
            code="E_SCHEDULE_POLICY_INVALID",
            reason=str(value or "overlap_policy"),
        )
    return _OVERLAP_TO_WINDOWS[normalized]


def _find_text(root: ET.Element, path: str) -> str:
    value = root.findtext(path, default="", namespaces=_TASK_NS)
    return value.strip() if isinstance(value, str) else ""


def _bool_from_text(value: str, *, default: bool) -> bool:
    normalized = str(value or "").strip().lower()
    if normalized == "":
        return default
    return normalized == "true"


def _parse_duration_minutes(raw: str) -> int | None:
    value = (raw or "").strip().upper()
    if value in {"", "PT0S"}:
        return None
    if not value.startswith("P"):
        return None
    days = 0
    hours = 0
    minutes = 0
    body = value[1:]
    time_body = ""
    if "T" in body:
        date_body, time_body = body.split("T", 1)
    else:
        date_body = body
    if "D" in date_body:
        day_part = date_body.split("D", 1)[0]
        days = int(day_part or "0")
    if "H" in time_body:
        hour_part, time_body = time_body.split("H", 1)
        hours = int(hour_part or "0")
    if "M" in time_body:
        minute_part = time_body.split("M", 1)[0]
        minutes = int(minute_part or "0")
    total = (days * 24 * 60) + (hours * 60) + minutes
    return total if total > 0 else None


def _parse_int(value: str) -> int:
    raw = str(value or "").strip()
    return int(raw) if raw else 0


def _ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
