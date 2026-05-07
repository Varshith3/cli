# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
# service.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import re
import shutil
import sys

import typer

from platform_cli.exec.runner import run_cmd
from platform_cli.manifests.load import current_platform_key
from platform_cli.manifests.validate import validate_team_resolves
from platform_cli.state.store import update_tool_state, get_tool_state
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.tools.versions import check_version_req
from platform_cli.tools.winget import ensure_winget_ready
from platform_cli.tools.aws_sso import maybe_bootstrap_after_install
from platform_cli.tools.github_auth import maybe_bootstrap_after_install as maybe_bootstrap_github_after_install
from platform_cli.tools.jira_sso import maybe_bootstrap_after_install as maybe_bootstrap_jira_after_install
from platform_cli.tools.codex_auth import maybe_bootstrap_after_install as maybe_bootstrap_codex_after_install
from platform_cli.tools.claude_auth import maybe_bootstrap_after_install as maybe_bootstrap_claude_after_install
from platform_cli.tools.ownership import (
    OwnershipPolicy,
    build_ownership_policy,
    reconcile_tool_ownership,
)
from platform_cli.tools.user_global_agent_config import sync_user_global_agent_config

# ✅ Use your standard PlatformError (single source of truth)
try:
    from platform_cli.core.errors import PlatformError  # type: ignore
except Exception:  # pragma: no cover
    class PlatformError(RuntimeError):
        def __init__(self, message: str, code: str = "E_INTERNAL", reason: str = "UNKNOWN", alert: bool = False):
            super().__init__(message)
            self.code = code
            self.reason = reason
            self.alert = alert


@dataclass(frozen=True)
class ToolRuntimeSpec:
    name: str
    display_name: str
    detect_cmd: List[str]
    version_cmd: Optional[List[str]]
    install_cmd: List[str]
    upgrade_cmd: Optional[List[str]]
    uninstall_cmd: Optional[List[str]]
    version_req: Optional[Dict[str, Any]]  # {"op": ">=", "version": "x.y.z"} from toolset
    ownership_policy: OwnershipPolicy = field(default_factory=OwnershipPolicy)

    # Step-10 additions (non-breaking; optional metadata for manager-aware detection)
    bin_name: Optional[str] = None
    manager: Optional[str] = None
    brew_formula: Optional[str] = None
    brew_cask: Optional[str] = None
    # Optional manager metadata for Windows / other managers
    winget_id: Optional[str] = None
    choco_package: Optional[str] = None

    # ✅ mac app-bundle presence (VSCode-style) to avoid brew failures
    darwin_app_path: Optional[str] = None


@dataclass(frozen=True)
class ToolOnboardingStatus:
    tool_name: str
    display_name: str
    status: str
    short_status: str
    next_action: str = ""
    detail_hint: str = ""
    phase: str = ""


@dataclass(frozen=True)
class ToolDetectionResult:
    tool_name: str
    installed_any: bool
    display_version: str
    status: str
    detail_hint: str = ""
    code: str = ""
    manager_installed: bool = False
    managed_version: str = ""
    active_path: str = ""
    active_version: str = ""
    app_present: bool = False
    app_version: str = ""


def _status_result(
    spec: ToolRuntimeSpec,
    status: str,
    short_status: str,
    *,
    next_action: str = "",
    detail_hint: str = "",
    phase: str = "",
) -> ToolOnboardingStatus:
    return ToolOnboardingStatus(
        tool_name=spec.name,
        display_name=spec.display_name,
        status=status,
        short_status=short_status,
        next_action=next_action,
        detail_hint=detail_hint,
        phase=phase,
    )


def _install_command_hint(spec: ToolRuntimeSpec, *, interactive: bool = False) -> str:
    cmd = f"ghdp tools install --tool {spec.name}"
    if interactive:
        return f"Rerun `{cmd}` in an interactive terminal."
    return f"Rerun `{cmd}`."


def _status_rank(status: str) -> int:
    order = {
        "failed": 4,
        "action_required": 3,
        "ready": 2,
        "already_ready": 1,
        "skipped": 0,
    }
    return order.get(status, -1)


def _merge_status(primary: ToolOnboardingStatus, secondary: ToolOnboardingStatus) -> ToolOnboardingStatus:
    if _status_rank(secondary.status) > _status_rank(primary.status):
        return secondary
    if _status_rank(secondary.status) == _status_rank(primary.status):
        if secondary.next_action and not primary.next_action:
            return secondary
        if secondary.detail_hint and not primary.detail_hint:
            return secondary
    return primary


def _ready_result(spec: ToolRuntimeSpec, *, already_ready: bool = False, short_status: str = "") -> ToolOnboardingStatus:
    if already_ready:
        return _status_result(spec, "already_ready", short_status or "Already ready")
    return _status_result(spec, "ready", short_status or "Ready")


def _action_required_result(
    spec: ToolRuntimeSpec,
    short_status: str,
    *,
    next_action: str,
    detail_hint: str = "",
    phase: str = "",
) -> ToolOnboardingStatus:
    return _status_result(
        spec,
        "action_required",
        short_status,
        next_action=next_action,
        detail_hint=detail_hint,
        phase=phase,
    )


def _skipped_result(
    spec: ToolRuntimeSpec,
    short_status: str,
    *,
    next_action: str = "",
    detail_hint: str = "",
    phase: str = "",
) -> ToolOnboardingStatus:
    return _status_result(spec, "skipped", short_status, next_action=next_action, detail_hint=detail_hint, phase=phase)


def _post_action_out_of_policy_result(
    spec: ToolRuntimeSpec,
    *,
    action: str,
    policy_version: str,
    validation: Any,
) -> ToolOnboardingStatus:
    next_action = (
        f"Rerun `ghdp tools install --tool {spec.name} --upgrade`."
        if spec.upgrade_cmd
        else _install_command_hint(spec)
    )
    return _action_required_result(
        spec,
        "Out of policy; upgrade required",
        next_action=next_action,
        detail_hint=_policy_detail(validation, policy_version),
        phase=action,
    )


def build_install_failure_result(spec: ToolRuntimeSpec, exc: Exception) -> ToolOnboardingStatus:
    code = getattr(exc, "code", "E_INSTALL_FAILED") or "E_INSTALL_FAILED"
    message = str(exc).strip() or "Install failed"
    short_status = message
    next_action = ""
    detail_hint = ""

    if code == "E_AWS_SSO_NEEDS_INTERACTIVE":
        short_status = "Installed, but AWS SSO still needs interactive setup"
        next_action = _install_command_hint(spec, interactive=True)
    elif code == "E_AWS_SSO_CONFIG_INCOMPLETE":
        short_status = "Installed, but AWS SSO configuration is incomplete"
        next_action = _install_command_hint(spec, interactive=True)
    elif code == "E_JIRA_AUTH_NEEDS_INTERACTIVE":
        short_status = "Installed, but Jira authentication still needs interactive setup"
        next_action = _install_command_hint(spec, interactive=True)
    elif code == "E_JIRA_AUTH_INCOMPLETE":
        short_status = "Installed, but Jira authentication did not complete"
        next_action = _install_command_hint(spec, interactive=True)
    elif code == "E_CLAUDE_ATHENA_WORKGROUP_REQUIRED":
        short_status = "Installed, but Claude still needs an Athena workgroup"
        next_action = (
            "Set `DP_AWS_ATHENA_WORKGROUP`, save `claude.athena_workgroup`, "
            "or rerun `ghdp tools install --tool claude` interactively."
        )
    elif code == "E_CLAUDE_ATHENA_WORKGROUP_INVALID":
        short_status = "Claude setup needs a valid Athena workgroup"
        next_action = (
            "Set a non-empty `DP_AWS_ATHENA_WORKGROUP` or rerun "
            "`ghdp tools install --tool claude` interactively."
        )
    elif code == "E_CLAUDE_ATHENA_WORKGROUP_MAP_INVALID":
        short_status = "Claude setup could not load its Athena workgroup mapping"
        next_action = "Fix the Claude Athena workgroup mapping source, then rerun `ghdp tools install --tool claude`."
    elif code == "E_CODEX_LOGIN_INCOMPLETE":
        short_status = "Installed, but Codex login did not complete"
        next_action = _install_command_hint(spec, interactive=True)

    if code.startswith("E_") and code not in {"E_INSTALL_FAILED"}:
        detail_hint = code
    return _status_result(
        spec,
        "failed",
        short_status,
        next_action=next_action,
        detail_hint=detail_hint or message,
        phase="install",
    )


def _normalize_tool_version_req(req: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if isinstance(req, dict) and ("op" in req or "version" in req):
        return {"op": req.get("op"), "version": req.get("version")}
    return None


def _build_runtime_spec(
    *,
    tool_name: str,
    reg: Dict[str, Any],
    req: Optional[Dict[str, Any]],
    toolset_source: str,
) -> ToolRuntimeSpec:
    os_key = current_platform_key()
    plat = reg["platforms"][os_key]
    ownership_policy = build_ownership_policy(req if isinstance(req, dict) else None, toolset_source)

    brew = reg.get("brew", {}) if isinstance(reg.get("brew", {}), dict) else {}
    winget = reg.get("winget", {}) if isinstance(reg.get("winget", {}), dict) else {}
    choco = reg.get("choco", {}) if isinstance(reg.get("choco", {}), dict) else {}

    return ToolRuntimeSpec(
        name=tool_name,
        display_name=reg.get("display_name", tool_name),
        detect_cmd=plat.get("detect_cmd") or reg["detect_cmd"],
        version_cmd=plat.get("version_cmd") or reg.get("version_cmd"),
        install_cmd=plat["install"],
        upgrade_cmd=plat.get("upgrade"),
        uninstall_cmd=plat.get("uninstall"),
        version_req=_normalize_tool_version_req(req),
        ownership_policy=ownership_policy,
        bin_name=reg.get("bin"),
        manager=reg.get("manager"),
        brew_formula=brew.get("formula"),
        brew_cask=brew.get("cask"),
        winget_id=winget.get("id"),
        choco_package=choco.get("package"),
        darwin_app_path=reg.get("darwin_app_path"),
    )


def build_tool_runtime_spec(
    tool_name: str,
    registry: Dict[str, Any],
    *,
    version_req: Optional[Dict[str, Any]] = None,
    toolset_source: str = "",
) -> ToolRuntimeSpec:
    tools = registry.get("tools", {})
    if not isinstance(tools, dict) or tool_name not in tools:
        raise PlatformError(
            f"Unknown tool '{tool_name}' in tool registry.",
            code="E_MANIFEST_INVALID",
            reason=tool_name,
        )
    reg = tools[tool_name]
    return _build_runtime_spec(
        tool_name=tool_name,
        reg=reg,
        req=version_req,
        toolset_source=toolset_source,
    )


def resolve_team_tools(
    team: str,
    toolset: Dict[str, Any],
    registry: Dict[str, Any],
    *,
    toolset_source: str = "",
) -> List[ToolRuntimeSpec]:
    tool_names = validate_team_resolves(team, toolset, registry)
    out: List[ToolRuntimeSpec] = []
    for t in tool_names:
        reg = registry["tools"][t]
        req = toolset["teams"][team]["tools"].get(t, None)
        out.append(
            _build_runtime_spec(
                tool_name=t,
                reg=reg,
                req=req,
                toolset_source=toolset_source,
            )
        )

    return out


# -------------------------
# Helpers (Step-10)
# -------------------------

def _sh(cmd: str) -> str:
    """Run a shell snippet best-effort and return stdout stripped."""
    try:
        if sys.platform.startswith("win"):
            return (run_cmd(["powershell", "-NoProfile", "-Command", cmd], check=False).stdout or "").strip()
        return (run_cmd(["bash", "-lc", cmd], check=False).stdout or "").strip()
    except Exception:
        return ""


def _active_path_and_version(spec: ToolRuntimeSpec) -> Tuple[str, str]:
    """
    What the shell will actually execute.
    Returns (active_path, active_version).
    """
    bin_name = spec.bin_name or spec.name
    path = shutil.which(bin_name) or ""

    ver = ""
    if path:
        try:
            res = run_cmd([bin_name, "--version"], check=False)
            ver = (res.stdout or "").strip().splitlines()[0] if res.stdout else ""
        except Exception:
            ver = ""

        if not ver and spec.version_cmd:
            try:
                ver = (run_cmd(spec.version_cmd, check=False).stdout or "").strip()
            except Exception:
                ver = ""

    return path, ver


_SEMVERISH_RE = re.compile(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9._-]+)?")


def _normalize_version_like(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    match = _SEMVERISH_RE.search(text)
    if match:
        return match.group(0).strip()
    return text


def _darwin_app_present(spec: ToolRuntimeSpec) -> bool:
    """macOS: detect app bundle presence like /Applications/Visual Studio Code.app"""
    if not sys.platform.startswith("darwin"):
        return False
    if not spec.darwin_app_path:
        return False
    return Path(spec.darwin_app_path).exists()


def _darwin_app_version(spec: ToolRuntimeSpec) -> str:
    """
    macOS: best-effort app bundle version read.
    Works even when `code` isn't on PATH.
    """
    if not sys.platform.startswith("darwin"):
        return ""
    if not spec.darwin_app_path:
        return ""
    if not Path(spec.darwin_app_path).exists():
        return ""

    app = spec.darwin_app_path.replace("'", "\\'")
    # 1) Spotlight metadata (often works)
    v = _sh(f"mdls -name kMDItemVersion -raw '{app}' 2>/dev/null")
    if v and v.strip() and v.strip() != "(null)":
        return v.strip()

    # 2) Info.plist CFBundleShortVersionString
    v = _sh(f"/usr/bin/defaults read '{app}/Contents/Info' CFBundleShortVersionString 2>/dev/null")
    if v and v.strip():
        return v.strip()

    return ""


def _policy_check(version_str: str, req: Optional[Dict[str, Any]]):
    return check_version_req(version_str, req)


def _policy_detail(vc, got_version: str) -> str:
    op = getattr(vc, "op", None)
    required = getattr(vc, "required", None)
    parsed = getattr(vc, "parsed", None)
    if op and required:
        return f"required {op}{required}, got {parsed or got_version}"
    return f"policy not satisfied, got {parsed or got_version}"

def _already_installed_patterns() -> List[str]:
    return [
        "already installed",
        "is already installed",
        "already an app at",
        "already an app",
        "already exists",
        "already present",
        "already at",
        "exists at",
        "is present",
        "already up to date",
        "latest version is already installed",
        "the provided package is already installed",
        "nothing to do",
        "no available upgrade found",
        "no newer package versions are available",
        "found an existing package already installed",
    ]


def _build_error_text(exc: Exception) -> str:
    parts: List[str] = []
    msg = str(exc).strip()
    if msg:
        parts.append(msg)
    for attr in ("message", "stderr", "stdout"):
        val = getattr(exc, attr, None)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    return "\n".join(parts).lower()


def _is_already_installed_error(exc: Exception) -> bool:
    text = _build_error_text(exc)
    return any(pat in text for pat in _already_installed_patterns())


def _is_missing_tool_error(exc: Exception) -> bool:
    text = _build_error_text(exc)
    patterns = [
        "not found",
        "not recognized",
        "command not found",
        "could not find",
        "cannot find",
        "no such file or directory",
        "is not installed",
    ]
    return any(pat in text for pat in patterns)


def _winget_installed_and_version(winget_id: str) -> Tuple[bool, str]:
    """
    Uses winget list to detect install even if PATH isn't refreshed.
    Returns (installed, version).
    """
    try:
        res = run_cmd(
            [
                "winget",
                "list",
                "-e",
                "--id",
                winget_id,
                "--accept-source-agreements",
                "--disable-interactivity",
            ],
            check=False,
        )
        out = (res.stdout or "").strip()
        if res.returncode != 0 or not out:
            return False, ""

        # Find the row containing the exact id; then parse tokens:
        # ... <Id> <Version> <Source>
        for line in out.splitlines():
            if winget_id.lower() in line.lower():
                parts = line.split()
                if len(parts) >= 3 and parts[-3].lower() == winget_id.lower():
                    return True, parts[-2]  # version
                return True, ""  # installed but couldn't parse version
        return False, ""
    except Exception:
        return False, ""

def _run_aws_sso_post_step(spec: ToolRuntimeSpec) -> ToolOnboardingStatus:
    try:
        maybe_bootstrap_after_install()
        update_tool_state("awscli", {"aws_sso_state": "ok"})
        return _ready_result(spec, short_status="Installed and authenticated")
    except PlatformError as e:
        # ✅ If AWS SSO isn't visible in this session yet (Windows alias/PATH refresh),
        # do NOT fail the whole tool install. Mark as deferred and let user rerun GHDP later.
        if getattr(e, "code", "") == "E_AWS_SSO_NOT_AVAILABLE_YET":
            update_tool_state(
                "awscli",
                {
                    "aws_sso_post_step": "deferred",
                    "aws_sso_deferred_reason": str(e),
                    "aws_sso_state": "deferred",
                },
            )
            typer.echo("")
            typer.echo("ℹ️ AWS CLI installed, but Windows hasn't exposed the `aws` alias in this session yet.")
            typer.echo("   Close & reopen your terminal, then rerun the SAME GHDP command.")
            typer.echo("   GHDP will automatically complete AWS SSO login.")
            typer.echo("")
            return _action_required_result(
                spec,
                "Installed, but AWS SSO setup is waiting for a fresh terminal",
                next_action="Close and reopen your terminal, then rerun `ghdp tools install --tool awscli`.",
                detail_hint=str(e),
            )
        if getattr(e, "code", "") == "E_AWS_SSO_NEEDS_INTERACTIVE":
            update_tool_state("awscli", {"aws_sso_state": "deferred", "aws_sso_deferred_reason": str(e)})
            return _action_required_result(
                spec,
                "Installed, but AWS SSO still needs interactive setup",
                next_action=_install_command_hint(spec, interactive=True),
                detail_hint=str(e),
            )
        raise
    except Exception as e:
        raise PlatformError(
            f"AWS SSO post-install step failed: {e}",
            code="E_AWS_SSO_POST_INSTALL_FAILED",
            reason="aws_sso",
        )


def _run_jira_auth_post_step(spec: ToolRuntimeSpec) -> ToolOnboardingStatus:
    try:
        maybe_bootstrap_jira_after_install()
        update_tool_state("acli", {"jira_auth_state": "ok"})
        return _ready_result(spec, short_status="Installed and authenticated")
    except PlatformError as e:
        # ✅ If ACLI isn't visible in this session yet (Windows alias/PATH refresh),
        # do NOT fail the whole tool install. Mark as deferred and let user rerun GHDP later.
        if getattr(e, "code", "") == "E_ACLI_NOT_AVAILABLE_YET":
            update_tool_state(
                "acli",
                {
                    "jira_auth_post_step": "deferred",
                    "jira_auth_deferred_reason": str(e),
                    "jira_auth_state": "deferred",
                },
            )
            typer.echo("")
            typer.echo("ℹ️ ACLI installed, but Windows hasn't exposed the `acli` alias in this session yet.")
            typer.echo("   Close & reopen your terminal, then rerun the SAME GHDP command.")
            typer.echo("   GHDP will automatically complete Jira browser login.")
            typer.echo("")
            return _action_required_result(
                spec,
                "Installed, but Jira authentication is waiting for a fresh terminal",
                next_action="Close and reopen your terminal, then rerun `ghdp tools install --tool acli`.",
                detail_hint=str(e),
            )
        if getattr(e, "code", "") == "E_JIRA_AUTH_NEEDS_INTERACTIVE":
            update_tool_state("acli", {"jira_auth_state": "deferred", "jira_auth_deferred_reason": str(e)})
            return _action_required_result(
                spec,
                "Installed, but Jira authentication still needs interactive setup",
                next_action=_install_command_hint(spec, interactive=True),
                detail_hint=str(e),
            )
        raise
    except Exception as e:
        raise PlatformError(
            f"Jira auth post-install step failed: {e}",
            code="E_JIRA_AUTH_POST_INSTALL_FAILED",
            reason="jira_auth",
        )


def _run_github_auth_post_step(spec: ToolRuntimeSpec, *, status_printer=None) -> ToolOnboardingStatus:
    try:
        maybe_bootstrap_github_after_install(status_printer=status_printer)
        update_tool_state("gh", {"gh_auth_state": "ok"})
        return _ready_result(spec, short_status="Installed and authenticated")
    except PlatformError as e:
        if getattr(e, "code", "") == "E_GH_NOT_AVAILABLE_YET":
            update_tool_state(
                "gh",
                {
                    "gh_auth_post_step": "deferred",
                    "gh_auth_deferred_reason": str(e),
                    "gh_auth_state": "deferred",
                },
            )
            return _action_required_result(
                spec,
                "Installed, but GitHub CLI auth is waiting for a fresh terminal",
                next_action="Close and reopen your terminal, then rerun `ghdp tools install --tool gh`.",
                detail_hint=str(e),
            )
        if getattr(e, "code", "") == "E_GH_AUTH_NEEDS_INTERACTIVE":
            update_tool_state("gh", {"gh_auth_state": "deferred", "gh_auth_deferred_reason": str(e)})
            return _action_required_result(
                spec,
                "Installed, but GitHub CLI authentication still needs interactive setup",
                next_action=_install_command_hint(spec, interactive=True),
                detail_hint=str(e),
            )
        raise
    except Exception as e:
        raise PlatformError(
            f"GitHub auth post-install step failed: {e}",
            code="E_GH_AUTH_POST_INSTALL_FAILED",
            reason="gh_auth",
        )


def _run_codex_post_step(spec: ToolRuntimeSpec) -> ToolOnboardingStatus:
    try:
        maybe_bootstrap_codex_after_install()
        st = get_tool_state("codex")
        if str(st.get("codex_login_state", "")).strip() == "deferred":
            return _action_required_result(
                spec,
                "Installed, but Codex login still needs interactive setup",
                next_action=_install_command_hint(spec, interactive=True),
                detail_hint=str(st.get("codex_login_status", "") or "Codex login pending"),
            )
        update_tool_state("codex", {"codex_onboarding_state": "ok"})
        return _ready_result(spec, short_status="Installed and ready")
    except PlatformError as e:
        # If codex alias visibility is delayed in the current Windows session,
        # do not fail install. State is marked deferred and can be retried.
        if getattr(e, "code", "") == "E_CODEX_NOT_AVAILABLE_YET":
            update_tool_state(
                "codex",
                {
                    "codex_post_step": "deferred",
                    "codex_deferred_reason": str(e),
                    "codex_onboarding_state": "deferred",
                },
            )
            typer.echo("")
            typer.echo("Codex installed, but the command is not visible in this terminal session yet.")
            typer.echo("GHDP will retry Codex login automatically on the next install run.")
            typer.echo("")
            return _action_required_result(
                spec,
                "Installed, but Codex is waiting for a fresh terminal session",
                next_action="Close and reopen your terminal, then rerun `ghdp tools install --tool codex`.",
                detail_hint=str(e),
            )
        raise
    except Exception as e:
        raise PlatformError(
            f"Codex post-install step failed: {e}",
            code="E_CODEX_POST_INSTALL_FAILED",
            reason="codex_auth",
        )


def _run_claude_post_step(spec: ToolRuntimeSpec, *, status_printer=None) -> ToolOnboardingStatus:
    try:
        if status_printer is not None:
            status_printer("Preparing Claude post-install setup...")
        maybe_bootstrap_claude_after_install(status_printer=status_printer)
        st = get_tool_state("claude")
        source = str(st.get("claude_athena_workgroup_source", "")).strip()
        fallback_active = bool(st.get("claude_athena_workgroup_mapping_fallback_active", False))
        if source == "deferred":
            update_tool_state("claude", {"claude_onboarding_state": "deferred"})
            return _action_required_result(
                spec,
                "Installed, but Claude Athena workgroup is not configured yet",
                next_action=(
                    "Run `ghdp config claude-athena-workgroup --value <workgroup>` when you know the value, "
                    "or rerun `ghdp tools install --tool claude` interactively to enter or skip it again."
                ),
                detail_hint=str(st.get("claude_athena_workgroup_detail", "") or "Claude Athena workgroup deferred"),
            )
        if source == "env":
            short_status = "Installed and ready (Athena workgroup from env override)"
        elif source == "derived" and fallback_active:
            short_status = "Installed and ready (Athena workgroup derived via packaged backup mapping)"
        elif source == "derived":
            short_status = "Installed and ready (Athena workgroup derived from AWS identity)"
        elif source == "config":
            short_status = "Installed and ready (saved Athena workgroup reused)"
        elif source == "prompt":
            short_status = "Installed and ready (Athena workgroup prompted and saved)"
        else:
            short_status = "Installed and ready"
        update_tool_state("claude", {"claude_onboarding_state": "ok"})
        return _ready_result(spec, short_status=short_status)
    except PlatformError as e:
        if getattr(e, "code", "") == "E_CLAUDE_NOT_AVAILABLE_YET":
            update_tool_state(
                "claude",
                {
                    "claude_post_step": "deferred",
                    "claude_deferred_reason": str(e),
                    "claude_onboarding_state": "deferred",
                },
            )
            typer.echo("")
            typer.echo("Claude Code installed, but the command is not visible in this terminal session yet.")
            typer.echo("GHDP will retry Claude bootstrap automatically on the next install run.")
            typer.echo("")
            return _action_required_result(
                spec,
                "Installed, but Claude is waiting for a fresh terminal session",
                next_action="Close and reopen your terminal, then rerun `ghdp tools install --tool claude`.",
                detail_hint=str(e),
            )
        if getattr(e, "code", "") == "E_CLAUDE_ATHENA_WORKGROUP_REQUIRED":
            update_tool_state("claude", {"claude_onboarding_state": "deferred", "claude_deferred_reason": str(e)})
            return _action_required_result(
                spec,
                "Installed, but Claude still needs an Athena workgroup",
                next_action=(
                    "Set `DP_AWS_ATHENA_WORKGROUP`, save `claude.athena_workgroup`, "
                    "or rerun `ghdp tools install --tool claude` interactively."
                ),
                detail_hint=str(e),
            )
        if getattr(e, "code", "") == "E_CLAUDE_ATHENA_WORKGROUP_MAP_INVALID":
            update_tool_state("claude", {"claude_onboarding_state": "failed", "claude_deferred_reason": str(e)})
            return _action_required_result(
                spec,
                "Installed, but Claude could not load its Athena workgroup mapping",
                next_action="Fix the Claude Athena workgroup mapping source, then rerun `ghdp tools install --tool claude`.",
                detail_hint=str(e),
            )
        raise
    except Exception as e:
        raise PlatformError(
            f"Claude post-install step failed: {e}",
            code="E_CLAUDE_POST_INSTALL_FAILED",
            reason="claude_auth",
        )


def _run_agent_config_post_step(tool_name: str, *, status_printer=None) -> ToolOnboardingStatus:
    try:
        if status_printer is not None:
            status_printer(f"Refreshing {tool_name} agent config...")
        result = sync_user_global_agent_config(tool_name)
        update_tool_state(
            tool_name,
            {
                f"{tool_name}_global_agent_config_state": "ok",
                f"{tool_name}_global_agent_config_action": result.action,
                f"{tool_name}_global_agent_config_path": result.path,
            },
        )
        spec = ToolRuntimeSpec(
            name=tool_name,
            display_name=tool_name.upper(),
            detect_cmd=[],
            version_cmd=None,
            install_cmd=[],
            upgrade_cmd=None,
            uninstall_cmd=None,
            version_req=None,
        )
        return _ready_result(spec, short_status="Global agent config ready")
    except Exception as e:
        raise PlatformError(
            f"Global agent config post-install step failed for '{tool_name}': {e}",
            code="E_AGENT_CONFIG_POST_INSTALL_FAILED",
            reason=tool_name,
        )

def _action_present_participle(action: str) -> str:
    mapping = {
        "install": "Installing",
        "upgrade": "Upgrading",
        "uninstall": "Uninstalling",
        "adopt": "Adopting",
    }
    return mapping.get((action or "").strip().lower(), "Running")


def _run_tool_cmd(
    cmd: List[str],
    *,
    check: bool = True,
    stream: bool = False,
) -> None:
    """
    Standard tool command runner:
    - On Windows + winget: ensure winget is ready and stream output
    - Otherwise: optionally show a live spinner so installs do not look frozen
    """
    if not cmd:
        raise PlatformError("No command provided", code="E_INTERNAL", reason="missing_cmd")

    if sys.platform.startswith("win") and cmd and str(cmd[0]).lower() == "winget":
        ensure_winget_ready(allow_repair=True)
        run_cmd(cmd, check=check, capture=False, rich_logs=stream)
        return

    run_cmd(cmd, check=check, capture=True, rich_logs=stream)


def _resolve_and_persist_ownership(spec: ToolRuntimeSpec):
    return reconcile_tool_ownership(spec.name, spec.ownership_policy)

def detect_tool_details(spec: ToolRuntimeSpec) -> ToolDetectionResult:
    """
    Returns rich detection metadata while preserving the legacy detect_tool tuple wrapper.

    - detected = "manager-installed truth" (brew list for brew-managed tools)
    - also records active_path/active_version (what shell uses)
    - records darwin_app_present + darwin_app_version for mac GUI apps
    """
    manager_installed = False
    managed_version = ""
    detection_status = "not_installed"
    detection_error = ""
    detection_code = ""

    # ✅ Windows: prefer winget-based detection when available (avoids brew detect + PATH issues)
    if sys.platform.startswith("win") and spec.winget_id:
        manager_installed, managed_version = _winget_installed_and_version(spec.winget_id)
        detection_status = "installed" if manager_installed else "not_installed"
    else:
        try:
            result = run_cmd(spec.detect_cmd, check=False)
            if int(getattr(result, "returncode", 0) or 0) == 0:
                manager_installed = True
                detection_status = "installed"
            elif int(getattr(result, "returncode", 0) or 0) == 1:
                manager_installed = False
                detection_status = "not_installed"
            else:
                manager_installed = False
                detection_status = "detect_cmd_failed"
                detection_code = "E_TOOL_DETECT_FAILED"
                detection_error = (
                    (result.stderr or "").strip()
                    or (result.stdout or "").strip()
                    or f"Command failed ({result.returncode}): {' '.join(spec.detect_cmd)}"
                )
        except Exception as exc:
            manager_installed = False
            detection_error = str(exc).strip()
            detection_code = getattr(exc, "code", "") or "E_TOOL_DETECT_FAILED"
            detection_status = "not_installed" if _is_missing_tool_error(exc) else "detect_cmd_failed"

        if manager_installed and spec.version_cmd:
            try:
                version_result = run_cmd(spec.version_cmd, check=False)
                managed_version = (version_result.stdout or "").strip()
                if int(getattr(version_result, "returncode", 0) or 0) != 0:
                    managed_version = ""
                    detection_error = (
                        (version_result.stderr or "").strip()
                        or (version_result.stdout or "").strip()
                        or f"Command failed ({getattr(version_result, 'returncode', 'unknown')}): {' '.join(spec.version_cmd)}"
                    )
                    detection_code = "E_TOOL_VERSION_CHECK_FAILED"
                    detection_status = "version_check_failed"
            except Exception as exc:
                managed_version = ""
                detection_error = str(exc).strip()
                detection_code = getattr(exc, "code", "") or "E_TOOL_VERSION_CHECK_FAILED"
                detection_status = "version_check_failed"

    active_path, active_version = _active_path_and_version(spec)
    active_version = _normalize_version_like(active_version)
    active_present = bool(active_path)

    app_present = _darwin_app_present(spec)
    app_version = _darwin_app_version(spec) if app_present else ""
    app_version = _normalize_version_like(app_version)
    managed_version = _normalize_version_like(managed_version)

    installed_any = bool(manager_installed or active_present or app_present)
    display_version = (managed_version or active_version or app_version or "").strip()
    if (
        sys.platform.startswith("win")
        and spec.winget_id
        and detection_status == "not_installed"
        and active_present
    ):
        # Windows machines often have a usable binary on PATH even when the
        # package did not originate from winget. Treat this as installed rather
        # than ambiguous so GHDP can continue with policy/onboarding flows.
        detection_status = "installed"
        if not managed_version and active_version:
            managed_version = active_version

    if installed_any:
        if detection_status == "not_installed":
            detection_status = "detection_ambiguous"
            if not detection_error:
                detection_error = "Shell detection did not confirm the tool, but another presence signal exists."
                detection_code = "E_TOOL_DETECTION_AMBIGUOUS"
        elif detection_status == "detect_cmd_failed":
            detection_status = "detection_ambiguous"
            if not detection_code:
                detection_code = "E_TOOL_DETECTION_AMBIGUOUS"

    if manager_installed and active_present and managed_version and active_version and managed_version != active_version:
        detection_status = "detection_ambiguous"
        detection_error = f"Managed version '{managed_version}' differs from active PATH version '{active_version}'."
        detection_code = "E_TOOL_DETECTION_AMBIGUOUS"
    elif manager_installed and app_present and managed_version and app_version and managed_version != app_version:
        detection_status = "detection_ambiguous"
        detection_error = f"Managed version '{managed_version}' differs from app bundle version '{app_version}'."
        detection_code = "E_TOOL_DETECTION_AMBIGUOUS"

    patch: Dict[str, Any] = {
        # manager-level detection truth (brew/winget/choco ownership)
        "detected": manager_installed,
        # any detection truth (manager or active PATH or mac app bundle)
        "detected_any": installed_any,
        # explicit marker that a detect scan was executed
        "detection_scanned": True,
        "detected_version": managed_version,

        "managed_version": managed_version,
        "active_path": active_path,
        "active_version": active_version,
        "active_detected": active_present,

        "darwin_app_path": spec.darwin_app_path or "",
        "darwin_app_present": app_present,
        "darwin_app_version": app_version,

        "path_shadowed": bool(manager_installed and active_path.startswith("/usr/bin/")),
        "detection_status": detection_status,
        "detection_error": detection_error,
        "detection_error_code": detection_code,
    }

    # ✅ policy check against best available truth:
    # managed_version (brew) > active_version (PATH) > app_version (bundle)
    policy_version = (managed_version or active_version or app_version or "").strip()
    vc = _policy_check(policy_version, spec.version_req)
    patch["policy_ok"] = getattr(vc, "ok", None)
    patch["policy_req"] = spec.version_req or {}
    patch["policy_got"] = policy_version
    patch["policy_detail"] = "" if getattr(vc, "ok", None) is not False else _policy_detail(vc, policy_version)

    update_tool_state(spec.name, patch)
    _resolve_and_persist_ownership(spec)
    return ToolDetectionResult(
        tool_name=spec.name,
        installed_any=installed_any,
        display_version=display_version,
        status=detection_status,
        detail_hint=detection_error,
        code=detection_code,
        manager_installed=manager_installed,
        managed_version=managed_version,
        active_path=active_path,
        active_version=active_version,
        app_present=app_present,
        app_version=app_version,
    )


def detect_tool(spec: ToolRuntimeSpec) -> Tuple[bool, str]:
    result = detect_tool_details(spec)
    return result.installed_any, result.display_version


def _post_install_onboarding_status(spec: ToolRuntimeSpec, *, status_printer=None) -> ToolOnboardingStatus:
    status = _ready_result(spec, short_status="Installed and ready")

    if spec.name == "awscli":
        return _run_aws_sso_post_step(spec)

    if spec.name == "gh":
        return _run_github_auth_post_step(spec)

    if spec.name == "acli":
        return _run_jira_auth_post_step(spec)

    if spec.name == "codex":
        status = _merge_status(status, _run_codex_post_step(spec))
        config_status = _run_agent_config_post_step("codex", status_printer=status_printer)
        return _merge_status(status, _status_result(spec, config_status.status, config_status.short_status, next_action=config_status.next_action, detail_hint=config_status.detail_hint))

    if spec.name == "claude":
        status = _merge_status(status, _run_claude_post_step(spec, status_printer=status_printer))
        config_status = _run_agent_config_post_step("claude", status_printer=status_printer)
        return _merge_status(status, _status_result(spec, config_status.status, config_status.short_status, next_action=config_status.next_action, detail_hint=config_status.detail_hint))

    return status


def install_tool(
    spec: ToolRuntimeSpec,
    *,
    dry_run: bool = False,
    upgrade: bool = False,
    adopt_existing: bool = False,
    status_printer=None,
) -> ToolOnboardingStatus:
    installed_any, display_ver = detect_tool(spec)
    st = get_tool_state(spec.name)
    ownership = _resolve_and_persist_ownership(spec)
    managed_by = ownership.effective_owner

    # ✅ Special mac GUI-app case (VSCode):
    # App exists in /Applications, but Homebrew doesn't own it -> brew install errors.
    if (
        sys.platform.startswith("darwin")
        and spec.manager == "brew"
        and spec.brew_cask
        and st.get("darwin_app_present") is True
        and st.get("detected") is False  # not brew-managed
    ):
        if managed_by == "ghdp" or adopt_existing:
            adopt_cmd = ["bash", "-lc", f"brew install --cask --adopt {spec.brew_cask}"]
            action = "upgrade" if upgrade else "install"

            if dry_run:
                update_tool_state(spec.name, {"last_action": action, "last_status": "dry_run", "planned_cmd": adopt_cmd})
                return _skipped_result(
                    spec,
                    "Dry run only",
                    next_action=_install_command_hint(spec),
                )

            run_cmd(adopt_cmd, check=True)
            detect_tool(spec)
            ownership = _resolve_and_persist_ownership(spec)
            update_tool_state(
                spec.name,
                {"managed_by": ownership.effective_owner, "last_action": "adopt", "last_status": "ok"},
            )
            return _ready_result(spec, short_status="Installed and ready")

        update_tool_state(
            spec.name,
            {
                "last_action": "upgrade" if upgrade else "install",
                "last_status": "skipped",
                "reason": "ownership_user_managed",
            },
        )
        return _skipped_result(spec, "Already installed and user-managed")

    # ----------------------------
    # 1) Installed + upgrade requested
    # ----------------------------
    if installed_any and upgrade:
        if managed_by != "ghdp":
            update_tool_state(
                spec.name,
                {"last_action": "upgrade", "last_status": "skipped", "reason": "ownership_user_managed"},
            )
            return _skipped_result(spec, "Upgrade skipped for user-managed tool")

    # ----------------------------
    # 2) Installed + not upgrading -> non-interactive ownership handling
    # ----------------------------
    if installed_any and not upgrade:
        # ✅ Even if awscli is already installed, we must ensure SSO setup/login succeeded.
        # This fixes the case where SSO failed earlier and user retries `ghdp tools install awscli`.
        if spec.name == "awscli" and not dry_run:
            onboarding = _run_aws_sso_post_step(spec)
        elif spec.name == "gh" and not dry_run:
            onboarding = _run_github_auth_post_step(spec, status_printer=status_printer)
        elif spec.name == "acli" and not dry_run:
            onboarding = _run_jira_auth_post_step(spec)
        elif spec.name == "codex" and not dry_run:
            onboarding = _post_install_onboarding_status(spec, status_printer=status_printer)
        elif spec.name == "claude" and not dry_run:
            onboarding = _post_install_onboarding_status(spec, status_printer=status_printer)
        elif dry_run:
            onboarding = _skipped_result(spec, "Dry run only", next_action=_install_command_hint(spec))
        else:
            onboarding = _ready_result(spec, already_ready=True)

        if managed_by == "ghdp":
            update_tool_state(spec.name, {"last_action": "install", "last_status": "skipped", "reason": "already_managed"})
            if onboarding.status == "ready":
                return _ready_result(spec, already_ready=True)
            if onboarding.status == "skipped":
                return _ready_result(spec, already_ready=True)
            return onboarding

        update_tool_state(
            spec.name,
            {"last_action": "install", "last_status": "skipped", "reason": "ownership_user_managed"},
        )
        if onboarding.status == "action_required":
            return onboarding
        return _skipped_result(spec, "Already installed and user-managed")

    # ----------------------------
    # 3) Install / upgrade execution
    # ----------------------------
    cmd = spec.upgrade_cmd if (upgrade and spec.upgrade_cmd) else spec.install_cmd
    action = "upgrade" if (upgrade and spec.upgrade_cmd) else "install"

    if dry_run:
        update_tool_state(spec.name, {"last_action": action, "last_status": "dry_run", "planned_cmd": cmd})
        return _skipped_result(spec, "Dry run only", next_action=_install_command_hint(spec))

    # Run the install/upgrade command with exception handling.
    try:
        typer.echo(
            f"   {_action_present_participle(action)} {spec.display_name}. "
            "This can take a few minutes."
        )
        _run_tool_cmd(cmd, check=True, stream=True)
    except Exception as exc:
        if _is_already_installed_error(exc):
            installed_any2, _ = detect_tool(spec)
            st2 = get_tool_state(spec.name)
            ownership = _resolve_and_persist_ownership(spec)
            update_tool_state(
                spec.name,
                {
                    "managed_by": ownership.effective_owner,
                    "last_action": action,
                    "last_status": "ok" if installed_any2 else "skipped",
                    "reason": "already_installed_treated_as_detected",
                    "detected": True,
                    "detected_any": bool(installed_any2),
                    "detection_scanned": True,
                    "detected_version": st2.get("detected_version", ""),
                    "managed_version": st2.get("managed_version", ""),
                },
            )
            return _ready_result(spec, short_status="Already installed")
        raise  # Not a known pattern: rethrow as a real failure

    installed_any2, _ = detect_tool(spec)
    ownership = _resolve_and_persist_ownership(spec)

    update_tool_state(
        spec.name,
        {
            "managed_by": ownership.effective_owner,
            "last_action": action,
            "last_status": "ok" if installed_any2 else "error",
            "detected": bool(get_tool_state(spec.name).get("detected", False)),
            "detected_version": get_tool_state(spec.name).get("detected_version", ""),
            "managed_version": get_tool_state(spec.name).get("managed_version", ""),
        },
    )

    if spec.name == "gh":
        onboarding = _run_github_auth_post_step(spec, status_printer=status_printer)
    else:
        onboarding = _post_install_onboarding_status(spec, status_printer=status_printer)

    st2 = get_tool_state(spec.name)
    policy_version = (st2.get("policy_got") or "").strip()
    vc = _policy_check(policy_version, spec.version_req)
    if getattr(vc, "ok", None) is False:
        update_tool_state(
            spec.name,
            {
                "last_status": "action_required",
                "reason": "post_action_policy_out_of_policy",
            },
        )
        return _merge_status(
            onboarding,
            _post_action_out_of_policy_result(
                spec,
                action=action,
                policy_version=policy_version,
                validation=vc,
            ),
        )

    if onboarding.status == "ready":
        return _ready_result(spec, short_status="Installed and ready")
    return onboarding

def uninstall_tool(spec: ToolRuntimeSpec, *, dry_run: bool = False, force: bool = False) -> None:
    if not spec.uninstall_cmd:
        raise PlatformError(
            f"Tool '{spec.name}' does not define an uninstall command for this OS",
            code="E_UNINSTALL_UNSUPPORTED",
            reason=spec.name,
        )

    installed_any, display_ver = detect_tool(spec)
    st = get_tool_state(spec.name)
    ownership = _resolve_and_persist_ownership(spec)
    managed_by = ownership.effective_owner

    if not installed_any:
        update_tool_state(
            spec.name,
            {"last_action": "uninstall", "last_status": "skipped", "reason": "not_installed", "detected": False, "detected_version": ""},
        )
        return

    if managed_by != "ghdp":
        if not force:
            update_tool_state(
                spec.name,
                {"last_action": "uninstall", "last_status": "skipped", "reason": "user_managed_not_allowed",
                 "detected": bool(st.get("detected", False)), "detected_version": st.get("detected_version", "")},
            )
            raise PlatformError(
                f"Refusing to uninstall user-managed tool '{spec.name}'. Use --force to override.",
                code="E_UNINSTALL_NOT_GHDP_MANAGED",
                reason=spec.name,
            )

        if not bool(cli_ctx.non_interactive):
            ok = typer.confirm(
                f"'{spec.display_name}' appears user-managed (version='{display_ver}'). Force uninstall anyway?",
                default=False,
            )
            if not ok:
                update_tool_state(
                    spec.name,
                    {"last_action": "uninstall", "last_status": "skipped", "reason": "user_declined_force",
                     "detected": bool(st.get("detected", False)), "detected_version": st.get("detected_version", "")},
                )
                return

    if dry_run:
        update_tool_state(
            spec.name,
            {"last_action": "uninstall", "last_status": "dry_run", "planned_cmd": spec.uninstall_cmd,
             "detected": bool(st.get("detected", False)), "detected_version": st.get("detected_version", "")},
        )
        return
    
    # Windows: if this tool uses WinGet, ensure winget exists (Windows 10 can be missing it).
    typer.echo(f"   {_action_present_participle('uninstall')} {spec.display_name}. This can take a few minutes.")
    _run_tool_cmd(spec.uninstall_cmd, check=True, stream=True)
    installed_any2, _ = detect_tool(spec)

    st2 = get_tool_state(spec.name)
    active_present = bool(st2.get("active_detected", False))
    app_present = bool(st2.get("darwin_app_present", False))

    if not installed_any2:
        final_owner = "none"
        if active_present or app_present:
            final_owner = _resolve_and_persist_ownership(spec).effective_owner
        update_tool_state(
            spec.name,
            {"managed_by": final_owner, "last_action": "uninstall", "last_status": "ok",
             "detected": False, "detected_version": "", "managed_version": ""},
        )
        return

    update_tool_state(
        spec.name,
        {"last_action": "uninstall", "last_status": "error", "reason": "uninstall_failed",
         "detected": bool(st2.get("detected", False)), "detected_version": st2.get("detected_version", ""),
         "managed_version": st2.get("managed_version", "")},
    )
    raise PlatformError(
        f"Uninstall failed for '{spec.name}': still detected after uninstall (version='{st2.get('policy_got') or ''}')",
        code="E_UNINSTALL_FAILED",
        reason=spec.name,
    )
