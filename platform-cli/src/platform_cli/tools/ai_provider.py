from __future__ import annotations

import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict

import typer

from platform_cli.core.config import set_value
from platform_cli.core.errors import PlatformError
from platform_cli.exec.runner import run_cmd
from platform_cli.state.store import get_tool_state, update_tool_state
from platform_cli.tools.claude_auth import _claude_health_status, _resolve_claude_exe
from platform_cli.tools.codex_auth import _codex_login_status, _resolve_codex_exe

VALID_PROVIDERS = {"auto", "manual", "codex", "claude"}


@dataclass(frozen=True)
class ProviderStatus:
    name: str
    available: bool
    executable: str
    auth_ok: bool
    detail: str


def _detect_codex_status(*, refresh: bool) -> ProviderStatus:
    state = get_tool_state("codex")
    if not refresh and state.get("codex_exe") and state.get("codex_login_state") == "ok":
        return ProviderStatus(
            name="codex",
            available=True,
            executable=str(state.get("codex_exe") or ""),
            auth_ok=True,
            detail=str(state.get("codex_login_status") or ""),
        )

    try:
        executable = _resolve_codex_exe()
        logged_in, status = _codex_login_status(executable)
        update_tool_state(
            "codex",
            {
                "codex_exe": executable,
                "codex_login_state": "ok" if logged_in else "missing",
                "codex_login_status": status,
                "codex_login_last_checked_at": int(time.time()),
            },
        )
        return ProviderStatus(
            name="codex",
            available=logged_in,
            executable=executable,
            auth_ok=logged_in,
            detail=status,
        )
    except PlatformError as exc:
        update_tool_state(
            "codex",
            {
                "codex_login_state": "missing",
                "codex_login_status": str(exc),
                "codex_login_last_checked_at": int(time.time()),
            },
        )
        return ProviderStatus(
            name="codex",
            available=False,
            executable="",
            auth_ok=False,
            detail=str(exc),
        )


def _detect_claude_status(*, refresh: bool) -> ProviderStatus:
    state = get_tool_state("claude")
    if not refresh and state.get("claude_exe") and state.get("claude_health_state") == "ok":
        return ProviderStatus(
            name="claude",
            available=True,
            executable=str(state.get("claude_exe") or ""),
            auth_ok=True,
            detail=str(state.get("claude_health_status") or ""),
        )

    try:
        executable = _resolve_claude_exe()
        healthy, status = _claude_health_status(executable)
        update_tool_state(
            "claude",
            {
                "claude_exe": executable,
                "claude_health_state": "ok" if healthy else "missing",
                "claude_health_status": status,
                "claude_health_last_checked_at": int(time.time()),
            },
        )
        return ProviderStatus(
            name="claude",
            available=healthy,
            executable=executable,
            auth_ok=healthy,
            detail=status,
        )
    except PlatformError as exc:
        update_tool_state(
            "claude",
            {
                "claude_health_state": "missing",
                "claude_health_status": str(exc),
                "claude_health_last_checked_at": int(time.time()),
            },
        )
        return ProviderStatus(
            name="claude",
            available=False,
            executable="",
            auth_ok=False,
            detail=str(exc),
        )


def detect_provider_statuses(*, refresh: bool = False) -> Dict[str, ProviderStatus]:
    return {
        "codex": _detect_codex_status(refresh=refresh),
        "claude": _detect_claude_status(refresh=refresh),
    }


def _prompt_provider_choice(statuses: Dict[str, ProviderStatus]) -> str:
    choices = []
    if statuses["codex"].available:
        choices.append("1=codex")
    if statuses["claude"].available:
        choices.append("2=claude")
    prompt = "AI provider [" + " | ".join(choices) + "]"

    while True:
        raw = str(typer.prompt(prompt)).strip().lower()
        if raw in {"1", "codex"} and statuses["codex"].available:
            return "codex"
        if raw in {"2", "claude"} and statuses["claude"].available:
            return "claude"
        typer.echo("Invalid provider choice.")


def select_provider(
    *,
    preferred: str,
    interactive: bool,
    refresh_on_missing: bool,
    persist_key: str | None = None,
) -> tuple[str, Dict[str, ProviderStatus]]:
    selected_preference = str(preferred or "auto").strip().lower()
    if selected_preference not in VALID_PROVIDERS:
        raise PlatformError(
            f"Invalid AI provider preference '{selected_preference}'. Use one of: auto, manual, codex, claude.",
            code="E_PROVIDER_PREFERENCE_INVALID",
            reason="provider_preference",
        )

    statuses = detect_provider_statuses(refresh=False)
    if refresh_on_missing and selected_preference in {"auto", "codex", "claude"}:
        needs_refresh = selected_preference == "auto" and not any(status.available for status in statuses.values())
        if selected_preference in statuses and not statuses[selected_preference].available:
            needs_refresh = True
        if needs_refresh:
            statuses = detect_provider_statuses(refresh=True)

    if selected_preference == "manual":
        return "manual", statuses

    if selected_preference in {"codex", "claude"}:
        if statuses[selected_preference].available:
            return selected_preference, statuses
        return "manual", statuses

    available = [name for name, status in statuses.items() if status.available]
    if not available:
        return "manual", statuses
    if len(available) == 1:
        return available[0], statuses
    if not interactive:
        return ("codex" if statuses["codex"].available else "claude"), statuses

    selected = _prompt_provider_choice(statuses)
    if selected in {"codex", "claude"} and persist_key:
        set_value(persist_key, selected)
    return selected, statuses


def _run_codex_text(executable: str, prompt: str, *, model: str | None = None) -> str:
    fd, output_path = tempfile.mkstemp(prefix="ghdp-repo-ready-", suffix=".txt")
    os.close(fd)
    path = Path(output_path)
    try:
        cmd = [executable, "exec", "--skip-git-repo-check"]
        if model:
            cmd.extend(["-m", model])
        cmd.extend(["-o", str(path), prompt])
        result = run_cmd(
            cmd,
            check=False,
            capture=True,
            text=False,
        )
        payload = path.read_text(encoding="utf-8").strip() if path.exists() else ""
        if result.returncode != 0 or not payload:
            raise PlatformError(
                "Codex did not return text output.",
                code="E_PROVIDER_GENERATION_FAILED",
                reason="codex",
            )
        return payload
    finally:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def _run_claude_text(executable: str, prompt: str) -> str:
    result = run_cmd(
        [executable, "-p", "--output-format", "text", prompt],
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    payload = (result.stdout or "").strip()
    if result.returncode != 0 or not payload:
        raise PlatformError(
            "Claude did not return text output.",
            code="E_PROVIDER_GENERATION_FAILED",
            reason="claude",
        )
    return payload


def _run_with_heartbeat(
    fn: Callable[[], str],
    *,
    heartbeat: Callable[[float], None] | None = None,
    initial_delay_s: float = 5.0,
    interval_s: float = 10.0,
) -> str:
    if heartbeat is None:
        return fn()

    stop_event = threading.Event()
    started_at = time.monotonic()

    def _heartbeat_loop() -> None:
        if stop_event.wait(initial_delay_s):
            return
        heartbeat(time.monotonic() - started_at)
        while not stop_event.wait(interval_s):
            heartbeat(time.monotonic() - started_at)

    worker = threading.Thread(target=_heartbeat_loop, daemon=True)
    worker.start()
    try:
        return fn()
    finally:
        stop_event.set()
        worker.join(timeout=0.2)


def generate_text(
    *,
    provider: str,
    statuses: Dict[str, ProviderStatus],
    prompt: str,
    model: str | None = None,
    heartbeat: Callable[[float], None] | None = None,
) -> str:
    if provider == "codex":
        return _run_with_heartbeat(
            lambda: _run_codex_text(statuses["codex"].executable, prompt, model=model),
            heartbeat=heartbeat,
        )
    if provider == "claude":
        return _run_with_heartbeat(
            lambda: _run_claude_text(statuses["claude"].executable, prompt),
            heartbeat=heartbeat,
        )
    raise PlatformError(
        f"Provider '{provider}' cannot generate text output.",
        code="E_PROVIDER_PREFERENCE_INVALID",
        reason="provider_generation",
    )
