from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from platform_cli.core import access
from platform_cli.core.config import set_value
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.errors import PlatformError
from platform_cli.core.team_context import set_selected_team
from platform_cli.manifests.load import load_access_policy
from platform_cli.state.access_session import (
    get_remembered_actor,
    set_active_token,
    set_assumed_team,
    set_remembered_actor,
)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def _write_access_policy(
    home,
) -> None:
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
                    "publish.execute",
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
                    "usage.read",
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
            "capability_catalog": {
                "local.lifecycle": {
                    "key": "local_lifecycle",
                    "label": "Local lifecycle",
                    "description": "Allow local init, build, and Terraform deployment helper flows for data-product repositories.",
                    "group": "Local",
                    "selectable_by_scope": [],
                    "order": 100,
                }
            },
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


def _bootstrap_signer(home) -> None:
    access.setup_local_signer(key_id="test-admin-key", overwrite=True, update_local_policy=True)


def test_resolve_actor_uses_remembered_actor_when_gh_is_unavailable(isolated_home, monkeypatch) -> None:
    set_remembered_actor("remembered-user")
    monkeypatch.setattr(
        access,
        "run_cmd",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )

    actor = access.resolve_actor()

    assert actor.login == "remembered-user"
    assert actor.status == "remembered"
    assert actor.source == "state:remembered_actor"


def test_resolve_actor_prefers_gh_over_remembered_actor(isolated_home, monkeypatch) -> None:
    set_remembered_actor("remembered-user")
    monkeypatch.setattr(
        access,
        "run_cmd",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="gh-user", stderr=""),
    )

    actor = access.resolve_actor()

    assert actor.login == "gh-user"
    assert actor.status == "resolved"
    assert actor.source == "gh"
    assert get_remembered_actor() == "gh-user"


def test_resolve_actor_prompts_and_remembers_when_requested(isolated_home, monkeypatch) -> None:
    cli_ctx.non_interactive = False
    monkeypatch.setattr(
        access,
        "run_cmd",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    monkeypatch.setattr(access.typer, "prompt", lambda *_args, **_kwargs: "prompted-user")

    actor = access.resolve_actor(interactive=True)

    assert actor.login == "prompted-user"
    assert actor.status == "prompted"
    assert actor.source == "prompt"
    assert get_remembered_actor() == "prompted-user"


def test_ensure_capability_requires_identity_for_privileged_command(isolated_home, monkeypatch) -> None:
    cli_ctx.non_interactive = True
    monkeypatch.setattr(access, "run_cmd", lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""))

    with pytest.raises(PlatformError) as exc:
        access.ensure_capability("release.manage", command_name="release plan-binaries")

    assert exc.value.code == "E_ACTOR_IDENTITY_REQUIRED"


def test_ensure_capability_does_not_prompt_when_non_admin_capability_is_already_allowed(isolated_home, monkeypatch) -> None:
    _write_access_policy(isolated_home)
    cli_ctx.non_interactive = False
    monkeypatch.setattr(access, "run_cmd", lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""))
    monkeypatch.setattr(access.typer, "prompt", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected prompt")))

    access.ensure_capability("team.switch", team="data_platform", command_name="team use")


def test_resolve_access_context_only_resolves_actor_once(isolated_home, monkeypatch) -> None:
    _write_access_policy(isolated_home)
    set_selected_team("data_platform")
    calls: list[bool] = []

    monkeypatch.setattr(
        access,
        "resolve_actor",
        lambda **_kwargs: calls.append(True) or access.ActorResolution("gh-user", "prompted", "prompt"),
    )
    monkeypatch.setattr(
        access,
        "release_runtime",
        lambda: SimpleNamespace(channel="stable", policy_source="pkg:release_policy"),
    )

    ctx = access.resolve_access_context(interactive=True)

    assert ctx.actor == "gh-user"
    assert ctx.effective_team == "data_platform"
    assert calls == [True]


def test_issue_and_evaluate_token_round_trip(isolated_home, monkeypatch) -> None:
    _write_access_policy(isolated_home)
    _bootstrap_signer(isolated_home)

    token = access.issue_token(
        target_actor="octocat",
        capabilities=["team.switch", "repo.fix"],
        ttl_minutes=30,
        team="platform",
    )

    evaluated = access.evaluate_token(token, actor="octocat", team="platform")

    assert evaluated.status == "active"
    assert evaluated.claims is not None
    assert set(evaluated.claims.capabilities) == {"team.switch", "repo.fix"}
    assert evaluated.claims.team == "platform"
    assert evaluated.claims.scope == "user_team"


def test_issue_and_evaluate_team_only_token_round_trip(isolated_home, monkeypatch) -> None:
    _write_access_policy(isolated_home)
    _bootstrap_signer(isolated_home)

    token = access.issue_token(
        target_actor="",
        capabilities=["tools.install"],
        ttl_minutes=15,
        team="inform",
    )

    evaluated = access.evaluate_token(token, actor="another-user", team="inform")

    assert evaluated.status == "active"
    assert evaluated.claims is not None
    assert evaluated.claims.actor == ""
    assert evaluated.claims.team == "inform"
    assert evaluated.claims.scope == "team"


def test_evaluate_team_only_token_rejects_wrong_team(isolated_home, monkeypatch) -> None:
    _write_access_policy(isolated_home)
    _bootstrap_signer(isolated_home)
    token = access.issue_token(
        target_actor="",
        capabilities=["tools.install"],
        ttl_minutes=15,
        team="inform",
    )

    evaluated = access.evaluate_token(token, actor="any-user", team="platform")

    assert evaluated.status == "team_mismatch"
    assert evaluated.claims is not None
    assert evaluated.claims.scope == "team"


def test_evaluate_user_team_token_requires_actor_and_team(isolated_home, monkeypatch) -> None:
    _write_access_policy(isolated_home)
    _bootstrap_signer(isolated_home)
    token = access.issue_token(
        target_actor="octocat",
        capabilities=["tools.install"],
        ttl_minutes=30,
        team="inform",
    )

    actor_mismatch = access.evaluate_token(token, actor="someone-else", team="inform")
    team_mismatch = access.evaluate_token(token, actor="octocat", team="platform")

    assert actor_mismatch.status == "actor_mismatch"
    assert team_mismatch.status == "team_mismatch"


def test_issue_token_uses_legacy_allowlist_fallback_for_missing_scope(isolated_home, monkeypatch) -> None:
    _write_access_policy(isolated_home)
    policy_path = isolated_home / ".ghdp" / "policies" / "access_policy.json"
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    del payload["token"]["allowed_capabilities_by_scope"]["user_team"]
    policy_path.write_text(json.dumps(payload), encoding="utf-8")
    _bootstrap_signer(isolated_home)

    token = access.issue_token(
        target_actor="octocat",
        capabilities=["team.switch"],
        ttl_minutes=30,
        team="platform",
    )

    evaluated = access.evaluate_token(token, actor="octocat", team="platform")

    assert evaluated.status == "active"
    assert evaluated.claims is not None
    assert evaluated.claims.scope == "user_team"


def test_issue_token_rejects_local_lifecycle_when_capability_is_not_token_grantable(isolated_home, monkeypatch) -> None:
    _write_access_policy(isolated_home)
    _bootstrap_signer(isolated_home)

    with pytest.raises(PlatformError) as exc:
        access.issue_token(
            target_actor="octocat",
            capabilities=["local.lifecycle"],
            ttl_minutes=30,
        )

    assert exc.value.code == "E_ADMIN_TOKEN_CAPABILITY_INVALID"


def test_issue_token_enforces_team_only_ttl_cap(isolated_home, monkeypatch) -> None:
    _write_access_policy(isolated_home)
    _bootstrap_signer(isolated_home)

    with pytest.raises(PlatformError) as exc:
        access.issue_token(
            target_actor="",
            capabilities=["tools.install"],
            ttl_minutes=31,
            team="inform",
        )

    assert exc.value.code == "E_ADMIN_TOKEN_TTL_INVALID"


def test_evaluate_legacy_team_only_token_requires_reissue(isolated_home, monkeypatch) -> None:
    _write_access_policy(isolated_home)
    monkeypatch.setattr(access.time, "time", lambda: 1_000)
    payload = {
        "v": 1,
        "actor": "",
        "capabilities": ["tools.install"],
        "team": "inform",
        "issued_at": 1_000,
        "expires_at": 1_300,
    }
    payload_raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    token = f"{access._b64url_encode(payload_raw)}.{access._b64url_encode(b'legacy-signature')}"

    evaluated = access.evaluate_token(token, actor="any-user", team="inform")

    assert evaluated.status == "legacy_reissue_required"
    assert "fresh token" in evaluated.message


def test_setup_local_signer_writes_local_policy_override(isolated_home, monkeypatch) -> None:
    _write_access_policy(isolated_home)
    created = access.setup_local_signer(key_id="local-test-key", overwrite=True, update_local_policy=True)
    policy, source = load_access_policy()

    assert created["key_id"] == "local-test-key"
    assert "access_policy.json" in source
    assert policy["token"]["signing"]["active_key_id"] == "local-test-key"
    assert policy["token"]["signing"]["verification_keys"]


def test_evaluate_token_rejects_expired_token(isolated_home, monkeypatch) -> None:
    _write_access_policy(isolated_home)
    _bootstrap_signer(isolated_home)
    monkeypatch.setattr(access.time, "time", lambda: 1_000)
    token = access.issue_token(
        target_actor="octocat",
        capabilities=["team.switch"],
        ttl_minutes=1,
    )

    monkeypatch.setattr(access.time, "time", lambda: 1_500)
    evaluated = access.evaluate_token(token, actor="octocat")

    assert evaluated.status == "expired"
    assert evaluated.claims is not None
    assert evaluated.claims.actor == "octocat"
    assert "team.switch" in evaluated.claims.capabilities


def test_set_selected_team_allows_non_admin_switch_with_packaged_policy(isolated_home, monkeypatch) -> None:
    monkeypatch.setattr(access, "run_cmd", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="basic-user", stderr=""))
    set_value("team.selected", "default")

    set_selected_team("platform")

    assert access.resolve_access_context().selected_team == "platform"


def test_set_selected_team_allows_admin_switch(isolated_home, monkeypatch) -> None:
    policy_dir = isolated_home / ".ghdp" / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "team-policy.managed.json").write_text(
        json.dumps({"schema_version": "1.0", "managed_by": "ghdp", "admin_users": ["admin-user"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(access, "run_cmd", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="admin-user", stderr=""))
    set_value("team.selected", "default")

    set_selected_team("platform")

    assert access.resolve_access_context().selected_team == "platform"


def test_set_selected_team_allows_synced_admin_switch(isolated_home, monkeypatch) -> None:
    policy_dir = isolated_home / ".ghdp" / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "team-policy.managed.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "managed_by": "ghdp",
                "admin_users": ["admin-user"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(access, "run_cmd", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="admin-user", stderr=""))
    set_value("team.selected", "default")

    set_selected_team("platform")

    ctx = access.resolve_access_context()
    assert ctx.selected_team == "platform"
    assert ctx.persona == "admin"
    assert ctx.base_persona == "admin"
    assert "team-policy.managed.json" in ctx.admin_users_source


def test_inform_team_denies_capabilities_without_token(isolated_home, monkeypatch) -> None:
    policy_dir = isolated_home / ".ghdp" / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "team-policy.managed.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "managed_by": "ghdp",
                "teams": {
                    "inform": {
                        "allow_capabilities": [],
                        "deny_capabilities": ["tools.install", "config.user_safe_write"],
                    }
                },
                "admin_users": ["gh-mshyam"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(access, "run_cmd", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="inform-user", stderr=""))

    capabilities, actor, token_eval, _ = access.effective_capabilities(team="inform")

    assert actor.login == "inform-user"
    assert token_eval.status == "missing"
    assert "tools.install" not in capabilities
    assert "config.user_safe_write" not in capabilities
    assert "tools.read" in capabilities


def test_assumed_team_suppresses_admin_only_capabilities(isolated_home, monkeypatch) -> None:
    policy_dir = isolated_home / ".ghdp" / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "team-policy.managed.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "managed_by": "ghdp",
                "teams": {
                    "inform": {
                        "allow_capabilities": [],
                        "deny_capabilities": ["tools.install", "config.user_safe_write"],
                    }
                },
                "admin_users": ["gh-mshyam"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(access, "run_cmd", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="gh-mshyam", stderr=""))
    set_value("team.selected", "platform")
    set_assumed_team("inform")

    ctx = access.resolve_access_context()

    assert ctx.base_persona == "admin"
    assert ctx.persona == "non-admin"
    assert ctx.active_mode == "assumed-team"
    assert ctx.effective_team == "inform"
    assert "admin.token.issue" not in ctx.capabilities
    assert access.RETURN_FROM_ASSUMED_TEAM in ctx.capabilities
    assert "tools.install" not in ctx.capabilities


def test_token_team_override_changes_effective_team_without_switch(isolated_home, monkeypatch) -> None:
    policy_dir = isolated_home / ".ghdp" / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "team-policy.managed.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "managed_by": "ghdp",
                "teams": {
                    "inform": {
                        "allow_capabilities": [],
                        "deny_capabilities": ["tools.install"],
                    }
                },
                "admin_users": ["gh-mshyam"],
            }
        ),
        encoding="utf-8",
    )
    _write_access_policy(isolated_home)
    _bootstrap_signer(isolated_home)
    monkeypatch.setattr(access, "run_cmd", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="inform-user", stderr=""))
    set_value("team.selected", "platform")
    token = access.issue_token(
        target_actor="inform-user",
        capabilities=["tools.install"],
        ttl_minutes=5,
        team="inform",
    )
    set_active_token(token)

    ctx = access.resolve_access_context()

    assert ctx.selected_team == "platform"
    assert ctx.effective_team == "inform"
    assert ctx.active_mode == "token-team"
    assert ctx.token_status == "active"
    assert "tools.install" in ctx.capabilities


def test_expired_token_keeps_team_context_but_not_capabilities(isolated_home, monkeypatch) -> None:
    policy_dir = isolated_home / ".ghdp" / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "team-policy.managed.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "managed_by": "ghdp",
                "teams": {
                    "inform": {
                        "allow_capabilities": [],
                        "deny_capabilities": ["tools.install", "config.user_safe_write"],
                    }
                },
                "admin_users": ["gh-mshyam"],
            }
        ),
        encoding="utf-8",
    )
    _write_access_policy(isolated_home)
    _bootstrap_signer(isolated_home)
    monkeypatch.setattr(access, "run_cmd", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="inform-user", stderr=""))
    set_value("team.selected", "platform")
    monkeypatch.setattr(access.time, "time", lambda: 1_000)
    token = access.issue_token(
        target_actor="inform-user",
        capabilities=["tools.install", "config.user_safe_write"],
        ttl_minutes=5,
        team="inform",
    )
    set_active_token(token)

    monkeypatch.setattr(access.time, "time", lambda: 1_301)
    ctx = access.resolve_access_context()

    assert ctx.selected_team == "platform"
    assert ctx.effective_team == "inform"
    assert ctx.token_status == "expired"
    assert ctx.token_team == "inform"
    assert "tools.install" not in ctx.capabilities
    assert "config.user_safe_write" not in ctx.capabilities


def test_inform_team_token_regrants_denied_capabilities_until_expiry(isolated_home, monkeypatch) -> None:
    policy_dir = isolated_home / ".ghdp" / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "team-policy.managed.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "managed_by": "ghdp",
                "teams": {
                    "inform": {
                        "allow_capabilities": [],
                        "deny_capabilities": ["tools.install", "config.user_safe_write"],
                    }
                },
                "admin_users": ["gh-mshyam"],
            }
        ),
        encoding="utf-8",
    )
    _write_access_policy(isolated_home)
    _bootstrap_signer(isolated_home)
    monkeypatch.setattr(access, "run_cmd", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="inform-user", stderr=""))
    monkeypatch.setattr(access.time, "time", lambda: 1_000)
    token = access.issue_token(
        target_actor="inform-user",
        capabilities=["tools.install", "config.user_safe_write"],
        ttl_minutes=5,
        team="inform",
    )
    set_active_token(token)

    active_capabilities, _, active_eval, _ = access.effective_capabilities(team="inform")

    assert active_eval.status == "active"
    assert "tools.install" in active_capabilities
    assert "config.user_safe_write" in active_capabilities

    monkeypatch.setattr(access.time, "time", lambda: 1_301)
    expired_capabilities, _, expired_eval, _ = access.effective_capabilities(team="inform")

    assert expired_eval.status == "expired"
    assert "tools.install" not in expired_capabilities
    assert "config.user_safe_write" not in expired_capabilities


def test_denied_message_includes_token_activation_guidance(isolated_home, monkeypatch) -> None:
    policy_dir = isolated_home / ".ghdp" / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "team-policy.managed.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "managed_by": "ghdp",
                "teams": {
                    "inform": {
                        "allow_capabilities": [],
                        "deny_capabilities": ["tools.install"],
                    }
                },
                "admin_users": ["gh-mshyam"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(access, "run_cmd", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="inform-user", stderr=""))
    set_value("team.selected", "inform")

    with pytest.raises(PlatformError) as exc:
        access.ensure_capability("tools.install", team="inform", command_name="tools install")

    assert "ghdp access token" in str(exc.value)
    assert "platform team" in str(exc.value)


def test_sync_policy_uses_packaged_fallback_when_managed_team_policy_is_missing(isolated_home, monkeypatch) -> None:
    _write_access_policy(isolated_home)
    monkeypatch.setattr(access, "run_cmd", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="basic-user", stderr=""))
    set_value("team.selected", "data_platform")

    policy = access.resolve_sync_capability_policy(team="data_platform", interactive=False)

    assert policy.restricted is True
    assert "codex-skills-aws" in policy.allowed_capabilities
    assert "tableau-athena-jars" not in policy.allowed_capabilities


def test_sync_policy_merges_packaged_fallback_when_team_policy_has_no_sync_rules(isolated_home, monkeypatch) -> None:
    _write_access_policy(isolated_home)
    policy_dir = isolated_home / ".ghdp" / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "team-policy.managed.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "managed_by": "ghdp",
                "teams": {
                    "data_platform": {
                        "allow_capabilities": [],
                        "deny_capabilities": [],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(access, "run_cmd", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="basic-user", stderr=""))

    policy = access.resolve_sync_capability_policy(team="data_platform", interactive=False)

    assert policy.restricted is True
    assert "marketplace-codex-plugin-query-athena" in policy.allowed_capabilities


def test_load_access_policy_prefers_synced_access_policy_name(isolated_home, monkeypatch) -> None:
    policy_dir = isolated_home / ".ghdp" / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "access_policy.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "personas": {
                    "non_admin": {"capabilities": ["admin.view"]},
                    "admin": {"capabilities": ["admin.view", "sync.mutate"]},
                },
                "token": {"allowed_capabilities": ["sync.mutate"]},
                "config_rules": {},
            }
        ),
        encoding="utf-8",
    )

    policy, source = load_access_policy()

    assert policy["schema_version"] == "1.0"
    assert "access_policy.json" in source


def test_packaged_access_policy_grants_non_admin_sync_mutate_without_publish() -> None:
    policy_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "platform_cli"
        / "resources"
        / "policy"
        / "access_policy.json"
    )

    payload = json.loads(policy_path.read_text(encoding="utf-8"))

    assert "sync.mutate" in payload["personas"]["non_admin"]["capabilities"]
    assert "local.lifecycle" in payload["personas"]["non_admin"]["capabilities"]
    assert "local.lifecycle" in payload["personas"]["admin"]["capabilities"]
    assert "tools.uninstall" in payload["personas"]["non_admin"]["capabilities"]
    assert "team.switch" in payload["personas"]["non_admin"]["capabilities"]
    assert "publish.execute" in payload["personas"]["non_admin"]["capabilities"]
    assert "local.lifecycle" in payload["token"]["capability_catalog"]
    assert "local.lifecycle" not in payload["token"]["allowed_capabilities"]


def test_packaged_access_policy_keeps_local_lifecycle_out_of_all_token_scopes() -> None:
    policy_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "platform_cli"
        / "resources"
        / "policy"
        / "access_policy.json"
    )

    payload = json.loads(policy_path.read_text(encoding="utf-8"))

    assert "local.lifecycle" not in payload["token"]["allowed_capabilities_by_scope"]["user"]
    assert "local.lifecycle" not in payload["token"]["allowed_capabilities_by_scope"]["team"]
    assert "local.lifecycle" not in payload["token"]["allowed_capabilities_by_scope"]["user_team"]
    assert payload["token"]["capability_catalog"]["local.lifecycle"]["selectable_by_scope"] == []


def test_packaged_team_policy_denies_local_lifecycle_for_data_analyst(isolated_home, monkeypatch) -> None:
    monkeypatch.setattr(access, "run_cmd", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="basic-user", stderr=""))
    set_value("team.selected", "data_analyst")

    capabilities, actor, token_eval, _ = access.effective_capabilities(team="data_analyst")

    assert actor.login == "basic-user"
    assert token_eval.status == "missing"
    assert "tools.install" in capabilities
    assert "local.lifecycle" not in capabilities
    assert "publish.execute" not in capabilities


def test_enforce_config_write_denies_non_admin_downgrade(isolated_home, monkeypatch) -> None:
    monkeypatch.setattr(access, "run_cmd", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="basic-user", stderr=""))

    with pytest.raises(PlatformError) as exc:
        access.enforce_config_write("precommit.mode", "off")

    assert exc.value.code == "E_ACCESS_DENIED"


def test_enforce_config_write_allows_admin_downgrade(isolated_home, monkeypatch) -> None:
    policy_dir = isolated_home / ".ghdp" / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "team-policy.managed.json").write_text(
        json.dumps({"schema_version": "1.0", "managed_by": "ghdp", "admin_users": ["admin-user"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(access, "run_cmd", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="admin-user", stderr=""))

    access.enforce_config_write("precommit.mode", "off")
