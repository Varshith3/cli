# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

import typer

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.errors import PlatformError
from platform_cli.exec.runner import run_cmd
from platform_cli.state.store import update_tool_state
from platform_cli.tools.codex_skill_sync import sync_aws_readonly_skill


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _find_codex_from_where() -> Optional[str]:
    if not _is_windows():
        p = shutil.which("codex")
        return p if p else None

    try:
        res = run_cmd(["where.exe", "codex"], check=False, capture=True)
        out = (res.stdout or "").strip()
        if not out:
            return None
        for line in out.splitlines():
            candidate = line.strip()
            if candidate and Path(candidate).exists():
                return candidate
    except Exception:
        return None
    return None


def _find_codex_from_winget_links() -> Optional[str]:
    localapp = os.environ.get("LOCALAPPDATA", "")
    if not localapp:
        return None
    p = Path(localapp) / "Microsoft" / "WinGet" / "Links" / "codex.exe"
    return str(p) if p.exists() else None


def _latest_winget_codex_package_dir() -> Optional[Path]:
    localapp = os.environ.get("LOCALAPPDATA", "")
    if not localapp:
        return None

    root = Path(localapp) / "Microsoft" / "WinGet" / "Packages"
    if not root.exists():
        return None

    candidates = [p for p in root.glob("OpenAI.Codex_*") if p.is_dir()]
    if not candidates:
        return None

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _repair_windows_codex_shim(pkg_dir: Path) -> Optional[str]:
    codex_exe = pkg_dir / "codex.exe"
    if codex_exe.exists():
        return str(codex_exe)

    source = pkg_dir / "codex-x86_64-pc-windows-msvc.exe"
    if not source.exists():
        matches = sorted(pkg_dir.glob("codex-*-pc-windows-msvc.exe"))
        source = matches[0] if matches else source

    if source.exists():
        try:
            shutil.copyfile(source, codex_exe)
            if codex_exe.exists():
                return str(codex_exe)
        except Exception:
            return str(source)
        return str(source)

    return None


def _resolve_codex_exe() -> str:
    if not _is_windows():
        p = _find_codex_from_where()
        if p:
            return p

        for candidate in (
            str(Path.home() / ".local" / "bin" / "codex"),
            "/opt/homebrew/bin/codex",
            "/usr/local/bin/codex",
        ):
            if Path(candidate).exists():
                return candidate
        raise PlatformError(
            "Codex executable was not found on PATH after installation.",
            code="E_CODEX_NOT_FOUND",
            reason="codex",
        )

    # Windows: prefer WinGet install locations first, then fallback to PATH lookup.
    p = _find_codex_from_winget_links()
    if p:
        return p

    pkg_dir = _latest_winget_codex_package_dir()
    if pkg_dir:
        repaired = _repair_windows_codex_shim(pkg_dir)
        if repaired:
            return repaired

    p = _find_codex_from_where()
    if p:
        return p

    raise PlatformError(
        "Codex was installed but is not available in this session yet.",
        code="E_CODEX_NOT_AVAILABLE_YET",
        reason="codex",
    )


def _codex_version(codex_exe: str) -> str:
    res = run_cmd([codex_exe, "--version"], check=False, capture=True)
    out = (res.stdout or res.stderr or "").strip()
    if out:
        return out.splitlines()[0].strip()
    if res.returncode != 0:
        raise PlatformError(
            "Unable to read Codex version after install.",
            code="E_CODEX_VERSION_CHECK_FAILED",
            reason="codex",
        )
    return ""


def _codex_login_status(codex_exe: str) -> Tuple[bool, str]:
    res = run_cmd([codex_exe, "login", "status"], check=False, capture=True)
    txt = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
    low = txt.lower()

    if "not logged in" in low or "logged out" in low:
        return False, txt
    if "logged in" in low and res.returncode == 0:
        return True, txt
    if res.returncode == 0 and txt:
        return True, txt

    return False, txt


def maybe_bootstrap_after_install() -> None:
    """
    Post-install step for codex:
      1) Resolve executable even if PATH alias is stale in current shell.
      2) Verify version.
      3) Sync global AWS read-only Codex skill.
      4) Trigger `codex login` only when not already logged in.
    """
    update_tool_state("codex", {"codex_post_step_attempted_at": int(time.time())})

    codex_exe = _resolve_codex_exe()
    version = _codex_version(codex_exe)
    update_tool_state("codex", {"codex_exe": codex_exe, "codex_version": version})

    try:
        sync = sync_aws_readonly_skill()
        update_tool_state(
            "codex",
            {
                "codex_skill_sync_state": "ok",
                "codex_skill_sync_name": str(sync.get("skill_name", "")),
                "codex_skill_sync_path": str(sync.get("target_path", "")),
                "codex_skill_sync_file_count": int(sync.get("file_count", 0)),
                "codex_skill_sync_updated_count": int(sync.get("updated_count", 0)),
                "codex_skill_sync_hash": str(sync.get("content_hash", "")),
                "codex_skill_sync_source": str(sync.get("source", "")),
                "codex_skill_sync_release_repo": str(sync.get("release_repo", "")),
                "codex_skill_sync_release_tag": str(sync.get("release_tag", "")),
                "codex_skill_sync_content_version": str(sync.get("content_version", "")),
                "codex_skill_sync_at": int(sync.get("synced_at", int(time.time()))),
            },
        )
    except PlatformError as e:
        update_tool_state(
            "codex",
            {
                "codex_skill_sync_state": "error",
                "codex_skill_sync_error": str(e),
                "codex_skill_sync_error_code": getattr(e, "code", "E_CODEX_SKILL_SYNC_FAILED"),
                "codex_skill_sync_at": int(time.time()),
            },
        )
        raise

    logged_in, status = _codex_login_status(codex_exe)
    if logged_in:
        update_tool_state(
            "codex",
            {
                "codex_login_state": "ok",
                "codex_login_status": status,
                "codex_login_last_checked_at": int(time.time()),
            },
        )
        return

    if bool(cli_ctx.non_interactive):
        update_tool_state(
            "codex",
            {
                "codex_login_state": "deferred",
                "codex_login_status": status,
                "codex_login_deferred_reason": "non_interactive",
            },
        )
        return

    typer.echo("")
    typer.echo("Codex is installed. Opening browser login now...")
    typer.echo("")
    run_cmd([codex_exe, "login"], check=True, capture=False)

    logged_in_after, status_after = _codex_login_status(codex_exe)
    if not logged_in_after:
        raise PlatformError(
            "Codex login was launched but login status is still not authenticated.",
            code="E_CODEX_LOGIN_INCOMPLETE",
            reason="codex",
        )

    update_tool_state(
        "codex",
        {
            "codex_login_state": "ok",
            "codex_login_status": status_after,
            "codex_login_last_checked_at": int(time.time()),
        },
    )
