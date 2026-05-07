# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from platform_cli.core.config import get_bool
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.decorators import command_meta, requires_capability, tracked_command
from platform_cli.tools.create_branch_service import BranchCreateRequest, create_branch
from platform_cli.tools.create_branch_workflow_adapter import write_branch_outputs_if_supported


def register(root_app: typer.Typer) -> None:
    @root_app.command("create-branch")
    @tracked_command("branch.create")
    @requires_capability("branch.create", team_kwarg=None)
    @command_meta(
        name="create-branch",
        category="git",
        description="Create a feature branch directly on GitHub, validate Jira, and manage repo intent.",
        tags=["git", "branch", "jira", "intent"],
    )
    def create_branch_cmd(
        branch: Optional[str] = typer.Argument(
            None,
            help="Shorthand like feature/EPPE-6654-ENHANCEMENT-branch-orchestration or EPPE-6654-ENHANCEMENT-branch-orchestration.",
        ),
        branch_type: Optional[str] = typer.Option(None, "--type", help="TECHNICAL | ENHANCEMENT | BUGFIX."),
        ticket: Optional[str] = typer.Option(None, "--ticket", help="Jira key like EPPE-6654."),
        slug: Optional[str] = typer.Option(None, "--slug", help="User-owned branch slug."),
        base_branch: Optional[str] = typer.Option(None, "--base", help="Base branch. Defaults to the repo default branch."),
        repo: Optional[str] = typer.Option(None, "--repo", help="Optional OWNER/REPO. If omitted, GHDP resolves it from local repo context."),
        validate_jira: bool = typer.Option(False, "--validate-jira", help="Enforce Jira validation before branch creation."),
        no_validate_jira: bool = typer.Option(False, "--no-validate-jira", help="Skip Jira validation for this run."),
        comment_on_jira: bool = typer.Option(False, "--comment-on-jira", help="Post a Jira comment after branch creation."),
        no_comment_on_jira: bool = typer.Option(False, "--no-comment-on-jira", help="Disable Jira commenting for this run."),
        intent_mode: str = typer.Option("auto", "--intent-mode", help="auto | generate | provided | skip"),
        intent_text: Optional[str] = typer.Option(None, "--intent-text", help="Intent text to persist when --intent-mode provided is used."),
        intent_file: Optional[Path] = typer.Option(None, "--intent-file", help="Path to a file containing intent text for --intent-mode provided."),
        provider: str = typer.Option("auto", "--provider", help="auto | manual | codex | claude"),
        request_id: Optional[str] = typer.Option(None, "--request-id", help="Optional request id for workflow/comment correlation."),
        local_checkout: bool = typer.Option(True, "--local-checkout/--no-local-checkout", help="Check out the created branch locally when safe."),
        persist_intent: bool = typer.Option(True, "--persist-intent/--no-persist-intent", help="Persist repo intent when intent text is available."),
        commit_intent: bool = typer.Option(False, "--commit-intent/--no-commit-intent", help="Commit and push the persisted repo intent file."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Resolve the branch plan and optional intent prompt without creating the branch."),
        github_output: Optional[Path] = typer.Option(None, "--github-output", help="Optional path to write GitHub Actions outputs."),
        intent_prompt_file: Optional[Path] = typer.Option(None, "--intent-prompt-file", help="Optional file path to write the rendered AI intent prompt."),
    ) -> None:
        resolved_branch, resolved_type, resolved_ticket, resolved_slug, resolved_repo, resolved_mode, resolved_intent = _resolve_cli_inputs(
            branch=branch,
            branch_type=branch_type,
            ticket=ticket,
            slug=slug,
            repo=repo,
            provider=provider,
            intent_mode=intent_mode,
            intent_text=_load_intent_text(intent_text=intent_text, intent_file=intent_file),
        )
        _run_create_branch(
            BranchCreateRequest(
                branch=resolved_branch,
                branch_type=resolved_type,
                ticket=resolved_ticket,
                slug=resolved_slug,
                base_branch=base_branch,
                repo=resolved_repo,
                validate_jira=_resolve_optional_flag(validate_jira, no_validate_jira, option_name="validate-jira"),
                comment_on_jira=_resolve_optional_flag(comment_on_jira, no_comment_on_jira, option_name="comment-on-jira"),
                intent_mode=resolved_mode,
                intent_text=resolved_intent,
                provider=provider,
                request_id=request_id,
                local_checkout=local_checkout,
                persist_intent=persist_intent,
                commit_intent=commit_intent,
                dry_run=dry_run,
                intent_prompt_file=str(intent_prompt_file) if intent_prompt_file else None,
            ),
            github_output=str(github_output) if github_output else None,
        )

    @root_app.command("crbr")
    @tracked_command("branch.create")
    @requires_capability("branch.create", team_kwarg=None)
    @command_meta(
        name="crbr",
        category="git",
        description="Alias for create-branch.",
        tags=["git", "branch", "jira", "intent", "alias"],
    )
    def crbr(
        branch: Optional[str] = typer.Argument(None),
        branch_type: Optional[str] = typer.Option(None, "--type"),
        ticket: Optional[str] = typer.Option(None, "--ticket"),
        slug: Optional[str] = typer.Option(None, "--slug"),
        base_branch: Optional[str] = typer.Option(None, "--base"),
        repo: Optional[str] = typer.Option(None, "--repo"),
        validate_jira: bool = typer.Option(False, "--validate-jira"),
        no_validate_jira: bool = typer.Option(False, "--no-validate-jira"),
        comment_on_jira: bool = typer.Option(False, "--comment-on-jira"),
        no_comment_on_jira: bool = typer.Option(False, "--no-comment-on-jira"),
        intent_mode: str = typer.Option("auto", "--intent-mode"),
        intent_text: Optional[str] = typer.Option(None, "--intent-text"),
        intent_file: Optional[Path] = typer.Option(None, "--intent-file"),
        provider: str = typer.Option("auto", "--provider"),
        request_id: Optional[str] = typer.Option(None, "--request-id"),
        local_checkout: bool = typer.Option(True, "--local-checkout/--no-local-checkout"),
        persist_intent: bool = typer.Option(True, "--persist-intent/--no-persist-intent"),
        commit_intent: bool = typer.Option(False, "--commit-intent/--no-commit-intent"),
        dry_run: bool = typer.Option(False, "--dry-run"),
        github_output: Optional[Path] = typer.Option(None, "--github-output"),
        intent_prompt_file: Optional[Path] = typer.Option(None, "--intent-prompt-file"),
    ) -> None:
        resolved_branch, resolved_type, resolved_ticket, resolved_slug, resolved_repo, resolved_mode, resolved_intent = _resolve_cli_inputs(
            branch=branch,
            branch_type=branch_type,
            ticket=ticket,
            slug=slug,
            repo=repo,
            provider=provider,
            intent_mode=intent_mode,
            intent_text=_load_intent_text(intent_text=intent_text, intent_file=intent_file),
        )
        _run_create_branch(
            BranchCreateRequest(
                branch=resolved_branch,
                branch_type=resolved_type,
                ticket=resolved_ticket,
                slug=resolved_slug,
                base_branch=base_branch,
                repo=resolved_repo,
                validate_jira=_resolve_optional_flag(validate_jira, no_validate_jira, option_name="validate-jira"),
                comment_on_jira=_resolve_optional_flag(comment_on_jira, no_comment_on_jira, option_name="comment-on-jira"),
                intent_mode=resolved_mode,
                intent_text=resolved_intent,
                provider=provider,
                request_id=request_id,
                local_checkout=local_checkout,
                persist_intent=persist_intent,
                commit_intent=commit_intent,
                dry_run=dry_run,
                intent_prompt_file=str(intent_prompt_file) if intent_prompt_file else None,
            ),
            github_output=str(github_output) if github_output else None,
        )


def _run_create_branch(request: BranchCreateRequest, *, github_output: str | None) -> None:
    result = create_branch(request)
    write_branch_outputs_if_supported(result, explicit_path=github_output)

    if cli_ctx.json:
        typer.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return

    typer.echo(f"repo            : {result.repo}")
    typer.echo(f"ticket          : {result.ticket}")
    typer.echo(f"type            : {result.branch_type}")
    typer.echo(f"slug            : {result.slug}")
    typer.echo(f"base            : {result.base_branch}")
    typer.echo(f"branch          : {result.branch_name}")
    typer.echo(f"request_id      : {result.request_id}")
    typer.echo(f"branch_created  : {result.branch_created}")
    if result.jira_validated:
        typer.echo("jira            : validated")
    elif result.jira_warning:
        typer.echo(f"jira            : {result.jira_warning}")
    if result.intent_provider:
        typer.echo(f"intent_provider : {result.intent_provider}")
        typer.echo(f"intent_saved    : {result.intent_saved}")
        if result.intent_path:
            typer.echo(f"intent_path     : {result.intent_path}")
        typer.echo(f"intent_committed: {result.intent_committed}")
    typer.echo(f"jira_commented  : {result.jira_comment_posted}")
    typer.echo(f"checkout        : {result.local_checkout_message}")


def _load_intent_text(*, intent_text: Optional[str], intent_file: Optional[Path]) -> Optional[str]:
    text = (intent_text or "").strip()
    if intent_file is None:
        return text or None
    file_text = intent_file.read_text(encoding="utf-8").strip()
    if text and file_text and text != file_text:
        raise typer.BadParameter("Use either --intent-text or --intent-file, not both with different values.")
    return file_text or text or None


def _resolve_optional_flag(enabled: bool, disabled: bool, *, option_name: str) -> bool | None:
    if enabled and disabled:
        raise typer.BadParameter(f"Use either --{option_name} or --no-{option_name}, not both.")
    if enabled:
        return True
    if disabled:
        return False
    return None


def _resolve_cli_inputs(
    *,
    branch: Optional[str],
    branch_type: Optional[str],
    ticket: Optional[str],
    slug: Optional[str],
    repo: Optional[str],
    provider: str,
    intent_mode: str,
    intent_text: Optional[str],
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str], str, Optional[str]]:
    resolved_branch = branch
    resolved_type = branch_type
    resolved_ticket = ticket
    resolved_slug = slug
    resolved_repo = repo
    resolved_mode = intent_mode
    resolved_intent = intent_text
    prompt_for_missing = get_bool("branch.create.prompt_for_missing", True)

    if not cli_ctx.non_interactive and prompt_for_missing and not resolved_branch:
        if not resolved_ticket:
            resolved_ticket = typer.prompt("Jira ticket key (for example: EPPE-1234)").strip().upper()
        if not resolved_type:
            resolved_type = typer.prompt("Branch type: technical, enhancement, or bugfix").strip()
        if not resolved_slug:
            resolved_slug = typer.prompt(
                "Branch slug in simple hyphenated words (for example: update-login-flow)"
            ).strip()

    if not cli_ctx.non_interactive and (provider or "").strip().lower() == "manual" and not resolved_intent:
        resolved_intent = typer.prompt("Branch intent").strip()
        resolved_mode = "provided"

    return (
        resolved_branch,
        resolved_type,
        resolved_ticket,
        resolved_slug,
        resolved_repo,
        resolved_mode,
        resolved_intent,
    )
