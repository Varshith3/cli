# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
# src/platform_cli/commands/usage.py
from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from platform_cli.core.decorators import tracked_command, command_meta, requires_capability

console = Console()
_USAGE_LOG = Path.home() / ".ghdp" / "usage.log"


def register(app: typer.Typer) -> None:
    @app.command("usage")
    @command_meta(
        name="usage",
        category="tools",
        description="Show recent GHDP command usage from local telemetry.",
        tags=["telemetry", "analytics"],
    )
    @tracked_command("usage")
    @requires_capability("usage.read", team_kwarg=None)
    def usage(
        limit: int = typer.Option(
            20,
            "--limit",
            "-n",
            help="How many recent entries to show from local usage history.",
        ),
    ) -> None:
        """
        Show recent GHDP command usage from local telemetry.
        """

        console.print("[bold cyan]GHDP – Local Usage History[/bold cyan]\n")

        if not _USAGE_LOG.exists():
            console.print(
                "[dim]No usage log found yet. "
                "Run a few GHDP commands (tf-init/plan/deploy) first.[/dim]"
            )
            console.print(
                "\n[dim]If you disabled telemetry via [bold]GHDP_TELEMETRY=0[/bold] "
                "or turned it off in `ghdp config`, no new entries will be recorded.[/dim]"
            )
            return

        try:
            rows = []
            with _USAGE_LOG.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rows.append(json.loads(line))
        except Exception:
            console.print("[red]Could not read usage log.[/red]")
            return

        if not rows:
            console.print("[dim]Usage log is empty.[/dim]")
            return

        rows = rows[-limit:]

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Time (UTC)", style="dim", no_wrap=True)
        table.add_column("Command", style="bold")
        table.add_column("Service")
        table.add_column("Env")
        table.add_column("Status")
        table.add_column("Code / Reason")

        for entry in rows:
            status = entry.get("status", "-")
            code = entry.get("error_code")
            reason = entry.get("reason")
            code_reason = ""
            if code:
                code_reason += code
            if reason:
                code_reason += f" ({reason})"

            table.add_row(
                entry.get("ts", "-"),
                entry.get("command", "-"),
                entry.get("service") or "-",
                entry.get("env") or "-",
                status or "-",
                code_reason or "-",
            )

        console.print(table)
        console.print(
            "\n[dim]Telemetry flag:[/dim] "
            "[bold]GHDP_TELEMETRY=0[/bold] or "
            "`ghdp config telemetry --disabled` disables logging; "
            "enable again with env or `ghdp config telemetry --enabled`."
        )
