"""CI environment detection and utilities."""
# NOTE: Architectural rules in ARCHITECTURE.md – do not refactor cross-layer.

from __future__ import annotations

import json
import os
from pathlib import Path

from platform_cli.exec.runner import run_cmd

try:
    from platform_cli.core.errors import PlatformError
except Exception:  # pragma: no cover
    class PlatformError(RuntimeError):
        def __init__(self, message: str, code: str = "E_INTERNAL", reason: str = "UNKNOWN", alert: bool = False):
            super().__init__(message)
            self.code, self.reason, self.alert = code, reason, alert


def is_jenkins_pipeline() -> bool:
    """Detect if running inside a Jenkins pipeline.

    Uses multiple environment variables to make spoofing difficult:
      - JENKINS_URL (standard Jenkins)
      - BUILD_TAG starting with "jenkins-" (includes full job path)
      - NODE_NAME starting with "fargate-cloud-" (Fargate agent naming)
      - ECS_CONTAINER_METADATA_URI (only inside ECS Fargate — impossible to fake locally)

    Requires at least 3 of 4 checks to pass.
    """
    checks = [
        bool(os.environ.get("JENKINS_URL", "")),
        os.environ.get("BUILD_TAG", "").startswith("jenkins-"),
        os.environ.get("NODE_NAME", "").startswith("fargate-cloud-"),
        bool(os.environ.get("ECS_CONTAINER_METADATA_URI", "")),
    ]
    return sum(checks) >= 3


def _require_jenkins() -> None:
    """Raise if not running in Jenkins pipeline."""
    if not is_jenkins_pipeline():
        raise PlatformError(
            "This command is only available in Jenkins pipelines.",
            code="E_CI_ONLY",
            reason="not_jenkins",
        )


def _get_account_name() -> str:
    """Get AWS account name from Jenkins environment.

    Tries multiple sources:
      1. ACCOUNT_NAME env var (from Jenkins credentials() or withCredentials)
      2. If ACCOUNT_NAME is a file path, reads the file content
      3. Falls back to reading from SSM parameter /gh/account_name
    """
    raw = os.environ.get("ACCOUNT_NAME", "")

    if raw:
        # Jenkins SecretFile binding may set env var to a temp file path
        if os.path.isfile(raw):
            try:
                raw = Path(raw).read_text().strip()
            except Exception:
                pass
        if raw and not os.path.isfile(raw):
            return raw

    # Fallback: read from SSM parameter (always available on Jenkins agent via IAM role)
    try:
        result = run_cmd(
            ["aws", "ssm", "get-parameter",
             "--name", "/gh/account_name",
             "--query", "Parameter.Value",
             "--output", "text"],
            check=False,
            capture=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except PlatformError:
        pass

    raise PlatformError(
        "Could not determine AWS account name. "
        "Set ACCOUNT_NAME env var or ensure /gh/account_name SSM parameter exists.",
        code="E_ACCOUNT_NAME_MISSING",
        reason="missing_account_name",
    )


def _fetch_github_token_from_secrets_manager(account_name: str) -> tuple:
    """Fetch GitHub service account token from AWS Secrets Manager.

    Secret path: /{account_name}/security/jenkins/github_svc_acc_token
    Returns (username, password) tuple.
    """
    secret_id = f"/{account_name}/security/jenkins/github_svc_acc_token"

    result = run_cmd(
        ["aws", "secretsmanager", "get-secret-value",
         "--secret-id", secret_id,
         "--query", "SecretString",
         "--output", "text"],
        check=False,
        capture=True,
    )
    if result.returncode != 0:
        raise PlatformError(
            "Failed to fetch GitHub token from Secrets Manager",
            code="E_SECRET_FETCH_FAILED",
            reason="github_token",
        )

    secret_text = result.stdout.strip()

    # Jenkins usernamePassword secrets are stored as JSON: {"username": "...", "password": "..."}
    try:
        secret = json.loads(secret_text)
        return secret.get("username", ""), secret.get("password", "")
    except json.JSONDecodeError:
        # Some secrets are stored as plain text (token only)
        return "git", secret_text


def _configure_git_credentials(username: str, token: str) -> None:
    """Configure git credentials using an inline credential helper.

    Sets a shell-function credential helper that reads GIT_USER and GIT_TOKEN
    from environment variables at runtime. No credentials are written to disk —
    the helper function is stored in git config, but the actual secret values
    come from env vars injected by Jenkins withCredentials.

    This approach:
      - No plaintext credential files (~/.git-credentials is never created)
      - No URL-encoding needed (username/password are separate fields)
      - No daemon or socket required (unlike credential-cache)
      - Credentials only exist in process memory via env vars
    """
    # Set env vars that git subprocesses read at runtime via GIT_CONFIG_COUNT/KEY/VALUE.
    # Uses GIT_CREDS_USR/GIT_CREDS_PSW which are the standard env var names
    # created by Jenkins credentials() binding for usernamePassword type.
    # The helper also falls back to GIT_USER/GIT_TOKEN for withCredentials usage.
    # NOTE: We deliberately do NOT call "git config --global" here — that would
    # write to ~/.gitconfig and contaminate other builds on retained Jenkins agents.
    # Credentials are injected per-subprocess via GIT_CONFIG_COUNT/KEY/VALUE instead
    # (see ensure_deps in terraform_runner.py).
    os.environ["GIT_CREDS_USR"] = username
    os.environ["GIT_CREDS_PSW"] = token
    os.environ["GIT_USER"] = username
    os.environ["GIT_TOKEN"] = token


def _resolve_git_credentials() -> tuple:
    """Resolve git credentials from available sources.

    Priority:
      1. Jenkins credentials() env vars: GIT_CREDS_USR + GIT_CREDS_PSW
      2. Jenkins withCredentials env vars: GIT_USER + GIT_TOKEN
      3. AWS Secrets Manager: /{account}/security/jenkins/github_svc_acc_token

    Returns:
        (username, token) tuple. Both empty if no credentials found.
    """
    # Source 1: Jenkins credentials() binding (usernamePassword type)
    git_user = os.environ.get("GIT_CREDS_USR", "")
    git_token = os.environ.get("GIT_CREDS_PSW", "")
    if git_user and git_token:
        return git_user, git_token

    # Source 2: Jenkins withCredentials env vars
    git_user = os.environ.get("GIT_USER", "")
    git_token = os.environ.get("GIT_TOKEN", "")
    if git_user and git_token:
        return git_user, git_token

    # Source 3: AWS Secrets Manager
    try:
        account_name = _get_account_name()
        username, token = _fetch_github_token_from_secrets_manager(account_name)
        if username and token:
            return username, token
    except PlatformError:
        pass

    return "", ""


def setup_ci_environment() -> None:
    """One-shot CI setup for Jenkins pipeline.

    Handles all CI initialization behind the scenes:
      1. Sets CI-safe environment (JAVA_HOME, PATH, GHDP flags)
      2. Creates ~/.ghdp/config.json with CI defaults
      3. Installs uv (Python build tool) if not present
      4. Configures git credentials via inline credential helper (no files on disk)

    Only works inside Jenkins pipeline — fails if run locally.
    After this command, all ghdp commands work directly without any extra config.

    Fargate containers are ephemeral — no cleanup needed.
    """
    _require_jenkins()

    # 1. Set CI environment variables for this process and child processes
    home = str(Path.home())
    local_bin = f"{home}/.local/bin"
    java_home = "/usr/local/openjdk-8"

    current_path = os.environ.get("PATH", "")
    if local_bin not in current_path:
        os.environ["PATH"] = f"{local_bin}:{java_home}/bin:{current_path}"
    os.environ["JAVA_HOME"] = java_home
    os.environ["GHDP_NON_INTERACTIVE"] = "1"
    os.environ["GHDP_TELEMETRY"] = "0"
    os.environ["GHDP_UPDATE_CHECK_DISABLE"] = "1"
    print("  CI environment configured (PATH, JAVA_HOME, GHDP flags)")

    # 2. CI-safe ghdp config
    config_dir = Path.home() / ".ghdp"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.json"
    config_path.write_text(
        '{\n'
        '  "confirm.dangerous": false,\n'
        '  "telemetry.enabled": false,\n'
        '  "updates.enabled": false\n'
        '}\n'
    )
    print("  CI config written to ~/.ghdp/config.json")

    # 3. Install uv if not present
    try:
        uv_check = run_cmd(["uv", "--version"], check=False, capture=True)
        uv_found = uv_check.returncode == 0
    except PlatformError:
        uv_found = False
    if not uv_found:
        print("  Installing uv...")
        run_cmd(
            ["bash", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
            check=False,
        )
        try:
            uv_verify = run_cmd(["uv", "--version"], check=False, capture=True)
            if uv_verify.returncode == 0:
                print(f"  uv installed: {uv_verify.stdout.strip()}")
            else:
                print("  WARNING: uv installation may have failed")
        except PlatformError:
            print("  WARNING: uv not found after install attempt")
    else:
        print(f"  uv already available: {uv_check.stdout.strip()}")

    # 4. Git credentials via inline credential helper (no files on disk)
    git_user, git_token = _resolve_git_credentials()
    if git_user and git_token:
        _configure_git_credentials(git_user, git_token)
        print("  Git credentials configured (inline credential helper)")
    else:
        print("  WARNING: No git credentials available — git clone of private repos may fail")


def setup_git_credentials() -> None:
    """Configure git credentials for Jenkins (standalone command).

    Resolves credentials from env vars or Secrets Manager,
    then configures inline credential helper (no files on disk).
    """
    _require_jenkins()

    git_user, git_token = _resolve_git_credentials()
    if not git_token:
        raise PlatformError(
            "No git credentials found. Set GIT_CREDS or GIT_USER/GIT_TOKEN env vars, "
            "or ensure Secrets Manager contains the GitHub token.",
            code="E_GIT_CREDENTIALS_MISSING",
            reason="no_credentials",
        )

    _configure_git_credentials(git_user, git_token)
    print("  Git credentials configured (inline credential helper)")
