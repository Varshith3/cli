# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
# src/platform_cli/commands/commands_overview.py
from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from platform_cli.core.decorators import COMMAND_REGISTRY
from platform_cli.manifests.load import load_command_restrictions_policy

console = Console()


def _normalize_string_list(value: object) -> set[str]:
    if isinstance(value, str):
        normalized = value.strip()
        return {normalized} if normalized else set()
    if isinstance(value, list):
        return {str(item).strip() for item in value if str(item).strip()}
    return set()


def _rule_matches(meta: dict[str, object], rule: dict[str, object]) -> bool:
    match = rule.get("match", {})
    if not isinstance(match, dict):
        return False

    name = str(meta.get("name", "") or "").strip()
    category = str(meta.get("category", "") or "").strip()
    aliases = _normalize_string_list(meta.get("aliases"))
    tags = _normalize_string_list(meta.get("tags"))

    commands = _normalize_string_list(match.get("commands"))
    if commands and not ({name} | aliases).intersection(commands):
        return False

    categories = _normalize_string_list(match.get("category")) | _normalize_string_list(match.get("categories"))
    if categories and category not in categories:
        return False

    tags_any = _normalize_string_list(match.get("tags_any"))
    if tags_any and not tags.intersection(tags_any):
        return False

    return True


def _load_command_annotations() -> tuple[dict[str, object], dict[str, str]]:
    try:
        payload, _ = load_command_restrictions_policy()
    except Exception:
        return {}, {}

    defaults = payload.get("defaults", {})
    resolved: dict[str, str] = {}
    if isinstance(defaults, dict):
        for key in ("access_tier", "team_scope", "release_tier", "install_tier"):
            value = str(defaults.get(key, "") or "").strip()
            if value:
                resolved[key] = value

    return payload if isinstance(payload, dict) else {}, resolved


def _resolve_annotations(
    meta: dict[str, object],
    policy_payload: dict[str, object],
    defaults: dict[str, str],
) -> dict[str, str]:
    resolved = dict(defaults)
    rules = policy_payload.get("rules", []) if isinstance(policy_payload, dict) else []
    if isinstance(rules, list):
        for rule in rules:
            if not isinstance(rule, dict) or not _rule_matches(meta, rule):
                continue
            annotations = rule.get("annotations", {})
            if not isinstance(annotations, dict):
                continue
            for key in ("access_tier", "team_scope", "release_tier", "install_tier"):
                value = str(annotations.get(key, "") or "").strip()
                if value:
                    resolved[key] = value

    for key in ("access_tier", "team_scope", "release_tier", "install_tier"):
        value = str(meta.get(key, "") or "").strip()
        if value:
            resolved[key] = value

    return resolved


def register(app: typer.Typer) -> None:
    @app.command("commands")
    def commands_cmd(
        category: str | None = typer.Option(
            None,
            "--category",
            "-c",
            help="Filter by category (e.g. terraform, config).",
        ),
        tag: str | None = typer.Option(
            None,
            "--tag",
            "-t",
            help="Filter by tag (e.g. terraform, drift).",
        ),
        show_all: bool = typer.Option(
            False,
            "--all",
            help="Include commands marked as not visible.",
        ),
    ) -> None:
        """
        List GHDP commands known to the framework, with simple filters.

        Data comes from @command_meta on each command.
        """

        if not COMMAND_REGISTRY:
            console.print("[dim]No commands registered in COMMAND_REGISTRY yet.[/dim]")
            return

        rows = []
        for meta in COMMAND_REGISTRY.values():
            # skip "internal" commands unless --all
            if not show_all and not meta.get("visible", True):
                continue

            if category and meta.get("category") != category:
                continue

            if tag:
                tags = meta.get("tags") or []
                if tag not in tags:
                    continue

            rows.append(meta)

        if not rows:
            console.print("[dim]No commands matched your filters.[/dim]")
            return

        # Stable, readable ordering
        rows.sort(key=lambda m: (m.get("category", ""), m.get("name", "")))
        annotation_policy, annotation_defaults = _load_command_annotations()

        table = Table(
            title="GHDP Commands",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Command", style="bold", no_wrap=True)
        table.add_column("Aliases", no_wrap=True)
        table.add_column("Category", no_wrap=True)
        table.add_column("Access", no_wrap=True)
        table.add_column("Tags")
        table.add_column("Description")

        for meta in rows:
            annotations = _resolve_annotations(meta, annotation_policy, annotation_defaults)
            name = meta.get("name", "-")
            aliases = ", ".join(meta.get("aliases") or []) or "-"
            category_val = meta.get("category", "-")
            access_val = annotations.get("access_tier", "-")
            tags_val = ", ".join(meta.get("tags") or [])
            desc = meta.get("description", "-")
            table.add_row(name, aliases, category_val, access_val, tags_val, desc)

        console.print(table)
        console.print(
            "\n[dim]Tip: try[/dim] "
            "[bold]ghdp commands --category terraform[/bold] "
            "[dim]or[/dim] "
            "[bold]ghdp commands --tag tf[/bold]"
        )
