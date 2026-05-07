from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from platform_cli.core.config import get_value
from platform_cli.core.errors import PlatformError
from platform_cli.exec.runner import run_cmd
from platform_cli.tools.jenkins_release_api import JenkinsApiClient, JenkinsBuildObservation
from platform_cli.tools.git_repo import get_current_branch, get_repo_name

DEFAULT_JENKINS_MCP_URL = "https://ai.npdata.guardanthealth.com/tool/jenkins/mcp/"
DEFAULT_JENKINS_BASE_URL = "https://jenkins.npdata.guardanthealth.com"
OKTA_EMAIL_CONFIG_KEY = "jenkins.okta_email"
JENKINS_API_TOKEN_CONFIG_KEY = "jenkins.api_token"
FEATURE_BRANCH_PREFIX = "feature/"
RELEASE_TYPE_CHOICES = ("major", "minor", "bugfix")
PR_LOOKUP_BASE_BRANCH = "develop"
PR_LOOKUP_TIMEOUT_S = 60
PR_LOOKUP_POLL_INTERVAL_S = 5
DIRECT_BACKEND = "jenkins_api"
MCP_BACKEND = "mcp"

_BUILD_URL_RE = re.compile(r"https?://\S+/job/\S+/\d+/?")
_PR_URL_RE = re.compile(r"https?://github\.com/\S+/pull/\d+")
_AUTH_FAILURE_HINTS = (
    "unauthorized",
    "forbidden",
    "authentication failed",
    "authentication error",
    "invalid api token",
    "invalid token",
    "expired token",
    "bad credentials",
)


@dataclass(frozen=True)
class RepoIdentity:
    repo_name: str
    full_name: str


@dataclass(frozen=True)
class ReleaseCredentials:
    email: str
    jenkins_api_token: str
    github_api_token: str = ""


@dataclass(frozen=True)
class JenkinsReleaseResult:
    message: str
    build_url: str = ""
    build_number: int | None = None
    pull_request_url: str = ""
    status: str = ""
    queue_url: str = ""
    console_url: str = ""
    diagnostic_reason: str = ""
    artifact_urls: tuple[str, ...] = ()


def is_feature_branch(branch: str) -> bool:
    return (branch or "").strip().lower().startswith(FEATURE_BRANCH_PREFIX)


def normalize_release_type(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in RELEASE_TYPE_CHOICES:
        raise PlatformError(
            f"Invalid release type '{value}'. Allowed values: {', '.join(RELEASE_TYPE_CHOICES)}",
            code="E_RELEASE_TYPE_INVALID",
            reason="release_type",
        )
    return normalized


def resolve_current_branch_name() -> str:
    branch = (get_current_branch() or "").strip()
    if not branch or branch == "unknown":
        raise PlatformError(
            "Could not detect the current git branch. Run GHDP from inside the target repository or pass --branch.",
            code="E_BRANCH_NOT_DETECTED",
            reason="branch_detection",
        )
    return branch


def resolve_repo_identity(repo: str | None = None) -> RepoIdentity:
    raw = (repo or "").strip()
    if raw:
        if "/" in raw:
            return RepoIdentity(repo_name=raw.rsplit("/", 1)[-1], full_name=raw)
        return RepoIdentity(repo_name=raw, full_name=_best_effort_full_repo_name(raw))

    full_name = _best_effort_full_repo_name("")
    if full_name:
        return RepoIdentity(repo_name=full_name.rsplit("/", 1)[-1], full_name=full_name)

    repo_name = (get_repo_name() or "").strip()
    if repo_name and repo_name != "unknown":
        return RepoIdentity(repo_name=repo_name, full_name="")

    raise PlatformError(
        "Could not determine the current GitHub repository. Run GHDP from inside the target repo or pass --repo.",
        code="E_REPO_NOT_DETECTED",
        reason="repo_detection",
    )


def resolve_release_credentials(
    *,
    require_github_token: bool,
    email: str | None = None,
    jenkins_api_token: str | None = None,
    github_api_token: str | None = None,
) -> ReleaseCredentials:
    resolved_email = _normalize_email(_first_nonempty_lazy(lambda: email, resolve_okta_email))
    resolved_jenkins_token = _first_nonempty_lazy(lambda: jenkins_api_token, resolve_jenkins_api_token)
    resolved_github_token = _first_nonempty_lazy(lambda: github_api_token, resolve_github_api_token)

    if not resolved_email:
        raise PlatformError(
            "An Okta email is required for Jenkins release execution. GHDP checked env/config, GitHub CLI, ACLI, and git config. Set OKTA_USER_EMAIL or configure `ghdp config jenkins-okta-email --email user@guardanthealth.com` if inference is unavailable.",
            code="E_RELEASE_OKTA_EMAIL_MISSING",
            reason="release_auth",
        )
    if not resolved_jenkins_token:
        raise PlatformError(
            "A Jenkins API token is required. Set JENKINS_API_TOKEN or provide it interactively before retrying.",
            code="E_RELEASE_JENKINS_TOKEN_MISSING",
            reason="release_auth",
        )
    if require_github_token and not resolved_github_token:
        raise PlatformError(
            "A GitHub API token is required for feature-to-dev. Log in with `gh auth login` or set GITHUB_API_TOKEN.",
            code="E_RELEASE_GITHUB_TOKEN_MISSING",
            reason="release_auth",
        )

    return ReleaseCredentials(
        email=resolved_email,
        jenkins_api_token=resolved_jenkins_token,
        github_api_token=resolved_github_token,
    )


def resolve_okta_email() -> str:
    return _normalize_email(
        _first_nonempty_lazy(
            lambda: os.getenv("OKTA_USER_EMAIL"),
            lambda: get_value(OKTA_EMAIL_CONFIG_KEY, ""),
            _gh_auth_email,
            _acli_auth_email,
            _git_user_email,
        )
    )


def resolve_jenkins_api_token() -> str:
    return _first_nonempty(
        os.getenv("JENKINS_API_TOKEN"),
        os.getenv("GHDP_JENKINS_API_TOKEN"),
        get_value(JENKINS_API_TOKEN_CONFIG_KEY, ""),
    )


def resolve_jenkins_api_token_with_source() -> tuple[str, str]:
    env_token = _first_nonempty(
        os.getenv("JENKINS_API_TOKEN"),
        os.getenv("GHDP_JENKINS_API_TOKEN"),
    )
    if env_token:
        return env_token, "env"

    config_token = _first_nonempty(get_value(JENKINS_API_TOKEN_CONFIG_KEY, ""))
    if config_token:
        return config_token, "config"

    return "", ""


def resolve_github_api_token() -> str:
    return _first_nonempty_lazy(
        lambda: os.getenv("GITHUB_API_TOKEN"),
        lambda: os.getenv("GHDP_GITHUB_API_TOKEN"),
        _gh_auth_token,
    )


def run_feature_to_dev(
    *,
    repo_name: str,
    branch: str,
    credentials: ReleaseCredentials,
    deploy_on_sqa: bool,
    contract: Mapping[str, Any] | None = None,
    timeout_s: int = 900,
    status_printer: Callable[[str], None] | None = None,
) -> JenkinsReleaseResult:
    backend = _resolve_flow_backend(contract=contract, flow_name="feature_to_dev")
    if status_printer is not None:
        status_printer("Triggering Jenkins job...")
    if backend == MCP_BACKEND:
        payload = call_jenkins_mcp_tool(
            tool_name="create_pull_request",
            arguments={
                "github_repository_name": repo_name,
                "branch": branch,
                "email": credentials.email,
                "jenkins_api_token": credentials.jenkins_api_token,
                "github_api_token": credentials.github_api_token,
                "deploy_on_sqa": deploy_on_sqa,
            },
            timeout_s=timeout_s,
        )
        result = _result_from_payload(payload)
        return _finalize_feature_to_dev_result(
            result=result,
            repo_name=repo_name,
            branch=branch,
            credentials=credentials,
            timeout_s=timeout_s,
            status_printer=status_printer,
        )

    flow_policy = _flow_policy(contract=contract, flow_name="feature_to_dev")
    return _run_feature_to_dev_via_direct_api(
        repo_name=repo_name,
        branch=branch,
        credentials=credentials,
        deploy_on_sqa=deploy_on_sqa,
        flow_policy=flow_policy,
        timeout_s=timeout_s,
        status_printer=status_printer,
    )


def run_make_release(
    *,
    repo_name: str,
    credentials: ReleaseCredentials,
    release_type: str,
    parent: str,
    params: Mapping[str, Any] | None = None,
    contract: Mapping[str, Any] | None = None,
    timeout_s: int = 900,
) -> JenkinsReleaseResult:
    backend = _resolve_flow_backend(contract=contract, flow_name="make_release")
    if backend == MCP_BACKEND:
        payload = call_jenkins_mcp_tool(
            tool_name="create_release",
            arguments={
                "github_repository_name": repo_name,
                "branch": "develop",
                "email": credentials.email,
                "jenkins_api_token": credentials.jenkins_api_token,
                "release_type": normalize_release_type(release_type),
                "parent": (parent or "").strip(),
                "params": dict(params or {}),
            },
            timeout_s=timeout_s,
        )
        return _result_from_payload(payload)

    flow_policy = _flow_policy(contract=contract, flow_name="make_release")
    return _run_make_release_via_direct_api(
        repo_name=repo_name,
        credentials=credentials,
        release_type=release_type,
        parent=parent,
        params=params,
        flow_policy=flow_policy,
        timeout_s=timeout_s,
    )


def execute_with_jenkins_token_refresh(
    *,
    credentials: ReleaseCredentials,
    token_source: str,
    runner: Callable[[ReleaseCredentials], JenkinsReleaseResult],
    token_refresher: Callable[..., str],
    non_interactive: bool,
) -> JenkinsReleaseResult:
    current_credentials = credentials
    refreshed = False
    while True:
        try:
            return runner(current_credentials)
        except PlatformError as exc:
            if not is_probable_jenkins_auth_failure(exc):
                raise
            if token_source == "env":
                raise
            if non_interactive:
                raise PlatformError(
                    "The stored Jenkins API token appears invalid or expired. Update it with `ghdp config jenkins-api-token --token ...`, clear it with `ghdp config jenkins-api-token --clear`, or set JENKINS_API_TOKEN and retry.",
                    code="E_RELEASE_JENKINS_TOKEN_INVALID",
                    reason="release_auth",
                ) from exc
            if refreshed:
                raise

            replacement = token_refresher(
                prompt_text="Stored Jenkins API token appears invalid or expired. Enter a new Jenkins API token",
            )
            current_credentials = ReleaseCredentials(
                email=current_credentials.email,
                jenkins_api_token=replacement,
                github_api_token=current_credentials.github_api_token,
            )
            token_source = "prompt"
            refreshed = True


def _resolve_flow_backend(*, contract: Mapping[str, Any] | None, flow_name: str) -> str:
    forced = (os.getenv("GHDP_RELEASE_BACKEND") or "").strip().lower()
    if forced in {DIRECT_BACKEND, MCP_BACKEND}:
        return forced
    policy = _flow_policy(contract=contract, flow_name=flow_name)
    if not policy:
        return MCP_BACKEND
    backend = str(policy.get("execution_backend") or policy.get("backend") or DIRECT_BACKEND).strip().lower()
    if backend in {DIRECT_BACKEND, MCP_BACKEND}:
        return backend
    return DIRECT_BACKEND


def _flow_policy(*, contract: Mapping[str, Any] | None, flow_name: str) -> Mapping[str, Any]:
    if not isinstance(contract, Mapping):
        return {}
    flows = contract.get("flows")
    if not isinstance(flows, Mapping):
        return {}
    policy = flows.get(flow_name)
    return policy if isinstance(policy, Mapping) else {}


def _jenkins_client(*, credentials: ReleaseCredentials, timeout_s: int) -> JenkinsApiClient:
    return JenkinsApiClient(
        user=credentials.email,
        token=credentials.jenkins_api_token,
        base_url=(os.getenv("GHDP_JENKINS_BASE_URL") or DEFAULT_JENKINS_BASE_URL).strip(),
        timeout_s=timeout_s,
    )


def _run_feature_to_dev_via_direct_api(
    *,
    repo_name: str,
    branch: str,
    credentials: ReleaseCredentials,
    deploy_on_sqa: bool,
    flow_policy: Mapping[str, Any],
    timeout_s: int,
    status_printer: Callable[[str], None] | None = None,
) -> JenkinsReleaseResult:
    job_path = str(flow_policy.get("job_path") or "").strip()
    if not job_path:
        raise PlatformError(
            "The repo-local Jenkins contract is missing the feature-to-dev Jenkins job path.",
            code="E_RELEASE_JENKINS_JOB_MISSING",
            reason="feature_to_dev",
        )
    queue_timeout_s = _int_policy(flow_policy, "queue_timeout_s", fallback=180)
    build_timeout_s = _int_policy(flow_policy, "build_timeout_s", fallback=timeout_s)
    poll_interval_s = _int_policy(flow_policy, "poll_interval_s", fallback=2)

    client = _jenkins_client(credentials=credentials, timeout_s=min(timeout_s, build_timeout_s))
    handle = client.trigger_job(
        job_path=job_path,
        params={
            "GITHUB_TOKEN": credentials.github_api_token,
            "REPOSITORY": repo_name,
            "FEATURE_BRANCH_NAME": branch,
            "DEPLOY_ON_SQA": str(bool(deploy_on_sqa)).lower(),
        },
    )
    if status_printer is not None:
        status_printer("Waiting for Jenkins to assign a build...")
    build = client.wait_for_build_number(handle, timeout_s=queue_timeout_s, poll_interval_s=poll_interval_s)
    if status_printer is not None:
        status_printer(f"Jenkins build started: {build.build_url}")
    completed = client.wait_for_build_completion(
        job_path=job_path,
        build_number=build.build_number,
        timeout_s=build_timeout_s,
        poll_interval_s=poll_interval_s,
    )
    if status_printer is not None:
        status_printer("Inspecting Jenkins build outcome...")
    console_text = client.get_console_text(job_path=job_path, build_number=build.build_number)
    result = _result_from_direct_observation(
        observation=completed,
        console_text=console_text,
        queue_url=handle.queue_url,
        branch=branch,
    )
    return _finalize_feature_to_dev_result(
        result=result,
        repo_name=repo_name,
        branch=branch,
        credentials=credentials,
        timeout_s=timeout_s,
        status_printer=status_printer,
    )


def _run_make_release_via_direct_api(
    *,
    repo_name: str,
    credentials: ReleaseCredentials,
    release_type: str,
    parent: str,
    params: Mapping[str, Any] | None,
    flow_policy: Mapping[str, Any],
    timeout_s: int,
) -> JenkinsReleaseResult:
    job_path = str(flow_policy.get("job_path") or "").strip()
    if not job_path:
        raise PlatformError(
            "The repo-local Jenkins contract is missing the make-release Jenkins job path.",
            code="E_RELEASE_JENKINS_JOB_MISSING",
            reason="make_release",
        )
    queue_timeout_s = _int_policy(flow_policy, "queue_timeout_s", fallback=180)
    poll_interval_s = _int_policy(flow_policy, "poll_interval_s", fallback=2)
    client = _jenkins_client(credentials=credentials, timeout_s=timeout_s)
    release_params = dict(params or {})
    release_params.update(
        {
            "PARENT": (parent or "").strip(),
            "RELEASE_TYPE": normalize_release_type(release_type),
            "REPOSITORY": repo_name,
            "SOURCE_BRANCH": "develop",
            "APPLY": str(_normalize_bool_param(release_params.get("APPLY"), default=True)).lower(),
            "TESTED_OK_ON_UAT": str(_normalize_bool_param(release_params.get("TESTED_OK_ON_UAT"), default=True)).lower(),
        }
    )
    handle = client.trigger_job(job_path=job_path, params=release_params)
    build = client.wait_for_build_number(handle, timeout_s=queue_timeout_s, poll_interval_s=poll_interval_s)
    return JenkinsReleaseResult(
        message=f"Release creation has been triggered successfully. Build URL: {build.build_url}",
        build_url=build.build_url,
        build_number=build.build_number,
        status="QUEUED",
        queue_url=handle.queue_url,
        console_url=f"{build.build_url}/consoleText",
    )


def call_jenkins_mcp_tool(*, tool_name: str, arguments: Mapping[str, Any], timeout_s: int) -> Any:
    session_id = _initialize_mcp_session(timeout_s=timeout_s)
    _send_notification(
        session_id=session_id,
        method="notifications/initialized",
        params={},
        timeout_s=timeout_s,
    )
    response = _post_mcp_json(
        session_id=session_id,
        payload={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": dict(arguments),
            },
        },
        timeout_s=timeout_s,
    )
    if not isinstance(response, dict):
        raise PlatformError(
            f"Unexpected Jenkins MCP response for tool '{tool_name}'.",
            code="E_RELEASE_MCP_RESPONSE_INVALID",
            reason="mcp_response",
        )

    if "error" in response:
        message = _stringify_mcp_payload(response.get("error")) or f"Tool call failed: {tool_name}"
        raise PlatformError(
            message,
            code="E_RELEASE_MCP_TOOL_FAILED",
            reason=tool_name,
        )

    if "result" not in response:
        raise PlatformError(
            f"Jenkins MCP returned no result for tool '{tool_name}'.",
            code="E_RELEASE_MCP_RESPONSE_INVALID",
            reason="mcp_response",
        )

    return response["result"]


def _initialize_mcp_session(*, timeout_s: int) -> str:
    response, headers = _post_mcp_json_raw(
        session_id="",
        payload={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ghdp", "version": "0.0.0"},
            },
        },
        timeout_s=timeout_s,
        include_session=False,
    )
    session_id = (headers.get("mcp-session-id") or "").strip()
    if not session_id:
        raise PlatformError(
            "Jenkins MCP did not return a session id during initialization.",
            code="E_RELEASE_MCP_SESSION_MISSING",
            reason="mcp_session",
        )
    if "error" in response:
        message = _stringify_mcp_payload(response.get("error")) or "Failed to initialize Jenkins MCP session."
        raise PlatformError(
            message,
            code="E_RELEASE_MCP_INIT_FAILED",
            reason="mcp_initialize",
        )
    return session_id


def _send_notification(*, session_id: str, method: str, params: Mapping[str, Any], timeout_s: int) -> None:
    _post_mcp_json_raw(
        session_id=session_id,
        payload={
            "jsonrpc": "2.0",
            "method": method,
            "params": dict(params),
        },
        timeout_s=timeout_s,
    )


def _post_mcp_json(session_id: str, payload: Mapping[str, Any], timeout_s: int) -> Any:
    parsed, _headers = _post_mcp_json_raw(
        session_id=session_id,
        payload=payload,
        timeout_s=timeout_s,
    )
    return parsed


def _post_mcp_json_raw(
    *,
    session_id: str,
    payload: Mapping[str, Any],
    timeout_s: int,
    include_session: bool = True,
) -> tuple[Any, Mapping[str, str]]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if include_session and session_id:
        headers["mcp-session-id"] = session_id

    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        _jenkins_mcp_url(),
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return _parse_mcp_http_body(raw), dict(response.headers.items())
    except urllib.error.HTTPError as e:
        error_text = ""
        try:
            error_text = e.read().decode("utf-8", errors="replace")
        except Exception:
            error_text = ""
        message = _stringify_mcp_payload(_parse_mcp_http_body(error_text)) or error_text or str(e)
        raise PlatformError(
            f"Jenkins MCP request failed ({e.code}): {message}",
            code="E_RELEASE_MCP_HTTP",
            reason=str(e.code),
        )
    except urllib.error.URLError as e:
        raise PlatformError(
            f"Could not reach Jenkins MCP: {e.reason}",
            code="E_RELEASE_MCP_UNREACHABLE",
            reason="mcp_network",
        )
    except TimeoutError:
        raise PlatformError(
            "Jenkins MCP request timed out.",
            code="E_RELEASE_MCP_TIMEOUT",
            reason="mcp_timeout",
        )


def _parse_mcp_http_body(raw: str) -> Any:
    text = (raw or "").strip()
    if not text:
        return {}

    data_events: list[str] = []
    current_event: list[str] = []
    for raw_line in text.splitlines():
        clean = raw_line.rstrip("\r")
        if clean.startswith("data:"):
            current_event.append(clean[len("data:") :].strip())
            continue
        if not clean:
            if current_event:
                data_events.append("\n".join(current_event).strip())
                current_event = []
            continue

    if current_event:
        data_events.append("\n".join(current_event).strip())

    if data_events:
        for item in reversed(data_events):
            if not item:
                continue
            try:
                return json.loads(item)
            except Exception:
                continue
        return {"raw": data_events[-1]}

    try:
        return json.loads(text)
    except Exception:
        return {"raw": text}


def _result_from_payload(payload: Any) -> JenkinsReleaseResult:
    message = _stringify_mcp_payload(payload)
    build_url = _extract_build_url(message)
    pr_url = _extract_pull_request_url(message)
    build_number = _extract_build_number(build_url)
    return JenkinsReleaseResult(
        message=message,
        build_url=build_url,
        build_number=build_number,
        pull_request_url=pr_url,
        console_url=f"{build_url.rstrip('/')}/consoleText" if build_url else "",
    )


def _result_from_direct_observation(
    *,
    observation: JenkinsBuildObservation,
    console_text: str,
    queue_url: str,
    branch: str,
) -> JenkinsReleaseResult:
    pr_url = _extract_pull_request_url(console_text)
    result = observation.result or ""
    message = f"Pull request creation job triggered successfully. Build URL: {observation.build_url}"
    if pr_url:
        message = f"{message}\nPull Request Link: {pr_url}"
    else:
        message = f"{message}\nPull Request Link: Not found in Jenkins logs."
    return JenkinsReleaseResult(
        message=message,
        build_url=observation.build_url,
        build_number=observation.build_number,
        pull_request_url=pr_url,
        status=result,
        queue_url=queue_url,
        console_url=f"{observation.build_url.rstrip('/')}/consoleText",
        diagnostic_reason=_feature_to_dev_diagnostic_from_logs(console_text=console_text, branch=branch),
        artifact_urls=tuple(observation.artifact_urls),
    )


def _finalize_feature_to_dev_result(
    *,
    result: JenkinsReleaseResult,
    repo_name: str,
    branch: str,
    credentials: ReleaseCredentials,
    timeout_s: int,
    status_printer: Callable[[str], None] | None = None,
) -> JenkinsReleaseResult:
    if result.pull_request_url or not result.build_url:
        return result

    repo_full_name = _best_effort_full_repo_name(repo_name) or repo_name
    pr_url = _poll_github_pull_request_url(
        repo_full_name=repo_full_name,
        branch=branch,
        timeout_s=min(timeout_s, PR_LOOKUP_TIMEOUT_S),
        status_printer=status_printer,
    )
    if pr_url:
        return JenkinsReleaseResult(
            message=_append_message_line(
                result.message,
                f"Pull request creation job triggered successfully. Pull request resolved via GitHub CLI: {pr_url}",
            ),
            build_url=result.build_url,
            build_number=result.build_number,
            pull_request_url=pr_url,
        )

    diagnostic = result.diagnostic_reason or _feature_to_dev_diagnostic_message(
        repo_name=repo_name,
        branch=branch,
        build_number=result.build_number,
        credentials=credentials,
        timeout_s=timeout_s,
    )
    if not diagnostic:
        diagnostic = (
            "Pull Request Link: Not found in Jenkins logs or via GitHub CLI yet."
        )

    return JenkinsReleaseResult(
        message=_append_message_line(result.message, diagnostic),
        build_url=result.build_url,
        build_number=result.build_number,
        pull_request_url="",
        status=result.status,
        queue_url=result.queue_url,
        console_url=result.console_url,
        diagnostic_reason=diagnostic,
        artifact_urls=result.artifact_urls,
    )


def _stringify_mcp_payload(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, dict):
        if "content" in payload and isinstance(payload["content"], list):
            parts: list[str] = []
            for item in payload["content"]:
                if isinstance(item, dict):
                    text = str(item.get("text") or "").strip()
                    if text:
                        parts.append(text)
                elif isinstance(item, str) and item.strip():
                    parts.append(item.strip())
            if parts:
                return "\n".join(parts).strip()
        if "text" in payload and str(payload.get("text") or "").strip():
            return str(payload.get("text")).strip()
        if "message" in payload and str(payload.get("message") or "").strip():
            return str(payload.get("message")).strip()
        if "raw" in payload and str(payload.get("raw") or "").strip():
            return str(payload.get("raw")).strip()
        try:
            return json.dumps(payload, indent=2, sort_keys=True)
        except Exception:
            return str(payload).strip()
    if isinstance(payload, list):
        parts = [_stringify_mcp_payload(item) for item in payload]
        return "\n".join(part for part in parts if part).strip()
    return str(payload).strip()


def _extract_build_url(text: str) -> str:
    match = _BUILD_URL_RE.search(text or "")
    return match.group(0) if match else ""


def _extract_build_number(build_url: str) -> int | None:
    if not build_url:
        return None
    suffix = build_url.rstrip("/").rsplit("/", 1)[-1]
    if suffix.isdigit():
        return int(suffix)
    return None


def _extract_pull_request_url(text: str) -> str:
    match = _PR_URL_RE.search(text or "")
    return match.group(0) if match else ""


def _poll_github_pull_request_url(
    *,
    repo_full_name: str,
    branch: str,
    timeout_s: int,
    status_printer: Callable[[str], None] | None = None,
) -> str:
    if not repo_full_name:
        return ""

    deadline = time.time() + max(timeout_s, 0)
    announced = False
    while True:
        if not announced:
            if status_printer is not None:
                status_printer("Resolving pull request via GitHub CLI...")
            announced = True
        pr_url = _find_pull_request_url_via_gh(repo_full_name=repo_full_name, branch=branch)
        if pr_url:
            return pr_url
        if time.time() >= deadline:
            return ""
        time.sleep(PR_LOOKUP_POLL_INTERVAL_S)


def _find_pull_request_url_via_gh(*, repo_full_name: str, branch: str) -> str:
    result = run_cmd(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo_full_name,
            "--head",
            branch,
            "--base",
            PR_LOOKUP_BASE_BRANCH,
            "--state",
            "all",
            "--json",
            "url,number,state,updatedAt",
        ],
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        return ""
    try:
        payload = json.loads(result.stdout)
    except Exception:
        return ""
    if not isinstance(payload, list) or not payload:
        return ""

    ordered = sorted(
        [item for item in payload if isinstance(item, dict)],
        key=lambda item: str(item.get("updatedAt") or ""),
        reverse=True,
    )
    for item in ordered:
        url = str(item.get("url") or "").strip()
        if url:
            return url
    return ""


def _feature_to_dev_diagnostic_message(
    *,
    repo_name: str,
    branch: str,
    build_number: int | None,
    credentials: ReleaseCredentials,
    timeout_s: int,
) -> str:
    if not build_number:
        return ""
    try:
        payload = call_jenkins_mcp_tool(
            tool_name="monitor_jenkins_logs",
            arguments={
                "build_number": build_number,
                "repo_name": repo_name,
                "branch": branch,
                "user": credentials.email,
                "token": credentials.jenkins_api_token,
            },
            timeout_s=min(timeout_s, 180),
        )
    except PlatformError:
        return ""

    logs = _stringify_mcp_payload(payload)
    pr_url = _extract_pull_request_url(logs)
    if pr_url:
        return f"Pull request resolved from Jenkins logs: {pr_url}"

    lower_logs = logs.lower()
    if "no commits between develop and" in lower_logs:
        return f"No pull request was created because Jenkins reported no commits between {PR_LOOKUP_BASE_BRANCH} and {branch}."
    if "rebase failed" in lower_logs or "conflict" in lower_logs:
        return "No pull request was created because Jenkins reported a rebase conflict."
    if "pull request link is null" in lower_logs:
        return "Jenkins completed without creating a pull request."
    return ""


def _feature_to_dev_diagnostic_from_logs(*, console_text: str, branch: str) -> str:
    lower_logs = (console_text or "").lower()
    if not lower_logs:
        return ""
    if "no commits between develop and" in lower_logs:
        return f"No pull request was created because Jenkins reported no commits between {PR_LOOKUP_BASE_BRANCH} and {branch}."
    if "rebase failed" in lower_logs or "conflict" in lower_logs:
        return "No pull request was created because Jenkins reported a rebase conflict."
    if "pull request link is null" in lower_logs:
        return "Jenkins completed without creating a pull request."
    return ""


def _int_policy(flow_policy: Mapping[str, Any], key: str, *, fallback: int) -> int:
    raw = flow_policy.get(key) if isinstance(flow_policy, Mapping) else None
    if isinstance(raw, int):
        return raw
    try:
        return int(str(raw or "").strip())
    except Exception:
        return fallback


def _normalize_bool_param(value: Any, *, default: bool) -> bool:
    lowered = str(value or "").strip().lower()
    if lowered in {"true", "1", "yes", "on"}:
        return True
    if lowered in {"false", "0", "no", "off"}:
        return False
    return default


def is_probable_jenkins_auth_failure(error: PlatformError) -> bool:
    if error.code == "E_RELEASE_JENKINS_AUTH":
        return True
    if error.code == "E_RELEASE_MCP_HTTP" and str(error.reason or "") in {"401", "403"}:
        return True
    if error.code == "E_RELEASE_JENKINS_HTTP" and str(error.reason or "") in {"401", "403"}:
        return True

    text = " ".join(
        part
        for part in (
            str(error),
            str(getattr(error, "code", "") or ""),
            str(getattr(error, "reason", "") or ""),
        )
        if part
    ).lower()
    if "github" in text and "jenkins" not in text:
        return False
    return any(hint in text for hint in _AUTH_FAILURE_HINTS)


def _append_message_line(message: str, extra: str) -> str:
    base = (message or "").strip()
    suffix = (extra or "").strip()
    if not base:
        return suffix
    if not suffix:
        return base
    if suffix in base:
        return base
    return f"{base}\n{suffix}"


def _jenkins_mcp_url() -> str:
    return (os.getenv("GHDP_JENKINS_MCP_URL") or DEFAULT_JENKINS_MCP_URL).strip()


def _normalize_email(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if "@" not in text:
        return f"{text}@guardanthealth.com"
    return text


def _first_nonempty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _first_nonempty_lazy(*providers: Callable[[], Any]) -> str:
    for provider in providers:
        text = str(provider() or "").strip()
        if text:
            return text
    return ""


def _best_effort_full_repo_name(fallback_repo_name: str) -> str:
    result = _optional_cmd_result(["gh", "repo", "view", "--json", "nameWithOwner"])
    if result is None:
        return fallback_repo_name if "/" in fallback_repo_name else ""
    if result.returncode == 0 and result.stdout:
        try:
            payload = json.loads(result.stdout)
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            name = str(payload.get("nameWithOwner") or "").strip()
            if name:
                return name

    if fallback_repo_name and "/" in fallback_repo_name:
        return fallback_repo_name
    return ""


def _gh_auth_token() -> str:
    result = _optional_cmd_result(["gh", "auth", "token"])
    if result is None:
        return ""
    if result.returncode == 0 and result.stdout:
        return result.stdout.strip()
    return ""


def _gh_auth_email() -> str:
    primary = _optional_cmd_result(["gh", "api", "user/emails", "--jq", ".[] | select(.primary == true) | .email"])
    if primary is None:
        return ""
    if primary.returncode == 0 and primary.stdout:
        return primary.stdout.strip()

    fallback = _optional_cmd_result(["gh", "api", "user", "--jq", ".email"])
    if fallback is None:
        return ""
    if fallback.returncode == 0 and fallback.stdout:
        return fallback.stdout.strip()
    return ""


def _acli_auth_email() -> str:
    result = _optional_cmd_result(["acli", "jira", "auth", "status"])
    if result is None:
        return ""
    if result.returncode != 0:
        return ""

    text = f"{result.stdout or ''}\n{result.stderr or ''}"
    match = re.search(r"^\s*Email:\s*(?P<email>\S+)\s*$", text, flags=re.MULTILINE)
    return match.group("email").strip() if match else ""


def _git_user_email() -> str:
    result = _optional_cmd_result(["git", "config", "user.email"])
    if result is None:
        return ""
    if result.returncode == 0 and result.stdout:
        return result.stdout.strip()
    return ""


def _optional_cmd_result(cmd: list[str]) -> Any | None:
    try:
        return run_cmd(cmd, check=False)
    except PlatformError as exc:
        if exc.code == "E_CMD_NOT_FOUND":
            return None
        raise
