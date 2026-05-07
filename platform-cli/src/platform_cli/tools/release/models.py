from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BuildTarget:
    system: str
    machine: str
    asset: str
    built_path: str


@dataclass(frozen=True)
class ReleasePlan:
    repo_root: Path
    repo_name_with_owner: str
    source_ref: str
    workdir: Path
    install_flavor: str
    python_version: str
    latest_stable_tag: str
    next_stable_tag: str
    tag: str
    ticket: str
    feature_slug: str
    is_stable_branch: bool
    draft: bool
    prerelease: bool
    summary_file: Path
    template_file: Path
    build_meta_path: Path
    runtime_defaults_path: Path
    build_version: str
    build_channel: str
    build_target: BuildTarget
    version_override: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["repo_root"] = str(self.repo_root)
        payload["workdir"] = str(self.workdir)
        payload["summary_file"] = str(self.summary_file)
        payload["template_file"] = str(self.template_file)
        payload["build_meta_path"] = str(self.build_meta_path)
        payload["runtime_defaults_path"] = str(self.runtime_defaults_path)
        return payload


@dataclass(frozen=True)
class ReleaseExecutionResult:
    tag: str
    asset: str
    asset_path: Path
    checksum_path: Path
    install_flavor: str
    prerelease: bool
    draft: bool

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tag": self.tag,
            "asset": self.asset,
            "asset_path": str(self.asset_path),
            "checksum_path": str(self.checksum_path),
            "install_flavor": self.install_flavor,
            "prerelease": self.prerelease,
            "draft": self.draft,
        }
        return payload
