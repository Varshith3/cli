# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
# src/platform_cli/commands/doctor.py
from __future__ import annotations

import json
import sys

import typer
from rich.console import Console
from rich.table import Table

from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.decorators import tracked_command, feature_flag
from platform_cli.tools.doctor_checks import doctor_payload
from platform_cli.tools.winget import ensure_winget

console = Console()


def register(app: typer.Typer) -> None:
    @app.command("doctor")
    @feature_flag("features.doctor", default=True, mode="fail")
    @tracked_command("doctor")
    def doctor(
        fix_winget: bool = typer.Option(
            False,
            "--fix-winget",
            help="On Windows: attempt to bootstrap WinGet (winget) if missing.",
        ),
        json_out: bool = typer.Option(
            False,
            "--json",
            help="Machine-readable diagnostics output.",
        ),
    ) -> None:
        """
        Quick environment diagnostic for common GHDP dependencies.
        """
        table = Table(
            show_header=True,
            header_style="bold cyan",
            title="GHDP doctor",
        )
        table.add_column("Check")
        table.add_column("Value")

        # Windows-only: winget diagnostic (and optional self-heal)
        if sys.platform.startswith("win"):
            if fix_winget:
                try:
                    ensure_winget(allow_repair=True)
                except Exception as e:  # keep doctor usable even if repair fails
                    table.add_row("winget (fix)", f"failed: {e}")

        payload = doctor_payload()

        if cli_ctx.json or json_out:
            typer.echo(json.dumps({"checks": payload}, indent=2))
            return

        for row in payload:
            table.add_row(str(row["check"]), str(row["value"]))

        console.print(table)
        console.print(
            "\n[dim]If a required tool shows '-' it is missing or not usable from this shell. "
            "Install it or fix PATH before using full GHDP features.[/dim]"
        )
