from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest


_ASSET_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "release-assets" / "repo_ready"
_GLOBAL_ASSET_SUBDIR = Path(".ghdp") / "repo_ready" / "base"
_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"

if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from platform_cli.tools import repo_ready, repo_ready_adapters, repo_ready_generation


def _seed_repo_ready_assets(repo_root: Path | None = None) -> dict[str, object]:
    target_root = Path.home() / _GLOBAL_ASSET_SUBDIR
    if target_root.exists():
        return {"source": "existing", "target_path": str(target_root)}
    shutil.copytree(_ASSET_SOURCE_ROOT, target_root, dirs_exist_ok=True)
    return {"source": "seeded", "target_path": str(target_root)}


@pytest.fixture(autouse=True)
def seed_repo_ready_assets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home_root = tmp_path / "ghdp-home"
    monkeypatch.setenv("HOME", str(home_root))
    monkeypatch.setenv("USERPROFILE", str(home_root))
    monkeypatch.setattr(repo_ready, "ensure_repo_ready_assets_synced", _seed_repo_ready_assets)
    monkeypatch.setattr(repo_ready_generation, "ensure_repo_ready_assets_synced", _seed_repo_ready_assets)
    monkeypatch.setattr(repo_ready_adapters, "ensure_repo_ready_assets_synced", _seed_repo_ready_assets)
