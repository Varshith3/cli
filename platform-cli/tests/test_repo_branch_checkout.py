from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from platform_cli.tools import repo_branch_checkout


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    return tmp_path / "repo"


def test_checkout_skips_outside_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        repo_branch_checkout,
        "run_cmd",
        lambda cmd, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    result = repo_branch_checkout.checkout_remote_branch_if_safe(repo="owner/repo", branch_name="feature/test")
    assert result.persist_ready is False
    assert "not inside a git repo" in result.message


def test_checkout_skips_dirty_worktree(monkeypatch: pytest.MonkeyPatch, repo_root: Path) -> None:
    def _fake_run(cmd, **kwargs):
        if cmd[:3] == ["git", "rev-parse", "--is-inside-work-tree"]:
            return SimpleNamespace(returncode=0, stdout="true", stderr="")
        if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return SimpleNamespace(returncode=0, stdout=str(repo_root), stderr="")
        if cmd[:4] == ["gh", "repo", "view", "--json"]:
            return SimpleNamespace(returncode=0, stdout='{"nameWithOwner":"owner/repo"}', stderr="")
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return SimpleNamespace(returncode=0, stdout=" M file.txt", stderr="")
        raise AssertionError(cmd)

    monkeypatch.setattr(repo_branch_checkout, "run_cmd", _fake_run)
    result = repo_branch_checkout.checkout_remote_branch_if_safe(repo="owner/repo", branch_name="feature/test")
    assert result.persist_ready is False
    assert "not clean" in result.message


def test_checkout_succeeds_for_matching_clean_repo(monkeypatch: pytest.MonkeyPatch, repo_root: Path) -> None:
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:3] == ["git", "rev-parse", "--is-inside-work-tree"]:
            return SimpleNamespace(returncode=0, stdout="true", stderr="")
        if cmd[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return SimpleNamespace(returncode=0, stdout=str(repo_root), stderr="")
        if cmd[:4] == ["gh", "repo", "view", "--json"]:
            return SimpleNamespace(returncode=0, stdout='{"nameWithOwner":"owner/repo"}', stderr="")
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["git", "fetch", "origin"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["git", "checkout", "-B"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(cmd)

    monkeypatch.setattr(repo_branch_checkout, "run_cmd", _fake_run)
    result = repo_branch_checkout.checkout_remote_branch_if_safe(repo="owner/repo", branch_name="feature/test")
    assert result.persist_ready is True
    assert result.repo_root == repo_root
    assert ["git", "fetch", "origin", "feature/test"] in calls
