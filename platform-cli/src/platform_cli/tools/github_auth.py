# NOTE: Architectural rules in ARCHITECTURE.md -- do not refactor cross-layer.
from __future__ import annotations

import os
import shutil
import sys
import time
import typer

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.errors import PlatformError
from platform_cli.core.github_auth import gh_subprocess_env, is_managed_install, managed_install_token
from platform_cli.exec.runner import run_cmd
from platform_cli.state.store import update_tool_state

StatusPrinter = callable


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _resolve_gh_exe() -> str | None:
    if not _is_windows():
        return shutil.which("gh") or "gh"

    try:
        res = run_cmd(["where.exe", "gh"], check=False, capture=True)
        out = (res.stdout or "").strip()
        if out:
            return out.splitlines()[0].strip()
    except Exception:
        pass

    install_dir = ""
    try:
        res = run_cmd(["winget", "show", "--id", "GitHub.cli", "-e"], check=False, capture=True)
        txt = (res.stdout or "")
        for line in txt.splitlines():
            if line.lower().startswith("install location:"):
                install_dir = line.split(":", 1)[1].strip()
                break
    except Exception:
        install_dir = ""

    def _find_gh_exe_in_dir(path: str) -> str | None:
        if not path:
            return None
        try:
            probe = run_cmd(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        f"Get-ChildItem -Path '{path}' -Recurse -Filter gh.exe "
                        f"-ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName"
                    ),
                ],
                check=False,
                capture=True,
            )
            resolved = (probe.stdout or "").strip()
            return resolved if resolved else None
        except Exception:
            return None

    if install_dir:
        resolved = _find_gh_exe_in_dir(install_dir)
        if resolved:
            return resolved

    try:
        localapp = os.environ.get("LOCALAPPDATA", "")
        winget_pkgs = os.path.join(localapp, "Microsoft", "WinGet", "Packages")
        resolved = _find_gh_exe_in_dir(winget_pkgs)
        if resolved:
            return resolved
    except Exception:
        pass

    return None


def _github_auth_status_ok(gh_exe: str) -> bool:
    try:
        run_cmd([gh_exe, "auth", "status"], check=True, capture=True, env=gh_subprocess_env())
        return True
    except Exception:
        return False


def _emit_status(status_printer, message: str) -> None:
    if status_printer is not None:
        status_printer(message)


def ensure_github_authenticated(*, force: bool = False, status_printer=None) -> None:
    if is_managed_install():
        token = managed_install_token()
        if token:
            _emit_status(status_printer, "Managed GitHub auth is configured; skipping interactive GitHub CLI login.")
            return

    gh_exe = _resolve_gh_exe()
    if not gh_exe:
        raise PlatformError(
            "GitHub CLI was installed but is not yet available in this terminal session. "
            "Close and reopen your terminal, then rerun `ghdp tools install --tool gh`.",
            code="E_GH_NOT_AVAILABLE_YET",
            reason="gh",
        )

    if not force and _github_auth_status_ok(gh_exe):
        _emit_status(status_printer, "GitHub CLI is already authenticated...")
        return

    if bool(cli_ctx.non_interactive):
        raise PlatformError(
            "GitHub CLI is installed but not authenticated yet. Run GHDP interactively once so it can start `gh auth login`.",
            code="E_GH_AUTH_NEEDS_INTERACTIVE",
            reason="gh_auth",
        )

    _emit_status(status_printer, "Opening GitHub CLI login...")
    typer.echo("")
    typer.echo("GitHub authentication is required for GHDP access checks and synced content refresh.")
    typer.echo(f"Opening GitHub CLI login now ({os.path.basename(gh_exe)} auth login)...")
    typer.echo("")

    run_cmd([gh_exe, "auth", "login"], check=True, capture=False)
    _emit_status(status_printer, "Verifying GitHub CLI authentication...")

    if not _github_auth_status_ok(gh_exe):
        raise PlatformError(
            "GitHub login was initiated but `gh auth status` still fails. Retry the GHDP install step once login is complete.",
            code="E_GH_AUTH_INCOMPLETE",
            reason="gh_auth",
        )


def maybe_bootstrap_after_install(*, status_printer=None) -> None:
    update_tool_state("gh", {"gh_auth_post_step_attempted_at": int(time.time())})
    ensure_github_authenticated(force=False, status_printer=status_printer)
