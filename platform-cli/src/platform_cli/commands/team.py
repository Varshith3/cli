from __future__ import annotations

import typer

from platform_cli.core.access import resolve_effective_team_name
from platform_cli.core.decorators import command_meta, tracked_command
from platform_cli.core.team_context import (
    format_invalid_selected_team_notice,
    get_selected_team,
    is_valid_team,
    list_available_teams,
    selected_team_is_valid,
    set_selected_team,
)
from platform_cli.manifests.load import load_manifests, toolset_source_kind
from platform_cli.tools.team_toolset_assets import ensure_team_toolset_synced

app = typer.Typer(help="Manage GHDP team selection.", no_args_is_help=True)


def _echo_toolset_source_notice(toolset_source: str) -> None:
    kind = toolset_source_kind(toolset_source)
    if kind in {"packaged", "dev"}:
        typer.echo("Notice: managed synced team toolset is not active; using a fallback team list.")


def _echo_invalid_selected_team_notice(toolset: dict) -> None:
    selected = get_selected_team()
    if not selected or selected_team_is_valid(toolset):
        return
    typer.echo(format_invalid_selected_team_notice(selected, list_available_teams(toolset)))


def register(root_app: typer.Typer) -> None:
    root_app.add_typer(app, name="team")


def _load_manifests_with_team_toolset_sync():
    ensure_team_toolset_synced()
    return load_manifests()


@app.command("list")
@tracked_command("team list")
@command_meta(
    name="team list",
    category="team",
    description="List available teams from toolset manifest.",
    tags=["team", "config"],
)
def team_list() -> None:
    toolset, _, sources = _load_manifests_with_team_toolset_sync()
    _echo_toolset_source_notice(sources.get("toolset", ""))
    _echo_invalid_selected_team_notice(toolset)
    teams = list_available_teams(toolset)
    active_team = resolve_effective_team_name()
    selected = get_selected_team()
    for t in teams:
        if t == active_team and t != selected:
            typer.echo(f"* {t} (active session)")
            continue
        marker = "*" if t == selected else " "
        typer.echo(f"{marker} {t}")


@app.command("current")
@tracked_command("team current")
@command_meta(
    name="team current",
    category="team",
    description="Show current selected team from config.",
    tags=["team", "config"],
)
def team_current() -> None:
    toolset, _, sources = _load_manifests_with_team_toolset_sync()
    _echo_toolset_source_notice(sources.get("toolset", ""))
    teams = list_available_teams(toolset)
    selected = get_selected_team()
    selected_valid = bool(selected and is_valid_team(toolset, selected))
    team = resolve_effective_team_name()

    if selected and not selected_valid:
        if team and team != selected:
            typer.echo(f"{team} (active session)")
        typer.echo(format_invalid_selected_team_notice(selected, teams))
        return

    if team:
        if team != selected:
            typer.echo(f"{team} (active session)")
        else:
            typer.echo(team)
    else:
        typer.echo("(not set)")


@app.command("use")
@tracked_command("team use")
@command_meta(
    name="team use",
    category="team",
    description="Set selected team in user config.",
    tags=["team", "config"],
)
def team_use(
    team: str = typer.Option(..., "--team", help="Team name to select"),
) -> None:
    selected = (team or "").strip()
    toolset, _, sources = _load_manifests_with_team_toolset_sync()
    _echo_toolset_source_notice(sources.get("toolset", ""))
    if not is_valid_team(toolset, selected):
        teams = ", ".join(list_available_teams(toolset))
        raise typer.BadParameter(f"Unknown team '{selected}'. Available teams: {teams}")

    current = get_selected_team()
    if current and not is_valid_team(toolset, current):
        typer.echo(format_invalid_selected_team_notice(current, list_available_teams(toolset)))

    set_selected_team(selected, allow_stale_current=bool(current and not is_valid_team(toolset, current)))
    typer.echo(f"Saved team: {selected}")
