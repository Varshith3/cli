from typer.testing import CliRunner

from platform_cli.cli import app
from platform_cli.commands import version_change as version_change_cmd
import platform_cli.core.update as update_mod
from platform_cli.core.update import InstallResult


runner = CliRunner()


def test_version_change_non_interactive_latest_stable_updates(monkeypatch) -> None:
    monkeypatch.setenv("GHDP_UPDATE_REPO", "owner/repo")
    monkeypatch.setattr(
        version_change_cmd,
        "resolve_latest_stable_target",
        lambda repo, allow_prompt_token=False: ("v0.1.0", "v0.2.0", True),
    )
    monkeypatch.setattr(
        version_change_cmd,
        "install_selected_version_detailed",
        lambda repo, tag, method="auto": InstallResult(
            method="installer",
            target_tag=tag,
            verification_status="verified",
            active_tag=tag,
        ),
    )

    result = runner.invoke(app, ["--non-interactive", "version", "change", "--latest-stable"])

    assert result.exit_code == 0
    assert "Installed latest stable GHDP v0.2.0 via installer." in result.stdout


def test_version_change_non_interactive_latest_stable_noop(monkeypatch) -> None:
    monkeypatch.setenv("GHDP_UPDATE_REPO", "owner/repo")
    monkeypatch.setattr(
        version_change_cmd,
        "resolve_latest_stable_target",
        lambda repo, allow_prompt_token=False: ("v0.2.0", "v0.2.0", False),
    )
    monkeypatch.setattr(
        version_change_cmd,
        "install_selected_version_detailed",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("install should not be called")),
    )

    result = runner.invoke(app, ["--non-interactive", "version", "change", "--latest-stable"])

    assert result.exit_code == 0
    assert "already on the latest stable release v0.2.0" in result.stdout


def test_version_change_non_interactive_requires_version_or_latest_stable(monkeypatch) -> None:
    monkeypatch.setenv("GHDP_UPDATE_REPO", "owner/repo")

    result = runner.invoke(app, ["--non-interactive", "version", "change"])

    assert result.exit_code == 1
    assert str(result.exception) == "--version or --latest-stable is required in non-interactive mode."


def test_version_change_interactive_defaults_to_latest_stable(monkeypatch) -> None:
    monkeypatch.setenv("GHDP_UPDATE_REPO", "owner/repo")
    monkeypatch.setattr(
        version_change_cmd,
        "resolve_latest_stable_target",
        lambda repo, allow_prompt_token=False: ("v0.1.0", "v0.2.0", True),
    )
    installs: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        version_change_cmd,
        "install_selected_version_detailed",
        lambda repo, tag, method="auto": installs.append((repo, tag, method))
        or InstallResult(method="installer", target_tag=tag, verification_status="verified", active_tag=tag),
    )

    result = runner.invoke(app, ["version", "change"], input="1\ny\n")

    assert result.exit_code == 0
    assert installs == [("owner/repo", "v0.2.0", "auto")]
    assert "Installed latest stable GHDP v0.2.0 via installer." in result.stdout


def test_version_change_interactive_can_pick_specific_release(monkeypatch) -> None:
    monkeypatch.setenv("GHDP_UPDATE_REPO", "owner/repo")
    monkeypatch.setattr(
        version_change_cmd,
        "resolve_latest_stable_target",
        lambda repo, allow_prompt_token=False: ("v0.1.0", "v0.2.0", True),
    )
    monkeypatch.setattr(
        version_change_cmd,
        "list_release_tags",
        lambda repo, limit=30, include_drafts=False: [
            type("Rel", (), {"tag": "v0.3.0-rc1", "prerelease": True})(),
            type("Rel", (), {"tag": "v0.2.0", "prerelease": False})(),
        ],
    )
    installs: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        version_change_cmd,
        "install_selected_version_detailed",
        lambda repo, tag, method="auto": installs.append((repo, tag, method))
        or InstallResult(method="installer", target_tag=tag, verification_status="verified", active_tag=tag),
    )

    result = runner.invoke(app, ["version", "change"], input="2\n1\n")

    assert result.exit_code == 0
    assert installs == [("owner/repo", "v0.3.0-rc1", "auto")]
    assert "Installed GHDP v0.3.0-rc1 via installer." in result.stdout


def test_version_change_non_interactive_latest_stable_pending_swap(monkeypatch) -> None:
    monkeypatch.setenv("GHDP_UPDATE_REPO", "owner/repo")
    monkeypatch.setattr(
        version_change_cmd,
        "resolve_latest_stable_target",
        lambda repo, allow_prompt_token=False: ("v0.1.0", "v0.2.0", True),
    )
    monkeypatch.setattr(
        version_change_cmd,
        "install_selected_version_detailed",
        lambda repo, tag, method="auto": InstallResult(
            method="installer",
            target_tag=tag,
            verification_status="pending_swap",
            active_tag="v0.1.0",
        ),
    )

    result = runner.invoke(app, ["--non-interactive", "version", "change", "--latest-stable"])

    assert result.exit_code == 0
    assert "active binary swap is \nstill pending on Windows" in result.stdout


def test_version_change_non_interactive_latest_stable_uses_github_latest_anchor(monkeypatch) -> None:
    monkeypatch.setenv("GHDP_UPDATE_REPO", "owner/repo")
    monkeypatch.setattr(version_change_cmd, "resolve_latest_stable_target", update_mod.resolve_latest_stable_target)
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
    monkeypatch.setattr(
        version_change_cmd,
        "install_selected_version_detailed",
        lambda repo, tag, method="auto": InstallResult(
            method="installer",
            target_tag=tag,
            verification_status="verified",
            active_tag=tag,
        ),
    )

    result = runner.invoke(app, ["--non-interactive", "version", "change", "--latest-stable"])

    assert result.exit_code == 0
    assert "Installed latest stable GHDP v0.2.1 via installer." in result.stdout
