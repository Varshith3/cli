from __future__ import annotations

import platform_cli.tools.github_auth as tools_github_auth


def test_ensure_github_authenticated_skips_interactive_login_for_managed_token(monkeypatch) -> None:
    monkeypatch.setattr(tools_github_auth, "is_managed_install", lambda: True)
    monkeypatch.setattr(tools_github_auth, "managed_install_token", lambda: "managed-token")
    monkeypatch.setattr(
        tools_github_auth,
        "_resolve_gh_exe",
        lambda: (_ for _ in ()).throw(AssertionError("gh executable lookup should be skipped")),
    )

    tools_github_auth.ensure_github_authenticated(force=False, status_printer=None)
