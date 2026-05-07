from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from platform_cli.core.errors import PlatformError


DEFAULT_TARGET_TYPE = "filesystem"


class SyncTargetHandler(Protocol):
    name: str

    def resolve_install_root(
        self,
        *,
        root_key: str,
        target_subdir: str,
        resolve_root_key: Callable[[str], Path],
    ) -> Path:
        ...


@dataclass(frozen=True)
class FilesystemTargetHandler:
    name: str = DEFAULT_TARGET_TYPE

    def resolve_install_root(
        self,
        *,
        root_key: str,
        target_subdir: str,
        resolve_root_key: Callable[[str], Path],
    ) -> Path:
        return resolve_root_key(root_key) / target_subdir


_TARGET_HANDLERS: dict[str, SyncTargetHandler] = {}


def register_target_handler(handler: SyncTargetHandler) -> None:
    name = handler.name.strip()
    if not name:
        raise ValueError("Target handler name cannot be empty.")
    _TARGET_HANDLERS[name] = handler


def get_target_handler(target_type: str) -> SyncTargetHandler:
    target_name = target_type.strip() or DEFAULT_TARGET_TYPE
    handler = _TARGET_HANDLERS.get(target_name)
    if handler is None:
        raise PlatformError(
            f"Unsupported sync target type: {target_name}",
            code="E_SYNC_TARGET_UNSUPPORTED",
            reason=target_name,
        )
    return handler


register_target_handler(FilesystemTargetHandler())
register_target_handler(FilesystemTargetHandler(name="codex_skills"))
register_target_handler(FilesystemTargetHandler(name="codex_plugins"))
register_target_handler(FilesystemTargetHandler(name="claude_skills"))
register_target_handler(FilesystemTargetHandler(name="claude_plugins"))
register_target_handler(FilesystemTargetHandler(name="tableau_drivers"))
