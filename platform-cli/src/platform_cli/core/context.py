# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
# src/platform_cli/core/context.py

from dataclasses import dataclass


@dataclass
class CLIContext:
    """
    Global CLI context for flags and modes.

    This lets all commands behave consistently:
    - verbose/quiet
    - json output
    - interactive vs non-interactive
    """
    verbose: bool = False
    quiet: bool = False
    json: bool = False
    non_interactive: bool = False


# Single shared instance for the process.
ctx = CLIContext()
