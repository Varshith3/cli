from __future__ import annotations

from pathlib import Path

import platform_cli.core.github_auth as github_auth


def test_write_managed_github_token_writes_local_state(monkeypatch, tmp_path: Path) -> None:
    token_path = tmp_path / "managed-auth" / "github-token"
    monkeypatch.setattr(github_auth, "managed_auth_token_path", lambda: token_path)

    written = github_auth.write_managed_github_token("managed-123")

    assert written == token_path
    assert token_path.read_text(encoding="utf-8").strip() == "managed-123"


def test_resolve_mode_defaults_to_locked_when_managed_policy_missing(monkeypatch, tmp_path: Path) -> None:
    policy_path = tmp_path / "managed-auth" / "policy.json"
    monkeypatch.setattr(github_auth, "managed_auth_policy_path", lambda: policy_path)

    mode = github_auth.resolve_github_auth_mode(managed_install=True)

    assert mode.mode == github_auth.AUTH_MODE_MANAGED_LOCKED
    assert mode.policy_valid is False
    assert mode.source == "managed-failsafe"


def test_set_mode_persists_policy(monkeypatch, tmp_path: Path) -> None:
    policy_path = tmp_path / "managed-auth" / "policy.json"
    monkeypatch.setattr(github_auth, "managed_auth_policy_path", lambda: policy_path)

    state = github_auth.set_github_auth_mode(
        github_auth.AUTH_MODE_PERSONAL_ALLOWED,
        actor="alice",
        reason="support-case",
    )

    assert state.mode == github_auth.AUTH_MODE_PERSONAL_ALLOWED
    payload = policy_path.read_text(encoding="utf-8")
    assert '"mode": "personal_allowed"' in payload
    assert '"changed_by": "alice"' in payload
    assert '"reason": "support-case"' in payload


def test_managed_locked_blocks_personal_env_override(monkeypatch, tmp_path: Path) -> None:
    marker = tmp_path / "managed-install"
    state_path = tmp_path / "install-state.json"
    policy_path = tmp_path / "managed-auth" / "policy.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("managed\n", encoding="utf-8")
    state_path.write_text('{"install_mode":"managed"}\n', encoding="utf-8")
    policy_path.write_text('{"mode":"managed_locked"}\n', encoding="utf-8")

    monkeypatch.setattr(github_auth, "managed_install_marker_path", lambda: marker)
    monkeypatch.setattr(github_auth, "install_state_path", lambda: state_path)
    monkeypatch.setattr(github_auth, "managed_auth_policy_path", lambda: policy_path)
    monkeypatch.setattr(github_auth, "build_install_flavor", lambda: "managed")
    monkeypatch.setattr(github_auth, "managed_embedded_github_token", lambda: "managed-token")
    monkeypatch.setenv("GHDP_MANAGED_INSTALL", "1")
    monkeypatch.setenv("GHDP_TOKEN", "personal-token")

    token = github_auth.managed_install_token()

    assert token == "managed-token"


def test_managed_personal_allowed_accepts_personal_env_fallback(monkeypatch, tmp_path: Path) -> None:
    marker = tmp_path / "managed-install"
    token_path = tmp_path / "managed-auth" / "github-token"
    policy_path = tmp_path / "managed-auth" / "policy.json"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("managed\n", encoding="utf-8")
    policy_path.write_text('{"mode":"personal_allowed"}\n', encoding="utf-8")

    monkeypatch.setattr(github_auth, "managed_install_marker_path", lambda: marker)
    monkeypatch.setattr(github_auth, "managed_auth_token_path", lambda: token_path)
    monkeypatch.setattr(github_auth, "managed_auth_policy_path", lambda: policy_path)
    monkeypatch.setenv("GHDP_MANAGED_INSTALL", "1")
    monkeypatch.setenv("GHDP_TOKEN", "personal-token")

    token = github_auth.managed_install_token()

    assert token == "personal-token"


def test_gh_subprocess_env_scrubs_empty_github_tokens(monkeypatch) -> None:
    monkeypatch.setattr(github_auth, "managed_install_token", lambda: "")
    base = {"PATH": "x", "GHDP_TOKEN": "old", "GH_TOKEN": "old", "GITHUB_TOKEN": "old"}

    env = github_auth.gh_subprocess_env(base)

    assert env["PATH"] == "x"
    assert "GHDP_TOKEN" not in env
    assert "GH_TOKEN" not in env
    assert "GITHUB_TOKEN" not in env


def test_read_managed_github_token_strips_utf8_bom(monkeypatch, tmp_path: Path) -> None:
    token_path = tmp_path / "managed-auth" / "github-token"
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text("\ufeffghp_testtoken123\n", encoding="utf-8")
    monkeypatch.setattr(github_auth, "managed_auth_token_path", lambda: token_path)

    assert github_auth.read_managed_github_token() == "ghp_testtoken123"

