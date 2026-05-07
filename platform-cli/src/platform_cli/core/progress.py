from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import typer

from platform_cli.core.context import ctx as cli_ctx


@dataclass
class GenerationProgressReporter:
    prefix: str = "[repo]"

    def _emit(self, message: str) -> None:
        if cli_ctx.json or cli_ctx.quiet:
            return
        typer.echo(f"{self.prefix} {message}")

    def phase(self, message: str) -> None:
        self._emit(message)

    def info(self, message: str) -> None:
        self._emit(message)

    def target_started(self, rel_path: str, *, action: str = "Generating") -> None:
        self._emit(f"{action} {rel_path}...")

    def target_heartbeat(self, rel_path: str, *, action: str = "Still generating", elapsed_s: float | None = None) -> None:
        if cli_ctx.verbose and elapsed_s is not None:
            self._emit(f"{action} {rel_path}... ({int(elapsed_s)}s elapsed)")
            return
        self._emit(f"{action} {rel_path}...")

    def target_done(self, rel_path: str, *, outcome: str = "generated") -> None:
        self._emit(f"{outcome} {rel_path}")

    def target_failed(self, rel_path: str, message: str, *, action: str = "generation failed") -> None:
        self._emit(f"{action} for {rel_path}: {message}")

    def heartbeat_callback(
        self,
        rel_path: str,
        *,
        action: str = "Still generating",
    ) -> Callable[[float], None]:
        return lambda elapsed_s: self.target_heartbeat(rel_path, action=action, elapsed_s=elapsed_s)
