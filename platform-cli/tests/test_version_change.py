import pytest

from platform_cli.commands import version_change as version_change_mod
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.errors import PlatformError
from platform_cli.core.update import InstallResult


def test_version_change_interactive_latest_stable_path(monkeypatch, capsys):
    installs = []

    monkeypatch.setattr(version_change_mod, "_resolved_repo", lambda repo: "owner/repo")
    monkeypatch.setattr(
        version_change_mod,
        "resolve_latest_stable_target",
        lambda repo, allow_prompt_token=False: ("v0.1.0", "v0.2.0", True),
    )
    monkeypatch.setattr(version_change_mod.typer, "prompt", lambda *args, **kwargs: "1")
    monkeypatch.setattr(version_change_mod.typer, "confirm", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        version_change_mod,
        "install_selected_version_detailed",
        lambda repo, tag, method="auto": installs.append((repo, tag, method))
        or InstallResult(method="installer", target_tag=tag, verification_status="verified", active_tag=tag),
    )
    cli_ctx.non_interactive = False

    version_change_mod._run_version_change(version=None, latest_stable=False, method="auto", repo=None)
    output = capsys.readouterr().out

    assert installs == [("owner/repo", "v0.2.0", "auto")]
    assert "Installed latest stable GHDP v0.2.0 via installer." in output


def test_version_change_non_interactive_latest_stable_skips_prompt(monkeypatch):
    installs = []

    monkeypatch.setattr(version_change_mod, "_resolved_repo", lambda repo: "owner/repo")
    monkeypatch.setattr(
        version_change_mod,
        "resolve_latest_stable_target",
        lambda repo, allow_prompt_token=False: ("v0.1.0", "v0.2.0", True),
    )
    monkeypatch.setattr(
        version_change_mod,
        "install_selected_version_detailed",
        lambda repo, tag, method="auto": installs.append((repo, tag, method))
        or InstallResult(method="installer", target_tag=tag, verification_status="verified", active_tag=tag),
    )
    cli_ctx.non_interactive = True

    version_change_mod._run_version_change(version=None, latest_stable=True, method="auto", repo=None)

    assert installs == [("owner/repo", "v0.2.0", "auto")]


def test_version_change_latest_stable_reports_up_to_date(monkeypatch, capsys):
    monkeypatch.setattr(version_change_mod, "_resolved_repo", lambda repo: "owner/repo")
    monkeypatch.setattr(
        version_change_mod,
        "resolve_latest_stable_target",
        lambda repo, allow_prompt_token=False: ("v0.2.0", "v0.2.0", False),
    )
    cli_ctx.non_interactive = True

    version_change_mod._run_version_change(version=None, latest_stable=True, method="auto", repo=None)
    output = capsys.readouterr().out

    assert "already on the latest stable release v0.2.0" in output


def test_version_change_latest_stable_reports_pending_swap(monkeypatch, capsys):
    monkeypatch.setattr(version_change_mod, "_resolved_repo", lambda repo: "owner/repo")
    monkeypatch.setattr(
        version_change_mod,
        "resolve_latest_stable_target",
        lambda repo, allow_prompt_token=False: ("v0.1.0", "v0.2.0", True),
    )
    monkeypatch.setattr(version_change_mod.typer, "confirm", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        version_change_mod,
        "install_selected_version_detailed",
        lambda repo, tag, method="auto": InstallResult(
            method="installer",
            target_tag=tag,
            verification_status="pending_swap",
            active_tag="v0.1.0",
        ),
    )
    cli_ctx.non_interactive = False

    version_change_mod._run_version_change(version=None, latest_stable=True, method="auto", repo=None)
    output = capsys.readouterr().out

    assert "active binary swap is \nstill pending on Windows" in output


def test_version_change_rejects_version_and_latest_stable_together(monkeypatch):
    monkeypatch.setattr(version_change_mod, "_resolved_repo", lambda repo: "owner/repo")
    cli_ctx.non_interactive = False

    with pytest.raises(PlatformError) as exc:
        version_change_mod._run_version_change(version="v0.2.0", latest_stable=True, method="auto", repo=None)

    assert "Use either --version or --latest-stable" in str(exc.value)
