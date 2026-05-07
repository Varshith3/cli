# Rich UI abstraction for GHDP CLI
# Provides spinners, progress, panels, step logging, and TTY/plain fallback

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel
from rich.table import Table
import sys

console = Console()

class RichProgress:
    def __init__(self):
        self.progress = Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True, console=console)
        self.task_id = None

    def start(self, description: str):
        self.task_id = self.progress.add_task(description, start=True)
        self.progress.start()

    def update(self, description: str):
        if self.task_id is not None:
            self.progress.update(self.task_id, description=description)

    def stop(self, description: str = None):
        if self.task_id is not None:
            self.progress.stop()
            if description:
                console.print(f"[bold green]✓ {description}[/]")
            self.task_id = None

rich_progress = RichProgress()

# Step logging (can be used anywhere)
def log_step(step_name: str, status: str = "pending"):
    if sys.stdout.isatty():
        status_color = {
            "pending": "yellow",
            "ok": "green",
            "error": "red"
        }.get(status, "white")
        console.print(f"[bold {status_color}]{step_name}[/] [{status}]")
    else:
        print(f"{step_name} [{status}]")

# Panel printing
def print_panel(title: str, content: str):
    if sys.stdout.isatty():
        console.print(Panel(content, title=title))
    else:
        print(f"=== {title} ===\n{content}")

# Table printing
def print_table(headers, rows):
    if sys.stdout.isatty():
        table = Table(*headers)
        for row in rows:
            table.add_row(*row)
        console.print(table)
    else:
        print("\t".join(headers))
        for row in rows:
            print("\t".join(row))

# Decorator for step logging (optional)
def rich_steps(fn):
    def wrapper(*args, **kwargs):
        log_step(f"Starting {fn.__name__}", "pending")
        try:
            result = fn(*args, **kwargs)
            log_step(f"Finished {fn.__name__}", "ok")
            return result
        except Exception as e:
            log_step(f"Error in {fn.__name__}: {e}", "error")
            raise
    return wrapper

# Usage:
# from platform_cli.core.rich_ui import log_step, print_panel, rich_progress, rich_steps
# log_step("Fetching data")
# rich_progress.start("Running task")
# ... rich_progress.update("Step 2") ...
# rich_progress.stop("Task done")
# print_panel("Title", "Content")
# print_table(["Header1", "Header2"], [["A", "B"], ["C", "D"]])
# @rich_steps
# def my_command(...): ...
