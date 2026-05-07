from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import platform_cli
from typer.testing import CliRunner

from platform_cli.commands import release as release_commands
from platform_cli.commands import schedule as schedule_commands
from platform_cli.commands import sync as sync_commands
from platform_cli.cli import app
from platform_cli.core.access import CapabilityDecision
from platform_cli.core.decorators import COMMAND_REGISTRY, requires_capability, requires_release_gate
from platform_cli.core.release_policy import ReleaseGateDecision
from platform_cli.manifests.load import load_command_restrictions_policy
from platform_cli.state.access_session import set_assumed_team

runner = CliRunner()


def test_owned_commands_expose_gate_annotations() -> None:
    plan_gates = getattr(release_commands.plan_binaries_cmd, "__ghdp_release_gates__", ())
    assert plan_gates == ()

    schedule_gates = getattr(schedule_commands.schedule_apply, "__ghdp_release_gates__", ())
    assert schedule_gates == ()

    assert "sync.mutate" in getattr(sync_commands.sync_update, "__ghdp_required_capabilities__", ())
    assert "sync.mutate" in COMMAND_REGISTRY["sync update"]["required_capabilities"]


def test_requires_capability_can_route_denial_to_hook(monkeypatch) -> None:
    seen: list[CapabilityDecision] = []

    monkeypatch.setattr(
        "platform_cli.core.access.evaluate_capability_requirement",
        lambda capability, **kwargs: CapabilityDecision(
            capability=capability,
            command_name="dummy",
            status="denied",
            message="blocked",
            code="E_ACCESS_DENIED",
            reason=capability,
            context=None,
        ),
    )

    @requires_capability("tools.install", on_denied=lambda decision: seen.append(decision) or "hooked")
    def _dummy() -> str:
        raise AssertionError("wrapped function should not run when denied")

    assert _dummy() == "hooked"
    assert len(seen) == 1
    assert seen[0].capability == "tools.install"


def test_requires_release_gate_can_route_denial_to_hook(monkeypatch) -> None:
    seen: list[ReleaseGateDecision] = []

    monkeypatch.setattr(
        "platform_cli.core.release_policy.evaluate_release_gate",
        lambda command_name, **kwargs: ReleaseGateDecision(
            command_name=command_name,
            channel="stable",
            policy_source="test",
            status="blocked",
            preview_capability="",
            message="blocked",
        ),
    )

    @requires_release_gate(on_denied=lambda decision: seen.append(decision) or "hooked")
    def _dummy() -> str:
        raise AssertionError("wrapped function should not run when denied")

    assert _dummy() == "hooked"
    assert len(seen) == 1
    assert seen[0].status == "blocked"


def _set_home(monkeypatch, home: Path) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr("platform_cli.cli.maybe_check_for_update", lambda force=False: False)


def _mock_actor(monkeypatch, login: str) -> None:
    monkeypatch.setattr(
        "platform_cli.core.access.run_cmd",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout=login, stderr=""),
    )


def _write_team_policy(home: Path, *, admin_users: list[str], data_analyst_denies: list[str] | None = None) -> None:
    policy_dir = home / ".ghdp" / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "managed_by": "ghdp",
        "admin_users": admin_users,
        "teams": {
            "data_analyst": {
                "allow_capabilities": [],
                "deny_capabilities": data_analyst_denies or [],
            }
        },
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


def _annotation_for(command_name: str) -> dict[str, str]:
    payload, _ = load_command_restrictions_policy()
    defaults = payload.get("defaults", {}) if isinstance(payload, dict) else {}
    annotations = {key: str(defaults.get(key, "") or "").strip() for key in ("access_tier", "team_scope", "release_tier")}
    for rule in payload.get("rules", []) if isinstance(payload, dict) else []:
        if not isinstance(rule, dict):
            continue
        match = rule.get("match", {})
        if not isinstance(match, dict):
            continue
        commands = match.get("commands", [])
        if isinstance(commands, list) and command_name in {str(item).strip() for item in commands if str(item).strip()}:
            rule_annotations = rule.get("annotations", {})
            if isinstance(rule_annotations, dict):
                for key in ("access_tier", "team_scope", "release_tier"):
                    value = str(rule_annotations.get(key, "") or "").strip()
                    if value:
                        annotations[key] = value
    return annotations


def test_create_branch_is_denied_for_non_admin(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path)
    _mock_actor(monkeypatch, "basic-user")

    res = runner.invoke(app, ["create-branch", "EPPE-7349-ENHANCEMENT-test-branch", "--dry-run"])

    assert res.exit_code != 0
    assert "branch.create" in str(res.exception)


def test_tableau_init_requires_data_analyst_team_context(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path)
    policy_dir = tmp_path / ".ghdp" / "policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "team-policy.managed.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "managed_by": "ghdp",
                "admin_users": ["gh-mshyam"],
                "teams": {
                    "data_analyst": {
                        "allow_capabilities": ["tableau.use"],
                        "deny_capabilities": [],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    _mock_actor(monkeypatch, "basic-user")
    monkeypatch.setattr("platform_cli.commands.tableau.tableau_init", lambda **kwargs: {"messages": ["ok"]})

    denied = runner.invoke(app, ["tableau", "init", "--dry-run"])
    assert denied.exit_code != 0
    assert "tableau.use" in str(denied.exception)

    from platform_cli.core.config import set_value

    set_value("team.selected", "data_analyst")
    allowed = runner.invoke(app, ["tableau", "init", "--dry-run"])

    assert allowed.exit_code == 0
    assert "ok" in allowed.output


def test_ci_commands_are_blocked_in_stable_even_for_admin(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path)
    _write_team_policy(tmp_path, admin_users=["gh-mshyam"])
    _mock_actor(monkeypatch, "gh-mshyam")
    monkeypatch.setattr(platform_cli, "__channel__", "stable")

    res = runner.invoke(app, ["ci", "is-jenkins"])

    assert res.exit_code != 0
    assert "not available in stable GHDP releases" in str(res.exception)


def test_ci_commands_run_for_admin_in_prerelease(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path)
    _write_team_policy(tmp_path, admin_users=["gh-mshyam"])
    _mock_actor(monkeypatch, "gh-mshyam")
    monkeypatch.setattr(platform_cli, "__channel__", "beta")
    monkeypatch.setattr("platform_cli.commands.ci.is_jenkins_pipeline", lambda: True)

    res = runner.invoke(app, ["ci", "is-jenkins"])

    assert res.exit_code == 0
    assert "Jenkins pipeline detected." in res.output


def test_local_lifecycle_commands_expose_expected_capability_annotations() -> None:
    expected = {"local.lifecycle"}

    for command_name in ("init", "build", "deploy", "tf-plan"):
        assert expected.issubset(set(COMMAND_REGISTRY[command_name]["required_capabilities"]))

    assert COMMAND_REGISTRY["build"].get("release_gates", []) == []
    assert COMMAND_REGISTRY["deploy"].get("release_gates", []) == []


def test_local_lifecycle_command_annotations_are_end_user_policy_managed() -> None:
    for command_name in (
        "init",
        "build",
        "deploy",
        "tf-init",
        "tf-set-workspace",
        "tf-validate",
        "tf-plan",
        "tf-apply",
        "tf-fmt",
        "tf-deploy",
    ):
        annotations = _annotation_for(command_name)
        assert annotations["access_tier"] == "end-user"
        assert annotations["team_scope"] == "policy-managed"
        assert annotations["release_tier"] == "stable"


def test_build_runs_for_non_admin_in_stable_with_local_lifecycle(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path)
    _mock_actor(monkeypatch, "basic-user")
    monkeypatch.setattr(platform_cli, "__channel__", "stable")

    app_config = SimpleNamespace(path="batch-processor", type="python")
    fake_repo = SimpleNamespace(apps=[app_config], get_app=lambda name: app_config if name == "batch-processor" else None)
    monkeypatch.setattr("platform_cli.commands.build_app.discover_repo_structure", lambda _repo_root: fake_repo)
    monkeypatch.setattr(
        "platform_cli.commands.build_app.build_app",
        lambda **_kwargs: SimpleNamespace(artifact_path="dist/batch-processor.whl", docker_image=None),
    )

    res = runner.invoke(app, ["build", "--app", "batch-processor"])

    assert res.exit_code == 0
    assert "not available in stable GHDP releases" not in res.output
    assert "Built batch-processor" in res.output


def test_deploy_runs_for_admin_in_stable_without_release_block(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path)
    _write_team_policy(tmp_path, admin_users=["gh-mshyam"])
    _mock_actor(monkeypatch, "gh-mshyam")
    monkeypatch.setattr(platform_cli, "__channel__", "stable")

    from platform_cli.core.config import set_value

    set_value("git.strict_clean", False)

    stack = SimpleNamespace(id="networking", deployment_order=1)
    fake_repo = SimpleNamespace(
        apps=[],
        infra_stacks=[stack],
        infra_templates_version="v1",
        get_infra_stack=lambda name: stack if name == "networking" else None,
    )
    monkeypatch.setattr("platform_cli.commands.deploy_infra.get_all_valid_environments", lambda: ["dev"])
    monkeypatch.setattr("platform_cli.commands.deploy_infra.get_local_allowed_envs", lambda: ["dev"])
    monkeypatch.setattr("platform_cli.commands.deploy_infra.discover_repo_structure", lambda _repo_root: fake_repo)
    monkeypatch.setattr("platform_cli.commands.deploy_infra.ensure_git_url_env", lambda _repo_root: None)
    monkeypatch.setattr("platform_cli.commands.deploy_infra.resolve_deploy_commit", lambda _repo_root, _commit_id: "abc123")
    monkeypatch.setattr(
        "platform_cli.commands.deploy_infra.deploy_infra_stack",
        lambda **_kwargs: {"status": "planned", "plan_file": ".terraform/dev.tfplan"},
    )
    monkeypatch.setattr("platform_cli.tools.ci_environment.is_jenkins_pipeline", lambda: False)

    res = runner.invoke(app, ["deploy", "--env", "dev", "--stack", "networking", "--plan-only"])

    assert res.exit_code == 0
    assert "not available in stable GHDP releases" not in res.output
    assert "Stack 'networking': planned" in res.output


def test_init_is_denied_for_data_analyst_selected_team(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path)
    _write_team_policy(tmp_path, admin_users=["gh-mshyam"], data_analyst_denies=["local.lifecycle"])
    _mock_actor(monkeypatch, "basic-user")

    from platform_cli.core.config import set_value

    set_value("team.selected", "data_analyst")
    res = runner.invoke(app, ["init"])

    assert res.exit_code != 0
    assert "local.lifecycle" in str(res.exception)


def test_tf_plan_is_denied_for_admin_assumed_data_analyst(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path)
    _write_team_policy(tmp_path, admin_users=["gh-mshyam"], data_analyst_denies=["local.lifecycle"])
    _mock_actor(monkeypatch, "gh-mshyam")
    set_assumed_team("data_analyst")

    try:
        res = runner.invoke(app, ["tf-plan", "-e", "dev"])
    finally:
        set_assumed_team("")

    assert res.exit_code != 0
    assert "local.lifecycle" in str(res.exception)


def test_publish_runs_for_non_admin_outside_data_analyst(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path)
    _mock_actor(monkeypatch, "basic-user")

    app_config = SimpleNamespace(path="batch-processor", type="python")
    fake_repo = SimpleNamespace(apps=[app_config], get_app=lambda name: app_config if name == "batch-processor" else None)
    monkeypatch.setattr("platform_cli.commands.publish.get_all_valid_environments", lambda: ["dev"])
    monkeypatch.setattr("platform_cli.commands.publish.get_local_allowed_envs", lambda: ["dev"])
    monkeypatch.setattr("platform_cli.commands.publish.discover_repo_structure", lambda _repo_root: fake_repo)
    monkeypatch.setattr("platform_cli.tools.ci_environment.is_jenkins_pipeline", lambda: False)
    monkeypatch.setattr(
        "platform_cli.commands.publish.publish_app",
        lambda **_kwargs: {"codeartifact_uri": "ca://batch-processor", "ecr_uri": None},
    )

    res = runner.invoke(app, ["publish", "--app", "batch-processor", "--env", "dev"])

    assert res.exit_code == 0
    assert "Published to CodeArtifact" in res.output


def test_publish_is_denied_for_data_analyst_selected_team(tmp_path: Path, monkeypatch) -> None:
    _set_home(monkeypatch, tmp_path)
    _write_access_policy(tmp_path)
    _write_team_policy(tmp_path, admin_users=["gh-mshyam"], data_analyst_denies=["local.lifecycle", "publish.execute"])
    _mock_actor(monkeypatch, "basic-user")

    from platform_cli.core.config import set_value

    set_value("team.selected", "data_analyst")
    res = runner.invoke(app, ["publish", "--app", "batch-processor"])

    assert res.exit_code != 0
    assert "publish.execute" in str(res.exception)
