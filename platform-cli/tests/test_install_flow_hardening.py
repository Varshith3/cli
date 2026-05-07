from __future__ import annotations

from types import SimpleNamespace

import platform_cli.tools.service as service_mod
import platform_cli.tools.winget as winget_mod


def test_run_tool_cmd_uses_spinner_for_non_winget_installs(monkeypatch) -> None:
    calls: list[tuple[list[str], dict]] = []

    def _fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((list(cmd), dict(kwargs)))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(service_mod, "run_cmd", _fake_run)
    monkeypatch.setattr(service_mod.sys, "platform", "darwin")

    service_mod._run_tool_cmd(["bash", "-lc", "brew install gh"], check=True, stream=True)

    assert calls == [
        (
            ["bash", "-lc", "brew install gh"],
            {"check": True, "capture": True, "rich_logs": True},
        )
    ]


def test_run_tool_cmd_prepares_winget_and_streams_output(monkeypatch) -> None:
    calls: list[tuple[list[str], dict]] = []
    prepared: list[bool] = []

    def _fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((list(cmd), dict(kwargs)))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(service_mod, "run_cmd", _fake_run)
    monkeypatch.setattr(service_mod, "ensure_winget_ready", lambda allow_repair=True: prepared.append(allow_repair))
    monkeypatch.setattr(service_mod.sys, "platform", "win32")

    service_mod._run_tool_cmd(["winget", "install", "--id", "GitHub.cli"], check=True, stream=True)

    assert prepared == [True]
    assert calls == [
        (
            ["winget", "install", "--id", "GitHub.cli"],
            {"check": True, "capture": False, "rich_logs": True},
        )
    ]


def test_ensure_winget_ready_accepts_known_sources(monkeypatch) -> None:
    calls: list[list[str]] = []

    monkeypatch.setattr(winget_mod, "is_windows", lambda: True)
    monkeypatch.setattr(winget_mod, "ensure_winget", lambda allow_repair=True: calls.append(["ensure", str(allow_repair)]))
    monkeypatch.setattr(
        winget_mod,
        "run_cmd",
        lambda cmd, check=False: calls.append(list(cmd)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    winget_mod.ensure_winget_ready()

    assert calls == [
        ["ensure", "True"],
        ["winget", "list", "--source", "winget", "--accept-source-agreements", "--disable-interactivity"],
        ["winget", "list", "--source", "msstore", "--accept-source-agreements", "--disable-interactivity"],
    ]
