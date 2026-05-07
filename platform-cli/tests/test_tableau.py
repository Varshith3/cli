from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from platform_cli.core.errors import PlatformError
from platform_cli.tools import tableau


JAR_A = "AthenaJDBC42_2.0.27.1000.jar"
JAR_B = "athena-jdbc-1.0.0-SNAPSHOT.jar"


def _write_jars(download_dir: Path) -> None:
    download_dir.mkdir(parents=True, exist_ok=True)
    (download_dir / JAR_A).write_text("a", encoding="utf-8")
    (download_dir / JAR_B).write_text("b", encoding="utf-8")


def test_tableau_init_dry_run_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    download_dir = tmp_path / "Downloads"
    _write_jars(download_dir)
    monkeypatch.setattr(tableau.sys, "platform", "win32")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))

    result = tableau.init(download_dir=download_dir, dry_run=True, force=False)

    assert result["success"] is True
    assert result["platform"] == "windows"
    assert result["properties_updated"] is False
    assert len(result["jars_copied"]) == 2


def test_tableau_init_missing_jar_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    download_dir = tmp_path / "Downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    (download_dir / JAR_A).write_text("a", encoding="utf-8")
    monkeypatch.setattr(tableau.sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))

    with pytest.raises(PlatformError) as err:
        tableau.init(download_dir=download_dir, dry_run=False, force=False)

    assert err.value.code == "E_TABLEAU_JAR_MISSING"


def test_tableau_init_mac_updates_properties_and_backup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    download_dir = tmp_path / "Downloads"
    drivers_dir = tmp_path / "custom-drivers"
    _write_jars(download_dir)
    monkeypatch.setattr(tableau.sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))

    props = tmp_path / "Documents" / "My Tableau Repository" / "Datasources" / "athena.properties"
    props.parent.mkdir(parents=True, exist_ok=True)
    props.write_text("old", encoding="utf-8")

    result = tableau.init(
        download_dir=download_dir,
        drivers_dir=drivers_dir,
        dry_run=False,
        force=True,
    )

    assert result["success"] is True
    assert result["properties_updated"] is True
    assert result["properties_backup"] is not None
    assert props.exists()
    assert "AwsCredentialsProviderClass=com.simba.athena.amazonaws.auth.DefaultAWSCredentialsProviderChain" in props.read_text(
        encoding="utf-8"
    )
    assert (drivers_dir / JAR_A).exists()
    assert (drivers_dir / JAR_B).exists()


def test_tableau_init_release_dry_run_without_local_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tableau.sys, "platform", "win32")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))

    result = tableau.init(download_dir=None, dry_run=True, force=False)

    assert result["success"] is True
    assert result["platform"] == "windows"
    assert any("release assets" in m.lower() for m in result["messages"])


def test_tableau_init_fallback_to_release_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    download_dir = tmp_path / "Downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    # only one jar present -> should trigger release fallback
    (download_dir / JAR_A).write_text("a", encoding="utf-8")

    release_drivers = tmp_path / "Program Files" / "Tableau" / "Drivers"
    release_drivers.mkdir(parents=True, exist_ok=True)

    def _fake_install_release_content(**kwargs):  # type: ignore[no-untyped-def]
        (release_drivers / JAR_A).write_text("a2", encoding="utf-8")
        (release_drivers / JAR_B).write_text("b2", encoding="utf-8")
        return {
            "capability": "tableau-athena-jars",
            "target_path": str(release_drivers),
            "file_count": 2,
            "updated_count": 2,
            "content_hash": "abc",
            "synced_at": 123,
            "source": "release",
            "release_repo": "owner/repo",
            "release_tag": "v1.0.0",
            "content_version": "1.0.0",
        }

    monkeypatch.setattr(tableau, "install_release_content", _fake_install_release_content)
    monkeypatch.setattr(tableau.sys, "platform", "win32")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    drivers_dir = tmp_path / "drivers"

    result = tableau.init(
        download_dir=download_dir,
        drivers_dir=drivers_dir,
        dry_run=False,
        force=True,
    )

    assert result["success"] is True
    assert result["drivers_dir"] == str(drivers_dir)
    assert result["download_dir"] == str(release_drivers)
    assert result["jars_copied"] == []
    assert result["jars_skipped"] == []
    assert any("installed or updated: 2" in message for message in result["messages"])


def test_tableau_init_release_download_failure_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tableau.sys, "platform", "win32")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))

    def _failing_install_release_content(**kwargs):  # type: ignore[no-untyped-def]
        raise PlatformError("download failed", code="E_RELEASE_CONTENT_DOWNLOAD_FAILED", reason="content-manifest.json")

    monkeypatch.setattr(tableau, "install_release_content", _failing_install_release_content)

    with pytest.raises(PlatformError) as err:
        tableau.init(download_dir=None, dry_run=False, force=False)

    assert err.value.code == "E_RELEASE_CONTENT_DOWNLOAD_FAILED"


def test_ensure_initialized_for_login_skips_when_artifacts_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tableau.sys, "platform", "win32")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))

    drivers_dir = tmp_path / "Program Files" / "Tableau" / "Drivers"
    drivers_dir.mkdir(parents=True, exist_ok=True)
    (drivers_dir / JAR_A).write_text("a", encoding="utf-8")
    (drivers_dir / JAR_B).write_text("b", encoding="utf-8")

    result = tableau.ensure_initialized_for_login()
    assert result["initialized_now"] is False
    assert "already completed" in " ".join(result["messages"]).lower()


def test_ensure_initialized_for_login_runs_init_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tableau.sys, "platform", "win32")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))

    called = {"ok": False}

    def _fake_init(**kwargs):  # type: ignore[no-untyped-def]
        called["ok"] = True
        return {"success": True, "messages": ["initialized from fake init"]}

    monkeypatch.setattr(tableau, "init", _fake_init)

    result = tableau.ensure_initialized_for_login()
    assert called["ok"] is True
    assert result["initialized_now"] is True


def test_refresh_credentials_for_tableau_syncs_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    state_updates: list[dict[str, object]] = []
    configured: list[str] = []

    def _fake_login(*, profile: str) -> None:
        assert profile == "dp-hr"

    def _fake_ensure(*, profile: str) -> None:
        configured.append(profile)

    def _fake_run_aws(args, *, capture=False, check=True):  # type: ignore[no-untyped-def]
        calls.append(list(args))
        if args[:3] == ["configure", "export-credentials", "--profile"]:
            return SimpleNamespace(
                stdout='{"AccessKeyId":"AKIA1","SecretAccessKey":"SECRET1","SessionToken":"TOKEN1","Expiration":"2026-03-03T08:00:00Z"}'
            )
        return SimpleNamespace(stdout="")

    def _fake_update(tool_name: str, patch: dict[str, object]) -> None:
        assert tool_name == "tableau"
        state_updates.append(patch)

    monkeypatch.setattr(tableau, "aws_sso_login", _fake_login)
    monkeypatch.setattr(tableau, "ensure_sso_configured", _fake_ensure)
    monkeypatch.setattr(tableau, "run_aws_cli", _fake_run_aws)
    monkeypatch.setattr(tableau, "update_tool_state", _fake_update)

    result = tableau.refresh_credentials_for_tableau(profile="dp-hr")

    assert result["success"] is True
    assert result["target_profile"] == "default"
    assert configured == ["dp-hr"]
    assert any(cmd[:5] == ["configure", "set", "aws_access_key_id", "AKIA1", "--profile"] for cmd in calls)
    assert any(cmd[:5] == ["configure", "set", "aws_secret_access_key", "SECRET1", "--profile"] for cmd in calls)
    assert any(cmd[:5] == ["configure", "set", "aws_session_token", "TOKEN1", "--profile"] for cmd in calls)
    assert state_updates
    assert state_updates[0]["default_sync_status"] == "ok"


def test_refresh_credentials_for_tableau_raises_on_export_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_login(*, profile: str) -> None:
        assert profile == "dp-hr"

    def _fake_ensure(*, profile: str) -> None:
        assert profile == "dp-hr"

    def _fake_run_aws(args, *, capture=False, check=True):  # type: ignore[no-untyped-def]
        raise PlatformError("boom", code="E_CMD_FAILED", reason="nonzero_exit")

    monkeypatch.setattr(tableau, "aws_sso_login", _fake_login)
    monkeypatch.setattr(tableau, "ensure_sso_configured", _fake_ensure)
    monkeypatch.setattr(tableau, "run_aws_cli", _fake_run_aws)

    with pytest.raises(PlatformError) as err:
        tableau.refresh_credentials_for_tableau(profile="dp-hr")

    assert err.value.code == "E_TABLEAU_EXPORT_CREDENTIALS_FAILED"


def test_refresh_credentials_for_tableau_raises_on_missing_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_login(*, profile: str) -> None:
        assert profile == "dp-hr"

    def _fake_ensure(*, profile: str) -> None:
        assert profile == "dp-hr"

    def _fake_run_aws(args, *, capture=False, check=True):  # type: ignore[no-untyped-def]
        if args[:3] == ["configure", "export-credentials", "--profile"]:
            return SimpleNamespace(stdout='{"AccessKeyId":"AKIA1"}')
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(tableau, "aws_sso_login", _fake_login)
    monkeypatch.setattr(tableau, "ensure_sso_configured", _fake_ensure)
    monkeypatch.setattr(tableau, "run_aws_cli", _fake_run_aws)

    with pytest.raises(PlatformError) as err:
        tableau.refresh_credentials_for_tableau(profile="dp-hr")

    assert err.value.code == "E_TABLEAU_CREDENTIALS_JSON_INVALID"


def test_refresh_credentials_for_tableau_raises_on_default_sync_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_login(*, profile: str) -> None:
        assert profile == "dp-hr"

    def _fake_ensure(*, profile: str) -> None:
        assert profile == "dp-hr"

    def _fake_run_aws(args, *, capture=False, check=True):  # type: ignore[no-untyped-def]
        if args[:3] == ["configure", "export-credentials", "--profile"]:
            return SimpleNamespace(stdout='{"AccessKeyId":"AKIA1","SecretAccessKey":"SECRET1","SessionToken":"TOKEN1"}')
        if args[:3] == ["configure", "set", "aws_secret_access_key"]:
            raise PlatformError("write failed", code="E_CMD_FAILED", reason="nonzero_exit")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(tableau, "aws_sso_login", _fake_login)
    monkeypatch.setattr(tableau, "ensure_sso_configured", _fake_ensure)
    monkeypatch.setattr(tableau, "run_aws_cli", _fake_run_aws)

    with pytest.raises(PlatformError) as err:
        tableau.refresh_credentials_for_tableau(profile="dp-hr")

    assert err.value.code == "E_TABLEAU_DEFAULT_PROFILE_SYNC_FAILED"


def test_refresh_credentials_for_tableau_raises_on_sso_config_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_ensure(*, profile: str) -> None:
        assert profile == "dp-hr"
        raise PlatformError("config failed", code="E_AWS_SSO_CONFIG_INCOMPLETE", reason="aws_sso")

    monkeypatch.setattr(tableau, "ensure_sso_configured", _fake_ensure)

    with pytest.raises(PlatformError) as err:
        tableau.refresh_credentials_for_tableau(profile="dp-hr")

    assert err.value.code == "E_TABLEAU_AWS_SSO_CONFIG_FAILED"

