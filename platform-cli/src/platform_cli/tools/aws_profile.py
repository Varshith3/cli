# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import typer

from platform_cli.core.config import get_value, set_value
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.errors import PlatformError
from platform_cli.exec.runner import run_cmd
from platform_cli.state.store import FileLock, default_state_paths, load_state, save_state


GLOBAL_ACTIVE_PROFILE_KEY = "aws.active_profile"
STATE_AWS_KEY = "aws"
STATE_REPO_ACTIVE_PROFILES_KEY = "repo_active_profiles"
PROFILE_MARKER_START = "# Added by GHDP AWS profile sync"
PROFILE_MARKER_END = "# End GHDP AWS profile sync"


@dataclass(frozen=True)
class AwsProfileResolution:
    profile: str
    source: str  # flag | env | repo | global | prompt | default
    repo_key: str


def _normalize_profile_name(name: str) -> str:
    return (name or "").strip()


def _repo_root() -> str:
    try:
        res = run_cmd(["git", "rev-parse", "--show-toplevel"], check=False, capture=True)
        out = (res.stdout or "").strip()
        return out
    except Exception:
        return ""


def _repo_key() -> str:
    root = _repo_root()
    if not root:
        return ""
    return str(Path(root).resolve()).lower()


def _get_repo_active_profiles_map() -> Dict[str, str]:
    st = load_state()
    aws_obj = st.get(STATE_AWS_KEY, {}) or {}
    if not isinstance(aws_obj, dict):
        return {}
    repo_obj = aws_obj.get(STATE_REPO_ACTIVE_PROFILES_KEY, {}) or {}
    if not isinstance(repo_obj, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in repo_obj.items():
        if isinstance(k, str) and isinstance(v, str) and v.strip():
            out[k] = v.strip()
    return out


def _set_repo_active_profile(repo_key: str, profile: str) -> None:
    paths = default_state_paths()
    with FileLock(paths.lock_file):
        st = load_state(paths)
        aws_obj = st.setdefault(STATE_AWS_KEY, {})
        if not isinstance(aws_obj, dict):
            aws_obj = {}
            st[STATE_AWS_KEY] = aws_obj
        repo_obj = aws_obj.setdefault(STATE_REPO_ACTIVE_PROFILES_KEY, {})
        if not isinstance(repo_obj, dict):
            repo_obj = {}
            aws_obj[STATE_REPO_ACTIVE_PROFILES_KEY] = repo_obj
        repo_obj[repo_key] = profile
        save_state(st, paths)


def get_global_active_profile() -> str:
    return _normalize_profile_name(str(get_value(GLOBAL_ACTIVE_PROFILE_KEY, "") or ""))


def set_global_active_profile(profile: str) -> None:
    set_value(GLOBAL_ACTIVE_PROFILE_KEY, _normalize_profile_name(profile))


def _is_windows() -> bool:
    return os.name == "nt"


def _windows_profile_path() -> Path:
    return Path.home() / "Documents" / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1"


def _unix_profile_path() -> Path:
    shell = (os.environ.get("SHELL", "") or "").lower()
    if "bash" in shell:
        return Path.home() / ".bashrc"
    return Path.home() / ".zshrc"


def _profile_path() -> Path:
    return _windows_profile_path() if _is_windows() else _unix_profile_path()


def _profile_block(profile: str) -> str:
    if _is_windows():
        return "\n".join(
            [
                PROFILE_MARKER_START,
                f'$env:AWS_PROFILE = "{profile}"',
                PROFILE_MARKER_END,
            ]
        ) + "\n"
    return "\n".join(
        [
            PROFILE_MARKER_START,
            f'export AWS_PROFILE="{profile}"',
            PROFILE_MARKER_END,
        ]
    ) + "\n"


def _upsert_profile_block(profile_path: Path, profile: str) -> None:
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    existing = profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
    block = _profile_block(profile)
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


def _persist_windows_user_env(profile: str) -> None:
    ps = f"[Environment]::SetEnvironmentVariable('AWS_PROFILE', '{profile}', 'User')"
    run_cmd(["powershell", "-NoProfile", "-Command", ps], check=True, capture=True)


def apply_active_profile_env(profile: str, *, scope: str = "global") -> None:
    selected = _normalize_profile_name(profile) or "default"
    os.environ["AWS_PROFILE"] = selected
    if (scope or "global").strip().lower() != "global":
        return
    if _is_windows():
        _persist_windows_user_env(selected)
        return
    _upsert_profile_block(_profile_path(), selected)


def set_active_profile(profile: str, scope: str = "global") -> str:
    p = _normalize_profile_name(profile) or "default"
    s = (scope or "global").strip().lower()
    if s == "repo":
        key = _repo_key()
        if not key:
            raise ValueError("Cannot set repo-scoped AWS profile outside a git repository.")
        _set_repo_active_profile(key, p)
        apply_active_profile_env(p, scope="repo")
        return "repo"

    set_global_active_profile(p)
    apply_active_profile_env(p, scope="global")
    return "global"


def get_repo_active_profile() -> str:
    key = _repo_key()
    if not key:
        return ""
    return _normalize_profile_name(_get_repo_active_profiles_map().get(key, ""))


def prompt_aws_profile_choice(default_profile: str = "default") -> str:
    profiles = list_configured_aws_profiles()
    if profiles:
        typer.echo("Pick an existing AWS CLI profile by number or type the profile name.")
        typer.echo("Available AWS profiles:")
        for idx, prof in enumerate(profiles, start=1):
            typer.echo(f"  {idx}. {prof}")
        typer.echo("Enter profile number or type profile name.")
    else:
        typer.echo(
            "Choose an AWS CLI profile name for first-time setup. Use 'default' unless your team has asked you to use a different profile."
        )

    raw = _normalize_profile_name(typer.prompt("AWS profile", default=default_profile))
    if not raw:
        return default_profile

    if raw.isdigit() and profiles:
        i = int(raw) - 1
        if 0 <= i < len(profiles):
            return profiles[i]
        typer.echo("Invalid profile number; using entered text as profile name.")
    return raw


def resolve_aws_profile(
    *,
    explicit_profile: Optional[str] = None,
    prompt_if_unresolved: bool = False,
    prompt_when_flag_missing: bool = False,
    persist_prompt_scope: str = "global",
    allow_default_fallback: bool = True,
) -> AwsProfileResolution:
    p = _normalize_profile_name(explicit_profile or "")
    repo_key = _repo_key()
    if p:
        return AwsProfileResolution(profile=p, source="flag", repo_key=repo_key)

    if prompt_when_flag_missing and not bool(cli_ctx.non_interactive):
        prompted = prompt_aws_profile_choice(default_profile="default")
        set_active_profile(prompted, scope=persist_prompt_scope)
        return AwsProfileResolution(profile=prompted, source="prompt", repo_key=repo_key)

    env_profile = _normalize_profile_name(str(os.environ.get("AWS_PROFILE", "") or ""))
    if env_profile:
        return AwsProfileResolution(profile=env_profile, source="env", repo_key=repo_key)

    repo_profile = get_repo_active_profile()
    if repo_profile:
        return AwsProfileResolution(profile=repo_profile, source="repo", repo_key=repo_key)

    global_profile = get_global_active_profile()
    if global_profile:
        return AwsProfileResolution(profile=global_profile, source="global", repo_key=repo_key)

    if prompt_if_unresolved and not bool(cli_ctx.non_interactive):
        prompted = prompt_aws_profile_choice(default_profile="default")
        set_active_profile(prompted, scope=persist_prompt_scope)
        return AwsProfileResolution(profile=prompted, source="prompt", repo_key=repo_key)

    if not allow_default_fallback:
        raise PlatformError(
            "AWS profile could not be resolved. Pass --profile, set AWS_PROFILE, or set an active profile via `ghdp aws profile use`.",
            code="E_AWS_PROFILE_UNRESOLVED",
            reason="aws_profile",
        )

    return AwsProfileResolution(profile="default", source="default", repo_key=repo_key)


def list_configured_aws_profiles() -> List[str]:
    aws_dir = Path.home() / ".aws"
    config_path = aws_dir / "config"
    credentials_path = aws_dir / "credentials"
    profiles: List[str] = []

    if config_path.exists():
        try:
            text = config_path.read_text(encoding="utf-8", errors="ignore")
            profiles.extend(_profiles_from_config_text(text))
        except Exception:
            pass

    if credentials_path.exists():
        try:
            text = credentials_path.read_text(encoding="utf-8", errors="ignore")
            profiles.extend(_profiles_from_credentials_text(text))
        except Exception:
            pass

    return sorted(set([p for p in profiles if p]))


def profile_exists(profile: str, candidates: Optional[Iterable[str]] = None) -> bool:
    name = _normalize_profile_name(profile)
    if not name:
        return False
    pool = candidates if candidates is not None else list_configured_aws_profiles()
    return name in set([_normalize_profile_name(p) for p in pool])


def _profiles_from_config_text(text: str) -> List[str]:
    profiles: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not (line.startswith("[") and line.endswith("]")):
            continue
        body = line[1:-1].strip()
        if not body:
            continue
        if body.lower() == "default":
            profiles.append("default")
            continue
        m = re.match(r"^profile\s+(.+)$", body, flags=re.IGNORECASE)
        if m:
            name = _normalize_profile_name(m.group(1))
            if name:
                profiles.append(name)
    return profiles


def _profiles_from_credentials_text(text: str) -> List[str]:
    profiles: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not (line.startswith("[") and line.endswith("]")):
            continue
        body = _normalize_profile_name(line[1:-1])
        if body:
            profiles.append(body)
    return profiles
