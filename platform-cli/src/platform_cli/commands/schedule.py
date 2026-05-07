from __future__ import annotations

import json

import typer

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.decorators import command_meta, tracked_command
from platform_cli.core.errors import PlatformError
from platform_cli.core.live_status import command_status
from platform_cli.tools import scheduler, scheduler_assets


app = typer.Typer(help="Manage GHDP scheduled background jobs.", no_args_is_help=False, invoke_without_command=True)


def register(root_app: typer.Typer) -> None:
    root_app.add_typer(app, name="schedule")


def _require_auto_approve(auto_approve: bool, *, operation: str, prompt: str) -> None:
    if auto_approve:
        return
    if cli_ctx.non_interactive:
        raise PlatformError(
            f"Confirmation required for {operation} in non-interactive mode. Re-run with --auto-approve.",
            code="E_SCHEDULE_CONFIRM_REQUIRED",
            reason=operation.replace(" ", "_"),
        )
    if not typer.confirm(prompt):
        raise typer.Abort()


def _print_schedule_action_menu() -> None:
    typer.echo("available schedule commands:")
    typer.echo("  1. list    Show effective scheduler tasks and runtime status")
    typer.echo("  2. check   Show scheduler drift and needed actions")
    typer.echo("  3. apply   Create or update local scheduled tasks")
    typer.echo("  4. repair  Repair missing or drifted local scheduled tasks")
    typer.echo("  5. remove  Remove local scheduled tasks")
    typer.echo("  6. run     Force-run one scheduler task now")
    typer.echo("  7. exit    Leave scheduler without taking action")


def _prompt_schedule_action(*, default: str = "list") -> str:
    aliases = {
        "1": "list",
        "2": "check",
        "3": "apply",
        "4": "repair",
        "5": "remove",
        "6": "run",
        "7": "exit",
        "ls": "list",
        "rm": "remove",
    }

    while True:
        raw = typer.prompt("Choose the next scheduler action", default=default).strip().lower()
        action = aliases.get(raw, raw)
        if action in {"list", "check", "apply", "repair", "remove", "run", "exit"}:
            return action
        typer.echo("Unknown action. Choose one of: list, check, apply, repair, remove, run, exit.")


def _prompt_optional_task_id(*, label: str) -> str | None:
    raw = typer.prompt(label, default="").strip()
    return raw or None


def _prompt_required_task_id(*, label: str) -> str:
    while True:
        raw = typer.prompt(label).strip()
        if raw:
            return raw
        typer.echo("Task id is required for this action.")


def _asset_resolution_from_items(items: list[dict[str, object]]) -> dict[str, object] | None:
    if not items:
        return None
    first = items[0]
    return {
        "source_kind": first.get("asset_source_kind", "synced"),
        "materialization_state": first.get("asset_materialization_state", "cached"),
        "fallback_active": bool(first.get("asset_fallback_active")),
        "source_explanation": first.get("asset_source_explanation", ""),
    }


def _maybe_print_asset_source(items: list[dict[str, object]]) -> None:
    resolution = _asset_resolution_from_items(items)
    if resolution is None:
        return
    if scheduler_assets.should_surface_scheduler_asset_source(resolution, verbose=cli_ctx.verbose):
        typer.echo(f"asset source: {resolution['source_explanation']}")


def _print_schedule_apply_trust_summary(*, changed_count: int) -> None:
    summary = scheduler.build_schedule_apply_trust_summary(scope="user")
    _maybe_print_asset_source(summary["items"])
    typer.echo(f"Changed: {changed_count} task(s)")
    active_items = list(summary["active_items"])
    if active_items:
        active_text = ", ".join(f"{item['task_id']} every {item['interval_minutes']}m" for item in active_items)
        typer.echo(f"Active: {active_text}")
    else:
        typer.echo("Active: none")
    typer.echo(f"Logs: {summary['logs_path']}")
    auto_update = summary["auto_update_item"]
    if auto_update is None:
        typer.echo("Auto-update: no latest-stable auto-update task is active.")
    else:
        message = (
            f"{auto_update['task_id']} will check for the latest stable release every "
            f"{auto_update['interval_minutes']}m."
        )
        if auto_update["provider"] == "windows_task_scheduler":
            message += " On Windows, an installed update may finalize after the current GHDP process exits."
        typer.echo(f"Auto-update: {message}")
    if cli_ctx.verbose:
        providers = ", ".join(
            f"{item['task_id']}={item['provider']}"
            for item in active_items
        )
        if providers:
            typer.echo(f"Provider detail: {providers}")


def _run_guided_schedule_action(action: str) -> None:
    if action == "exit":
        typer.echo("Schedule command exited without changes.")
        return
    if action == "list":
        schedule_list(task_id=_prompt_optional_task_id(label="Task id filter (leave blank for all tasks)"))
        return
    if action == "check":
        schedule_check(task_id=_prompt_optional_task_id(label="Task id filter (leave blank for all tasks)"))
        return
    if action == "apply":
        task_id = _prompt_optional_task_id(label="Task id filter (leave blank for all tasks)")
        dry_run = typer.confirm("Preview only (--dry-run)?", default=False)
        auto_approve = False if dry_run else typer.confirm("Skip confirmation (--auto-approve)?", default=False)
        schedule_apply(task_id=task_id, auto_approve=auto_approve, dry_run=dry_run)
        return
    if action == "repair":
        task_id = _prompt_optional_task_id(label="Task id filter (leave blank for all tasks)")
        dry_run = typer.confirm("Preview only (--dry-run)?", default=False)
        auto_approve = False if dry_run else typer.confirm("Skip confirmation (--auto-approve)?", default=False)
        schedule_repair(task_id=task_id, auto_approve=auto_approve, dry_run=dry_run)
        return
    if action == "remove":
        task_id = _prompt_optional_task_id(label="Task id filter (leave blank for all tasks)")
        auto_approve = typer.confirm("Skip confirmation (--auto-approve)?", default=False)
        schedule_remove(task_id=task_id, auto_approve=auto_approve)
        return
    if action == "run":
        schedule_run(task_id=_prompt_required_task_id(label="Scheduler task id to execute"))
        return
    raise PlatformError(
        f"Unknown scheduler action '{action}'.",
        code="E_SCHEDULE_ACTION_INVALID",
        reason=action,
    )


@app.callback(invoke_without_command=True)
def schedule_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None or ctx.resilient_parsing:
        return
    if cli_ctx.non_interactive or cli_ctx.json:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)
    typer.echo("")
    _print_schedule_action_menu()
    _run_guided_schedule_action(_prompt_schedule_action(default="list"))
    raise typer.Exit(0)


@app.command("list")
@tracked_command("schedule list")
@command_meta(
    name="schedule list",
    category="schedule",
    description="List effective scheduler tasks and current runtime status.",
    tags=["schedule", "list", "status"],
)
def schedule_list(
    task_id: str | None = typer.Option(None, "--task-id", "--job-id", help="Limit results to one task id."),
) -> None:
    items = scheduler.list_schedule_jobs(scope="user", job_id=task_id)
    if not items:
        typer.echo("No scheduler tasks found.")
        return
    for item in items:
        typer.echo(
            f"{item['task_id']} status={item['status']} action={item['action']} every={item['interval_minutes']}m"
        )
        typer.echo(f"  args: {' '.join(item['ghdp_args'])}")
        typer.echo(f"  provider: {item['provider']} health={item['health_status']} last_run={item['last_run_at'] or '-'}")
        typer.echo(f"  asset: {item['artifact_path']}")


@app.command("check")
@tracked_command("schedule check")
@command_meta(
    name="schedule check",
    category="schedule",
    description="Check scheduler capability tasks against local scheduler state.",
    tags=["schedule", "check", "repair"],
)
def schedule_check(
    task_id: str | None = typer.Option(None, "--task-id", "--job-id", help="Limit results to one task id."),
) -> None:
    items = scheduler.list_schedule_jobs(scope="user", job_id=task_id)
    if not items:
        typer.echo("No scheduler tasks found.")
        return
    _maybe_print_asset_source(items)
    actionable = False
    for item in items:
        typer.echo(f"{item['task_id']} status={item['status']} action={item['action']}")
        if item["policy_error"]:
            typer.echo(f"  policy issue: {item['policy_error']}")
        if item["action"] != "none":
            actionable = True
    if not actionable:
        typer.echo("No scheduler reconciliation actions are needed.")


@app.command("apply")
@tracked_command("schedule apply")
@command_meta(
    name="schedule apply",
    category="schedule",
    description="Create or update scheduled tasks on the local machine with lightweight runtime status.",
    tags=["schedule", "apply", "scheduler"],
)
def schedule_apply(
    task_id: str | None = typer.Option(None, "--task-id", "--job-id", help="Limit results to one task id."),
    auto_approve: bool = typer.Option(False, "--auto-approve", "--yes", help="Apply without confirmation."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview scheduler apply actions without making changes."),
) -> None:
    status = command_status("schedule")
    status.update("Building scheduler apply preview...")
    preview = scheduler.preview_schedule_operation(scope="user", job_id=task_id, operation="apply")
    items = preview["items"]
    blockers = preview["readiness"]["blockers"]
    if blockers:
        names = ", ".join(str(item["task_id"]) for item in blockers)
        reasons = ", ".join(str(item["reason"]) for item in blockers if item["reason"])
        status.finish()
        raise PlatformError(
            f"Schedule apply is blocked by unsupported phase-1 policy for: {names}.",
            code="E_SCHEDULE_POLICY_UNSUPPORTED",
            reason=reasons or names,
        )
    candidates = [item for item in items if item["action"] in {"apply", "repair"}]
    if not candidates:
        status.finish("No scheduler apply actions are needed.")
        return
    # Clear transient status before the durable preview rows are printed.
    status.finish()
    for warning in preview["readiness"]["warnings"]:
        typer.echo(f"warning: {warning['task_id']} -> {warning['reason']}")
    for item in candidates:
        typer.echo(f"{item['task_id']} -> {item['action']} ({item['provider']})")
    if dry_run:
        status.finish()
        typer.echo(json.dumps(preview, indent=2, sort_keys=True))
        return
    try:
        status.update("Awaiting approval for scheduled task changes...")
        status.finish()
        _require_auto_approve(auto_approve, operation="schedule apply", prompt="Apply these scheduled task changes?")
    except typer.Abort:
        typer.echo("Schedule apply cancelled.")
        return
    status.update("Applying scheduled task changes...")
    results = scheduler.apply_schedule_jobs(scope="user", job_id=task_id)
    for item in results:
        typer.echo(f"Applied {item['task_id']} as {item['task_name']}")
    status.finish("Schedule apply complete.")
    _print_schedule_apply_trust_summary(changed_count=len(results))


@app.command("repair")
@tracked_command("schedule repair")
@command_meta(
    name="schedule repair",
    category="schedule",
    description="Repair missing or drifted scheduled tasks with lightweight runtime status.",
    tags=["schedule", "repair", "scheduler"],
)
def schedule_repair(
    task_id: str | None = typer.Option(None, "--task-id", "--job-id", help="Limit results to one task id."),
    auto_approve: bool = typer.Option(False, "--auto-approve", "--yes", help="Repair without confirmation."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview scheduler repair actions without making changes."),
) -> None:
    status = command_status("schedule")
    status.update("Building scheduler repair preview...")
    preview = scheduler.preview_schedule_operation(scope="user", job_id=task_id, operation="repair")
    items = preview["items"]
    blockers = preview["readiness"]["blockers"]
    if blockers:
        names = ", ".join(str(item["task_id"]) for item in blockers)
        reasons = ", ".join(str(item["reason"]) for item in blockers if item["reason"])
        status.finish()
        raise PlatformError(
            f"Schedule repair is blocked by unsupported phase-1 policy for: {names}.",
            code="E_SCHEDULE_POLICY_UNSUPPORTED",
            reason=reasons or names,
        )
    candidates = [item for item in items if item["status"] in {"missing", "drifted"}]
    if not candidates:
        status.finish("No scheduler repair actions are needed.")
        return
    # Clear transient status before the durable preview rows are printed.
    status.finish()
    for warning in preview["readiness"]["warnings"]:
        typer.echo(f"warning: {warning['task_id']} -> {warning['reason']}")
    for item in candidates:
        typer.echo(f"{item['task_id']} -> repair ({item['provider']})")
    if dry_run:
        status.finish()
        typer.echo(json.dumps(preview, indent=2, sort_keys=True))
        return
    try:
        status.update("Awaiting approval for scheduled task repairs...")
        status.finish()
        _require_auto_approve(auto_approve, operation="schedule repair", prompt="Repair these scheduled tasks?")
    except typer.Abort:
        typer.echo("Schedule repair cancelled.")
        return
    status.update("Applying scheduled task repairs...")
    results = scheduler.repair_schedule_jobs(scope="user", job_id=task_id)
    for item in results:
        typer.echo(f"Repaired {item['task_id']} as {item['task_name']}")
    status.finish(f"Schedule repair complete. Tasks repaired: {len(results)}")


@app.command("remove")
@tracked_command("schedule remove")
@command_meta(
    name="schedule remove",
    category="schedule",
    description="Remove scheduled tasks from the local machine without deleting the synced capability assets.",
    tags=["schedule", "remove", "scheduler"],
)
def schedule_remove(
    task_id: str | None = typer.Option(None, "--task-id", "--job-id", help="Limit results to one task id."),
    auto_approve: bool = typer.Option(False, "--auto-approve", "--yes", help="Remove without confirmation."),
) -> None:
    candidates = scheduler.list_schedule_jobs(scope="user", job_id=task_id)
    if not candidates:
        typer.echo("No scheduler tasks found.")
        return
    for item in candidates:
        typer.echo(f"{item['task_id']} -> remove local task ({item['provider']})")
    try:
        _require_auto_approve(auto_approve, operation="schedule remove", prompt="Remove these scheduled tasks?")
    except typer.Abort:
        typer.echo("Schedule remove cancelled.")
        return
    results = scheduler.remove_schedule_jobs(scope="user", job_id=task_id)
    for item in results:
        typer.echo(f"Removed {item['task_id']} from local scheduler")


@app.command("run")
@tracked_command("schedule run")
@command_meta(
    name="schedule run",
    category="schedule",
    description="Force-run one synced scheduler task immediately.",
    tags=["schedule", "run", "runtime"],
)
def schedule_run(
    task_id: str = typer.Option(..., "--task-id", "--job-id", help="Scheduler task id to execute."),
) -> None:
    result = scheduler.force_run_schedule_job(scope="user", job_id=task_id)
    typer.echo(
        f"Executed {result['task_id']} exit_code={result['exit_code']} finished_at={result['finished_at']}"
    )


@app.command("run-job", hidden=True)
@tracked_command("schedule run-job")
@command_meta(
    name="schedule run-job",
    category="schedule",
    description="Hidden entrypoint used by local scheduler wrappers to execute one task.",
    tags=["schedule", "hidden", "runtime"],
)
def schedule_run_job(
    task_id: str = typer.Option(..., "--task-id", "--job-id", help="Scheduler task id to execute."),
) -> None:
    result = scheduler.run_scheduled_job(job_id=task_id)
    typer.echo(f"Executed schedule task {result['task_id']}")
