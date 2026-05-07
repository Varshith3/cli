"""Publish application artifacts to CodeArtifact and ECR."""
# NOTE: Architectural rules in ARCHITECTURE.md – do not refactor cross-layer.

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

try:
    from platform_cli.core.errors import PlatformError  # type: ignore
except Exception:  # pragma: no cover

    class PlatformError(RuntimeError):
        def __init__(
            self,
            message: str,
            code: str = "E_INTERNAL",
            reason: str = "UNKNOWN",
            alert: bool = False,
        ):
            super().__init__(message)
            self.code = code
            self.reason = reason
            self.alert = alert


from platform_cli.exec.runner import run_cmd
from platform_cli.tools.git_repo import get_short_commit_hash


def _load_platform_config() -> Dict[str, Any]:
    """Load platform config from account-environments manifest (lazy, cached)."""
    from platform_cli.manifests.load import (
        get_aws_region,
        get_codeartifact_config,
        get_codeartifact_repo_name as _manifest_get_ca_repo,
    )
    return {
        "get_aws_region": get_aws_region,
        "get_codeartifact_config": get_codeartifact_config,
        "get_codeartifact_repo_name": _manifest_get_ca_repo,
    }


def _get_codeartifact_repo_name(app_type: str, repo_root: Optional[Path] = None) -> Optional[str]:
    """
    Get CodeArtifact repository name based on app type and git branch.

    Branch determines snapshot vs release:
    - main/master → release repos
    - any other branch → snapshot repos

    Returns None if no repository is configured for this type/mode combination
    (e.g., Scala release repos may not be provisioned yet).
    """
    from platform_cli.tools.app.version_manager import get_codeartifact_mode
    cfg = _load_platform_config()
    mode = get_codeartifact_mode(repo_root)
    ca = cfg["get_codeartifact_config"]()
    repos = ca.get("repositories", {}).get(app_type, {})
    repo = repos.get(mode, "")
    if not repo:
        return None
    return repo


def _get_codeartifact_token(domain: str, domain_owner: str, region: str) -> str:
    """Get CodeArtifact authorization token."""
    result = run_cmd(
        [
            "aws", "codeartifact", "get-authorization-token",
            "--domain", domain,
            "--domain-owner", domain_owner,
            "--region", region,
            "--query", "authorizationToken",
            "--output", "text",
        ],
        check=False,
        capture=True,
    )
    if result.returncode != 0:
        raise PlatformError(
            f"Failed to get CodeArtifact auth token: {result.stderr}",
            code="E_CODEARTIFACT_AUTH_FAILED",
            reason="auth_token",
        )
    return result.stdout.strip()


def _get_codeartifact_url(domain: str, domain_owner: str, repo: str, region: str, fmt: str = "pypi") -> str:
    """Get CodeArtifact repository endpoint URL."""
    result = run_cmd(
        [
            "aws", "codeartifact", "get-repository-endpoint",
            "--domain", domain,
            "--domain-owner", domain_owner,
            "--repository", repo,
            "--format", fmt,
            "--region", region,
            "--query", "repositoryEndpoint",
            "--output", "text",
        ],
        check=False,
        capture=True,
    )
    if result.returncode != 0:
        raise PlatformError(
            f"Failed to get CodeArtifact endpoint: {result.stderr}",
            code="E_CODEARTIFACT_ENDPOINT_FAILED",
            reason="endpoint",
        )
    return result.stdout.strip()


def _get_account_id() -> str:
    """Get current AWS account ID."""
    result = run_cmd(
        ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
        check=False,
        capture=True,
    )
    if result.returncode != 0:
        raise PlatformError(
            f"Failed to get AWS account ID: {result.stderr}",
            code="E_AWS_AUTH_FAILED",
            reason="sts",
        )
    return result.stdout.strip()


def _publish_to_codeartifact(
    app_dir: Path,
    domain: str,
    domain_owner: str,
    repo: str,
    region: str,
) -> str:
    """Publish Python wheel to CodeArtifact using uv publish."""
    dist_dir = app_dir / "dist"
    if not dist_dir.exists() or not list(dist_dir.glob("*.whl")):
        raise PlatformError(
            f"No wheel found in {dist_dir}. Run 'ghdp build' first.",
            code="E_NO_BUILD_ARTIFACT",
            reason=str(dist_dir),
        )

    token = _get_codeartifact_token(domain, domain_owner, region)
    endpoint = _get_codeartifact_url(domain, domain_owner, repo, region)

    # uv publish uses --publish-url for the upload endpoint
    # CodeArtifact returns the correct endpoint for publishing (no /legacy/ needed)
    publish_url = endpoint.rstrip("/")

    result = run_cmd(
        [
            "uv", "publish",
            "--publish-url", publish_url,
            "--token", token,
        ],
        cwd=str(app_dir),
        check=False,
    )
    if result.returncode != 0:
        raise PlatformError(
            f"uv publish failed: {result.stderr}",
            code="E_UV_PUBLISH_FAILED",
            reason="codeartifact",
        )

    return f"{endpoint}{app_dir.name}/"


def _publish_maven_to_codeartifact(
    app_dir: Path,
    domain: str,
    domain_owner: str,
    repo: str,
    region: str,
) -> str:
    """Publish Maven JAR to CodeArtifact using mvn deploy:deploy-file.

    Uses pom.xml artifactId natively — the developer controls the package name.
    """
    target_dir = app_dir / "target"
    if not target_dir.exists() or not list(target_dir.glob("*.jar")):
        raise PlatformError(
            f"No JAR found in {target_dir}. Run 'ghdp build' first.",
            code="E_NO_BUILD_ARTIFACT",
            reason=str(target_dir),
        )

    # Prefer .flattened-pom.xml (CI-friendly versions resolved) over pom.xml
    flattened_pom = app_dir / ".flattened-pom.xml"
    pom_path = flattened_pom if flattened_pom.exists() else app_dir / "pom.xml"
    if not pom_path.exists():
        raise PlatformError(
            f"pom.xml not found in {app_dir}",
            code="E_POM_NOT_FOUND",
            reason=str(app_dir),
        )

    token = _get_codeartifact_token(domain, domain_owner, region)
    maven_endpoint = _get_codeartifact_url(domain, domain_owner, repo, region, fmt="maven")

    # Find the shaded JAR (not original-*.jar)
    jars = [j for j in target_dir.glob("*.jar") if not j.name.startswith("original-")]
    if not jars:
        raise PlatformError(
            f"No deployable JAR found in {target_dir}",
            code="E_NO_BUILD_ARTIFACT",
            reason=str(target_dir),
        )
    jar_path = jars[0]

    # Create temporary settings.xml with CodeArtifact auth using tempfile
    # for safety — avoids leaving tokens on disk if process is killed
    import tempfile
    settings_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<settings>
  <servers>
    <server>
      <id>codeartifact</id>
      <username>aws</username>
      <password>{token}</password>
    </server>
  </servers>
</settings>"""

    deploy_url = maven_endpoint.rstrip("/")
    tmp_fd = tempfile.NamedTemporaryFile(
        mode="w", suffix=".xml", prefix="mvn-ca-settings-",
        dir=str(app_dir), delete=False,
    )
    try:
        tmp_fd.write(settings_content)
        tmp_fd.close()
        settings_path = Path(tmp_fd.name)

        result = run_cmd(
            [
                "mvn", "deploy:deploy-file",
                f"-Dfile={jar_path}",
                f"-DpomFile={pom_path}",
                f"-DrepositoryId=codeartifact",
                f"-Durl={deploy_url}",
                f"--settings={settings_path}",
            ],
            cwd=str(app_dir),
            check=False,
        )
        if result.returncode != 0:
            raise PlatformError(
                f"Maven deploy failed: {result.stderr}",
                code="E_MVN_DEPLOY_FAILED",
                reason="codeartifact",
            )
    finally:
        tmp_path = Path(tmp_fd.name)
        tmp_fd.close()  # ensure fd closed before unlink
        if tmp_path.exists():
            tmp_path.unlink()

    return f"{maven_endpoint}"


def _push_to_ecr(
    app: Any,
    region: str,
    repo_root: Path,
    env: Optional[str] = None,
) -> str:
    """
    Push Docker image to ECR following Jenkins dockerBuild tagging convention.

    Tags pushed (matching dockerBuild.groovy):
      {component}-{git_short_hash}   (version tag — always)
      {component}-{env}              (workspace tag — only if env provided)
      {component}-latest             (latest tag — always)

    Where component comes from docker_details.component (e.g., "historical-load")
    """
    if not app.ecr_repository:
        raise PlatformError(
            f"App '{app.path}' has docker in tools but no docker_details.ecr_repository specified in apps.json",
            code="E_ECR_REPOSITORY_NOT_SPECIFIED",
            reason=app.path,
        )

    git_hash = get_short_commit_hash(repo_root)
    component = app.component  # from docker_details.component (e.g., "historical-load")

    # Use shared helper for local tag (single source of truth with docker_builder)
    from platform_cli.tools.app.docker_builder import get_local_docker_tag
    local_tag = get_local_docker_tag(app, git_hash)

    # Tags matching Jenkins dockerBuild.groovy:
    tag_version = f"{component}-{git_hash}"
    tag_latest = f"{component}-latest"

    account_id = _get_account_id()

    # Resolve {account} placeholder in ecr_repository using account-environments manifest
    from platform_cli.manifests.load import get_account_alias_by_id
    account_alias = get_account_alias_by_id(account_id)
    if not account_alias:
        raise PlatformError(
            f"AWS account {account_id} not found in account-environments manifest. "
            "Cannot resolve ECR repository name.",
            code="E_ACCOUNT_NOT_CONFIGURED",
            reason=account_id,
        )
    resolved_ecr_repo = app.ecr_repository.replace("{account}", account_alias)
    ecr_registry = f"{account_id}.dkr.ecr.{region}.amazonaws.com"
    ecr_repo = f"{ecr_registry}/{resolved_ecr_repo}"

    # ECR login — cross-platform (no bash -c dependency)
    # Step 1: Get ECR login password
    pwd_result = run_cmd(
        ["aws", "ecr", "get-login-password", "--region", region],
        check=False,
        capture=True,
    )
    if pwd_result.returncode != 0:
        raise PlatformError(
            f"ECR get-login-password failed: {pwd_result.stderr}",
            code="E_ECR_LOGIN_FAILED",
            reason="ecr-get-password",
        )
    ecr_password = pwd_result.stdout.strip()

    # Step 2: Docker login using --password-stdin via run_cmd (cross-platform, no bash -c)
    docker_login = run_cmd(
        ["docker", "login", "--username", "AWS", "--password-stdin", ecr_registry],
        check=False,
        capture=True,
        input_text=ecr_password,
    )
    if docker_login.returncode != 0:
        raise PlatformError(
            f"Docker login to ECR failed: {docker_login.stderr}",
            code="E_ECR_LOGIN_FAILED",
            reason="docker-login",
        )

    # Tag and push (matching Jenkins dockerBuildUpload)
    ecr_tags = [tag_version, tag_latest]
    if env:
        ecr_tags.insert(1, f"{component}-{env}")
    pushed_tags = []

    for ecr_tag_suffix in ecr_tags:
        ecr_full_tag = f"{ecr_repo}:{ecr_tag_suffix}"

        tag_result = run_cmd(
            ["docker", "tag", local_tag, ecr_full_tag],
            check=False,
        )
        if tag_result.returncode != 0:
            raise PlatformError(
                f"Docker tag failed for {ecr_tag_suffix}: {tag_result.stderr}",
                code="E_DOCKER_TAG_FAILED",
                reason=app.path,
            )

        print(f"  Pushing {ecr_full_tag}...")
        push_result = run_cmd(
            ["docker", "push", ecr_full_tag],
            check=False,
        )
        if push_result.returncode != 0:
            raise PlatformError(
                f"Docker push failed for {ecr_tag_suffix}: {push_result.stderr}",
                code="E_ECR_PUSH_FAILED",
                reason=app.path,
            )
        pushed_tags.append(ecr_full_tag)

    return ", ".join(pushed_tags)


def publish_app(
    app: Any,  # AppConfig
    context: Dict[str, Any],
    repo_root: Path,
    env: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Publish app artifacts to CodeArtifact and/or ECR.

    CodeArtifact repo selection is branch-based (snapshot vs release).
    The env parameter is only used for Docker's {component}-{env} tag.

    Args:
        app: AppConfig instance
        context: Publish context (verbose, quiet flags)
        repo_root: Root path of data-product repo
        env: Optional environment — only used for Docker env tag

    Returns:
        Dict with codeartifact_uri and/or ecr_uri
    """
    app_dir = repo_root / "apps" / app.path
    result: Dict[str, Any] = {}

    # Load config from manifest
    cfg = _load_platform_config()
    ca_config = cfg["get_codeartifact_config"]()
    ca_domain = ca_config.get("domain", "")
    ca_domain_owner = ca_config.get("domain_owner", "")
    region = cfg["get_aws_region"]()

    # Determine target CodeArtifact repo based on app type + branch (snapshot/release)
    # Returns None if no repo is configured (e.g., Scala release not provisioned)
    ca_repo = _get_codeartifact_repo_name(app.type, repo_root)

    if ca_repo and (app.type in ("python", "scala")):
        if not ca_domain or not ca_domain_owner:
            raise PlatformError(
                "CodeArtifact domain/domain_owner not configured in account-environments.json",
                code="E_CODEARTIFACT_CONFIG_MISSING",
                reason="missing_domain_config",
            )

    if app.type == "python":
        if not ca_repo:
            raise PlatformError(
                "No CodeArtifact repository configured for Python in current mode",
                code="E_CODEARTIFACT_REPO_NOT_CONFIGURED",
                reason="python",
            )
        print(f"  Publishing to CodeArtifact ({ca_repo})...")
        ca_uri = _publish_to_codeartifact(
            app_dir=app_dir,
            domain=ca_domain,
            domain_owner=ca_domain_owner,
            repo=ca_repo,
            region=region,
        )
        result["codeartifact_uri"] = ca_uri
    elif app.type == "scala":
        if ca_repo:
            print(f"  Publishing Maven artifact to CodeArtifact ({ca_repo})...")
            ca_uri = _publish_maven_to_codeartifact(
                app_dir=app_dir,
                domain=ca_domain,
                domain_owner=ca_domain_owner,
                repo=ca_repo,
                region=region,
            )
            result["codeartifact_uri"] = ca_uri
        else:
            print(f"  Skipping CodeArtifact publish for Scala (no repository configured for current mode)")

    # Push Docker image if app requires it
    if app.needs_docker:
        print(f"  Pushing Docker image to ECR...")
        ecr_uri = _push_to_ecr(app=app, region=region, repo_root=repo_root, env=env)
        result["ecr_uri"] = ecr_uri

    return result
