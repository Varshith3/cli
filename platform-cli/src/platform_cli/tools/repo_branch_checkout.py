# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from platform_cli.core.github_auth import gh_subprocess_env
from platform_cli.exec.runner import run_cmd


@dataclass(frozen=True)
class CheckoutResult:
    persist_ready: bool
    message: str
    repo_root: Path | None = None


def checkout_remote_branch_if_safe(*, repo: str, branch_name: str) -> CheckoutResult:
    inside = run_cmd(["git", "rev-parse", "--is-inside-work-tree"], check=False, capture=True)
    if inside.returncode != 0 or (inside.stdout or "").strip().lower() != "true":
        return CheckoutResult(False, "ℹ️ Current directory is not inside a git repo. Branch creation succeeded, but local checkout was skipped.")

    root_res = run_cmd(["git", "rev-parse", "--show-toplevel"], check=False, capture=True)
    repo_root = Path((root_res.stdout or "").strip()) if root_res.returncode == 0 and (root_res.stdout or "").strip() else None
    if repo_root is None:
        return CheckoutResult(False, "ℹ️ Could not resolve the local repo root. Branch creation succeeded, but local checkout was skipped.")

    local_repo = _resolve_local_repo_name(repo_root)
    if local_repo != repo:
        return CheckoutResult(
            False,
            f"ℹ️ Local repo '{local_repo or 'unknown'}' does not match target repo '{repo}'. Branch creation succeeded, but local checkout was skipped.",
            repo_root=repo_root,
        )

    dirty = run_cmd(["git", "status", "--porcelain"], check=False, capture=True, cwd=repo_root)
    if (dirty.stdout or "").strip():
        return CheckoutResult(False, "ℹ️ Local worktree is not clean. Branch creation succeeded, but local checkout was skipped.", repo_root=repo_root)

    run_cmd(["git", "fetch", "origin", branch_name], check=True, capture=True, cwd=repo_root)
    run_cmd(["git", "checkout", "-B", branch_name, f"origin/{branch_name}"], check=True, capture=True, cwd=repo_root)
    return CheckoutResult(True, f"✅ Checked out local branch: {branch_name}", repo_root=repo_root)


def _resolve_local_repo_name(repo_root: Path) -> str:
    res = run_cmd(
        ["gh", "repo", "view", "--json", "nameWithOwner"],
        check=False,
        capture=True,
        cwd=repo_root,
        env=gh_subprocess_env(),
    )
    if res.returncode != 0:
        return ""
    return (res.stdout or "").replace("{", "").replace("}", "").strip() if "nameWithOwner" not in (res.stdout or "") else _parse_name_with_owner(res.stdout or "")


def _parse_name_with_owner(raw: str) -> str:
    import json

    try:
        data = json.loads(raw or "{}")
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("nameWithOwner") or "").strip()
