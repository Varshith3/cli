# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

from pathlib import Path

import typer

from platform_cli.core.access import SyncCapabilityPolicy, resolve_sync_capability_policy
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.decorators import command_meta, requires_capability, tracked_command
from platform_cli.core.errors import PlatformError
from platform_cli.core.live_status import command_status
from platform_cli.core.release_content import (
    apply_content_update,
    build_sync_root_resolver,
    DEFAULT_SCOPE_KIND,
    install_content_capability,
    REPO_SCOPE_KIND,
    list_sync_status,
    preview_content_updates,
    repair_content,
    run_sync_actions,
    scan_content_inventory,
)


app = typer.Typer(help="Manage GHDP-synced external capability content.", no_args_is_help=True)


def register(root_app: typer.Typer) -> None:
    root_app.add_typer(app, name="sync")


def _filtered_items(capability: str | None) -> list[dict[str, object]]:
    result = preview_content_updates(capability=capability)
    return list(result["capabilities"])


def _stage(message: str) -> None:
    typer.echo(f"[sync] {message}")


def _summary(message: str) -> None:
    typer.echo(f"[sync] {message}")


def _require_approval(flag: bool, *, operation: str, prompt: str) -> None:
    if flag:
        return
    if cli_ctx.non_interactive:
        raise PlatformError(
            f"Confirmation required for {operation} in non-interactive mode. Re-run with --auto-approve.",
            code="E_SYNC_CONFIRM_REQUIRED",
            reason=operation.replace(" ", "_"),
        )
    if not typer.confirm(prompt):
        raise typer.Abort()


def _sync_kwargs(repo_root: Path | None) -> dict[str, object]:
    resolved_repo_root = repo_root.expanduser().resolve() if repo_root is not None else None
    if resolved_repo_root is None:
        return {
            "scope_kind": DEFAULT_SCOPE_KIND,
            "scope_ref": None,
            "resolve_root_key": build_sync_root_resolver(),
        }
    return {
        "scope_kind": REPO_SCOPE_KIND,
        "scope_ref": str(resolved_repo_root),
        "resolve_root_key": build_sync_root_resolver(repo_root=resolved_repo_root),
    }


def _sync_policy() -> SyncCapabilityPolicy:
    return resolve_sync_capability_policy(interactive=False)


def _is_capability_allowed(policy: SyncCapabilityPolicy, capability: str) -> bool:
    if not policy.restricted:
        return True
    denied = set(policy.denied_capabilities)
    if capability in denied:
        return False
    allowed = set(policy.allowed_capabilities)
    if policy.allow_configured and capability not in allowed:
        return False
    return True


def _policy_block_item(item: dict[str, object], policy: SyncCapabilityPolicy) -> dict[str, object]:
    blocked = dict(item)
    blocked["action"] = "blocked"
    blocked["blocked_reason"] = "team_policy_restricted"
    blocked["blocked_team"] = policy.context.effective_team
    return blocked


def _partition_sync_items(
    items: list[dict[str, object]],
    policy: SyncCapabilityPolicy,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    allowed: list[dict[str, object]] = []
    blocked: list[dict[str, object]] = []
    for item in items:
        capability_name = str(item.get("capability", "")).strip()
        if capability_name and not _is_capability_allowed(policy, capability_name):
            blocked.append(_policy_block_item(item, policy))
            continue
        allowed.append(item)
    return allowed, blocked


def _merge_blocked_items(
    existing: list[dict[str, object]],
    policy_blocked: list[dict[str, object]],
) -> list[dict[str, object]]:
    blocked: dict[str, dict[str, object]] = {}
    for item in existing:
        blocked[str(item.get("capability", "")).strip()] = item
    for item in policy_blocked:
        blocked[str(item.get("capability", "")).strip()] = item
    return list(blocked.values())


def _echo_policy_block(item: dict[str, object]) -> None:
    team_name = str(item.get("blocked_team", "")).strip() or "the effective team"
    typer.echo(f"{item['capability']}: blocked")
    typer.echo(f"  blocked by team sync policy for '{team_name}'")


def _echo_sync_policy_context(policy: SyncCapabilityPolicy) -> None:
    context = policy.context
    team_name = context.effective_team or "-"
    restriction = "restricted" if policy.restricted else "open"
    typer.echo(f"[sync] context: mode={context.active_mode} team={team_name} policy={restriction}")


@app.command("scan")
@tracked_command("sync scan")
@command_meta(
    name="sync scan",
    category="sync",
    description="Scan local capability locations and record tracked and extra files.",
    tags=["sync", "content", "scan"],
)
def sync_scan(
    capability: str | None = typer.Option(None, "--capability", help="Limit results to one capability."),
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Scope sync operations to a repository root for repo-local capabilities.",
    ),
) -> None:
    _stage("1/2 Checking local capability locations...")
    result = scan_content_inventory(capability=capability, persist=True, **_sync_kwargs(repo_root))
    items = list(result["capabilities"])
    if not items:
        typer.echo("No sync capabilities found.")
        return

    _stage("2/2 Recording local inventory state...")
    for item in items:
        status = item["local_status"]
        tracked = ", ".join(item["tracked_local_files"]) or "-"
        extras = ", ".join(item["extra_local_files"]) or "-"
        typer.echo(f"{item['capability']}: state={status}")
        typer.echo(f"  tracked local files: {tracked}")
        typer.echo(f"  extra local files: {extras}")
    _summary(f"Scan complete. Capabilities inspected: {len(items)}")


@app.command("list")
@tracked_command("sync list")
@command_meta(
    name="sync list",
    category="sync",
    description="List known sync capabilities and local install state.",
    tags=["sync", "content", "list"],
)
def sync_list(
    capability: str | None = typer.Option(None, "--capability", help="Limit results to one capability."),
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Scope sync operations to a repository root for repo-local capabilities.",
    ),
) -> None:
    result = list_sync_status(capability=capability, **_sync_kwargs(repo_root))
    items = list(result["capabilities"])
    if not items:
        typer.echo("No sync capabilities found.")
        return

    for item in items:
        installed = "installed" if item["installed"] else "not_installed"
        local_version = item["local_version"] or "-"
        latest_version = item["latest_version"] or "-"
        typer.echo(
            f"- {item['capability']}: {installed} local={local_version} latest={latest_version} state={item['local_status']}"
        )
        if item["extra_local_files"]:
            typer.echo(f"  extra local files: {', '.join(item['extra_local_files'])}")


@app.command("check")
@tracked_command("sync check")
@command_meta(
    name="sync check",
    category="sync",
    description="Check installed sync capabilities for updates or repair needs.",
    tags=["sync", "content", "check"],
)
def sync_check(
    capability: str | None = typer.Option(None, "--capability", help="Limit results to one capability."),
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Scope sync operations to a repository root for repo-local capabilities.",
    ),
) -> None:
    _stage("1/3 Loading local sync inventory...")
    result = preview_content_updates(capability=capability, **_sync_kwargs(repo_root))
    policy = _sync_policy()
    items, policy_blocked = _partition_sync_items(list(result["capabilities"]), policy)
    blocked_items = _merge_blocked_items([], policy_blocked)
    actionable = False
    if not items and not blocked_items:
        typer.echo("No sync capabilities found.")
        return

    _stage("2/3 Fetching remote content index and manifests...")
    _stage("3/3 Building sync action preview...")
    for item in items:
        action = str(item["action"])
        if action == "none":
            continue
        actionable = True
        display_action = "bootstrap" if action == "install" else action
        typer.echo(f"{item['capability']}: action={display_action}")
        typer.echo(f"  installed={item['local_version'] or '-'} latest={item['latest_version'] or '-'}")
        if action in {"bootstrap", "install"}:
            typer.echo(f"  files to install: {', '.join(item['missing_local_files'])}")
        elif item["missing_local_files"]:
            typer.echo(f"  files to repair: {', '.join(item['missing_local_files'])}")
        if item["updatable_files"] and action not in {"bootstrap", "install"}:
            typer.echo(f"  files to update: {', '.join(item['updatable_files'])}")
        if item["ignored_new_files"]:
            typer.echo(f"  new files ignored: {', '.join(item['ignored_new_files'])}")
        if item["missing_from_latest_manifest"]:
            typer.echo(f"  blocked; missing from latest manifest: {', '.join(item['missing_from_latest_manifest'])}")
        if action == "blocked":
            detail = str(item.get("recovery_detail", "")).strip()
            if detail == "install_if_missing_disabled":
                typer.echo("  blocked; install-if-missing recovery is not allowed for this capability")
            elif detail == "install_target_unresolvable":
                typer.echo("  blocked; install target is not resolvable for the current scope")
            recovery_hint = str(item.get("recovery_hint", "")).strip()
            if recovery_hint:
                typer.echo(f"  next step: {recovery_hint}")
            recovery_hint = str(item.get("recovery_hint", "")).strip()
            if recovery_hint:
                typer.echo(f"  next step: {recovery_hint}")
    for item in blocked_items:
        actionable = True
        _echo_policy_block(item)

    if not actionable:
        _summary("Check complete. No sync actions needed.")
        return

    _summary("Check complete. Review the listed repair, update, and blocked capabilities.")


@app.command("update")
@tracked_command("sync update")
@command_meta(
    name="sync update",
    category="sync",
    description="Update existing tracked files for installed sync capabilities with lightweight runtime status.",
    tags=["sync", "content", "update"],
)
@requires_capability("sync.mutate")
def sync_update(
    capability: str | None = typer.Option(None, "--capability", help="Update only one capability."),
    auto_approve: bool = typer.Option(False, "--auto-approve", "--yes", help="Apply update without confirmation."),
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Scope sync operations to a repository root for repo-local capabilities.",
    ),
) -> None:
    status = command_status("sync")
    status.update("1/3 Building update plan from local and remote state...")
    sync_kwargs = _sync_kwargs(repo_root)
    result = preview_content_updates(capability=capability, **sync_kwargs)
    policy = _sync_policy()
    allowed_items, policy_blocked = _partition_sync_items(list(result["capabilities"]), policy)
    candidates = [item for item in allowed_items if item["action"] == "update"]
    if not candidates and not policy_blocked:
        status.finish("Update check complete. No eligible sync updates found.")
        return

    status.update("2/3 Review update candidates...")
    # Clear transient status before the durable preview rows are printed.
    status.finish()
    _echo_sync_policy_context(policy)
    for item in candidates:
        typer.echo(f"{item['capability']}: {item['local_version']} -> {item['latest_version']}")
        typer.echo(f"  update files: {', '.join(item['updatable_files'])}")
        if item["ignored_new_files"]:
            typer.echo(f"  ignored new files: {', '.join(item['ignored_new_files'])}")
    for item in policy_blocked:
        _echo_policy_block(item)

    if not candidates:
        _summary("Update blocked by team sync restrictions.")
        return

    try:
        status.update("3/3 Awaiting approval for update actions...")
        status.finish()
        _require_approval(auto_approve, operation="sync update", prompt="Apply these sync updates?")
    except typer.Abort:
        typer.echo("Sync update cancelled.")
        return

    status.update("Applying content updates...")
    for item in candidates:
        update_result = apply_content_update(str(item["capability"]), **sync_kwargs)
        typer.echo(
            f"Updated {item['capability']}: {update_result['updated_count']} file(s) to {update_result['latest_version']}"
        )
    status.finish(f"Update complete. Capabilities updated: {len(candidates)}")


@app.command("repair")
@tracked_command("sync repair")
@command_meta(
    name="sync repair",
    category="sync",
    description="Repair missing tracked files for installed sync capabilities with lightweight runtime status.",
    tags=["sync", "content", "repair"],
)
@requires_capability("sync.mutate")
def sync_repair(
    capability: str | None = typer.Option(None, "--capability", help="Repair only one capability."),
    auto_approve: bool = typer.Option(False, "--auto-approve", "--yes", help="Apply repair without confirmation."),
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Scope sync operations to a repository root for repo-local capabilities.",
    ),
) -> None:
    status = command_status("sync")
    status.update("1/3 Checking tracked files for repair needs...")
    sync_kwargs = _sync_kwargs(repo_root)
    result = preview_content_updates(capability=capability, **sync_kwargs)
    policy = _sync_policy()
    allowed_items, policy_blocked = _partition_sync_items(list(result["capabilities"]), policy)
    candidates = [item for item in allowed_items if item["action"] in {"repair", "bootstrap", "install"}]
    if not candidates and not policy_blocked:
        status.finish("Repair check complete. No sync repairs needed.")
        return

    status.update("2/3 Review repair candidates...")
    # Clear transient status before the durable preview rows are printed.
    status.finish()
    _echo_sync_policy_context(policy)
    for item in candidates:
        if item["action"] in {"bootstrap", "install"}:
            typer.echo(f"{item['capability']}: install files {', '.join(item['missing_local_files'])}")
        else:
            typer.echo(f"{item['capability']}: repair files {', '.join(item['missing_local_files'])}")
    for item in policy_blocked:
        _echo_policy_block(item)

    if not candidates:
        _summary("Repair blocked by team sync restrictions.")
        return

    try:
        status.update("3/3 Awaiting approval for repair actions...")
        status.finish()
        _require_approval(auto_approve, operation="sync repair", prompt="Repair these tracked files?")
    except typer.Abort:
        typer.echo("Sync repair cancelled.")
        return

    status.update("Repairing tracked files...")
    for item in candidates:
        repair_result = repair_content(str(item["capability"]), **sync_kwargs)
        prefix = "Bootstrapped" if item["action"] in {"bootstrap", "install"} else "Repaired"
        typer.echo(f"{prefix} {item['capability']}: {repair_result['repaired_count']} file(s)")
    status.finish(f"Repair complete. Capabilities repaired: {len(candidates)}")


@app.command("run")
@tracked_command("sync run")
@command_meta(
    name="sync run",
    category="sync",
    description="Scan local content, repair tracked files, and apply eligible updates with lightweight runtime status.",
    tags=["sync", "content", "run"],
)
@requires_capability("sync.mutate")
def sync_run(
    capability: str | None = typer.Option(None, "--capability", help="Limit results to one capability."),
    auto_approve: bool = typer.Option(False, "--auto-approve", "--yes", help="Apply repairs and updates without confirmation."),
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Scope sync operations to a repository root for repo-local capabilities.",
    ),
) -> None:
    status = command_status("sync")
    status.update("1/4 Checking local inventory...")
    sync_kwargs = _sync_kwargs(repo_root)
    result = run_sync_actions(capability=capability, apply=False, **sync_kwargs)
    policy = _sync_policy()
    items, policy_blocked = _partition_sync_items(list(result["preview"]["capabilities"]), policy)
    if not items and not policy_blocked:
        status.finish("No sync capabilities found.")
        return

    status.update("2/4 Recording local inventory and fetching remote sync metadata...")
    status.update("3/4 Building repair and update plan...")
    # Clear transient status before the durable preview rows are printed.
    status.finish()
    _echo_sync_policy_context(policy)

    installs = [item for item in result.get("installs", []) if _is_capability_allowed(policy, str(item.get("capability", "")).strip())]
    repairs = [item for item in result.get("repairs", []) if _is_capability_allowed(policy, str(item.get("capability", "")).strip())]
    updates = [item for item in result.get("updates", []) if _is_capability_allowed(policy, str(item.get("capability", "")).strip())]
    blocked = _merge_blocked_items(
        [
            item
            for item in result.get("blocked", [])
            if _is_capability_allowed(policy, str(item.get("capability", "")).strip())
        ],
        policy_blocked,
    )
    actionable = bool(installs or repairs or updates)
    for item in installs:
        typer.echo(f"{item['capability']}: bootstrap install files {', '.join(item['missing_local_files'])}")
    for item in repairs:
        if item["action"] in {"bootstrap", "install"}:
            typer.echo(f"{item['capability']}: bootstrap install files {', '.join(item['missing_local_files'])}")
        else:
            typer.echo(f"{item['capability']}: repair files {', '.join(item['missing_local_files'])}")
    for item in updates:
        typer.echo(f"{item['capability']}: {item['local_version'] or '-'} -> {item['latest_version'] or '-'}")
        typer.echo(f"  update files: {', '.join(item['updatable_files'])}")
        if item["ignored_new_files"]:
            typer.echo(f"  ignored new files: {', '.join(item['ignored_new_files'])}")
    for item in blocked:
        if item.get("blocked_reason") == "team_policy_restricted":
            _echo_policy_block(item)
        elif item["missing_from_latest_manifest"]:
            typer.echo(f"{item['capability']}: blocked")
            typer.echo(f"  missing from latest manifest: {', '.join(item['missing_from_latest_manifest'])}")
        elif item.get("recovery_mode") == "blocked":
            typer.echo(f"{item['capability']}: blocked")
            detail = str(item.get("recovery_detail", "")).strip()
            if detail == "install_if_missing_disabled":
                typer.echo("  install-if-missing recovery is not allowed for this capability")
            elif detail == "install_target_unresolvable":
                typer.echo("  install target is not resolvable for the current scope")
            recovery_hint = str(item.get("recovery_hint", "")).strip()
            if recovery_hint:
                typer.echo(f"  next step: {recovery_hint}")

    if not actionable:
        if blocked:
            _summary(f"Run complete. No eligible sync repairs or updates found. Blocked capabilities: {len(blocked)}")
            return
        status.finish("Run complete. No sync actions needed.")
        return

    try:
        status.update("4/4 Awaiting approval for repair and update actions...")
        status.finish()
        _require_approval(auto_approve, operation="sync run", prompt="Apply these sync repairs and updates?")
    except typer.Abort:
        typer.echo("Sync run cancelled.")
        return

    status.update("Applying repairs and updates...")
    install_results: list[dict[str, object]] = []
    repair_results: list[dict[str, object]] = []
    update_results: list[dict[str, object]] = []
    for item in installs:
        install_result = install_content_capability(str(item["capability"]), **sync_kwargs)
        install_results.append(install_result)
        typer.echo(f"Bootstrapped {item['capability']}: {install_result['installed_count']} file(s)")
    for item in repairs:
        repair_result = repair_content(str(item["capability"]), **sync_kwargs)
        repair_results.append({"action": item["action"], **repair_result})
        prefix = "Bootstrapped" if item["action"] in {"bootstrap", "install"} else "Repaired"
        typer.echo(f"{prefix} {item['capability']}: {repair_result['repaired_count']} file(s)")
    for item in updates:
        update_result = apply_content_update(str(item["capability"]), **sync_kwargs)
        update_results.append(update_result)
        typer.echo(f"Updated {item['capability']}: {update_result['updated_count']} file(s) to {update_result['latest_version']}")
    blocked_suffix = f"; blocked: {len(blocked)}" if blocked else ""
    status.finish(
        "Run complete. "
        f"bootstraps applied: {len(install_results)}; "
        f"repairs applied: {len(repair_results)}; "
        f"updates applied: {len(update_results)}"
        f"{blocked_suffix}"
    )
