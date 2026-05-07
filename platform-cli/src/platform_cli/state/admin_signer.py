from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from platform_cli.core.errors import PlatformError
from platform_cli.state.store import StatePaths, default_state_paths

ADMIN_SIGNER_DIRNAME = "admin"
ADMIN_SIGNER_SUBDIR = "signer"
ADMIN_SIGNER_PRIVATE_KEY = "private_ed25519.pem"
ADMIN_SIGNER_METADATA = "metadata.json"


@dataclass(frozen=True)
class AdminSignerPaths:
    signer_dir: Path
    private_key_path: Path
    metadata_path: Path


def default_admin_signer_paths(paths: Optional[StatePaths] = None) -> AdminSignerPaths:
    resolved = paths or default_state_paths()
    signer_dir = resolved.root / ADMIN_SIGNER_DIRNAME / ADMIN_SIGNER_SUBDIR
    return AdminSignerPaths(
        signer_dir=signer_dir,
        private_key_path=signer_dir / ADMIN_SIGNER_PRIVATE_KEY,
        metadata_path=signer_dir / ADMIN_SIGNER_METADATA,
    )


def read_signer_metadata(paths: Optional[StatePaths] = None) -> Dict[str, Any]:
    resolved = default_admin_signer_paths(paths)
    if not resolved.metadata_path.exists():
        return {}
    try:
        payload = json.loads(resolved.metadata_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PlatformError(
            f"Failed to read admin signer metadata: {exc}",
            code="E_ADMIN_SIGNER_READ_FAILED",
            reason=str(resolved.metadata_path),
        )
    if not isinstance(payload, dict):
        raise PlatformError(
            "Admin signer metadata must be a JSON object.",
            code="E_ADMIN_SIGNER_INVALID",
            reason=str(resolved.metadata_path),
        )
    return payload


def write_signer_metadata(payload: Dict[str, Any], paths: Optional[StatePaths] = None) -> Path:
    resolved = default_admin_signer_paths(paths)
    resolved.signer_dir.mkdir(parents=True, exist_ok=True)
    resolved.metadata_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return resolved.metadata_path


def read_private_key_pem(paths: Optional[StatePaths] = None) -> str:
    resolved = default_admin_signer_paths(paths)
    if not resolved.private_key_path.exists():
        return ""
    try:
        return resolved.private_key_path.read_text(encoding="utf-8").strip()
    except Exception as exc:
        raise PlatformError(
            f"Failed to read admin signer private key: {exc}",
            code="E_ADMIN_SIGNER_READ_FAILED",
            reason=str(resolved.private_key_path),
        )


def write_private_key_pem(private_key_pem: str, paths: Optional[StatePaths] = None) -> Path:
    resolved = default_admin_signer_paths(paths)
    resolved.signer_dir.mkdir(parents=True, exist_ok=True)
    resolved.private_key_path.write_text(str(private_key_pem or "").strip() + "\n", encoding="utf-8")
    return resolved.private_key_path


def has_local_signer(paths: Optional[StatePaths] = None) -> bool:
    resolved = default_admin_signer_paths(paths)
    return resolved.private_key_path.exists() and resolved.metadata_path.exists()

