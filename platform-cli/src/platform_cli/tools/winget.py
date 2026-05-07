# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
"""Windows WinGet bootstrap helpers.

Goal (DX):
  - `ghdp tools install` should not assume winget exists.
  - If winget is missing on Windows 10/11, attempt a best-effort bootstrap.

Implementation follows Microsoft Learn guidance:
  1) Ask Windows to register App Installer (DesktopAppInstaller family)
  2) If still missing, use PowerShell module `Microsoft.WinGet.Client` +
     `Repair-WinGetPackageManager` to bootstrap WinGet.
"""

from __future__ import annotations

import shutil
import sys
from typing import Iterable, Tuple

from platform_cli.exec.runner import run_cmd

try:
    from platform_cli.core.errors import PlatformError  # type: ignore
except Exception:  # pragma: no cover
    class PlatformError(RuntimeError):
        def __init__(self, message: str, code: str = "E_INTERNAL", reason: str = "UNKNOWN", alert: bool = False):
            super().__init__(message)
            self.code = code
            self.reason = reason
            self.alert = alert


MIN_WIN10_BUILD = 17763  # Windows 10 1809
DEFAULT_WINGET_SOURCES = ("winget", "msstore")


def is_windows() -> bool:
    return sys.platform.startswith("win")


def windows_build_number() -> int:
    """Return Windows build number if available (else 0)."""
    try:
        return int(getattr(sys.getwindowsversion(), "build", 0))  # type: ignore[attr-defined]
    except Exception:
        return 0


def winget_path() -> str:
    return shutil.which("winget") or ""


def winget_version() -> str:
    try:
        res = run_cmd(["winget", "--version"], check=False)
        return (res.stdout or res.stderr or "").strip()
    except Exception:
        return ""


def _ps(cmd: str, *, check: bool = False) -> Tuple[int, str, str]:
    """Run a PowerShell one-liner. Returns (rc, stdout, stderr)."""
    res = run_cmd(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            cmd,
        ],
        check=check,
    )
    return res.returncode, res.stdout, res.stderr


def _source_probe_cmd(source: str) -> list[str]:
    return [
        "winget",
        "list",
        "--source",
        source,
        "--accept-source-agreements",
        "--disable-interactivity",
    ]


def accept_winget_source_agreements(*, sources: Iterable[str] = DEFAULT_WINGET_SOURCES) -> None:
    """
    Best-effort agreement acceptance for configured sources before installs begin.

    Some Windows environments prompt for source agreements the first time a source
    is touched, which makes GHDP installs look hung or fail before any package
    work starts. We proactively touch the common sources with the accept flag so
    later detect/install commands stay non-interactive.
    """

    if not is_windows():
        return

    for source in sources:
        try:
            run_cmd(_source_probe_cmd(source), check=False)
        except Exception:
            # This is a pre-hook only; do not block installs if a source is
            # unavailable on a specific machine.
            continue


def ensure_winget_ready(*, allow_repair: bool = True) -> None:
    ensure_winget(allow_repair=allow_repair)
    accept_winget_source_agreements()


def ensure_winget(*, allow_repair: bool = True) -> None:
    """Ensure `winget` is callable on Windows.

    - If winget is already present: no-op.
    - If OS is unsupported: raise a structured PlatformError.
    - If missing: attempt best-effort registration + repair.
      If still missing, raise a structured PlatformError with actionable guidance.
    """

    if not is_windows():
        return

    build = windows_build_number()
    if build and build < MIN_WIN10_BUILD:
        raise PlatformError(
            f"WinGet is not supported on this Windows build ({build}). "
            f"Requires Windows 10 version 1809 (build {MIN_WIN10_BUILD}) or newer.",
            code="E_WINGET_UNSUPPORTED",
            reason=str(build),
        )

    if winget_path():
        return

    # 1) Ask Windows to register App Installer (covers first-login async registration cases).
    _ps(
        "Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe",
        check=False,
    )

    if winget_path():
        return

    if not allow_repair:
        raise PlatformError(
            "WinGet (winget.exe) was not found on PATH.",
            code="E_WINGET_NOT_FOUND",
            reason="missing",
        )

    # 2) Best-effort repair/bootstrap using Microsoft.WinGet.Client PowerShell module.
    #    Uses CurrentUser scope (works without admin). If running as admin, also tries -AllUsers.
    bootstrap = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# Ensure NuGet provider exists (needed for PSGallery installs)
try { Install-PackageProvider -Name NuGet -Force | Out-Null } catch { }

# Install Microsoft.WinGet.Client PowerShell module (user scope by default)
try {
  if (-not (Get-Module -ListAvailable -Name Microsoft.WinGet.Client)) {
    Install-Module -Name Microsoft.WinGet.Client -Force -Repository PSGallery -Scope CurrentUser | Out-Null
  }
} catch {
  # If policies block module install, we'll fail later with guidance
}

# Repair/Bootstrap WinGet
try {
  Repair-WinGetPackageManager | Out-Null
} catch {
  $isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  if ($isAdmin) {
    try { Repair-WinGetPackageManager -AllUsers | Out-Null } catch { }
  }
}
"""
    _ps(bootstrap, check=False)

    if winget_path():
        return

    raise PlatformError(
        "WinGet (winget.exe) is required on Windows for GHDP tool installation, but it is not available. "
        "Install/Update 'App Installer' (Microsoft.DesktopAppInstaller) and ensure the winget app execution alias is enabled, "
        "Or install it by following the instructions from this link: https://learn.microsoft.com/en-us/windows/package-manager/winget/"
        "then retry the command.",
        code="E_WINGET_NOT_AVAILABLE",
        reason="missing_after_repair",
    )
