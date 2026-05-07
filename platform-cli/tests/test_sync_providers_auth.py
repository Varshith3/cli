from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import platform_cli.core.sync_providers as sync_providers


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_github_release_provider_prefers_api_when_token_present(monkeypatch, tmp_path: Path) -> None:
    provider = sync_providers.GitHubReleaseProvider(run_cmd_impl=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("gh fallback should not run")))
    monkeypatch.setattr(sync_providers, "managed_install_token", lambda: "managed-token")
    monkeypatch.setattr(sync_providers, "direct_github_token", lambda: "")

    def _fake_urlopen(req, timeout=0):  # type: ignore[no-untyped-def]
        url = req.full_url
        if "/releases/tags/" in url:
            payload = {"assets": [{"name": "content-manifest.json", "id": 101}]}
            return _FakeResponse(json.dumps(payload).encode("utf-8"))
        if "/releases/assets/" in url:
            return _FakeResponse(b'{"schema_version":"1.0"}')
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(sync_providers.urllib.request, "urlopen", _fake_urlopen)

    downloaded = provider.download_asset(
        source={"repo": "owner/repo", "tag": "v1.0.0"},
        asset_name="content-manifest.json",
        download_dir=tmp_path,
    )

    assert downloaded.exists()
    assert downloaded.read_bytes() == b'{"schema_version":"1.0"}'


def test_github_release_provider_falls_back_to_gh_when_api_download_fails(monkeypatch, tmp_path: Path) -> None:
    calls: list[SimpleNamespace] = []

    def _fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(SimpleNamespace(cmd=list(cmd), kwargs=dict(kwargs)))
        asset_name = cmd[cmd.index("--pattern") + 1]
        out_dir = Path(cmd[cmd.index("--dir") + 1])
        (out_dir / asset_name).write_text("ok", encoding="utf-8")

    provider = sync_providers.GitHubReleaseProvider(run_cmd_impl=_fake_run)
    monkeypatch.setattr(sync_providers, "managed_install_token", lambda: "managed-token")
    monkeypatch.setattr(sync_providers, "direct_github_token", lambda: "")
    monkeypatch.setattr(sync_providers.urllib.request, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("api down")))
    monkeypatch.setattr(sync_providers, "gh_subprocess_env", lambda: {"GH_TOKEN": "managed-token"})

    downloaded = provider.download_asset(
        source={"repo": "owner/repo", "tag": "v1.0.0"},
        asset_name="content-manifest.json",
        download_dir=tmp_path,
    )

    assert downloaded.exists()
    assert downloaded.read_text(encoding="utf-8") == "ok"
    assert len(calls) == 1
    assert calls[0].kwargs.get("env") == {"GH_TOKEN": "managed-token"}


def test_marketplace_provider_uses_managed_env_for_commit_resolution(monkeypatch) -> None:
    calls: list[SimpleNamespace] = []

    def _fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(SimpleNamespace(cmd=list(cmd), kwargs=dict(kwargs)))
        return SimpleNamespace(stdout='{"sha":"abc123"}')

    provider = sync_providers.MarketplaceRepoProvider(run_cmd_impl=_fake_run)
    monkeypatch.setattr(sync_providers, "gh_subprocess_env", lambda: {"GH_TOKEN": "managed-token"})

    commit = provider.resolve_version(source={"repo": "owner/repo", "branch": "develop"})

    assert commit == "abc123"
    assert len(calls) == 1
    assert calls[0].cmd[:3] == ["gh", "api", "repos/owner/repo/commits/develop"]
    assert calls[0].kwargs.get("env") == {"GH_TOKEN": "managed-token"}


def test_marketplace_provider_uses_managed_env_for_token_lookup(monkeypatch, tmp_path: Path) -> None:
    calls: list[SimpleNamespace] = []

    def _fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(SimpleNamespace(cmd=list(cmd), kwargs=dict(kwargs)))
        return SimpleNamespace(stdout="managed-token\n")

    provider = sync_providers.MarketplaceRepoProvider(run_cmd_impl=_fake_run)
    monkeypatch.setattr(sync_providers, "gh_subprocess_env", lambda: {"GH_TOKEN": "managed-token"})
    monkeypatch.setattr(
        sync_providers.urllib.request,
        "urlopen",
        lambda req: _FakeResponse(b"tar-data"),
    )

    archive_path = tmp_path / "snapshot.tar"
    provider._download_remote_snapshot(repo="owner/repo", commit="abc123", archive_path=archive_path)

    assert archive_path.read_bytes() == b"tar-data"
    assert len(calls) == 1
    assert calls[0].cmd[:3] == ["gh", "auth", "token"]
    assert calls[0].kwargs.get("env") == {"GH_TOKEN": "managed-token"}
