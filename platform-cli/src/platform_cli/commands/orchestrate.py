# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

import json
from pathlib import Path

import typer

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.decorators import command_meta, tracked_command
from platform_cli.orchestrate_kernel.stage19b_published_prerelease_retest import run_published_prerelease_retest_stage
from platform_cli.orchestrate_kernel.runtime_support import resolve_active_run_context
from platform_cli.orchestrate_kernel.stage20_release_notes import run_release_notes_refresh_stage
from platform_cli.orchestrate_kernel.stage21_pr_external import run_pr_external_integration_stage
from platform_cli.orchestrate_kernel.stage22_traceability import run_traceability_capture_stage
from platform_cli.orchestrate_kernel.subagents import persist_scenario_result, run_subagent_scenario
from platform_cli.tools.orchestrate_contract import inspect_orchestrate_contract
from platform_cli.tools.orchestrate_commit_push import run_commit_push_stage
from platform_cli.tools.orchestrate_execution import run_execution_prep
from platform_cli.tools.orchestrate_front_door import run_front_door_gates
from platform_cli.tools.orchestrate_coverage import run_coverage_stage
from platform_cli.tools.orchestrate_binary_validation import run_packaged_artifact_validation_stage
from platform_cli.tools.orchestrate_asset_lifecycle import run_asset_lifecycle
from platform_cli.tools.orchestrate_implementation import run_implementation_stage
from platform_cli.tools.orchestrate_merge_hygiene import (
    run_finalize_merge_hygiene,
    run_verify_merge_hygiene,
)
from platform_cli.tools.orchestrate_prerelease import run_prerelease_stage
from platform_cli.tools.orchestrate_qa import run_qa_scenario_stage
from platform_cli.tools.orchestrate_regression import run_regression_stage
from platform_cli.tools.orchestrate_release_readiness import run_release_readiness_stage
from platform_cli.tools.orchestrate_review import run_review_layer
from platform_cli.tools.orchestrate_test_execution import run_developer_test_execution_stage
from platform_cli.tools.orchestrate_runtime import (
    handoff_orchestrate_run,
    resume_orchestrate_run,
    start_orchestrate_run,
)


app = typer.Typer(
    help="Phase 1 orchestrator contract and runtime inspection commands.",
    invoke_without_command=True,
)


def register(root_app: typer.Typer) -> None:
    root_app.add_typer(app, name="orchestrate")


@app.callback(invoke_without_command=True)
def orchestrate_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    ctx.invoke(orchestrate_status)


@app.command("status")
@tracked_command("orchestrate status")
@command_meta(
    name="orchestrate status",
    category="orchestrate",
    description="Inspect the repo-level Phase 1 orchestrator contract and active branch runtime skeleton.",
    tags=["orchestrate", "agents", "skills", "plugins", "memory"],
)
def orchestrate_status(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Optional repo root override. Defaults to the current git repo.",
    ),
) -> None:
    status = inspect_orchestrate_contract(repo_root=repo_root)
    payload = status.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo(f"repo_root             : {status.repo_root}")
    typer.echo(f"branch                : {status.branch_name or '(unresolved)'}")
    typer.echo(f"ticket                : {status.ticket_key or '(missing)'}")
    typer.echo(f"repo_contract_ready   : {status.repo_contract_ready}")
    typer.echo(f"branch_runtime_ready  : {status.branch_runtime_ready}")
    typer.echo(f"contract_ready        : {status.contract_ready}")
    typer.echo(f"agents                : {status.agents_count}")
    typer.echo(f"skills                : {status.skills_count}")
    typer.echo(f"plugins               : {status.plugins_count}")
    typer.echo(f"memory_partitions     : {status.memory_partition_count}")
    typer.echo(f"active_run_key        : {status.active_run_key or '(missing)'}")
    typer.echo(f"branch_runtime_root   : {status.branch_runtime_root}")
    typer.echo(f"branch_runtime_mode   : {status.branch_runtime_mode}")
    if status.missing:
        typer.echo("missing:")
        for item in status.missing:
            typer.echo(f"  - {item}")
    if status.warnings:
        typer.echo("warnings:")
        for item in status.warnings:
            typer.echo(f"  - {item}")


@app.command("start")
@tracked_command("orchestrate start")
@command_meta(
    name="orchestrate start",
    category="orchestrate",
    description="Bootstrap or reuse the active branch-scoped orchestrator run for the current feature branch.",
    tags=["orchestrate", "runtime", "start"],
)
def orchestrate_start(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Optional repo root override. Defaults to the current git repo.",
    ),
) -> None:
    result = start_orchestrate_run(repo_root=repo_root)
    _print_lifecycle_result(result)


@app.command("resume")
@tracked_command("orchestrate resume")
@command_meta(
    name="orchestrate resume",
    category="orchestrate",
    description="Resume the active branch-scoped orchestrator run for the current feature branch.",
    tags=["orchestrate", "runtime", "resume"],
)
def orchestrate_resume(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Optional repo root override. Defaults to the current git repo.",
    ),
) -> None:
    result = resume_orchestrate_run(repo_root=repo_root)
    _print_lifecycle_result(result)


@app.command("handoff")
@tracked_command("orchestrate handoff")
@command_meta(
    name="orchestrate handoff",
    category="orchestrate",
    description="Pause the active branch run and record handoff context for the next human or agent.",
    tags=["orchestrate", "runtime", "handoff"],
)
def orchestrate_handoff(
    summary: str = typer.Option(..., "--summary", help="Short summary of the current state."),
    next_action: str = typer.Option(..., "--next-action", help="The exact next action the next owner should take."),
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Optional repo root override. Defaults to the current git repo.",
    ),
) -> None:
    result = handoff_orchestrate_run(summary=summary, next_action=next_action, repo_root=repo_root)
    _print_lifecycle_result(result)


@app.command("front-door")
@tracked_command("orchestrate front-door")
@command_meta(
    name="orchestrate front-door",
    category="orchestrate",
    description="Run the Phase 1 front-door gates: intake sufficiency, work type, autonomy, context discovery, and POA refresh.",
    tags=["orchestrate", "front-door", "intake", "planning"],
)
def orchestrate_front_door(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Optional repo root override. Defaults to the current git repo.",
    ),
) -> None:
    result = run_front_door_gates(repo_root=repo_root)
    _print_front_door_result(result)


@app.command("asset-lifecycle")
@tracked_command("orchestrate asset-lifecycle")
@command_meta(
    name="orchestrate asset-lifecycle",
    category="orchestrate",
    description="Run the lightweight asset lifecycle path for inventory, revise, version-update, or removal work on GHDP-managed assets.",
    tags=["orchestrate", "asset", "sync", "capability"],
)
def orchestrate_asset_lifecycle(
    operation: str = typer.Option(
        "inventory",
        "--operation",
        help="Asset lifecycle operation: inventory, create, revise, update_versioned_asset, or remove.",
    ),
    asset_target: str = typer.Option(
        "",
        "--asset-target",
        help="Optional asset target id from .ghdp/plugins/asset-lifecycle-sync/plugin.json.",
    ),
    new_version: str = typer.Option(
        "",
        "--new-version",
        help="Semantic version used by versioned asset revisions such as toolset minimum-version changes.",
    ),
    payload_file: Path | None = typer.Option(
        None,
        "--payload-file",
        help="Optional JSON payload file for generic create/revise/remove asset operations.",
    ),
    provider_family: str = typer.Option(
        "",
        "--provider-family",
        help="Optional provider family override: github_release or marketplace_repo.",
    ),
    publish: bool = typer.Option(
        False,
        "--publish/--no-publish",
        help="When enabled for release-backed assets, publish the built bundle with gh and refresh content-index-latest.",
    ),
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Optional repo root override. Defaults to the current git repo.",
    ),
) -> None:
    result = run_asset_lifecycle(
        repo_root=repo_root,
        operation=operation,
        asset_target=asset_target,
        new_version=new_version,
        payload_file=payload_file,
        provider_family=provider_family,
        publish=publish,
    )
    _print_asset_lifecycle_result(result)


@app.command("review")
@tracked_command("orchestrate review")
@command_meta(
    name="orchestrate review",
    category="orchestrate",
    description="Run the Stage D review layer across architecture and UX/DX findings for the current branch run.",
    tags=["orchestrate", "review", "architecture", "uxdx"],
)
def orchestrate_review(
    scope: str = typer.Option(
        "all",
        "--scope",
        help="Review scope: all, architecture, or uxdx.",
    ),
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Optional repo root override. Defaults to the current git repo.",
    ),
) -> None:
    result = run_review_layer(scope=scope, repo_root=repo_root)
    _print_review_result(result)


@app.command("execution-prep")
@tracked_command("orchestrate execution-prep")
@command_meta(
    name="orchestrate execution-prep",
    category="orchestrate",
    description="Generate the Stage E execution-layer artifacts and bind the repo-level skills/plugins required for implementation, regression, coverage, and test execution.",
    tags=["orchestrate", "execution", "testing", "skills", "plugins"],
)
def orchestrate_execution_prep(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Optional repo root override. Defaults to the current git repo.",
    ),
) -> None:
    result = run_execution_prep(repo_root=repo_root)
    _print_execution_prep_result(result)


@app.command("implement")
@tracked_command("orchestrate implement")
@command_meta(
    name="orchestrate implement",
    category="orchestrate",
    description="Activate Stage 11 implementation using the repo-level implementation agent contract and write the implementation packet.",
    tags=["orchestrate", "implementation", "stage11", "agents"],
)
def orchestrate_implement(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Optional repo root override. Defaults to the current git repo.",
    ),
) -> None:
    result = run_implementation_stage(repo_root=repo_root)
    _print_implementation_result(result)


@app.command("commit-push")
@tracked_command("orchestrate commit-push")
@command_meta(
    name="orchestrate commit-push",
    category="orchestrate",
    description="Run Stage 12 commit/push using repo-backed implementation artifacts and push the active branch.",
    tags=["orchestrate", "commit", "push", "stage12", "git"],
)
def orchestrate_commit_push(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Optional repo root override. Defaults to the current git repo.",
    ),
) -> None:
    result = run_commit_push_stage(repo_root=repo_root)
    _print_commit_push_result(result)


@app.command("qa-scenarios")
@tracked_command("orchestrate qa-scenarios")
@command_meta(
    name="orchestrate qa-scenarios",
    category="orchestrate",
    description="Run Stage 13 QA scenario design using the repo-level QA agent contract and produce a repo-backed scenario packet.",
    tags=["orchestrate", "qa", "stage13", "agents", "testing"],
)
def orchestrate_qa_scenarios(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Optional repo root override. Defaults to the current git repo.",
    ),
) -> None:
    result = run_qa_scenario_stage(repo_root=repo_root)
    _print_qa_result(result)


@app.command("regression")
@tracked_command("orchestrate regression")
@command_meta(
    name="orchestrate regression",
    category="orchestrate",
    description="Run Stage 14 touched-scope regression validation using the repo-level regression agent contract and produce a repo-backed regression packet.",
    tags=["orchestrate", "regression", "stage14", "agents", "testing"],
)
def orchestrate_regression(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Optional repo root override. Defaults to the current git repo.",
    ),
) -> None:
    result = run_regression_stage(repo_root=repo_root)
    _print_regression_result(result)


@app.command("coverage")
@tracked_command("orchestrate coverage")
@command_meta(
    name="orchestrate coverage",
    category="orchestrate",
    description="Run Stage 15 new test coverage authoring using the repo-level coverage agent contract and produce a repo-backed coverage backlog.",
    tags=["orchestrate", "coverage", "stage15", "agents", "testing"],
)
def orchestrate_coverage(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Optional repo root override. Defaults to the current git repo.",
    ),
) -> None:
    result = run_coverage_stage(repo_root=repo_root)
    _print_coverage_result(result)


@app.command("test-execution")
@tracked_command("orchestrate test-execution")
@command_meta(
    name="orchestrate test-execution",
    category="orchestrate",
    description="Run Stage 16 developer test execution using the repo-level execution agent contract and execute the selected regression and coverage tests.",
    tags=["orchestrate", "test-execution", "stage16", "agents", "testing"],
)
def orchestrate_test_execution(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Optional repo root override. Defaults to the current git repo.",
    ),
) -> None:
    result = run_developer_test_execution_stage(repo_root=repo_root)
    _print_test_execution_result(result)


@app.command("binary-validate")
@tracked_command("orchestrate binary-validate")
@command_meta(
    name="orchestrate binary-validate",
    category="orchestrate",
    description="Run Stage 17 packaged artifact validation by installing GHDP through pipx and smoke-validating the packaged CLI path.",
    tags=["orchestrate", "binary-validation", "stage17", "agents", "testing"],
)
def orchestrate_binary_validate(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Optional repo root override. Defaults to the current git repo.",
    ),
) -> None:
    result = run_packaged_artifact_validation_stage(repo_root=repo_root)
    _print_binary_validation_result(result)


@app.command("release-readiness")
@tracked_command("orchestrate release-readiness")
@command_meta(
    name="orchestrate release-readiness",
    category="orchestrate",
    description="Run Stage 18 release readiness review using the accumulated execution and artifact-validation evidence.",
    tags=["orchestrate", "release-readiness", "stage18", "agents", "release"],
)
def orchestrate_release_readiness(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Optional repo root override. Defaults to the current git repo.",
    ),
) -> None:
    result = run_release_readiness_stage(repo_root=repo_root)
    _print_release_readiness_result(result)


@app.command("prerelease")
@tracked_command("orchestrate prerelease")
@command_meta(
    name="orchestrate prerelease",
    category="orchestrate",
    description="Run Stage 19 prerelease creation using the real release engine and record either the created prerelease or the blocking reason.",
    tags=["orchestrate", "prerelease", "stage19", "agents", "release"],
)
def orchestrate_prerelease(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Optional repo root override. Defaults to the current git repo.",
    ),
) -> None:
    result = run_prerelease_stage(repo_root=repo_root)
    _print_prerelease_result(result)


@app.command("published-prerelease-retest")
@tracked_command("orchestrate published-prerelease-retest")
@command_meta(
    name="orchestrate published-prerelease-retest",
    category="orchestrate",
    description="Run Stage 19B published prerelease retest by downloading the actual prerelease asset for the current host and validating it directly.",
    tags=["orchestrate", "prerelease", "stage19b", "release", "artifact"],
)
def orchestrate_published_prerelease_retest(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Optional repo root override. Defaults to the current git repo.",
    ),
) -> None:
    result = run_published_prerelease_retest_stage(repo_root=repo_root)
    _print_simple_result(result)


@app.command("release-notes-refresh")
@tracked_command("orchestrate release-notes-refresh")
@command_meta(
    name="orchestrate release-notes-refresh",
    category="orchestrate",
    description="Run Stage 20 release-notes refresh to update notes.md from repo-backed run artifacts and create the freshness commit.",
    tags=["orchestrate", "release-notes", "stage20", "release"],
)
def orchestrate_release_notes_refresh(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Optional repo root override. Defaults to the current git repo.",
    ),
) -> None:
    result = run_release_notes_refresh_stage(repo_root=repo_root)
    _print_release_notes_result(result)


@app.command("pr-integrate")
@tracked_command("orchestrate pr-integrate")
@command_meta(
    name="orchestrate pr-integrate",
    category="orchestrate",
    description="Run Stage 21 PR and Jira integration using portable GitHub CLI and ACLI-backed contracts.",
    tags=["orchestrate", "pr", "jira", "stage21", "integration"],
)
def orchestrate_pr_integrate(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Optional repo root override. Defaults to the current git repo.",
    ),
) -> None:
    result = run_pr_external_integration_stage(repo_root=repo_root)
    _print_pr_integration_result(result)


@app.command("historian-closeout")
@tracked_command("orchestrate historian-closeout")
@command_meta(
    name="orchestrate historian-closeout",
    category="orchestrate",
    description="Run Stage 22 traceability capture and finalize the branch run packet.",
    tags=["orchestrate", "historian", "stage22", "traceability"],
)
def orchestrate_historian_closeout(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Optional repo root override. Defaults to the current git repo.",
    ),
) -> None:
    result = run_traceability_capture_stage(repo_root=repo_root)
    _print_historian_result(result)


@app.command("finalize")
@tracked_command("orchestrate finalize")
@command_meta(
    name="orchestrate finalize",
    category="orchestrate",
    description="Archive runtime-only branch orchestrate artifacts, promote durable memory, and prune the branch runtime folder before merge.",
    tags=["orchestrate", "finalize", "merge-hygiene", "memory"],
)
def orchestrate_finalize(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Repo root override.",
    ),
) -> None:
    result = run_finalize_merge_hygiene(repo_root=repo_root)
    _print_finalize_result(result)


@app.command("verify-merge-hygiene")
@tracked_command("orchestrate verify-merge-hygiene")
@command_meta(
    name="orchestrate verify-merge-hygiene",
    category="orchestrate",
    description="Non-mutating merge gate that verifies runtime-only orchestrate files are pruned and durable closeout memory is present.",
    tags=["orchestrate", "verify", "merge-hygiene", "ci"],
)
def orchestrate_verify_merge_hygiene(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Repo root override.",
    ),
) -> None:
    result = run_verify_merge_hygiene(repo_root=repo_root)
    _print_verify_merge_hygiene_result(result)


@app.command("subagent-scenario")
@tracked_command("orchestrate subagent-scenario")
@command_meta(
    name="orchestrate subagent-scenario",
    category="orchestrate",
    description="Run a repo-defined sub-agent scenario through the provider-adapter and topology contracts, optionally executing the provider outputs.",
    tags=["orchestrate", "subagents", "scenario", "provider-adapters"],
)
def orchestrate_subagent_scenario(
    scenario_id: str = typer.Option("new_feature_subagent_smoke", "--scenario-id", help="Repo-defined scenario id."),
    execute_provider: bool = typer.Option(False, "--execute-provider", help="Execute the provider-compatible prompt packets instead of planning only."),
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Optional repo root override. Defaults to the current git repo.",
    ),
) -> None:
    result = run_subagent_scenario(scenario_id=scenario_id, repo_root=repo_root, execute_provider=execute_provider)
    context = resolve_active_run_context(repo_root=repo_root)
    persist_scenario_result(run_root=context.run_root, result=result)
    _print_subagent_scenario_result(result)


def _print_lifecycle_result(result: object) -> None:
    payload = result.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo(f"action                : {payload['action']}")
    typer.echo(f"branch                : {payload['branch_name']}")
    typer.echo(f"ticket                : {payload['ticket_key'] or '(missing)'}")
    typer.echo(f"active_run_key        : {payload['active_run_key']}")
    typer.echo(f"status                : {payload['status']}")
    typer.echo(f"current_stage         : {payload['current_stage']}")
    typer.echo(f"next_action           : {payload['next_action']}")
    typer.echo(f"created_new_run       : {payload['created_new_run']}")
    typer.echo(f"policy_source         : {payload['policy_source']}")
    typer.echo(f"execution_mode        : {payload['execution_mode']}")
    typer.echo(f"provider_mode         : {payload['provider_mode']}")
    typer.echo(f"branch_runtime_root   : {payload['branch_runtime_root']}")
    typer.echo(f"message               : {payload['message']}")


def _print_front_door_result(result: object) -> None:
    payload = result.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo(f"action                : {payload['action']}")
    typer.echo(f"branch                : {payload['branch_name']}")
    typer.echo(f"ticket                : {payload['ticket_key'] or '(missing)'}")
    typer.echo(f"active_run_key        : {payload['active_run_key']}")
    typer.echo(f"status                : {payload['status']}")
    typer.echo(f"current_stage         : {payload['current_stage']}")
    typer.echo(f"work_type             : {payload['work_type']}")
    typer.echo(f"autonomy_level        : {payload['autonomy_level']}")
    typer.echo(f"autonomy_confidence   : {payload['autonomy_confidence']}")
    typer.echo(f"intake_sufficient     : {payload['intake_sufficient']}")
    typer.echo(f"intake_confidence     : {payload['intake_confidence']}")
    typer.echo(f"spec_action           : {payload['spec_action']}")
    typer.echo(f"delivery_route        : {payload['delivery_route']}")
    typer.echo(f"asset_operation       : {payload['asset_operation']}")
    typer.echo(f"parallel_decision     : {payload['parallel_work_decision']}")
    typer.echo(f"next_action           : {payload['next_action']}")
    typer.echo(f"branch_runtime_root   : {payload['branch_runtime_root']}")
    typer.echo(f"message               : {payload['message']}")
    if payload["capability_matches"]:
        typer.echo("capability_matches:")
        for item in payload["capability_matches"]:
            typer.echo(f"  - {item}")
    if payload["impacted_areas"]:
        typer.echo("impacted_areas:")
        for item in payload["impacted_areas"]:
            typer.echo(f"  - {item}")
    if payload["clarification_questions"]:
        typer.echo("clarification_questions:")
        for item in payload["clarification_questions"]:
            typer.echo(f"  - {item}")


def _print_asset_lifecycle_result(result: object) -> None:
    payload = result.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo(f"action                : {payload['action']}")
    typer.echo(f"branch                : {payload['branch_name'] or '(no active run)'}")
    typer.echo(f"ticket                : {payload['ticket_key'] or '(missing)'}")
    typer.echo(f"active_run_key        : {payload['active_run_key'] or '(missing)'}")
    typer.echo(f"status                : {payload['status']}")
    typer.echo(f"operation             : {payload['operation']}")
    typer.echo(f"asset_target          : {payload['asset_target'] or '(inventory only)'}")
    typer.echo(f"capability_id         : {payload['capability_id'] or '(n/a)'}")
    typer.echo(f"provider_family       : {payload['provider_family'] or '(n/a)'}")
    typer.echo(f"inventory_count       : {payload['inventory_count']}")
    typer.echo(f"bundle_contract_path  : {payload.get('bundle_contract_path') or '(n/a)'}")
    typer.echo(f"built_bundle_dir      : {payload.get('built_bundle_dir') or '(n/a)'}")
    typer.echo(f"published             : {payload.get('published', False)}")
    typer.echo(f"message               : {payload['message']}")
    if payload["source_files"]:
        typer.echo("source_files:")
        for item in payload["source_files"]:
            typer.echo(f"  - {item}")
    if payload["changed_files"]:
        typer.echo("changed_files:")
        for item in payload["changed_files"]:
            typer.echo(f"  - {item}")
    if payload["changed_teams"]:
        typer.echo("changed_teams:")
        for item in payload["changed_teams"]:
            typer.echo(f"  - {item}")
    if payload["release_implications"]:
        typer.echo("release_implications:")
        for item in payload["release_implications"]:
            typer.echo(f"  - {item}")


def _print_review_result(result: object) -> None:
    payload = result.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo(f"action                : {payload['action']}")
    typer.echo(f"scope                 : {payload['scope']}")
    typer.echo(f"branch                : {payload['branch_name']}")
    typer.echo(f"ticket                : {payload['ticket_key'] or '(missing)'}")
    typer.echo(f"active_run_key        : {payload['active_run_key']}")
    typer.echo(f"status                : {payload['status']}")
    typer.echo(f"current_stage         : {payload['current_stage']}")
    typer.echo(f"blocking_findings     : {payload['blocking_findings']}")
    typer.echo(f"next_action           : {payload['next_action']}")
    typer.echo(f"branch_runtime_root   : {payload['branch_runtime_root']}")
    typer.echo(f"message               : {payload['message']}")
    if payload["architecture_findings"]:
        typer.echo("architecture_findings:")
        for item in payload["architecture_findings"]:
            typer.echo(f"  - {item}")
    if payload["uxdx_findings"]:
        typer.echo("uxdx_findings:")
        for item in payload["uxdx_findings"]:
            typer.echo(f"  - {item}")


def _print_execution_prep_result(result: object) -> None:
    payload = result.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo(f"action                : {payload['action']}")
    typer.echo(f"branch                : {payload['branch_name']}")
    typer.echo(f"ticket                : {payload['ticket_key'] or '(missing)'}")
    typer.echo(f"active_run_key        : {payload['active_run_key']}")
    typer.echo(f"status                : {payload['status']}")
    typer.echo(f"current_stage         : {payload['current_stage']}")
    typer.echo(f"work_type             : {payload['work_type']}")
    typer.echo(f"next_action           : {payload['next_action']}")
    typer.echo(f"branch_runtime_root   : {payload['branch_runtime_root']}")
    typer.echo(f"message               : {payload['message']}")
    if payload["implementation_targets"]:
        typer.echo("implementation_targets:")
        for item in payload["implementation_targets"]:
            typer.echo(f"  - {item}")
    if payload["regression_targets"]:
        typer.echo("regression_targets:")
        for item in payload["regression_targets"]:
            typer.echo(f"  - {item}")
    if payload["coverage_targets"]:
        typer.echo("coverage_targets:")
        for item in payload["coverage_targets"]:
            typer.echo(f"  - {item}")
    if payload["skills_bound"]:
        typer.echo("skills_bound:")
        for item in payload["skills_bound"]:
            typer.echo(f"  - {item}")
    if payload["plugins_bound"]:
        typer.echo("plugins_bound:")
        for item in payload["plugins_bound"]:
            typer.echo(f"  - {item}")


def _print_implementation_result(result: object) -> None:
    payload = result.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo(f"action                : {payload['action']}")
    typer.echo(f"branch                : {payload['branch_name']}")
    typer.echo(f"ticket                : {payload['ticket_key'] or '(missing)'}")
    typer.echo(f"active_run_key        : {payload['active_run_key']}")
    typer.echo(f"status                : {payload['status']}")
    typer.echo(f"current_stage         : {payload['current_stage']}")
    typer.echo(f"implementation_agent  : {payload['implementation_agent']}")
    typer.echo(f"next_action           : {payload['next_action']}")
    typer.echo(f"branch_runtime_root   : {payload['branch_runtime_root']}")
    typer.echo(f"message               : {payload['message']}")
    if payload["allowed_skills"]:
        typer.echo("allowed_skills:")
        for item in payload["allowed_skills"]:
            typer.echo(f"  - {item}")
    if payload["allowed_plugins"]:
        typer.echo("allowed_plugins:")
        for item in payload["allowed_plugins"]:
            typer.echo(f"  - {item}")
    if payload["implementation_targets"]:
        typer.echo("implementation_targets:")
        for item in payload["implementation_targets"]:
            typer.echo(f"  - {item}")


def _print_commit_push_result(result: object) -> None:
    payload = result.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo(f"action                : {payload['action']}")
    typer.echo(f"branch                : {payload['branch_name']}")
    typer.echo(f"ticket                : {payload['ticket_key'] or '(missing)'}")
    typer.echo(f"active_run_key        : {payload['active_run_key']}")
    typer.echo(f"status                : {payload['status']}")
    typer.echo(f"current_stage         : {payload['current_stage']}")
    typer.echo(f"commit_message        : {payload['commit_message']}")
    typer.echo(f"head_sha              : {payload['head_sha']}")
    typer.echo(f"remote                : {payload['remote_name']}/{payload['remote_branch']}")
    typer.echo(f"pushed                : {payload['pushed']}")
    typer.echo(f"next_action           : {payload['next_action']}")
    typer.echo(f"branch_runtime_root   : {payload['branch_runtime_root']}")
    typer.echo(f"message               : {payload['message']}")
    if payload["files_committed"]:
        typer.echo("files_committed:")
        for item in payload["files_committed"]:
            typer.echo(f"  - {item}")


def _print_qa_result(result: object) -> None:
    payload = result.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo(f"action                : {payload['action']}")
    typer.echo(f"branch                : {payload['branch_name']}")
    typer.echo(f"ticket                : {payload['ticket_key'] or '(missing)'}")
    typer.echo(f"active_run_key        : {payload['active_run_key']}")
    typer.echo(f"status                : {payload['status']}")
    typer.echo(f"current_stage         : {payload['current_stage']}")
    typer.echo(f"qa_agent              : {payload['qa_agent']}")
    typer.echo(f"scenario_count        : {payload['scenario_count']}")
    typer.echo(f"next_action           : {payload['next_action']}")
    typer.echo(f"branch_runtime_root   : {payload['branch_runtime_root']}")
    typer.echo(f"message               : {payload['message']}")
    if payload["allowed_skills"]:
        typer.echo("allowed_skills:")
        for item in payload["allowed_skills"]:
            typer.echo(f"  - {item}")
    if payload["allowed_plugins"]:
        typer.echo("allowed_plugins:")
        for item in payload["allowed_plugins"]:
            typer.echo(f"  - {item}")


def _print_regression_result(result: object) -> None:
    payload = result.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo(f"action                : {payload['action']}")
    typer.echo(f"branch                : {payload['branch_name']}")
    typer.echo(f"ticket                : {payload['ticket_key'] or '(missing)'}")
    typer.echo(f"active_run_key        : {payload['active_run_key']}")
    typer.echo(f"status                : {payload['status']}")
    typer.echo(f"current_stage         : {payload['current_stage']}")
    typer.echo(f"regression_agent      : {payload['regression_agent']}")
    typer.echo(f"next_action           : {payload['next_action']}")
    typer.echo(f"branch_runtime_root   : {payload['branch_runtime_root']}")
    typer.echo(f"message               : {payload['message']}")
    if payload["allowed_skills"]:
        typer.echo("allowed_skills:")
        for item in payload["allowed_skills"]:
            typer.echo(f"  - {item}")
    if payload["allowed_plugins"]:
        typer.echo("allowed_plugins:")
        for item in payload["allowed_plugins"]:
            typer.echo(f"  - {item}")
    if payload["selected_tests"]:
        typer.echo("selected_tests:")
        for item in payload["selected_tests"]:
            typer.echo(f"  - {item}")


def _print_coverage_result(result: object) -> None:
    payload = result.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo(f"action                : {payload['action']}")
    typer.echo(f"branch                : {payload['branch_name']}")
    typer.echo(f"ticket                : {payload['ticket_key'] or '(missing)'}")
    typer.echo(f"active_run_key        : {payload['active_run_key']}")
    typer.echo(f"status                : {payload['status']}")
    typer.echo(f"current_stage         : {payload['current_stage']}")
    typer.echo(f"coverage_agent        : {payload['coverage_agent']}")
    typer.echo(f"authored_test_count   : {payload['authored_test_count']}")
    typer.echo(f"next_action           : {payload['next_action']}")
    typer.echo(f"branch_runtime_root   : {payload['branch_runtime_root']}")
    typer.echo(f"message               : {payload['message']}")
    if payload["allowed_skills"]:
        typer.echo("allowed_skills:")
        for item in payload["allowed_skills"]:
            typer.echo(f"  - {item}")
    if payload["allowed_plugins"]:
        typer.echo("allowed_plugins:")
        for item in payload["allowed_plugins"]:
            typer.echo(f"  - {item}")


def _print_test_execution_result(result: object) -> None:
    payload = result.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo(f"action                : {payload['action']}")
    typer.echo(f"branch                : {payload['branch_name']}")
    typer.echo(f"ticket                : {payload['ticket_key'] or '(missing)'}")
    typer.echo(f"active_run_key        : {payload['active_run_key']}")
    typer.echo(f"status                : {payload['status']}")
    typer.echo(f"current_stage         : {payload['current_stage']}")
    typer.echo(f"execution_agent       : {payload['execution_agent']}")
    typer.echo(f"execution_mode        : {payload['execution_mode']}")
    typer.echo(f"next_action           : {payload['next_action']}")
    typer.echo(f"branch_runtime_root   : {payload['branch_runtime_root']}")
    typer.echo(f"message               : {payload['message']}")
    if payload["allowed_skills"]:
        typer.echo("allowed_skills:")
        for item in payload["allowed_skills"]:
            typer.echo(f"  - {item}")
    if payload["allowed_plugins"]:
        typer.echo("allowed_plugins:")
        for item in payload["allowed_plugins"]:
            typer.echo(f"  - {item}")
    if payload["executed_tests"]:
        typer.echo("executed_tests:")
        for item in payload["executed_tests"]:
            typer.echo(f"  - {item}")
    if payload["failed_tests"]:
        typer.echo("failed_tests:")
        for item in payload["failed_tests"]:
            typer.echo(f"  - {item}")


def _print_binary_validation_result(result: object) -> None:
    payload = result.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo(f"action                : {payload['action']}")
    typer.echo(f"branch                : {payload['branch_name']}")
    typer.echo(f"ticket                : {payload['ticket_key'] or '(missing)'}")
    typer.echo(f"active_run_key        : {payload['active_run_key']}")
    typer.echo(f"status                : {payload['status']}")
    typer.echo(f"current_stage         : {payload['current_stage']}")
    typer.echo(f"validation_agent      : {payload['validation_agent']}")
    typer.echo(f"package_root          : {payload['package_root']}")
    typer.echo(f"installed_cli_version : {payload['installed_cli_version']}")
    typer.echo(f"next_action           : {payload['next_action']}")
    typer.echo(f"branch_runtime_root   : {payload['branch_runtime_root']}")
    typer.echo(f"message               : {payload['message']}")
    if payload["allowed_skills"]:
        typer.echo("allowed_skills:")
        for item in payload["allowed_skills"]:
            typer.echo(f"  - {item}")
    if payload["allowed_plugins"]:
        typer.echo("allowed_plugins:")
        for item in payload["allowed_plugins"]:
            typer.echo(f"  - {item}")
    if payload["smoke_commands"]:
        typer.echo("smoke_commands:")
        for item in payload["smoke_commands"]:
            typer.echo(f"  - {item}")


def _print_release_readiness_result(result: object) -> None:
    payload = result.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo(f"action                : {payload['action']}")
    typer.echo(f"branch                : {payload['branch_name']}")
    typer.echo(f"ticket                : {payload['ticket_key'] or '(missing)'}")
    typer.echo(f"active_run_key        : {payload['active_run_key']}")
    typer.echo(f"status                : {payload['status']}")
    typer.echo(f"current_stage         : {payload['current_stage']}")
    typer.echo(f"readiness_agent       : {payload['readiness_agent']}")
    typer.echo(f"next_action           : {payload['next_action']}")
    typer.echo(f"branch_runtime_root   : {payload['branch_runtime_root']}")
    typer.echo(f"message               : {payload['message']}")
    if payload["allowed_skills"]:
        typer.echo("allowed_skills:")
        for item in payload["allowed_skills"]:
            typer.echo(f"  - {item}")
    if payload["allowed_plugins"]:
        typer.echo("allowed_plugins:")
        for item in payload["allowed_plugins"]:
            typer.echo(f"  - {item}")
    if payload["blocking_findings"]:
        typer.echo("blocking_findings:")
        for item in payload["blocking_findings"]:
            typer.echo(f"  - {item}")


def _print_prerelease_result(result: object) -> None:
    payload = result.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    typer.echo(f"action                : {payload['action']}")
    typer.echo(f"branch                : {payload['branch_name']}")
    typer.echo(f"ticket                : {payload['ticket_key'] or '(missing)'}")
    typer.echo(f"active_run_key        : {payload['active_run_key']}")
    typer.echo(f"status                : {payload['status']}")
    typer.echo(f"current_stage         : {payload['current_stage']}")
    typer.echo(f"prerelease_agent      : {payload['prerelease_agent']}")
    typer.echo(f"prerelease_tag        : {payload['prerelease_tag']}")
    typer.echo(f"prerelease_url        : {payload['prerelease_url'] or '(not created)'}")
    typer.echo(f"blocked_reason        : {payload['blocked_reason'] or '(none)'}")
    typer.echo(f"next_action           : {payload['next_action']}")
    typer.echo(f"branch_runtime_root   : {payload['branch_runtime_root']}")
    typer.echo(f"message               : {payload['message']}")
    if payload["allowed_skills"]:
        typer.echo("allowed_skills:")
        for item in payload["allowed_skills"]:
            typer.echo(f"  - {item}")
    if payload["allowed_plugins"]:
        typer.echo("allowed_plugins:")
        for item in payload["allowed_plugins"]:
            typer.echo(f"  - {item}")


def _print_release_notes_result(result: object) -> None:
    payload = result.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"branch                : {payload['branch_name']}")
    typer.echo(f"active_run_key        : {payload['active_run_key']}")
    typer.echo(f"status                : {payload['status']}")
    typer.echo(f"current_stage         : {payload['current_stage']}")
    typer.echo(f"release_agent         : {payload['release_agent']}")
    typer.echo(f"notes_path            : {payload['notes_path']}")
    typer.echo(f"freshness_commit      : {payload['freshness_commit'] or '(none)'}")
    typer.echo(f"blocked_reason        : {payload['blocked_reason'] or '(none)'}")
    typer.echo(f"next_action           : {payload['next_action']}")
    typer.echo(f"message               : {payload['message']}")


def _print_pr_integration_result(result: object) -> None:
    payload = result.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"branch                : {payload['branch_name']}")
    typer.echo(f"active_run_key        : {payload['active_run_key']}")
    typer.echo(f"status                : {payload['status']}")
    typer.echo(f"current_stage         : {payload['current_stage']}")
    typer.echo(f"integration_agent     : {payload['integration_agent']}")
    typer.echo(f"pr_link               : {payload['pr_link'] or '(missing)'}")
    typer.echo(f"blocked_reason        : {payload['blocked_reason'] or '(none)'}")
    typer.echo(f"next_action           : {payload['next_action']}")
    typer.echo(f"message               : {payload['message']}")


def _print_historian_result(result: object) -> None:
    payload = result.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"branch                : {payload['branch_name']}")
    typer.echo(f"active_run_key        : {payload['active_run_key']}")
    typer.echo(f"status                : {payload['status']}")
    typer.echo(f"current_stage         : {payload['current_stage']}")
    typer.echo(f"historian_agent       : {payload['historian_agent']}")
    typer.echo(f"final_status          : {payload['final_status']}")
    typer.echo(f"next_action           : {payload['next_action']}")
    typer.echo(f"message               : {payload['message']}")


def _print_finalize_result(result: object) -> None:
    payload = result.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"branch                : {payload['branch_name']}")
    typer.echo(f"active_run_key        : {payload['active_run_key']}")
    typer.echo(f"status                : {payload['status']}")
    typer.echo(f"runtime_removed       : {payload['runtime_removed']}")
    typer.echo(f"archive_path          : {payload['archive_path']}")
    typer.echo(f"memory_summary_path   : {payload['memory_summary_path']}")
    typer.echo(f"memory_receipt_path   : {payload['memory_receipt_path']}")
    typer.echo(f"retention_days        : {payload['retention_days']}")
    typer.echo(f"next_action           : {payload['next_action']}")
    typer.echo(f"message               : {payload['message']}")
    if payload["purged_archives"]:
        typer.echo("purged_archives:")
        for item in payload["purged_archives"]:
            typer.echo(f"  - {item}")


def _print_verify_merge_hygiene_result(result: object) -> None:
    payload = result.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"branch                : {payload['branch_name']}")
    typer.echo(f"status                : {payload['status']}")
    typer.echo(f"merge_safe            : {payload['merge_safe']}")
    typer.echo(f"branch_runtime_mode   : {payload['branch_runtime_mode']}")
    typer.echo(f"runtime_root          : {payload['runtime_root']}")
    typer.echo(f"memory_receipt_path   : {payload['memory_receipt_path']}")
    typer.echo(f"memory_summary_path   : {payload['memory_summary_path']}")
    typer.echo(f"message               : {payload['message']}")
    if payload["warnings"]:
        typer.echo("warnings:")
        for item in payload["warnings"]:
            typer.echo(f"  - {item}")


def _print_subagent_scenario_result(result: object) -> None:
    payload = result.to_dict()
    if cli_ctx.json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"scenario_id           : {payload['scenario_id']}")
    typer.echo(f"provider_plugin       : {payload['provider_plugin']}")
    typer.echo(f"requested_host        : {payload['requested_host']}")
    typer.echo(f"effective_provider    : {payload['effective_provider']}")
    typer.echo(f"fallback_used         : {payload['fallback_used']}")
    typer.echo(f"executed              : {payload['executed']}")
    typer.echo(f"packet_count          : {len(payload['packets'])}")
    if payload["outputs"]:
        typer.echo("outputs:")
        for item in payload["outputs"]:
            typer.echo(f"  - {item['agent_id']}")
