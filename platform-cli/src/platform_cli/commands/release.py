# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import typer

from platform_cli.core.access import ensure_capability
from platform_cli.core.config import set_value
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.decorators import command_meta, tracked_command
from platform_cli.core.errors import PlatformError
from platform_cli.core.live_status import LiveStatus
from platform_cli.core.output import prompt_guided_choice
from platform_cli.tools.repo_jenkins_contract import (
    ensure_repo_jenkins_contract,
    inspect_repo_jenkins_contract,
    load_repo_jenkins_contract,
    resolve_repo_root as resolve_contract_repo_root,
)
from platform_cli.tools.release import (
    build_binaries_for_current_platform,
    ensure_binaries_release,
    plan_binaries_release,
)
from platform_cli.tools.release.workflow_adapter import write_prepare_outputs_if_supported
from platform_cli.tools.release_mcp import (
    JENKINS_API_TOKEN_CONFIG_KEY,
    RELEASE_TYPE_CHOICES,
    JenkinsReleaseResult,
    ReleaseCredentials,
    RepoIdentity,
    is_feature_branch,
    normalize_release_type,
    resolve_current_branch_name,
    resolve_github_api_token,
    resolve_jenkins_api_token_with_source,
    resolve_okta_email,
    resolve_release_credentials,
    resolve_repo_identity,
    execute_with_jenkins_token_refresh,
    run_feature_to_dev,
    run_make_release,
)
from platform_cli.tools.ci_environment import is_jenkins_pipeline

app = typer.Typer(
    help="Release build utilities and Jenkins-backed release management.",
    invoke_without_command=True,
)

_RELEASE_FLOW_CHOICES: tuple[tuple[str, str], ...] = (
    ("feature-to-dev", "feature-to-dev"),
    ("make-release", "make-release"),
)
_RELEASE_FLOW_ALIASES: dict[str, str] = {
    "1": "feature-to-dev",
    "ftd": "feature-to-dev",
    "feature-to-dev": "feature-to-dev",
    "2": "make-release",
    "mr": "make-release",
    "make-release": "make-release",
}


def register(root_app: typer.Typer) -> None:
    root_app.add_typer(app, name="release")


def _release_ci_executor_active() -> bool:
    return str(os.environ.get("GITHUB_ACTIONS", "")).strip().lower() == "true" or is_jenkins_pipeline()


def _ensure_release_manage(*, command_name: str, allow_ci_executor: bool = False) -> None:
    if allow_ci_executor and _release_ci_executor_active():
        return
    ensure_capability("release.manage", team=None, command_name=command_name)


@app.callback(invoke_without_command=True)
def release_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return

    if cli_ctx.non_interactive:
        raise PlatformError(
            "No release action was provided. Use `ghdp release feature-to-dev`, `ghdp release make-release`, or a binaries subcommand.",
            code="E_RELEASE_ACTION_REQUIRED",
            reason="missing_release_subcommand",
        )

    default = "1" if _current_branch_is_feature() else "2"
    choice = prompt_guided_choice(
        title="Choose release flow:",
        prompt_text="Select release flow",
        choices=_RELEASE_FLOW_CHOICES,
        default=default,
        aliases=_RELEASE_FLOW_ALIASES,
        prompt_fn=typer.prompt,
        echo_fn=typer.echo,
        invalid_message="Unknown release flow. Choose 1/ftd/feature-to-dev or 2/mr/make-release.",
    )
    if choice == "feature-to-dev":
        ctx.invoke(
            release_feature_to_dev,
            branch=None,
            repo=None,
            deploy_on_sqa=None,
            refresh_jenkins_contract=False,
            yes=False,
        )
        return
    if choice == "make-release":
        ctx.invoke(
            release_make_release,
            repo=None,
            release_type=None,
            parent=None,
            param=[],
            tested_ok_on_uat=None,
            merge_pr=None,
            tag_release=None,
            deploy=None,
            refresh_jenkins_contract=False,
            yes=False,
        )
        return


@app.command("plan-binaries")
@tracked_command("release plan-binaries")
@command_meta(
    name="release plan-binaries",
    category="release",
    description="Compute a portable GHDP binary release plan for the current repo.",
    tags=["release", "binaries", "plan"],
)
def plan_binaries_cmd(
    source_ref: str | None = typer.Option(None, "--source-ref", help="Branch / tag / SHA to build from."),
    workdir: str | None = typer.Option(
        None,
        "--workdir",
        help="Override the auto-detected release workdir when the repo layout is non-standard.",
    ),
    install_flavor: str = typer.Option("standard", "--install-flavor", help="standard | managed"),
    release_visibility: str = typer.Option("auto", "--release-visibility", help="auto | draft | published"),
    release_channel: str = typer.Option("auto", "--release-channel", help="auto | prerelease | ga"),
    python_version: str = typer.Option(
        f"{sys.version_info.major}.{sys.version_info.minor}",
        "--python-version",
        help="Python version used by the current execution environment (3.10+).",
    ),
) -> None:
    _ensure_release_manage(command_name="release plan-binaries")
    plan = plan_binaries_release(
        repo_root=Path.cwd(),
        source_ref=source_ref,
        workdir=workdir,
        install_flavor=install_flavor,
        release_visibility=release_visibility,
        release_channel=release_channel,
        python_version=python_version,
    )
    payload = plan.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo(f"source_ref      : {plan.source_ref}")
    typer.echo(f"repo            : {plan.repo_name_with_owner}")
    typer.echo(f"tag             : {plan.tag}")
    typer.echo(f"install flavor  : {plan.install_flavor}")
    typer.echo(f"version override: {plan.version_override or '(auto; stable branches only)'}")
    typer.echo(f"latest stable   : {plan.latest_stable_tag}")
    typer.echo(f"prerelease      : {plan.prerelease}")
    typer.echo(f"draft           : {plan.draft}")
    typer.echo(f"install flavor  : {plan.install_flavor}")
    typer.echo(f"asset           : {plan.build_target.asset}")
    typer.echo(f"built path      : {plan.build_target.built_path}")
    typer.echo(f"summary file    : {plan.summary_file}")


@app.command("prepare-binaries-release")
@tracked_command("release prepare-binaries-release")
@command_meta(
    name="release prepare-binaries-release",
    category="release",
    description="Validate notes, compute release metadata, and ensure the GitHub release exists.",
    tags=["release", "binaries", "prepare"],
)
def prepare_binaries_release_cmd(
    source_ref: str | None = typer.Option(None, "--source-ref", help="Branch / tag / SHA to build from."),
    workdir: str | None = typer.Option(
        None,
        "--workdir",
        help="Override the auto-detected release workdir when the repo layout is non-standard.",
    ),
    install_flavor: str = typer.Option("standard", "--install-flavor", help="standard | managed"),
    release_visibility: str = typer.Option("auto", "--release-visibility", help="auto | draft | published"),
    release_channel: str = typer.Option("auto", "--release-channel", help="auto | prerelease | ga"),
    python_version: str = typer.Option(
        f"{sys.version_info.major}.{sys.version_info.minor}",
        "--python-version",
        help="Python version used by the current execution environment (3.10+).",
    ),
    version_override: str = typer.Option(
        "",
        "--version-override",
        help="Optional stable release tag override (for example v0.2.3).",
    ),
) -> None:
    _ensure_release_manage(command_name="release prepare-binaries-release", allow_ci_executor=True)
    plan = plan_binaries_release(
        repo_root=Path.cwd(),
        source_ref=source_ref,
        workdir=workdir,
        install_flavor=install_flavor,
        release_visibility=release_visibility,
        release_channel=release_channel,
        python_version=python_version,
        version_override=version_override,
    )
    result = ensure_binaries_release(plan)
    write_prepare_outputs_if_supported(plan)
    if cli_ctx.json:
        typer.echo(json.dumps(result, indent=2, sort_keys=True))
        return
    typer.echo(f"Prepared release: {result['tag']}")
    typer.echo(f"repo            : {result['release_repo']}")
    typer.echo(f"source_ref      : {result['source_ref']}")
    typer.echo(f"install flavor  : {plan.install_flavor}")
    typer.echo(f"version override: {plan.version_override or '(auto; stable branches only)'}")
    typer.echo(f"prerelease      : {result['prerelease']}")
    typer.echo(f"draft           : {result['draft']}")


@app.command("build-binaries")
@tracked_command("release build-binaries")
@command_meta(
    name="release build-binaries",
    category="release",
    description="Build and upload GHDP binaries for the current platform.",
    tags=["release", "binaries", "build"],
)
def build_binaries_cmd(
    source_ref: str | None = typer.Option(None, "--source-ref", help="Branch / tag / SHA to build from."),
    workdir: str | None = typer.Option(
        None,
        "--workdir",
        help="Override the auto-detected release workdir when the repo layout is non-standard.",
    ),
    install_flavor: str = typer.Option("standard", "--install-flavor", help="standard | managed"),
    release_visibility: str = typer.Option("auto", "--release-visibility", help="auto | draft | published"),
    release_channel: str = typer.Option("auto", "--release-channel", help="auto | prerelease | ga"),
    python_version: str = typer.Option(
        f"{sys.version_info.major}.{sys.version_info.minor}",
        "--python-version",
        help="Python version used by the current execution environment (3.10+).",
    ),
    version_override: str = typer.Option(
        "",
        "--version-override",
        help="Optional stable release tag override (for example v0.2.3).",
    ),
    ensure_release: bool = typer.Option(
        True,
        "--ensure-release/--skip-ensure-release",
        help="Ensure the GitHub release exists before building and uploading assets.",
    ),
) -> None:
    _ensure_release_manage(command_name="release build-binaries", allow_ci_executor=True)
    plan = plan_binaries_release(
        repo_root=Path.cwd(),
        source_ref=source_ref,
        workdir=workdir,
        install_flavor=install_flavor,
        release_visibility=release_visibility,
        release_channel=release_channel,
        python_version=python_version,
        version_override=version_override,
    )
    result = build_binaries_for_current_platform(plan, ensure_release=ensure_release)
    payload = result.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo(f"Built + uploaded tag : {result.tag}")
    typer.echo(f"install flavor       : {plan.install_flavor}")
    typer.echo(f"version override     : {plan.version_override or '(auto; stable branches only)'}")
    typer.echo(f"asset                : {result.asset}")
    typer.echo(f"asset path           : {result.asset_path}")
    typer.echo(f"checksum path        : {result.checksum_path}")
    typer.echo(f"install flavor       : {result.install_flavor}")
