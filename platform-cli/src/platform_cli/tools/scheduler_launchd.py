from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import plistlib
import sys

from platform_cli.core.errors import PlatformError
from platform_cli.exec.runner import run_cmd


LAUNCHD_PROVIDER = "launchd"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


@dataclass(frozen=True)
class LaunchdTaskSpec:
    task_name: str
    description: str
    interval_minutes: int
    wrapper_path: Path
    stdout_path: Path
    stderr_path: Path


@dataclass(frozen=True)
class LaunchdTaskObservation:
    exists: bool
    task_name: str
    plist_path: Path
    label: str = ""
    interval_minutes: int | None = None
    program_arguments: tuple[str, ...] = ()
    stdout_path: str = ""
    stderr_path: str = ""
    loaded: bool = False


def provider_supported() -> bool:
    return sys.platform.startswith("darwin")


def plist_path(task_name: str) -> Path:
    return LAUNCH_AGENTS_DIR / f"{task_name}.plist"


def query_task(task_name: str) -> LaunchdTaskObservation:
    path = plist_path(task_name)
    if not path.exists():
        return LaunchdTaskObservation(exists=False, task_name=task_name, plist_path=path)

    try:
        payload = plistlib.loads(path.read_bytes())
    except Exception as exc:
        raise PlatformError(
            f"Launchd plist for '{task_name}' could not be parsed: {exc}",
            code="E_SCHEDULE_TASK_QUERY_INVALID",
            reason=task_name,
        )

    label = str(payload.get("Label", "")).strip()
    interval_seconds = payload.get("StartInterval")
    interval_minutes = int(interval_seconds // 60) if isinstance(interval_seconds, int) and interval_seconds > 0 else None
    program_arguments = payload.get("ProgramArguments", [])
    args_tuple = tuple(str(item) for item in program_arguments) if isinstance(program_arguments, list) else ()
    loaded = _launchd_job_loaded(label or task_name)
    return LaunchdTaskObservation(
        exists=True,
        task_name=task_name,
        plist_path=path,
        label=label,
        interval_minutes=interval_minutes,
        program_arguments=args_tuple,
        stdout_path=str(payload.get("StandardOutPath", "")).strip(),
        stderr_path=str(payload.get("StandardErrorPath", "")).strip(),
        loaded=loaded,
    )


def task_matches(spec: LaunchdTaskSpec, observation: LaunchdTaskObservation) -> bool:
    if not observation.exists:
        return False
    if observation.label != spec.task_name:
        return False
    if observation.interval_minutes != spec.interval_minutes:
        return False
    if observation.program_arguments != (str(spec.wrapper_path),):
        return False
    if observation.stdout_path != str(spec.stdout_path):
        return False
    if observation.stderr_path != str(spec.stderr_path):
        return False
    if not observation.loaded:
        return False
    return True


def apply_task(spec: LaunchdTaskSpec) -> None:
    if not provider_supported():
        raise PlatformError(
            "launchd is only supported on macOS hosts.",
            code="E_SCHEDULE_PROVIDER_UNSUPPORTED",
            reason=LAUNCHD_PROVIDER,
        )
    if spec.interval_minutes <= 0:
        raise PlatformError(
            "Launchd task interval must be a positive number of minutes.",
            code="E_SCHEDULE_INTERVAL_INVALID",
            reason=spec.task_name,
        )

    path = plist_path(spec.task_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    spec.stdout_path.parent.mkdir(parents=True, exist_ok=True)
    spec.stderr_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": spec.task_name,
        "ProgramArguments": [str(spec.wrapper_path)],
        "StartInterval": int(spec.interval_minutes) * 60,
        "RunAtLoad": False,
        "StandardOutPath": str(spec.stdout_path),
        "StandardErrorPath": str(spec.stderr_path),
        "ProcessType": "Background",
    }
    path.write_bytes(plistlib.dumps(payload, sort_keys=True))

    _run_launchctl(["bootout", _launchctl_domain(), str(path)], check=False)
    _run_launchctl(["bootstrap", _launchctl_domain(), str(path)], check=True)
    _run_launchctl(["enable", f"{_launchctl_domain()}/{spec.task_name}"], check=False)


def remove_task(task_name: str) -> None:
    path = plist_path(task_name)
    if not path.exists():
        return
    _run_launchctl(["bootout", _launchctl_domain(), str(path)], check=False)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    if query_task(task_name).exists:
        raise PlatformError(
            f"Launchd task '{task_name}' could not be removed cleanly.",
            code="E_SCHEDULE_REMOVE_FAILED",
            reason=task_name,
        )


def _launchctl_domain() -> str:
    return f"gui/{_current_uid()}"


def _current_uid() -> int:
    return int(run_cmd(["id", "-u"], check=True, capture=True, encoding="utf-8", errors="replace").stdout.strip())


def _launchd_job_loaded(label: str) -> bool:
    if not label:
        return False
    domain = _launchctl_domain()
    for command in (
        ["print", f"{domain}/{label}"],
        ["list", label],
    ):
        res = _run_launchctl(command, check=False)
        if res.returncode == 0:
            return True
    return False


def _run_launchctl(args: list[str], *, check: bool) -> object:
    return run_cmd(
        ["launchctl", *args],
        check=check,
        capture=True,
        encoding="utf-8",
        errors="replace",
    )
