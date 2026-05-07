from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from platform_cli.core.errors import PlatformError
from platform_cli.core.github_auth import gh_auth_ready, gh_subprocess_env, is_managed_install
from platform_cli.exec.runner import run_cmd

from .metadata import (
    render_release_notes,
    write_build_metadata,
    write_runtime_defaults,
)
from .models import ReleaseExecutionResult, ReleasePlan
from .planner import validate_release_notes_freshness


def ensure_binaries_release(plan: ReleasePlan) -> dict[str, object]:
    _ensure_gh_authenticated()
    validate_release_notes_freshness(plan)
    notes = render_release_notes(plan)

    with tempfile.TemporaryDirectory(prefix="ghdp_release_notes_") as tmpdir:
        notes_path = Path(tmpdir) / "release_notes.md"
        notes_path.write_text(notes, encoding="utf-8")
        _ensure_tag_ref(plan)
        release_id = _find_release_id(plan)
        if release_id:
            _update_release(plan=plan, release_id=release_id, notes_path=notes_path)
        else:
            _create_release(plan=plan, notes_path=notes_path)

    return {
        "tag": plan.tag,
        "draft": plan.draft,
        "prerelease": plan.prerelease,
        "source_ref": plan.source_ref,
        "release_repo": plan.repo_name_with_owner,
    }


def build_binaries_for_current_platform(
    plan: ReleasePlan,
    *,
    ensure_release: bool = False,
) -> ReleaseExecutionResult:
    if ensure_release:
        ensure_binaries_release(plan)

    write_build_metadata(plan)
    write_runtime_defaults(plan)
    _install_build_dependencies(plan)
    _run_pyinstaller(plan)
    asset_path, checksum_path = _prepare_asset(plan)
    asset_paths = [asset_path, checksum_path]
    _upload_assets(plan, asset_paths=asset_paths)
    return ReleaseExecutionResult(
        tag=plan.tag,
        asset=plan.build_target.asset,
        asset_path=asset_path,
        checksum_path=checksum_path,
        install_flavor=plan.install_flavor,
        prerelease=plan.prerelease,
        draft=plan.draft,
    )


def _ensure_gh_authenticated() -> None:
    if gh_auth_ready():
        return
    if is_managed_install():
        raise PlatformError(
            "Managed GitHub auth is not configured for this installation. Reinstall or refresh the managed bundle.",
            code="E_GH_NOT_AUTHENTICATED",
            reason="managed_github_auth_missing",
        )
    raise PlatformError(
        "GitHub CLI is not authenticated. Run 'gh auth login' or provide GH_TOKEN.",
        code="E_GH_NOT_AUTHENTICATED",
        reason="gh_auth",
    )


def _ensure_tag_ref(plan: ReleasePlan) -> None:
    ref = _gh_run_cmd(
        ["gh", "api", f"repos/{plan.repo_name_with_owner}/git/ref/tags/{plan.tag}"],
        check=False,
    )
    if ref.returncode == 0:
        return
    sha = _gh_run_cmd(
        ["gh", "api", f"repos/{plan.repo_name_with_owner}/commits/{plan.source_ref}", "-q", ".sha"],
        check=True,
    ).stdout.strip()
    if not sha:
        raise PlatformError(
            f"GHDP could not create or verify release tag '{plan.tag}' because it could not resolve a commit SHA for "
            f"source ref '{plan.source_ref}'. Confirm that the ref exists on GitHub, then retry.",
            code="E_RELEASE_SOURCE_SHA_MISSING",
            reason="source_ref",
        )
    _gh_run_cmd(
        [
            "gh",
            "api",
            "-X",
            "POST",
            f"repos/{plan.repo_name_with_owner}/git/refs",
            "-f",
            f"ref=refs/tags/{plan.tag}",
            "-f",
            f"sha={sha}",
        ],
        check=True,
    )


def _find_release_id(plan: ReleasePlan) -> int | None:
    res = _gh_run_cmd(
        ["gh", "api", f"repos/{plan.repo_name_with_owner}/releases/tags/{plan.tag}"],
        check=False,
    )
    if res.returncode != 0:
        return None
    try:
        payload = json.loads(res.stdout or "{}")
    except Exception as e:
        raise PlatformError(
            f"GHDP found an existing GitHub release response for tag '{plan.tag}', but could not parse it: {e}. "
            "Retry once, and if it still fails inspect the GitHub release payload for that tag.",
            code="E_RELEASE_LOOKUP_INVALID",
            reason="release_lookup",
        )
    raw_id = payload.get("id")
    return int(raw_id) if raw_id is not None else None


def _update_release(*, plan: ReleasePlan, release_id: int, notes_path: Path) -> None:
    if plan.is_stable_branch and _is_stable_tag(plan.tag):
        raise PlatformError(
            f"GHDP will not modify stable release '{plan.tag}' from source ref '{plan.source_ref}' because that "
            "stable tag already exists. Choose a new stable version or use a prerelease tag instead.",
            code="E_RELEASE_STABLE_EXISTS",
            reason="release",
        )
    _gh_run_cmd(
        [
            "gh",
            "release",
            "edit",
            plan.tag,
            "--repo",
            plan.repo_name_with_owner,
            "--notes-file",
            str(notes_path),
        ],
        check=True,
    )
    _gh_run_cmd(
        [
            "gh",
            "api",
            "-X",
            "PATCH",
            f"repos/{plan.repo_name_with_owner}/releases/{release_id}",
            "-f",
            f"draft={'true' if plan.draft else 'false'}",
            "-f",
            f"prerelease={'true' if plan.prerelease else 'false'}",
        ],
        check=True,
    )


def _create_release(*, plan: ReleasePlan, notes_path: Path) -> None:
    cmd = [
        "gh",
        "release",
        "create",
        plan.tag,
        "--repo",
        plan.repo_name_with_owner,
        "--target",
        plan.source_ref,
        "--title",
        plan.tag,
        "--notes-file",
        str(notes_path),
    ]
    if plan.draft:
        cmd.append("--draft")
    if plan.prerelease:
        cmd.append("--prerelease")
    _gh_run_cmd(cmd, check=True)


def _install_build_dependencies(plan: ReleasePlan) -> None:
    cwd = plan.workdir
    run_cmd([sys.executable, "-m", "pip", "install", "-U", "pip"], check=True, cwd=cwd)
    run_cmd([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True, cwd=cwd)


def _run_pyinstaller(plan: ReleasePlan) -> None:
    run_cmd(
        [sys.executable, "-m", "PyInstaller", "ghdp.spec", "--clean", "--noconfirm"],
        check=True,
        cwd=plan.workdir,
    )


def _prepare_asset(plan: ReleasePlan) -> tuple[Path, Path]:
    out_dir = plan.workdir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    source = _resolve_built_artifact(plan)
    asset_path = out_dir / plan.build_target.asset
    shutil.copy2(source, asset_path)
    checksum_path = _write_checksum_file(asset_path)
    return asset_path, checksum_path


def _resolve_built_artifact(plan: ReleasePlan) -> Path:
    built_path = plan.workdir / plan.build_target.built_path
    if built_path.is_file():
        return built_path
    if built_path.is_dir():
        files = [p for p in built_path.rglob("*") if p.is_file()]
        if files:
            return files[0]

    dist_root = plan.workdir / "dist"
    candidates = [p for p in dist_root.rglob("ghdp*") if p.is_file()] if dist_root.exists() else []
    if candidates:
        return candidates[0]

    raise PlatformError(
        f"GHDP finished the build step but could not find the expected artifact '{plan.build_target.asset}' under "
        f"'{plan.workdir / 'dist'}'. Check the PyInstaller output for this platform, then retry the upload.",
        code="E_RELEASE_ARTIFACT_MISSING",
        reason="artifact",
    )


def _write_checksum_file(asset_path: Path) -> Path:
    checksum = hashlib.sha256(asset_path.read_bytes()).hexdigest()
    checksum_path = asset_path.with_name(f"{asset_path.name}.sha256")
    checksum_path.write_text(checksum + "\n", encoding="utf-8")
    return checksum_path


def _upload_assets(plan: ReleasePlan, *, asset_paths: list[Path]) -> None:
    _gh_run_cmd(
        [
            "gh",
            "release",
            "upload",
            plan.tag,
            "--repo",
            plan.repo_name_with_owner,
            *[str(path) for path in asset_paths],
            "--clobber",
        ],
        check=True,
    )


def _is_stable_tag(tag: str) -> bool:
    import re

    return bool(re.match(r"^v\d+\.\d+\.\d+$", tag))


def _gh_run_cmd(cmd: list[str], *, check: bool = True, cwd: str | Path | None = None):
    return run_cmd(cmd, check=check, cwd=cwd, env=gh_subprocess_env())
