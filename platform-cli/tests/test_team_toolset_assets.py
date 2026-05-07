from __future__ import annotations

from pathlib import Path

import pytest

from platform_cli.core.errors import PlatformError
from platform_cli.tools import team_toolset_assets


def test_ensure_team_toolset_available_uses_cached_file(tmp_path, monkeypatch):
    managed_path = tmp_path / "team-toolset.managed.json"
    managed_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(team_toolset_assets, "managed_team_toolset_path", lambda: managed_path)
    monkeypatch.setattr(
        team_toolset_assets,
        "sync_team_toolset",
        lambda **_kwargs: pytest.fail("sync_team_toolset should not run when cache exists"),
    )

    result = team_toolset_assets.ensure_team_toolset_available()

    assert result["local_status"] == "cached"
    assert result["used_cached"] is True


def test_ensure_team_toolset_available_syncs_when_cache_missing(tmp_path, monkeypatch):
    managed_path = tmp_path / "team-toolset.managed.json"
    calls: list[bool] = []

    monkeypatch.setattr(team_toolset_assets, "managed_team_toolset_path", lambda: managed_path)
    monkeypatch.setattr(
        team_toolset_assets,
        "sync_team_toolset",
        lambda **kwargs: calls.append(bool(kwargs.get("fail_on_error"))) or {"local_status": "synced"},
    )

    result = team_toolset_assets.ensure_team_toolset_available()

    assert result["local_status"] == "synced"
    assert calls == [True]


def test_ensure_team_toolset_available_force_refresh_syncs_even_with_cache(tmp_path, monkeypatch):
    managed_path = tmp_path / "team-toolset.managed.json"
    managed_path.write_text("{}", encoding="utf-8")
    calls: list[bool] = []

    monkeypatch.setattr(team_toolset_assets, "managed_team_toolset_path", lambda: managed_path)
    monkeypatch.setattr(
        team_toolset_assets,
        "sync_team_toolset",
        lambda **kwargs: calls.append(bool(kwargs.get("fail_on_error"))) or {"local_status": "current"},
    )

    result = team_toolset_assets.ensure_team_toolset_available(force_refresh=True)

    assert result["local_status"] == "current"
    assert calls == [True]


def test_sync_team_toolset_raises_when_missing_capability_and_required(tmp_path, monkeypatch):
    managed_path = tmp_path / "team-toolset.managed.json"

    monkeypatch.setattr(team_toolset_assets, "managed_team_toolset_path", lambda: managed_path)
    monkeypatch.setattr(team_toolset_assets, "build_sync_root_resolver", lambda: lambda _key: Path(tmp_path))
    monkeypatch.setattr(team_toolset_assets, "preview_content_updates", lambda **_kwargs: {"capabilities": []})

    with pytest.raises(PlatformError, match="could not find the 'ghdp-team-toolset' capability"):
        team_toolset_assets.sync_team_toolset(fail_on_error=True)
