"""Command modules for the GHDP CLI.

NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.

Any module in this package that:
  - does NOT start with '_' and
  - exposes a `register(app: typer.Typer)` function

will be auto-discovered and registered by `platform_cli.cli`.
"""
