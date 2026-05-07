from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from platform_cli import cli as root_cli
from platform_cli.commands import deploy_infra as deploy_commands
from platform_cli.commands import tf_apply as tf_apply_commands
from platform_cli.commands import tf_deploy as tf_deploy_commands
from platform_cli.commands import tf_init as tf_init_commands
from platform_cli.commands import tf_plan as tf_plan_commands
from platform_cli.core import decorators as decorator_core


runner = CliRunner()


@pytest.fixture(autouse=True)
def _suppress_root_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(root_cli, "print_header", lambda: None)
    monkeypatch.setattr(
        decorator_core,
        "run_cmd",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )


class _RecorderStatus:
    def __init__(self, events: list[tuple[str, str | None]], prefix: str) -> None:
        events.append(("status:init", prefix))
        self._events = events

    def update(self, message: str) -> None:
        self._events.append(("status:update", message))

    def finish(self, message: str | None = None) -> None:
        self._events.append(("status:finish", message))


def _fake_status_factory(events: list[tuple[str, str | None]]):
    return lambda command: _RecorderStatus(events, f"[{command}]")


def _echo_recorder(events: list[tuple[str, str | None]]):
    return lambda message="": events.append(("echo", str(message)))


def _call_index(events: list[tuple[str, str | None]], needle: tuple[str, str | None]) -> int:
    for index, event in enumerate(events):
        if event == needle:
            return index
    raise AssertionError(f"Event not found: {needle!r}\n{events!r}")


def test_tf_init_clears_status_before_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, str | None]] = []
    runtime = SimpleNamespace(
        policy={"allowed_envs": ["dev"]},
        policy_source="packaged:terraform_local.json",
        tf_root=Path("/tmp/tf"),
    )

    monkeypatch.setattr(tf_init_commands, "command_status", _fake_status_factory(events))
    monkeypatch.setattr(tf_init_commands.typer, "echo", _echo_recorder(events))
    monkeypatch.setattr(tf_init_commands, "build_runtime", lambda **kwargs: runtime)
    monkeypatch.setattr(tf_init_commands, "ensure_env_allowed", lambda policy, env: None)
    monkeypatch.setattr(
        tf_init_commands,
        "run_init_sequence",
        lambda *args, **kwargs: (Path("/tmp/backend.properties"), "repo/terraform.tfstate", "dev"),
    )

    result = runner.invoke(root_cli.app, ["tf-init"])

    assert result.exit_code == 0, result.output
    assert ("status:update", "validating") in events
    assert ("status:update", "finalizing") in events
    assert _call_index(events, ("status:finish", None)) < _call_index(
        events, ("echo", "policy source:  packaged:terraform_local.json")
    )


def test_tf_plan_stages_finish_before_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, str | None]] = []
    runtime = SimpleNamespace(
        policy={"allowed_envs": ["dev"]},
        policy_source="packaged:terraform_local.json",
        tf_root=Path("/tmp/tf"),
        env_vars={"AWS_REGION": "us-east-1"},
    )
    summary = SimpleNamespace(
        creates=1,
        updates=2,
        replacements=0,
        deletes=0,
        no_ops=3,
        replacement_resources=[],
    )

    monkeypatch.setattr(tf_plan_commands, "command_status", _fake_status_factory(events))
    monkeypatch.setattr(tf_plan_commands.typer, "echo", _echo_recorder(events))
    monkeypatch.setattr(tf_plan_commands, "build_runtime", lambda **kwargs: runtime)
    monkeypatch.setattr(tf_plan_commands, "ensure_env_allowed", lambda policy, env: None)
    monkeypatch.setattr(tf_plan_commands, "run_init_sequence", lambda *args, **kwargs: (Path("/tmp/backend.properties"), "repo/state", "dev"))
    monkeypatch.setattr(tf_plan_commands, "run_validate", lambda *args, **kwargs: None)
    monkeypatch.setattr(tf_plan_commands, "resolve_planfile", lambda *args, **kwargs: Path("/tmp/dev_tfplan"))
    monkeypatch.setattr(tf_plan_commands, "build_plan_vars", lambda **kwargs: {"env": "dev"})
    monkeypatch.setattr(tf_plan_commands, "terraform_plan", lambda *args, **kwargs: Path("/tmp/dev_tfplan"))
    monkeypatch.setattr(tf_plan_commands, "terraform_show_json", lambda *args, **kwargs: {})
    monkeypatch.setattr(tf_plan_commands, "enforce_guardrails", lambda *args, **kwargs: summary)
    monkeypatch.setattr(tf_plan_commands, "top_plan_resources", lambda plan_summary: ["module.example"])

    result = runner.invoke(root_cli.app, ["tf-plan"])

    assert result.exit_code == 0, result.output
    assert ("status:update", "validating") in events
    assert ("status:update", "planning") in events
    assert ("status:update", "finalizing") in events
    assert _call_index(events, ("status:finish", None)) < _call_index(
        events, ("echo", "policy source:  packaged:terraform_local.json")
    )


def test_tf_apply_restarts_status_after_summary_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, str | None]] = []
    runtime = SimpleNamespace(
        policy={"allowed_envs": ["dev"]},
        policy_source="packaged:terraform_local.json",
        tf_root=Path("/tmp/tf"),
        env_vars={"AWS_REGION": "us-east-1"},
    )
    summary = SimpleNamespace(
        creates=1,
        updates=0,
        replacements=0,
        deletes=0,
        no_ops=0,
        replacement_resources=[],
    )

    monkeypatch.setattr(tf_apply_commands, "command_status", _fake_status_factory(events))
    monkeypatch.setattr(tf_apply_commands.typer, "echo", _echo_recorder(events))
    monkeypatch.setattr(tf_apply_commands, "build_runtime", lambda **kwargs: runtime)
    monkeypatch.setattr(tf_apply_commands, "ensure_env_allowed", lambda policy, env: None)
    monkeypatch.setattr(tf_apply_commands, "run_init_sequence", lambda *args, **kwargs: (Path("/tmp/backend.properties"), "repo/state", "dev"))
    monkeypatch.setattr(tf_apply_commands, "run_validate", lambda *args, **kwargs: None)
    monkeypatch.setattr(tf_apply_commands, "resolve_planfile", lambda *args, **kwargs: Path("/tmp/dev_tfplan"))
    monkeypatch.setattr(tf_apply_commands, "build_plan_vars", lambda **kwargs: {"env": "dev"})
    monkeypatch.setattr(tf_apply_commands, "terraform_plan", lambda *args, **kwargs: Path("/tmp/dev_tfplan"))
    monkeypatch.setattr(tf_apply_commands, "terraform_show_json", lambda *args, **kwargs: {})
    monkeypatch.setattr(tf_apply_commands, "enforce_guardrails", lambda *args, **kwargs: summary)
    monkeypatch.setattr(tf_apply_commands, "top_plan_resources", lambda plan_summary: ["module.example"])
    monkeypatch.setattr(
        tf_apply_commands,
        "confirm_or_fail",
        lambda yes, **kwargs: events.append(("confirm", str(yes))),
    )
    monkeypatch.setattr(tf_apply_commands, "terraform_apply", lambda *args, **kwargs: None)

    result = runner.invoke(root_cli.app, ["tf-apply", "--yes"])

    assert result.exit_code == 0, result.output
    assert ("status:update", "planning") in events
    assert ("status:update", "finalizing") in events
    assert _call_index(events, ("status:finish", None)) < _call_index(
        events, ("echo", "policy source:  packaged:terraform_local.json")
    )
    assert ("confirm", "True") in events
    assert events[-1] == ("echo", "status:         tf-apply completed")


def test_tf_deploy_uses_same_summary_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, str | None]] = []
    runtime = SimpleNamespace(
        policy={"allowed_envs": ["dev"]},
        policy_source="packaged:terraform_local.json",
        tf_root=Path("/tmp/tf"),
        env_vars={"AWS_REGION": "us-east-1"},
    )
    summary = SimpleNamespace(
        creates=0,
        updates=1,
        replacements=0,
        deletes=0,
        no_ops=0,
        replacement_resources=[],
    )

    monkeypatch.setattr(decorator_core, "get_bool", lambda key, default=True: False if key == "confirm.dangerous" else default)
    monkeypatch.setattr(tf_deploy_commands, "command_status", _fake_status_factory(events))
    monkeypatch.setattr(tf_deploy_commands.typer, "echo", _echo_recorder(events))
    monkeypatch.setattr(tf_deploy_commands, "build_runtime", lambda **kwargs: runtime)
    monkeypatch.setattr(tf_deploy_commands, "ensure_env_allowed", lambda policy, env: None)
    monkeypatch.setattr(tf_deploy_commands, "run_init_sequence", lambda *args, **kwargs: (Path("/tmp/backend.properties"), "repo/state", "dev"))
    monkeypatch.setattr(tf_deploy_commands, "run_validate", lambda *args, **kwargs: None)
    monkeypatch.setattr(tf_deploy_commands, "resolve_planfile", lambda *args, **kwargs: Path("/tmp/dev_tfplan"))
    monkeypatch.setattr(tf_deploy_commands, "build_plan_vars", lambda **kwargs: {"env": "dev"})
    monkeypatch.setattr(tf_deploy_commands, "terraform_plan", lambda *args, **kwargs: Path("/tmp/dev_tfplan"))
    monkeypatch.setattr(tf_deploy_commands, "terraform_show_json", lambda *args, **kwargs: {})
    monkeypatch.setattr(tf_deploy_commands, "enforce_guardrails", lambda *args, **kwargs: summary)
    monkeypatch.setattr(tf_deploy_commands, "top_plan_resources", lambda plan_summary: ["module.example"])
    monkeypatch.setattr(
        tf_deploy_commands,
        "confirm_or_fail",
        lambda yes, **kwargs: events.append(("confirm", str(yes))),
    )
    monkeypatch.setattr(tf_deploy_commands, "terraform_apply", lambda *args, **kwargs: None)

    result = runner.invoke(root_cli.app, ["tf-deploy", "--yes"])

    assert result.exit_code == 0, result.output
    assert ("status:update", "planning") in events
    assert ("status:update", "finalizing") in events
    assert _call_index(events, ("status:finish", None)) < _call_index(
        events, ("echo", "policy source:  packaged:terraform_local.json")
    )


def test_deploy_clears_status_before_first_durable_output(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, str | None]] = []
    stack = SimpleNamespace(id="default", deployment_order=1)
    repo = SimpleNamespace(
        infra_stacks=[stack],
        infra_templates_version="v1.2.3",
        apps=[],
        get_infra_stack=lambda name: stack if name == "default" else None,
    )

    monkeypatch.setattr(deploy_commands, "command_status", _fake_status_factory(events))
    monkeypatch.setattr(deploy_commands, "print", _echo_recorder(events))
    monkeypatch.setattr(deploy_commands, "get_all_valid_environments", lambda: ["dev"])
    monkeypatch.setattr(deploy_commands, "get_local_allowed_envs", lambda: ["dev"])
    monkeypatch.setattr(deploy_commands, "discover_repo_structure", lambda repo_root: repo)
    monkeypatch.setattr(deploy_commands, "ensure_git_url_env", lambda repo_root: None)
    monkeypatch.setattr(deploy_commands, "resolve_deploy_commit", lambda repo_root, commit_id=None: "abc1234")
    monkeypatch.setattr(
        deploy_commands,
        "deploy_infra_stack",
        lambda **kwargs: {"status": "applied", "plan_file": "/tmp/dev_tfplan"},
    )
    monkeypatch.setattr("platform_cli.tools.ci_environment.is_jenkins_pipeline", lambda: False)

    result = runner.invoke(root_cli.app, ["deploy", "--env", "dev", "--yes"])

    assert result.exit_code == 0, result.output
    assert ("status:update", "validating") in events
    assert _call_index(events, ("status:finish", None)) < _call_index(
        events, ("echo", "Deploying current HEAD: abc1234")
    )
