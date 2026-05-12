# NOTE: Architectural rules in ARCHITECTURE.md -- do not refactor cross-layer.
# tools.py
from __future__ import annotations

from typing import Optional

import typer

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.decorators import command_meta, requires_capability, tracked_command
from platform_cli.core.errors import PlatformError
from platform_cli.core.live_status import command_status
from platform_cli.core.team_context import resolve_team
from platform_cli.manifests.load import load_manifests
from platform_cli.manifests.validate import validate_team_resolves
from platform_cli.state.store import get_tool_state  # TODO: Step-10: show managed vs active truth in status output
from platform_cli.tools.ownership import (
    clear_tool_ownership_override,
    format_ownership_compact,
    format_ownership_details,
    reconcile_tool_ownership,
    set_tool_ownership_override,
)
from platform_cli.tools.service import (
    ToolDetectionResult,
    ToolOnboardingStatus,
    ToolRuntimeSpec,
    build_tool_runtime_spec,
    build_install_failure_result,
    detect_tool,
    install_tool,
    resolve_team_tools,
    uninstall_tool,
)
from platform_cli.tools.install_summary import (
    InstallCommandIssue,
    install_summary_has_failures,
    install_summary_has_follow_up,
    issue_from_exception as build_install_issue_from_exception,
    make_issue as build_install_issue,
    render_install_summary,
)
from platform_cli.tools.user_global_agent_config import sync_user_global_agent_configs
from platform_cli.tools import scheduler as scheduler_tools
from platform_cli.tools.team_toolset_assets import TEAM_TOOLSET_CAPABILITY, ensure_team_toolset_available
from platform_cli.tools.versions import check_version_req

app = typer.Typer(help="Manage developer tools from tool manifests (PoC).", no_args_is_help=True)
ownership_app = typer.Typer(help="Inspect or change GHDP tool ownership overrides.", no_args_is_help=True)


def register(root_app: typer.Typer) -> None:
    root_app.add_typer(app, name="tools")
    app.add_typer(ownership_app, name="ownership")


def _resolve_effective_team(toolset: dict, team: Optional[str]) -> str:
    resolved = resolve_team(toolset, team, non_interactive=bool(cli_ctx.non_interactive))
    if resolved.source == "prompt":
        typer.echo(f"Using team '{resolved.team}' (saved).")
    return resolved.team


def _echo_team_toolset_resolution(result: dict[str, object], *, refresh_toolset: bool) -> None:
    status = str(result.get("local_status", "")).strip()
    if status == "cached":
        typer.echo("Using cached team toolset.")
        return
    if status == "synced":
        message = "Refreshed team toolset before running." if refresh_toolset else "No cached team toolset found, syncing now."
        typer.echo(message)
        return
    if status == "current":
        typer.echo("Team toolset is already up to date.")
        return
    if status == "fallback":
        typer.echo("Managed team toolset is unavailable; using packaged fallback for this run.")
        return
    if status == "warning":
        typer.echo("Managed team toolset sync failed; continuing with the packaged fallback for this run.")
        return


def _fallback_team_toolset_resolution(exc: Exception) -> dict[str, object]:
    return {
        "capability": TEAM_TOOLSET_CAPABILITY,
        "target_path": "",
        "local_status": "fallback",
        "sync_result": {
            "action": "warning",
            "message": str(exc),
        },
        "used_cached": False,
    }


def _load_manifests_with_team_toolset_resolution(refresh_toolset: bool = False):
    try:
        result = ensure_team_toolset_available(force_refresh=refresh_toolset)
    except PlatformError as exc:
        result = _fallback_team_toolset_resolution(exc)
    _echo_team_toolset_resolution(result, refresh_toolset=refresh_toolset)
    toolset, registry, sources = load_manifests()
    return toolset, registry, sources, result


def _load_manifests_with_team_toolset(refresh_toolset: bool = False):
    toolset, registry, sources, _ = _load_manifests_with_team_toolset_resolution(refresh_toolset=refresh_toolset)
    return toolset, registry, sources


def _emit_status_or_echo(status_printer, message: str) -> None:
    if status_printer is not None:
        status_printer(message)
        return
    typer.echo(message)


def _echo_post_gh_toolset_refresh(result: dict[str, object], *, status_printer=None) -> None:
    status = str(result.get("local_status", "")).strip()
    if status in {"synced", "current"}:
        _emit_status_or_echo(status_printer, "GitHub CLI is ready; refreshed the latest managed team toolset.")
        return
    if status in {"fallback", "warning"}:
        _emit_status_or_echo(
            status_printer,
            "GitHub CLI is ready, but managed team toolset refresh is still unavailable; continuing with packaged fallback.",
        )


def _prioritize_install_specs(specs: list[ToolRuntimeSpec], *, install_all: bool) -> list[ToolRuntimeSpec]:
    if not install_all:
        return list(specs)

    gh_specs = [spec for spec in specs if spec.name == "gh"]
    if not gh_specs:
        return list(specs)

    ordered = list(gh_specs)
    ordered.extend(spec for spec in specs if spec.name != "gh")
    return ordered


def _find_tool_requirement(toolset: dict, tool_name: str) -> Optional[dict[str, object]]:
    teams = toolset.get("teams", {})
    if not isinstance(teams, dict):
        return None
    for team_payload in teams.values():
        if not isinstance(team_payload, dict):
            continue
        tools = team_payload.get("tools", {})
        if not isinstance(tools, dict):
            continue
        requirement = tools.get(tool_name)
        if isinstance(requirement, dict):
            return dict(requirement)
    return None


def _maybe_inject_gh_bootstrap_spec(
    specs: list[ToolRuntimeSpec],
    *,
    install_all: bool,
    started_from_fallback: bool,
    toolset: dict,
    registry: dict,
    toolset_source: str,
) -> list[ToolRuntimeSpec]:
    if not install_all:
        return list(specs)
    if not started_from_fallback:
        return list(specs)
    if any(spec.name == "gh" for spec in specs):
        return list(specs)
    if not any(spec.name in {"codex", "claude"} for spec in specs):
        return list(specs)

    try:
        gh_spec = build_tool_runtime_spec(
            "gh",
            registry,
            version_req=_find_tool_requirement(toolset, "gh"),
            toolset_source=toolset_source,
        )
    except PlatformError:
        return list(specs)

    return [gh_spec, *specs]


def _refresh_toolset_after_gh_install(
    *,
    selected_team: str,
    install_all: bool,
    active_toolset_source: str,
    selected_specs: list[ToolRuntimeSpec],
    status_printer=None,
) -> tuple[list[ToolRuntimeSpec], str, list[InstallCommandIssue]]:
    issues: list[InstallCommandIssue] = []
    if not install_all or "managed:" in active_toolset_source:
        return selected_specs, active_toolset_source, issues

    if status_printer is not None:
        status_printer("Refreshing managed team toolset...")
    try:
        refresh_result = ensure_team_toolset_available(force_refresh=True)
    except PlatformError as exc:
        refresh_result = _fallback_team_toolset_resolution(exc)
        _echo_post_gh_toolset_refresh(refresh_result, status_printer=status_printer)
        issues.append(
            build_install_issue_from_exception(
                phase="refresh.toolset",
                exc=exc,
                outcome="warning",
                short_status="Managed team toolset refresh failed after GitHub CLI bootstrap",
                next_action="Rerun `ghdp tools install --refresh-toolset` after GitHub CLI is ready.",
            )
        )
        return selected_specs, active_toolset_source, issues

    if status_printer is not None:
        status_printer("Reloading managed team and tool definitions...")
    try:
        refreshed_toolset, refreshed_registry, refreshed_sources = load_manifests()
    except Exception as exc:
        _echo_post_gh_toolset_refresh(refresh_result, status_printer=status_printer)
        issues.append(
            build_install_issue_from_exception(
                phase="refresh.manifest_load",
                exc=exc,
                outcome="warning",
                short_status="Managed team toolset refresh completed, but reloading manifests failed",
                next_action="Rerun `ghdp tools install --refresh-toolset` once managed manifests are available.",
            )
        )
        return selected_specs, active_toolset_source, issues
    if str(refreshed_sources.get("toolset", "")).strip() == active_toolset_source:
        _echo_post_gh_toolset_refresh(refresh_result, status_printer=status_printer)
        return selected_specs, active_toolset_source, issues

    _echo_post_gh_toolset_refresh(refresh_result, status_printer=status_printer)
    try:
        refreshed_specs = resolve_team_tools(
            selected_team,
            refreshed_toolset,
            refreshed_registry,
            toolset_source=refreshed_sources["toolset"],
        )
    except Exception as exc:
        issues.append(
            build_install_issue_from_exception(
                phase="refresh.re_resolve",
                exc=exc,
                outcome="warning",
                short_status="Managed team toolset refresh succeeded, but the refreshed tool list could not be rebuilt",
                next_action="Rerun `ghdp tools install --refresh-toolset` once team manifests are valid again.",
            )
        )
        return selected_specs, active_toolset_source, issues

    return _prioritize_install_specs(refreshed_specs, install_all=install_all), refreshed_sources["toolset"], issues


def _tools_status_message(message: str, *, status=None) -> None:
    if status is not None:
        status.finish()
    typer.echo(message)


def _build_command_issue(
    *,
    phase: str,
    short_status: str,
    detail_hint: str = "",
    next_action: str = "",
    outcome: str = "failed",
    code: str = "",
    tool_name: str = "",
) -> InstallCommandIssue:
    return build_install_issue(
        phase=phase,
        outcome=outcome,
        code=code or "E_INSTALL_SESSION",
        short_status=short_status,
        next_action=next_action,
        detail_hint=detail_hint,
        tool_name=tool_name,
    )


def _build_detection_issue(result: ToolDetectionResult) -> InstallCommandIssue | None:
    if result.status not in {"detect_cmd_failed", "version_check_failed", "detection_ambiguous"}:
        return None

    short_status = {
        "detect_cmd_failed": "Detection command failed before install",
        "version_check_failed": "Detection could not verify the installed version",
        "detection_ambiguous": "Detection found conflicting install signals",
    }[result.status]
    next_action = {
        "detect_cmd_failed": f"Inspect the local `{result.tool_name}` installation and rerun `ghdp tools install --tool {result.tool_name}`.",
        "version_check_failed": f"Inspect the local `{result.tool_name}` installation or rerun `ghdp tools install --tool {result.tool_name} --upgrade`.",
        "detection_ambiguous": f"Inspect the local `{result.tool_name}` installation before rerunning `ghdp tools install --tool {result.tool_name}`.",
    }[result.status]
    return _build_command_issue(
        phase="detect",
        outcome="warning",
        code=result.code or "E_TOOL_DETECTION",
        short_status=short_status,
        detail_hint=result.detail_hint,
        next_action=next_action,
        tool_name=result.tool_name,
    )


def _observed_detection_result(
    spec: ToolRuntimeSpec,
    *,
    installed_any: bool,
    display_version: str,
) -> ToolDetectionResult:
    state = get_tool_state(spec.name)
    return ToolDetectionResult(
        tool_name=spec.name,
        installed_any=installed_any,
        display_version=display_version,
        status=str(state.get("detection_status", "installed" if installed_any else "not_installed")).strip() or ("installed" if installed_any else "not_installed"),
        detail_hint=str(state.get("detection_error") or state.get("detection_detail") or "").strip(),
        code=str(state.get("detection_error_code") or "").strip(),
        manager_installed=bool(state.get("detected", False)),
        managed_version=str(state.get("managed_version") or "").strip(),
        active_path=str(state.get("active_path") or "").strip(),
        active_version=str(state.get("active_version") or "").strip(),
        app_present=bool(state.get("darwin_app_present", False)),
        app_version=str(state.get("darwin_app_version") or "").strip(),
    )


def _normalize_detection_observation(
    spec: ToolRuntimeSpec,
    observed,
) -> ToolDetectionResult:
    if isinstance(observed, ToolDetectionResult):
        return observed

    if isinstance(observed, (tuple, list)):
        installed_any = bool(observed[0]) if len(observed) >= 1 else False
        display_version = str(observed[1] or "").strip() if len(observed) >= 2 else ""
        return _observed_detection_result(
            spec,
            installed_any=installed_any,
            display_version=display_version,
        )

    state = get_tool_state(spec.name)
    installed_any = bool(
        state.get("detected")
        or state.get("darwin_app_present")
        or str(state.get("active_path") or "").strip()
    )
    display_version = (
        str(state.get("managed_version") or "").strip()
        or str(state.get("active_version") or "").strip()
        or str(state.get("darwin_app_version") or "").strip()
    )
    return _observed_detection_result(
        spec,
        installed_any=installed_any,
        display_version=display_version,
    )


def _toolset_resolution_issue(
    result: dict[str, object],
    *,
    phase: str,
) -> InstallCommandIssue | None:
    local_status = str(result.get("local_status", "")).strip()
    if local_status not in {"fallback", "warning"}:
        return None

    sync_result = result.get("sync_result", {})
    sync_payload = sync_result if isinstance(sync_result, dict) else {}
    short_status = (
        "Managed team toolset was unavailable; packaged fallback is being used"
        if local_status == "fallback"
        else "Managed team toolset sync failed; continuing with the current toolset"
    )
    return _build_command_issue(
        phase=phase,
        outcome="warning",
        code=str(sync_payload.get("code") or "E_TEAM_TOOLSET_FALLBACK"),
        short_status=short_status,
        detail_hint=str(sync_payload.get("message") or "").strip(),
        next_action="Install or authenticate GitHub CLI, then rerun `ghdp tools install --refresh-toolset`.",
    )


def _build_direct_tool_spec(
    *,
    tool: Optional[str],
    toolset: dict,
    registry: dict,
    toolset_source: str,
) -> ToolRuntimeSpec | None:
    if not tool:
        return None
    try:
        return build_tool_runtime_spec(
            tool,
            registry,
            version_req=_find_tool_requirement(toolset, tool),
            toolset_source=toolset_source,
        )
    except PlatformError:
        return None


def _resolve_install_selection(
    *,
    team: Optional[str],
    tool: Optional[str],
    install_all: bool,
    refresh_toolset: bool,
    status,
) -> tuple[str, list[ToolRuntimeSpec], str, bool, list[InstallCommandIssue]]:
    issues: list[InstallCommandIssue] = []

    try:
        toolset, registry, sources, team_toolset_result = _load_manifests_with_team_toolset_resolution(
            refresh_toolset=refresh_toolset
        )
    except Exception as exc:
        return "", [], "", False, [
            build_install_issue_from_exception(
                phase="preflight.manifest_load",
                exc=exc,
                outcome="failed",
                short_status="Could not load team and tool manifests",
                next_action="Fix the manifest or team-toolset source, then rerun `ghdp tools install`.",
            )
        ]

    toolset_issue = _toolset_resolution_issue(team_toolset_result, phase="preflight.toolset")
    if toolset_issue is not None:
        issues.append(toolset_issue)

    status.update("Resolving team and tool manifests...")
    toolset_source = str(sources.get("toolset", "")).strip()
    started_from_fallback = str(team_toolset_result.get("local_status", "")).strip() in {"fallback", "warning"} or toolset_source.startswith("pkg:")

    try:
        selected_team = _resolve_effective_team(toolset, team)
    except Exception as exc:
        direct_spec = _build_direct_tool_spec(
            tool=tool,
            toolset=toolset,
            registry=registry,
            toolset_source=sources["toolset"],
        )
        if direct_spec is not None:
            issues.append(
                build_install_issue_from_exception(
                    phase="preflight.team_resolution",
                    exc=exc,
                    outcome="warning",
                    short_status=f"Could not resolve the selected team; continuing with direct `{tool}` tool metadata",
                    next_action="Rerun with a valid `--team` once the team toolset is available.",
                )
            )
            return team or "", [direct_spec], toolset_source, started_from_fallback, issues

        issues.append(
            build_install_issue_from_exception(
                phase="preflight.team_resolution",
                exc=exc,
                outcome="failed",
                short_status="Could not resolve the selected team",
                next_action="Fix the team selection or manifest data, then rerun `ghdp tools install`.",
            )
        )
        return "", [], toolset_source, started_from_fallback, issues

    try:
        specs = resolve_team_tools(selected_team, toolset, registry, toolset_source=sources["toolset"])
    except Exception as exc:
        direct_spec = _build_direct_tool_spec(
            tool=tool,
            toolset=toolset,
            registry=registry,
            toolset_source=sources["toolset"],
        )
        if direct_spec is not None:
            issues.append(
                build_install_issue_from_exception(
                    phase="preflight.tool_resolution",
                    exc=exc,
                    outcome="warning",
                    short_status=f"Could not expand the team's tool list; continuing with direct `{tool}` tool metadata",
                    next_action="Refresh the toolset and rerun once the team manifests are valid again.",
                )
            )
            return selected_team, [direct_spec], toolset_source, started_from_fallback, issues

        issues.append(
            build_install_issue_from_exception(
                phase="preflight.tool_resolution",
                exc=exc,
                outcome="failed",
                short_status="Could not resolve the selected team's tools",
                next_action="Fix the team selection or manifest data, then rerun `ghdp tools install`.",
            )
        )
        return selected_team, [], toolset_source, started_from_fallback, issues

    selected = specs if install_all else [s for s in specs if s.name == tool]
    selected = _maybe_inject_gh_bootstrap_spec(
        selected,
        install_all=install_all,
        started_from_fallback=started_from_fallback,
        toolset=toolset,
        registry=registry,
        toolset_source=sources["toolset"],
    )
    selected = _prioritize_install_specs(selected, install_all=install_all)

    if tool and not selected:
        issues.append(
            _build_command_issue(
                phase="preflight.selection",
                outcome="failed",
                code="E_TOOL_NOT_SELECTED",
                short_status=f"Unknown tool '{tool}' for team '{selected_team}'",
                next_action="Choose a tool that exists in the selected team manifest.",
            )
        )

    return selected_team, selected, toolset_source, started_from_fallback, issues


def _maybe_run_scheduler_setup_after_install(*, install_all: bool, dry_run: bool) -> None:
    if not install_all or dry_run:
        return
    if cli_ctx.json:
        return
    scheduler_status = command_status("schedule")
    scheduler_status.start("Checking scheduler initialization...")
    init_status = scheduler_tools.scheduler_initialization_status(scope="user")
    if not init_status["supported"] or init_status["initialized"]:
        scheduler_status.finish()
        return
    try:
        result = scheduler_tools.ensure_post_install_scheduler_setup(
            scope="user",
            source="tools_install",
            status_printer=scheduler_status.update,
        )
    except Exception as exc:
        scheduler_status.finish()
        typer.echo("")
        typer.echo("warning: scheduler setup could not be completed automatically.")
        typer.echo("  next: run `ghdp schedule apply`")
        typer.echo(f"  detail: {exc}")
        return

    planned = list(result["planned"])
    applied = list(result["applied"])
    scheduler_status.finish()
    typer.echo("")
    if not planned:
        typer.echo("scheduler setup: already current")
        return
    typer.echo(f"scheduler setup: initialized ({len(applied)} task(s) updated)")


@app.command("validate")
@tracked_command("tools validate")
@command_meta(
    name="tools validate",
    category="tools",
    description="Validate toolset + registry for a team on this OS.",
    tags=["tools", "manifest", "validate"],
)
def tools_validate(
    team: Optional[str] = typer.Option(None, "--team", help="Team name from the resolved team-toolset source"),
    refresh_toolset: bool = typer.Option(False, "--refresh-toolset", help="Sync the managed team toolset before loading manifests."),
):
    toolset, registry, sources = _load_manifests_with_team_toolset(refresh_toolset=refresh_toolset)
    selected_team = _resolve_effective_team(toolset, team)
    tools = validate_team_resolves(selected_team, toolset, registry)
    typer.echo(f"manifests OK for team='{selected_team}'")
    typer.echo(f"sources: {sources}")
    typer.echo(f"tools: {tools}")


@app.command("list")
@tracked_command("tools list")
@command_meta(
    name="tools list",
    category="tools",
    description="List tools configured for a team (with requirements).",
    tags=["tools", "manifest"],
)
def tools_list(
    team: Optional[str] = typer.Option(None, "--team"),
    refresh_toolset: bool = typer.Option(False, "--refresh-toolset", help="Sync the managed team toolset before loading manifests."),
):
    toolset, registry, sources = _load_manifests_with_team_toolset(refresh_toolset=refresh_toolset)
    selected_team = _resolve_effective_team(toolset, team)
    specs = resolve_team_tools(selected_team, toolset, registry, toolset_source=sources["toolset"])
    for s in specs:
        typer.echo(f"- {s.name} ({s.display_name}) req={s.version_req or {}}")


@app.command("status")
@tracked_command("tools status")
@command_meta(
    name="tools status",
    category="tools",
    description="Detect whether tools are installed (optionally refresh).",
    tags=["tools", "status"],
)
def tools_status(
    team: Optional[str] = typer.Option(None, "--team"),
    refresh: bool = typer.Option(False, "--refresh", help="Actively detect tools"),
    refresh_toolset: bool = typer.Option(False, "--refresh-toolset", help="Sync the managed team toolset before loading manifests."),
):
    toolset, registry, sources = _load_manifests_with_team_toolset(refresh_toolset=refresh_toolset)
    selected_team = _resolve_effective_team(toolset, team)
    specs = resolve_team_tools(selected_team, toolset, registry, toolset_source=sources["toolset"])

    for s in specs:
        detection = _normalize_detection_observation(s, detect_tool(s)) if refresh else None
        if not refresh:
            typer.echo(f"{s.name}: (use --refresh to detect)")
            continue

        installed = detection.installed_any if detection is not None else False
        managed_ver_raw = detection.display_version if detection is not None else ""
        ownership = reconcile_tool_ownership(s.name, s.ownership_policy)
        st = get_tool_state(s.name)

        # TODO: Step-10: prefer managed_version (manager-aware truth), show active binary truth separately
        managed_ver = (st.get("managed_version") or managed_ver_raw or "").strip()
        active_path = (st.get("active_path") or "").strip()
        active_ver = (st.get("active_version") or "").strip()
        shadowed = bool(st.get("path_shadowed", False))
        app_present = bool(st.get("darwin_app_present", False))
        app_path = (st.get("darwin_app_path") or "").strip()
        app_ver = (st.get("darwin_app_version") or "").strip()
        best_ver = (managed_ver or active_ver or app_ver or managed_ver_raw or "").strip()
        vc = check_version_req(best_ver, s.version_req)

        policy = ""
        if getattr(vc, "op", None) and getattr(vc, "required", None):
            policy = f" policy='{vc.op}{vc.required}'"

        detect_suffix = ""
        if detection is not None and detection.status not in {"installed", "not_installed"}:
            detect_suffix = f" detect='{detection.status}'"
            if detection.code:
                detect_suffix = f"{detect_suffix} detect_code='{detection.code}'"

        # Installed meaning "manager-installed truth" (brew-installed for brew-managed tools)
        if not installed:
            extra = ""
            if active_path:
                extra = f" | active={active_path} ({active_ver or 'unknown'})"
            typer.echo(f"{s.name}: missing{policy}{detect_suffix}{extra}{format_ownership_compact(ownership)}")
            continue

        verdict = "UNKNOWN"
        if getattr(vc, "ok", None) is True:
            verdict = "OK"
        elif getattr(vc, "ok", None) is False:
            verdict = "OUT_OF_POLICY"

        extra = ""
        if active_path and active_ver and managed_ver and (active_ver != managed_ver):
            extra = f" | active={active_path} ({active_ver})"
        elif active_path and not active_ver:
            extra = f" | active={active_path} (unknown)"

        if app_present and app_path:
            extra = f"{extra} | app={app_path} ({app_ver or 'unknown'})"

        if shadowed:
            extra = f"{extra} | PATH_SHADOWED"

        typer.echo(
            f"{s.name}: installed managed_version='{getattr(vc, 'parsed', None) or best_ver}'"
            f"{policy}{detect_suffix} => {verdict}{format_ownership_compact(ownership)}{extra}"
        )


@app.command("install")
@tracked_command("tools install")
@command_meta(
    name="tools install",
    category="tools",
    description="Install tools for a team (all or one).",
    tags=["tools", "install"],
)
@requires_capability("tools.install", interactive=False)
def tools_install(
    team: Optional[str] = typer.Option(None, "--team"),
    tool: Optional[str] = typer.Option(None, "--tool", help="Install only one tool"),
    all: bool = typer.Option(False, "--all", help="Install all tools for this team (default when --tool is omitted)"),
    upgrade: bool = typer.Option(False, "--upgrade", help="Use upgrade cmd when available"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Do not execute; only record planned actions"),
    refresh_toolset: bool = typer.Option(False, "--refresh-toolset", help="Sync the managed team toolset before loading manifests."),
    debug_install: bool = typer.Option(False, "--debug-install", help="Show detailed install diagnostics in the final summary."),
):
    if not isinstance(debug_install, bool):
        debug_install = False
    status = command_status("tools")
    issues: list[InstallCommandIssue] = []
    deferred_toolset_issue: InstallCommandIssue | None = None
    install_all = all or (tool is None)
    status.start("Resolving team toolset...")
    selected_team, selected, toolset_source, started_from_fallback, preflight_issues = _resolve_install_selection(
        team=team,
        tool=tool,
        install_all=install_all,
        refresh_toolset=refresh_toolset,
        status=status,
    )
    if install_all and started_from_fallback and any(spec.name == "gh" for spec in selected):
        for item in preflight_issues:
            if item.phase == "preflight.toolset":
                deferred_toolset_issue = item
                continue
            issues.append(item)
    else:
        issues.extend(preflight_issues)
    if not selected:
        status.finish()
        render_install_summary([], issues, echo=typer.echo, debug=debug_install)
        typer.echo("install finished with failures")
        raise SystemExit(1)

    results: list[ToolOnboardingStatus] = []
    processed: set[str] = set()
    previous_claude_launch_preference = getattr(cli_ctx, "claude_launch_same_session", True)
    cli_ctx.claude_launch_same_session = bool(tool == "claude" and not install_all)

    try:
        index = 0
        while index < len(selected):
            s = selected[index]
            if s.name in processed:
                index += 1
                continue
            status.update(f"Validating {s.name}...")
            detection = _normalize_detection_observation(s, detect_tool(s))
            installed = detection.installed_any
            managed_ver_raw = detection.display_version
            detection_state = get_tool_state(s.name)
            detection_issue = _build_detection_issue(detection)
            st = detection_state
            best_ver = (
                (st.get("managed_version") or "").strip()
                or (st.get("active_version") or "").strip()
                or (st.get("darwin_app_version") or "").strip()
                or (managed_ver_raw or "").strip()
            )

            if installed:
                vc = check_version_req(best_ver, s.version_req)
                if getattr(vc, "ok", None) is False and not upgrade:
                    policy = (
                        f"{vc.op}{vc.required}"
                        if (getattr(vc, "op", None) and getattr(vc, "required", None))
                        else "policy"
                    )
                    _tools_status_message(
                        f"-> skip {s.name}: managed_version='{getattr(vc, 'parsed', None) or best_ver}' is OUT_OF_POLICY ({policy}); "
                        f"re-run with --upgrade",
                        status=status,
                    )
                    results.append(
                        ToolOnboardingStatus(
                            tool_name=s.name,
                            display_name=s.display_name,
                            status="action_required",
                            short_status="Installed (out of policy)",
                            next_action=f"Rerun `ghdp tools install --tool {s.name} --upgrade`.",
                            detail_hint=f"Detected {getattr(vc, 'parsed', None) or best_ver}; requires {policy}",
                        )
                    )
                    processed.add(s.name)
                    index += 1
                    continue

            _tools_status_message(f"-> {('DRY ' if dry_run else '')}install {s.name}", status=status)
            try:
                status.update(f"Installing {s.name}...")
                install_result = install_tool(s, dry_run=dry_run, upgrade=upgrade, status_printer=status.update)
                results.append(install_result)
            except PlatformError as e:
                failure = build_install_failure_result(s, e)
                results.append(failure)
                _tools_status_message(f"x {s.name}: {e}", status=status)
                install_result = failure
            except Exception as e:
                failure = build_install_failure_result(s, e)
                results.append(failure)
                _tools_status_message(f"x {s.name}: {e}", status=status)
                install_result = failure

            if detection_issue is not None and debug_install:
                final_detection = _normalize_detection_observation(s, None)
                if final_detection.status == "installed" and install_result.status in {"ready", "already_ready"}:
                    final_detection_issue = None
                    issue_to_append = None
                else:
                    final_detection_issue = _build_detection_issue(final_detection)
                    issue_to_append = final_detection_issue or detection_issue
                if issue_to_append is not None:
                    issues.append(issue_to_append)

            processed.add(s.name)
            if (
                started_from_fallback
                and install_all
                and s.name == "gh"
                and install_result.status in {"ready", "already_ready", "action_required"}
            ):
                try:
                    selected, toolset_source, refresh_issues = _refresh_toolset_after_gh_install(
                        selected_team=selected_team,
                        install_all=install_all,
                        active_toolset_source=toolset_source,
                        selected_specs=selected,
                        status_printer=status.update,
                    )
                except Exception as exc:
                    refresh_issues = [
                        build_install_issue_from_exception(
                            phase="refresh.unexpected",
                            exc=exc,
                            outcome="warning",
                            short_status="Managed team toolset refresh failed after GitHub CLI bootstrap",
                            next_action="Rerun `ghdp tools install --refresh-toolset` after GitHub CLI is ready.",
                        )
                    ]
                issues.extend(refresh_issues)
                if deferred_toolset_issue is not None:
                    refresh_ok = not refresh_issues and ("managed:" in toolset_source)
                    if not refresh_ok:
                        issues.append(deferred_toolset_issue)
                    deferred_toolset_issue = None
                started_from_fallback = toolset_source.startswith("pkg:")
            index += 1
    finally:
        cli_ctx.claude_launch_same_session = previous_claude_launch_preference

    if deferred_toolset_issue is not None:
        issues.append(deferred_toolset_issue)

    status.update("Finalizing install summary...")
    status.finish()
    render_install_summary(results, issues, echo=typer.echo, debug=debug_install)

    if install_summary_has_failures(results, issues):
        typer.echo("install finished with failures")
        raise SystemExit(1)

    _maybe_run_scheduler_setup_after_install(install_all=install_all, dry_run=dry_run)

    if debug_install and install_summary_has_follow_up(results, issues):
        typer.echo("install finished with follow-up actions")
        return

    typer.echo("install finished")


@app.command("uninstall")
@tracked_command("tools uninstall")
@command_meta(
    name="tools uninstall",
    category="tools",
    description="Uninstall tools for a team (all or one).",
    tags=["tools", "uninstall"],
)
@requires_capability("tools.uninstall")
def tools_uninstall(
    team: Optional[str] = typer.Option(None, "--team", help="Team name from the resolved team-toolset source"),
    tool: Optional[str] = typer.Option(None, "--tool", help="Uninstall only one tool"),
    all: bool = typer.Option(False, "--all", help="Uninstall all tools for this team"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    force: bool = typer.Option(False, "--force", help="Allow uninstall even if tool is not GHDP-managed"),
    refresh_toolset: bool = typer.Option(False, "--refresh-toolset", help="Sync the managed team toolset before loading manifests."),
):
    if not all and not tool:
        raise typer.BadParameter("Provide either --all or --tool <name>")

    toolset, registry, sources = _load_manifests_with_team_toolset(refresh_toolset=refresh_toolset)
    selected_team = _resolve_effective_team(toolset, team)
    specs = resolve_team_tools(selected_team, toolset, registry, toolset_source=sources["toolset"])
    selected = [s for s in specs if (tool is None or s.name == tool)]

    if tool and not selected:
        raise typer.BadParameter(f"Unknown tool '{tool}' for team '{selected_team}'")

    for s in selected:
        typer.echo(f"-> {('DRY ' if dry_run else '')}uninstall {s.name}")
        uninstall_tool(s, dry_run=dry_run, force=force)

    typer.echo("uninstall finished")


@ownership_app.command("list")
@tracked_command("tools ownership list")
@command_meta(
    name="tools ownership list",
    category="tools",
    description="Inspect effective ownership and policy for team tools.",
    tags=["tools", "ownership", "status"],
)
def tools_ownership_list(
    team: Optional[str] = typer.Option(None, "--team"),
    refresh: bool = typer.Option(False, "--refresh", help="Actively detect tools before showing ownership."),
    refresh_toolset: bool = typer.Option(False, "--refresh-toolset", help="Sync the managed team toolset before loading manifests."),
):
    toolset, registry, sources = _load_manifests_with_team_toolset(refresh_toolset=refresh_toolset)
    selected_team = _resolve_effective_team(toolset, team)
    specs = resolve_team_tools(selected_team, toolset, registry, toolset_source=sources["toolset"])

    for s in specs:
        if refresh:
            detect_tool(s)
        resolution = reconcile_tool_ownership(s.name, s.ownership_policy)
        typer.echo(
            f"{s.name}: {format_ownership_details(resolution)}"
        )


@ownership_app.command("set")
@tracked_command("tools ownership set")
@command_meta(
    name="tools ownership set",
    category="tools",
    description="Set a tool ownership override.",
    tags=["tools", "ownership", "set"],
)
def tools_ownership_set(
    team: Optional[str] = typer.Option(None, "--team"),
    tool: str = typer.Option(..., "--tool", help="Tool name from the selected team."),
    owner: str = typer.Option(..., "--owner", help="ghdp | user"),
    refresh_toolset: bool = typer.Option(False, "--refresh-toolset", help="Sync the managed team toolset before loading manifests."),
):
    toolset, registry, sources = _load_manifests_with_team_toolset(refresh_toolset=refresh_toolset)
    selected_team = _resolve_effective_team(toolset, team)
    specs = resolve_team_tools(selected_team, toolset, registry, toolset_source=sources["toolset"])
    spec = next((item for item in specs if item.name == tool), None)
    if spec is None:
        raise typer.BadParameter(f"Unknown tool '{tool}' for team '{selected_team}'")

    resolution = set_tool_ownership_override(spec.name, spec.ownership_policy, owner, source="command:tools ownership set")
    typer.echo(f"{spec.name}: {format_ownership_details(resolution)}")


@ownership_app.command("clear")
@tracked_command("tools ownership clear")
@command_meta(
    name="tools ownership clear",
    category="tools",
    description="Clear a tool ownership override and return to policy default.",
    tags=["tools", "ownership", "clear"],
)
def tools_ownership_clear(
    team: Optional[str] = typer.Option(None, "--team"),
    tool: str = typer.Option(..., "--tool", help="Tool name from the selected team."),
    refresh_toolset: bool = typer.Option(False, "--refresh-toolset", help="Sync the managed team toolset before loading manifests."),
):
    toolset, registry, sources = _load_manifests_with_team_toolset(refresh_toolset=refresh_toolset)
    selected_team = _resolve_effective_team(toolset, team)
    specs = resolve_team_tools(selected_team, toolset, registry, toolset_source=sources["toolset"])
    spec = next((item for item in specs if item.name == tool), None)
    if spec is None:
        raise typer.BadParameter(f"Unknown tool '{tool}' for team '{selected_team}'")

    resolution = clear_tool_ownership_override(spec.name, spec.ownership_policy, source="command:tools ownership clear")
    typer.echo(f"{spec.name}: {format_ownership_details(resolution)}")


@app.command("setup-agent-config")
@tracked_command("tools setup-agent-config")
@command_meta(
    name="tools setup-agent-config",
    category="tools",
    description="Install or refresh user-global GHDP instructions for Claude/Codex.",
    tags=["tools", "claude", "codex", "config"],
)
def tools_setup_agent_config(
    tool: Optional[str] = typer.Option(None, "--tool", help="claude or codex"),
    all: bool = typer.Option(False, "--all", help="Apply both Claude and Codex global config"),
):
    selected: list[str]
    if all or tool is None:
        selected = ["claude", "codex"]
    else:
        normalized = str(tool).strip().lower()
        if normalized not in {"claude", "codex"}:
            raise typer.BadParameter("tool must be one of: claude, codex")
        selected = [normalized]

    results = sync_user_global_agent_configs(tools=selected)
    for item in results:
        typer.echo(f"{item.tool}: {item.action} -> {item.path}")
