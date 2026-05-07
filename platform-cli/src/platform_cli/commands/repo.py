# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

import json
from pathlib import Path

import typer

from platform_cli.core.access import ensure_capability
from platform_cli.core.config import get_bool, get_value
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.decorators import command_meta, requires_capability, requires_release_gate, tracked_command
from platform_cli.core.errors import PlatformError
from platform_cli.core.progress import GenerationProgressReporter
from platform_cli.core.output import prompt_guided_choice
from platform_cli.tools.ai_provider import detect_provider_statuses, select_provider
from platform_cli.tools.repo_ready_adapters import RepoReadyAdapterSyncResult, sync_repo_local_adapters
from platform_cli.tools.repo_jenkins_contract import (
    JenkinsContractSyncResult,
    ensure_repo_jenkins_contract,
    inspect_repo_jenkins_contract,
)
from platform_cli.tools.repo_ready import (
    RepoReadyResult,
    accept_repo_ready_reviews,
    assess_repo_ready,
    write_repo_readiness_report,
)
from platform_cli.tools.repo_ready_generation import (
    RepoReadyDraftResult,
    SUPPORTED_DRAFT_TARGETS,
    generate_repo_ready_drafts,
)

repo_app = typer.Typer(help="Repo governance readiness utilities.", invoke_without_command=True)


def register(root_app: typer.Typer) -> None:
    root_app.add_typer(repo_app, name="repo")


def _print_result(result: RepoReadyResult) -> None:
    typer.echo("GHDP repo")
    typer.echo(f"repo_root: {result.repo_root}")
    typer.echo(f"mode: {result.mode}")
    typer.echo(f"template_version: {result.template_version}")
    typer.echo(f"summary: {'READY' if result.ready else 'NOT READY'}")

    if result.created:
        typer.echo("created:")
        for rel_path in result.created:
            typer.echo(f"  - {rel_path}")

    if result.missing_required:
        typer.echo("missing required:")
        for rel_path in result.missing_required:
            typer.echo(f"  - {rel_path}")

    if result.invalid_required:
        typer.echo("invalid required:")
        for rel_path in result.invalid_required:
            typer.echo(f"  - {rel_path}")

    if result.pending_required:
        typer.echo("pending user input:")
        for rel_path in result.pending_required:
            typer.echo(f"  - {rel_path}")

    if result.missing_required_adapters:
        typer.echo("missing required adapters:")
        for rel_path in result.missing_required_adapters:
            typer.echo(f"  - {rel_path}")

    if result.pending_required_adapters:
        typer.echo("pending required adapters:")
        for rel_path in result.pending_required_adapters:
            typer.echo(f"  - {rel_path}")

    if result.recommended_missing:
        typer.echo("recommended missing:")
        for rel_path in result.recommended_missing:
            typer.echo(f"  - {rel_path}")

    if result.pending_recommended:
        typer.echo("recommended pending user input:")
        for rel_path in result.pending_recommended:
            typer.echo(f"  - {rel_path}")

    if result.warnings:
        typer.echo("warnings:")
        for warning in result.warnings:
            typer.echo(f"  - {warning}")

    typer.echo("files:")
    for item in result.files:
        suffix = f" ({'; '.join(item.messages)})" if item.messages else ""
        typer.echo(f"  - {item.rel_path}: {item.status}{suffix}")

    if result.adapters:
        typer.echo("adapters:")
        for item in result.adapters:
            suffix_parts = list(item.messages)
            if item.required_by_enabled_tools:
                suffix_parts.append("required by enabled tools")
            suffix = f" ({'; '.join(suffix_parts)})" if suffix_parts else ""
            typer.echo(f"  - {item.rel_path}: {item.state}{suffix}")


def _run_repo_mode(mode: str, repo_root: Path | None = None) -> RepoReadyResult:
    result = assess_repo_ready(mode=mode, repo_root=repo_root)
    write_repo_readiness_report(result)

    if cli_ctx.json:
        typer.echo(result.to_json())
    else:
        _print_result(result)

    return result


def _assess_repo_mode_silently(mode: str, repo_root: Path | None = None) -> RepoReadyResult:
    result = assess_repo_ready(mode=mode, repo_root=repo_root)
    write_repo_readiness_report(result)
    return result


def _repo_ai_provider() -> str:
    return str(get_value("repo.ai.provider", "auto") or "auto").strip().lower()


def _repo_ai_refresh_on_missing() -> bool:
    return bool(get_bool("repo.ai.refresh_on_missing", True))


def _resolve_ready_mode(*, report: bool, fix: bool, verify: bool) -> str:
    enabled = [name for name, selected in (("report", report), ("fix", fix), ("verify", verify)) if selected]
    if len(enabled) > 1:
        raise PlatformError(
            "Use only one of `--report`, `--fix`, or `--verify` with `ghdp repo ready`.",
            code="E_REPO_READY_MODE_CONFLICT",
            reason="ready_mode_conflict",
        )
    return enabled[0] if enabled else "report"


def _default_guided_action(result: RepoReadyResult) -> str:
    if result.missing_required or result.invalid_required or result.missing_required_adapters:
        return "fix"
    if result.pending_required or result.pending_required_adapters:
        return "exit"
    return "verify"


_REPO_ACTION_CHOICES: tuple[tuple[str, str], ...] = (
    ("report", "report  Inspect repo readiness without writing files"),
    ("fix", "fix     Scaffold missing readiness files"),
    ("accept", "accept  Mark reviewed GHDP drafts as confirmed"),
    ("verify", "verify  Validate required files for CI"),
)
_REPO_ACTION_ALIASES: dict[str, str] = {
    "1": "report",
    "report": "report",
    "2": "fix",
    "fix": "fix",
    "3": "accept",
    "accept": "accept",
    "4": "verify",
    "verify": "verify",
}


def _run_guided_action_loop(*, initial_result: RepoReadyResult, repo_root: Path | None = None) -> RepoReadyResult:
    result = initial_result
    action = prompt_guided_choice(
        title="available repo commands:",
        prompt_text="Choose the next repo action",
        choices=_REPO_ACTION_CHOICES,
        default=_default_guided_action(result),
        aliases=_REPO_ACTION_ALIASES,
        prompt_fn=typer.prompt,
        echo_fn=typer.echo,
        invalid_message="Unknown action. Choose one of: report, fix, accept, verify.",
    )

    typer.echo("")
    if action == "fix":
        return _run_repo_fix_flow(repo_root=repo_root)
    if action == "accept":
        return _run_repo_accept_flow(repo_root=repo_root)
    return _run_repo_mode(action, repo_root=repo_root)


def _has_ready_mode_selection(*, report: bool, fix: bool, verify: bool) -> bool:
    return any((report, fix, verify))


def _draft_targets(result: RepoReadyResult) -> list[str]:
    supported = set(SUPPORTED_DRAFT_TARGETS)
    ordered = result.pending_required + result.pending_recommended
    return [rel_path for rel_path in ordered if rel_path in supported]


def _print_draft_result(result: RepoReadyDraftResult) -> None:
    typer.echo(f"suggested drafts via {result.provider}:")
    if result.generated:
        typer.echo("generated:")
        for rel_path in result.generated:
            typer.echo(f"  - {rel_path}")
    if result.failed:
        typer.echo("generation failed:")
        for rel_path, message in result.failed.items():
            typer.echo(f"  - {rel_path}: {message}")
    if result.warnings:
        typer.echo("warnings:")
        for warning in result.warnings:
            typer.echo(f"  - {warning}")


def _print_adapter_sync_result(result: RepoReadyAdapterSyncResult) -> None:
    typer.echo("repo-local adapters:")
    if result.generated:
        typer.echo("generated:")
        for rel_path in result.generated:
            typer.echo(f"  - {rel_path}")
    if result.updated:
        typer.echo("updated:")
        for rel_path in result.updated:
            typer.echo(f"  - {rel_path}")
    if result.warnings:
        typer.echo("warnings:")
        for warning in result.warnings:
            typer.echo(f"  - {warning}")


def _print_jenkins_contract_result(result: JenkinsContractSyncResult) -> None:
    typer.echo("jenkins contract:")
    typer.echo(f"  - {result.rel_path}: {result.status} ({result.message})")


def _verification_failure_message(result: RepoReadyResult) -> str:
    details = []
    if result.missing_required:
        details.append(f"missing required files: {', '.join(result.missing_required)}")
    if result.invalid_required:
        details.append(f"invalid required files: {', '.join(result.invalid_required)}")
    if result.pending_required:
        details.append(f"pending required files: {', '.join(result.pending_required)}")
    if result.missing_required_adapters:
        details.append(f"missing required adapters: {', '.join(result.missing_required_adapters)}")
    if result.pending_required_adapters:
        details.append(f"pending required adapters: {', '.join(result.pending_required_adapters)}")

    message = "Repo readiness verification failed."
    if details:
        message += " " + " ".join(details)
    return message


def _run_repo_jenkins_contract_flow(*, repo_root: Path | None = None, refresh: bool) -> None:
    resolved_root = Path(repo_root).resolve() if repo_root is not None else None
    result = ensure_repo_jenkins_contract(resolved_root or Path.cwd(), refresh=refresh)
    if not cli_ctx.json:
        _print_jenkins_contract_result(result)
        return
    typer.echo(
        json.dumps(
            {
            "rel_path": result.rel_path,
            "abs_path": result.abs_path,
            "status": result.status,
            "message": result.message,
            "branch_name": result.branch_name,
            "source_hash": result.source_hash,
            },
            indent=2,
            sort_keys=True,
        )
    )


def _run_repo_fix_flow(repo_root: Path | None = None) -> RepoReadyResult:
    ensure_capability("repo.fix", command_name="repo fix")
    progress = GenerationProgressReporter()
    progress.phase("Assessing readiness and scaffolding base files...")
    result = _run_repo_mode("fix", repo_root=repo_root)
    resolved_root = Path(result.repo_root)
    statuses = None

    targets = _draft_targets(result)
    allow_ai = not cli_ctx.json and not cli_ctx.non_interactive
    if allow_ai and targets:
        progress.phase("Selecting AI provider and planning draft generation...")
        provider, statuses = select_provider(
            preferred=_repo_ai_provider(),
            interactive=True,
            refresh_on_missing=_repo_ai_refresh_on_missing(),
            persist_key="repo.ai.provider",
        )
        if provider == "manual":
            typer.echo("")
            typer.echo("No Claude/Codex provider is currently available. Review the pending files manually.")
        else:
            typer.echo("")
            confirmed = typer.confirm(
                f"Generate suggested drafts for pending repo files with {provider}? ({', '.join(targets)})",
                default=True,
            )
            if confirmed:
                progress.phase("Generating suggested drafts...")
                draft_result = generate_repo_ready_drafts(
                    repo_root=resolved_root,
                    provider=provider,
                    statuses=statuses,
                    targets=targets,
                    confirmed_tools=[],
                    progress=progress,
                )
                typer.echo("")
                _print_draft_result(draft_result)
                typer.echo("")
    elif allow_ai:
        progress.phase("Checking AI provider availability...")
        statuses = detect_provider_statuses(refresh=_repo_ai_refresh_on_missing())

    progress.phase("Syncing repo-local adapters...")
    adapter_result = sync_repo_local_adapters(
        repo_root=resolved_root,
        statuses=statuses,
        allow_ai=allow_ai,
        progress=progress,
    )
    if not cli_ctx.json:
        typer.echo("")
        _print_adapter_sync_result(adapter_result)
        typer.echo("")

    inspection = inspect_repo_jenkins_contract(resolved_root)
    if inspection.jenkinsfile_exists:
        progress.phase("Refreshing repo-local Jenkins contract...")
        jenkins_contract_result = ensure_repo_jenkins_contract(resolved_root, refresh=False)
        if not cli_ctx.json:
            typer.echo("")
            _print_jenkins_contract_result(jenkins_contract_result)
            typer.echo("")

    progress.phase("Rechecking repo readiness...")
    return _run_repo_mode("report", repo_root=repo_root)


def _run_repo_accept_flow(repo_root: Path | None = None) -> RepoReadyResult:
    ensure_capability("repo.accept", command_name="repo accept")
    changed = accept_repo_ready_reviews(repo_root=repo_root)
    if not cli_ctx.json:
        typer.echo("")
        if changed:
            typer.echo("accepted review markers:")
            for rel_path in changed:
                typer.echo(f"  - {rel_path}")
        else:
            typer.echo("No review markers were updated.")
        typer.echo("")
    return _run_repo_mode("report", repo_root=repo_root)


@repo_app.callback(invoke_without_command=True)
def repo_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return

    if cli_ctx.non_interactive:
        raise PlatformError(
            "No repo action was provided. Use `ghdp repo ready --report`, `ghdp repo ready --fix`, or `ghdp repo ready --verify`.",
            code="E_REPO_ACTION_REQUIRED",
            reason="missing_repo_subcommand",
        )

    result = _run_repo_mode("report")
    if cli_ctx.json:
        return
    _run_guided_action_loop(initial_result=result)


@repo_app.command("ready")
@tracked_command("repo ready")
@requires_capability("platform.internal", team_kwarg=None)
@requires_release_gate(command_name="repo ready", allow_admin_bypass=False, team_kwarg=None)
@command_meta(
    name="repo ready",
    category="repo",
    description="Run GHDP repo readiness in report, fix, or verify mode.",
    tags=["repo", "readiness", "governance"],
)
def repo_ready(
    report: bool = typer.Option(False, "--report", help="Read-only readiness report."),
    fix: bool = typer.Option(False, "--fix", help="Scaffold missing files and apply safe updates."),
    verify: bool = typer.Option(False, "--verify", help="Exit non-zero if the repo is not compliant."),
    fix_jenkins_contract: bool = typer.Option(
        False,
        "--fix-jenkins-contract",
        help="Create the repo-local Jenkins contract from Jenkinsfile if it is missing or invalid.",
    ),
    refresh_jenkins_contract: bool = typer.Option(
        False,
        "--refresh-jenkins-contract",
        help="Force-refresh the repo-local Jenkins contract from the current Jenkinsfile.",
    ),
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Advanced repo root override. Defaults to the current git repository root.",
    ),
) -> None:
    if fix_jenkins_contract and refresh_jenkins_contract:
        raise PlatformError(
            "Use only one of `--fix-jenkins-contract` or `--refresh-jenkins-contract`.",
            code="E_REPO_JENKINS_CONTRACT_MODE_CONFLICT",
            reason="jenkins_contract_mode_conflict",
        )
    if fix_jenkins_contract or refresh_jenkins_contract:
        if report or fix or verify:
            raise PlatformError(
                "Use Jenkins contract flags on their own without `--report`, `--fix`, or `--verify`.",
                code="E_REPO_JENKINS_CONTRACT_MODE_CONFLICT",
                reason="jenkins_contract_mode_conflict",
            )
        _run_repo_jenkins_contract_flow(repo_root=repo_root, refresh=refresh_jenkins_contract)
        return

    if (
        not _has_ready_mode_selection(report=report, fix=fix, verify=verify)
        and not cli_ctx.non_interactive
        and not cli_ctx.json
    ):
        initial_result = _assess_repo_mode_silently("report", repo_root=repo_root)
        _run_guided_action_loop(initial_result=initial_result, repo_root=repo_root)
        return

    mode = _resolve_ready_mode(report=report, fix=fix, verify=verify)
    result = _run_repo_fix_flow(repo_root=repo_root) if mode == "fix" else _run_repo_mode(mode, repo_root=repo_root)
    if mode == "verify" and not result.compliant:
        raise PlatformError(
            _verification_failure_message(result),
            code="E_REPO_NOT_READY",
            reason="missing_invalid_or_pending_repo_files",
        )


@repo_app.command("report")
@tracked_command("repo report")
@requires_capability("platform.internal", team_kwarg=None)
@requires_release_gate(command_name="repo report", allow_admin_bypass=False, team_kwarg=None)
@command_meta(
    name="repo report",
    category="repo",
    description="Report GHDP repo readiness without writing files.",
    tags=["repo", "readiness", "governance"],
)
def repo_report(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Advanced repo root override. Defaults to the current git repository root.",
    ),
) -> None:
    _run_repo_mode("report", repo_root=repo_root)


@repo_app.command("fix")
@tracked_command("repo fix")
@command_meta(
    name="repo fix",
    category="repo",
    description="Scaffold missing GHDP repo readiness files.",
    tags=["repo", "readiness", "fix"],
)
@requires_capability("repo.fix", team_kwarg=None)
def repo_fix(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Advanced repo root override. Defaults to the current git repository root.",
    ),
) -> None:
    _run_repo_fix_flow(repo_root=repo_root)


@repo_app.command("accept")
@tracked_command("repo accept")
@command_meta(
    name="repo accept",
    category="repo",
    description="Mark reviewed GHDP repo readiness drafts as confirmed.",
    tags=["repo", "readiness", "review"],
)
@requires_capability("repo.accept", team_kwarg=None)
def repo_accept(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Advanced repo root override. Defaults to the current git repository root.",
    ),
) -> None:
    _run_repo_accept_flow(repo_root=repo_root)


@repo_app.command("verify")
@tracked_command("repo verify")
@requires_capability("platform.internal", team_kwarg=None)
@requires_release_gate(command_name="repo verify", allow_admin_bypass=False, team_kwarg=None)
@command_meta(
    name="repo verify",
    category="repo",
    description="Verify GHDP repo readiness for CI and non-interactive flows.",
    tags=["repo", "readiness", "verify"],
)
def repo_verify(
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Advanced repo root override. Defaults to the current git repository root.",
    ),
) -> None:
    result = _run_repo_mode("verify", repo_root=repo_root)
    if not result.compliant:
        raise PlatformError(
            _verification_failure_message(result),
            code="E_REPO_NOT_READY",
            reason="missing_invalid_or_pending_repo_files",
        )
