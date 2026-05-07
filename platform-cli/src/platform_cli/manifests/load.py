# NOTE: Architectural rules in ARCHITECTURE.md -- do not refactor cross-layer.
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import importlib.resources as pkg_resources

# TODO: align this to your single PlatformError import (core.errors) once repo is unified.
try:
    from platform_cli.core.errors import PlatformError  # type: ignore
except Exception:  # pragma: no cover
    class PlatformError(RuntimeError):  # fallback to keep dev unblocked
        def __init__(self, message: str, code: str = "E_INTERNAL", reason: str = "UNKNOWN", alert: bool = False):
            super().__init__(message)
            self.code = code
            self.reason = reason
            self.alert = alert


DEFAULT_TOOLSET_NAME = "toolset.json"
DEFAULT_MANAGED_TOOLSET_NAME = "team-toolset.managed.json"
DEFAULT_REGISTRY_NAME = "tool-registry.json"
DEFAULT_CONFIG_DEFAULTS_NAME = "config-defaults.json"
DEFAULT_TERRAFORM_LOCAL_POLICY_NAME = "terraform_local.json"
DEFAULT_ACCESS_POLICY_NAME = "access_policy.json"
LEGACY_ACCESS_POLICY_NAME = "access_phase0.json"
DEFAULT_TEAM_POLICY_NAME = "team-policy.managed.json"
DEFAULT_TEAM_SYNC_POLICY_FALLBACK_NAME = "team-sync-policy.fallback.json"
DEFAULT_RELEASE_POLICY_NAME = "release_policy.json"
DEFAULT_MANAGED_RELEASE_POLICY_NAME = "release-policy.managed.json"
DEFAULT_COMMAND_RESTRICTIONS_POLICY_NAME = "command_restrictions.json"
DEFAULT_MANAGED_COMMAND_RESTRICTIONS_POLICY_NAME = "command-restrictions.managed.json"
DEFAULT_SCHEDULER_INSTALL_POLICY_NAME = "scheduler_install.json"
DEFAULT_MANAGED_SCHEDULER_INSTALL_POLICY_NAME = "scheduler-install.managed.json"
DEFAULT_ACCOUNT_ENVIRONMENTS_NAME = "account-environments.json"
DEFAULT_CLAUDE_ATHENA_WORKGROUP_MAP_NAME = "athena-workgroup-map.json"
DEFAULT_CLAUDE_ATHENA_WORKGROUP_MAP_BACKUP_NAME = "athena-workgroup-map.backup.json"
DEFAULT_USER_CLAUDE_ATHENA_WORKGROUP_MAP_NAME = "claude-athena-workgroup-map.json"
DEFAULT_MANAGED_CLAUDE_ATHENA_WORKGROUP_MAP_NAME = "claude-athena-workgroup-map.managed.json"
CLAUDE_ATHENA_WORKGROUP_MAP_ENV_KEY = "GHDP_CLAUDE_ATHENA_WORKGROUP_MAP_PATH"


def preferred_user_access_policy_path() -> Path:
    return Path.home() / ".ghdp" / "policy" / DEFAULT_ACCESS_POLICY_NAME


def preferred_user_toolset_path() -> Path:
    return Path.home() / ".ghdp" / "manifests" / DEFAULT_TOOLSET_NAME


def preferred_legacy_toolset_path() -> Path:
    return Path.home() / ".ghdp" / DEFAULT_TOOLSET_NAME


def preferred_managed_toolset_path() -> Path:
    return Path.home() / ".ghdp" / "policies" / DEFAULT_MANAGED_TOOLSET_NAME


def preferred_user_claude_athena_workgroup_map_path() -> Path:
    return Path.home() / ".ghdp" / "policy" / DEFAULT_USER_CLAUDE_ATHENA_WORKGROUP_MAP_NAME


def preferred_managed_claude_athena_workgroup_map_path() -> Path:
    return Path.home() / ".ghdp" / "policies" / DEFAULT_MANAGED_CLAUDE_ATHENA_WORKGROUP_MAP_NAME


def load_packaged_team_sync_policy_fallback() -> Tuple[Optional[Dict[str, Any]], str]:
    try:
        payload = _read_packaged_resource("policy", DEFAULT_TEAM_SYNC_POLICY_FALLBACK_NAME)
    except PlatformError as exc:
        if getattr(exc, "code", "") == "E_MANIFEST_NOT_FOUND":
            return None, "missing"
        raise
    return payload, f"pkg:platform_cli/resources/policy/{DEFAULT_TEAM_SYNC_POLICY_FALLBACK_NAME}"


def toolset_source_kind(source: str) -> str:
    label = (source or "").strip()
    if label.startswith("managed:"):
        return "managed"
    if label.startswith("user:"):
        return "user"
    if label.startswith("env:"):
        return "env"
    if label.startswith("pkg:"):
        return "packaged"
    if label.startswith("file:") or label.startswith("cwd:"):
        return "dev"
    if label == "missing":
        return "missing"
    return "unknown"


def _read_json_file(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise PlatformError(f"Manifest file not found: {path}", code="E_MANIFEST_NOT_FOUND", reason=str(path))
    except json.JSONDecodeError as exc:
        raise PlatformError(f"Invalid JSON in {path}: {exc}", code="E_MANIFEST_INVALID", reason="JSON_DECODE_ERROR")


def _read_packaged_resource(subdir: str, filename: str) -> Dict[str, Any]:
    """
    Reads JSON shipped inside:
      platform_cli/resources/<subdir>/<filename>

    Works for pipx installs, wheels, and PyInstaller (when package-data is included).
    """
    try:
        data = (pkg_resources.files("platform_cli.resources") / subdir / filename).read_text(encoding="utf-8-sig")
        return json.loads(data)
    except FileNotFoundError:
        raise PlatformError(
            f"Packaged resource missing: platform_cli/resources/{subdir}/{filename}",
            code="E_MANIFEST_NOT_FOUND",
            reason=f"{subdir}/{filename}",
        )
    except ModuleNotFoundError:
        raise PlatformError(
            "Packaged resources package missing: platform_cli.resources",
            code="E_MANIFEST_NOT_FOUND",
            reason="platform_cli.resources",
        )
    except json.JSONDecodeError as exc:
        raise PlatformError(
            f"Invalid packaged JSON for {filename}: {exc}",
            code="E_MANIFEST_INVALID",
            reason="JSON_DECODE_ERROR",
        )


def _read_packaged_json(filename: str) -> Dict[str, Any]:
    return _read_packaged_resource("manifests", filename)


def _read_best_effort(
    filename: str,
    user_override: Optional[Path],
    *,
    subdir: str,
    env_key: Optional[str] = None,
    managed_override: Optional[Path] = None,
    cwd_subdir: Optional[str] = None,
) -> Tuple[Dict[str, Any], str]:
    """
    Generic loader for bundled JSON resources.

    Load order:
      1) user_override (if exists)
      2) env override (if key present and file exists)
      3) managed_override (if exists)
      4) packaged resource (platform_cli/resources/<subdir>/<filename>)
      5) repo-adjacent fallback (editable/dev)
      6) cwd fallback (optional)

    Returns (json, source_label)
    """
    if user_override and user_override.exists():
        return _read_json_file(user_override), f"user:{user_override}"

    if env_key:
        env_path = os.environ.get(env_key)
        if env_path:
            p = Path(env_path).expanduser()
            if p.exists():
                return _read_json_file(p), f"env:{env_key}:{p}"

    if managed_override and managed_override.exists():
        return _read_json_file(managed_override), f"managed:{managed_override}"

    try:
        return _read_packaged_resource(subdir, filename), f"pkg:platform_cli/resources/{subdir}/{filename}"
    except PlatformError:
        pass

    try:
        repo_adjacent = Path(__file__).resolve().parents[1] / "resources" / subdir / filename
        if repo_adjacent.exists():
            return _read_json_file(repo_adjacent), f"file:{repo_adjacent}"
    except Exception:
        pass

    if cwd_subdir:
        repo_guess = Path.cwd() / "resources" / cwd_subdir / filename
    else:
        repo_guess = Path.cwd() / "resources" / filename

    if repo_guess.exists():
        return _read_json_file(repo_guess), f"cwd:{repo_guess}"

    raise PlatformError(
        f"Could not locate resource {filename}.",
        code="E_MANIFEST_NOT_FOUND",
        reason=filename,
    )


def _load_json_candidates(
    candidates: List[Tuple[str, Path]],
    *,
    env_keys: Optional[List[str]] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    for label, path in candidates:
        if path.exists():
            return _read_json_file(path), f"{label}:{path}"

    for env_key in env_keys or []:
        env_path = os.environ.get(env_key, "")
        if not env_path:
            continue
        candidate = Path(env_path).expanduser()
        if candidate.exists():
            return _read_json_file(candidate), f"env:{env_key}:{candidate}"

    return None, "missing"



_JENKINSFILE_NAME = "Jenkinsfile"
_DEP_CALL_PATTERN = re.compile(r"downloadDependencies\s*\((?P<body>.*?)\)", re.DOTALL)
_DEP_REPOSITORY_PATTERN = re.compile(r"repository\s*:\s*['\"](?P<repo>[^'\"]+)['\"]")
_DEP_REF_PATTERN = re.compile(r"(?:branch|tag|ref)\s*:\s*['\"](?P<ref>[^'\"]+)['\"]")
_REGION_PATTERN = re.compile(r"\b(?:REGION|AWS_REGION)\b\s*=\s*['\"](?P<region>[^'\"]+)['\"]")



def _find_jenkinsfile_near_cwd(start: Optional[Path] = None) -> Optional[Path]:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        path = candidate / _JENKINSFILE_NAME
        if path.exists() and path.is_file():
            return path
    return None



def _dependency_repo_to_git_url(repository: str) -> Tuple[str, str]:
    repo = repository.strip().strip("/")
    if repo.startswith("https://") or repo.startswith("http://"):
        url = repo if repo.endswith(".git") else f"{repo}.git"
        name = repo.rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        return name, url

    name = repo.split("/")[-1]
    return name, f"https://github.com/{repo}.git"



def _extract_dependencies_from_jenkinsfile(text: str) -> List[Dict[str, Any]]:
    deps: List[Dict[str, Any]] = []
    seen = set()

    for call in _DEP_CALL_PATTERN.finditer(text):
        body = call.group("body")
        repo_match = _DEP_REPOSITORY_PATTERN.search(body)
        if not repo_match:
            continue

        repo_raw = repo_match.group("repo").strip()
        name, git_url = _dependency_repo_to_git_url(repo_raw)
        ref_match = _DEP_REF_PATTERN.search(body)
        ref = ref_match.group("ref").strip() if ref_match else ""

        if name in seen:
            continue

        dep: Dict[str, Any] = {
            "name": name,
            "git_url": git_url,
        }
        if ref:
            dep["ref"] = ref

        deps.append(dep)
        seen.add(name)

    return deps



def _extract_region_from_jenkinsfile(text: str) -> Optional[str]:
    match = _REGION_PATTERN.search(text)
    if not match:
        return None

    region = match.group("region").strip()
    return region or None



def _merge_jenkinsfile_terraform_hints(policy: Dict[str, Any], source: str) -> Tuple[Dict[str, Any], str]:
    jenkinsfile = _find_jenkinsfile_near_cwd()
    if not jenkinsfile:
        return policy, source

    try:
        text = jenkinsfile.read_text(encoding="utf-8")
    except Exception:
        return policy, source

    deps = _extract_dependencies_from_jenkinsfile(text)
    region = _extract_region_from_jenkinsfile(text)
    if not deps and not region:
        return policy, source

    merged = dict(policy)
    if deps:
        merged["dependencies"] = deps
    if region:
        merged["default_region"] = region

    return merged, f"{source}+jenkins:{jenkinsfile}"

def load_manifests() -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, str]]:
    """
    Returns: (toolset_json, registry_json, sources)
    sources = {"toolset": "...", "registry": "..."}
    Source labels are prefixed with user:, managed:, env:, pkg:, file:, or cwd:
    so later phases can distinguish managed sync from user overrides and packaged fallbacks.
    """
    home = Path.home()

    toolset_user_override_new = home / ".ghdp" / "manifests" / DEFAULT_TOOLSET_NAME
    toolset_user_override_old = home / ".ghdp" / DEFAULT_TOOLSET_NAME
    toolset_user_override = toolset_user_override_new if toolset_user_override_new.exists() else toolset_user_override_old
    toolset_managed_override = preferred_managed_toolset_path()

    registry_override_new = home / ".ghdp" / "manifests" / DEFAULT_REGISTRY_NAME
    registry_override_old = home / ".ghdp" / DEFAULT_REGISTRY_NAME
    registry_override = registry_override_new if registry_override_new.exists() else registry_override_old

    toolset, toolset_src = _read_best_effort(
        DEFAULT_TOOLSET_NAME,
        toolset_user_override,
        subdir="manifests",
        env_key=f"GHDP_{DEFAULT_TOOLSET_NAME.replace('-', '_').replace('.', '_').upper()}_PATH",
        managed_override=toolset_managed_override,
    )
    registry, registry_src = _read_best_effort(
        DEFAULT_REGISTRY_NAME,
        registry_override,
        subdir="manifests",
        env_key=f"GHDP_{DEFAULT_REGISTRY_NAME.replace('-', '_').replace('.', '_').upper()}_PATH",
    )

    return toolset, registry, {"toolset": toolset_src, "registry": registry_src}


def load_config_defaults() -> Tuple[Dict[str, Any], str]:
    """
    Returns: (config_defaults_json, source)

    Load order:
      - ~/.ghdp/manifests/config-defaults.json (or legacy ~/.ghdp/config-defaults.json)
      - env override GHDP_CONFIG_DEFAULTS_JSON_PATH
      - packaged resource under platform_cli/resources/manifests/
      - repo-adjacent fallback for editable/dev installs
    """
    home = Path.home()
    cfg_override_new = home / ".ghdp" / "manifests" / DEFAULT_CONFIG_DEFAULTS_NAME
    cfg_override_old = home / ".ghdp" / DEFAULT_CONFIG_DEFAULTS_NAME
    cfg_override = cfg_override_new if cfg_override_new.exists() else cfg_override_old

    cfg, src = _read_best_effort(
        DEFAULT_CONFIG_DEFAULTS_NAME,
        cfg_override,
        subdir="manifests",
        env_key=f"GHDP_{DEFAULT_CONFIG_DEFAULTS_NAME.replace('-', '_').replace('.', '_').upper()}_PATH",
    )
    return cfg, src


def load_terraform_local_policy() -> Tuple[Dict[str, Any], str]:
    """
    Returns: (terraform_local_policy_json, source)

    Load order (manifest-style):
      1) ~/.ghdp/policy/terraform_local.json (preferred)
      2) GHDP_TERRAFORM_POLICY_PATH
      3) packaged resource platform_cli/resources/policy/terraform_local.json
      4) repo-adjacent fallback for editable installs

    Additional best-effort enhancement:
      - If Jenkinsfile is found near cwd, extract only terraform dependencies and region,
        and merge those fields into the loaded policy.
    """
    home = Path.home()

    policy_override_new = home / ".ghdp" / "policy" / DEFAULT_TERRAFORM_LOCAL_POLICY_NAME
    policy_override_old = home / ".ghdp" / DEFAULT_TERRAFORM_LOCAL_POLICY_NAME
    policy_override = policy_override_new if policy_override_new.exists() else policy_override_old

    try:
        policy, source = _read_best_effort(
            DEFAULT_TERRAFORM_LOCAL_POLICY_NAME,
            policy_override,
            subdir="policy",
            env_key="GHDP_TERRAFORM_POLICY_PATH",
            cwd_subdir="policy",
        )
    except PlatformError as e:
        # Fail fast instead of silently using a hardcoded fallback that may differ from the packaged policy
        raise PlatformError(
            f"Failed to load terraform local policy: {e}. "
            "Ensure terraform_local.json is available in resources/policy/ or ~/.ghdp/policy/.",
            code="E_TF_POLICY_NOT_FOUND",
            reason="terraform_local_policy",
        )

    return _merge_jenkinsfile_terraform_hints(policy, source)


def load_access_policy() -> Tuple[Dict[str, Any], str]:
    """
    Returns: (access_policy_json, source)

    Load order:
      1) ~/.ghdp/policy/access_policy.json (preferred)
      2) ~/.ghdp/policy/access_phase0.json (legacy fallback)
      3) ~/.ghdp/policies/access_policy.json (sync-managed path)
      4) ~/.ghdp/policies/access_phase0.json (legacy sync-managed path)
      5) GHDP_ACCESS_POLICY_PATH
      6) GHDP_ACCESS_PHASE0_POLICY_PATH (legacy env fallback)
      7) packaged resource platform_cli/resources/policy/access_policy.json
      8) repo-adjacent fallback for editable installs
    """
    home = Path.home()

    candidates = [
        home / ".ghdp" / "policy" / DEFAULT_ACCESS_POLICY_NAME,
        home / ".ghdp" / "policy" / LEGACY_ACCESS_POLICY_NAME,
        home / ".ghdp" / "policies" / DEFAULT_ACCESS_POLICY_NAME,
        home / ".ghdp" / "policies" / LEGACY_ACCESS_POLICY_NAME,
        home / ".ghdp" / DEFAULT_ACCESS_POLICY_NAME,
        home / ".ghdp" / LEGACY_ACCESS_POLICY_NAME,
    ]

    for candidate in candidates:
        if candidate.exists():
            return _read_json_file(candidate), f"user:{candidate}"

    for env_key in ("GHDP_ACCESS_POLICY_PATH", "GHDP_ACCESS_PHASE0_POLICY_PATH"):
        env_path = os.environ.get(env_key, "")
        if not env_path:
            continue
        candidate = Path(env_path).expanduser()
        if candidate.exists():
            return _read_json_file(candidate), f"env:{env_key}:{candidate}"

    policy, source = _read_best_effort(
        DEFAULT_ACCESS_POLICY_NAME,
        None,
        subdir="policy",
        cwd_subdir="policy",
    )
    return policy, source


def load_access_phase0_policy() -> Tuple[Dict[str, Any], str]:
    """
    Backward-compatible alias for older imports while access policy naming migrates.
    """
    return load_access_policy()


def load_optional_team_policy() -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Returns: (team_policy_json_or_none, source)

    Load order:
      1) ~/.ghdp/policy/team-policy.managed.json (preferred)
      2) ~/.ghdp/policies/team-policy.managed.json (sync-managed path)
      3) ~/.ghdp/team-policy.managed.json (legacy fallback)
      4) GHDP_TEAM_POLICY_PATH
      5) packaged resource platform_cli/resources/policy/team-policy.managed.json
      6) repo-adjacent fallback for editable installs
      7) cwd/policy/team-policy.managed.json
      8) cwd/team-policy.managed.json

    This loader is optional because GHDP must keep working even when the
    synced team policy artifact has not been installed yet.
    """
    home = Path.home()

    candidates = [
        ("user", home / ".ghdp" / "policy" / DEFAULT_TEAM_POLICY_NAME),
        ("user", home / ".ghdp" / "policies" / DEFAULT_TEAM_POLICY_NAME),
        ("user", home / ".ghdp" / DEFAULT_TEAM_POLICY_NAME),
    ]

    env_path = os.environ.get("GHDP_TEAM_POLICY_PATH", "")
    if env_path:
        candidates.append(("env:GHDP_TEAM_POLICY_PATH", Path(env_path).expanduser()))

    for label, path in candidates:
        if path.exists():
            return _read_json_file(path), f"{label}:{path}"

    try:
        policy, source = _read_best_effort(
            DEFAULT_TEAM_POLICY_NAME,
            None,
            subdir="policy",
            cwd_subdir="policy",
        )
        if isinstance(policy, dict):
            return policy, source
    except PlatformError:
        pass
    return None, "missing"


def load_release_policy() -> Tuple[Dict[str, Any], str]:
    """
    Returns: (release_policy_json, source)

    Load order:
      1) ~/.ghdp/policy/release_policy.json (explicit user override)
      2) ~/.ghdp/policies/release-policy.managed.json (sync-managed path)
      3) ~/.ghdp/release_policy.json (legacy local fallback)
      4) GHDP_RELEASE_POLICY_PATH
      5) packaged resource platform_cli/resources/policy/release_policy.json
      6) repo-adjacent fallback for editable installs
    """
    home = Path.home()

    candidates = [
        home / ".ghdp" / "policy" / DEFAULT_RELEASE_POLICY_NAME,
        home / ".ghdp" / "policies" / DEFAULT_MANAGED_RELEASE_POLICY_NAME,
        home / ".ghdp" / DEFAULT_RELEASE_POLICY_NAME,
    ]

    for candidate in candidates:
        if candidate.exists():
            return _read_json_file(candidate), f"user:{candidate}"

    env_path = os.environ.get("GHDP_RELEASE_POLICY_PATH", "")
    if env_path:
        candidate = Path(env_path).expanduser()
        if candidate.exists():
            return _read_json_file(candidate), f"env:GHDP_RELEASE_POLICY_PATH:{candidate}"

    policy, source = _read_best_effort(
        DEFAULT_RELEASE_POLICY_NAME,
        None,
        subdir="policy",
        cwd_subdir="policy",
    )
    return policy, source


def load_command_restrictions_policy() -> Tuple[Dict[str, Any], str]:
    """
    Returns: (command_restrictions_policy_json, source)

    Load order:
      1) ~/.ghdp/policy/command_restrictions.json (explicit user override)
      2) ~/.ghdp/policy/command-restrictions.managed.json (managed compatibility path)
      3) ~/.ghdp/policies/command-restrictions.managed.json (sync-managed path)
      4) ~/.ghdp/command_restrictions.json (legacy local fallback)
      5) GHDP_COMMAND_RESTRICTIONS_POLICY_PATH
      6) packaged resource platform_cli/resources/policy/command_restrictions.json
      7) repo-adjacent fallback for editable installs
    """
    home = Path.home()

    candidates = [
        ("user", home / ".ghdp" / "policy" / DEFAULT_COMMAND_RESTRICTIONS_POLICY_NAME),
        ("managed", home / ".ghdp" / "policy" / DEFAULT_MANAGED_COMMAND_RESTRICTIONS_POLICY_NAME),
        ("managed", home / ".ghdp" / "policies" / DEFAULT_MANAGED_COMMAND_RESTRICTIONS_POLICY_NAME),
        ("user", home / ".ghdp" / DEFAULT_COMMAND_RESTRICTIONS_POLICY_NAME),
    ]

    policy, source = _load_json_candidates(
        candidates,
        env_keys=["GHDP_COMMAND_RESTRICTIONS_POLICY_PATH"],
    )
    if policy is not None:
        return policy, source

    policy, source = _read_best_effort(
        DEFAULT_COMMAND_RESTRICTIONS_POLICY_NAME,
        None,
        subdir="policy",
        cwd_subdir="policy",
    )
    return policy, source


def load_scheduler_install_policy() -> Tuple[Dict[str, Any], str]:
    """
    Returns: (scheduler_install_policy_json, source)

    Load order:
      1) ~/.ghdp/policy/scheduler_install.json (explicit user override)
      2) ~/.ghdp/policy/scheduler-install.managed.json (managed compatibility path)
      3) ~/.ghdp/policies/scheduler-install.managed.json (sync-managed path)
      4) ~/.ghdp/scheduler_install.json (legacy local fallback)
      5) GHDP_SCHEDULER_INSTALL_POLICY_PATH
      6) packaged resource platform_cli/resources/policy/scheduler_install.json
      7) repo-adjacent fallback for editable installs
    """
    home = Path.home()

    candidates = [
        ("user", home / ".ghdp" / "policy" / DEFAULT_SCHEDULER_INSTALL_POLICY_NAME),
        ("managed", home / ".ghdp" / "policy" / DEFAULT_MANAGED_SCHEDULER_INSTALL_POLICY_NAME),
        ("managed", home / ".ghdp" / "policies" / DEFAULT_MANAGED_SCHEDULER_INSTALL_POLICY_NAME),
        ("user", home / ".ghdp" / DEFAULT_SCHEDULER_INSTALL_POLICY_NAME),
    ]

    policy, source = _load_json_candidates(
        candidates,
        env_keys=["GHDP_SCHEDULER_INSTALL_POLICY_PATH"],
    )
    if policy is not None:
        return policy, source

    policy, source = _read_best_effort(
        DEFAULT_SCHEDULER_INSTALL_POLICY_NAME,
        None,
        subdir="policy",
        cwd_subdir="policy",
    )
    return policy, source


def _validate_claude_athena_workgroup_payload(payload: Dict[str, Any]) -> None:
    from platform_cli.manifests.validate import validate_claude_athena_workgroup_map

    validate_claude_athena_workgroup_map(payload)


def _load_validated_claude_athena_workgroup_file(path: Path, *, source: str, invalid_prefix: str) -> Tuple[List[Dict[str, Any]], str]:
    try:
        payload = _read_json_file(path)
        _validate_claude_athena_workgroup_payload(payload)
    except PlatformError as exc:
        raise PlatformError(
            f"{invalid_prefix}: {exc}",
            code="E_MANIFEST_INVALID",
            reason=exc.reason or source,
        ) from exc
    return list(payload.get("mappings", [])), source


def _load_validated_packaged_claude_athena_workgroup(filename: str) -> List[Dict[str, Any]]:
    payload = _read_packaged_resource("claude", filename)
    _validate_claude_athena_workgroup_payload(payload)
    return list(payload.get("mappings", []))


def load_claude_athena_workgroup_map() -> Tuple[List[Dict[str, Any]], str, bool]:
    """
    Returns: (mappings, source_label, fallback_active)

    Load order:
      1) GHDP_CLAUDE_ATHENA_WORKGROUP_MAP_PATH
      2) ~/.ghdp/policy/claude-athena-workgroup-map.json
      3) ~/.ghdp/policies/claude-athena-workgroup-map.managed.json
      4) packaged primary resource
      5) packaged backup resource only if packaged primary is unusable
    """
    env_path_raw = str(os.environ.get(CLAUDE_ATHENA_WORKGROUP_MAP_ENV_KEY, "") or "").strip()
    if env_path_raw:
        env_path = Path(env_path_raw).expanduser()
        if not env_path.exists():
            raise PlatformError(
                f"Invalid env override Claude Athena workgroup mapping: file not found at {env_path}",
                code="E_MANIFEST_INVALID",
                reason=f"{CLAUDE_ATHENA_WORKGROUP_MAP_ENV_KEY}:{env_path}",
            )
        return (*_load_validated_claude_athena_workgroup_file(
            env_path,
            source=f"env:{CLAUDE_ATHENA_WORKGROUP_MAP_ENV_KEY}:{env_path}",
            invalid_prefix="Invalid env override Claude Athena workgroup mapping",
        ), False)

    user_path = preferred_user_claude_athena_workgroup_map_path()
    if user_path.exists():
        return (*_load_validated_claude_athena_workgroup_file(
            user_path,
            source=f"user:{user_path}",
            invalid_prefix="Invalid user-managed Claude Athena workgroup mapping",
        ), False)

    managed_path = preferred_managed_claude_athena_workgroup_map_path()
    if managed_path.exists():
        return (*_load_validated_claude_athena_workgroup_file(
            managed_path,
            source=f"managed:{managed_path}",
            invalid_prefix="Invalid managed Claude Athena workgroup mapping",
        ), False)

    primary_source = f"pkg:platform_cli/resources/claude/{DEFAULT_CLAUDE_ATHENA_WORKGROUP_MAP_NAME}"
    backup_source = f"pkg:platform_cli/resources/claude/{DEFAULT_CLAUDE_ATHENA_WORKGROUP_MAP_BACKUP_NAME}"
    try:
        return _load_validated_packaged_claude_athena_workgroup(DEFAULT_CLAUDE_ATHENA_WORKGROUP_MAP_NAME), primary_source, False
    except PlatformError as primary_exc:
        try:
            return (
                _load_validated_packaged_claude_athena_workgroup(DEFAULT_CLAUDE_ATHENA_WORKGROUP_MAP_BACKUP_NAME),
                backup_source,
                True,
            )
        except PlatformError as backup_exc:
            raise PlatformError(
                "Broken packaged backup Claude Athena workgroup mapping. "
                f"Primary failed with: {primary_exc}. Backup failed with: {backup_exc}",
                code="E_MANIFEST_INVALID",
                reason=backup_exc.reason or backup_source,
            ) from backup_exc


def current_platform_key() -> str:
    """
    Returns one of: darwin | linux | windows
    (matches keys used in tool-registry.json)
    """
    if sys.platform.startswith("darwin"):
        return "darwin"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform.startswith("win"):
        return "windows"
    return "unknown"


def load_account_environments() -> Dict[str, Any]:
    """
    Load account-environments manifest from bundled resources.

    Returns:
        Dict with accounts mapping and version_modes.
    """
    return _read_packaged_resource("manifests", DEFAULT_ACCOUNT_ENVIRONMENTS_NAME)


def get_all_valid_environments(account_envs: Optional[Dict[str, Any]] = None) -> List[str]:
    """
    Get flat list of all valid environment names across all accounts.

    Args:
        account_envs: Pre-loaded manifest (loads from resources if None)

    Returns:
        Sorted list of all valid environment names.
    """
    data = account_envs or load_account_environments()
    envs: set = set()
    for account_info in data.get("accounts", {}).values():
        envs.update(account_info.get("environments", []))
    return sorted(envs)


def get_account_for_env(env: str, account_envs: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """
    Get account alias for a given environment.

    Args:
        env: Environment name (e.g., "dev", "prod")
        account_envs: Pre-loaded manifest (loads from resources if None)

    Returns:
        Account alias (e.g., "dpnp", "dpp") or None if not found.
    """
    data = account_envs or load_account_environments()
    for alias, info in data.get("accounts", {}).items():
        if env in info.get("environments", []):
            return alias
    return None


def get_version_mode_for_env(env: str, account_envs: Optional[Dict[str, Any]] = None) -> str:
    """
    Get version mode (snapshot/release) for a given environment.

    Args:
        env: Environment name
        account_envs: Pre-loaded manifest (loads from resources if None)

    Returns:
        "snapshot" or "release" (defaults to "snapshot" if not found)
    """
    data = account_envs or load_account_environments()
    for info in data.get("accounts", {}).values():
        if env in info.get("environments", []):
            return info.get("version_mode", "snapshot")
    return "snapshot"


def get_local_allowed_envs(account_envs: Optional[Dict[str, Any]] = None) -> List[str]:
    """
    Get list of environments allowed for local CLI operations.

    This is a security guardrail — only these envs can be used with
    ghdp build/publish/deploy from a local machine. Controlled via
    'local_allowed_envs' in account-environments.json.

    Args:
        account_envs: Pre-loaded manifest (loads from resources if None)

    Returns:
        List of allowed environment names (e.g., ["dev"])
    """
    data = account_envs or load_account_environments()
    return list(data.get("local_allowed_envs", []))


def get_aws_region(account_envs: Optional[Dict[str, Any]] = None) -> str:
    """Get AWS region from account-environments manifest."""
    data = account_envs or load_account_environments()
    return str(data.get("aws_region", "us-west-2"))


def get_account_config(account_alias: str, account_envs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Get full config for an account alias (dpnp/dpp)."""
    data = account_envs or load_account_environments()
    return dict(data.get("accounts", {}).get(account_alias, {}))


def get_account_alias_by_id(account_id: str, account_envs: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Map AWS account ID to alias (e.g., '617336469044' -> 'dpnp')."""
    data = account_envs or load_account_environments()
    for alias, info in data.get("accounts", {}).items():
        if info.get("account_id") == account_id:
            return alias
    return None


def get_state_bucket(account_alias: str, account_envs: Optional[Dict[str, Any]] = None) -> str:
    """Get S3 state bucket for an account alias."""
    cfg = get_account_config(account_alias, account_envs)
    bucket = cfg.get("state_bucket", "")
    if not bucket:
        raise PlatformError(
            f"No state_bucket configured for account '{account_alias}'",
            code="E_STATE_BUCKET_NOT_FOUND",
            reason=account_alias,
        )
    return bucket


def get_codeartifact_config(account_envs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Get CodeArtifact config from account-environments manifest."""
    data = account_envs or load_account_environments()
    return dict(data.get("codeartifact", {}))


def get_codeartifact_repo_name(app_type: str, version_mode: str, account_envs: Optional[Dict[str, Any]] = None) -> str:
    """Get CodeArtifact repository name for app type and version mode."""
    ca = get_codeartifact_config(account_envs)
    repos = ca.get("repositories", {}).get(app_type, {})
    repo = repos.get(version_mode, "")
    if not repo:
        raise PlatformError(
            f"No CodeArtifact repository configured for type={app_type}, mode={version_mode}",
            code="E_CODEARTIFACT_REPO_NOT_CONFIGURED",
            reason=f"{app_type}/{version_mode}",
        )
    return repo


def get_infra_templates_repo(account_envs: Optional[Dict[str, Any]] = None) -> str:
    """Get infra-templates GitHub org/repo from account-environments manifest."""
    data = account_envs or load_account_environments()
    repo = data.get("infra_templates_repo", "")
    if not repo:
        raise PlatformError(
            "infra_templates_repo not configured in account-environments manifest",
            code="E_INFRA_TEMPLATES_REPO_NOT_CONFIGURED",
            reason="account-environments.json",
        )
    return repo
