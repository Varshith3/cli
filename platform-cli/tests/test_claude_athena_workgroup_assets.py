from __future__ import annotations

import pytest

from platform_cli.tools import claude_athena_workgroup_assets


def test_ensure_claude_athena_workgroup_map_available_uses_cached_file(tmp_path, monkeypatch):
    managed_path = tmp_path / "claude-athena-workgroup-map.managed.json"
    managed_path.write_text("{}", encoding="utf-8")

    monkeypatch.delenv(claude_athena_workgroup_assets.CLAUDE_ATHENA_WORKGROUP_MAP_ENV_KEY, raising=False)
    monkeypatch.setattr(
        claude_athena_workgroup_assets,
        "user_claude_athena_workgroup_map_path",
        lambda: tmp_path / "claude-athena-workgroup-map.json",
    )
    monkeypatch.setattr(
        claude_athena_workgroup_assets,
        "managed_claude_athena_workgroup_map_path",
        lambda: managed_path,
    )
    monkeypatch.setattr(
        claude_athena_workgroup_assets,
        "sync_claude_athena_workgroup_map",
        lambda **_kwargs: pytest.fail("sync_claude_athena_workgroup_map should not run when cache exists"),
    )

    result = claude_athena_workgroup_assets.ensure_claude_athena_workgroup_map_available()

    assert result["local_status"] == "cached"
    assert result["used_cached"] is True


def test_ensure_claude_athena_workgroup_map_available_syncs_when_cache_missing(tmp_path, monkeypatch):
    calls: list[bool] = []

    monkeypatch.delenv(claude_athena_workgroup_assets.CLAUDE_ATHENA_WORKGROUP_MAP_ENV_KEY, raising=False)
    monkeypatch.setattr(
        claude_athena_workgroup_assets,
        "user_claude_athena_workgroup_map_path",
        lambda: tmp_path / "claude-athena-workgroup-map.json",
    )
    monkeypatch.setattr(
        claude_athena_workgroup_assets,
        "managed_claude_athena_workgroup_map_path",
        lambda: tmp_path / "claude-athena-workgroup-map.managed.json",
    )
    monkeypatch.setattr(
        claude_athena_workgroup_assets,
        "sync_claude_athena_workgroup_map",
        lambda **kwargs: calls.append(bool(kwargs.get("fail_on_error"))) or {"local_status": "fallback"},
    )

    result = claude_athena_workgroup_assets.ensure_claude_athena_workgroup_map_available()

    assert result["local_status"] == "fallback"
    assert calls == [False]


def test_ensure_claude_athena_workgroup_map_available_force_refresh_syncs_even_with_cache(tmp_path, monkeypatch):
    managed_path = tmp_path / "claude-athena-workgroup-map.managed.json"
    managed_path.write_text("{}", encoding="utf-8")
    calls: list[bool] = []

    monkeypatch.delenv(claude_athena_workgroup_assets.CLAUDE_ATHENA_WORKGROUP_MAP_ENV_KEY, raising=False)
    monkeypatch.setattr(
        claude_athena_workgroup_assets,
        "user_claude_athena_workgroup_map_path",
        lambda: tmp_path / "claude-athena-workgroup-map.json",
    )
    monkeypatch.setattr(
        claude_athena_workgroup_assets,
        "managed_claude_athena_workgroup_map_path",
        lambda: managed_path,
    )
    monkeypatch.setattr(
        claude_athena_workgroup_assets,
        "sync_claude_athena_workgroup_map",
        lambda **kwargs: calls.append(bool(kwargs.get("fail_on_error"))) or {"local_status": "synced"},
    )

    result = claude_athena_workgroup_assets.ensure_claude_athena_workgroup_map_available(force_refresh=True)

    assert result["local_status"] == "synced"
    assert calls == [False]

