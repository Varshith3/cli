from __future__ import annotations

import json
import importlib.resources as pkg_resources
from dataclasses import dataclass

from platform_cli.core.errors import PlatformError

from .models import BuildTarget


@dataclass(frozen=True)
class ManualBuildPolicy:
    stable_branches: tuple[str, ...]
    branch_kind_tokens: tuple[str, ...]
    summary_file: str
    template_file: str
    workdir_default: str
    build_meta_path: str
    runtime_defaults_path: str
    asset_targets: tuple[BuildTarget, ...]


def load_manual_build_policy() -> ManualBuildPolicy:
    try:
        raw = (
            pkg_resources.files("platform_cli.resources")
            / "policy"
            / "manual-build-binaries.json"
        ).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PlatformError(
            "Missing release policy resource: policy/manual-build-binaries.json",
            code="E_RELEASE_POLICY_MISSING",
            reason="manual_build_policy",
        )

    try:
        payload = json.loads(raw)
    except Exception as e:
        raise PlatformError(
            f"Failed to parse manual build release policy: {e}",
            code="E_RELEASE_POLICY_INVALID",
            reason="manual_build_policy",
        )

    build = payload.get("build") if isinstance(payload, dict) else None
    notes = payload.get("release_notes") if isinstance(payload, dict) else None
    if not isinstance(build, dict) or not isinstance(notes, dict):
        raise PlatformError(
            "Manual build release policy is missing build or release_notes sections.",
            code="E_RELEASE_POLICY_INVALID",
            reason="manual_build_policy",
        )

    targets: list[BuildTarget] = []
    for item in build.get("asset_targets", []):
        if not isinstance(item, dict):
            continue
        try:
            targets.append(
                BuildTarget(
                    system=str(item.get("system") or "").strip().lower(),
                    machine=str(item.get("machine") or "").strip().lower(),
                    asset=str(item.get("asset") or "").strip(),
                    built_path=str(item.get("built_path") or "").strip(),
                )
            )
        except Exception:
            continue

    if not targets:
        raise PlatformError(
            "Manual build release policy does not define any asset targets.",
            code="E_RELEASE_POLICY_INVALID",
            reason="asset_targets",
        )

    stable_branches = tuple(str(v).strip() for v in payload.get("stable_branches", []) if str(v).strip())
    branch_kind_tokens = tuple(str(v).strip().lower() for v in payload.get("branch_kind_tokens", []) if str(v).strip())
    if not stable_branches:
        raise PlatformError(
            "Manual build release policy does not define stable branches.",
            code="E_RELEASE_POLICY_INVALID",
            reason="stable_branches",
        )

    return ManualBuildPolicy(
        stable_branches=stable_branches,
        branch_kind_tokens=branch_kind_tokens,
        summary_file=str(notes.get("summary_file") or "").strip(),
        template_file=str(notes.get("template_file") or "").strip(),
        workdir_default=str(build.get("workdir_default") or "platform-cli").strip(),
        build_meta_path=str(build.get("build_meta_path") or "").strip(),
        runtime_defaults_path=str(build.get("runtime_defaults_path") or "").strip(),
        asset_targets=tuple(targets),
    )
