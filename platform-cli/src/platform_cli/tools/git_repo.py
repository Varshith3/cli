# NOTE: Architectural rules in ARCHITECTURE.md -- do not refactor cross-layer.
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from platform_cli.exec.runner import run_cmd


def get_commit_sha(default: str = "unknown") -> str:
    """Best-effort git commit SHA for current repository."""
    try:
        res = run_cmd(["git", "rev-parse", "HEAD"], check=False)
        if res.returncode == 0 and res.stdout:
            return res.stdout.splitlines()[0].strip()
    except Exception:
        pass
    return default


def get_short_commit_hash(repo_root: Optional[Path] = None, length: int = 7) -> str:
    """
    Get short git commit hash, matching Jenkins VERSION = GIT_COMMIT.take(7).

    Args:
        repo_root: Repository root path (uses cwd if None)
        length: Hash length (default 7, matching Jenkins)

    Returns:
        Short commit hash string (e.g., "a317aec")
    """
    cmd = ["git", "rev-parse", f"--short={length}", "HEAD"]
    kwargs = {}
    if repo_root:
        kwargs["cwd"] = str(repo_root)
    try:
        res = run_cmd(cmd, check=False, **kwargs)
        if res.returncode == 0 and res.stdout:
            return res.stdout.strip()
    except Exception:
        pass
    return "unknown"


def get_repo_name(default: str = "unknown") -> str:
    """Best-effort repository name for current repository.

    Prefers remote origin URL over directory name, because CI environments
    (Jenkins, GitHub Actions) often rename the workspace directory.
    """
    # Prefer remote URL — gives the canonical repo name regardless of workspace dir
    try:
        remote = run_cmd(["git", "config", "--get", "remote.origin.url"], check=False)
        if remote.returncode == 0 and remote.stdout:
            raw = remote.stdout.strip().rstrip("/")
            name = raw.split("/")[-1]
            if name.endswith(".git"):
                name = name[:-4]
            if name:
                return name
    except Exception:
        pass

    # Fallback to directory name (works for local dev, may be wrong in CI)
    try:
        top = run_cmd(["git", "rev-parse", "--show-toplevel"], check=False)
        if top.returncode == 0 and top.stdout:
            return Path(top.stdout.strip()).name
    except Exception:
        pass

    return default


def get_current_branch(repo_root: Optional[Path] = None) -> str:
    """Get current git branch name."""
    cmd = ["git", "rev-parse", "--abbrev-ref", "HEAD"]
    kwargs = {}
    if repo_root:
        kwargs["cwd"] = str(repo_root)
    try:
        res = run_cmd(cmd, check=False, **kwargs)
        if res.returncode == 0 and res.stdout:
            return res.stdout.strip()
    except Exception:
        pass
    return "unknown"


def resolve_short_hash(commit_id: str, repo_root: Optional[Path] = None, length: int = 7) -> str:
    """Resolve a commit reference to a short hash."""
    cmd = ["git", "rev-parse", f"--short={length}", commit_id]
    kwargs = {}
    if repo_root:
        kwargs["cwd"] = str(repo_root)
    try:
        res = run_cmd(cmd, check=False, **kwargs)
        if res.returncode == 0 and res.stdout:
            return res.stdout.strip()
    except Exception:
        pass
    return "unknown"


def is_main_branch(repo_root: Optional[Path] = None) -> bool:
    """Check if current branch is main or master."""
    branch = get_current_branch(repo_root)
    return branch in ("main", "master")


def fetch_tags(repo_root: Optional[Path] = None) -> None:
    """Fetch tags from origin to ensure we have latest release info."""
    cmd = ["git", "fetch", "--tags", "--quiet"]
    kwargs = {}
    if repo_root:
        kwargs["cwd"] = str(repo_root)
    try:
        run_cmd(cmd, check=False, **kwargs)
    except Exception:
        pass  # Best-effort — don't block if offline


def get_latest_release_tag(repo_root: Optional[Path] = None) -> str:
    """
    Get the latest semver release tag (e.g., 'v3.0.0' → '3.0.0').

    Fetches tags from origin first, then finds the latest vX.Y.Z tag.

    Returns:
        Version string without 'v' prefix (e.g., '3.0.0'), or '0.0.0' if no tags.
    """
    import re
    fetch_tags(repo_root)

    cmd = ["git", "tag", "--sort=-v:refname"]
    kwargs = {}
    if repo_root:
        kwargs["cwd"] = str(repo_root)
    try:
        res = run_cmd(cmd, check=False, **kwargs)
        if res.returncode == 0 and res.stdout:
            for line in res.stdout.strip().splitlines():
                tag = line.strip()
                match = re.match(r'^v?(\d+\.\d+\.\d+)$', tag)
                if match:
                    return match.group(1)
    except Exception:
        pass
    return "0.0.0"


def parse_semver(version: str) -> Tuple[int, int, int]:
    """Parse X.Y.Z into (major, minor, patch) tuple."""
    import re
    match = re.match(r'^(\d+)\.(\d+)\.(\d+)', version)
    if match:
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return (0, 0, 0)


def increment_patch(version: str) -> str:
    """Increment patch version: 3.0.0 → 3.0.1."""
    major, minor, patch = parse_semver(version)
    return f"{major}.{minor}.{patch + 1}"
