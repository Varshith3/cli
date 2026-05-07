# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import typer

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.errors import PlatformError
from platform_cli.exec.runner import run_cmd
from platform_cli.state.store import update_tool_state
from platform_cli.tools.athena_workgroup import AthenaWorkgroupResolution, resolve_athena_workgroup
from platform_cli.tools.claude_athena_workgroup_assets import ensure_claude_athena_workgroup_map_available
from platform_cli.tools.aws_profile import (
    AwsProfileResolution,
    prompt_aws_profile_choice,
    resolve_aws_profile,
    set_active_profile,
)
from platform_cli.tools.aws_sso import maybe_bootstrap_after_install as maybe_bootstrap_aws_after_install
from platform_cli.tools.claude_skill_sync import sync_aws_readonly_skill


PROFILE_MARKER_START = "# Added by GHDP Claude bootstrap"
PROFILE_MARKER_END = "# End GHDP Claude bootstrap"


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _candidate_claude_paths() -> List[Path]:
    home = Path.home()
    candidates: List[Path] = []

    path_hit = shutil.which("claude")
    if path_hit:
        candidates.append(Path(path_hit))

    if _is_windows():
        candidates.extend(
            [
                home / ".local" / "bin" / "claude.exe",
                home / ".claude" / "local" / "claude.exe",
            ]
        )
    else:
        candidates.extend(
            [
                home / ".local" / "bin" / "claude",
                home / ".claude" / "local" / "claude",
                Path("/opt/homebrew/bin/claude"),
                Path("/usr/local/bin/claude"),
            ]
        )

    out: List[Path] = []
    seen = set()
    for candidate in candidates:
        key = str(candidate).lower() if _is_windows() else str(candidate)
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def _find_claude_from_where() -> Optional[str]:
    if not _is_windows():
        p = shutil.which("claude")
        return p if p else None

    try:
        res = run_cmd(["where.exe", "claude"], check=False, capture=True)
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


def _resolve_claude_exe() -> str:
    if _is_windows():
        p = _find_claude_from_where()
        if p:
            return p

    for candidate in _candidate_claude_paths():
        if candidate.exists():
            return str(candidate)

    raise PlatformError(
        "Claude Code was installed but is not available in this session yet.",
        code="E_CLAUDE_NOT_AVAILABLE_YET",
        reason="claude",
    )


def _claude_version(claude_exe: str) -> str:
    res = run_cmd([claude_exe, "--version"], check=False, capture=True)
    out = (res.stdout or res.stderr or "").strip()
    if out:
        return out.splitlines()[0].strip()
    if res.returncode != 0:
        raise PlatformError(
            "Unable to read Claude Code version after install.",
            code="E_CLAUDE_VERSION_CHECK_FAILED",
            reason="claude",
        )
    return ""


def _parse_version(raw: str) -> str:
    m = re.search(r"(\d+\.\d+\.\d+(?:[-+][A-Za-z0-9._-]+)?)", raw or "")
    return m.group(1) if m else (raw or "").strip()


def _windows_profile_path() -> Path:
    try:
        res = run_cmd(["powershell", "-NoProfile", "-Command", "Write-Output $PROFILE"], check=False, capture=True)
        out = (res.stdout or "").strip()
        if out:
            return Path(out)
    except Exception:
        pass
    return Path.home() / "Documents" / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1"


def _unix_profile_path() -> Path:
    shell = (os.environ.get("SHELL", "") or "").lower()
    if "bash" in shell:
        return Path.home() / ".bashrc"
    return Path.home() / ".zshrc"


def _profile_path() -> Path:
    return _windows_profile_path() if _is_windows() else _unix_profile_path()


def _profile_block(
    workgroup: str,
    aws_profile: str,
    *,
    persist_workgroup: bool = True,
    preserve_existing_workgroup: bool = False,
    preserved_workgroup_line: str = "",
) -> str:
    if _is_windows():
        lines = [
            PROFILE_MARKER_START,
            '$env:CLAUDE_CODE_USE_BEDROCK = "1"',
            '$env:AWS_REGION = "us-west-2"',
            "$claudeBin = Join-Path (Join-Path $env:USERPROFILE '.local') 'bin'",
            'if (Test-Path $claudeBin) {',
            '  if (-not (($env:Path -split ";") -contains $claudeBin)) {',
            '    $env:Path = "$claudeBin;$env:Path"',
            '  }',
            '}',
            PROFILE_MARKER_END,
        ]
        if persist_workgroup:
            lines.insert(4, f'$env:DP_AWS_ATHENA_WORKGROUP = "{workgroup}"')
        elif preserve_existing_workgroup and preserved_workgroup_line:
            lines.insert(4, preserved_workgroup_line)
        return "\n".join(lines) + "\n"

    lines = [
        PROFILE_MARKER_START,
        "export CLAUDE_CODE_USE_BEDROCK=1",
        "export AWS_REGION=us-west-2",
        'export PATH="$HOME/.local/bin:$PATH"',
        PROFILE_MARKER_END,
    ]
    if persist_workgroup:
        lines.insert(4, f'export DP_AWS_ATHENA_WORKGROUP="{workgroup}"')
    elif preserve_existing_workgroup and preserved_workgroup_line:
        lines.insert(4, preserved_workgroup_line)
    return "\n".join(lines) + "\n"


def _extract_profile_workgroup_line(existing: str) -> str:
    if not existing:
        return ""
    if _is_windows():
        pattern = re.compile(r"^\$env:DP_AWS_ATHENA_WORKGROUP\s*=.*$", re.MULTILINE)
    else:
        pattern = re.compile(r'^export DP_AWS_ATHENA_WORKGROUP=.*$', re.MULTILINE)
    match = pattern.search(existing)
    return match.group(0).strip() if match else ""


def _upsert_profile_block(
    profile_path: Path,
    workgroup: str,
    aws_profile: str,
    *,
    persist_workgroup: bool = True,
    preserve_existing_workgroup: bool = False,
) -> None:
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    existing = profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
    preserved_workgroup_line = ""
    if not persist_workgroup and preserve_existing_workgroup:
        preserved_workgroup_line = _extract_profile_workgroup_line(existing)
    block = _profile_block(
        workgroup,
        aws_profile,
        persist_workgroup=persist_workgroup,
        preserve_existing_workgroup=preserve_existing_workgroup,
        preserved_workgroup_line=preserved_workgroup_line,
    )

    start = re.escape(PROFILE_MARKER_START)
    end = re.escape(PROFILE_MARKER_END)
    pattern = re.compile(rf"{start}\n.*?{end}\n?", re.DOTALL)

    if pattern.search(existing):
        updated = pattern.sub(block, existing, count=1)
    else:
        updated = existing
        if updated and not updated.endswith("\n"):
            updated += "\n"
        if updated:
            updated += "\n"
        updated += block

    if updated != existing:
        profile_path.write_text(updated, encoding="utf-8")


def _persist_windows_user_env(
    workgroup: str,
    aws_profile: str,
    *,
    persist_workgroup: bool = True,
    preserve_existing_workgroup: bool = False,
) -> None:
    if not _is_windows():
        return

    claude_bin = str(Path.home() / ".local" / "bin")
    if persist_workgroup:
        workgroup_line = f"[Environment]::SetEnvironmentVariable('DP_AWS_ATHENA_WORKGROUP', '{workgroup}', 'User')"
    elif preserve_existing_workgroup:
        workgroup_line = ""
    else:
        workgroup_line = "[Environment]::SetEnvironmentVariable('DP_AWS_ATHENA_WORKGROUP', $null, 'User')"
    ps = f"""
$claudeBin = [System.IO.Path]::Combine($env:USERPROFILE, '.local\\bin')
[Environment]::SetEnvironmentVariable('CLAUDE_CODE_USE_BEDROCK', '1', 'User')
[Environment]::SetEnvironmentVariable('AWS_REGION', 'us-west-2', 'User')
{workgroup_line}
$existing = [Environment]::GetEnvironmentVariable('Path', 'User')
$parts = @()
if ($existing) {{
  $parts = $existing -split ';' | Where-Object {{ $_ }}
}}
if (-not ($parts -contains $claudeBin)) {{
  $newPath = if ($existing) {{ "$claudeBin;$existing" }} else {{ $claudeBin }}
  [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
}}
"""
    run_cmd(["powershell", "-NoProfile", "-Command", ps], check=True, capture=True)


def _ensure_windows_execution_policy() -> None:
    if not _is_windows():
        return
    run_cmd(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force",
        ],
        check=True,
        capture=True,
    )


def _apply_process_env(workgroup: str, aws_profile: str) -> None:
    os.environ["CLAUDE_CODE_USE_BEDROCK"] = "1"
    os.environ["AWS_REGION"] = "us-west-2"
    os.environ["AWS_PROFILE"] = aws_profile
    if workgroup:
        os.environ["DP_AWS_ATHENA_WORKGROUP"] = workgroup
    else:
        os.environ.pop("DP_AWS_ATHENA_WORKGROUP", None)

    claude_bin = str(Path.home() / ".local" / "bin")
    current_path = os.environ.get("PATH", "")
    entries = current_path.split(os.pathsep) if current_path else []
    if claude_bin not in entries:
        os.environ["PATH"] = claude_bin + (os.pathsep + current_path if current_path else "")


def _apply_process_workgroup_only(workgroup: str) -> None:
    if workgroup:
        os.environ["DP_AWS_ATHENA_WORKGROUP"] = workgroup
    else:
        os.environ.pop("DP_AWS_ATHENA_WORKGROUP", None)


def sync_saved_claude_workgroup_runtime(workgroup: str) -> Path:
    """
    Keep shell/runtime state in sync when users update `claude.athena_workgroup`
    via `ghdp config claude-athena-workgroup`.
    """
    normalized_workgroup = (workgroup or "").strip()
    profile_path = _profile_path()
    persist_workgroup = bool(normalized_workgroup)

    if _is_windows():
        _persist_windows_user_env(
            normalized_workgroup,
            os.environ.get("AWS_PROFILE", ""),
            persist_workgroup=persist_workgroup,
            preserve_existing_workgroup=False,
        )

    _upsert_profile_block(
        profile_path,
        normalized_workgroup,
        os.environ.get("AWS_PROFILE", ""),
        persist_workgroup=persist_workgroup,
        preserve_existing_workgroup=False,
    )
    _apply_process_workgroup_only(normalized_workgroup)
    return profile_path


def _resolve_athena_workgroup(aws_profile: str, *, status_printer=None) -> AthenaWorkgroupResolution:
    return resolve_athena_workgroup(aws_profile=aws_profile, status_printer=status_printer)


def _show_claude_install_profile(profile: str, source: str) -> None:
    typer.echo(f"Claude install AWS profile: {profile} (source={source})")


def _resolve_claude_install_profile() -> AwsProfileResolution:
    resolved = resolve_aws_profile(
        prompt_if_unresolved=not bool(cli_ctx.non_interactive),
        prompt_when_flag_missing=False,
        persist_prompt_scope="global",
    )

    if bool(cli_ctx.non_interactive):
        _show_claude_install_profile(resolved.profile, resolved.source)
        return resolved

    keep_current = typer.confirm(
        f"Use AWS profile '{resolved.profile}' for this Claude install?",
        default=True,
    )
    if keep_current:
        _show_claude_install_profile(resolved.profile, resolved.source)
        return resolved

    chosen = prompt_aws_profile_choice(default_profile=resolved.profile)
    set_active_profile(chosen, scope="global")
    final = AwsProfileResolution(profile=chosen, source="prompt", repo_key=resolved.repo_key)
    _show_claude_install_profile(final.profile, final.source)
    return final


def _claude_health_status(claude_exe: str) -> Tuple[bool, str]:
    env: Dict[str, str] = dict(os.environ)
    res = run_cmd([claude_exe, "--version"], check=False, capture=True, env=env)
    txt = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
    return (res.returncode == 0), txt


def _maybe_launch_claude_same_session(claude_exe: str) -> None:
    if not _is_windows():
        return
    if bool(cli_ctx.non_interactive):
        return

    typer.echo("")
    typer.echo("Claude Code is ready in this terminal.")
    launch_now = typer.confirm("Launch Claude now in this same PowerShell session?", default=True)
    if not launch_now:
        update_tool_state(
            "claude",
            {
                "claude_launch_same_session": "declined",
                "claude_launch_same_session_at": int(time.time()),
            },
        )
        typer.echo("You can run `claude` manually in a new PowerShell window later.")
        return

    update_tool_state(
        "claude",
        {
            "claude_launch_same_session": "started",
            "claude_launch_same_session_at": int(time.time()),
        },
    )
    typer.echo("")
    typer.echo("Launching Claude now...")
    typer.echo("")
    run_cmd([claude_exe], check=False, capture=False, env=dict(os.environ))


def _print_unix_reload_hint(profile_path: Path) -> None:
    if _is_windows():
        return

    shell = (os.environ.get("SHELL", "") or "").lower()
    if "bash" in shell:
        cmd = "source ~/.bashrc"
    else:
        cmd = "source ~/.zshrc"

    typer.echo("")
    typer.echo("Claude environment was written to your shell profile.")
    typer.echo(f"Run `{cmd}` or open a new Terminal window before running `claude`.")
    typer.echo(f"Profile updated: {profile_path}")


def maybe_bootstrap_after_install(*, status_printer=None) -> None:
    """
    Post-install step for Claude Code:
      1) Ensure AWS CLI + SSO/token are ready.
      2) Resolve Athena workgroup and persist profile env vars.
      3) Resolve Claude executable even if PATH is stale.
      4) Sync Claude AWS read-only skill files.
      5) Verify Claude is callable.
    """
    update_tool_state("claude", {"claude_post_step_attempted_at": int(time.time())})

    if status_printer is not None:
        status_printer("Checking Claude AWS profile...")
    selected_profile = _resolve_claude_install_profile()
    if status_printer is not None:
        status_printer(f"Ensuring AWS SSO is ready for profile '{selected_profile.profile}'...")
    aws_resolution = maybe_bootstrap_aws_after_install(profile=selected_profile.profile)
    aws_profile = str(getattr(aws_resolution, "profile", "") or "").strip() or "default"
    if status_printer is not None:
        status_printer("Checking Claude Athena workgroup mapping cache...")
    mapping_asset_result = ensure_claude_athena_workgroup_map_available()
    mapping_status = str(mapping_asset_result.get("local_status", "")).strip()
    if status_printer is not None:
        if mapping_status == "cached":
            status_printer("Using cached Claude Athena workgroup mapping.")
        elif mapping_status == "synced":
            status_printer("Fetched Claude Athena workgroup mapping before setup.")
        elif mapping_status == "current":
            status_printer("Claude Athena workgroup mapping is already current.")
        elif mapping_status == "fallback":
            status_printer("Claude Athena workgroup mapping sync is unavailable; using packaged fallback.")
        elif mapping_status == "warning":
            status_printer("Claude Athena workgroup mapping sync failed; using packaged fallback for now.")
    update_tool_state(
        "claude",
        {
            "claude_athena_map_capability": str(mapping_asset_result.get("capability", "")),
            "claude_athena_map_target_path": str(mapping_asset_result.get("target_path", "")),
            "claude_athena_map_local_status": str(mapping_asset_result.get("local_status", "")),
            "claude_athena_map_latest_tag": str(mapping_asset_result.get("latest_tag", "")),
            "claude_athena_map_latest_version": str(mapping_asset_result.get("latest_version", "")),
            "claude_athena_map_used_cached": bool(mapping_asset_result.get("used_cached", False)),
            "claude_athena_map_sync_result": dict(mapping_asset_result.get("sync_result", {})),
        },
    )
    workgroup_resolution = _resolve_athena_workgroup(aws_profile, status_printer=status_printer)
    workgroup = workgroup_resolution.workgroup
    persist_workgroup = bool(workgroup) and workgroup_resolution.source in {"config", "prompt", "derived"}
    preserve_existing_workgroup = workgroup_resolution.source == "env"
    profile_path = _profile_path()
    if status_printer is not None:
        status_printer("Writing Claude environment and profile settings...")
    if _is_windows():
        _ensure_windows_execution_policy()
        _persist_windows_user_env(
            workgroup,
            aws_profile,
            persist_workgroup=persist_workgroup,
            preserve_existing_workgroup=preserve_existing_workgroup,
        )
    _upsert_profile_block(
        profile_path,
        workgroup,
        aws_profile,
        persist_workgroup=persist_workgroup,
        preserve_existing_workgroup=preserve_existing_workgroup,
    )
    _apply_process_env(workgroup, aws_profile)

    if status_printer is not None:
        status_printer("Verifying Claude installation...")
    claude_exe = _resolve_claude_exe()
    version_raw = _claude_version(claude_exe)
    version = _parse_version(version_raw)
    update_tool_state(
        "claude",
        {
            "claude_exe": claude_exe,
            "claude_version": version,
            "claude_version_raw": version_raw,
            "claude_profile_path": str(profile_path),
            "claude_aws_profile": aws_profile,
            "claude_athena_workgroup": workgroup,
            "claude_athena_workgroup_source": workgroup_resolution.source,
            "claude_athena_workgroup_account_id": workgroup_resolution.account_id,
            "claude_athena_workgroup_role_name": workgroup_resolution.role_name,
            "claude_athena_workgroup_mapping_source": workgroup_resolution.mapping_source,
            "claude_athena_workgroup_mapping_fallback_active": bool(workgroup_resolution.fallback_active),
            "claude_athena_workgroup_persisted": bool(workgroup_resolution.persisted),
            "claude_athena_workgroup_configured": bool(workgroup_resolution.configured),
            "claude_athena_workgroup_shell_persisted": bool(persist_workgroup),
            "claude_athena_workgroup_detail": workgroup_resolution.detail_message,
            "claude_windows_user_env_persisted": bool(_is_windows() and persist_workgroup),
        },
    )

    try:
        if status_printer is not None:
            status_printer("Refreshing Claude AWS helper content...")
        sync = sync_aws_readonly_skill()
        update_tool_state(
            "claude",
            {
                "claude_skill_sync_state": "ok",
                "claude_skill_sync_name": str(sync.get("skill_name", "")),
                "claude_skill_sync_path": str(sync.get("target_path", "")),
                "claude_skill_sync_file_count": int(sync.get("file_count", 0)),
                "claude_skill_sync_updated_count": int(sync.get("updated_count", 0)),
                "claude_skill_sync_hash": str(sync.get("content_hash", "")),
                "claude_skill_sync_source": str(sync.get("source", "")),
                "claude_skill_sync_release_repo": str(sync.get("release_repo", "")),
                "claude_skill_sync_release_tag": str(sync.get("release_tag", "")),
                "claude_skill_sync_content_version": str(sync.get("content_version", "")),
                "claude_skill_sync_at": int(sync.get("synced_at", int(time.time()))),
            },
        )
    except PlatformError as e:
        update_tool_state(
            "claude",
            {
                "claude_skill_sync_state": "error",
                "claude_skill_sync_error": str(e),
                "claude_skill_sync_error_code": getattr(e, "code", "E_CLAUDE_SKILL_SYNC_FAILED"),
                "claude_skill_sync_at": int(time.time()),
            },
        )
        raise

    if status_printer is not None:
        status_printer("Running Claude health check...")
    ok, status = _claude_health_status(claude_exe)
    if not ok:
        raise PlatformError(
            "Claude Code was installed but failed the post-install health check.",
            code="E_CLAUDE_HEALTHCHECK_FAILED",
            reason="claude",
        )

    update_tool_state(
        "claude",
        {
            "claude_health_state": "ok",
            "claude_health_status": status,
            "claude_health_last_checked_at": int(time.time()),
        },
    )

    _print_unix_reload_hint(profile_path)
    if bool(getattr(cli_ctx, "claude_launch_same_session", True)):
        _maybe_launch_claude_same_session(claude_exe)
