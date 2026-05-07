# NOTE: Architectural rules in ARCHITECTURE.md -- do not refactor cross-layer.
from __future__ import annotations

from pathlib import Path

import pytest

from platform_cli.core.errors import PlatformError
from platform_cli.commands._tf_common import _build_backend_template_defaults
from platform_cli.commands._tf_common import build_plan_vars, discover_declared_tf_variables
from platform_cli.tools.terraform.terraform_runner import (
    enforce_guardrails,
    ensure_backend_config,
    ensure_deps,
    summarize_plan,
)


POLICY = {
    "allowed_envs": ["dev"],
}


def test_summarize_plan_actions() -> None:
    plan_json = {
        "resource_changes": [
            {"address": "a", "change": {"actions": ["create"]}},
            {"address": "b", "change": {"actions": ["update"]}},
            {"address": "c", "change": {"actions": ["delete"]}},
            {"address": "d", "change": {"actions": ["delete", "create"]}},
            {"address": "e", "change": {"actions": ["no-op"]}},
        ]
    }

    summary = summarize_plan(plan_json)

    assert summary.creates == 1
    assert summary.updates == 1
    assert summary.deletes == 1
    assert summary.replacements == 1
    assert summary.no_ops == 1


def test_enforce_guardrails_blocks_delete() -> None:
    plan_json = {
        "resource_changes": [
            {"address": "x", "change": {"actions": ["delete"]}},
        ]
    }

    with pytest.raises(PlatformError) as exc:
        enforce_guardrails(plan_json, POLICY, env="dev")

    assert exc.value.code == "E_TF_POLICY_DENY"


def test_enforce_guardrails_allows_replace_update() -> None:
    plan_json = {
        "resource_changes": [
            {"address": "x", "change": {"actions": ["update"]}},
            {"address": "y", "change": {"actions": ["create", "delete"]}},
        ]
    }

    summary = enforce_guardrails(plan_json, POLICY, env="dev")
    assert summary.updates == 1
    assert summary.replacements == 1


def test_enforce_guardrails_blocks_env() -> None:
    with pytest.raises(PlatformError):
        enforce_guardrails({}, POLICY, env="prod")


def test_ensure_deps_uses_tf_root_directory(tmp_path: Path) -> None:
    tf_root = tmp_path / "terraform"
    tf_root.mkdir(parents=True, exist_ok=True)

    targets = ensure_deps(tf_root, [])

    assert targets == []
    assert (tf_root / ".dependencies").exists()


def test_ensure_backend_config_respects_output_filename(tmp_path: Path) -> None:
    tf_root = tmp_path / "terraform"
    tf_root.mkdir(parents=True, exist_ok=True)

    out = ensure_backend_config(
        tf_root,
        backend_config_file=None,
        bucket="sample-bucket",
        key="sample/repo/terraform.tfstate",
        region="us-west-2",
        output_filename="state-lock-config-module-dpnp.properties",
        use_lockfile=True,
    )

    assert out.name == "state-lock-config-module-dpnp.properties"
    text = out.read_text(encoding="utf-8")
    assert 'bucket = "sample-bucket"' in text
    assert 'key = "sample/repo/terraform.tfstate"' in text
    assert "use_lockfile = true" in text


def test_build_backend_template_defaults_module(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = {
        "default_backend_account": "dpnp",
        "backend": {"default_mode": "module"},
        "backend_templates": {
            "accounts": {
                "dpnp": {
                    "module_bucket": "gh-dp-non-production-terraform-state-modules",
                    "standard_bucket": "gh-dp-non-production-terraform-state",
                }
            }
        },
    }
    monkeypatch.setattr("platform_cli.commands._tf_common.get_repo_name", lambda default="unknown": "demo-repo")

    bucket, key, filename = _build_backend_template_defaults(
        policy,
        env="dev",
        account="dpnp",
        backend_account=None,
        terraform_component="analytics",
    )

    assert bucket == "gh-dp-non-production-terraform-state-modules"
    assert key == "demo-repo-analytics/terraform.tfstate"
    assert filename == "state-lock-config-module-dpnp-analytics.properties"


def test_discover_declared_tf_variables(tmp_path: Path) -> None:
    tf_root = tmp_path / "terraform"
    tf_root.mkdir(parents=True, exist_ok=True)
    (tf_root / "variables.tf").write_text(
        'variable "env" {}\nvariable "account" {}\nvariable "commit_id" {}\n',
        encoding="utf-8",
    )

    declared = discover_declared_tf_variables(tf_root)
    assert declared == {"env", "account", "commit_id"}


def test_build_plan_vars_only_declared(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tf_root = tmp_path / "terraform"
    tf_root.mkdir(parents=True, exist_ok=True)
    (tf_root / "variables.tf").write_text('variable "env" {}\n', encoding="utf-8")
    monkeypatch.setattr("platform_cli.commands._tf_common.get_commit_sha", lambda default="": "abc123")
    monkeypatch.setattr("platform_cli.commands._tf_common.resolve_account", lambda _account: "dpnp")

    vars_out = build_plan_vars(
        tf_root=tf_root,
        env="dev",
        account=None,
        commit_id=None,
        non_interactive=True,
    )
    assert vars_out == {"env": "dev"}


def test_build_plan_vars_commit_detected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tf_root = tmp_path / "terraform"
    tf_root.mkdir(parents=True, exist_ok=True)
    (tf_root / "variables.tf").write_text('variable "commit_id" {}\n', encoding="utf-8")
    monkeypatch.setattr("platform_cli.commands._tf_common.get_commit_sha", lambda default="": "abcdef1234")

    vars_out = build_plan_vars(
        tf_root=tf_root,
        env="dev",
        account=None,
        commit_id=None,
        non_interactive=True,
    )
    assert vars_out == {"commit_id": "abcdef1234"}


def test_build_plan_vars_commit_missing_proceed_without(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tf_root = tmp_path / "terraform"
    tf_root.mkdir(parents=True, exist_ok=True)
    (tf_root / "variables.tf").write_text('variable "commit_id" {}\n', encoding="utf-8")
    monkeypatch.setattr("platform_cli.commands._tf_common.get_commit_sha", lambda default="": "")
    monkeypatch.setattr("platform_cli.commands._tf_common.typer.confirm", lambda *_args, **_kwargs: True)

    vars_out = build_plan_vars(
        tf_root=tf_root,
        env="dev",
        account=None,
        commit_id=None,
        non_interactive=False,
    )
    assert vars_out == {}


def test_build_plan_vars_commit_missing_prompt_value(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tf_root = tmp_path / "terraform"
    tf_root.mkdir(parents=True, exist_ok=True)
    (tf_root / "variables.tf").write_text('variable "commit_id" {}\n', encoding="utf-8")
    monkeypatch.setattr("platform_cli.commands._tf_common.get_commit_sha", lambda default="": "")
    monkeypatch.setattr("platform_cli.commands._tf_common.typer.confirm", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("platform_cli.commands._tf_common.typer.prompt", lambda *_args, **_kwargs: "manual-sha")

    vars_out = build_plan_vars(
        tf_root=tf_root,
        env="dev",
        account=None,
        commit_id=None,
        non_interactive=False,
    )
    assert vars_out == {"commit_id": "manual-sha"}
