from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from platform_cli.manifests.validate import validate_claude_athena_workgroup_map


CAPABILITY = "claude-athena-workgroup-map"
VERSION = "1.0.3"
TAG = "claude-athena-workgroup-map-v1.0.3"
TARGET_ROOT_KEY = "ghdp_user_root"
TARGET_SUBDIR = "policies"
SOURCE_FILENAME = "athena-workgroup-map.json"
TARGET_FILENAME = "claude-athena-workgroup-map.managed.json"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _source_path() -> Path:
    return _repo_root() / "src" / "platform_cli" / "resources" / "claude" / SOURCE_FILENAME


def build_assets(output_dir: Path) -> Path:
    source_path = _source_path()
    if not source_path.exists():
        raise FileNotFoundError(f"Claude Athena workgroup source file is missing: {source_path}")

    payload = json.loads(source_path.read_text(encoding="utf-8-sig"))
    validate_claude_athena_workgroup_map(payload)

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(source_path, output_dir / SOURCE_FILENAME)
    manifest = {
        "capability": CAPABILITY,
        "version": VERSION,
        "tag": TAG,
        "target_root_key": TARGET_ROOT_KEY,
        "target_subdir": TARGET_SUBDIR,
        "files": [
            {
                "asset_name": SOURCE_FILENAME,
                "target_path": TARGET_FILENAME,
            }
        ],
    }
    (output_dir / "content-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Build flattened Claude Athena workgroup release assets for GHDP sync.")
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
