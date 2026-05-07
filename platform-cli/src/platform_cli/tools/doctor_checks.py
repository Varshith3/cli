# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
from __future__ import annotations

import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from platform_cli.exec.runner import run_cmd
from platform_cli.core.github_auth import inspect_github_auth
from platform_cli.manifests.load import current_platform_key, load_manifests
from platform_cli.tools.service import ToolRuntimeSpec
from platform_cli.tools.winget import winget_path, winget_version


def _version_text(cmd: list[str], *, timeout_s: int = 5) -> str:
    try:
        res = run_cmd(cmd, check=False, timeout_s=timeout_s)
    except Exception:
        return ""

    raw = (res.stdout or res.stderr or "").strip()
    return raw.splitlines()[0].strip() if raw else ""


def binary_status(cmd: str, *, version_cmd: list[str] | None = None, timeout_s: int = 5) -> str:
    path = shutil.which(cmd)
    if not path:
        return "-"

    version = _version_text(version_cmd or [cmd, "--version"], timeout_s=timeout_s)
    return f"{path} ({version})" if version else path


def _tool_spec_from_registry(name: str) -> ToolRuntimeSpec | None:
    try:
        _, registry, _ = load_manifests()
    except Exception:
        return None

    reg = registry.get("tools", {}).get(name)
    if not isinstance(reg, dict):
        return None

    os_key = current_platform_key()
    plat = reg.get("platforms", {}).get(os_key, {})
    brew = reg.get("brew", {}) if isinstance(reg.get("brew", {}), dict) else {}
    winget = reg.get("winget", {}) if isinstance(reg.get("winget", {}), dict) else {}
    choco = reg.get("choco", {}) if isinstance(reg.get("choco", {}), dict) else {}

    return ToolRuntimeSpec(
        name=name,
        display_name=reg.get("display_name", name),
        detect_cmd=list(reg.get("detect_cmd", [])),
        version_cmd=list(reg.get("version_cmd", [])) if reg.get("version_cmd") else None,
        install_cmd=list(plat.get("install", [])),
        upgrade_cmd=list(plat.get("upgrade", [])) if plat.get("upgrade") else None,
        uninstall_cmd=list(plat.get("uninstall", [])) if plat.get("uninstall") else None,
        version_req=None,
        bin_name=reg.get("bin"),
        manager=reg.get("manager"),
        brew_formula=brew.get("formula"),
        brew_cask=brew.get("cask"),
        winget_id=winget.get("id"),
        choco_package=choco.get("package"),
        darwin_app_path=reg.get("darwin_app_path"),
    )


def tool_status(name: str) -> str:
    spec = _tool_spec_from_registry(name)
    if spec is None:
        return binary_status(name)

    managed_version = ""
    try:
        detected = run_cmd(spec.detect_cmd, check=False, timeout_s=5)
        if detected.returncode == 0 and spec.version_cmd:
            managed_version = _version_text(spec.version_cmd, timeout_s=5)
    except Exception:
        managed_version = ""

    active_path = shutil.which(spec.bin_name or name) or ""
    active_version = _version_text([spec.bin_name or name, "--version"], timeout_s=5) if active_path else ""
    app_path = spec.darwin_app_path or ""
    app_present = bool(app_path) and Path(app_path).exists()

    parts: list[str] = []
    if managed_version:
        parts.append(f"managed={managed_version}")

    if active_path:
        active = active_path
        if active_version and active_version != managed_version:
            active = f"{active_path} ({active_version})"
        parts.append(f"active={active}")

    if app_present and app_path and not active_path:
        parts.append(f"app={app_path}")

    if parts:
        return " | ".join(parts)
    if active_path:
        return active_path
    if app_present:
        return app_path
    return "-"


def doctor_payload() -> list[dict[str, Any]]:
    auth_state = inspect_github_auth()
    rows: list[dict[str, Any]] = [
        {"check": "Platform", "value": platform.platform()},
        {"check": "Python", "value": platform.python_version()},
        {"check": "Python executable", "value": sys.executable},
        {"check": "install flavor", "value": auth_state.install_flavor},
        {"check": "auth mode", "value": auth_state.auth_mode},
        {"check": "managed auth", "value": auth_state.managed_auth_status},
        {"check": "github auth source", "value": auth_state.effective_github_auth_source},
    ]

    if sys.platform.startswith("darwin"):
        rows.append({"check": "brew", "value": binary_status("brew")})
    elif sys.platform.startswith("win"):
        rows.append({"check": "winget", "value": winget_path() or "-"})
        rows.append({"check": "winget version", "value": winget_version() or "-"})

    rows.extend(
        [
            {"check": "git", "value": tool_status("git")},
            {"check": "terraform", "value": tool_status("terraform")},
            {"check": "gh", "value": tool_status("gh")},
            {"check": "pipx", "value": binary_status("pipx")},
        ]
    )
    return rows
