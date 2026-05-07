# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import yaml

from platform_cli.core.errors import PlatformError


@dataclass(frozen=True)
class RepoReadyAssetLocation:
    scope_name: str
    root: Path


def _global_repo_ready_base_dir() -> Path:
    return Path.home() / ".ghdp" / "repo_ready" / "base"


def _parse_yaml(text: str, *, source: str) -> Dict[str, Any]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise PlatformError(
            f"Invalid YAML in {source}: {e}",
            code="E_REPO_READY_INVALID_YAML",
            reason=source,
        )

    if not isinstance(data, dict):
        raise PlatformError(
            f"Expected a YAML mapping in {source}",
            code="E_REPO_READY_INVALID_YAML",
            reason=source,
        )

    return data


def load_repo_ready_yaml_file(path: Path) -> Dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PlatformError(
            f"Repo readiness file not found: {path}",
            code="E_REPO_READY_FILE_NOT_FOUND",
            reason=str(path),
        )

    return _parse_yaml(text, source=str(path))


def _asset_locations(*, repo_root: Path | None = None) -> List[RepoReadyAssetLocation]:
    # Future expansion point:
    # - category overlays under ~/.ghdp/repo_ready/categories/<category>
    # - repo overlays under ~/.ghdp/repo_ready/repos/<repo-name>
    return [RepoReadyAssetLocation(scope_name="base_global", root=_global_repo_ready_base_dir().expanduser().resolve())]


def _find_repo_ready_asset(*parts: str, repo_root: Path | None = None) -> Path | None:
    for location in reversed(_asset_locations(repo_root=repo_root)):
        candidate = location.root.joinpath(*parts)
        if candidate.exists():
            return candidate
    return None


def _searched_roots(*parts: str, repo_root: Path | None = None) -> List[str]:
    return [str(location.root.joinpath(*parts)) for location in _asset_locations(repo_root=repo_root)]


def _require_repo_ready_asset(*parts: str, repo_root: Path | None = None, code: str, reason: str) -> Path:
    asset = _find_repo_ready_asset(*parts, repo_root=repo_root)
    if asset is None:
        searched = "; ".join(_searched_roots(*parts, repo_root=repo_root)) or "(no asset scopes configured)"
        raise PlatformError(
            f"Repo-ready asset missing. Searched: {searched}",
            code=code,
            reason=reason,
        )
    return asset


def load_repo_ready_template(relative_path: str, *, repo_root: Path | None = None) -> str:
    target = _require_repo_ready_asset(
        "templates",
        relative_path,
        repo_root=repo_root,
        code="E_REPO_READY_TEMPLATE_MISSING",
        reason=relative_path,
    )
    return target.read_text(encoding="utf-8")


def load_repo_ready_prompt(relative_path: str, *, repo_root: Path | None = None) -> str:
    target = _require_repo_ready_asset(
        "prompts",
        relative_path,
        repo_root=repo_root,
        code="E_REPO_READY_PROMPT_MISSING",
        reason=relative_path,
    )
    return target.read_text(encoding="utf-8")


def load_repo_ready_vocab(*, repo_root: Path | None = None) -> Dict[str, Any]:
    target = _require_repo_ready_asset(
        "vocab.yaml",
        repo_root=repo_root,
        code="E_REPO_READY_VOCAB_MISSING",
        reason="repo_ready/vocab.yaml",
    )
    return _parse_yaml(target.read_text(encoding="utf-8"), source=str(target))
