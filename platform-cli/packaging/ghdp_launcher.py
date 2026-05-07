"""Launcher for GHDP (packaging entrypoint).

NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
"""
from platform_cli.cli import _run

if __name__ == "__main__":
    raise SystemExit(_run())
