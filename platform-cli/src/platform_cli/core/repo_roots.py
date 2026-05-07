from __future__ import annotations

from pathlib import Path

from platform_cli.core.errors import PlatformError


_REPO_MARKER = ".git"


def find_repo_root(start_path: Path | None = None) -> Path | None:
    current = _normalize_start_path(start_path)
    for candidate in (current, *current.parents):
        if (candidate / _REPO_MARKER).exists():
            return candidate
    return None


def resolve_repo_root(
    explicit_repo_root: Path | None = None,
    *,
    cwd: Path | None = None,
) -> Path:
    start = explicit_repo_root if explicit_repo_root is not None else (cwd or Path.cwd())
    current = _normalize_start_path(start)
    repo_root = find_repo_root(current)
    if repo_root is not None:
        return repo_root

    start_label = "the provided path" if explicit_repo_root is not None else "the current directory"
    raise PlatformError(
        f"Could not find a git repository by searching upward from {start_label} '{current}'. "
        "Run this command from inside the target repo, or pass --repo-root <path>.",
        code="E_REPO_ROOT_NOT_FOUND",
        reason=str(current),
    )


def _normalize_start_path(path: Path | None) -> Path:
    candidate = (path or Path.cwd()).expanduser()
    resolved = candidate.resolve()
    if not resolved.exists():
        raise PlatformError(
            f"Path does not exist: {resolved}",
            code="E_REPO_ROOT_NOT_FOUND",
            reason=str(resolved),
        )
    if resolved.is_file():
        return resolved.parent
    return resolved
