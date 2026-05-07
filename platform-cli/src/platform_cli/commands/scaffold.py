# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
# src/platform_cli/commands/scaffold.py
from __future__ import annotations

from pathlib import Path
from typing import List
import textwrap

import typer
from rich import print

from platform_cli.core.errors import PlatformError
from platform_cli.core.decorators import (
    command_meta,
    tracked_command,
    requires_capability,
    requires_release_gate,
    requires_clean_git,
    feature_flag,
    dangerous_command,
)


def register(app: typer.Typer) -> None:
    @app.command("scaffold")
    @tracked_command("scaffold")
    @requires_capability("platform.internal", team_kwarg=None)
    @requires_release_gate(command_name="scaffold", allow_admin_bypass=False, team_kwarg=None)
    @command_meta(
        name="scaffold",
        category="platform",
        description="Scaffold new internal GHDP command modules inside the CLI repo.",
        tags=["platform", "internal", "scaffold"],
    )
    def scaffold_command(
        kind: str = typer.Argument(
            ...,
            help="What to scaffold (currently only 'command' is supported).",
        ),
        name: str = typer.Argument(
            ...,
            help="Command name, e.g. 'tf-drift' or 'doctor'.",
        ),
        category: str = typer.Option(
            "",
            "--category",
            "-c",
            help="Logical category (terraform, tools, doctor, etc.). Optional.",
        ),
        tags: List[str] = typer.Option(
            [],
            "--tag",
            "-t",
            help=(
                "Tag(s) for this command (can be passed multiple times). "
                "Optional; used for grouping/search in `ghdp commands`."
            ),
        ),
        dangerous: bool = typer.Option(
            False,
            "--dangerous/--safe",
            help="Mark command as dangerous (adds confirmation decorator).",
        ),
        git_clean: bool = typer.Option(
            False,
            "--git-clean/--no-git-clean",
            help="Require clean git state before the command runs.",
        ),
        feature_flag_key: str = typer.Option(
            "",
            "--feature-flag",
            help=(
                "Optional config key to guard this command, "
                "e.g. 'features.tf_drift.enabled'. "
                "If omitted, a default placeholder will be scaffolded."
            ),
        ),
        force: bool = typer.Option(
            False,
            "--force",
            "-f",
            help="Overwrite existing file if present.",
        ),
    ) -> None:
        """
        Generate a new command module under src/platform_cli/commands.

        Intended for platform devs working inside the repo (not typical end users).

        Examples:
          ghdp scaffold command tf-drift
          ghdp scaffold command tf-drift -c terraform --tag terraform --tag drift
          ghdp scaffold command doctor -c diagnostics --tag health
        """
        kind = kind.lower().strip()
        if kind != "command":
            raise PlatformError(
                f"Unknown scaffold kind '{kind}'. Currently only 'command' is supported.",
                code="E_UNKNOWN_SCAFFOLD_KIND",
                reason="UNSUPPORTED_SCAFFOLD_KIND",
            )

        cli_name = name.strip()
        if not cli_name:
            raise PlatformError(
                "Command name cannot be empty.",
                code="E_INVALID_COMMAND_NAME",
                reason="EMPTY_NAME",
            )

        # CLI name may have hyphens, module/function uses underscores.
        module_name = cli_name.replace("-", "_")
        func_name = module_name

        project_root = Path.cwd()
        cmds_dir = project_root / "src" / "platform_cli" / "commands"
        if not cmds_dir.exists():
            raise PlatformError(
                f"Could not find commands directory at '{cmds_dir}'. "
                "Run this from the GHDP repo root.",
                code="E_COMMANDS_DIR_NOT_FOUND",
                reason="BAD_CWD_FOR_SCAFFOLD",
            )

        target_file = cmds_dir / f"{module_name}.py"
        if target_file.exists() and not force:
            raise PlatformError(
                f"Command module '{target_file}' already exists. "
                "Use --force to overwrite.",
                code="E_SCAFFOLD_FILE_EXISTS",
                reason="FILE_ALREADY_EXISTS",
            )

        # ---------------- CATEGORY & TAGS PLACEHOLDERS ---------------- #
        if category:
            category_literal = category
        else:
            category_literal = "TODO_CATEGORY"

        if tags:
            tags_list = tags
        else:
            tags_list = ["TODO_TAG"]

        tags_literal = repr(tags_list)

        # ----------------- OPTIONAL DECORATOR LINES ------------------- #
        # dangerous_command
        if dangerous:
            dangerous_line = (
                f'@dangerous_command("{cli_name}")  '
                f'# TODO: Destructive / irreversible operations require confirmation'
            )
        else:
            dangerous_line = (
                f'# @dangerous_command("{cli_name}")  '
                f'# TODO: Uncomment if this command performs destructive actions'
            )

        # requires_clean_git
        if git_clean:
            git_clean_line = (
                "@requires_clean_git()  "
                "# TODO: Enforce clean git working tree before running"
            )
        else:
            git_clean_line = (
                "# @requires_clean_git()  "
                "# TODO: Uncomment if this command should block on dirty git state"
            )

        # feature_flag
        if feature_flag_key:
            key = feature_flag_key
        else:
            # sensible default suggestion based on command name
            key = f"features.{module_name}.enabled"

        if feature_flag_key:
            feature_flag_line = (
                f'@feature_flag("{key}")  '
                "# TODO: Feature flag for rolling out this command safely"
            )
        else:
            feature_flag_line = (
                f'# @feature_flag("{key}")  '
                "# TODO: Uncomment to gate this command behind a feature flag"
            )

        # ----------------------- TEMPLATE ----------------------------- #
        template = f'''from __future__ import annotations

import typer
from rich import print

from platform_cli.core.output import print_header
from platform_cli.core.decorators import (
    command_meta,
    tracked_command,
    requires_clean_git,
    feature_flag,
    dangerous_command,
)


def register(app: typer.Typer) -> None:
    @app.command("{cli_name}")
    @command_meta(
        name="{cli_name}",  # TODO: CLI name as shown in `ghdp commands`
        category="{category_literal}",  # TODO: Adjust category if needed
        description="TODO: Describe what `{cli_name}` does.",
        tags={tags_literal},  # TODO: Adjust tags for search / grouping
    )
    # MUST-HAVE: telemetry + usage tracking for this command
    @tracked_command("{cli_name}")
    # OPTIONAL: uncomment / adjust these decorators as needed ↓
    {git_clean_line}
    {feature_flag_line}
    {dangerous_line}
    def {func_name}() -> None:
        """
        TODO: Implement the behaviour for `{cli_name}`.

        Notes:
          - This command is auto-registered by GHDP via commands auto-discovery.
          - It will automatically show up in `ghdp commands` because of @command_meta.
        """
        print_header()
        print()
        print("🔧 [bold cyan]{cli_name}[/bold cyan]")
        print("   TODO: implement command body here.")
        print()
'''

        target_file.write_text(textwrap.dedent(template), encoding="utf-8")
        print(f"✅ Created command module at [green]{target_file}[/green]")
        print("   It will be auto-registered by GHDP via commands auto-discovery.\n")
        print("   TIP: Open this file and:")
        print("     - Update description/category/tags in @command_meta")
        print("     - Uncomment requires_clean_git / feature_flag / dangerous_command")
        print("       where it makes sense for your command.\n")
