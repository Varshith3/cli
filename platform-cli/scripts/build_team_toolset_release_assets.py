from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from platform_cli.manifests.validate import validate_toolset, validate_toolset_ownership_alignment


CAPABILITY = "ghdp-team-toolset"
VERSION = "1.0.5"
TAG = "ghdp-team-toolset-v1.0.5"
TARGET_ROOT_KEY = "ghdp_user_root"
TARGET_SUBDIR = "policies"
SOURCE_FILENAME = "toolset.json"
TARGET_FILENAME = "team-toolset.managed.json"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _source_root() -> Path:
    return _repo_root() / "release-assets" / "team_toolset"


def _packaged_fallback_path() -> Path:
    return _repo_root() / "src" / "platform_cli" / "resources" / "manifests" / "toolset.json"


def _asset_name(rel_path: Path) -> str:
    return "__".join(rel_path.parts)


def build_assets(output_dir: Path) -> Path:
    source_root = _source_root()
    source_toolset_path = source_root / SOURCE_FILENAME
    packaged_fallback_path = _packaged_fallback_path()
    if not source_root.exists():
        raise FileNotFoundError(f"Team-toolset release asset source is missing: {source_root}")
    if not source_toolset_path.exists():
        raise FileNotFoundError(f"Team-toolset source file is missing: {source_toolset_path}")
    if not packaged_fallback_path.exists():
        raise FileNotFoundError(f"Packaged fallback toolset is missing: {packaged_fallback_path}")

    source_toolset = json.loads(source_toolset_path.read_text(encoding="utf-8-sig"))
    packaged_toolset = json.loads(packaged_fallback_path.read_text(encoding="utf-8-sig"))
    validate_toolset(source_toolset)
    validate_toolset(packaged_toolset)
    validate_toolset_ownership_alignment(packaged_toolset, source_toolset)

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    files: list[dict[str, str]] = []
    for source_path in sorted(path for path in source_root.rglob("*") if path.is_file()):
        rel_path = source_path.relative_to(source_root)
        asset_name = _asset_name(rel_path)
        shutil.copy2(source_path, output_dir / asset_name)
        files.append(
            {
                "asset_name": asset_name,
                "target_path": TARGET_FILENAME if rel_path.name == SOURCE_FILENAME else rel_path.as_posix(),
            }
        )

    if not files:
        raise FileNotFoundError(f"Team-toolset release asset source is empty: {source_root}")

    manifest = {
        "capability": CAPABILITY,
        "version": VERSION,
        "tag": TAG,
        "target_root_key": TARGET_ROOT_KEY,
        "target_subdir": TARGET_SUBDIR,
        "files": files,
    }
    (output_dir / "content-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Build flattened team-toolset release assets for GHDP sync.")
    parser.add_argument(
        "--output-dir",
        default=str(_repo_root() / "dist" / TAG),
        help="Output directory for release assets.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()
    built = build_assets(output_dir)
    print(str(built))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
