# NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
# src/platform_cli/commands/ci.py
"""
Commands for CI/CD pipeline integration (Jenkins).

These commands are restricted to Jenkins pipeline execution only
and will fail if run locally.
"""
from __future__ import annotations

import typer

from platform_cli.core.decorators import command_meta, requires_release_gate, tracked_command
from platform_cli.core.errors import PlatformError
from platform_cli.tools.ci_environment import (
    is_jenkins_pipeline,
    setup_ci_environment,
    setup_git_credentials,
)

app = typer.Typer(help="CI/CD pipeline utilities (Jenkins only).")


def register(root_app: typer.Typer) -> None:
    root_app.add_typer(app, name="ci")


@app.command("setup")
@command_meta(
    name="ci setup",
    category="ci",
    description="One-shot CI setup: config, tools (uv), git credentials (Jenkins only).",
    tags=["ci", "setup", "jenkins"],
)
@tracked_command("ci setup")
@requires_release_gate(command_name="ci setup", allow_admin_bypass=False, allow_ci_bypass=True, team_kwarg=None)
def ci_setup() -> None:
    """One-shot CI environment setup for Jenkins pipeline.

    Handles all CI initialization in a single command:
      1. Creates ~/.ghdp/config.json with CI-safe defaults
      2. Installs uv (Python build tool) if not present
      3. Configures git credentials via inline credential helper (no files on disk)

    Only works inside Jenkins pipeline — fails if run locally.
    After this command, all ghdp commands (build, publish, deploy) work directly.
    """
    setup_ci_environment()


@app.command("setup-git")
@command_meta(
    name="ci setup-git",
    category="ci",
    description="Configure git credentials for Jenkins pipeline (Jenkins only).",
    tags=["ci", "git", "jenkins"],
)
@tracked_command("ci setup-git")
@requires_release_gate(command_name="ci setup-git", allow_admin_bypass=False, allow_ci_bypass=True, team_kwarg=None)
def ci_setup_git() -> None:
    """Configure git to use Jenkins GitHub token for private repo access.

    Resolves credentials from Jenkins env vars or AWS Secrets Manager,
    then configures inline credential helper (no files on disk).
    Only works inside Jenkins pipeline — fails if run locally.
    """
    setup_git_credentials()


@app.command("is-jenkins")
@command_meta(
    name="ci is-jenkins",
    category="ci",
    description="Check if running inside a Jenkins pipeline.",
    tags=["ci", "jenkins", "detect"],
)
@tracked_command("ci is-jenkins")
@requires_release_gate(command_name="ci is-jenkins", allow_admin_bypass=False, allow_ci_bypass=True, team_kwarg=None)
def ci_is_jenkins() -> None:
    """Check if the current environment is a Jenkins pipeline.

    Exits with code 0 if Jenkins, code 1 if not.
    """
    if is_jenkins_pipeline():
        print("Jenkins pipeline detected.")
    else:
        raise PlatformError(
            "Not running in a Jenkins pipeline.",
            code="E_NOT_JENKINS",
            reason="not_jenkins",
        )
