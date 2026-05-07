# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
# src/platform_cli/tools/jira_sso.py
from __future__ import annotations

import os
import sys
import time
import typer

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.errors import PlatformError
from platform_cli.exec.runner import run_cmd
from platform_cli.state.store import update_tool_state


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _resolve_acli_exe() -> str | None:
    """
    Windows note:
      WinGet may install ACLI and add an alias ("acli") that is NOT visible to the
      current PowerShell process immediately. Waiting may not help.

      So resolution order:
        1) where.exe acli (PATH/alias)
        2) winget show -> Install Location, search for acli.exe
        3) fallback: check common WinGet Packages path
    mac/linux:
      return "acli" (PATH is reliable after brew install)
    """
    if not _is_windows():
        return "acli"

    # 1) where.exe (best-case)
    try:
        res = run_cmd(["where.exe", "acli"], check=False, capture=True)
        out = (res.stdout or "").strip()
        if out:
            return out.splitlines()[0].strip()
    except Exception:
        pass

    # 2) winget show -> Install Location (best-effort)
    install_dir = ""
    try:
        res = run_cmd(["winget", "show", "--id", "Atlassian.AtlassianCLI", "-e"], check=False, capture=True)
        txt = (res.stdout or "")
        for line in txt.splitlines():
            if line.lower().startswith("install location:"):
                install_dir = line.split(":", 1)[1].strip()
                break
    except Exception:
        install_dir = ""

    def _find_acli_exe_in_dir(path: str) -> str | None:
        if not path:
            return None
        try:
            probe = run_cmd(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        f"Get-ChildItem -Path '{path}' -Recurse -Filter acli.exe "
                        f"-ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName"
                    ),
                ],
                check=False,
                capture=True,
            )
            p = (probe.stdout or "").strip()
            return p if p else None
        except Exception:
            return None

    if install_dir:
        p = _find_acli_exe_in_dir(install_dir)
        if p:
            return p

    # 3) fallback: search WinGet packages dir (user context)
    # Usually: %LOCALAPPDATA%\Microsoft\WinGet\Packages\
    try:
        localapp = os.environ.get("LOCALAPPDATA", "")
        winget_pkgs = os.path.join(localapp, "Microsoft", "WinGet", "Packages")
        p = _find_acli_exe_in_dir(winget_pkgs)
        if p:
            return p
    except Exception:
        pass

    return None


def _jira_auth_status_ok(acli_exe: str) -> bool:
    """
    Must check auth status first to avoid re-authing / creating fresh sessions/tokens unnecessarily.
    """
    try:
        run_cmd([acli_exe, "jira", "auth", "status"], check=True, capture=True)
        return True
    except Exception:
        return False


def ensure_jira_authenticated(*, force: bool = False) -> None:
    """
    Ensure Jira authentication exists for ACLI.

    Behavior:
      - Resolve executable path robustly on Windows (don't rely on alias visibility)
      - Check `acli jira auth status` FIRST (skip if already authenticated unless force=True)
      - If missing:
          - interactive -> `acli jira auth login --web` (opens browser)
          - non-interactive -> error with remediation
    """
    acli_exe = _resolve_acli_exe()
    if not acli_exe:
        # Don't force user to run manual auth; instruct to rerun GHDP after new terminal if needed.
        raise PlatformError(
            "ACLI was installed but is not yet available in this terminal session "
            "(Windows alias/PATH refresh issue). Close & reopen your terminal, then rerun:\n"
            "  ghdp tools install --team platform --tool acli\n"
            "GHDP will automatically complete the browser login.",
            code="E_ACLI_NOT_AVAILABLE_YET",
            reason="acli",
        )

    if not force and _jira_auth_status_ok(acli_exe):
        typer.echo("✅ Jira auth already set up (acli jira auth status OK). Skipping login.")
        return

    if bool(cli_ctx.non_interactive):
        raise PlatformError(
            "Atlassian CLI (acli) is installed but Jira authentication is not set up yet. "
            "Run GHDP interactively once so it can open the browser login.",
            code="E_JIRA_AUTH_NEEDS_INTERACTIVE",
            reason="jira_auth",
        )

    typer.echo("")
    typer.echo("🔐 Jira authentication is required to validate Jira keys from your laptop.")
    typer.echo(f"Opening browser-based login now ({os.path.basename(acli_exe)} jira auth login --web)...")
    typer.echo("")

    run_cmd([acli_exe, "jira", "auth", "login", "--web"], check=True, capture=False)

    if not _jira_auth_status_ok(acli_exe):
        raise PlatformError(
            "Jira login was initiated but auth status still fails. "
            "Retry the GHDP install step (it will re-check status and open login if needed).",
            code="E_JIRA_AUTH_INCOMPLETE",
            reason="jira_auth",
        )


def maybe_bootstrap_after_install() -> None:
    """
    Post-install step for `acli` (mirrors AWS SSO pattern):
      - Check Jira auth status
      - Only if missing -> open browser login
    """
    update_tool_state("acli", {"jira_auth_post_step_attempted_at": int(time.time())})
    ensure_jira_authenticated(force=False)
