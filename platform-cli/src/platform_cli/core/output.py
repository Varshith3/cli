# NOTE: Architectural rules in ARCHITECTURE.md -- do not refactor cross-layer.
# src/platform_cli/core/output.py
import sys
from collections.abc import Callable, Mapping, Sequence

import typer
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from platform_cli import __channel__, __version__

console = Console()
_MAX_ERROR_MESSAGE_LENGTH = 4000


def _channel_label() -> str:
    return "Stable" if __channel__ == "stable" else "Beta"


def print_header() -> None:
    """
    Small header shown on every run.
    (Intentionally minimal - the big visual is in print_welcome.)
    """
    v = __version__
    ch = _channel_label()
    console.print(
        Text(
            f"GHDP :: Guardant Dev Platform CLI v{v} -- {ch} Version",
            style="dim",
        )
    )
    return


def print_welcome() -> None:
    """
    Big welcome panel shown when user just types `ghdp`.
    """
    v = __version__
    ch = _channel_label()
    title = Text(f" GHDP CLI v{v} ", style="bold white on blue")

    intro = Text()
    intro.append("Use GHDP for three core flows in this release:\n", style="bold")
    intro.append("1. Install your team tools\n")
    intro.append("2. Run local lifecycle commands in data-product repos\n")
    intro.append("3. Set up and inspect background scheduler jobs")

    install = Table.grid(padding=(0, 1))
    install.add_row("[bold]Install tools[/bold]", "")
    install.add_row(" ", "[cyan]ghdp tools install[/cyan]  -> install tools for your selected team")

    local_dev = Table.grid(padding=(0, 1))
    local_dev.add_row("[bold]Local lifecycle[/bold]", "")
    local_dev.add_row(" ", "[cyan]ghdp init[/cyan]  -> initialize local app and infra dependencies")
    local_dev.add_row(" ", "[cyan]ghdp build[/cyan]  -> build local application artifacts")
    local_dev.add_row(" ", "[cyan]ghdp deploy --env <env>[/cyan]  -> deploy local infra changes")

    scheduler = Table.grid(padding=(0, 1))
    scheduler.add_row("[bold]Scheduler[/bold]", "")
    scheduler.add_row(" ", "[cyan]ghdp schedule apply[/cyan]  -> create or refresh local background jobs")
    scheduler.add_row(" ", "[cyan]ghdp schedule list[/cyan]  -> inspect the current scheduler jobs")

    footer = Table.grid(padding=(0, 1))
    footer.add_row(
        "[bold]Docs[/bold]",
        "Tools install guide  -> https://guardanthealth.atlassian.net/wiki/spaces/DG/pages/5612011623/As+a+Data+User+how+can+I+install+all+the+tools+required+for+the+development",
    )
    footer.add_row(
        " ",
        "Release v1.0.0 PoA  -> https://guardanthealth.atlassian.net/wiki/spaces/DP/pages/5591533056/v1.0.0+stable+release+PoA",
    )

    group = Group(
        intro,
        "",
        install,
        "",
        local_dev,
        "",
        scheduler,
        "",
        footer,
    )

    panel = Panel(
        group,
        title=f" Welcome to the Guardant Dev Platform CLI -- {ch} Version ",
        border_style="cyan",
        padding=(1, 2),
    )

    console.print(title)
    console.print(panel)


def prompt_guided_choice(
    *,
    title: str,
    prompt_text: str,
    choices: Sequence[tuple[str, str]],
    default: str,
    aliases: Mapping[str, str] | None = None,
    prompt_fn: Callable[..., str] = typer.prompt,
    echo_fn: Callable[..., None] = typer.echo,
    invalid_message: str | None = None,
) -> str:
    """
    Render a small indexed menu and resolve a user selection.

    The helper stays presentation-only: callers decide what each choice means.
    """
    normalized_choices = [(str(value).strip(), str(label).strip()) for value, label in choices]
    choice_values = [value for value, _label in normalized_choices]
    alias_map: dict[str, str] = {}
    for alias, value in (aliases or {}).items():
        alias_text = str(alias).strip().lower()
        value_text = str(value).strip()
        if alias_text and value_text:
            alias_map[alias_text] = value_text

    alias_hints: dict[str, list[str]] = {}
    for alias, value in alias_map.items():
        if alias.isdigit() or alias == value:
            continue
        alias_hints.setdefault(value, []).append(alias)

    echo_fn(title)
    for index, (value, label) in enumerate(normalized_choices, start=1):
        hints = alias_hints.get(value, [])
        suffix = f" ({', '.join(hints)})" if hints else ""
        echo_fn(f"  {index}. {label}{suffix}")

    default_text = str(default).strip()
    prompt_invalid_message = invalid_message or "Unknown selection. Choose one of the indexed options."
    while True:
        raw = prompt_fn(prompt_text, default=default_text).strip().lower()
        if raw in choice_values:
            return raw
        if raw in alias_map:
            return alias_map[raw]
        if raw.isdigit():
            choice_index = int(raw) - 1
            if 0 <= choice_index < len(choice_values):
                return choice_values[choice_index]
        if default_text.isdigit() and raw == "":
            choice_index = int(default_text) - 1
            if 0 <= choice_index < len(choice_values):
                return choice_values[choice_index]
        elif default_text in choice_values and raw == "":
            return default_text
        elif default_text in alias_map and raw == "":
            return alias_map[default_text]

        echo_fn(prompt_invalid_message)


def print_error(
    message: str,
    code: str | None = None,
    reason: str | None = None,
) -> None:
    """
    Pretty error renderer used by PlatformError handling.
    """
    pieces: list[str] = []
    if code:
        pieces.append(f"[bold red]{code}[/bold red]")
    if reason:
        pieces.append(f"(reason: [yellow]{reason}[/yellow])")

    header = " ".join(pieces) if pieces else "[bold red]ERROR[/bold red]"

    panel = Panel(
        Text(_safe_console_text(message)),
        title=header,
        border_style="red",
    )
    console.print(panel)


def _safe_console_text(message: str) -> str:
    text = str(message or "")
    encoding = getattr(sys.stdout, "encoding", None)
    if encoding:
        try:
            text.encode(encoding)
        except UnicodeEncodeError:
            text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    if len(text) > _MAX_ERROR_MESSAGE_LENGTH:
        text = f"{text[: _MAX_ERROR_MESSAGE_LENGTH - 14]}...[truncated]"
    return text
