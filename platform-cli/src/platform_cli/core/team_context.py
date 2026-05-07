from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import typer

from platform_cli.core.config import get_value, set_value
from platform_cli.core.errors import PlatformError


@dataclass
class TeamResolution:
    team: str
    source: str  # cli | config | prompt
    persisted: bool = False
    notice: str = ""


def list_available_teams(toolset: Dict[str, Any]) -> list[str]:
    teams = toolset.get("teams", {})
    if not isinstance(teams, dict) or not teams:
        raise PlatformError(
            "No teams found in toolset manifest.",
            code="E_MANIFEST_INVALID",
            reason="toolset.teams",
        )
    return [str(name) for name in teams.keys()]


def is_valid_team(toolset: Dict[str, Any], team: str) -> bool:
    teams = toolset.get("teams", {})
    return isinstance(teams, dict) and team in teams


def selected_team_is_valid(toolset: Dict[str, Any]) -> bool:
    selected = get_selected_team()
    return bool(selected and is_valid_team(toolset, selected))


def format_invalid_selected_team_notice(selected: str, teams: list[str]) -> str:
    available = ", ".join(teams)
    message = (
        f"Saved team '{selected}' is no longer available in the current team list."
    )
    if available:
        message += f" Available teams: {available}."
    message += " Run 'ghdp team use --team <name>' to reselect."
    return message


def get_selected_team() -> str:
    return str(get_value("team.selected", "") or "").strip()


def set_selected_team(team: str, *, allow_stale_current: bool = False) -> None:
    selected = team.strip()
    current = get_selected_team()
    if current and current != selected and not allow_stale_current:
        from platform_cli.core.access import ensure_team_selection_allowed

        ensure_team_selection_allowed(current_team=current, target_team=selected)
    set_value("team.selected", selected)


def prompt_for_team(teams: list[str]) -> str:
    typer.echo("Select your team (number or name):")
    for idx, name in enumerate(teams, start=1):
        typer.echo(f"  {idx}. {name}")

    while True:
        raw = str(typer.prompt("Team")).strip()
        if not raw:
            typer.echo("Please enter a team number or name.")
            continue

        if raw.isdigit():
            pos = int(raw)
            if 1 <= pos <= len(teams):
                return teams[pos - 1]
            typer.echo(f"Invalid selection '{raw}'. Choose 1..{len(teams)}.")
            continue

        if raw in teams:
            return raw

        typer.echo(f"Unknown team '{raw}'. Enter a listed team name or number.")


def resolve_team(
    toolset: Dict[str, Any],
    explicit_team: Optional[str],
    *,
    non_interactive: bool,
) -> TeamResolution:
    teams = list_available_teams(toolset)
    selected = get_selected_team()
    selected_valid = bool(selected and selected in teams)
    explicit = (explicit_team or "").strip()
    if explicit:
        if explicit not in teams:
            raise PlatformError(
                f"Unknown team '{explicit}'. Available teams: {', '.join(teams)}",
                code="E_TEAM_UNKNOWN",
                reason=explicit,
            )
        from platform_cli.core.access import resolve_effective_team_name

        current_effective = resolve_effective_team_name()
        persisted = explicit != selected or not selected_valid
        if current_effective and current_effective != explicit:
            if not (selected and not selected_valid and current_effective == selected):
                from platform_cli.core.access import ensure_team_selection_allowed

                ensure_team_selection_allowed(current_team=current_effective, target_team=explicit)
        allow_stale_current = not selected_valid or bool(current_effective and current_effective == explicit and selected != explicit)
        set_selected_team(explicit, allow_stale_current=allow_stale_current)
        return TeamResolution(team=explicit, source="cli", persisted=persisted)

    from platform_cli.core.access import resolve_effective_team_name

    effective = resolve_effective_team_name()
    if effective and effective in teams:
        source = "config" if selected_valid and selected == effective else "session"
        notice = ""
        if selected and not selected_valid:
            notice = format_invalid_selected_team_notice(selected, teams)
        return TeamResolution(team=effective, source=source, persisted=False, notice=notice)

    if selected and not selected_valid:
        notice = format_invalid_selected_team_notice(selected, teams)
        if non_interactive:
            raise PlatformError(
                notice,
                code="E_TEAM_INVALID_AFTER_SYNC",
                reason=selected,
            )
        typer.echo(notice)
        chosen = prompt_for_team(teams)
        set_selected_team(chosen, allow_stale_current=True)
        return TeamResolution(team=chosen, source="prompt", persisted=True, notice=notice)

    if non_interactive:
        raise PlatformError(
            "Team is not configured. Pass --team <name> or set one with 'ghdp team use --team <name>'.",
            code="E_TEAM_REQUIRED_NON_INTERACTIVE",
            reason="missing_team_selection",
        )

    chosen = prompt_for_team(teams)
    set_selected_team(chosen, allow_stale_current=True)
    return TeamResolution(team=chosen, source="prompt", persisted=True)
