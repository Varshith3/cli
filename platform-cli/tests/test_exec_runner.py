from __future__ import annotations

from types import SimpleNamespace

from platform_cli.exec import runner


def test_run_cmd_defaults_text_decode_errors_to_replace(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", _fake_run)

    result = runner.run_cmd(["echo", "ok"], check=True, capture=True, text=True)

    assert result.returncode == 0
    assert captured.get("errors") == "replace"
    assert captured.get("text") is True


def test_run_cmd_respects_explicit_decode_errors(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", _fake_run)

    result = runner.run_cmd(["echo", "ok"], check=True, capture=True, text=True, errors="strict")

    assert result.returncode == 0
    assert captured.get("errors") == "strict"
