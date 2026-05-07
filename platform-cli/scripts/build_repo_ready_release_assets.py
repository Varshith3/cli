from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


CAPABILITY = "repo-ready-assets"
VERSION = "1.0.0"
TAG = "repo-ready-assets-v1.0.0"
TARGET_ROOT_KEY = "ghdp_user_root"
TARGET_SUBDIR = "repo_ready/base"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _source_root() -> Path:
    return _repo_root() / "release-assets" / "repo_ready"


def _asset_name(rel_path: Path) -> str:
    return "__".join(rel_path.parts)


def build_assets(output_dir: Path) -> Path:
    source_root = _source_root()
    if not source_root.exists():
        raise FileNotFoundError(f"Repo-ready release asset source is missing: {source_root}")

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
                "target_path": rel_path.as_posix(),
            }
        )

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
    parser = argparse.ArgumentParser(description="Build flattened repo-ready release assets for GHDP sync.")
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
