from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import platform_cli
from typer.testing import CliRunner

from platform_cli.cli import app
from platform_cli.core import access
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.release_policy import evaluate_release_gate
from platform_cli.manifests.load import load_release_policy
from platform_cli.state.access_session import set_active_token

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


def _write_team_policy(home: Path, *, admin_users: list[str]) -> None:
    policy_dir = home / ".ghdp" / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "managed_by": "ghdp",
        "admin_users": admin_users,
    }
    (policy_dir / "team-policy.managed.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_access_policy(home: Path) -> None:
    policy_dir = home / ".ghdp" / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "personas": {
            "non_admin": {
                "capabilities": [
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
                    "local.lifecycle",
                    "team.switch",
                ]
            },
            "admin": {
                "capabilities": [
                    "admin.view",
                    "team.initial_select",
                    "config.user_safe_write",
                    "tools.install",
                    "tools.read",
                    "repo.read",
                    "sync.read",
                    "team.switch",
                    "config.admin_write",
                    "tools.uninstall",
                    "repo.fix",
                    "repo.accept",
                    "sync.mutate",
                    "local.lifecycle",
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
            ],
            "default_ttl_minutes": 60,
            "max_ttl_minutes": 480,
            "signing": {
                "format": "ghdp.sig.v2",
                "algorithm": "ed25519",
                "active_key_id": "",
                "verification_keys": [],
            },
            "capability_catalog": {},
        },
        "config_rules": {},
        "help": {"support_contact": "platform team"},
    }
    (policy_dir / "access_policy.json").write_text(json.dumps(payload), encoding="utf-8")


def _bootstrap_signer(home: Path) -> None:
    access.setup_local_signer(key_id="test-admin-key", overwrite=True, update_local_policy=True)


def test_load_release_policy_prefers_synced_managed_policy_name(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    policy_dir = tmp_path / ".ghdp" / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "release-policy.managed.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "default_channel": "stable",
                "channels": {"stable": {"blocked_commands": {}}, "prerelease": {"blocked_commands": {}}},
            }
        ),
        encoding="utf-8",
    )

    policy, source = load_release_policy()

    assert policy["schema_version"] == "1.0"
    assert "release-policy.managed.json" in source


def test_packaged_release_policy_no_longer_blocks_build_or_deploy() -> None:
    policy_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "platform_cli"
        / "resources"
        / "policy"
        / "release_policy.json"
    )
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    blocked = payload["channels"]["stable"]["blocked_commands"]

    assert "build" not in blocked
    assert "deploy" not in blocked
    assert "repo verify" in blocked


def test_packaged_release_policy_falls_back_to_allow_build_and_deploy_runtime(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    monkeypatch.setattr(platform_cli, "__channel__", "stable")

    build_decision = evaluate_release_gate("build")
    deploy_decision = evaluate_release_gate("deploy")

    assert build_decision.status == "allowed"
    assert deploy_decision.status == "allowed"
    assert build_decision.policy_source.startswith("pkg:")


def test_stable_blocks_access_inspect_without_preview_access(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _mock_actor(monkeypatch, "basic-user")
    monkeypatch.setattr(platform_cli, "__channel__", "stable")

    res = runner.invoke(app, ["access", "inspect"])

    assert res.exit_code != 0
    assert "platform.internal" in str(res.exception)


def test_prerelease_allows_access_inspect(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_team_policy(tmp_path, admin_users=["gh-mshyam"])
    _write_access_policy(tmp_path)
    _mock_actor(monkeypatch, "gh-mshyam")
    monkeypatch.setattr(platform_cli, "__channel__", "beta")

    res = runner.invoke(app, ["access", "inspect"])

    assert res.exit_code == 0
    assert "remembered_actor:" in res.output


def test_stable_blocks_admin_access_inspect_even_with_capability(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_team_policy(tmp_path, admin_users=["gh-mshyam"])
    _write_access_policy(tmp_path)
    _mock_actor(monkeypatch, "gh-mshyam")
    monkeypatch.setattr(platform_cli, "__channel__", "stable")

    res = runner.invoke(app, ["access", "inspect"])

    assert res.exit_code != 0
    assert "not available in stable GHDP releases" in str(res.exception)


def test_prerelease_still_blocks_non_admin_access_inspect(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _mock_actor(monkeypatch, "basic-user")
    monkeypatch.setattr(platform_cli, "__channel__", "beta")

    res = runner.invoke(app, ["access", "inspect"])

    assert res.exit_code != 0
    assert "platform.internal" in str(res.exception)


def test_access_view_shows_release_channel_and_policy_source(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _mock_actor(monkeypatch, "basic-user")
    monkeypatch.setattr(platform_cli, "__channel__", "beta")

    res = runner.invoke(app, ["access", "status"])

    assert res.exit_code == 0
    assert "release_channel: prerelease" in res.output
    assert "release_policy_source:" in res.output
