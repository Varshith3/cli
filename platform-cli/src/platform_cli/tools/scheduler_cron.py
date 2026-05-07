from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
import sys

from platform_cli.core.errors import PlatformError
from platform_cli.exec.runner import run_cmd


CRON_PROVIDER = "cron"
BLOCK_PREFIX = "# GHDP"


@dataclass(frozen=True)
class CronTaskSpec:
    task_name: str
    description: str
    interval_minutes: int
    wrapper_path: Path


@dataclass(frozen=True)
class CronTaskObservation:
    exists: bool
    task_name: str
    block_text: str = ""


def provider_supported() -> bool:
    return sys.platform.startswith("linux")


def query_task(task_name: str) -> CronTaskObservation:
    content = _read_crontab()
    block = _extract_task_block(content, task_name)
    if not block:
        return CronTaskObservation(exists=False, task_name=task_name)
    return CronTaskObservation(exists=True, task_name=task_name, block_text=block)


def task_matches(spec: CronTaskSpec, observation: CronTaskObservation) -> bool:
    if not observation.exists:
        return False
    return _normalize_block(observation.block_text) == _normalize_block(render_task_block(spec))


def apply_task(spec: CronTaskSpec) -> None:
    if not provider_supported():
        raise PlatformError(
            "cron is only supported on Linux hosts.",
            code="E_SCHEDULE_PROVIDER_UNSUPPORTED",
            reason=CRON_PROVIDER,
        )
    if spec.interval_minutes <= 0:
        raise PlatformError(
            "Cron task interval must be a positive number of minutes.",
            code="E_SCHEDULE_INTERVAL_INVALID",
            reason=spec.task_name,
        )
    if spec.interval_minutes > 1440:
        raise PlatformError(
            "Cron task intervals above 1440 minutes are not supported.",
            code="E_SCHEDULE_POLICY_INVALID",
            reason=spec.task_name,
        )

    current = _read_crontab()
    updated = _replace_or_append_task_block(current, spec.task_name, render_task_block(spec))
    _write_crontab(updated)


def remove_task(task_name: str) -> None:
    current = _read_crontab()
    updated = _remove_task_block(current, task_name)
    if updated == current:
        return
    _write_crontab(updated)
    if query_task(task_name).exists:
        raise PlatformError(
            f"Cron task '{task_name}' could not be removed cleanly.",
            code="E_SCHEDULE_REMOVE_FAILED",
            reason=task_name,
        )


def render_task_block(spec: CronTaskSpec) -> str:
    entries = _cron_entries_for_interval(spec.interval_minutes)
    command = shlex.quote(str(spec.wrapper_path))
    lines = [
        f"{BLOCK_PREFIX} BEGIN task_name={spec.task_name}",
        f"{BLOCK_PREFIX} managed_by=ghdp provider={CRON_PROVIDER} interval_minutes={spec.interval_minutes}",
        f"{BLOCK_PREFIX} description={spec.description}",
    ]
    for minute, hour in entries:
        lines.append(f"{minute} {hour} * * * {command}")
    lines.append(f"{BLOCK_PREFIX} END task_name={spec.task_name}")
    return "\n".join(lines)


def _cron_entries_for_interval(interval_minutes: int) -> list[tuple[int, int]]:
    seen: set[int] = set()
    minute_of_day = 0
    entries: list[tuple[int, int]] = []
    while minute_of_day not in seen:
        seen.add(minute_of_day)
        hour, minute = divmod(minute_of_day, 60)
        entries.append((minute, hour))
        minute_of_day = (minute_of_day + interval_minutes) % 1440
    return entries


def _read_crontab() -> str:
    res = run_cmd(["crontab", "-l"], check=False, capture=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        stderr = (res.stderr or "").lower()
        stdout = (res.stdout or "").lower()
        if "no crontab" in stderr or "no crontab" in stdout:
            return ""
        raise PlatformError(
            f"Failed to read current crontab: {res.stderr or res.stdout}",
            code="E_SCHEDULE_TASK_QUERY_INVALID",
            reason=CRON_PROVIDER,
        )
    return res.stdout.strip()


def _write_crontab(content: str) -> None:
    normalized = content.strip()
    if normalized:
        normalized += "\n"
    temp_path = Path.cwd() / ".ghdp-crontab.tmp"
    temp_path.write_text(normalized, encoding="utf-8")
    try:
        run_cmd(["crontab", str(temp_path)], check=True, capture=True, encoding="utf-8", errors="replace")
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _extract_task_block(content: str, task_name: str) -> str:
    if not content.strip():
        return ""
    lines = content.splitlines()
    begin = f"{BLOCK_PREFIX} BEGIN task_name={task_name}"
    end = f"{BLOCK_PREFIX} END task_name={task_name}"
    capture = False
    block: list[str] = []
    for line in lines:
        if line.strip() == begin:
            capture = True
        if capture:
            block.append(line.rstrip())
        if capture and line.strip() == end:
            return "\n".join(block)
    return ""


def _replace_or_append_task_block(content: str, task_name: str, block: str) -> str:
    updated = _remove_task_block(content, task_name).strip()
    if updated:
        return updated + "\n\n" + block.strip()
    return block.strip()


def _remove_task_block(content: str, task_name: str) -> str:
    if not content.strip():
        return ""
    begin = f"{BLOCK_PREFIX} BEGIN task_name={task_name}"
    end = f"{BLOCK_PREFIX} END task_name={task_name}"
    output: list[str] = []
    skipping = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == begin:
            skipping = True
            continue
        if skipping and stripped == end:
            skipping = False
            continue
        if not skipping:
            output.append(line.rstrip())
    cleaned = "\n".join(line for line in output if line.strip())
    return cleaned.strip()


def _normalize_block(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())
