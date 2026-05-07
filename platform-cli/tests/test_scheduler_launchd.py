from __future__ import annotations

from pathlib import Path
import plistlib
from types import SimpleNamespace

import pytest

from platform_cli.tools import scheduler_launchd


def test_query_task_parses_launchd_plist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scheduler_launchd, "LAUNCH_AGENTS_DIR", tmp_path)
    plist_path = tmp_path / "GHDP-test.plist"
    plist_path.write_bytes(
        plistlib.dumps(
            {
                "Label": "GHDP-test",
                "ProgramArguments": ["/tmp/GHDP-test.sh"],
                "StartInterval": 3600,
                "StandardOutPath": "/tmp/stdout.log",
                "StandardErrorPath": "/tmp/stderr.log",
            }
        )
    )
    monkeypatch.setattr(scheduler_launchd, "_launchd_job_loaded", lambda label: label == "GHDP-test")

    observation = scheduler_launchd.query_task("GHDP-test")

    assert observation.exists is True
    assert observation.label == "GHDP-test"
    assert observation.interval_minutes == 60
    assert observation.program_arguments == ("/tmp/GHDP-test.sh",)
    assert observation.loaded is True


def test_task_matches_compares_expected_fields() -> None:
    spec = scheduler_launchd.LaunchdTaskSpec(
        task_name="GHDP-test",
        description="desc",
        interval_minutes=60,
        wrapper_path=Path("C:/tmp/GHDP-test.sh"),
        stdout_path=Path("C:/tmp/stdout.log"),
        stderr_path=Path("C:/tmp/stderr.log"),
    )
    observation = scheduler_launchd.LaunchdTaskObservation(
        exists=True,
        task_name="GHDP-test",
        plist_path=Path("C:/tmp/GHDP-test.plist"),
        label="GHDP-test",
        interval_minutes=60,
        program_arguments=(str(spec.wrapper_path),),
        stdout_path=str(spec.stdout_path),
        stderr_path=str(spec.stderr_path),
        loaded=True,
    )

    assert scheduler_launchd.task_matches(spec, observation) is True


def test_apply_task_writes_plist_and_bootstraps(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(scheduler_launchd, "provider_supported", lambda: True)
    monkeypatch.setattr(scheduler_launchd, "LAUNCH_AGENTS_DIR", tmp_path)
    monkeypatch.setattr(scheduler_launchd, "_launchctl_domain", lambda: "gui/501")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        scheduler_launchd,
        "_run_launchctl",
        lambda args, check: calls.append(args) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    spec = scheduler_launchd.LaunchdTaskSpec(
        task_name="GHDP-test",
        description="desc",
        interval_minutes=60,
        wrapper_path=tmp_path / "GHDP-test.sh",
        stdout_path=tmp_path / "stdout.log",
        stderr_path=tmp_path / "stderr.log",
    )
    spec.wrapper_path.write_text("#!/bin/sh\n", encoding="utf-8")

    scheduler_launchd.apply_task(spec)

    plist_path = tmp_path / "GHDP-test.plist"
    payload = plistlib.loads(plist_path.read_bytes())
    assert payload["Label"] == "GHDP-test"
    assert payload["StartInterval"] == 3600
    assert calls[0][0] == "bootout"
    assert calls[1][0] == "bootstrap"


def test_remove_task_unlinks_plist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(scheduler_launchd, "LAUNCH_AGENTS_DIR", tmp_path)
    monkeypatch.setattr(scheduler_launchd, "_launchctl_domain", lambda: "gui/501")
    monkeypatch.setattr(
        scheduler_launchd,
        "_run_launchctl",
        lambda args, check: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    plist_path = tmp_path / "GHDP-test.plist"
    plist_path.write_bytes(plistlib.dumps({"Label": "GHDP-test"}))

    scheduler_launchd.remove_task("GHDP-test")

    assert not plist_path.exists()


def test_remove_task_raises_when_plist_still_exists(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(scheduler_launchd, "LAUNCH_AGENTS_DIR", tmp_path)
    monkeypatch.setattr(scheduler_launchd, "_launchctl_domain", lambda: "gui/501")
    monkeypatch.setattr(
        scheduler_launchd,
        "_run_launchctl",
        lambda args, check: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    plist_path = tmp_path / "GHDP-test.plist"
    plist_path.write_bytes(plistlib.dumps({"Label": "GHDP-test"}))
    monkeypatch.setattr(
        scheduler_launchd,
        "query_task",
        lambda task_name: scheduler_launchd.LaunchdTaskObservation(
            exists=True,
            task_name=task_name,
            plist_path=plist_path,
        ),
    )

    with pytest.raises(scheduler_launchd.PlatformError) as exc_info:
        scheduler_launchd.remove_task("GHDP-test")

    assert exc_info.value.code == "E_SCHEDULE_REMOVE_FAILED"
