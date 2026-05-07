from __future__ import annotations

import json

from typer import Typer
from typer.testing import CliRunner

from platform_cli.commands import doctor
from platform_cli.core.github_auth import GithubAuthState
from platform_cli.tools import doctor_checks


def test_doctor_payload_includes_brew_on_macos(monkeypatch) -> None:
    monkeypatch.setattr(doctor_checks.sys, "platform", "darwin")
    monkeypatch.setattr(doctor_checks.platform, "platform", lambda: "macOS-14.5-arm64")
    monkeypatch.setattr(doctor_checks.platform, "python_version", lambda: "3.12.7")
    monkeypatch.setattr(doctor_checks, "tool_status", lambda name: f"{name}-ok")

    def _fake_which(cmd: str) -> str | None:
        return {
            "brew": "/opt/homebrew/bin/brew",
            "pipx": "/opt/homebrew/bin/pipx",
        }.get(cmd)

    monkeypatch.setattr(doctor_checks.shutil, "which", _fake_which)
    monkeypatch.setattr(doctor_checks, "_version_text", lambda cmd, timeout_s=5: "1.0.0")

    payload = doctor_checks.doctor_payload()
    checks = {row["check"]: row["value"] for row in payload}

    assert checks["brew"] == "/opt/homebrew/bin/brew (1.0.0)"
    assert checks["git"] == "git-ok"
    assert checks["terraform"] == "terraform-ok"
    assert checks["gh"] == "gh-ok"
    assert "winget" not in checks
    assert checks["install flavor"] in {"managed", "standard tech"}
    assert checks["auth mode"] in {"managed_locked", "personal_allowed"}
    assert checks["managed auth"] in {"configured", "missing", "not applicable"}
    assert checks["github auth source"] in {"managed_state", "personal_env_or_cli", "managed_state_missing", "none"}


def test_tool_status_prefers_managed_and_active_details(monkeypatch) -> None:
    spec = doctor_checks.ToolRuntimeSpec(
        name="git",
        display_name="Git",
        detect_cmd=["detect"],
        version_cmd=["version"],
        install_cmd=[],
        upgrade_cmd=None,
        uninstall_cmd=None,
        version_req=None,
        bin_name="git",
        manager="brew",
        brew_formula="git",
    )

    monkeypatch.setattr(doctor_checks, "_tool_spec_from_registry", lambda name: spec if name == "git" else None)
    monkeypatch.setattr(doctor_checks.shutil, "which", lambda cmd: "/usr/bin/git")

    class _Result:
        def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _fake_run(cmd, check=False, timeout_s=5):
        if cmd == ["detect"]:
            return _Result(returncode=0)
        if cmd == ["version"]:
            return _Result(returncode=0, stdout="2.39.5")
        if cmd == ["git", "--version"]:
            return _Result(returncode=0, stdout="apple-git-154")
        raise AssertionError(cmd)

    monkeypatch.setattr(doctor_checks, "run_cmd", _fake_run)

    value = doctor_checks.tool_status("git")

    assert value == "managed=2.39.5 | active=/usr/bin/git (apple-git-154)"


def test_tool_status_falls_back_when_stateful_detection_fails(monkeypatch) -> None:
    spec = doctor_checks.ToolRuntimeSpec(
        name="git",
        display_name="Git",
        detect_cmd=["detect"],
        version_cmd=["version"],
        install_cmd=[],
        upgrade_cmd=None,
        uninstall_cmd=None,
        version_req=None,
        bin_name="git",
    )

    monkeypatch.setattr(doctor_checks, "_tool_spec_from_registry", lambda name: spec if name == "git" else None)
    monkeypatch.setattr(
        doctor_checks,
        "run_cmd",
        lambda cmd, check=False, timeout_s=5: (_ for _ in ()).throw(PermissionError("probe failed")),
    )
    monkeypatch.setattr(doctor_checks.shutil, "which", lambda cmd: "/usr/bin/git")

    assert doctor_checks.tool_status("git") == "active=/usr/bin/git"


def test_doctor_respects_json_flag(monkeypatch) -> None:
    runner = CliRunner()
    app = Typer()
    doctor.register(app)

    monkeypatch.setattr(doctor, "doctor_payload", lambda: [{"check": "brew", "value": "/opt/homebrew/bin/brew"}])

    result = runner.invoke(app, ["--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {"checks": [{"check": "brew", "value": "/opt/homebrew/bin/brew"}]}


def test_doctor_payload_reports_install_flavor_and_managed_auth(monkeypatch) -> None:
    monkeypatch.setattr(
        doctor_checks,
        "inspect_github_auth",
        lambda: GithubAuthState(
            install_flavor="managed",
            managed_auth_status="configured",
            managed_token_present=True,
            auth_mode="managed_locked",
            effective_github_auth_source="managed_state",
        ),
    )
    monkeypatch.setattr(doctor_checks.sys, "platform", "linux")
    monkeypatch.setattr(doctor_checks, "tool_status", lambda name: f"{name}-ok")
    monkeypatch.setattr(doctor_checks.shutil, "which", lambda cmd: None)

    payload = doctor_checks.doctor_payload()
    checks = {row["check"]: row["value"] for row in payload}

    assert checks["install flavor"] == "managed"
    assert checks["auth mode"] == "managed_locked"
    assert checks["managed auth"] == "configured"
    assert checks["github auth source"] == "managed_state"
