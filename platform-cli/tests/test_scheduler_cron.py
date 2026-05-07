from __future__ import annotations

from pathlib import Path

import pytest

from platform_cli.tools import scheduler_cron


def test_render_task_block_contains_managed_markers() -> None:
    spec = scheduler_cron.CronTaskSpec(
        task_name="GHDP-test",
        description="Background sync",
        interval_minutes=60,
        wrapper_path=Path("/tmp/GHDP-test.sh"),
    )

    block = scheduler_cron.render_task_block(spec)

    assert "# GHDP BEGIN task_name=GHDP-test" in block
    assert "managed_by=ghdp provider=cron interval_minutes=60" in block
    assert "GHDP-test.sh" in block


def test_task_matches_uses_rendered_block() -> None:
    spec = scheduler_cron.CronTaskSpec(
        task_name="GHDP-test",
        description="Background sync",
        interval_minutes=1440,
        wrapper_path=Path("/tmp/GHDP-test.sh"),
    )
    observation = scheduler_cron.CronTaskObservation(
        exists=True,
        task_name="GHDP-test",
        block_text=scheduler_cron.render_task_block(spec),
    )

    assert scheduler_cron.task_matches(spec, observation) is True


def test_apply_task_replaces_existing_block(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(scheduler_cron, "provider_supported", lambda: True)
    written: dict[str, str] = {}
    existing = "\n".join(
        [
            "# GHDP BEGIN task_name=GHDP-test",
            "# GHDP managed_by=ghdp provider=cron interval_minutes=60",
            "# GHDP description=Old",
            "0 * * * * /tmp/old.sh",
            "# GHDP END task_name=GHDP-test",
        ]
    )
    monkeypatch.setattr(scheduler_cron, "_read_crontab", lambda: existing)
    monkeypatch.setattr(scheduler_cron, "_write_crontab", lambda content: written.setdefault("content", content))

    scheduler_cron.apply_task(
        scheduler_cron.CronTaskSpec(
            task_name="GHDP-test",
            description="New",
            interval_minutes=1440,
            wrapper_path=tmp_path / "GHDP-test.sh",
        )
    )

    assert "description=New" in written["content"]
    assert "/tmp/old.sh" not in written["content"]


def test_query_task_extracts_existing_block(monkeypatch: pytest.MonkeyPatch) -> None:
    content = "\n".join(
        [
            "# GHDP BEGIN task_name=GHDP-test",
            "# GHDP managed_by=ghdp provider=cron interval_minutes=60",
            "# GHDP description=Background sync",
            "0 * * * * /tmp/GHDP-test.sh",
            "# GHDP END task_name=GHDP-test",
        ]
    )
    monkeypatch.setattr(scheduler_cron, "_read_crontab", lambda: content)

    observation = scheduler_cron.query_task("GHDP-test")

    assert observation.exists is True
    assert "Background sync" in observation.block_text


def test_remove_task_rewrites_crontab_without_target_block(monkeypatch: pytest.MonkeyPatch) -> None:
    content = "\n".join(
        [
            "# GHDP BEGIN task_name=GHDP-test",
            "# GHDP managed_by=ghdp provider=cron interval_minutes=60",
            "# GHDP description=Background sync",
            "0 * * * * /tmp/GHDP-test.sh",
            "# GHDP END task_name=GHDP-test",
            "@daily echo keep",
        ]
    )
    monkeypatch.setattr(scheduler_cron, "_read_crontab", lambda: content)
    written: dict[str, str] = {}
    monkeypatch.setattr(scheduler_cron, "_write_crontab", lambda updated: written.setdefault("content", updated))
    monkeypatch.setattr(
        scheduler_cron,
        "query_task",
        lambda task_name: scheduler_cron.CronTaskObservation(exists=False, task_name=task_name),
    )

    scheduler_cron.remove_task("GHDP-test")

    assert "GHDP-test.sh" not in written["content"]
    assert "@daily echo keep" in written["content"]


def test_apply_task_rejects_intervals_above_one_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scheduler_cron, "provider_supported", lambda: True)

    with pytest.raises(scheduler_cron.PlatformError) as exc_info:
        scheduler_cron.apply_task(
            scheduler_cron.CronTaskSpec(
                task_name="GHDP-test",
                description="Background sync",
                interval_minutes=1500,
                wrapper_path=Path("/tmp/GHDP-test.sh"),
            )
        )

    assert exc_info.value.code == "E_SCHEDULE_POLICY_INVALID"
