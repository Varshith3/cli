from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from platform_cli.tools import scheduler_windows


def test_query_task_parses_policy_fields(monkeypatch) -> None:
    xml = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Background sync [managed_by=ghdp capability=background-scheduler task_id=sync-run-background]</Description>
  </RegistrationInfo>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT20M</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <WakeToRun>true</WakeToRun>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Hidden>false</Hidden>
    <RestartOnFailure>
      <Count>3</Count>
      <Interval>PT15M</Interval>
    </RestartOnFailure>
  </Settings>
  <Triggers>
    <TimeTrigger>
      <RandomDelay>PT5M</RandomDelay>
      <Repetition>
        <Interval>PT1H</Interval>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\\wrapper.ps1"</Arguments>
    </Exec>
  </Actions>
</Task>
"""

    monkeypatch.setattr(
        scheduler_windows,
        "run_cmd",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=xml, stderr=""),
    )

    observation = scheduler_windows.query_task("GHDP-test")

    assert observation.exists is True
    assert observation.description == "Background sync [managed_by=ghdp capability=background-scheduler task_id=sync-run-background]"
    assert observation.interval_minutes == 60
    assert observation.random_delay_minutes == 5
    assert observation.allow_on_battery is True
    assert observation.stop_on_battery is False
    assert observation.require_network is True
    assert observation.wake_machine is True
    assert observation.start_when_available is True
    assert observation.execution_time_limit_minutes == 20
    assert observation.multiple_instances_policy == "IgnoreNew"
    assert observation.restart_count == 3
    assert observation.restart_interval_minutes == 15


def test_task_matches_compares_policy_fields() -> None:
    spec = scheduler_windows.WindowsTaskSpec(
        task_name="GHDP-test",
        description="Background sync [managed_by=ghdp capability=background-scheduler task_id=sync-run-background]",
        interval_minutes=60,
        random_delay_minutes=5,
        wrapper_path=Path("C:/wrapper.ps1"),
        allow_on_battery=True,
        stop_on_battery=False,
        require_network=True,
        wake_machine=True,
        start_when_available=True,
        execution_time_limit_minutes=20,
        multiple_instances_policy="skip",
        restart_count=3,
        restart_interval_minutes=15,
        hidden=False,
    )
    observation = scheduler_windows.WindowsTaskObservation(
        exists=True,
        task_name="GHDP-test",
        description="Background sync [managed_by=ghdp capability=background-scheduler task_id=sync-run-background]",
        interval_minutes=60,
        random_delay_minutes=5,
        command="powershell.exe",
        arguments='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\\wrapper.ps1"',
        allow_on_battery=True,
        stop_on_battery=False,
        require_network=True,
        wake_machine=True,
        start_when_available=True,
        execution_time_limit_minutes=20,
        multiple_instances_policy="IgnoreNew",
        restart_count=3,
        restart_interval_minutes=15,
        hidden=False,
    )

    assert scheduler_windows.task_matches(spec, observation) is True


def test_apply_task_builds_expected_powershell_registration(monkeypatch) -> None:
    captured: list[list[str]] = []

    monkeypatch.setattr(scheduler_windows, "provider_supported", lambda: True)
    monkeypatch.setattr(scheduler_windows, "run_cmd", lambda cmd, **kwargs: captured.append(cmd) or SimpleNamespace(returncode=0))

    scheduler_windows.apply_task(
        scheduler_windows.WindowsTaskSpec(
            task_name="GHDP-test",
            description="Background sync [managed_by=ghdp capability=background-scheduler task_id=sync-run-background]",
            interval_minutes=60,
            random_delay_minutes=5,
            wrapper_path=Path("C:/wrapper.ps1"),
            allow_on_battery=True,
            stop_on_battery=False,
            require_network=True,
            wake_machine=True,
            start_when_available=True,
            execution_time_limit_minutes=20,
            multiple_instances_policy="skip",
            restart_count=3,
            restart_interval_minutes=15,
            hidden=False,
        )
    )

    script = captured[0][4]
    assert "New-ScheduledTaskAction" in script
    assert "-WindowStyle Hidden" in script
    assert "-AllowStartIfOnBatteries" in script
    assert "-DontStopIfGoingOnBatteries" in script
    assert "-RunOnlyIfNetworkAvailable" in script
    assert "-WakeToRun" in script
    assert "-StartWhenAvailable" in script
    assert "-MultipleInstances IgnoreNew" in script


def test_remove_task_raises_when_task_still_exists(monkeypatch) -> None:
    calls: list[list[str]] = []

    monkeypatch.setattr(
        scheduler_windows,
        "run_cmd",
        lambda cmd, **kwargs: calls.append(cmd) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        scheduler_windows,
        "query_task",
        lambda task_name: scheduler_windows.WindowsTaskObservation(exists=True, task_name=task_name),
    )

    with pytest.raises(scheduler_windows.PlatformError) as exc_info:
        scheduler_windows.remove_task("GHDP-test")

    assert exc_info.value.code == "E_SCHEDULE_REMOVE_FAILED"
    assert calls[0][:3] == ["schtasks.exe", "/Delete", "/TN"]
