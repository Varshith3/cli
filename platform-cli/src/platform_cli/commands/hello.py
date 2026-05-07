# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
# src/platform_cli/commands/hello.py
from __future__ import annotations

import typer
from rich import print

from platform_cli.core.decorators import tracked_command, command_meta, feature_flag


def register(app: typer.Typer) -> None:
    @app.command("hello")
    @command_meta(
        name="hello",
        category="hello",
        description="Simple hello used to smoke-test the CLI.",
        tags=["hello"],
    )
    @feature_flag("features.hello", default=True, mode="warn")
    @tracked_command("hello")
    def hello(
        name: str = typer.Option("there", "--name", "-n", help="Your name"),
    ) -> None:
        """
        Simple hello used to smoke-test the CLI.
        """
        print(f"👋 Hello, [bold cyan]{name}[/bold cyan]!")
