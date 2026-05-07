# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
# cli.py
# src/platform_cli/cli.py
from __future__ import annotations

import importlib
import os
import pkgutil
from pathlib import Path

import typer
import click  # TODO: Step-10: catch Typer/Click usage errors cleanly

from platform_cli.core.runtime_env import load_runtime_env

# Load runtime defaults before importing command modules that read env-backed defaults.
load_runtime_env()

import platform_cli.commands as commands_pkg
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.errors import PlatformError
from platform_cli.core.output import print_header, print_welcome, print_error
from platform_cli.core.secure_defaults import bootstrap_secure_defaults_from_file, RUNTIME_DEFAULT_KEYS
from platform_cli.core.telemetry import log_error, send_alert
from platform_cli.core.update import maybe_check_for_update
from platform_cli import __app_name__, __version__, __channel__

# ✅ catch the “other” PlatformError class too (until you unify them)
try:
    from platform_cli.manifests.validate import PlatformError as ManifestPlatformError
except Exception:  # fallback if module not present
    ManifestPlatformError = PlatformError

app = typer.Typer(
    help="Guardant Dev Platform CLI (GHDP)",
    # ✅ prevents Typer/Rich pretty tracebacks
    pretty_exceptions_enable=False,
    pretty_exceptions_show_locals=False,
)


def _register_all_commands(app: typer.Typer) -> None:
    """
    Auto-register any module under platform_cli.commands that:
      - filename does NOT start with '_'
      - exposes a callable `register(app)` function.
    """
    for module_info in pkgutil.iter_modules(commands_pkg.__path__):
        name = module_info.name
        if name.startswith("_"):
            continue

        module_full_name = f"{commands_pkg.__name__}.{name}"
        module = importlib.import_module(module_full_name)

        register = getattr(module, "register", None)
        if callable(register):
            register(app)


_register_all_commands(app)

def _version_callback(value: bool):
    if value:
        typer.echo(f"{__app_name__} {__version__} ({__channel__})")
        raise typer.Exit(0)


@app.command("_bootstrap-defaults", hidden=True)
def bootstrap_defaults(
    file: Path = typer.Option(..., "--file", exists=True, dir_okay=False, readable=True),
):
    stored = bootstrap_secure_defaults_from_file(file)
    typer.echo(f"Processed {stored} installed runtime default(s).")


@app.command("_check-defaults", hidden=True)
def check_defaults():
    missing = [key for key in RUNTIME_DEFAULT_KEYS if not (os.getenv(key) or "").strip()]
    if missing:
        raise PlatformError(
            "Missing installed runtime defaults: " + ", ".join(missing),
            code="E_RUNTIME_DEFAULTS_NOT_LOADED",
            reason="secure_defaults",
        )
    typer.echo(f"Loaded {len(RUNTIME_DEFAULT_KEYS)} installed runtime default(s).")


@app.command("_schedule-apply-background", hidden=True)
def schedule_apply_background():
    from platform_cli.tools import scheduler as scheduler_tools

    result = scheduler_tools.run_background_schedule_apply(scope="user")
    planned = list(result["planned"])
    applied = list(result["applied"])
    if not planned:
        typer.echo("Background schedule apply found no changes.")
        return
    for item in applied:
        typer.echo(f"Background schedule apply updated {item['task_id']} as {item['task_name']}")
    typer.echo(f"Background schedule apply complete. Changed tasks: {len(applied)}")


@app.command("_post-install-scheduler-setup", hidden=True)
def post_install_scheduler_setup():
    from platform_cli.tools import scheduler as scheduler_tools

    try:
        result = scheduler_tools.ensure_post_install_scheduler_setup(scope="user", source="binary_install")
    except Exception as exc:
        typer.echo("warning: scheduler setup could not be completed automatically.")
        typer.echo("next: run `ghdp schedule apply`")
        typer.echo(f"detail: {exc}")
        return

    action = str(result.get("action", "")).strip()
    planned = list(result.get("planned", []))
    applied = list(result.get("applied", []))
    if action == "skipped":
        typer.echo("scheduler setup: already current")
        return
    if not planned:
        typer.echo("scheduler setup: already current")
        return
    typer.echo(f"scheduler setup: completed ({len(applied)} task(s) updated)")


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "--v",     # optional alias (remove if you don’t want it)
        "-V",      # short flag (since -v is verbose)
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    quiet: bool = typer.Option(False, "--quiet"),
    json_out: bool = typer.Option(False, "--json"),
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        envvar="GHDP_NON_INTERACTIVE",
    ),
):
    if verbose and quiet:
        raise typer.BadParameter("Cannot use --verbose and --quiet together.")

    cli_ctx.verbose = verbose
    cli_ctx.quiet = quiet
    cli_ctx.json = json_out
    cli_ctx.non_interactive = non_interactive

    if not cli_ctx.json:
        print_header()

    updated_in_place = False
    sub = ctx.invoked_subcommand or ""
    try:
        # Check on startup with normal throttle, and always re-check on explicit doctor.
        if sub == "":
            updated_in_place = maybe_check_for_update(force=False)
        elif sub == "doctor":
            updated_in_place = maybe_check_for_update(force=True)    # always re-check

    except Exception as e:
        if sub == "doctor":
            typer.echo(f"[debug] GHDP doctor update check failed: {e}")

    if updated_in_place:
        typer.echo("GHDP was updated. Please rerun your command.")
        raise typer.Exit(0)

    if ctx.invoked_subcommand is None:
        print_welcome()
        raise typer.Exit(0)


def _run() -> int:
    try:
        load_runtime_env()
        # ✅ standalone_mode=False makes sure exceptions bubble here reliably
        app(standalone_mode=False)
        return 0

    except (PlatformError, ManifestPlatformError) as e:
        log_error(e)
        if getattr(e, "alert", False):
            send_alert(e)

        msg = getattr(e, "message", str(e))
        code = getattr(e, "code", None)
        reason = getattr(e, "reason", None)
        print_error(msg, code=code, reason=reason)
        return 1

    # TODO: Step-10: normalize Typer/Click arg/usage errors into std error format (no tracebacks)
    except click.ClickException as e:
        # no_args_is_help=True raises this after help is printed; don't surface as an error panel.
        if e.__class__.__name__ == "NoArgsIsHelpError":
            return 0
        print_error(str(e), code="E_BAD_ARGS", reason=e.__class__.__name__)
        return int(getattr(e, "exit_code", 2) or 2)

    except typer.Exit as e:
        return e.exit_code

    except SystemExit as e:
        return int(e.code or 0)


if __name__ == "__main__":
    raise SystemExit(_run())
