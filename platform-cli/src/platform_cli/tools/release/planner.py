from __future__ import annotations

import json
import platform
import re
from pathlib import Path

from platform_cli.core.errors import PlatformError
from platform_cli.core.update import get_latest_stable_anchor
from platform_cli.exec.runner import run_cmd
from platform_cli.tools.git_repo import get_current_branch

from .models import BuildTarget, ReleasePlan
from .policy import ManualBuildPolicy, load_manual_build_policy


def plan_binaries_release(
    *,
    repo_root: Path,
    source_ref: str | None = None,
    workdir: str | None = None,
    install_flavor: str = "standard",
    release_visibility: str = "auto",
    release_channel: str = "auto",
    python_version: str = "",
    version_override: str = "",
) -> ReleasePlan:
    policy = load_manual_build_policy()
    resolved_repo_root = _resolve_repo_root(repo_root.expanduser().resolve())
    resolved_source_ref = (source_ref or get_current_branch(resolved_repo_root)).strip()
    resolved_install_flavor = _resolve_install_flavor(install_flavor)
    if not resolved_source_ref:
        raise PlatformError(
            "Could not determine source_ref for release planning.",
            code="E_RELEASE_SOURCE_REF_MISSING",
            reason="source_ref",
        )

    resolved_workdir = _resolve_workdir(resolved_repo_root, policy, workdir)
    repo_name_with_owner = _resolve_repo_name_with_owner(resolved_repo_root)
    latest_stable_tag, next_stable_tag = _resolve_stable_tags(repo_name_with_owner)
    stable_branches = set(policy.stable_branches)
    is_stable_branch = resolved_source_ref in stable_branches
    resolved_python_version = _validate_python_version(python_version)
    resolved_version_override = _normalize_version_override(
        version_override=version_override,
        source_ref=resolved_source_ref,
        stable_branches=stable_branches,
    )
    tag, ticket, feature_slug = _resolve_tag(
        source_ref=resolved_source_ref,
        next_stable_tag=next_stable_tag,
        is_stable_branch=is_stable_branch,
        branch_kind_tokens=set(policy.branch_kind_tokens),
        version_override=resolved_version_override,
    )
    draft = _resolve_draft(release_visibility)
    resolved_install_flavor = _resolve_install_flavor(install_flavor)
    prerelease = _resolve_prerelease(
        release_channel=release_channel,
        is_stable_branch=is_stable_branch,
    )
    build_target = _resolve_current_build_target(policy.asset_targets)

    summary_file = resolved_repo_root / policy.summary_file
    template_file = resolved_repo_root / policy.template_file
    build_meta_path = resolved_workdir / policy.build_meta_path
    runtime_defaults_path = resolved_workdir / policy.runtime_defaults_path

    return ReleasePlan(
        repo_root=resolved_repo_root,
        repo_name_with_owner=repo_name_with_owner,
        source_ref=resolved_source_ref,
        install_flavor=resolved_install_flavor,
        workdir=resolved_workdir,
        python_version=resolved_python_version,
        latest_stable_tag=latest_stable_tag,
        next_stable_tag=next_stable_tag,
        tag=tag,
        ticket=ticket,
        feature_slug=feature_slug,
        is_stable_branch=is_stable_branch,
        draft=draft,
        prerelease=prerelease,
        summary_file=summary_file,
        template_file=template_file,
        build_meta_path=build_meta_path,
        runtime_defaults_path=runtime_defaults_path,
        build_version=tag[1:] if tag.startswith("v") else tag,
        build_channel="beta" if prerelease else "stable",
        build_target=build_target,
        version_override=resolved_version_override,
    )


def _resolve_install_flavor(install_flavor: str) -> str:
    normalized = (install_flavor or "standard").strip().lower()
    if normalized in {"", "legacy-standard"}:
        normalized = "standard"
    if normalized not in {"standard", "managed"}:
        raise PlatformError(
            f"Unsupported install flavor '{install_flavor}'.",
            code="E_RELEASE_INSTALL_FLAVOR_INVALID",
            reason="install_flavor",
        )
    return normalized


def _resolve_workdir(repo_root: Path, policy: ManualBuildPolicy, workdir: str | None) -> Path:
    explicit = (workdir or "").strip()
    if explicit:
        resolved = _resolve_explicit_workdir(repo_root, Path(explicit), policy)
        _ensure_release_workdir(resolved, repo_root=repo_root)
        return resolved

    if _is_release_workdir(repo_root):
        return repo_root

    default_candidate = (repo_root / policy.workdir_default).expanduser().resolve()
    if _is_release_workdir(default_candidate):
        return default_candidate

    candidates = _find_release_workdir_candidates(repo_root)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise PlatformError(
            "Could not determine the release workdir automatically. Run the command from the repo root or the package directory, or pass --workdir <path>. Expected either the current folder to contain both 'ghdp.spec' and 'pyproject.toml', or a single direct child of the repo root to contain them.",
            code="E_RELEASE_WORKDIR_MISSING",
            reason="workdir",
        )

    candidate_list = ", ".join(str(path.relative_to(repo_root)) for path in candidates)
    raise PlatformError(
        f"Could not determine the release workdir automatically because multiple candidates matched: {candidate_list}. Pass --workdir to disambiguate.",
        code="E_RELEASE_WORKDIR_AMBIGUOUS",
        reason="workdir",
    )


def validate_release_notes_freshness(plan: ReleasePlan) -> None:
    if not plan.summary_file.exists():
        raise PlatformError(
            f"GHDP could not prepare release notes because the release summary file is missing: {plan.summary_file}. "
            "Add the summary file, then rerun the release command.",
            code="E_RELEASE_NOTES_MISSING",
            reason="release_notes",
        )

    if plan.is_stable_branch:
        return

    run_cmd(["git", "fetch", "--no-tags", "origin"], check=True, cwd=plan.repo_root)
    base_ref = _resolve_release_notes_base_ref(plan.repo_root)
    base = run_cmd(
        ["git", "merge-base", "HEAD", base_ref],
        check=True,
        cwd=plan.repo_root,
    ).stdout.strip()
    if not base:
        raise PlatformError(
            "GHDP could not verify release-notes freshness because it could not resolve a merge-base against "
            "origin/develop. Fetch the latest develop branch and retry.",
            code="E_RELEASE_NOTES_BASE_MISSING",
            reason="release_notes",
        )

    rel_summary = _repo_relative(plan.summary_file, plan.repo_root)
    changed_files = run_cmd(
        ["git", "diff", "--name-only", f"{base}..HEAD"],
        check=True,
        cwd=plan.repo_root,
    ).stdout.splitlines()
    if rel_summary not in {line.strip() for line in changed_files}:
        raise PlatformError(
            f"GHDP found that release notes are stale for this feature branch because '{rel_summary}' was not updated "
            f"between origin/develop and HEAD. Update that file with this branch's release summary, then retry.",
            code="E_RELEASE_NOTES_STALE",
            reason="release_notes",
        )

    recent_commits = run_cmd(
        ["git", "rev-list", "--max-count=4", "HEAD"],
        check=True,
        cwd=plan.repo_root,
    ).stdout.splitlines()
    touched_recently = False
    for commit in recent_commits:
        names = run_cmd(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", commit],
            check=True,
            cwd=plan.repo_root,
        ).stdout.splitlines()
        if rel_summary in {line.strip() for line in names}:
            touched_recently = True
            break
    if not touched_recently:
        raise PlatformError(
            f"GHDP found that release notes are stale because '{rel_summary}' was not touched in the latest 4 commits. "
            "Add or refresh the release summary in a recent commit, then retry.",
            code="E_RELEASE_NOTES_STALE",
            reason="release_notes",
        )


def _resolve_release_notes_base_ref(repo_root: Path) -> str:
    for candidate in ("origin/develop", "origin/main"):
        probe = run_cmd(["git", "rev-parse", "--verify", candidate], check=False, cwd=repo_root)
        if probe.returncode == 0:
            return candidate
    raise PlatformError(
        "Could not resolve release-notes base branch ref (expected origin/develop or origin/main).",
        code="E_RELEASE_NOTES_BASE_MISSING",
        reason="release_notes",
    )


def _resolve_repo_name_with_owner(repo_root: Path) -> str:
    res = run_cmd(
        ["gh", "repo", "view", "--json", "nameWithOwner"],
        check=True,
        cwd=repo_root,
    )
    try:
        payload = json.loads(res.stdout or "{}")
    except Exception as e:
        raise PlatformError(
            f"Failed to parse GitHub repo metadata: {e}",
            code="E_RELEASE_REPO_INVALID",
            reason="repo",
        )
    name_with_owner = str(payload.get("nameWithOwner") or "").strip()
    if not name_with_owner:
        raise PlatformError(
            "Could not resolve GitHub repo nameWithOwner from local repo context.",
            code="E_RELEASE_REPO_INVALID",
            reason="repo",
        )
    return name_with_owner


def _resolve_stable_tags(repo: str) -> tuple[str, str]:
    semver = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
    latest_tag = get_latest_stable_anchor(repo)
    match = semver.match(latest_tag)
    if not match:
        raise PlatformError(
            f"Latest stable release anchor '{latest_tag}' is not a stable semver tag.",
            code="E_RELEASE_STABLE_TAGS_MISSING",
            reason="stable_releases",
        )
    latest_tuple = tuple(int(part) for part in match.groups())
    next_stable_tag = f"v{latest_tuple[0]}.{latest_tuple[1]}.{latest_tuple[2] + 1}"
    return latest_tag, next_stable_tag


def _validate_python_version(python_version: str) -> str:
    normalized = (python_version or "").strip()
    if not normalized:
        return normalized

    match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?$", normalized)
    if not match:
        raise PlatformError(
            f"Unsupported Python version '{python_version}'. Use a version like 3.10 or 3.11.",
            code="E_RELEASE_PYTHON_VERSION_INVALID",
            reason="python_version",
        )

    major = int(match.group(1))
    minor = int(match.group(2))
    if (major, minor) < (3, 10):
        raise PlatformError(
            f"Python {normalized} is not supported for GHDP manual binary builds. Use Python 3.10 or newer.",
            code="E_RELEASE_PYTHON_VERSION_UNSUPPORTED",
            reason="python_version",
        )
    return normalized


def _normalize_version_override(
    *,
    version_override: str,
    source_ref: str,
    stable_branches: set[str],
) -> str:
    normalized = (version_override or "").strip()
    if not normalized:
        return ""

    if source_ref not in stable_branches:
        stable_text = ", ".join(sorted(stable_branches))
        raise PlatformError(
            f"Version override is supported only on stable branches ({stable_text}). Branch '{source_ref}' must keep the feature-branch version slug flow.",
            code="E_RELEASE_VERSION_OVERRIDE_UNSUPPORTED",
            reason="version_override",
        )

    if not normalized.startswith("v"):
        normalized = f"v{normalized}"
    if not re.match(r"^v\d+\.\d+\.\d+$", normalized):
        raise PlatformError(
            f"Unsupported version override '{version_override}'. Use a stable semver tag like v0.2.3.",
            code="E_RELEASE_VERSION_OVERRIDE_INVALID",
            reason="version_override",
        )
    return normalized


def _resolve_tag(
    *,
    source_ref: str,
    next_stable_tag: str,
    is_stable_branch: bool,
    branch_kind_tokens: set[str],
    version_override: str,
) -> tuple[str, str, str]:
    if is_stable_branch:
        return version_override or next_stable_tag, "", ""

    branch_leaf = source_ref.rsplit("/", 1)[-1]
    tokens = [token for token in re.split(r"[^A-Za-z0-9]+", branch_leaf) if token]
    ticket = ""
    ticket_end = -1
    if len(tokens) >= 2 and tokens[0].isalpha() and tokens[1].isdigit():
        ticket = f"{tokens[0].upper()}-{tokens[1]}"
        ticket_end = 1
    else:
        for idx, token in enumerate(tokens):
            match = re.match(r"^([A-Za-z]+)(\d+)$", token)
            if match:
                ticket = f"{match.group(1).upper()}-{match.group(2)}"
                ticket_end = idx
                break

    slug_tokens = tokens[ticket_end + 1 :] if ticket_end >= 0 else tokens
    if slug_tokens and slug_tokens[0].lower() in branch_kind_tokens:
        slug_tokens = slug_tokens[1:]
    feature_slug = "".join(token[:1].upper() + token[1:].lower() for token in slug_tokens) or "Feature"
    return f"{next_stable_tag}-{feature_slug}", ticket, feature_slug


def _resolve_draft(release_visibility: str) -> bool:
    normalized = (release_visibility or "auto").strip().lower()
    if normalized not in {"auto", "draft", "published"}:
        raise PlatformError(
            f"Unsupported release visibility '{release_visibility}'.",
            code="E_RELEASE_VISIBILITY_INVALID",
            reason="release_visibility",
        )
    return normalized == "draft"


def _resolve_prerelease(*, release_channel: str, is_stable_branch: bool) -> bool:
    normalized = (release_channel or "auto").strip().lower()
    if normalized not in {"auto", "prerelease", "ga"}:
        raise PlatformError(
            f"Unsupported release channel '{release_channel}'.",
            code="E_RELEASE_CHANNEL_INVALID",
            reason="release_channel",
        )
    if normalized == "auto":
        return not is_stable_branch
    return normalized == "prerelease"


def _resolve_install_flavor(install_flavor: str) -> str:
    normalized = (install_flavor or "standard").strip().lower()
    if normalized not in {"standard", "managed"}:
        raise PlatformError(
            f"Unsupported install flavor '{install_flavor}'.",
            code="E_RELEASE_INSTALL_FLAVOR_INVALID",
            reason="install_flavor",
        )
    return normalized


def _resolve_current_build_target(targets: tuple[BuildTarget, ...]) -> BuildTarget:
    normalized_system = platform.system().strip().lower()
    normalized_machine = platform.machine().strip().lower()
    machine_aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "aarch64": "arm64",
    }
    normalized_machine = machine_aliases.get(normalized_machine, normalized_machine)

    for target in targets:
        if target.system == normalized_system and target.machine == normalized_machine:
            return target
    raise PlatformError(
        f"Unsupported platform for binary build: {normalized_system}/{normalized_machine}",
        code="E_RELEASE_PLATFORM_UNSUPPORTED",
        reason=f"{normalized_system}:{normalized_machine}",
    )


def _resolve_candidate_workdir(repo_root: Path, candidate: Path) -> Path:
    if candidate.is_absolute():
        return candidate.resolve()
    return (repo_root / candidate).resolve()


def _resolve_explicit_workdir(repo_root: Path, candidate: Path, policy: ManualBuildPolicy) -> Path:
    attempts: list[Path] = []
    direct = _resolve_candidate_workdir(repo_root, candidate)
    attempts.append(direct)
    if _is_release_workdir(direct):
        return direct

    repo_like_root = _resolve_repo_root(repo_root)
    if repo_like_root != repo_root:
        from_detected_root = _resolve_candidate_workdir(repo_like_root, candidate)
        if from_detected_root not in attempts:
            attempts.append(from_detected_root)
            if _is_release_workdir(from_detected_root):
                return from_detected_root

    if candidate.name and repo_like_root.name == candidate.name and _is_release_workdir(repo_like_root):
        return repo_like_root

    return attempts[0]


def _resolve_repo_root(start_path: Path) -> Path:
    resolved_start = start_path.resolve()
    if _has_repo_root_markers(resolved_start):
        return resolved_start

    for parent in resolved_start.parents:
        if _has_repo_root_markers(parent):
            return parent

    return resolved_start


def _has_repo_root_markers(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    if (path / ".git").exists():
        return True
    return (path / ".ghdp").exists() and (path / ".github").exists()


def _find_release_workdir_candidates(repo_root: Path) -> list[Path]:
    candidates: list[Path] = []
    for child in sorted(repo_root.iterdir(), key=lambda path: path.name.lower()):
        if not child.is_dir():
            continue
        if _is_release_workdir(child):
            candidates.append(child.resolve())
    return candidates


def _is_release_workdir(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    return (path / "ghdp.spec").is_file() and (path / "pyproject.toml").is_file()


def _ensure_release_workdir(path: Path, *, repo_root: Path | None = None) -> None:
    if _is_release_workdir(path):
        return
    hint = ""
    if repo_root is not None:
        hint = f" Run the command from '{repo_root}' or the package directory, or pass --workdir with the correct path."
    raise PlatformError(
        f"Release workdir is not valid: {path}. Expected both 'ghdp.spec' and 'pyproject.toml'.{hint}",
        code="E_RELEASE_WORKDIR_INVALID",
        reason="workdir",
    )


def _repo_relative(path: Path, repo_root: Path) -> str:
    return str(path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
