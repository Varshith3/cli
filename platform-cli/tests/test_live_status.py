from __future__ import annotations

from io import StringIO

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.live_status import LiveStatus, command_status, live_status_enabled


class _FakeStream(StringIO):
    def __init__(self, *, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_live_status_non_tty_emits_plain_lines() -> None:
    cli_ctx.non_interactive = False
    cli_ctx.quiet = False
    cli_ctx.json = False
    stream = _FakeStream(tty=False)

    status = LiveStatus(prefix="[release]", stream=stream)
    status.start("Triggering Jenkins job...")
    status.update("Waiting for Jenkins to assign a build...")
    status.finish()

    assert stream.getvalue() == (
        "[release] Triggering Jenkins job...\n"
        "[release] Waiting for Jenkins to assign a build...\n"
    )


def test_live_status_non_tty_finish_writes_final_milestone() -> None:
    cli_ctx.non_interactive = False
    cli_ctx.quiet = False
    cli_ctx.json = False
    stream = _FakeStream(tty=False)

    status = LiveStatus(prefix="[release]", stream=stream)
    status.start("Triggering Jenkins job...")
    status.finish("Feature-to-dev complete.")

    assert stream.getvalue() == (
        "[release] Triggering Jenkins job...\n"
        "[release] Feature-to-dev complete.\n"
    )


def test_live_status_tty_updates_single_line_and_clears_on_finish() -> None:
    cli_ctx.non_interactive = False
    cli_ctx.quiet = False
    cli_ctx.json = False
    stream = _FakeStream(tty=True)

    status = LiveStatus(prefix="[release]", stream=stream)
    status.start("Triggering Jenkins job...")
    status.update("Waiting for Jenkins to assign a build...")
    status.finish()

    output = stream.getvalue()
    assert "\r[release] Triggering Jenkins job..." in output
    assert "\r[release] Waiting for Jenkins to assign a build..." in output
    assert output.endswith("\r")


def test_command_status_uses_standard_prefix() -> None:
    cli_ctx.non_interactive = False
    cli_ctx.quiet = False
    cli_ctx.json = False
    stream = _FakeStream(tty=False)

    command_status("sync", stream=stream).update("Loading local inventory...")

    assert stream.getvalue() == "[sync] Loading local inventory...\n"


def test_live_status_suppresses_quiet_and_json_modes() -> None:
    cli_ctx.non_interactive = False
    stream = _FakeStream(tty=True)

    cli_ctx.quiet = True
    cli_ctx.json = False
    LiveStatus(prefix="[release]", stream=stream).update("hello")
    assert stream.getvalue() == ""

    cli_ctx.quiet = False
    cli_ctx.json = True
    LiveStatus(prefix="[release]", stream=stream).update("world")
    assert stream.getvalue() == ""


def test_live_status_enabled_tracks_quiet_and_json_modes() -> None:
    cli_ctx.quiet = False
    cli_ctx.json = False
    assert live_status_enabled() is True

    cli_ctx.quiet = True
    assert live_status_enabled() is False

    cli_ctx.quiet = False
    cli_ctx.json = True
    assert live_status_enabled() is False
