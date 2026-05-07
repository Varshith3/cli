from __future__ import annotations

import sys
from typing import TextIO

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.output import _safe_console_text


def live_status_enabled() -> bool:
    """Return True when transient live status should be rendered."""
    return not bool(cli_ctx.quiet or cli_ctx.json)


class LiveStatus:
    """Shared presenter for lightweight long-running CLI status updates."""

    def __init__(self, *, prefix: str = "", stream: TextIO | None = None) -> None:
        self.prefix = str(prefix or "").strip()
        self.stream = stream or sys.stdout
        self._last_width = 0
        self._visible = False
        self._last_line = ""

    def start(self, message: str) -> None:
        self.update(message)

    def update(self, message: str) -> None:
        if self._suppressed:
            return
        line = self._format(message)
        if self._interactive:
            padded = line
            if len(line) < self._last_width:
                padded = f"{line}{' ' * (self._last_width - len(line))}"
            self._write(f"\r{padded}")
            self._last_width = len(line)
            self._visible = True
            return
        if line != self._last_line:
            self._write(f"{line}\n")
            self._last_line = line

    def milestone(self, message: str) -> None:
        if self._suppressed:
            return
        line = self._format(message)
        if self._interactive and self._visible:
            self._clear_line()
        self._write(f"{line}\n")
        self._last_line = line

    def finish(self, message: str | None = None) -> None:
        if self._suppressed:
            return
        if self._interactive:
            if message:
                self._clear_line()
                rendered = self._format(message)
                self._write(f"{rendered}\n")
                self._last_line = rendered
                return
            if self._visible:
                self._clear_line()
            return
        if message:
            line = self._format(message)
            if line != self._last_line:
                self._write(f"{line}\n")
                self._last_line = line

    @property
    def _suppressed(self) -> bool:
        return not live_status_enabled()

    @property
    def _interactive(self) -> bool:
        if cli_ctx.non_interactive or self._suppressed:
            return False
        is_tty = getattr(self.stream, "isatty", None)
        return bool(callable(is_tty) and is_tty())

    def _format(self, message: str) -> str:
        rendered = _safe_console_text(message)
        return f"{self.prefix} {rendered}".strip() if self.prefix else rendered

    def _clear_line(self) -> None:
        self._write(f"\r{' ' * self._last_width}\r")
        self._last_width = 0
        self._visible = False

    def _write(self, text: str) -> None:
        self.stream.write(text)
        self.stream.flush()


def command_status(command: str, *, stream: TextIO | None = None) -> LiveStatus:
    """Build a standard command-scoped live status presenter for transient runtime status."""
    cleaned = str(command or "").strip()
    prefix = f"[{cleaned}]" if cleaned else ""
    return LiveStatus(prefix=prefix, stream=stream)
