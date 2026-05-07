from __future__ import annotations

import pytest

from platform_cli.core.errors import PlatformError
from platform_cli.tools import ai_provider


def test_detect_provider_statuses_uses_cached_state_without_refresh(monkeypatch) -> None:
    def _fake_get_tool_state(name: str):
        if name == "codex":
            return {
                "codex_exe": "codex",
                "codex_login_state": "ok",
                "codex_login_status": "logged in",
            }
        return {
            "claude_exe": "claude",
            "claude_health_state": "ok",
            "claude_health_status": "healthy",
        }

    monkeypatch.setattr(ai_provider, "get_tool_state", _fake_get_tool_state)
    monkeypatch.setattr(ai_provider, "_resolve_codex_exe", lambda: (_ for _ in ()).throw(AssertionError("codex should not refresh")))
    monkeypatch.setattr(ai_provider, "_resolve_claude_exe", lambda: (_ for _ in ()).throw(AssertionError("claude should not refresh")))

    statuses = ai_provider.detect_provider_statuses(refresh=False)

    assert statuses["codex"].available is True
    assert statuses["codex"].executable == "codex"
    assert statuses["claude"].available is True
    assert statuses["claude"].executable == "claude"


def test_detect_codex_status_refresh_updates_state(monkeypatch) -> None:
    captured: dict[str, dict[str, object]] = {}

    monkeypatch.setattr(ai_provider, "get_tool_state", lambda name: {})
    monkeypatch.setattr(ai_provider, "_resolve_codex_exe", lambda: "codex")
    monkeypatch.setattr(ai_provider, "_codex_login_status", lambda exe: (True, "logged in"))
    monkeypatch.setattr(ai_provider, "update_tool_state", lambda name, patch: captured.setdefault(name, patch))

    status = ai_provider._detect_codex_status(refresh=True)

    assert status.available is True
    assert status.executable == "codex"
    assert captured["codex"]["codex_login_state"] == "ok"


def test_detect_claude_status_refresh_updates_state(monkeypatch) -> None:
    captured: dict[str, dict[str, object]] = {}

    monkeypatch.setattr(ai_provider, "get_tool_state", lambda name: {})
    monkeypatch.setattr(ai_provider, "_resolve_claude_exe", lambda: "claude")
    monkeypatch.setattr(ai_provider, "_claude_health_status", lambda exe: (True, "healthy"))
    monkeypatch.setattr(ai_provider, "update_tool_state", lambda name, patch: captured.setdefault(name, patch))

    status = ai_provider._detect_claude_status(refresh=True)

    assert status.available is True
    assert status.executable == "claude"
    assert captured["claude"]["claude_health_state"] == "ok"


def test_select_provider_auto_single_available(monkeypatch) -> None:
    statuses = {
        "codex": ai_provider.ProviderStatus("codex", True, "codex", True, "ok"),
        "claude": ai_provider.ProviderStatus("claude", False, "", False, "missing"),
    }
    monkeypatch.setattr(ai_provider, "detect_provider_statuses", lambda refresh=False: statuses)

    provider, observed = ai_provider.select_provider(preferred="auto", interactive=True, refresh_on_missing=False)

    assert provider == "codex"
    assert observed == statuses


def test_select_provider_auto_both_available_prompts_and_persists(monkeypatch) -> None:
    statuses = {
        "codex": ai_provider.ProviderStatus("codex", True, "codex", True, "ok"),
        "claude": ai_provider.ProviderStatus("claude", True, "claude", True, "ok"),
    }
    captured: dict[str, str] = {}

    monkeypatch.setattr(ai_provider, "detect_provider_statuses", lambda refresh=False: statuses)
    monkeypatch.setattr(ai_provider, "_prompt_provider_choice", lambda s: "claude")
    monkeypatch.setattr(ai_provider, "set_value", lambda key, value: captured.setdefault(key, value))

    provider, _ = ai_provider.select_provider(
        preferred="auto",
        interactive=True,
        refresh_on_missing=False,
        persist_key="repo.ai.provider",
    )

    assert provider == "claude"
    assert captured["repo.ai.provider"] == "claude"


def test_select_provider_auto_none_available_falls_back_to_manual(monkeypatch) -> None:
    statuses = {
        "codex": ai_provider.ProviderStatus("codex", False, "", False, "missing"),
        "claude": ai_provider.ProviderStatus("claude", False, "", False, "missing"),
    }
    monkeypatch.setattr(ai_provider, "detect_provider_statuses", lambda refresh=False: statuses)

    provider, observed = ai_provider.select_provider(preferred="auto", interactive=True, refresh_on_missing=False)

    assert provider == "manual"
    assert observed == statuses


def test_select_provider_refreshes_when_auto_initially_has_no_available_providers(monkeypatch) -> None:
    statuses_by_refresh = {
        False: {
            "codex": ai_provider.ProviderStatus("codex", False, "", False, "missing"),
            "claude": ai_provider.ProviderStatus("claude", False, "", False, "missing"),
        },
        True: {
            "codex": ai_provider.ProviderStatus("codex", True, "codex", True, "ok"),
            "claude": ai_provider.ProviderStatus("claude", False, "", False, "missing"),
        },
    }

    monkeypatch.setattr(ai_provider, "detect_provider_statuses", lambda refresh=False: statuses_by_refresh[refresh])

    provider, observed = ai_provider.select_provider(preferred="auto", interactive=False, refresh_on_missing=True)

    assert provider == "codex"
    assert observed == statuses_by_refresh[True]


def test_select_provider_rejects_invalid_preference(monkeypatch) -> None:
    monkeypatch.setattr(
        ai_provider,
        "detect_provider_statuses",
        lambda refresh=False: {
            "codex": ai_provider.ProviderStatus("codex", False, "", False, "missing"),
            "claude": ai_provider.ProviderStatus("claude", False, "", False, "missing"),
        },
    )

    with pytest.raises(PlatformError) as err:
        ai_provider.select_provider(preferred="wat", interactive=False, refresh_on_missing=False)

    assert err.value.code == "E_PROVIDER_PREFERENCE_INVALID"


def test_run_codex_text_does_not_capture_console_output(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    output_path = tmp_path / "codex-output.txt"

    def _fake_mkstemp(*args, **kwargs):
        output_path.write_text("", encoding="utf-8")
        return (0, str(output_path))

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run_cmd(cmd, **kwargs):
        captured.update(kwargs)
        output_path.write_text("draft output", encoding="utf-8")
        return _Result()

    monkeypatch.setattr(ai_provider.tempfile, "mkstemp", _fake_mkstemp)
    monkeypatch.setattr(ai_provider.os, "close", lambda fd: None)
    monkeypatch.setattr(ai_provider, "run_cmd", _fake_run_cmd)

    payload = ai_provider._run_codex_text("codex", "prompt")

    assert payload == "draft output"
    assert captured["capture"] is True
    assert captured["text"] is False


def test_run_claude_text_uses_utf8_decode(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Result:
        returncode = 0
        stdout = "draft output"
        stderr = ""

    monkeypatch.setattr(ai_provider, "run_cmd", lambda cmd, **kwargs: captured.update(kwargs) or _Result())

    payload = ai_provider._run_claude_text("claude", "prompt")

    assert payload == "draft output"
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"


def test_run_with_heartbeat_emits_updates_for_long_running_calls() -> None:
    observed: list[int] = []

    def _work() -> str:
        ai_provider.time.sleep(0.05)
        return "done"

    result = ai_provider._run_with_heartbeat(
        _work,
        heartbeat=lambda elapsed: observed.append(int(elapsed >= 0)),
        initial_delay_s=0.01,
        interval_s=0.01,
    )

    assert result == "done"
    assert observed
