# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
# src/platform_cli/commands/config_cli.py
from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from platform_cli.core.config import (
    delete_value,
    get_config_snapshot,
    set_value,
    set_value_guarded,
)
from platform_cli.core.context import ctx as cli_ctx
from platform_cli.core.decorators import command_meta, requires_capability, requires_release_gate, tracked_command
from platform_cli.core.errors import PlatformError
from platform_cli.tools.athena_workgroup import (
    clear_saved_athena_workgroup,
    get_saved_athena_workgroup,
    set_saved_athena_workgroup,
)
from platform_cli.tools.claude_auth import sync_saved_claude_workgroup_runtime

JENKINS_API_TOKEN_CONFIG_KEY = "jenkins.api_token"

console = Console()
_MASKED_CONFIG_KEYS = {JENKINS_API_TOKEN_CONFIG_KEY}


def _display_config_value(key: str, value: object) -> str:
    if key in _MASKED_CONFIG_KEYS:
        return "'***'" if str(value or "").strip() else "''"
    return repr(value)


def register(app: typer.Typer) -> None:
    """
    Register `ghdp config ...` subcommands.

    Usage examples:
      - ghdp config list
      - ghdp config telemetry --on
      - ghdp config telemetry --off
      - ghdp config precommit --mode warn
      - ghdp config updates --on
      - ghdp config updates --off
      - ghdp config git-strict-clean --enabled
      - ghdp config git-strict-clean --disabled
      - ghdp config jenkins-okta-email --email user@guardanthealth.com
      - ghdp config jenkins-api-token --token <token>
      - ghdp config jenkins-api-token --clear
    """
    config_app = typer.Typer(help="View and tweak GHDP CLI configuration.", no_args_is_help=True)
    app.add_typer(config_app, name="config")

    # ------------------------------------------------------------------ #
    # ghdp config list
    # ------------------------------------------------------------------ #
    @config_app.command("list")
    @command_meta(
        name="config list",
        category="config",
        description="Show merged GHDP config (defaults + user overrides).",
        tags=["config", "settings", "list"],
    )
    @tracked_command("config:list")
    def list_config() -> None:
        """
        Show merged config (defaults + user overrides).
        """
        data = get_config_snapshot()
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Key", style="bold")
        table.add_column("Value")

        for key, value in sorted(data.items()):
            table.add_row(key, _display_config_value(key, value))

        console.print(table)

    # ------------------------------------------------------------------ #
    # ghdp config telemetry --on/--off
    # ------------------------------------------------------------------ #
    @config_app.command("telemetry")
    @command_meta(
        name="config telemetry",
        category="config",
        description="Enable or disable local telemetry logging.",
        tags=["config", "telemetry"],
    )
    @tracked_command("config:telemetry")
    def telemetry(
        on: bool = typer.Option(
            ...,
            "--on/--off",
            help="Enable or disable local telemetry logging.",
        ),
    ) -> None:
        set_value("telemetry.enabled", bool(on))
        state = "enabled" if on else "disabled"
        console.print(f"[bold]Telemetry is now {state}[/bold]")

    # ------------------------------------------------------------------ #
    # ghdp config updates --on/--off
    # ------------------------------------------------------------------ #
    @config_app.command("updates")
    @command_meta(
        name="config updates",
        category="config",
        description="Enable or disable GHDP update checks on startup.",
        tags=["config", "updates", "version"],
    )
    @tracked_command("config:updates")
    def updates(
        on: bool = typer.Option(
            ...,
            "--on/--off",
            help="Enable or disable update checks on startup.",
        ),
    ) -> None:
        set_value("updates.enabled", bool(on))
        state = "enabled" if on else "disabled"
        console.print(f"[bold]Update checks are now {state}[/bold]")

    # ------------------------------------------------------------------ #
    # ghdp config precommit --mode off|warn|enforce
    # ------------------------------------------------------------------ #
    @config_app.command("precommit")
    @command_meta(
        name="config precommit",
        category="config",
        description="Control pre-commit behaviour: off | warn | enforce.",
        tags=["config", "precommit", "git"],
    )
    @tracked_command("config:precommit")
    def precommit(
        mode: str = typer.Option(
            ...,
            "--mode",
            "-m",
            help="Pre-commit behaviour: off | warn | enforce",
        ),
    ) -> None:
        mode = mode.lower()
        if mode not in {"off", "warn", "enforce"}:
            raise typer.BadParameter("mode must be one of: off, warn, enforce")

        set_value_guarded("precommit.mode", mode)
        console.print(f"[bold]Pre-commit mode set to [cyan]{mode}[/cyan][/bold]")

    # ------------------------------------------------------------------ #
    # ghdp config git-strict-clean --enabled/--disabled
    # ------------------------------------------------------------------ #
    @config_app.command("git-strict-clean")
    @command_meta(
        name="config git-strict-clean",
        category="config",
        description="Block TF commands when git working tree is dirty.",
        tags=["config", "git", "safety"],
    )
    @tracked_command("config:git-strict-clean")
    @requires_capability("platform.internal", team_kwarg=None)
    @requires_release_gate(command_name="config git-strict-clean", allow_admin_bypass=False, team_kwarg=None)
    def git_strict_clean(
        enabled: bool = typer.Option(
            ...,
            "--enabled/--disabled",
            help="Block TF commands when git working tree is dirty.",
        ),
    ) -> None:
        set_value_guarded("git.strict_clean", bool(enabled))
        state = "enabled" if enabled else "disabled"
        console.print(f"[bold]git.strict_clean is now {state}[/bold]")

    @config_app.command("branch-jira-check")
    @command_meta(
        name="config branch-jira-check",
        category="config",
        description="Control Jira validation mode for branch creation.",
        tags=["config", "branch", "jira"],
    )
    @tracked_command("config:branch-jira-check")
    @requires_capability("platform.internal", team_kwarg=None)
    @requires_release_gate(command_name="config branch-jira-check", allow_admin_bypass=False, team_kwarg=None)
    def branch_jira_check(
        mode: str = typer.Option(..., "--mode", help="warn | enforce"),
    ) -> None:
        mode = mode.lower().strip()
        if mode not in {"warn", "enforce"}:
            raise typer.BadParameter("mode must be one of: warn, enforce")
        set_value("branch.create.jira_check_mode", mode)
        console.print(f"[bold]Branch Jira validation mode set to [cyan]{mode}[/cyan][/bold]")

    @config_app.command("branch-ai-provider")
    @command_meta(
        name="config branch-ai-provider",
        category="config",
        description="Control which provider GHDP prefers for branch intent generation.",
        tags=["config", "branch", "ai"],
    )
    @tracked_command("config:branch-ai-provider")
    @requires_capability("platform.internal", team_kwarg=None)
    @requires_release_gate(command_name="config branch-ai-provider", allow_admin_bypass=False, team_kwarg=None)
    def branch_ai_provider(
        provider: str = typer.Option(..., "--provider", help="auto | codex | claude | manual"),
    ) -> None:
        provider = provider.lower().strip()
        if provider not in {"auto", "codex", "claude", "manual"}:
            raise typer.BadParameter("provider must be one of: auto, codex, claude, manual")
        set_value("branch.ai.provider", provider)
        console.print(f"[bold]Branch AI provider set to [cyan]{provider}[/cyan][/bold]")

    @config_app.command("branch-intent")
    @command_meta(
        name="config branch-intent",
        category="config",
        description="Enable or disable branch intent generation.",
        tags=["config", "branch", "intent"],
    )
    @tracked_command("config:branch-intent")
    @requires_capability("platform.internal", team_kwarg=None)
    @requires_release_gate(command_name="config branch-intent", allow_admin_bypass=False, team_kwarg=None)
    def branch_intent(
        enabled: bool = typer.Option(..., "--enabled/--disabled", help="Enable or disable branch intent generation."),
    ) -> None:
        set_value("branch.intent.enabled", bool(enabled))
        state = "enabled" if enabled else "disabled"
        console.print(f"[bold]branch.intent.enabled is now {state}[/bold]")

    @config_app.command("branch-intent-prompt")
    @command_meta(
        name="config branch-intent-prompt",
        category="config",
        description="Enable or disable manual prompting when no AI provider is available.",
        tags=["config", "branch", "intent"],
    )
    @tracked_command("config:branch-intent-prompt")
    @requires_capability("platform.internal", team_kwarg=None)
    @requires_release_gate(command_name="config branch-intent-prompt", allow_admin_bypass=False, team_kwarg=None)
    def branch_intent_prompt(
        enabled: bool = typer.Option(
            ...,
            "--enabled/--disabled",
            help="Enable or disable manual intent prompting when no AI provider is available.",
        ),
    ) -> None:
        set_value("branch.intent.prompt_if_no_ai", bool(enabled))
        state = "enabled" if enabled else "disabled"
        console.print(f"[bold]branch.intent.prompt_if_no_ai is now {state}[/bold]")

    @config_app.command("jenkins-okta-email")
    @command_meta(
        name="config jenkins-okta-email",
        category="config",
        description="Store the Okta email GHDP should use for Jenkins MCP calls.",
        tags=["config", "jenkins", "okta"],
    )
    @tracked_command("config:jenkins-okta-email")
    def jenkins_okta_email(
        email: str = typer.Option(
            ...,
            "--email",
            help="Okta email address to use for Jenkins MCP requests.",
        ),
    ) -> None:
        normalized = email.strip()
        if not normalized:
            raise typer.BadParameter("email cannot be empty")
        if "@" not in normalized:
            normalized = f"{normalized}@guardanthealth.com"
        set_value("jenkins.okta_email", normalized)
        console.print(f"[bold]jenkins.okta_email set to [cyan]{normalized}[/cyan][/bold]")

    @config_app.command("jenkins-api-token")
    @command_meta(
        name="config jenkins-api-token",
        category="config",
        description="Store or clear the Jenkins API token used for Jenkins MCP calls.",
        tags=["config", "jenkins", "token", "secret"],
    )
    @tracked_command("config:jenkins-api-token")
    def jenkins_api_token(
        token: str | None = typer.Option(
            None,
            "--token",
            help="Jenkins API token to store in local GHDP config.",
        ),
        clear: bool = typer.Option(
            False,
            "--clear",
            help="Remove the stored Jenkins API token from local GHDP config.",
        ),
    ) -> None:
        if token and clear:
            raise PlatformError(
                "Use either --token or --clear, not both.",
                code="E_CONFIG_TOKEN_CONFLICT",
                reason="config_jenkins_api_token",
            )

        if clear:
            delete_value(JENKINS_API_TOKEN_CONFIG_KEY)
            console.print(f"[bold]{JENKINS_API_TOKEN_CONFIG_KEY} cleared from GHDP config[/bold]")
            return

        resolved = (token or "").strip()
        if not resolved:
            if cli_ctx.non_interactive:
                raise PlatformError(
                    "No Jenkins API token was provided. Pass --token or use `--clear` to remove the stored token.",
                    code="E_CONFIG_TOKEN_REQUIRED",
                    reason="config_jenkins_api_token",
                )
            resolved = typer.prompt("Jenkins API token", hide_input=True).strip()

        if not resolved:
            raise PlatformError(
                "Jenkins API token cannot be empty.",
                code="E_CONFIG_TOKEN_REQUIRED",
                reason="config_jenkins_api_token",
            )

        set_value(JENKINS_API_TOKEN_CONFIG_KEY, resolved)
        console.print(f"[bold]{JENKINS_API_TOKEN_CONFIG_KEY} stored in GHDP config[/bold]")

    @config_app.command("claude-athena-workgroup")
    @command_meta(
        name="config claude-athena-workgroup",
        category="config",
        description="Show, set, or clear the saved Claude Athena workgroup.",
        tags=["config", "claude", "athena"],
    )
    @tracked_command("config:claude-athena-workgroup")
    def claude_athena_workgroup(
        value: str | None = typer.Option(
            None,
            "--value",
            help="Save this Athena workgroup for Claude bootstrap.",
        ),
        clear: bool = typer.Option(
            False,
            "--clear",
            help="Clear the saved Claude Athena workgroup from GHDP config.",
        ),
    ) -> None:
        if value and clear:
            raise PlatformError(
                "Use either --value or --clear, not both.",
                code="E_CONFIG_CLAUDE_ATHENA_WORKGROUP_CONFLICT",
                reason="config_claude_athena_workgroup",
            )

        if clear:
            clear_saved_athena_workgroup()
            profile_path = sync_saved_claude_workgroup_runtime("")
            console.print("[bold]Claude Athena workgroup cleared from GHDP config[/bold]")
            console.print(f"[dim]Updated runtime/profile state:[/dim] {profile_path}")
            return

        if value is not None:
            saved = set_saved_athena_workgroup(value)
            profile_path = sync_saved_claude_workgroup_runtime(saved)
            console.print(f"[bold]Claude Athena workgroup set to [cyan]{saved}[/cyan][/bold]")
            console.print(f"[dim]Updated runtime/profile state:[/dim] {profile_path}")
            return

        current = get_saved_athena_workgroup()
        if current:
            console.print(f"[bold]Claude Athena workgroup:[/bold] [cyan]{current}[/cyan]")
        else:
            console.print("[bold]Claude Athena workgroup:[/bold] [dim]not configured[/dim]")
