from types import SimpleNamespace

import pytest

import platform_cli.core.update as update_mod
from platform_cli.core.errors import PlatformError
from platform_cli.core.secret_resolver import extract_secret_value
from platform_cli.core.update import InstallResult, install_selected_version, list_release_tags, normalize_tag
from platform_cli.tools.release_notes import extract_most_important_release_note, extract_release_summary


def test_normalize_tag_accepts_plain_and_prefixed() -> None:
    assert normalize_tag("0.1.0") == "v0.1.0"
    assert normalize_tag("v0.1.0") == "v0.1.0"


def test_list_release_tags_filters_drafts(monkeypatch) -> None:
    payload = """[
      {"tag_name": "v0.2.0", "prerelease": false, "draft": false, "published_at": "2026-01-01T00:00:00Z"},
      {"tag_name": "v0.2.0-rc1", "prerelease": true, "draft": false, "published_at": "2025-12-31T00:00:00Z"},
      {"tag_name": "v0.3.0-draft", "prerelease": true, "draft": true, "published_at": ""}
    ]"""

    monkeypatch.setattr("platform_cli.core.update._gh_available", lambda: True)
    monkeypatch.setattr(
        "platform_cli.core.update.run_cmd",
        lambda *args, **kwargs: SimpleNamespace(stdout=payload),
    )

    rows = list_release_tags("owner/repo", include_drafts=False)
    assert [r.tag for r in rows] == ["v0.2.0", "v0.2.0-rc1"]

    rows_with_drafts = list_release_tags("owner/repo", include_drafts=True)
    assert [r.tag for r in rows_with_drafts] == ["v0.2.0", "v0.2.0-rc1", "v0.3.0-draft"]


def test_list_release_tags_reads_gh_output_as_utf8(monkeypatch) -> None:
    seen = {}

    def _stub_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        seen["encoding"] = kwargs.get("encoding")
        seen["errors"] = kwargs.get("errors")
        return SimpleNamespace(stdout="[]")

    monkeypatch.setattr("platform_cli.core.update._gh_available", lambda: True)
    monkeypatch.setattr("platform_cli.core.update.run_cmd", _stub_run)

    list_release_tags("owner/repo", include_drafts=False)

    assert seen == {"encoding": "utf-8", "errors": "replace"}


def test_list_release_tags_ignores_non_ghdp_style_tags(monkeypatch) -> None:
    payload = """[
      {"tag_name": "marketplace-skill-allowlist-v1.2.0", "prerelease": false, "draft": false, "published_at": "2026-01-01T00:00:00Z"},
      {"tag_name": "v0.2.0", "prerelease": false, "draft": false, "published_at": "2026-01-02T00:00:00Z"},
      {"tag_name": "release-foo", "prerelease": false, "draft": false, "published_at": "2026-01-03T00:00:00Z"}
    ]"""

    monkeypatch.setattr("platform_cli.core.update._gh_available", lambda: True)
    monkeypatch.setattr(
        "platform_cli.core.update.run_cmd",
        lambda *args, **kwargs: SimpleNamespace(stdout=payload),
    )

    rows = list_release_tags("owner/repo", include_drafts=False)
    assert [r.tag for r in rows] == ["v0.2.0"]


def test_list_release_tags_falls_back_to_api_when_gh_auth_unavailable(monkeypatch) -> None:
    monkeypatch.setattr("platform_cli.core.update._gh_available", lambda: True)

    def _stub_run(cmd, check=True, **kwargs):  # type: ignore[no-untyped-def]
        raise PlatformError("gh auth missing", code="E_CMD_FAILED", reason="nonzero_exit")

    monkeypatch.setattr("platform_cli.core.update.run_cmd", _stub_run)
    monkeypatch.setattr("platform_cli.core.update._managed_install_marker_exists", lambda: True)
    monkeypatch.setattr("platform_cli.core.update._token_from_managed_auth_state", lambda: "managed-pat-123")
    monkeypatch.setattr(
        "platform_cli.core.update._list_release_tags_via_api",
        lambda repo, limit, include_drafts, token: [
            update_mod.ReleaseTag(tag="v0.2.0", prerelease=False, draft=False, published_at="")
        ],
    )

    rows = list_release_tags("owner/repo", include_drafts=False)
    assert [r.tag for r in rows] == ["v0.2.0"]


def test_install_selected_version_auto_prefers_pipx_when_installed(monkeypatch) -> None:
    calls = []

    monkeypatch.setattr("platform_cli.core.update._pipx_has_ghdp", lambda: True)
    monkeypatch.setattr("platform_cli.core.update._pipx_available", lambda: True)
    monkeypatch.setattr(
        "platform_cli.core.update._apply_update_via_pipx",
        lambda repo, tag: calls.append(("pipx", repo, tag))
        or InstallResult(method="pipx", target_tag=tag, verification_status="verified", active_tag=tag),
    )
    monkeypatch.setattr(
        "platform_cli.core.update._apply_update_via_installer",
        lambda repo, tag: calls.append(("installer", repo, tag))
        or InstallResult(method="installer", target_tag=tag, verification_status="verified", active_tag=tag),
    )

    used = install_selected_version("owner/repo", "0.1.0", method="auto")

    expected = "installer" if update_mod.os.name == "nt" else "pipx"
    assert used == expected
    assert calls == [(expected, "owner/repo", "v0.1.0")]


def test_install_selected_version_auto_falls_back_to_installer(monkeypatch) -> None:
    calls = []

    monkeypatch.setattr("platform_cli.core.update._pipx_has_ghdp", lambda: False)
    monkeypatch.setattr(
        "platform_cli.core.update._apply_update_via_pipx",
        lambda repo, tag: calls.append(("pipx", repo, tag))
        or InstallResult(method="pipx", target_tag=tag, verification_status="verified", active_tag=tag),
    )
    monkeypatch.setattr(
        "platform_cli.core.update._apply_update_via_installer",
        lambda repo, tag: calls.append(("installer", repo, tag))
        or InstallResult(method="installer", target_tag=tag, verification_status="verified", active_tag=tag),
    )

    used = install_selected_version("owner/repo", "v0.1.0", method="auto")

    assert used == "installer"
    assert calls == [("installer", "owner/repo", "v0.1.0")]


def test_install_selected_version_rejects_unknown_method() -> None:
    with pytest.raises(PlatformError):
        install_selected_version("owner/repo", "0.1.0", method="unknown")


def test_gh_ready_for_repo_when_gh_missing(monkeypatch) -> None:
    monkeypatch.setattr("platform_cli.core.update._gh_available", lambda: False)
    ready, reason, login = update_mod._gh_ready_for_repo("owner/repo")
    assert ready is False
    assert reason == "gh_not_installed"
    assert login == ""


def test_gh_ready_for_repo_when_not_authenticated(monkeypatch) -> None:
    monkeypatch.setattr("platform_cli.core.update._gh_available", lambda: True)
    monkeypatch.setattr(
        "platform_cli.core.update.run_cmd",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    ready, reason, login = update_mod._gh_ready_for_repo("owner/repo")
    assert ready is False
    assert reason == "gh_not_authenticated"
    assert login == ""


def test_gh_ready_for_repo_happy_path(monkeypatch) -> None:
    monkeypatch.setattr("platform_cli.core.update._gh_available", lambda: True)

    def _stub_run(cmd, check=False, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[:3] == ["gh", "auth", "status"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["gh", "api", "user"]:
            return SimpleNamespace(returncode=0, stdout="octocat", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("platform_cli.core.update.run_cmd", _stub_run)
    ready, reason, login = update_mod._gh_ready_for_repo("owner/repo")
    assert ready is True
    assert reason == "ok"
    assert login == "octocat"


def test_resolve_github_token_falls_back_to_prompt(monkeypatch) -> None:
    monkeypatch.setenv("GHDP_TOKEN", "")
    monkeypatch.setenv("GH_TOKEN", "")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setattr("platform_cli.core.update._managed_install_marker_exists", lambda: False)
    monkeypatch.setattr("platform_cli.core.update._gh_ready_for_repo", lambda repo: (False, "gh_not_installed", ""))
    monkeypatch.setattr("platform_cli.core.update.typer.prompt", lambda *args, **kwargs: "pat-123")
    token = update_mod._resolve_github_token("owner/repo")
    assert token == "pat-123"


def test_resolve_github_token_uses_managed_auth_before_prompt(monkeypatch) -> None:
    monkeypatch.setenv("GHDP_TOKEN", "")
    monkeypatch.setenv("GH_TOKEN", "")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setattr("platform_cli.core.update._managed_install_marker_exists", lambda: True)
    monkeypatch.setattr("platform_cli.core.update._token_from_managed_auth_state", lambda: "managed-pat-123")
    monkeypatch.setattr(
        "platform_cli.core.update.typer.prompt",
        lambda *args, **kwargs: pytest.fail("prompt should not be used when managed auth is available"),
    )

    token = update_mod._resolve_github_token("owner/repo")
    assert token == "managed-pat-123"


def test_resolve_github_token_ignores_managed_auth_without_marker(monkeypatch) -> None:
    monkeypatch.setenv("GHDP_TOKEN", "")
    monkeypatch.setenv("GH_TOKEN", "")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setattr("platform_cli.core.update._managed_install_marker_exists", lambda: False)
    monkeypatch.setattr("platform_cli.core.update.typer.prompt", lambda *args, **kwargs: "pat-123")

    token = update_mod._resolve_github_token("owner/repo")
    assert token == "pat-123"


def test_resolve_github_token_managed_install_prompts_for_pat_after_local_auth_failure(monkeypatch) -> None:
    monkeypatch.setenv("GHDP_TOKEN", "")
    monkeypatch.setenv("GH_TOKEN", "")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setattr("platform_cli.core.update._managed_install_marker_exists", lambda: True)
    monkeypatch.setattr("platform_cli.core.update._token_from_managed_auth_state", lambda: "")
    monkeypatch.setattr("platform_cli.core.update.typer.prompt", lambda *args, **kwargs: "pat-123")

    token = update_mod._resolve_github_token("owner/repo")
    assert token == "pat-123"


def test_resolve_github_token_managed_install_shows_support_message_when_pat_not_provided(monkeypatch) -> None:
    monkeypatch.setenv("GHDP_TOKEN", "")
    monkeypatch.setenv("GH_TOKEN", "")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setattr("platform_cli.core.update._managed_install_marker_exists", lambda: True)
    monkeypatch.setattr("platform_cli.core.update._token_from_managed_auth_state", lambda: "")
    monkeypatch.setattr("platform_cli.core.update.typer.prompt", lambda *args, **kwargs: "")

    with pytest.raises(PlatformError) as exc:
        update_mod._resolve_github_token("owner/repo")

    assert "platform team" in str(exc.value)


def test_token_from_env_ignores_missing_values(monkeypatch) -> None:
    monkeypatch.delenv("GHDP_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    assert update_mod._token_from_env() == ""


def test_get_latest_tags_falls_back_to_http(monkeypatch) -> None:
    monkeypatch.setattr("platform_cli.core.update._get_latest_tags_via_gh", lambda repo: (None, None))
    monkeypatch.setenv("GHDP_TOKEN", "pat-123")
    monkeypatch.setattr("platform_cli.core.update._get_latest_stable_via_api", lambda repo, token: "v0.2.0")
    monkeypatch.setattr(
        "platform_cli.core.update._list_release_tags_via_api",
        lambda repo, limit, include_drafts, token: [
            update_mod.ReleaseTag(tag="v0.2.0-rc1", prerelease=True, draft=False, published_at=""),
        ],
    )

    stable, pre = update_mod._get_latest_tags("owner/repo", allow_prompt_token=False)
    assert stable == "v0.2.0"
    assert pre == "v0.2.0-rc1"


def test_get_latest_tags_via_gh_prefers_github_latest_release(monkeypatch) -> None:
    monkeypatch.setattr(update_mod, "_get_latest_stable_via_gh", lambda repo: "v0.2.0")
    monkeypatch.setattr(
        update_mod,
        "list_release_tags",
        lambda repo, limit=100, include_drafts=False: [
            update_mod.ReleaseTag(tag="v0.2.1", prerelease=False, draft=False, published_at=""),
            update_mod.ReleaseTag(tag="v0.2.0-rc1", prerelease=True, draft=False, published_at=""),
        ],
    )

    stable, pre = update_mod._get_latest_tags_via_gh("owner/repo")

    assert stable == "v0.2.0"
    assert pre == "v0.2.0-rc1"


def test_get_latest_tags_via_gh_falls_back_to_release_list_for_stable(monkeypatch) -> None:
    monkeypatch.setattr(update_mod, "_get_latest_stable_via_gh", lambda repo: None)
    monkeypatch.setattr(
        update_mod,
        "list_release_tags",
        lambda repo, limit=100, include_drafts=False: [
            update_mod.ReleaseTag(tag="v0.2.2-rc1", prerelease=True, draft=False, published_at=""),
            update_mod.ReleaseTag(tag="v0.2.1", prerelease=False, draft=False, published_at=""),
        ],
    )

    stable, pre = update_mod._get_latest_tags_via_gh("owner/repo")

    assert stable == "v0.2.1"
    assert pre == "v0.2.2-rc1"


def test_get_latest_tags_uses_http_latest_when_gh_latest_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(update_mod, "_get_latest_tags_via_gh", lambda repo: (None, "v0.2.2-rc1"))
    monkeypatch.setattr(update_mod, "list_release_tags", lambda repo, limit=100, include_drafts=False: [])
    monkeypatch.setenv("GHDP_TOKEN", "pat-123")
    monkeypatch.setattr(update_mod, "_get_latest_stable_via_api", lambda repo, token: "v0.2.0")
    monkeypatch.setattr(
        update_mod,
        "_list_release_tags_via_api",
        lambda repo, limit, include_drafts, token: [],
    )

    stable, pre = update_mod._get_latest_tags("owner/repo", allow_prompt_token=False)

    assert stable == "v0.2.0"
    assert pre == "v0.2.2-rc1"


def test_get_latest_tags_falls_back_to_release_list_when_github_latest_is_not_ghdp(monkeypatch) -> None:
    monkeypatch.setattr(update_mod, "_get_latest_tags_via_gh", lambda repo: (None, None))
    monkeypatch.setattr(
        update_mod,
        "list_release_tags",
        lambda repo, limit=100, include_drafts=False: [
            update_mod.ReleaseTag(tag="v0.2.2", prerelease=False, draft=False, published_at=""),
            update_mod.ReleaseTag(tag="v0.2.3-FeatureBranch", prerelease=True, draft=False, published_at=""),
        ],
    )
    monkeypatch.setenv("GHDP_TOKEN", "")
    monkeypatch.setattr(update_mod, "_token_from_managed_aws_secret", lambda: "")

    stable, pre = update_mod._get_latest_tags("owner/repo", allow_prompt_token=False)

    assert stable == "v0.2.2"
    assert pre == "v0.2.3-FeatureBranch"


def test_get_latest_tags_uses_api_release_list_when_http_latest_is_not_ghdp(monkeypatch) -> None:
    monkeypatch.setattr(update_mod, "_get_latest_tags_via_gh", lambda repo: (None, None))
    monkeypatch.setattr(update_mod, "list_release_tags", lambda repo, limit=100, include_drafts=False: [])
    monkeypatch.setenv("GHDP_TOKEN", "pat-123")
    monkeypatch.setattr(update_mod, "_get_latest_stable_via_api", lambda repo, token: None)
    monkeypatch.setattr(
        update_mod,
        "_list_release_tags_via_api",
        lambda repo, limit, include_drafts, token: [
            update_mod.ReleaseTag(tag="v0.2.2", prerelease=False, draft=False, published_at=""),
            update_mod.ReleaseTag(tag="v0.2.3-FeatureBranch", prerelease=True, draft=False, published_at=""),
        ],
    )

    stable, pre = update_mod._get_latest_tags("owner/repo", allow_prompt_token=False)

    assert stable == "v0.2.2"
    assert pre == "v0.2.3-FeatureBranch"


def test_extract_secret_value_supports_json_secret() -> None:
    token = extract_secret_value('{"token":"aws-pat-123"}', json_keys=("token",))
    assert token == "aws-pat-123"


def test_extract_most_important_release_note_prefers_first_bullet() -> None:
    body = """
    ## Changes
    - Fixed login issue
    - Added PAT fallback
    - Extra item
    """
    assert extract_most_important_release_note(body) == "Fixed login issue"


def test_extract_most_important_release_note_falls_back_to_first_sentence() -> None:
    body = "Fixed auth scope check. Added release install logging. Includes more diagnostics."
    assert extract_most_important_release_note(body) == "Fixed auth scope check"


def test_extract_release_summary_prefers_summary_section() -> None:
    body = """
    ## Metadata
    - tag: v1.2.3

    ## Summary
    - Added stable-only doctor update rule
    - Improved fallback token handling

    ## Install
    - Run installer
    """
    assert extract_release_summary(body) == (
        "- Added stable-only doctor update rule\n- Improved fallback token handling"
    )


def test_extract_release_summary_falls_back_to_most_important() -> None:
    body = """
    ## Changes
    - First bullet
    - Second bullet
    """
    assert extract_release_summary(body) == "First bullet"


def test_extract_release_summary_accepts_custom_section_names() -> None:
    body = """
    ## Highlights
    - Faster update checks

    ## Notes
    - Misc details
    """
    assert extract_release_summary(body, section_names=["Highlights"]) == "- Faster update checks"


def test_release_body_via_gh_reads_output_as_utf8(monkeypatch) -> None:
    seen = {}

    def _stub_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        seen["encoding"] = kwargs.get("encoding")
        seen["errors"] = kwargs.get("errors")
        return SimpleNamespace(returncode=0, stdout="body text")

    monkeypatch.setattr(update_mod, "_gh_available", lambda: True)
    monkeypatch.setattr(update_mod, "run_cmd", _stub_run)

    body = update_mod._release_body_via_gh("owner/repo", "v0.2.0")

    assert body == "body text"
    assert seen == {"encoding": "utf-8", "errors": "replace"}


def test_maybe_check_for_update_ignores_prerelease_for_plain_ghdp(monkeypatch) -> None:
    monkeypatch.setenv("GHDP_UPDATE_REPO", "owner/repo")
    monkeypatch.setattr(update_mod, "_CHECKED", False)
    monkeypatch.setattr(update_mod, "_gh_available", lambda: True)
    monkeypatch.setattr(update_mod, "get_tool_state", lambda name: {})
    state_updates = []
    monkeypatch.setattr(update_mod, "update_tool_state", lambda name, data: state_updates.append((name, data)))
    monkeypatch.setattr(update_mod, "_get_latest_tags", lambda repo, allow_prompt_token=False: ("v0.1.4", "v0.1.8-changeversion"))
    monkeypatch.setattr(update_mod, "__version__", "0.1.4")

    confirmed = []
    monkeypatch.setattr(update_mod.typer, "confirm", lambda *args, **kwargs: confirmed.append(args) or True)

    updated = update_mod.maybe_check_for_update(force=False)

    assert updated is False
    assert confirmed == []
    assert state_updates == [("ghdp", {"update_last_checked_at": state_updates[0][1]["update_last_checked_at"]})]


def test_maybe_check_for_update_force_only_prompts_for_stable(monkeypatch) -> None:
    monkeypatch.setenv("GHDP_UPDATE_REPO", "owner/repo")
    monkeypatch.setattr(update_mod, "_CHECKED", False)
    monkeypatch.setattr(update_mod, "_gh_available", lambda: True)
    monkeypatch.setattr(update_mod, "get_tool_state", lambda name: {})
    state_updates = []
    monkeypatch.setattr(update_mod, "update_tool_state", lambda name, data: state_updates.append((name, data)))
    monkeypatch.setattr(update_mod, "_get_latest_tags", lambda repo, allow_prompt_token=False: ("v0.1.4", "v0.1.8-changeversion"))
    monkeypatch.setattr(update_mod, "__version__", "0.1.4")

    prompts = []

    def _confirm(message, default=False):  # type: ignore[no-untyped-def]
        prompts.append(message)
        return False

    monkeypatch.setattr(update_mod.typer, "confirm", _confirm)

    updated = update_mod.maybe_check_for_update(force=True)

    assert updated is False
    assert prompts == []
    assert len(state_updates) == 1
    assert state_updates[0][0] == "ghdp"


def test_maybe_check_for_update_force_uses_auto_install_method(monkeypatch) -> None:
    monkeypatch.setenv("GHDP_UPDATE_REPO", "owner/repo")
    monkeypatch.setattr(update_mod, "_CHECKED", False)
    monkeypatch.setattr(update_mod, "_gh_available", lambda: True)
    monkeypatch.setattr(update_mod, "get_tool_state", lambda name: {})
    state_updates = []
    monkeypatch.setattr(update_mod, "update_tool_state", lambda name, data: state_updates.append((name, data)))
    monkeypatch.setattr(update_mod, "_get_latest_tags", lambda repo, allow_prompt_token=False: ("v0.1.6", "v0.1.4-installstrategy"))
    monkeypatch.setattr(update_mod, "__version__", "0.1.4-installstrategy")

    prompts = []

    def _confirm(message, default=False):  # type: ignore[no-untyped-def]
        prompts.append(message)
        return True

    installs = []

    def _install_selected_version(repo, tag, method="auto"):  # type: ignore[no-untyped-def]
        installs.append((repo, tag, method))
        return "pipx"

    monkeypatch.setattr(update_mod.typer, "confirm", _confirm)
    monkeypatch.setattr(update_mod, "install_selected_version", _install_selected_version)

    updated = update_mod.maybe_check_for_update(force=True)

    assert updated is True
    assert prompts == ["Install release v0.1.6 now?"]
    assert installs == [("owner/repo", "v0.1.6", "auto")]
    assert state_updates[0][0] == "ghdp"


def test_maybe_check_for_update_force_updates_prerelease_install_to_newer_stable(monkeypatch) -> None:
    monkeypatch.setenv("GHDP_UPDATE_REPO", "owner/repo")
    monkeypatch.setattr(update_mod, "_CHECKED", False)
    monkeypatch.setattr(update_mod, "_gh_available", lambda: True)
    monkeypatch.setattr(update_mod, "get_tool_state", lambda name: {})
    state_updates = []
    monkeypatch.setattr(update_mod, "update_tool_state", lambda name, data: state_updates.append((name, data)))
    monkeypatch.setattr(update_mod, "_get_latest_tags", lambda repo, allow_prompt_token=False: ("v0.1.6", None))
    monkeypatch.setattr(update_mod, "__version__", "0.1.4-ghdpdoctortest")

    prompts = []

    def _confirm(message, default=False):  # type: ignore[no-untyped-def]
        prompts.append(message)
        return True

    installs = []

    def _install_selected_version(repo, tag, method="auto"):  # type: ignore[no-untyped-def]
        installs.append((repo, tag, method))
        return "pipx"

    monkeypatch.setattr(update_mod.typer, "confirm", _confirm)
    monkeypatch.setattr(update_mod, "install_selected_version", _install_selected_version)

    updated = update_mod.maybe_check_for_update(force=True)

    assert updated is True
    assert prompts == ["Install release v0.1.6 now?"]
    assert installs == [("owner/repo", "v0.1.6", "auto")]
    assert state_updates[0][0] == "ghdp"


def test_maybe_check_for_update_force_reoffers_same_target_after_prior_notification(monkeypatch) -> None:
    monkeypatch.setenv("GHDP_UPDATE_REPO", "owner/repo")
    monkeypatch.setattr(update_mod, "_CHECKED", False)
    monkeypatch.setattr(update_mod, "_gh_available", lambda: True)
    monkeypatch.setattr(
        update_mod,
        "get_tool_state",
        lambda name: {"update_last_notified_tag": "v0.1.6"},
    )
    state_updates = []
    monkeypatch.setattr(update_mod, "update_tool_state", lambda name, data: state_updates.append((name, data)))
    monkeypatch.setattr(update_mod, "_get_latest_tags", lambda repo, allow_prompt_token=False: ("v0.1.6", None))
    monkeypatch.setattr(update_mod, "__version__", "0.1.5-ghdpdoctortest")

    prompts = []

    def _confirm(message, default=False):  # type: ignore[no-untyped-def]
        prompts.append(message)
        return True

    installs = []

    def _install_selected_version(repo, tag, method="auto"):  # type: ignore[no-untyped-def]
        installs.append((repo, tag, method))
        return "pipx"

    monkeypatch.setattr(update_mod.typer, "confirm", _confirm)
    monkeypatch.setattr(update_mod, "install_selected_version", _install_selected_version)

    updated = update_mod.maybe_check_for_update(force=True)

    assert updated is True
    assert prompts == ["Install release v0.1.6 now?"]
    assert installs == [("owner/repo", "v0.1.6", "auto")]
    assert state_updates[0][0] == "ghdp"


def test_maybe_check_for_update_uses_github_latest_stable_anchor(monkeypatch) -> None:
    monkeypatch.setenv("GHDP_UPDATE_REPO", "owner/repo")
    monkeypatch.setattr(update_mod, "_CHECKED", False)
    monkeypatch.setattr(update_mod, "_gh_available", lambda: True)
    monkeypatch.setattr(update_mod, "get_tool_state", lambda name: {})
    monkeypatch.setattr(update_mod, "update_tool_state", lambda name, data: None)
    monkeypatch.setattr(update_mod, "__version__", "0.2.0")
    monkeypatch.setattr(update_mod, "_get_latest_stable_via_gh", lambda repo: "v0.2.1")
    monkeypatch.setattr(
        update_mod,
        "list_release_tags",
        lambda repo, limit=100, include_drafts=False: [
            update_mod.ReleaseTag(tag="v0.2.3", prerelease=False, draft=False, published_at=""),
            update_mod.ReleaseTag(tag="v0.2.4-rc1", prerelease=True, draft=False, published_at=""),
        ],
    )

    prompts = []
    installs = []

    monkeypatch.setattr(update_mod.typer, "confirm", lambda message, default=False: prompts.append(message) or True)
    monkeypatch.setattr(
        update_mod,
        "install_selected_version",
        lambda repo, tag, method="auto": installs.append((repo, tag, method)) or "installer",
    )

    updated = update_mod.maybe_check_for_update(force=True)

    assert updated is True
    assert prompts == ["Install release v0.2.1 now?"]
    assert installs == [("owner/repo", "v0.2.1", "auto")]


def test_apply_update_via_release_binary_falls_back_to_gh_download(monkeypatch, tmp_path) -> None:
    install_dir = tmp_path / "bin"
    tmp_file = tmp_path / "download.tmp"

    monkeypatch.setattr(update_mod, "_release_asset_name", lambda: "ghdp-windows-amd64.exe")
    monkeypatch.setattr(update_mod, "_resolve_github_token", lambda repo: "gh-token")
    monkeypatch.setattr(update_mod, "_default_binary_install_dir", lambda: install_dir)
    monkeypatch.setattr(update_mod, "_ensure_runtime_env_exists", lambda: None)
    monkeypatch.setattr(update_mod, "_record_install_phase", lambda *args, **kwargs: None)
    monkeypatch.setattr(update_mod.tempfile, "mkstemp", lambda prefix, suffix: (99, str(tmp_file)))
    monkeypatch.setattr(update_mod.os, "close", lambda fd: None)
    monkeypatch.setattr(
        update_mod,
        "_swap_windows_binary_now",
        lambda **kwargs: kwargs["target"].write_bytes(kwargs["staged"].read_bytes()) or True,
    )
    monkeypatch.setattr(update_mod, "_verify_binary_version", lambda executable, expected_tag, **kwargs: True)

    gh_calls = []

    def _fail_api(repo, tag, asset, token, dest):  # type: ignore[no-untyped-def]
        raise PlatformError(
            "Failed to fetch release metadata for v0.1.5: ssl cert verify failed",
            code="E_UPDATE_INSTALL_FAILED",
            reason="release_metadata_fetch_failed",
        )

    def _gh_download(repo, tag, asset, dest):  # type: ignore[no-untyped-def]
        gh_calls.append((repo, tag, asset, dest))
        dest.write_bytes(b"binary")

    monkeypatch.setattr(update_mod, "_download_release_asset_via_api", _fail_api)
    monkeypatch.setattr(update_mod, "_download_release_asset_via_gh", _gh_download)
    monkeypatch.setattr(update_mod, "_gh_available", lambda: True)

    update_mod._apply_update_via_release_binary("owner/repo", "v0.1.5")

    assert gh_calls and gh_calls[0][0:3] == ("owner/repo", "v0.1.5", "ghdp-windows-amd64.exe")
    if update_mod.os.name != "nt":
        assert (install_dir / "ghdp").read_bytes() == b"binary"


def test_apply_update_via_installer_passes_resolved_token_and_managed_flag(monkeypatch, tmp_path) -> None:
    script = tmp_path / "install_ghdp.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    bundle = tmp_path / "managed-auth.env"
    bundle.write_text("GHDP_TOKEN=managed-pat-123\n", encoding="utf-8")

    calls = []

    def _fake_run(cmd, check=True, env=None, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((list(cmd), dict(env or {}), dict(kwargs)))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(update_mod, "_installer_script_path", lambda: script)
    monkeypatch.setattr(update_mod, "_resolve_github_token", lambda repo: "managed-pat-123")
    monkeypatch.setattr(update_mod, "_managed_install_marker_exists", lambda: True)
    monkeypatch.setenv("GHDP_MANAGED_AUTH_BUNDLE_PATH", str(bundle))
    monkeypatch.setattr(update_mod, "run_cmd", _fake_run)

    update_mod._apply_update_via_installer("owner/repo", "v0.1.5")

    assert calls
    cmd, env, kwargs = calls[0]
    if update_mod.os.name == "nt":
        assert cmd == ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    else:
        assert cmd == ["bash", str(script)]
    assert env["GHDP_REPO"] == "owner/repo"
    assert env["GHDP_VERSION"] == "v0.1.5"
    assert env["GHDP_TOKEN"] == "managed-pat-123"
    assert env["GHDP_MANAGED_INSTALL"] == "1"
    assert env["GHDP_MANAGED_AUTH_BUNDLE_PATH"] == str(bundle)
    assert kwargs == {}


def test_maybe_check_for_update_works_without_gh_when_release_token_path_exists(monkeypatch) -> None:
    monkeypatch.setenv("GHDP_UPDATE_REPO", "owner/repo")
    monkeypatch.setattr(update_mod, "_CHECKED", False)
    monkeypatch.setattr(update_mod, "_gh_available", lambda: False)
    monkeypatch.setattr(update_mod, "get_tool_state", lambda name: {})
    state_updates = []
    monkeypatch.setattr(update_mod, "update_tool_state", lambda name, data: state_updates.append((name, data)))
    monkeypatch.setattr(update_mod, "_get_latest_tags", lambda repo, allow_prompt_token=False: ("v0.1.6", None))
    monkeypatch.setattr(update_mod, "__version__", "0.1.4")

    prompts = []

    def _confirm(message, default=False):  # type: ignore[no-untyped-def]
        prompts.append(message)
        return False

    monkeypatch.setattr(update_mod.typer, "confirm", _confirm)

    updated = update_mod.maybe_check_for_update(force=False)

    assert updated is False
    assert prompts == ["Install release v0.1.6 now?"]
    assert len(state_updates) == 2
