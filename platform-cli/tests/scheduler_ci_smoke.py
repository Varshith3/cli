from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import time

from platform_cli.tools import scheduler_cron, scheduler_launchd, scheduler_windows


def _platform_key() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("darwin"):
        return "darwin"
    if sys.platform.startswith("linux"):
        return "linux"
    raise RuntimeError(f"Unsupported platform for scheduler smoke: {sys.platform}")


def _run_windows_smoke(tmp_dir: Path) -> None:
    wrapper = tmp_dir / "ghdp-scheduler-smoke.ps1"
    wrapper.write_text("exit 0\n", encoding="utf-8")
    spec = scheduler_windows.WindowsTaskSpec(
        task_name="GHDP-background-scheduler-ci-smoke",
        description="CI smoke [managed_by=ghdp capability=background-scheduler task_id=ci-smoke]",
        interval_minutes=60,
        random_delay_minutes=0,
        wrapper_path=wrapper,
        allow_on_battery=True,
        stop_on_battery=False,
        require_network=False,
        wake_machine=False,
        start_when_available=True,
        execution_time_limit_minutes=15,
        multiple_instances_policy="skip",
        restart_count=1,
        restart_interval_minutes=1,
        hidden=False,
    )
    try:
        scheduler_windows.apply_task(spec)
        time.sleep(3)
        observation = scheduler_windows.query_task(spec.task_name)
        if not scheduler_windows.task_matches(spec, observation):
            raise RuntimeError(f"Windows task did not match after apply: {observation}")
    finally:
        scheduler_windows.remove_task(spec.task_name)


def _run_launchd_smoke(tmp_dir: Path) -> None:
    wrapper = tmp_dir / "ghdp-scheduler-smoke.sh"
    wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    wrapper.chmod(0o755)
    spec = scheduler_launchd.LaunchdTaskSpec(
        task_name="GHDP-background-scheduler-ci-smoke",
        description="CI smoke [managed_by=ghdp capability=background-scheduler task_id=ci-smoke]",
        interval_minutes=60,
        wrapper_path=wrapper,
        stdout_path=tmp_dir / "stdout.log",
        stderr_path=tmp_dir / "stderr.log",
    )
    try:
        scheduler_launchd.apply_task(spec)
        time.sleep(3)
        observation = scheduler_launchd.query_task(spec.task_name)
        if not scheduler_launchd.task_matches(spec, observation):
            raise RuntimeError(f"launchd task did not match after apply: {observation}")
    finally:
        scheduler_launchd.remove_task(spec.task_name)


def _run_cron_smoke(tmp_dir: Path) -> None:
    wrapper = tmp_dir / "ghdp-scheduler-smoke.sh"
    wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    wrapper.chmod(0o755)
    spec = scheduler_cron.CronTaskSpec(
        task_name="GHDP-background-scheduler-ci-smoke",
        description="CI smoke [managed_by=ghdp capability=background-scheduler task_id=ci-smoke]",
        interval_minutes=60,
        wrapper_path=wrapper,
    )
    try:
        scheduler_cron.apply_task(spec)
        observation = scheduler_cron.query_task(spec.task_name)
        if not scheduler_cron.task_matches(spec, observation):
            raise RuntimeError(f"cron task did not match after apply: {observation}")
    finally:
        scheduler_cron.remove_task(spec.task_name)


def main() -> int:
    platform_key = _platform_key()
    with tempfile.TemporaryDirectory(prefix=f"ghdp-scheduler-smoke-{platform_key}-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        if platform_key == "windows":
            _run_windows_smoke(tmp_path)
        elif platform_key == "darwin":
            _run_launchd_smoke(tmp_path)
        elif platform_key == "linux":
            _run_cron_smoke(tmp_path)
        else:
            raise RuntimeError(f"Unsupported platform key: {platform_key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
