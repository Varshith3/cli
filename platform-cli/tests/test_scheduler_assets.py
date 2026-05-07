from __future__ import annotations

import importlib
from pathlib import Path

import pytest


scheduler_assets = importlib.import_module("platform_cli.tools.scheduler_assets")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home_root = tmp_path / "home"
    home_root.mkdir()
    monkeypatch.setenv("HOME", str(home_root))
    monkeypatch.setenv("USERPROFILE", str(home_root))
    return home_root


def _seed_installed_assets(capability_root: Path) -> None:
    capability_root.mkdir(parents=True, exist_ok=True)
    (capability_root / "capability.json").write_text(
        '{"schema_version":"1.0","capability_id":"background-scheduler","tasks_file":"tasks.json"}',
        encoding="utf-8",
    )
    (capability_root / "defaults.json").write_text(
        '{"schema_version":"1.0","capability_id":"background-scheduler","defaults":{}}',
        encoding="utf-8",
    )
    (capability_root / "tasks.json").write_text(
        '{"schema_version":"1.0","capability_id":"background-scheduler","tasks":[]}',
        encoding="utf-8",
    )


def test_ensure_scheduler_assets_synced_skips_targeted_sync_when_assets_are_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability_root = scheduler_assets.scheduler_assets_root()
    _seed_installed_assets(capability_root)
    preview_calls: list[dict[str, object]] = []

    def _fake_preview_content_updates(**kwargs):
        preview_calls.append(kwargs)
        return {
            "capabilities": [
                {
                    "capability": "background-scheduler",
                    "action": "none",
                    "latest_tag": "background-scheduler-v1.0.2",
                    "latest_version": "1.0.2",
                }
            ]
        }

    monkeypatch.setattr(scheduler_assets, "preview_content_updates", _fake_preview_content_updates)
    monkeypatch.setattr(
        scheduler_assets,
        "run_sync_actions",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("sync pre-hook should not run when scheduler assets are current")),
    )

    result = scheduler_assets.ensure_scheduler_assets_synced()

    assert Path(str(result["target_path"])) == capability_root
    assert result["source_kind"] == "synced"
    assert result["materialization_state"] == "cached"
    assert result["latest_tag"] == "background-scheduler-v1.0.2"
    assert preview_calls and preview_calls[0]["capability"] == "background-scheduler"


def test_ensure_scheduler_assets_synced_runs_capability_scoped_sync_pre_hook_for_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability_root = scheduler_assets.scheduler_assets_root()
    _seed_installed_assets(capability_root)
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        scheduler_assets,
        "preview_content_updates",
        lambda **kwargs: {
            "capabilities": [
                {
                    "capability": "background-scheduler",
                    "action": "update",
                    "latest_tag": "background-scheduler-v1.0.2",
                    "latest_version": "1.0.2",
                }
            ]
        },
    )

    def _fake_run_sync_actions(**kwargs):
        captured.update(kwargs)
        return {
            "preview": {
                "capabilities": [
                    {
                        "capability": "background-scheduler",
                        "action": "none",
                        "latest_tag": "background-scheduler-v1.0.2",
                        "latest_version": "1.0.2",
                    }
                ]
            },
            "results": {"updates": [{"capability": "background-scheduler"}], "repairs": [], "installs": []},
        }

    monkeypatch.setattr(scheduler_assets, "run_sync_actions", _fake_run_sync_actions)

    result = scheduler_assets.ensure_scheduler_assets_synced()

    assert Path(str(result["target_path"])) == capability_root
    assert captured["capability"] == "background-scheduler"
    assert captured["apply"] is True
    assert captured["scope_kind"] == "global"
    assert captured["scope_ref"] == ""
    assert result["source_kind"] == "synced"
    assert result["materialization_state"] == "installed"
    assert result["latest_version"] == "1.0.2"


def test_ensure_scheduler_assets_synced_best_effort_refresh_does_not_block_ready_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability_root = scheduler_assets.scheduler_assets_root()
    _seed_installed_assets(capability_root)

    monkeypatch.setattr(
        scheduler_assets,
        "preview_content_updates",
        lambda **kwargs: (_ for _ in ()).throw(
            scheduler_assets.PlatformError(
                "preview boom",
                code="E_SYNC_CAPABILITY_NOT_FOUND",
                reason="background-scheduler",
            )
        ),
    )

    result = scheduler_assets.ensure_scheduler_assets_synced()

    assert Path(str(result["target_path"])) == capability_root
    assert result["source_kind"] == "synced"
    assert result["materialization_state"] == "cached"
    assert result["sync_result"]["action"] == "warning"


def test_ensure_scheduler_assets_synced_repairs_incomplete_assets_through_targeted_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capability_root = scheduler_assets.scheduler_assets_root()
    capability_root.mkdir(parents=True)
    (capability_root / "tasks.json").write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        scheduler_assets,
        "preview_content_updates",
        lambda **kwargs: {
            "capabilities": [
                {
                    "capability": "background-scheduler",
                    "action": "install",
                    "latest_tag": "background-scheduler-v1.0.2",
                    "latest_version": "1.0.2",
                }
            ]
        },
    )

    def _fake_run_sync_actions(**kwargs):
        captured.update(kwargs)
        assert not (capability_root / "tasks.json").exists()
        _seed_installed_assets(capability_root)
        return {
            "preview": {
                "capabilities": [
                    {
                        "capability": "background-scheduler",
                        "action": "none",
                        "latest_tag": "background-scheduler-v1.0.2",
                        "latest_version": "1.0.2",
                    }
                ]
            },
            "results": {"installs": [{"capability": "background-scheduler"}], "repairs": [], "updates": []},
        }

    monkeypatch.setattr(scheduler_assets, "run_sync_actions", _fake_run_sync_actions)

    result = scheduler_assets.ensure_scheduler_assets_synced()

    assert Path(str(result["target_path"])) == capability_root
    assert captured["capability"] == "background-scheduler"
    assert result["source_kind"] == "synced"
    assert result["materialization_state"] == "installed"


def test_ensure_scheduler_assets_synced_wraps_blocked_sync_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        scheduler_assets,
        "preview_content_updates",
        lambda **kwargs: {
            "capabilities": [
                {
                    "capability": "background-scheduler",
                    "action": "blocked",
                    "latest_tag": "background-scheduler-v1.0.2",
                    "latest_version": "1.0.2",
                }
            ]
        },
    )

    result = scheduler_assets.ensure_scheduler_assets_synced()

    assert result["source_kind"] == "packaged"
    assert result["materialization_state"] == "installed"
    assert result["fallback_active"] is True
    assert result["sync_result"]["action"] == "fallback"
    assert (scheduler_assets.scheduler_assets_root() / "tasks.json").exists()


def test_ensure_scheduler_assets_synced_uses_packaged_fallback_when_preview_fails_without_installed_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        scheduler_assets,
        "preview_content_updates",
        lambda **kwargs: (_ for _ in ()).throw(
            scheduler_assets.PlatformError(
                "preview boom",
                code="E_SYNC_INDEX_UNAVAILABLE",
                reason="index",
            )
        ),
    )

    result = scheduler_assets.ensure_scheduler_assets_synced()

    assert result["source_kind"] == "packaged"
    assert result["materialization_state"] == "installed"
    assert result["fallback_active"] is True
    assert "packaged emergency bootstrap" in result["source_explanation"]
    tasks = scheduler_assets.scheduler_manifest.load_scheduler_tasks(
        capability_root=scheduler_assets.scheduler_assets_root()
    )
    assert {task.task_id for task in tasks} == {
        "schedule-apply-background",
        "sync-run-background",
        "version-change-latest-stable",
    }
