from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import platform_cli
import pytest
from typer.testing import CliRunner

from platform_cli.cli import app
from platform_cli.core import access
from platform_cli.core import release_content
from platform_cli.core.config import get_value, set_value
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.state.access_session import (
    get_access_session,
    get_active_token,
    get_assumed_team,
    set_active_token,
    set_assumed_team,
    set_remembered_actor,
)

runner = CliRunner()


def _set_home(monkeypatch, home: Path) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    cli_ctx.non_interactive = False
    cli_ctx.verbose = False
    cli_ctx.quiet = False
    cli_ctx.json = False
    monkeypatch.setattr("platform_cli.cli.maybe_check_for_update", lambda force=False: False)


def _mock_actor(monkeypatch, login: str) -> None:
    monkeypatch.setattr(
        access,
        "run_cmd",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=login, stderr=""),
    )


def _write_team_policy(
    home: Path,
    *,
    admin_users: list[str],
    inform_denies: list[str] | None = None,
    inform_sync_allow: list[str] | None = None,
    inform_sync_deny: list[str] | None = None,
    use_legacy_sync_fields: bool = False,
) -> None:
    policy_dir = home / ".ghdp" / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    inform_payload = {
        "allow_capabilities": [],
        "deny_capabilities": inform_denies or [],
    }
    if use_legacy_sync_fields:
        if inform_sync_allow is not None:
            inform_payload["allow_sync_capabilities"] = inform_sync_allow
        if inform_sync_deny is not None:
            inform_payload["deny_sync_capabilities"] = inform_sync_deny
    else:
        if inform_sync_allow is not None or inform_sync_deny is not None:
            inform_payload["sync"] = {
                "allow_capabilities": inform_sync_allow or [],
                "deny_capabilities": inform_sync_deny or [],
            }
    payload = {
        "schema_version": "1.0",
        "managed_by": "ghdp",
        "teams": {
            "inform": inform_payload
        },
        "admin_users": admin_users,
    }
    (policy_dir / "team-policy.managed.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_access_policy(
    home: Path,
    *,
    non_admin_extra_capabilities: list[str] | None = None,
) -> None:
    policy_dir = home / ".ghdp" / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    non_admin_capabilities = [
        "admin.view",
        "team.initial_select",
        "config.user_safe_write",
        "tools.install",
        "tools.uninstall",
        "tools.read",
        "usage.read",
        "repo.read",
        "sync.read",
        "sync.mutate",
        "publish.execute",
        "team.switch",
    ]
    if non_admin_extra_capabilities:
        non_admin_capabilities.extend(non_admin_extra_capabilities)
    payload = {
        "schema_version": "1.0",
        "personas": {
            "non_admin": {
                "capabilities": non_admin_capabilities
            },
            "admin": {
                "capabilities": [
                    "admin.view",
                    "team.initial_select",
                    "config.user_safe_write",
                    "tools.install",
                    "tools.read",
                    "usage.read",
                    "repo.read",
                    "sync.read",
                    "team.switch",
                    "config.admin_write",
                    "tools.uninstall",
                    "repo.fix",
                    "repo.accept",
                    "sync.mutate",
                    "publish.execute",
                    "release.manage",
                    "branch.create",
                    "platform.internal",
                    "admin.token.issue",
                ]
            },
        },
        "token": {
            "allowed_capabilities": [
                "config.user_safe_write",
                "tools.install",
                "team.switch",
                "config.admin_write",
                "tools.uninstall",
                "repo.fix",
                "repo.accept",
                "sync.mutate",
                "publish.execute",
                "usage.read",
            ],
            "allowed_capabilities_by_scope": {
                "user": [
                    "config.user_safe_write",
                    "tools.install",
                    "team.switch",
                    "config.admin_write",
                    "tools.uninstall",
                    "repo.fix",
                    "repo.accept",
                    "sync.mutate",
                    "publish.execute",
                    "usage.read",
                ],
                "team": [
                    "usage.read",
                    "tools.install",
                    "config.user_safe_write",
                ],
                "user_team": [
                    "config.user_safe_write",
                    "tools.install",
                    "team.switch",
                    "config.admin_write",
                    "tools.uninstall",
                    "repo.fix",
                    "repo.accept",
                    "sync.mutate",
                    "publish.execute",
                    "usage.read",
                ],
            },
            "default_ttl_minutes": 60,
            "max_ttl_minutes": 480,
            "default_team_only_ttl_minutes": 15,
            "max_team_only_ttl_minutes": 30,
            "signing": {
                "format": "ghdp.sig.v2",
                "algorithm": "ed25519",
                "active_key_id": "",
                "verification_keys": [],
            },
            "capability_catalog": {},
        },
        "config_rules": {
            "precommit.mode": {
                "user_safe_values": ["warn", "enforce"],
                "admin_only_values": ["off"],
            },
            "git.strict_clean": {
                "user_safe_values": [True],
                "admin_only_values": [False],
            },
        },
        "help": {"support_contact": "platform team"},
    }
    (policy_dir / "access_policy.json").write_text(json.dumps(payload), encoding="utf-8")


def _bootstrap_signer(home: Path) -> None:
    access.setup_local_signer(key_id="test-admin-key", overwrite=True, update_local_policy=True)


def test_team_use_allows_non_admin_switch_with_release1_policy(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _mock_actor(monkeypatch, "basic-user")
    monkeypatch.setattr("platform_cli.commands.team.ensure_team_toolset_synced", lambda: None)

    first = runner.invoke(app, ["team", "use", "--team", "default"])
    second = runner.invoke(app, ["team", "use", "--team", "data_analyst"])

    assert first.exit_code == 0
    assert "Saved team: default" in first.output
    assert second.exit_code == 0
    assert "Saved team: data_analyst" in second.output


def test_non_admin_cannot_set_precommit_off(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _mock_actor(monkeypatch, "basic-user")

    res = runner.invoke(app, ["config", "precommit", "--mode", "off"])

    assert res.exit_code != 0
    assert "config.admin_write" in str(res.exception)


def test_non_admin_cannot_disable_git_strict_clean(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _mock_actor(monkeypatch, "basic-user")

    res = runner.invoke(app, ["config", "git-strict-clean", "--disabled"])

    assert res.exit_code != 0
    assert "platform.internal" in str(res.exception)


def test_access_view_shows_admin_persona(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_team_policy(tmp_path, admin_users=["admin-user"])
    _mock_actor(monkeypatch, "admin-user")
    set_value("team.selected", "platform")

    res = runner.invoke(app, ["access", "view"])

    assert res.exit_code == 0
    assert "actor: admin-user" in res.output
    assert "base_persona: admin" in res.output
    assert "persona: admin" in res.output
    assert "selected_team: platform" in res.output
    assert "effective_team: platform" in res.output


def test_admin_view_hidden_alias_matches_access_view(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_team_policy(tmp_path, admin_users=["admin-user"])
    _mock_actor(monkeypatch, "admin-user")
    set_value("team.selected", "platform")

    access_res = runner.invoke(app, ["access", "view"])
    admin_res = runner.invoke(app, ["admin", "view"])

    assert access_res.exit_code == 0
    assert admin_res.exit_code == 0
    assert "actor: admin-user" in admin_res.output
    assert "persona: admin" in admin_res.output
    assert "effective_team: platform" in admin_res.output


def test_commands_overview_lists_access_and_admin_surface(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_team_policy(tmp_path, admin_users=["admin-user"])
    _mock_actor(monkeypatch, "admin-user")

    access_res = runner.invoke(app, ["commands", "--category", "access"])
    admin_res = runner.invoke(app, ["commands", "--category", "admin"])

    assert access_res.exit_code == 0
    assert "access status" in access_res.output
    assert "access token" in access_res.output
    assert "access inspect" in access_res.output
    assert "access reset" in access_res.output
    assert admin_res.exit_code == 0
    assert "admin token" in admin_res.output
    assert "admin assume" in admin_res.output
    assert "admin return" in admin_res.output
    assert "admin view" not in admin_res.output
    assert "admin token activate" not in admin_res.output


def test_sync_check_shows_bootstrap_action_when_missing_and_install_allowed(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)

    def _fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        asset_name = cmd[cmd.index("--pattern") + 1]
        download_dir = Path(cmd[cmd.index("--dir") + 1])
        tag = cmd[3]
        if asset_name == "content-index.json":
            payload = {
                "schema_version": "1.0",
                "generated_at": "2026-04-14T15:10:00Z",
                "capabilities": [
                    {
                        "capability": "example-capability",
                        "version": "1.0.0",
                        "provider": "github_release",
                        "source": {
                            "repo": "owner/repo",
                            "tag": tag,
                            "manifest_asset": "content-manifest.json",
                        },
                        "package_type": "file_bundle",
                        "target_type": "filesystem",
                        "allow_install_if_missing": True,
                        "policy": {
                            "allow_update_existing_files": True,
                            "allow_new_files_on_update": False,
                            "min_cli_version": "0.1.0",
                        },
                    }
                ],
            }
            (download_dir / asset_name).write_text(json.dumps(payload), encoding="utf-8")
            return
        if asset_name == "content-manifest.json":
            payload = {
                "schema_version": "1.0",
                "capability": "example-capability",
                "version": "1.0.0",
                "target_root_key": "ghdp_user_root",
                "target_subdir": "bundle",
                "files": [
                    {"asset_name": "a.txt", "target_path": "a.txt"},
                    {"asset_name": "b.txt", "target_path": "nested/b.txt"},
                ],
            }
            (download_dir / asset_name).write_text(json.dumps(payload), encoding="utf-8")
            return
        if asset_name == "a.txt":
            (download_dir / asset_name).write_text("a", encoding="utf-8")
            return
        if asset_name == "b.txt":
            (download_dir / asset_name).write_text("b", encoding="utf-8")
            return
        raise RuntimeError(f"unexpected asset {asset_name}")

    monkeypatch.setattr(release_content, "run_cmd", _fake_run)

    res = runner.invoke(app, ["sync", "check", "--capability", "example-capability"])

    assert res.exit_code == 0
    assert "action=bootstrap" in res.output
    assert "files to install: a.txt, nested/b.txt" in res.output


def test_sync_check_marks_disallowed_capability_blocked_by_team_policy(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path)
    _write_team_policy(
        tmp_path,
        admin_users=[],
        inform_sync_allow=["allowed-capability"],
    )
    _mock_actor(monkeypatch, "basic-user")
    set_value("team.selected", "inform")
    monkeypatch.setattr(
        "platform_cli.commands.sync.preview_content_updates",
        lambda **_kwargs: {
            "capabilities": [
                {
                    "capability": "example-capability",
                    "action": "update",
                    "local_version": "1.0.0",
                    "latest_version": "1.1.0",
                    "missing_local_files": [],
                    "updatable_files": ["a.txt"],
                    "ignored_new_files": [],
                    "missing_from_latest_manifest": [],
                    "recovery_detail": "",
                }
            ]
        },
    )

    res = runner.invoke(app, ["sync", "check", "--capability", "example-capability"])

    assert res.exit_code == 0
    assert "example-capability: blocked" in res.output
    assert "blocked by team sync policy for 'inform'" in res.output


def test_sync_update_skips_team_disallowed_capability(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path, non_admin_extra_capabilities=["sync.mutate"])
    _write_team_policy(
        tmp_path,
        admin_users=[],
        inform_sync_allow=["allowed-capability"],
    )
    _mock_actor(monkeypatch, "basic-user")
    set_value("team.selected", "inform")
    monkeypatch.setattr(
        "platform_cli.commands.sync.preview_content_updates",
        lambda **_kwargs: {
            "capabilities": [
                {
                    "capability": "example-capability",
                    "action": "update",
                    "local_version": "1.0.0",
                    "latest_version": "1.1.0",
                    "missing_local_files": [],
                    "updatable_files": ["a.txt"],
                    "ignored_new_files": [],
                    "missing_from_latest_manifest": [],
                    "recovery_detail": "",
                }
            ]
        },
    )
    called = {"count": 0}
    monkeypatch.setattr(
        "platform_cli.commands.sync.apply_content_update",
        lambda *_args, **_kwargs: called.__setitem__("count", called["count"] + 1),
    )

    res = runner.invoke(app, ["sync", "update", "--capability", "example-capability", "--auto-approve"])

    assert res.exit_code == 0
    assert called["count"] == 0
    assert "Update blocked by team sync restrictions." in res.output


def test_sync_repair_honors_legacy_team_sync_policy_fields(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path, non_admin_extra_capabilities=["sync.mutate"])
    _write_team_policy(
        tmp_path,
        admin_users=[],
        inform_sync_deny=["example-capability"],
        use_legacy_sync_fields=True,
    )
    _mock_actor(monkeypatch, "basic-user")
    set_value("team.selected", "inform")
    monkeypatch.setattr(
        "platform_cli.commands.sync.preview_content_updates",
        lambda **_kwargs: {
            "capabilities": [
                {
                    "capability": "example-capability",
                    "action": "repair",
                    "local_version": "1.0.0",
                    "latest_version": "1.0.0",
                    "missing_local_files": ["a.txt"],
                    "updatable_files": [],
                    "ignored_new_files": [],
                    "missing_from_latest_manifest": [],
                    "recovery_detail": "",
                }
            ]
        },
    )
    called = {"count": 0}
    monkeypatch.setattr(
        "platform_cli.commands.sync.repair_content",
        lambda *_args, **_kwargs: called.__setitem__("count", called["count"] + 1),
    )

    res = runner.invoke(app, ["sync", "repair", "--capability", "example-capability", "--auto-approve"])

    assert res.exit_code == 0
    assert called["count"] == 0
    assert "Repair blocked by team sync restrictions." in res.output


def test_sync_run_blocks_disallowed_capability_without_mutation(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path, non_admin_extra_capabilities=["sync.mutate"])
    _write_team_policy(
        tmp_path,
        admin_users=[],
        inform_sync_allow=["allowed-capability"],
    )
    _mock_actor(monkeypatch, "basic-user")
    set_value("team.selected", "inform")
    monkeypatch.setattr(
        "platform_cli.commands.sync.run_sync_actions",
        lambda **_kwargs: {
            "preview": {
                "capabilities": [
                    {
                        "capability": "example-capability",
                        "action": "repair",
                        "local_version": "1.0.0",
                        "latest_version": "1.0.0",
                        "missing_local_files": ["a.txt"],
                        "updatable_files": [],
                        "ignored_new_files": [],
                        "missing_from_latest_manifest": [],
                        "recovery_mode": "repair",
                        "recovery_detail": "",
                    }
                ]
            },
            "repairs": [
                {
                    "capability": "example-capability",
                    "action": "repair",
                    "missing_local_files": ["a.txt"],
                }
            ],
            "updates": [],
            "blocked": [],
        },
    )
    repair_called = {"count": 0}
    monkeypatch.setattr(
        "platform_cli.commands.sync.repair_content",
        lambda *_args, **_kwargs: repair_called.__setitem__("count", repair_called["count"] + 1),
    )

    res = runner.invoke(app, ["sync", "run", "--capability", "example-capability", "--auto-approve"])

    assert res.exit_code == 0
    assert repair_called["count"] == 0
    assert "blocked by team sync policy for 'inform'" in res.output


def test_sync_run_reports_bootstrap_actions_when_missing_and_install_allowed(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_team_policy(tmp_path, admin_users=["admin-user"])
    _write_access_policy(tmp_path)
    _mock_actor(monkeypatch, "admin-user")

    def _fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        asset_name = cmd[cmd.index("--pattern") + 1]
        download_dir = Path(cmd[cmd.index("--dir") + 1])
        tag = cmd[3]
        if asset_name == "content-index.json":
            payload = {
                "schema_version": "1.0",
                "generated_at": "2026-04-14T15:10:00Z",
                "capabilities": [
                    {
                        "capability": "example-capability",
                        "version": "1.0.0",
                        "provider": "github_release",
                        "source": {
                            "repo": "owner/repo",
                            "tag": tag,
                            "manifest_asset": "content-manifest.json",
                        },
                        "package_type": "file_bundle",
                        "target_type": "filesystem",
                        "allow_install_if_missing": True,
                        "policy": {
                            "allow_update_existing_files": True,
                            "allow_new_files_on_update": False,
                            "min_cli_version": "0.1.0",
                        },
                    }
                ],
            }
            (download_dir / asset_name).write_text(json.dumps(payload), encoding="utf-8")
            return
        if asset_name == "content-manifest.json":
            payload = {
                "schema_version": "1.0",
                "capability": "example-capability",
                "version": "1.0.0",
                "target_root_key": "ghdp_user_root",
                "target_subdir": "bundle",
                "files": [
                    {"asset_name": "a.txt", "target_path": "a.txt"},
                    {"asset_name": "b.txt", "target_path": "nested/b.txt"},
                ],
            }
            (download_dir / asset_name).write_text(json.dumps(payload), encoding="utf-8")
            return
        if asset_name == "a.txt":
            (download_dir / asset_name).write_text("a", encoding="utf-8")
            return
        if asset_name == "b.txt":
            (download_dir / asset_name).write_text("b", encoding="utf-8")
            return
        raise RuntimeError(f"unexpected asset {asset_name}")

    monkeypatch.setattr(release_content, "run_cmd", _fake_run)

    res = runner.invoke(app, ["sync", "run", "--capability", "example-capability", "--auto-approve"])

    assert res.exit_code == 0
    assert "example-capability: bootstrap install files a.txt, nested/b.txt" in res.output
    assert "Bootstrapped example-capability: 2 file(s)" in res.output
    assert "bootstraps applied: 1; repairs applied: 0; updates applied: 0" in res.output


def test_sync_update_remains_unrestricted_for_full_admin(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path)
    _write_team_policy(
        tmp_path,
        admin_users=["admin-user"],
        inform_sync_allow=["allowed-capability"],
    )
    _mock_actor(monkeypatch, "admin-user")
    set_value("team.selected", "inform")
    monkeypatch.setattr(
        "platform_cli.commands.sync.preview_content_updates",
        lambda **_kwargs: {
            "capabilities": [
                {
                    "capability": "example-capability",
                    "action": "update",
                    "local_version": "1.0.0",
                    "latest_version": "1.1.0",
                    "missing_local_files": [],
                    "updatable_files": ["a.txt"],
                    "ignored_new_files": [],
                    "missing_from_latest_manifest": [],
                    "recovery_detail": "",
                }
            ]
        },
    )
    called = {"count": 0}

    def _apply(*_args, **_kwargs):
        called["count"] += 1
        return {"updated_count": 1, "latest_version": "1.1.0"}

    monkeypatch.setattr("platform_cli.commands.sync.apply_content_update", _apply)

    res = runner.invoke(app, ["sync", "update", "--capability", "example-capability", "--auto-approve"])

    assert res.exit_code == 0
    assert called["count"] == 1
    assert "Updated example-capability: 1 file(s) to 1.1.0" in res.output


def test_sync_update_honors_team_policy_for_admin_assumed_team(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path, non_admin_extra_capabilities=["sync.mutate"])
    _write_team_policy(
        tmp_path,
        admin_users=["admin-user"],
        inform_sync_allow=["allowed-capability"],
    )
    _mock_actor(monkeypatch, "admin-user")
    set_value("team.selected", "platform")
    set_assumed_team("inform")
    monkeypatch.setattr(
        "platform_cli.commands.sync.preview_content_updates",
        lambda **_kwargs: {
            "capabilities": [
                {
                    "capability": "example-capability",
                    "action": "update",
                    "local_version": "1.0.0",
                    "latest_version": "1.1.0",
                    "missing_local_files": [],
                    "updatable_files": ["a.txt"],
                    "ignored_new_files": [],
                    "missing_from_latest_manifest": [],
                    "recovery_detail": "",
                }
            ]
        },
    )
    called = {"count": 0}

    def _apply(*_args, **_kwargs):
        called["count"] += 1
        return {"updated_count": 1, "latest_version": "1.1.0"}

    monkeypatch.setattr("platform_cli.commands.sync.apply_content_update", _apply)

    try:
        res = runner.invoke(app, ["sync", "update", "--capability", "example-capability", "--auto-approve"])

        assert res.exit_code == 0
        assert called["count"] == 0
        assert "blocked by team sync policy for 'inform'" in res.output
        assert "Update blocked by team sync restrictions." in res.output
    finally:
        set_assumed_team("")


def test_sync_run_blocks_shared_root_capability_when_only_untracked_files_exist(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_team_policy(tmp_path, admin_users=["admin-user"])
    _write_access_policy(tmp_path)
    _mock_actor(monkeypatch, "admin-user")

    shared_root = tmp_path / ".ghdp"
    shared_root.mkdir(parents=True, exist_ok=True)
    (shared_root / "manual.txt").write_text("manual", encoding="utf-8")

    def _fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        asset_name = cmd[cmd.index("--pattern") + 1]
        download_dir = Path(cmd[cmd.index("--dir") + 1])
        tag = cmd[3]
        if asset_name == "content-index.json":
            payload = {
                "schema_version": "1.0",
                "generated_at": "2026-04-14T15:10:00Z",
                "capabilities": [
                    {
                        "capability": "shared-capability",
                        "version": "1.0.0",
                        "provider": "github_release",
                        "source": {
                            "repo": "owner/repo",
                            "tag": tag,
                            "manifest_asset": "content-manifest.json",
                        },
                        "package_type": "file_bundle",
                        "target_type": "filesystem",
                        "recovery_hint": "Run 'ghdp tableau init' to bootstrap Tableau Athena drivers.",
                        "policy": {
                            "allow_update_existing_files": True,
                            "allow_new_files_on_update": False,
                            "min_cli_version": "0.1.0",
                        },
                    }
                ],
            }
            (download_dir / asset_name).write_text(json.dumps(payload), encoding="utf-8")
            return
        if asset_name == "content-manifest.json":
            payload = {
                "schema_version": "1.0",
                "capability": "shared-capability",
                "version": "1.0.0",
                "target_root_key": "ghdp_user_root",
                "target_subdir": ".",
                "files": [
                    {"asset_name": "shared-a.txt", "target_path": "shared-a.txt"},
                    {"asset_name": "shared-b.txt", "target_path": "shared-b.txt"},
                ],
            }
            (download_dir / asset_name).write_text(json.dumps(payload), encoding="utf-8")
            return
        if asset_name == "shared-a.txt":
            (download_dir / asset_name).write_text("a", encoding="utf-8")
            return
        if asset_name == "shared-b.txt":
            (download_dir / asset_name).write_text("b", encoding="utf-8")
            return
        raise RuntimeError(f"unexpected asset {asset_name}")

    monkeypatch.setattr(release_content, "run_cmd", _fake_run)

    res = runner.invoke(app, ["sync", "run", "--capability", "shared-capability", "--auto-approve"])

    assert res.exit_code == 0
    assert "shared-capability: blocked" in res.output
    assert "install-if-missing recovery is not allowed for this capability" in res.output
    assert "next step: Run 'ghdp tableau init' to bootstrap Tableau Athena drivers." in res.output
    assert "Blocked capabilities: 1" in res.output
    assert not (shared_root / "shared-a.txt").exists()
    assert not (shared_root / "shared-b.txt").exists()


def test_admin_create_token_copies_to_clipboard_by_default(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_team_policy(tmp_path, admin_users=["admin-user"])
    _write_access_policy(tmp_path)
    _bootstrap_signer(tmp_path)
    _mock_actor(monkeypatch, "admin-user")
    monkeypatch.setattr("platform_cli.commands.admin.copy_text", lambda value: (True, "clip"))

    res = runner.invoke(
        app,
        [
            "admin",
            "create-token",
            "--for-user",
            "basic-user",
            "--capability",
            "team.switch",
            "--ttl-minutes",
            "30",
        ],
    )

    assert res.exit_code == 0
    assert "scope: user" in res.output
    assert "for_user: basic-user" in res.output
    assert "clipboard: copied via clip" in res.output
    assert "token: hidden" in res.output


def test_admin_create_token_prints_raw_token_when_clipboard_unavailable(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_team_policy(tmp_path, admin_users=["admin-user"])
    _write_access_policy(tmp_path)
    _bootstrap_signer(tmp_path)
    _mock_actor(monkeypatch, "admin-user")
    monkeypatch.setattr("platform_cli.commands.admin.copy_text", lambda value: (False, "clip_missing"))

    res = runner.invoke(
        app,
        [
            "admin",
            "create-token",
            "--for-user",
            "basic-user",
            "--capability",
            "team.switch",
            "--ttl-minutes",
            "30",
        ],
    )

    assert res.exit_code == 0
    assert "scope: user" in res.output
    assert "clipboard: unavailable (clip_missing)" in res.output
    assert "token: " in res.output


def test_admin_create_token_requires_for_user_or_team(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_team_policy(tmp_path, admin_users=["admin-user"])
    _write_access_policy(tmp_path)
    _bootstrap_signer(tmp_path)
    _mock_actor(monkeypatch, "admin-user")

    res = runner.invoke(
        app,
        [
            "admin",
            "create-token",
            "--capability",
            "tools.install",
        ],
    )

    assert res.exit_code != 0
    assert "Provide at least one of --for-user or --team" in res.output


def test_admin_create_token_team_only_uses_scope_and_warning(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_team_policy(tmp_path, admin_users=["admin-user"])
    _write_access_policy(tmp_path)
    _bootstrap_signer(tmp_path)
    _mock_actor(monkeypatch, "admin-user")
    monkeypatch.setattr("platform_cli.commands.admin.copy_text", lambda value: (True, "clip"))

    res = runner.invoke(
        app,
        [
            "admin",
            "create-token",
            "--team",
            "data_analyst",
            "--capability",
            "tools.install",
        ],
    )

    assert res.exit_code == 0
    assert "scope: team" in res.output
    assert "for_user: (not restricted)" in res.output
    assert "warning: team-only tokens can be reused" in res.output
    assert "ttl_minutes: 15" in res.output


def test_admin_create_token_user_team_scope(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_team_policy(tmp_path, admin_users=["admin-user"])
    _write_access_policy(tmp_path)
    _bootstrap_signer(tmp_path)
    _mock_actor(monkeypatch, "admin-user")
    monkeypatch.setattr("platform_cli.commands.admin.copy_text", lambda value: (True, "clip"))

    res = runner.invoke(
        app,
        [
            "admin",
            "create-token",
            "--for-user",
            "basic-user",
            "--team",
            "data_analyst",
            "--capability",
            "tools.install",
            "--show-token",
        ],
    )

    assert res.exit_code == 0
    assert "scope: user_team" in res.output
    assert "for_user: basic-user" in res.output
    assert "team: data_analyst" in res.output


def test_admin_create_token_rejects_team_only_capability_outside_allowlist(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_team_policy(tmp_path, admin_users=["admin-user"])
    _write_access_policy(tmp_path)
    _bootstrap_signer(tmp_path)
    _mock_actor(monkeypatch, "admin-user")

    res = runner.invoke(
        app,
        [
            "admin",
            "create-token",
            "--team",
            "data_analyst",
            "--capability",
            "publish.execute",
        ],
    )

    assert res.exit_code != 0
    assert "Unsupported token capabilities for scope 'team'" in str(res.exception)


def test_admin_create_token_enforces_team_only_ttl_cap(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_team_policy(tmp_path, admin_users=["admin-user"])
    _write_access_policy(tmp_path)
    _bootstrap_signer(tmp_path)
    _mock_actor(monkeypatch, "admin-user")

    res = runner.invoke(
        app,
        [
            "admin",
            "create-token",
            "--team",
            "data_analyst",
            "--capability",
            "tools.install",
            "--ttl-minutes",
            "31",
        ],
    )

    assert res.exit_code != 0
    assert "Token ttl must be between 1 and 30 minutes." in str(res.exception)


def test_admin_signer_setup_creates_local_signer_material(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_team_policy(tmp_path, admin_users=["admin-user"])
    _write_access_policy(tmp_path)
    _mock_actor(monkeypatch, "admin-user")

    res = runner.invoke(
        app,
        [
            "admin",
            "signer",
            "setup",
            "--key-id",
            "admin-local",
            "--overwrite",
        ],
    )

    assert res.exit_code == 0
    assert "Admin signer setup complete." in res.output
    assert "key_id: admin-local" in res.output


def test_access_token_activate_persists_token_and_status(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path)
    _bootstrap_signer(tmp_path)
    _mock_actor(monkeypatch, "inform-user")
    token = access.issue_token(
        target_actor="inform-user",
        capabilities=["tools.install"],
        ttl_minutes=30,
        team="inform",
    )

    activate = runner.invoke(app, ["access", "token", "activate", "--token", token])
    status = runner.invoke(app, ["access", "token", "status"])

    assert activate.exit_code == 0
    assert "Access token activated." in activate.output
    assert "scope: user_team" in activate.output
    assert "team: inform" in activate.output
    assert get_active_token() == token
    assert status.exit_code == 0
    assert "token_status: active" in status.output
    assert "token_source: state:access_session" in status.output
    assert "token_scope: user_team" in status.output


def test_admin_token_activate_rejects_actor_mismatch(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path)
    _bootstrap_signer(tmp_path)
    _mock_actor(monkeypatch, "inform-user")
    token = access.issue_token(
        target_actor="someone-else",
        capabilities=["tools.install"],
        ttl_minutes=30,
        team="inform",
    )

    res = runner.invoke(app, ["access", "token", "activate", "--token", token])

    assert res.exit_code != 0
    assert "not 'inform-user'" in str(res.exception)


def test_access_token_activate_team_only_succeeds_without_actor_binding(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path)
    _bootstrap_signer(tmp_path)
    _mock_actor(monkeypatch, "inform-user")
    token = access.issue_token(
        target_actor="",
        capabilities=["tools.install"],
        ttl_minutes=15,
        team="inform",
    )

    activate = runner.invoke(app, ["access", "token", "activate", "--token", token])
    status = runner.invoke(app, ["access", "token", "status"])

    assert activate.exit_code == 0
    assert "scope: team" in activate.output
    assert "actor: (not restricted)" in activate.output
    assert "team: inform" in activate.output
    assert status.exit_code == 0
    assert "token_scope: team" in status.output


def test_access_token_clear_removes_locally_stored_token(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path)
    _bootstrap_signer(tmp_path)
    _mock_actor(monkeypatch, "inform-user")
    token = access.issue_token(
        target_actor="inform-user",
        capabilities=["tools.install"],
        ttl_minutes=30,
        team="inform",
    )
    runner.invoke(app, ["access", "token", "activate", "--token", token])

    res = runner.invoke(app, ["access", "token", "clear"])

    assert res.exit_code == 0
    assert "Cleared locally stored access token." in res.output
    assert get_active_token() == ""


def test_access_token_clear_does_not_warn_about_shell_env_precedence(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path)
    _bootstrap_signer(tmp_path)
    _mock_actor(monkeypatch, "inform-user")
    token = access.issue_token(
        target_actor="inform-user",
        capabilities=["tools.install"],
        ttl_minutes=30,
        team="inform",
    )
    runner.invoke(app, ["access", "token", "activate", "--token", token])

    res = runner.invoke(app, ["access", "token", "clear"])

    assert res.exit_code == 0
    assert "will continue to take precedence" not in res.output


def test_access_session_and_reset_are_local_state_only(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_team_policy(tmp_path, admin_users=["gh-mshyam"])
    _write_access_policy(tmp_path)
    _bootstrap_signer(tmp_path)
    _mock_actor(monkeypatch, "gh-mshyam")
    monkeypatch.setattr(platform_cli, "__channel__", "beta")
    set_value("team.selected", "platform")
    set_remembered_actor("inform-user")
    token = access.issue_token(
        target_actor="gh-mshyam",
        capabilities=["tools.install"],
        ttl_minutes=30,
        team="platform",
    )
    set_active_token(token)

    session_before = runner.invoke(app, ["access", "session"])
    reset = runner.invoke(app, ["access", "reset"])
    session_after = runner.invoke(app, ["access", "session"])

    assert session_before.exit_code == 0
    assert "remembered_actor: gh-mshyam" in session_before.output
    assert "active_token_present: yes" in session_before.output
    assert "effective_team: platform" in session_before.output
    assert reset.exit_code == 0
    assert "Cleared local access-session state." in reset.output
    assert session_after.exit_code == 0
    assert "remembered_actor: gh-mshyam" in session_after.output
    assert "active_token_present: no" in session_after.output
    assert get_access_session().get("remembered_actor") == "gh-mshyam"
    assert get_access_session().get("active_token") == ""
    assert get_access_session().get("assumed_team") == ""
    assert get_value("team.selected") == "platform"


def test_admin_assume_team_suppresses_admin_privileges_until_return(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_team_policy(tmp_path, admin_users=["gh-mshyam"])
    _mock_actor(monkeypatch, "gh-mshyam")
    monkeypatch.setattr(platform_cli, "__channel__", "beta")

    assume = runner.invoke(app, ["admin", "assume", "--team", "default"])
    create_token = runner.invoke(
        app,
        [
            "admin",
            "create-token",
            "--for-user",
            "inform-user",
            "--capability",
            "tools.install",
        ],
    )

    assert assume.exit_code == 0
    assert "Admin assume mode enabled for: default" in assume.output
    assert get_assumed_team() == "default"
    assert create_token.exit_code != 0
    assert "ghdp admin return" in str(create_token.exception)
    ret = runner.invoke(app, ["admin", "return"])
    assert ret.exit_code == 0
    assert "Admin mode restored." in ret.output
    assert get_assumed_team() == ""


def test_admin_view_shows_assumed_team_without_changing_identity(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_team_policy(tmp_path, admin_users=["gh-mshyam"])
    _mock_actor(monkeypatch, "gh-mshyam")
    monkeypatch.setattr(platform_cli, "__channel__", "beta")

    runner.invoke(app, ["admin", "assume", "--team", "default"])
    res = runner.invoke(app, ["access", "view"])

    assert res.exit_code == 0
    assert "actor: gh-mshyam" in res.output
    assert "base_persona: admin" in res.output
    assert "persona: non-admin" in res.output
    assert "active_mode: assumed-team" in res.output
    assert "effective_team: default" in res.output


def test_team_current_shows_active_session_team_from_token(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path)
    _bootstrap_signer(tmp_path)
    _mock_actor(monkeypatch, "inform-user")
    monkeypatch.setattr("platform_cli.commands.team.ensure_team_toolset_synced", lambda: None)
    set_value("team.selected", "platform")
    token = access.issue_token(
        target_actor="inform-user",
        capabilities=["tools.install"],
        ttl_minutes=30,
        team="inform",
    )

    runner.invoke(app, ["access", "token", "activate", "--token", token])
    res = runner.invoke(app, ["team", "current"])

    assert res.exit_code == 0
    assert "inform (active session)" in res.output


def test_valid_token_allows_non_admin_team_switch(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _mock_actor(monkeypatch, "basic-user")
    _write_access_policy(tmp_path)
    _bootstrap_signer(tmp_path)
    monkeypatch.setattr("platform_cli.commands.team.ensure_team_toolset_synced", lambda: None)
    set_value("team.selected", "default")
    token = access.issue_token(target_actor="basic-user", capabilities=["team.switch"], ttl_minutes=30, team="data_analyst")
    set_active_token(token)

    res = runner.invoke(app, ["team", "use", "--team", "data_analyst"])

    assert res.exit_code == 0
    assert "Saved team: data_analyst" in res.output


def test_non_admin_repo_fix_is_denied(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _mock_actor(monkeypatch, "basic-user")

    res = runner.invoke(app, ["repo", "fix"])

    assert res.exit_code != 0
    assert "repo.fix" in str(res.exception)


def test_non_admin_has_tools_uninstall_capability(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _mock_actor(monkeypatch, "basic-user")

    access.ensure_capability("tools.uninstall", command_name="tools uninstall")


def test_non_admin_publish_execute_is_allowed(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _mock_actor(monkeypatch, "basic-user")

    access.ensure_capability("publish.execute", command_name="publish")


def test_data_analyst_publish_execute_is_denied(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    policy_dir = tmp_path / ".ghdp" / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "team-policy.managed.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "managed_by": "ghdp",
                "teams": {
                    "data_analyst": {
                        "allow_capabilities": [],
                        "deny_capabilities": ["publish.execute"],
                    }
                },
                "admin_users": ["gh-mshyam"],
            }
        ),
        encoding="utf-8",
    )
    _mock_actor(monkeypatch, "basic-user")
    set_value("team.selected", "data_analyst")

    with pytest.raises(Exception) as exc:
        access.ensure_capability("publish.execute", command_name="publish")

    assert "publish.execute" in str(exc.value)


def test_inform_team_cannot_install_without_token(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_team_policy(tmp_path, admin_users=["gh-mshyam"], inform_denies=["tools.install"])
    _mock_actor(monkeypatch, "inform-user")

    res = runner.invoke(app, ["tools", "install", "--team", "inform", "--tool", "codex", "--dry-run"])

    assert res.exit_code != 0
    assert "tools.install" in str(res.exception)


def test_inform_team_cannot_view_usage_without_token(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_team_policy(tmp_path, admin_users=["gh-mshyam"], inform_denies=["usage.read"])
    _mock_actor(monkeypatch, "inform-user")
    set_value("team.selected", "inform")

    res = runner.invoke(app, ["usage"])

    assert res.exit_code != 0
    assert "usage.read" in str(res.exception)


def test_inform_team_can_view_usage_with_admin_token_then_loses_it_after_expiry(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_team_policy(tmp_path, admin_users=["gh-mshyam"], inform_denies=["usage.read"])
    _write_access_policy(tmp_path)
    _bootstrap_signer(tmp_path)
    _mock_actor(monkeypatch, "inform-user")
    set_value("team.selected", "inform")

    token = access.issue_token(
        target_actor="inform-user",
        capabilities=["usage.read"],
        ttl_minutes=30,
        team="inform",
    )
    set_active_token(token)

    allowed = runner.invoke(app, ["usage"])
    assert allowed.exit_code == 0
    assert "GHDP" in allowed.output

    original_now = access.time.time
    monkeypatch.setattr(access.time, "time", lambda: original_now() + (31 * 60))

    expired = runner.invoke(app, ["usage"])
    assert expired.exit_code != 0
    assert "usage.read" in str(expired.exception)


def test_restricted_team_can_install_with_admin_token_then_loses_it_after_expiry(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    policy_dir = tmp_path / ".ghdp" / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "team-policy.managed.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "managed_by": "ghdp",
                "teams": {
                    "data_analyst": {
                        "allow_capabilities": [],
                        "deny_capabilities": ["tools.install", "config.user_safe_write"],
                    }
                },
                "admin_users": ["gh-mshyam"],
            }
        ),
        encoding="utf-8",
    )
    _write_access_policy(tmp_path)
    _bootstrap_signer(tmp_path)
    _mock_actor(monkeypatch, "inform-user")
    set_value("team.selected", "data_analyst")
    monkeypatch.setattr("platform_cli.commands.tools.detect_tool", lambda *_args, **_kwargs: (False, ""))
    monkeypatch.setattr(
        "platform_cli.commands.tools.install_tool",
        lambda *_args, **_kwargs: SimpleNamespace(
            tool_name="codex",
            display_name="Codex",
            status="ready",
            short_status="Ready",
            next_action="",
            detail_hint="",
        ),
    )
    monkeypatch.setattr(access.time, "time", lambda: 1_000)
    token = access.issue_token(
        target_actor="inform-user",
        capabilities=["tools.install", "config.user_safe_write"],
        ttl_minutes=5,
        team="data_analyst",
    )
    set_active_token(token)

    install_res = runner.invoke(app, ["tools", "install", "--team", "data_analyst", "--tool", "codex", "--dry-run"])
    config_res = runner.invoke(app, ["config", "precommit", "--mode", "warn"])

    assert install_res.exit_code == 0
    assert "install finished" in install_res.output
    assert config_res.exit_code == 0
    assert "Pre-commit mode set to" in config_res.output

    monkeypatch.setattr(access.time, "time", lambda: 1_301)

    expired_install = runner.invoke(app, ["tools", "install", "--team", "data_analyst", "--tool", "codex", "--dry-run"])
    expired_config = runner.invoke(app, ["config", "precommit", "--mode", "warn"])

    assert expired_install.exit_code != 0
    assert "tools.install" in str(expired_install.exception)
    assert expired_config.exit_code != 0
    assert "config.user_safe_write" in str(expired_config.exception)


def test_token_activation_enables_restricted_team_without_manual_switch(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    policy_dir = tmp_path / ".ghdp" / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "team-policy.managed.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "managed_by": "ghdp",
                "teams": {
                    "data_analyst": {
                        "allow_capabilities": [],
                        "deny_capabilities": ["tools.install"],
                    }
                },
                "admin_users": ["gh-mshyam"],
            }
        ),
        encoding="utf-8",
    )
    _write_access_policy(tmp_path)
    _bootstrap_signer(tmp_path)
    _mock_actor(monkeypatch, "inform-user")
    set_value("team.selected", "platform")
    monkeypatch.setattr("platform_cli.commands.tools.detect_tool", lambda *_args, **_kwargs: (False, ""))
    monkeypatch.setattr(
        "platform_cli.commands.tools.install_tool",
        lambda *_args, **_kwargs: SimpleNamespace(
            tool_name="codex",
            display_name="Codex",
            status="ready",
            short_status="Ready",
            next_action="",
            detail_hint="",
        ),
    )
    token = access.issue_token(
        target_actor="inform-user",
        capabilities=["tools.install"],
        ttl_minutes=30,
        team="data_analyst",
    )

    denied = runner.invoke(app, ["tools", "install", "--team", "data_analyst", "--tool", "codex", "--dry-run"])
    activated = runner.invoke(app, ["access", "token", "activate", "--token", token])
    allowed = runner.invoke(app, ["tools", "install", "--team", "data_analyst", "--tool", "codex", "--dry-run"])

    assert denied.exit_code != 0
    assert "team.switch" in str(denied.exception) or "tools.install" in str(denied.exception)
    assert activated.exit_code == 0
    assert allowed.exit_code == 0
    assert "install finished" in allowed.output


def test_admin_hidden_token_aliases_remain_compatible(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path)
    _bootstrap_signer(tmp_path)
    _mock_actor(monkeypatch, "inform-user")
    token = access.issue_token(
        target_actor="inform-user",
        capabilities=["tools.install"],
        ttl_minutes=30,
        team="inform",
    )

    activate = runner.invoke(app, ["admin", "token", "activate", "--token", token])
    status = runner.invoke(app, ["admin", "token", "status"])
    clear = runner.invoke(app, ["admin", "token", "clear"])

    assert activate.exit_code == 0
    assert "Access token activated." in activate.output
    assert status.exit_code == 0
    assert "token_status: active" in status.output
    assert clear.exit_code == 0
    assert "Cleared locally stored access token." in clear.output
