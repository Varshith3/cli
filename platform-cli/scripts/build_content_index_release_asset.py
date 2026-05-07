from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


CAPABILITY = "content-index"
VERSION = "1.0.0"
TAG = "content-index-latest"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _source_root() -> Path:
    return _repo_root() / "release-assets" / "content_index"


def _source_index_path() -> Path:
    return _source_root() / "content-index.json"


def _validate_source(payload: object) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Content index source must be a JSON object.")
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise ValueError("Content index source must define at least one capability.")
    if not any(isinstance(item, dict) and str(item.get("capability", "")).strip() == "ghdp-team-toolset" for item in capabilities):
        raise ValueError("Content index source must include the ghdp-team-toolset capability.")


def build_assets(output_dir: Path) -> Path:
    source_root = _source_root()
    source_index = _source_index_path()
    if not source_root.exists():
        raise FileNotFoundError(f"Content-index release asset source is missing: {source_root}")
    if not source_index.exists():
        raise FileNotFoundError(f"Content-index source file is missing: {source_index}")

    payload = json.loads(source_index.read_text(encoding="utf-8-sig"))
    _validate_source(payload)

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "content-index.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the GHDP content-index release asset.")
    parser.add_argument(
        "--output-dir",
        default=str(_repo_root() / "dist" / TAG),
        help="Output directory for the content-index release asset.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()
    built = build_assets(output_dir)
    print(str(built))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
