from __future__ import annotations

import pytest

from platform_cli.core.errors import PlatformError
from platform_cli.tools import codex_skill_sync


def test_codex_skill_sync_uses_generic_release_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _fake_install(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return {
            "capability": "codex-skills-aws",
            "target_path": "/tmp/codex",
            "file_count": 2,
            "updated_count": 2,
            "content_hash": "abc",
            "synced_at": 123,
            "source": "release",
            "release_repo": "owner/repo",
            "release_tag": "v1.0.0",
            "content_version": "1.0.0",
        }

    monkeypatch.setattr(codex_skill_sync, "install_release_content", _fake_install)

    result = codex_skill_sync.sync_aws_readonly_skill()

    assert captured["repo"] == "gh-org-data-platform/dp-tools-local-setup"
    assert captured["tag"] == "codex-skills-aws-v1.0.0"
    assert captured["manifest_asset"] == "content-manifest.json"
    assert result["skill_name"] == "aws-readonly-runbook"
    assert result["source"] == "release"


def test_codex_skill_sync_raises_on_download_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _failing_install(**kwargs):  # type: ignore[no-untyped-def]
        raise PlatformError("download failed", code="E_RELEASE_CONTENT_DOWNLOAD_FAILED", reason="content-manifest.json")

    monkeypatch.setattr(codex_skill_sync, "install_release_content", _failing_install)

    with pytest.raises(PlatformError) as err:
        codex_skill_sync.sync_aws_readonly_skill()

    assert err.value.code == "E_CODEX_SKILL_SYNC_FAILED"


def test_codex_skill_sync_raises_on_invalid_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    def _invalid_manifest(**kwargs):  # type: ignore[no-untyped-def]
        raise PlatformError("bad manifest", code="E_RELEASE_CONTENT_MANIFEST_INVALID", reason="manifest")

    monkeypatch.setattr(codex_skill_sync, "install_release_content", _invalid_manifest)

    with pytest.raises(PlatformError) as err:
        codex_skill_sync.sync_aws_readonly_skill()

    assert err.value.code == "E_CODEX_SKILL_SYNC_FAILED"
