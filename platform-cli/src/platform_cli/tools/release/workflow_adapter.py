from __future__ import annotations

import os
from pathlib import Path

from .models import ReleasePlan


def write_prepare_outputs_if_supported(plan: ReleasePlan) -> bool:
    output_path = str(os.getenv("GITHUB_OUTPUT", "") or "").strip()
    if not output_path:
        return False

    _append_github_output(
        Path(output_path),
        {
            "tag": plan.tag,
            "script_ref": plan.source_ref,
            "install_flavor": plan.install_flavor,
            "is_main": "true" if plan.source_ref == "main" else "false",
            "is_stable": "true" if plan.is_stable_branch else "false",
            "draft": "true" if plan.draft else "false",
            "prerelease": "true" if plan.prerelease else "false",
        },
    )
    return True


def _append_github_output(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")
