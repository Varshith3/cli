# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
# src/platform_cli/core/update.py

from __future__ import annotations

import json
import base64
import os
import platform
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple
from urllib import request as urlrequest

import typer
from rich import print as rprint

from platform_cli import __version__
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.errors import PlatformError
from platform_cli.core import github_auth
from platform_cli.core.runtime_env import runtime_env_path
from platform_cli.exec.runner import run_cmd
from platform_cli.state.store import get_tool_state, update_tool_state
from platform_cli.tools.release_notes import extract_release_summary

DEFAULT_REPO = (os.getenv("GHDP_DEFAULT_REPO") or "").strip()
_CHECKED = False

# once per 24h by default
CHECK_INTERVAL_S = int(os.getenv("GHDP_UPDATE_CHECK_INTERVAL_S", str(24 * 60 * 60)))
_SEMVER_TAG_RE = re.compile(r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?$")


@dataclass
class ReleaseTag:
    tag: str
    prerelease: bool
    draft: bool
    published_at: str


@dataclass
class InstallResult:
    method: str
    target_tag: str
    verification_status: str
    active_tag: str = ""
    detail: str = ""


def current_version_tag() -> str:
    return f"v{__version__.lstrip('v')}"


def _record_install_phase(phase: str, *, target_tag: str = "", active_tag: str = "", detail: str = "") -> None:
    payload: dict[str, str] = {"update_install_phase": phase}
    if target_tag:
        payload["update_install_target_tag"] = target_tag
    if active_tag:
        payload["update_install_active_tag"] = active_tag
    if detail:
        payload["update_install_detail"] = detail
    update_tool_state("ghdp", payload)


def _current_executable_path() -> Path | None:
    raw = (sys.argv[0] or "").strip()
    if not raw:
        return None
    try:
        candidate = Path(raw)
        if candidate.exists():
            return candidate.resolve()
    except Exception:
        return None
    return None


def _paths_match(left: Path | None, right: Path | None) -> bool:
    if left is None or right is None:
        return False
    try:
        return left.resolve() == right.resolve()
    except Exception:
        return str(left).lower() == str(right).lower()


def _extract_version_tag_from_output(output: str) -> str:
    text = (output or "").strip()
    match = re.search(r"ghdp\s+([0-9A-Za-z._-]+)", text, flags=re.IGNORECASE)
    if not match:
        return ""
    return normalize_tag(match.group(1))


def _binary_version_tag(executable: Path) -> str:
    try:
        res = run_cmd([str(executable), "--version"], check=False)
    except PlatformError:
        return ""
    if res.returncode != 0:
        return ""
    return _extract_version_tag_from_output(res.stdout or "")


def _verify_binary_version(executable: Path, expected_tag: str, *, attempts: int = 5, sleep_seconds: float = 0.5) -> bool:
    normalized = normalize_tag(expected_tag)
    for _ in range(max(attempts, 1)):
        observed = _binary_version_tag(executable)
        if observed == normalized:
            return True
        time.sleep(max(sleep_seconds, 0))
    return False


def normalize_tag(tag: str) -> str:
    raw = (tag or "").strip()
    if not raw:
        raise PlatformError(
            "Version/tag is required.",
            code="E_VERSION_REQUIRED",
            reason="missing_tag",
        )
    return raw if raw.startswith("v") else f"v{raw}"


def _is_supported_release_tag(tag: str) -> bool:
    return bool(_SEMVER_TAG_RE.fullmatch((tag or "").strip()))


def _gh_available() -> bool:
    return shutil.which("gh") is not None


def _pipx_available() -> bool:
    return shutil.which("pipx") is not None


def _parse_version(tag: str) -> Tuple[int, int, int, str]:
    """
    Very small semver-ish parser.
    Returns (major, minor, patch, raw) and falls back to (0,0,0,raw) if weird.
    """
    raw = (tag or "").strip()
    match = _SEMVER_TAG_RE.fullmatch(raw)
    if not match:
        return (0, 0, 0, raw)
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        raw if raw.startswith("v") else f"v{raw}",
    )


def _is_newer(current_tag: str, candidate_tag: str) -> bool:
    c = _parse_version(current_tag)
    n = _parse_version(candidate_tag)
    return (n[0], n[1], n[2]) > (c[0], c[1], c[2])


def list_release_tags(
    repo: str,
    *,
    limit: int = 30,
    include_drafts: bool = False,
) -> list[ReleaseTag]:
    try:
        per_page = max(1, min(int(limit), 100))
    except Exception:
        raise PlatformError(
            f"Invalid release limit '{limit}'.",
            code="E_BAD_ARGS",
            reason="invalid_limit",
        )

    payload = None
    gh_error = ""

    gh_env = github_auth.gh_subprocess_env()

    if _gh_available():
        try:
            res = run_cmd(
                ["gh", "api", f"repos/{repo}/releases?per_page={per_page}"],
                check=True,
                env=gh_env or None,
                encoding="utf-8",
                errors="replace",
            )
            payload = json.loads(res.stdout or "[]")
        except PlatformError as e:
            gh_error = str(e)
        except Exception as e:
            gh_error = f"Failed to parse releases response: {e}"

    if payload is None:
        token = _token_from_env() or _token_from_managed_auth_state() or _token_from_gh_no_prompt(repo)
        if token:
            try:
                rows = _list_release_tags_via_api(
                    repo,
                    limit=per_page,
                    include_drafts=include_drafts,
                    token=token,
                )
                return rows
            except PlatformError as e:
                gh_error = str(e) if not gh_error else f"{gh_error}; {e}"

    if payload is None and (not bool(cli_ctx.non_interactive)):
        token = _resolve_github_token(repo)
        if token:
            try:
                return _list_release_tags_via_api(
                    repo,
                    limit=per_page,
                    include_drafts=include_drafts,
                    token=token,
                )
            except PlatformError as e:
                gh_error = str(e) if not gh_error else f"{gh_error}; {e}"

    if payload is None:
        detail = gh_error or "No GitHub authentication method is available."
        raise PlatformError(
            f"Failed to fetch releases from '{repo}': {detail}",
            code="E_RELEASES_FETCH_FAILED",
            reason="gh_api_releases_failed",
        )

    if not isinstance(payload, list):
        raise PlatformError(
            "Unexpected GitHub releases payload type.",
            code="E_RELEASES_PARSE_FAILED",
            reason="invalid_payload",
        )

    out: list[ReleaseTag] = []
    for item in payload:
        if not isinstance(item, dict):
            continue

        tag = (item.get("tag_name") or "").strip()
        if not tag:
            continue
        if not _is_supported_release_tag(tag):
            continue

        draft = bool(item.get("draft", False))
        if draft and not include_drafts:
            continue

        out.append(
            ReleaseTag(
                tag=tag,
                prerelease=bool(item.get("prerelease", False)),
                draft=draft,
                published_at=(item.get("published_at") or "").strip(),
            )
        )

    return out


def _list_release_tags_via_api(
    repo: str,
    *,
    limit: int = 30,
    include_drafts: bool = False,
    token: str = "",
) -> list[ReleaseTag]:
    try:
        per_page = max(1, min(int(limit), 100))
    except Exception:
        raise PlatformError(
            f"Invalid release limit '{limit}'.",
            code="E_BAD_ARGS",
            reason="invalid_limit",
        )

    url = f"https://api.github.com/repos/{repo}/releases?per_page={per_page}"
    req = urlrequest.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urlrequest.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise PlatformError(
            f"Failed to fetch releases from '{repo}' via GitHub API: {e}",
            code="E_RELEASES_FETCH_FAILED",
            reason="github_http_releases_failed",
        )

    if not isinstance(payload, list):
        raise PlatformError(
            "Unexpected GitHub releases payload type.",
            code="E_RELEASES_PARSE_FAILED",
            reason="invalid_payload",
        )

    out: list[ReleaseTag] = []
    for item in payload:
        if not isinstance(item, dict):
            continue

        tag = (item.get("tag_name") or "").strip()
        if not tag:
            continue
        if not _is_supported_release_tag(tag):
            continue

        draft = bool(item.get("draft", False))
        if draft and not include_drafts:
            continue

        out.append(
            ReleaseTag(
                tag=tag,
                prerelease=bool(item.get("prerelease", False)),
                draft=draft,
                published_at=(item.get("published_at") or "").strip(),
            )
        )

    return out
def _get_latest_tags_via_gh(repo: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (latest_stable_tag, latest_prerelease_tag).
    Stable is anchored to GitHub Latest; prerelease stays list-based.
    """
    stable = _get_latest_stable_via_gh(repo)
    try:
        releases = list_release_tags(repo, limit=100, include_drafts=False)
    except PlatformError:
        return (stable, None)

    if not stable:
        stable, _ = _pick_latest_tags(releases)

    pre = None
    for r in releases:
        if r.prerelease and pre is None:
            pre = r.tag
        if pre:
            break

    return (stable, pre)


def _get_latest_stable_via_gh(repo: str) -> Optional[str]:
    if not _gh_available():
        return None
    try:
        res = run_cmd(
            ["gh", "api", f"repos/{repo}/releases/latest", "-q", ".tag_name"],
            check=True,
            encoding="utf-8",
            errors="replace",
        )
    except PlatformError:
        return None
    tag = (res.stdout or "").strip()
    if not tag or not _is_supported_release_tag(tag):
        return None
    return normalize_tag(tag)


def _pick_latest_tags(releases: list[ReleaseTag]) -> tuple[Optional[str], Optional[str]]:
    stable = None
    pre = None
    for r in releases:
        if r.prerelease and pre is None:
            pre = r.tag
        if (not r.prerelease) and stable is None:
            stable = r.tag
        if stable and pre:
            break
    return (stable, pre)


def resolve_latest_stable_target(repo: str, *, allow_prompt_token: bool = False) -> tuple[str, str, bool]:
    current_tag = current_version_tag()
    normalized_stable = get_latest_stable_anchor(repo, allow_prompt_token=allow_prompt_token)
    return (current_tag, normalized_stable, current_tag != normalized_stable)


def _get_latest_tags(repo: str, *, allow_prompt_token: bool = False) -> tuple[Optional[str], Optional[str]]:
    stable, pre = _get_latest_tags_via_gh(repo)
    if stable and pre:
        return (stable, pre)

    if not stable or not pre:
        try:
            releases = list_release_tags(repo, limit=100, include_drafts=False)
        except PlatformError:
            releases = []
        fallback_stable, fallback_pre = _pick_latest_tags(releases)
        stable = stable or fallback_stable
        pre = pre or fallback_pre

    token = _token_from_env() or _token_from_managed_auth_state()
    if (not token) and allow_prompt_token and (not bool(cli_ctx.non_interactive)):
        token = _resolve_github_token(repo)
    if not stable and token:
        stable = _get_latest_stable_via_api(repo, token=token)
        if not stable:
            try:
                releases = _list_release_tags_via_api(repo, limit=100, include_drafts=False, token=token)
            except PlatformError:
                releases = []
            stable_from_api_list, pre_from_api_list = _pick_latest_tags(releases)
            stable = stable or stable_from_api_list
            pre = pre or pre_from_api_list
    if not pre and token:
        try:
            releases = _list_release_tags_via_api(repo, limit=100, include_drafts=False, token=token)
        except PlatformError:
            releases = []
        _, pre = _pick_latest_tags(releases)
    return (stable, pre)


def get_latest_stable_anchor(repo: str, *, allow_prompt_token: bool = False) -> str:
    stable_tag, _ = _get_latest_tags(repo, allow_prompt_token=allow_prompt_token)
    if not stable_tag:
        raise PlatformError(
            f"No stable GHDP release could be resolved from '{repo}'.",
            code="E_NO_STABLE_RELEASE",
            reason=repo,
        )
    return normalize_tag(stable_tag)


def _get_latest_stable_via_api(repo: str, *, token: str) -> Optional[str]:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urlrequest.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urlrequest.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None
    tag = str(payload.get("tag_name") or "").strip()
    if not tag or not _is_supported_release_tag(tag):
        return None
    return normalize_tag(tag)

def _token_from_env() -> str:
    return github_auth.direct_github_token()


def _managed_install_marker_path() -> Path:
    return github_auth.managed_install_marker_path()


def _managed_install_marker_exists() -> bool:
    return github_auth.managed_install_marker_exists()


def _token_from_managed_auth_state() -> str:
    if not _managed_install_marker_exists():
        return ""
    return github_auth.managed_install_token()


def _token_from_aws_secret() -> str:
    # Legacy name retained for compatibility with older tests and call sites.
    return _token_from_managed_auth_state()


def _token_from_managed_aws_secret() -> str:
    # Legacy name retained for compatibility with older tests and call sites.
    return _token_from_managed_auth_state()


def _managed_github_support_message() -> str:
    return "The GHDP managed GitHub auth token is unavailable in this managed build. Please contact the platform team."


def _token_from_gh_no_prompt(repo: str) -> str:
    try:
        res = run_cmd(["gh", "auth", "token"], check=False, env=github_auth.gh_subprocess_env())
    except PlatformError:
        return ""
    token = (res.stdout or "").strip()
    return token if res.returncode == 0 else ""


def _release_body_via_gh(repo: str, tag: str) -> str:
    if not _gh_available():
        return ""
    try:
        res = run_cmd(
            ["gh", "api", f"repos/{repo}/releases/tags/{tag}", "-q", ".body"],
            check=False,
            env=github_auth.gh_subprocess_env(),
            encoding="utf-8",
            errors="replace",
        )
    except PlatformError:
        return ""
    return (res.stdout or "").strip() if res.returncode == 0 else ""


def _release_body_via_api(repo: str, tag: str, token: str) -> str:
    rel_url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    rel_req = urlrequest.Request(rel_url)
    rel_req.add_header("Accept", "application/vnd.github+json")
    rel_req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if token:
        rel_req.add_header("Authorization", f"Bearer {token}")

    try:
        with urlrequest.urlopen(rel_req, timeout=30) as rel_resp:
            payload = json.loads(rel_resp.read().decode("utf-8"))
    except Exception:
        return ""

    if not isinstance(payload, dict):
        return ""
    return (payload.get("body") or "").strip()


def _print_release_notes_summary(repo: str, tag: str) -> None:
    body = _release_body_via_gh(repo, tag)
    if not body:
        token = _token_from_env() or _token_from_managed_auth_state() or _token_from_gh_no_prompt(repo)
        body = _release_body_via_api(repo, tag, token)

    summary = extract_release_summary(body)
    rprint("[dim]Release summary:[/dim]")
    if "\n" in summary:
        rprint(summary)
    else:
        rprint(f"  • {summary}")


def _pipx_has_ghdp() -> bool:
    if not _pipx_available():
        return False

    try:
        res = run_cmd(["pipx", "list", "--json"], check=False)
    except PlatformError:
        return False

    if res.returncode != 0:
        return False

    try:
        payload = json.loads(res.stdout or "{}")
    except Exception:
        return False

    if not isinstance(payload, dict):
        return False

    venvs = payload.get("venvs", {})
    return isinstance(venvs, dict) and "ghdp" in venvs


def _gh_ready_for_repo(repo: str) -> tuple[bool, str, str]:
    if not _gh_available():
        return (False, "gh_not_installed", "")

    gh_env = github_auth.gh_subprocess_env()
    try:
        auth = run_cmd(["gh", "auth", "status"], check=False, env=gh_env)
    except PlatformError:
        return (False, "gh_auth_check_failed", "")
    if auth.returncode != 0:
        return (False, "gh_not_authenticated", "")

    try:
        repo_access = run_cmd(["gh", "api", f"repos/{repo}"], check=False, env=gh_env)
    except PlatformError:
        return (False, "gh_repo_scope_check_failed", "")
    if repo_access.returncode != 0:
        return (False, "repo_not_in_scope", "")

    try:
        user = run_cmd(["gh", "api", "user", "-q", ".login"], check=False, env=gh_env)
    except PlatformError:
        return (True, "ok", "")

    login = (user.stdout or "").strip() if user.returncode == 0 else ""
    return (True, "ok", login)


def _resolve_github_token(repo: str) -> str:
    token = _token_from_env()
    if token:
        return token

    if _managed_install_marker_exists():
        token = _token_from_managed_auth_state()
        if token:
            return token

        if bool(cli_ctx.non_interactive):
            raise PlatformError(
                _managed_github_support_message(),
                code="E_GITHUB_TOKEN_REQUIRED",
                reason="managed_token_unavailable",
            )

        token = typer.prompt(
            "Enter GitHub token (PAT) to continue",
            hide_input=True,
            default="",
            show_default=False,
        ).strip()
        if token:
            return token

        raise PlatformError(
            _managed_github_support_message(),
            code="E_GITHUB_TOKEN_REQUIRED",
            reason="managed_token_unavailable",
        )

    gh_ready, gh_reason, gh_login = _gh_ready_for_repo(repo)
    if gh_ready:
        try:
            res = run_cmd(["gh", "auth", "token"], check=False, env=github_auth.gh_subprocess_env())
            token = (res.stdout or "").strip()
            if res.returncode == 0 and token:
                if gh_login:
                    rprint(f"[dim]Using GitHub CLI auth for user '{gh_login}'.[/dim]")
                return token
        except PlatformError:
            pass
    else:
        rprint(f"[yellow]GitHub CLI auth is not ready ({gh_reason}); falling back to PAT.[/yellow]")

    if bool(cli_ctx.non_interactive):
        return ""

    token = typer.prompt(
        "Enter GitHub token (PAT) to continue",
        hide_input=True,
        default="",
        show_default=False,
    ).strip()
    return token


def _apply_update_via_pipx(repo: str, tag: str) -> InstallResult:
    """
    Update GHDP installed from git subdirectory using pipx.
    """
    cmd = [
        "pipx",
        "install",
        "--force",
        f"git+https://github.com/{repo}.git@{tag}#subdirectory=platform-cli",
    ]
    rprint(f"[dim]Running:[/dim] [bold]{' '.join(cmd)}[/bold]")
    try:
        run_cmd(cmd, check=True)
    except PlatformError as e:
        raise PlatformError(
            f"Failed to update GHDP via pipx: {e}",
            code="E_UPDATE_INSTALL_FAILED",
            reason="pipx_install_failed",
        )
    _record_install_phase("verified", target_tag=normalize_tag(tag), active_tag=normalize_tag(tag), detail="pipx")
    return InstallResult(
        method="pipx",
        target_tag=normalize_tag(tag),
        verification_status="verified",
        active_tag=normalize_tag(tag),
        detail="pipx",
    )


def _installer_script_path() -> Path:
    root = Path(__file__).resolve().parents[3]
    name = "install_ghdp.ps1" if os.name == "nt" else "install_ghdp.sh"
    script = root / name
    if not script.exists():
        raise PlatformError(
            f"Installer script not found: {script}",
            code="E_INSTALLER_NOT_FOUND",
            reason="missing_installer_script",
        )
    return script




def _release_asset_name() -> str:
    sys_name = platform.system().lower()
    machine = platform.machine().lower()

    if sys_name == "darwin":
        os_part = "darwin"
    elif sys_name == "linux":
        os_part = "linux"
    elif sys_name == "windows":
        os_part = "windows"
    else:
        raise PlatformError(
            f"Unsupported OS for binary install: {sys_name}",
            code="E_UPDATE_INSTALL_FAILED",
            reason="unsupported_os",
        )

    if machine in {"arm64", "aarch64"}:
        arch_part = "arm64"
    elif machine in {"x86_64", "amd64"}:
        arch_part = "amd64"
    else:
        raise PlatformError(
            f"Unsupported architecture for binary install: {machine}",
            code="E_UPDATE_INSTALL_FAILED",
            reason="unsupported_arch",
        )

    suffix = ".exe" if os_part == "windows" else ""
    return f"ghdp-{os_part}-{arch_part}{suffix}"


def _download_release_asset_direct(repo: str, tag: str, asset: str, dest: Path) -> None:
    url = f"https://github.com/{repo}/releases/download/{tag}/{asset}"
    req = urlrequest.Request(url)
    try:
        with urlrequest.urlopen(req, timeout=60) as resp:
            dest.write_bytes(resp.read())
    except Exception as e:
        raise PlatformError(
            f"Failed to download GHDP asset from release {tag}: {e}",
            code="E_UPDATE_INSTALL_FAILED",
            reason="asset_download_failed_direct",
        )
def _download_release_asset_via_api(repo: str, tag: str, asset: str, token: str, dest: Path) -> None:
    rel_url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    rel_req = urlrequest.Request(rel_url)
    rel_req.add_header("Accept", "application/vnd.github+json")
    rel_req.add_header("Authorization", f"Bearer {token}")
    rel_req.add_header("X-GitHub-Api-Version", "2022-11-28")

    try:
        with urlrequest.urlopen(rel_req, timeout=30) as rel_resp:
            payload = json.loads(rel_resp.read().decode("utf-8"))
    except Exception as e:
        raise PlatformError(
            f"Failed to fetch release metadata for {tag}: {e}",
            code="E_UPDATE_INSTALL_FAILED",
            reason="release_metadata_fetch_failed",
        )

    assets = payload.get("assets") if isinstance(payload, dict) else None
    if not isinstance(assets, list):
        raise PlatformError(
            f"Release metadata for {tag} is missing assets.",
            code="E_UPDATE_INSTALL_FAILED",
            reason="release_assets_missing",
        )

    asset_id = None
    for item in assets:
        if isinstance(item, dict) and (item.get("name") or "").strip() == asset:
            asset_id = item.get("id")
            break

    if not asset_id:
        raise PlatformError(
            f"Release {tag} does not contain required asset '{asset}'.",
            code="E_UPDATE_INSTALL_FAILED",
            reason="asset_not_found",
        )

    dl_url = f"https://api.github.com/repos/{repo}/releases/assets/{asset_id}"
    dl_req = urlrequest.Request(dl_url)
    dl_req.add_header("Accept", "application/octet-stream")
    dl_req.add_header("Authorization", f"Bearer {token}")
    dl_req.add_header("X-GitHub-Api-Version", "2022-11-28")

    try:
        with urlrequest.urlopen(dl_req, timeout=60) as dl_resp:
            dest.write_bytes(dl_resp.read())
    except Exception as e:
        raise PlatformError(
            f"Failed to download GHDP asset via GitHub API: {e}",
            code="E_UPDATE_INSTALL_FAILED",
            reason="asset_download_failed_api",
        )


def _download_release_asset_via_gh(repo: str, tag: str, asset: str, dest: Path) -> None:
    if not _gh_available():
        raise PlatformError(
            "GitHub CLI is not available for release download fallback.",
            code="E_UPDATE_INSTALL_FAILED",
            reason="gh_release_download_unavailable",
        )

    cmd = [
        "gh",
        "release",
        "download",
        tag,
        "--repo",
        repo,
        "--pattern",
        asset,
        "--output",
        str(dest),
        "--clobber",
    ]
    res = run_cmd(cmd, check=False, env=github_auth.gh_subprocess_env())
    if res.returncode != 0:
        detail = res.stderr or res.stdout or "unknown gh release download failure"
        raise PlatformError(
            f"Failed to download GHDP asset via GitHub CLI: {detail}",
            code="E_UPDATE_INSTALL_FAILED",
            reason="gh_release_download_failed",
        )
    if (not dest.exists()) or dest.stat().st_size == 0:
        raise PlatformError(
            f"GitHub CLI did not produce asset '{asset}' for release {tag}.",
            code="E_UPDATE_INSTALL_FAILED",
            reason="gh_release_download_missing_file",
        )


def _default_binary_install_dir() -> Path:
    if os.name == "nt":
        return Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "ghdp" / "bin"

    for raw in (os.getenv("PATH") or "").split(os.pathsep):
        p = (raw or "").strip()
        if not p or p == ".":
            continue
        candidate = Path(p).expanduser()
        if candidate.is_dir() and os.access(candidate, os.W_OK | os.X_OK):
            return candidate

    return Path.home() / ".local" / "bin"


def _ensure_runtime_env_exists() -> None:
    runtime_env = runtime_env_path()
    runtime_env.parent.mkdir(parents=True, exist_ok=True)
    if runtime_env.exists():
        return
    runtime_env.write_text(
        "\n".join(
            [
                "# GHDP user runtime overrides",
                "# Add per-user values here. These override installed defaults.",
                "# Example:",
                "# GHDP_DEFAULT_REPO=gh-org-data-platform/dp-tools-local-setup",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

def _cleanup_windows_staged_binary(staged: Path) -> None:
    if not staged.exists():
        return
    try:
        staged.unlink()
    except Exception:
        pass


def _swap_windows_binary_now(*, staged: Path, target: Path, expected_tag: str) -> bool:
    normalized = normalize_tag(expected_tag)
    for _ in range(120):
        try:
            if staged.exists():
                os.replace(str(staged), str(target))
        except Exception:
            pass
        if target.exists() and _verify_binary_version(target, normalized, attempts=1, sleep_seconds=0):
            return True
        time.sleep(0.25)
    return False


def _launch_windows_swap_helper(*, staged: Path, target: Path, expected_tag: str) -> None:
    src = str(staged).replace("'", "''")
    dst = str(target).replace("'", "''")
    expected = normalize_tag(expected_tag).replace("'", "''")
    swap_script = (
        f"$src='{src}';"
        f"$dst='{dst}';"
        f"$expected='{expected}';"
        "$ok=$false;"
        "for($i=0; $i -lt 120 -and -not $ok; $i++){"
        "Start-Sleep -Milliseconds 250;"
        "try { if (Test-Path $src) { Move-Item -Force $src $dst } } catch {}"
        "try { "
        "  if (Test-Path $dst) { "
        "    $v = (& $dst --version 2>$null | Select-Object -First 1);"
        "    if ($v -match 'ghdp\\s+([0-9A-Za-z._-]+)') { "
        "      $observed = $Matches[1];"
        "      if (-not $observed.StartsWith('v')) { $observed = 'v' + $observed }"
        "      if ($observed -eq $expected) { $ok=$true }"
        "    }"
        "  } "
        "} catch {}"
        "}"
        "if(-not $ok){ exit 1 }"
    )
    encoded = base64.b64encode(swap_script.encode("utf-16le")).decode("ascii")
    launcher = (
        "Start-Process -WindowStyle Hidden powershell "
        f"-ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand','{encoded}')"
    )
    run_cmd(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", launcher],
        check=True,
    )


def _apply_update_via_release_binary(repo: str, tag: str) -> InstallResult:
    normalized_tag = normalize_tag(tag)
    current_executable = _current_executable_path()
    asset = _release_asset_name()
    token = _resolve_github_token(repo)
    default_install_dir = _default_binary_install_dir()
    install_dir = Path((os.getenv("GHDP_INSTALL_DIR") or str(default_install_dir)).strip())
    target = install_dir / ("ghdp.exe" if os.name == "nt" else "ghdp")

    fd, tmp_path = tempfile.mkstemp(prefix="ghdp_bin_", suffix="")
    os.close(fd)
    tmp = Path(tmp_path)

    try:
        if token:
            try:
                _download_release_asset_via_api(repo, tag, asset, token, tmp)
            except PlatformError as e:
                if _gh_available():
                    rprint(f"[yellow]GitHub API download failed; retrying via gh CLI.[/yellow] [dim]{e}[/dim]")
                    _download_release_asset_via_gh(repo, tag, asset, tmp)
                else:
                    raise
        else:
            _download_release_asset_direct(repo, tag, asset, tmp)

        install_dir.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            staged = install_dir / "ghdp.new.exe"
            _cleanup_windows_staged_binary(staged)
            shutil.move(str(tmp), str(staged))
            _record_install_phase("staged", target_tag=normalized_tag, detail=str(staged))
            if not _paths_match(current_executable, target):
                if not _swap_windows_binary_now(staged=staged, target=target, expected_tag=normalized_tag):
                    _record_install_phase("failed_swap", target_tag=normalized_tag, detail=str(target))
                    raise PlatformError(
                        f"Installed GHDP release {normalized_tag} was staged but the active binary at '{target}' did not update.",
                        code="E_UPDATE_VERIFY_FAILED",
                        reason="windows_swap_unverified",
                    )
                _ensure_runtime_env_exists()
                _record_install_phase("verified", target_tag=normalized_tag, active_tag=normalized_tag)
                return InstallResult(
                    method="installer",
                    target_tag=normalized_tag,
                    verification_status="verified",
                    active_tag=normalized_tag,
                    detail=str(target),
                )
            _launch_windows_swap_helper(staged=staged, target=target, expected_tag=normalized_tag)
            _record_install_phase("pending_swap", target_tag=normalized_tag, active_tag=_binary_version_tag(target), detail=str(staged))
            return InstallResult(
                method="installer",
                target_tag=normalized_tag,
                verification_status="pending_swap",
                active_tag=_binary_version_tag(target),
                detail=str(staged),
            )
        else:
            tmp.chmod(0o755)
            shutil.move(str(tmp), str(target))
        _ensure_runtime_env_exists()
        _record_install_phase("verified", target_tag=normalized_tag, active_tag=normalized_tag)
        rprint(f"[green]Installed GHDP release {normalized_tag} ({asset}).[/green]")
        return InstallResult(
            method="installer",
            target_tag=normalized_tag,
            verification_status="verified",
            active_tag=normalized_tag,
            detail=str(target),
        )
    except PlatformError:
        raise
    except Exception as e:
        _record_install_phase("failed", target_tag=normalized_tag, detail=str(e))
        raise PlatformError(
            f"Failed to install GHDP binary: {e}",
            code="E_UPDATE_INSTALL_FAILED",
            reason="binary_install_failed",
        )
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass



def _apply_update_via_installer(repo: str, tag: str) -> None:
    script = None
    try:
        script = _installer_script_path()
    except PlatformError as e:
        if e.code != "E_INSTALLER_NOT_FOUND":
            raise
        return _apply_update_via_release_binary(repo, tag)

    env = github_auth.gh_subprocess_env(os.environ)
    env["GHDP_REPO"] = repo
    env["GHDP_VERSION"] = tag
    token = _resolve_github_token(repo)
    if token:
        env["GHDP_TOKEN"] = token
        env["GH_TOKEN"] = token
        env["GITHUB_TOKEN"] = token
    if _managed_install_marker_exists():
        env.setdefault("GHDP_MANAGED_INSTALL", "1")

    if os.name == "nt":
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ]
    else:
        cmd = ["bash", str(script)]

    rprint(f"[dim]Running installer:[/dim] [bold]{' '.join(cmd)}[/bold]")
    try:
        run_cmd(cmd, check=True, env=env)
        normalized = normalize_tag(tag)
        _ensure_runtime_env_exists()
        _record_install_phase("verified", target_tag=normalized, active_tag=normalized, detail="installer_script")
        return InstallResult(
            method="installer",
            target_tag=normalized,
            verification_status="verified",
            active_tag=normalized,
            detail="installer_script",
        )
    except PlatformError as e:
        if os.name == "nt":
            rprint("[yellow]Installer script failed on Windows; falling back to direct binary install.[/yellow]")
            return _apply_update_via_release_binary(repo, tag)
        raise PlatformError(
            f"Failed to update GHDP via installer script: {e}",
            code="E_UPDATE_INSTALL_FAILED",
            reason="installer_run_failed",
        )

def install_selected_version_detailed(
    repo: str,
    tag: str,
    *,
    method: str = "auto",
) -> InstallResult:
    normalized = normalize_tag(tag)
    selected = (method or "auto").strip().lower()

    if selected not in {"auto", "pipx", "installer"}:
        raise PlatformError(
            f"Unsupported install method '{method}'. Use one of: auto, pipx, installer.",
            code="E_BAD_ARGS",
            reason="bad_method",
        )

    _print_release_notes_summary(repo, normalized)

    if selected == "auto":
        if os.name == "nt":
            selected = "installer"
        else:
            selected = "pipx" if _pipx_has_ghdp() else "installer"

    if selected == "pipx":
        if not _pipx_available():
            raise PlatformError(
                "pipx not found; cannot update via pipx.",
                code="E_PIPX_NOT_FOUND",
                reason="pipx_missing",
            )
        return _apply_update_via_pipx(repo, normalized)

    return _apply_update_via_installer(repo, normalized)


def install_selected_version(
    repo: str,
    tag: str,
    *,
    method: str = "auto",
) -> str:
    return install_selected_version_detailed(repo, tag, method=method).method


def maybe_check_for_update(force: bool = False) -> bool:
    global _CHECKED
    if _CHECKED:
        return False
    _CHECKED = True

    # config + env opt-out
    if os.getenv("GHDP_UPDATE_CHECK_DISABLE") == "1":
        return False

    repo = (os.getenv("GHDP_UPDATE_REPO") or DEFAULT_REPO).strip()
    if not repo:
        return False

    # persistent throttle across processes
    now = int(time.time())
    st = get_tool_state("ghdp")
    last_checked = int(st.get("update_last_checked_at", 0) or 0)

    if (not force) and (now - last_checked) < CHECK_INTERVAL_S:
        return False

    update_tool_state("ghdp", {"update_last_checked_at": now})

    stable_tag, pre_tag = _get_latest_tags(repo, allow_prompt_token=force)
    current_tag = f"v{__version__.lstrip('v')}"  # normalize display
    # Decide target:
    target_tag = None
    target_is_prerelease = False

    if stable_tag and _is_newer(current_tag, stable_tag):
        target_tag = stable_tag
        target_is_prerelease = False
    else:
        if force:
            current_is_prerelease = "-" in current_tag
            if stable_tag and current_is_prerelease and _is_newer(stable_tag, current_tag):
                rprint(
                    f"[dim]No newer stable release found. "
                    f"You are on pre-release {current_tag}, ahead of stable {stable_tag}.[/dim]"
                )
            else:
                rprint(f"[dim]No newer stable GHDP release found. Current version: {current_tag}[/dim]")
        return False

    # Don’t spam the same tag repeatedly on background checks,
    # but always allow explicit `ghdp doctor` to re-offer the update.
    last_notified = (st.get("update_last_notified_tag") or "").strip()
    if (not force) and last_notified == target_tag:
        return False

    update_tool_state(
        "ghdp",
        {
            "update_last_notified_tag": target_tag,
            "update_last_notified_at": now,
            "update_target_is_prerelease": target_is_prerelease,
        },
    )

    label = "pre-release" if target_is_prerelease else "release"
    rprint(
        f"[dim]Update available ({label}): "
        f"[yellow]{current_tag}[/yellow] → [green]{target_tag}[/green][/dim]"
    )

    # Non-interactive: just print hint, no prompt
    if bool(cli_ctx.non_interactive):
        rprint(
            f"[dim]To update (example):[/dim]\n"
            f"  [bold]pipx install --force "
            f"git+https://github.com/{repo}.git@{target_tag}#subdirectory=platform-cli[/bold]"
        )
        return False

    # Interactive prompt
    msg = (
        f"Install {label} {target_tag} now?"
        + (
            " (Recommended to use stable unless you need fixes/features.)"
            if target_is_prerelease
            else ""
        )
    )
    if not typer.confirm(msg, default=False):
        return False

    install_selected_version(repo, target_tag, method="auto")
    return True
