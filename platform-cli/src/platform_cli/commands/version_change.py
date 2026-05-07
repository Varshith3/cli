# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
from __future__ import annotations

import os
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.decorators import command_meta, tracked_command
from platform_cli.core.errors import PlatformError
from platform_cli.core.update import (
    DEFAULT_REPO,
    install_selected_version_detailed,
    list_release_tags,
    normalize_tag,
    resolve_latest_stable_target,
)

console = Console()
version_app = typer.Typer(help="Version management commands.", no_args_is_help=True)


def _resolved_repo(repo: Optional[str]) -> str:
    resolved = (repo or os.getenv("GHDP_UPDATE_REPO") or DEFAULT_REPO).strip()
    if resolved:
        return resolved
    raise PlatformError(
        "GitHub repo is not configured. Set GHDP_UPDATE_REPO or GHDP_DEFAULT_REPO.",
        code="E_CONFIG_REQUIRED",
        reason="version_change",
    )


def register(root_app: typer.Typer) -> None:
    root_app.add_typer(version_app, name="version")


def _print_version_change_menu(target_repo: str) -> None:
    typer.echo(f"available version change actions for {target_repo}:")
    typer.echo("  1. latest-stable  Check the latest stable release and install it if needed")
    typer.echo("  2. pick-version   Choose a specific release tag")
    typer.echo("  3. exit           Leave version change without taking action")


def _prompt_version_change_mode(*, default: str = "latest-stable") -> str:
    aliases = {
        "1": "latest-stable",
        "2": "pick-version",
        "3": "exit",
        "stable": "latest-stable",
        "specific": "pick-version",
    }

    while True:
        raw = typer.prompt("Choose the next version action", default=default).strip().lower()
        action = aliases.get(raw, raw)
        if action in {"latest-stable", "pick-version", "exit"}:
            return action
        typer.echo("Unknown action. Choose one of: latest-stable, pick-version, exit.")


def _install_latest_stable(*, target_repo: str, method: str) -> None:
    current_tag, stable_tag, update_needed = resolve_latest_stable_target(
        target_repo,
        allow_prompt_token=not bool(cli_ctx.non_interactive),
    )
    if not update_needed:
        console.print(f"[green]GHDP is already on the latest stable release {stable_tag}.[/green]")
        return
    if not cli_ctx.non_interactive:
        should_install = typer.confirm(
            f"Latest stable is {stable_tag} and current is {current_tag}. Install the latest stable now?",
            default=True,
        )
        if not should_install:
            console.print("[yellow]Version change cancelled.[/yellow]")
            return

    result = install_selected_version_detailed(target_repo, stable_tag, method=method)
    if result.verification_status == "verified":
        console.print(
            f"[green]Installed latest stable GHDP {stable_tag} via {result.method}.[/green]\n"
            "[dim]Run `ghdp --version` to verify your active binary.[/dim]"
        )
        return
    console.print(
        f"[yellow]Staged latest stable GHDP {stable_tag} via {result.method}, "
        "but the active binary swap is still pending on Windows.[/yellow]\n"
        "[dim]Allow the current GHDP process to exit, then run `ghdp --version` to verify the active binary.[/dim]"
    )


def _run_version_change(
    version: Optional[str] = typer.Option(None, "--version", help="Target version tag (e.g. v0.1.0 or 0.1.0)."),
    latest_stable: bool = typer.Option(
        False,
        "--latest-stable",
        help="Check the latest stable GHDP release and install it if the current version differs.",
    ),
    method: str = typer.Option(
        "auto",
        "--method",
        help="Install method: auto, pipx, installer.",
    ),
    repo: Optional[str] = typer.Option(None, "--repo", help="Override GitHub repo (owner/name)."),
) -> None:
    target_repo = _resolved_repo(repo)
    if latest_stable and version:
        raise PlatformError(
            "Use either --version or --latest-stable, not both.",
            code="E_BAD_ARGS",
            reason="version_change_conflict",
        )

    picked = (version or "").strip()
    if latest_stable:
        _install_latest_stable(target_repo=target_repo, method=method)
        return

    if not picked:
        if cli_ctx.non_interactive:
            raise PlatformError(
                "--version or --latest-stable is required in non-interactive mode.",
                code="E_BAD_ARGS",
                reason="missing_version_non_interactive",
            )

        _print_version_change_menu(target_repo)
        mode = _prompt_version_change_mode(default="latest-stable")
        if mode == "exit":
            console.print("[yellow]Version change exited without changes.[/yellow]")
            return
        if mode == "latest-stable":
            _install_latest_stable(target_repo=target_repo, method=method)
            return

        rows = list_release_tags(target_repo, limit=30, include_drafts=False)
        if not rows:
            raise PlatformError(
                "No releases available to select.",
                code="E_NO_RELEASES",
                reason="empty_release_list",
            )

        table = Table(title=f"Pick GHDP version ({target_repo})", show_header=True, header_style="bold cyan")
        table.add_column("#", style="bold", no_wrap=True)
        table.add_column("Tag", no_wrap=True)
        table.add_column("Channel", no_wrap=True)

        for idx, rel in enumerate(rows, 1):
            channel = "pre-release" if rel.prerelease else "stable"
            table.add_row(str(idx), rel.tag, channel)

        console.print(table)
        choice_raw = typer.prompt("Select version number")

        try:
            choice = int(choice_raw)
        except ValueError:
            raise PlatformError(
                f"Invalid selection '{choice_raw}'. Expected a number.",
                code="E_BAD_ARGS",
                reason="invalid_selection",
            )

        if choice < 1 or choice > len(rows):
            raise PlatformError(
                f"Selection out of range: {choice}.",
                code="E_BAD_ARGS",
                reason="selection_out_of_range",
            )

        picked = rows[choice - 1].tag

    normalized = normalize_tag(picked)
    result = install_selected_version_detailed(target_repo, normalized, method=method)
    if result.verification_status == "verified":
        console.print(
            f"[green]Installed GHDP {normalized} via {result.method}.[/green]\n"
            "[dim]Run `ghdp --version` to verify your active binary.[/dim]"
        )
        return
    console.print(
        f"[yellow]Staged GHDP {normalized} via {result.method}, "
        "but the active binary swap is still pending on Windows.[/yellow]\n"
        "[dim]Allow the current GHDP process to exit, then run `ghdp --version` to verify the active binary.[/dim]"
    )


@version_app.command("change")
@tracked_command("version change")
@command_meta(
    name="version change",
    category="self",
    description="Install a selected GHDP version (upgrade or downgrade).",
    tags=["version", "upgrade", "downgrade"],
)
def version_change(
    version: Optional[str] = typer.Option(None, "--version", help="Target version tag (e.g. v0.1.0 or 0.1.0)."),
    latest_stable: bool = typer.Option(
        False,
        "--latest-stable",
        help="Check the latest stable GHDP release and install it if the current version differs.",
    ),
    method: str = typer.Option(
        "auto",
        "--method",
        help="Install method: auto, pipx, installer.",
    ),
    repo: Optional[str] = typer.Option(None, "--repo", help="Override GitHub repo (owner/name)."),
) -> None:
    _run_version_change(version=version, latest_stable=latest_stable, method=method, repo=repo)
