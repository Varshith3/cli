from __future__ import annotations

import importlib

output = importlib.import_module("platform_cli.core.output")


def test_safe_console_text_replaces_unencodable_characters(monkeypatch) -> None:
    class _Stdout:
        encoding = "cp1252"

    monkeypatch.setattr(output.sys, "stdout", _Stdout())

    rendered = output._safe_console_text("Jenkins says ⇒ still running")

    assert "?" in rendered
    assert "⇒" not in rendered


def test_safe_console_text_truncates_large_messages(monkeypatch) -> None:
    class _Stdout:
        encoding = "utf-8"

    monkeypatch.setattr(output.sys, "stdout", _Stdout())

    rendered = output._safe_console_text("x" * 5000)

    assert rendered.endswith("...[truncated]")
    assert len(rendered) <= 4000
