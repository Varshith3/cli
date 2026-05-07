from __future__ import annotations

import importlib
import json
from pathlib import Path

from platform_cli.core.errors import PlatformError

release_mcp = importlib.import_module("platform_cli.tools.release_mcp")
release_parity = importlib.import_module("platform_cli.tools.release_parity")


def test_parse_mcp_http_body_event_stream() -> None:
    payload = release_mcp._parse_mcp_http_body(
        'event: message\r\ndata: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"ok"}]}}\r\n\r\n'
    )

    assert payload["result"]["content"][0]["text"] == "ok"


def test_parse_mcp_http_body_event_stream_with_ping_prefix() -> None:
    payload = release_mcp._parse_mcp_http_body(
        ": ping - 2026-04-10 10:42:06.252821+00:00\r\n\r\n"
        'event: message\r\n'
        'data: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"ok"}]}}\r\n\r\n'
    )

    assert payload["result"]["content"][0]["text"] == "ok"


def test_result_from_payload_extracts_build_and_pr_urls() -> None:
    result = release_mcp._result_from_payload(
        {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Triggered Jenkins build https://jenkins.example/job/a/job/b/123/ "
                        "and PR https://github.com/example/repo/pull/45"
                    ),
                }
            ]
        }
    )

    assert result.build_url == "https://jenkins.example/job/a/job/b/123/"
    assert result.build_number == 123
    assert result.pull_request_url == "https://github.com/example/repo/pull/45"


def test_run_feature_to_dev_invokes_expected_tool(monkeypatch) -> None:
    calls = {}

    def _fake_call_jenkins_mcp_tool(*, tool_name, arguments, timeout_s):
        calls["tool_name"] = tool_name
        calls["arguments"] = dict(arguments)
        calls["timeout_s"] = timeout_s
        return {"content": [{"type": "text", "text": "ok"}]}

    monkeypatch.setattr(release_mcp, "call_jenkins_mcp_tool", _fake_call_jenkins_mcp_tool)

    creds = release_mcp.ReleaseCredentials(
        email="svc@example.com",
        jenkins_api_token="jenkins-token",
        github_api_token="github-token",
    )
    release_mcp.run_feature_to_dev(
        repo_name="dp-tools-local-setup",
        branch="feature/EPPE-7239-smart-release",
        credentials=creds,
        deploy_on_sqa=False,
    )

    assert calls["tool_name"] == "create_pull_request"
    assert calls["arguments"]["github_repository_name"] == "dp-tools-local-setup"
    assert calls["arguments"]["branch"] == "feature/EPPE-7239-smart-release"


def test_jenkins_client_uses_configured_base_url_and_tls_context(monkeypatch) -> None:
    monkeypatch.setenv("GHDP_JENKINS_BASE_URL", "https://jenkins.example.internal")

    creds = release_mcp.ReleaseCredentials(
        email="svc@example.com",
        jenkins_api_token="jenkins-token",
        github_api_token="github-token",
    )
    client = release_mcp._jenkins_client(credentials=creds, timeout_s=42)

    assert client.base_url == "https://jenkins.example.internal"
    assert client.timeout_s == 42
    assert client.ssl_context is not None


def test_resolve_release_credentials_prefers_explicit_inputs(monkeypatch) -> None:
    monkeypatch.setattr(
        release_mcp,
        "resolve_okta_email",
        lambda: (_ for _ in ()).throw(AssertionError("resolve_okta_email should not run")),
    )
    monkeypatch.setattr(
        release_mcp,
        "resolve_github_api_token",
        lambda: (_ for _ in ()).throw(AssertionError("resolve_github_api_token should not run")),
    )
    monkeypatch.setattr(
        release_mcp,
        "resolve_jenkins_api_token",
        lambda: (_ for _ in ()).throw(AssertionError("resolve_jenkins_api_token should not run")),
    )

    creds = release_mcp.resolve_release_credentials(
        require_github_token=True,
        email="svc@example.com",
        jenkins_api_token="jenkins-token",
        github_api_token="github-token",
    )

    assert creds.email == "svc@example.com"
    assert creds.jenkins_api_token == "jenkins-token"
    assert creds.github_api_token == "github-token"


def test_resolve_release_credentials_uses_config_and_gh_auth(monkeypatch) -> None:
    monkeypatch.delenv("OKTA_USER_EMAIL", raising=False)
    monkeypatch.delenv("JENKINS_API_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_API_TOKEN", raising=False)
    monkeypatch.setenv("JENKINS_API_TOKEN", "jenkins-token")
    monkeypatch.setattr(release_mcp, "get_value", lambda key, default="": "configured@example.com")
    monkeypatch.setattr(release_mcp, "_gh_auth_token", lambda: "gh-token")

    creds = release_mcp.resolve_release_credentials(require_github_token=True)

    assert creds.email == "configured@example.com"
    assert creds.jenkins_api_token == "jenkins-token"
    assert creds.github_api_token == "gh-token"


def test_resolve_jenkins_api_token_with_source_prefers_env(monkeypatch) -> None:
    monkeypatch.setenv("JENKINS_API_TOKEN", "env-token")
    monkeypatch.setattr(release_mcp, "get_value", lambda key, default="": "config-token")

    token, source = release_mcp.resolve_jenkins_api_token_with_source()

    assert token == "env-token"
    assert source == "env"


def test_resolve_jenkins_api_token_with_source_uses_config(monkeypatch) -> None:
    monkeypatch.delenv("JENKINS_API_TOKEN", raising=False)
    monkeypatch.delenv("GHDP_JENKINS_API_TOKEN", raising=False)
    monkeypatch.setattr(release_mcp, "get_value", lambda key, default="": "config-token")

    token, source = release_mcp.resolve_jenkins_api_token_with_source()

    assert token == "config-token"
    assert source == "config"


def test_resolve_repo_identity_falls_back_to_git_repo_name_when_gh_is_missing(monkeypatch) -> None:
    def _missing(*_args, **_kwargs):
        raise PlatformError("Command not found: gh", code="E_CMD_NOT_FOUND", reason="gh")

    monkeypatch.setattr(release_mcp, "run_cmd", _missing)
    monkeypatch.setattr(release_mcp, "get_repo_name", lambda: "dp-tools-local-setup")

    identity = release_mcp.resolve_repo_identity()

    assert identity.repo_name == "dp-tools-local-setup"
    assert identity.full_name == ""


def test_resolve_okta_email_prefers_github_cli_then_acli_then_git(monkeypatch) -> None:
    monkeypatch.delenv("OKTA_USER_EMAIL", raising=False)
    monkeypatch.setattr(release_mcp, "get_value", lambda key, default="": "")
    monkeypatch.setattr(release_mcp, "_gh_auth_email", lambda: "gh@example.com")
    monkeypatch.setattr(release_mcp, "_acli_auth_email", lambda: "acli@example.com")
    monkeypatch.setattr(release_mcp, "_git_user_email", lambda: "git@example.com")

    assert release_mcp.resolve_okta_email() == "gh@example.com"


def test_resolve_okta_email_prefers_env_without_probings_helpers(monkeypatch) -> None:
    monkeypatch.setenv("OKTA_USER_EMAIL", "env@example.com")
    monkeypatch.setattr(
        release_mcp,
        "_gh_auth_email",
        lambda: (_ for _ in ()).throw(AssertionError("_gh_auth_email should not run")),
    )
    monkeypatch.setattr(
        release_mcp,
        "_acli_auth_email",
        lambda: (_ for _ in ()).throw(AssertionError("_acli_auth_email should not run")),
    )
    monkeypatch.setattr(
        release_mcp,
        "_git_user_email",
        lambda: (_ for _ in ()).throw(AssertionError("_git_user_email should not run")),
    )

    assert release_mcp.resolve_okta_email() == "env@example.com"


def test_resolve_okta_email_prefers_config_without_probings_helpers(monkeypatch) -> None:
    monkeypatch.delenv("OKTA_USER_EMAIL", raising=False)
    monkeypatch.setattr(release_mcp, "get_value", lambda key, default="": "config@example.com")
    monkeypatch.setattr(
        release_mcp,
        "_gh_auth_email",
        lambda: (_ for _ in ()).throw(AssertionError("_gh_auth_email should not run")),
    )
    monkeypatch.setattr(
        release_mcp,
        "_acli_auth_email",
        lambda: (_ for _ in ()).throw(AssertionError("_acli_auth_email should not run")),
    )
    monkeypatch.setattr(
        release_mcp,
        "_git_user_email",
        lambda: (_ for _ in ()).throw(AssertionError("_git_user_email should not run")),
    )

    assert release_mcp.resolve_okta_email() == "config@example.com"


def test_resolve_okta_email_falls_back_to_acli_then_git(monkeypatch) -> None:
    monkeypatch.delenv("OKTA_USER_EMAIL", raising=False)
    monkeypatch.setattr(release_mcp, "get_value", lambda key, default="": "")
    monkeypatch.setattr(release_mcp, "_gh_auth_email", lambda: "")
    monkeypatch.setattr(release_mcp, "_acli_auth_email", lambda: "acli@example.com")
    monkeypatch.setattr(release_mcp, "_git_user_email", lambda: "git@example.com")

    assert release_mcp.resolve_okta_email() == "acli@example.com"


def test_resolve_okta_email_falls_back_to_git_email(monkeypatch) -> None:
    monkeypatch.delenv("OKTA_USER_EMAIL", raising=False)
    monkeypatch.setattr(release_mcp, "get_value", lambda key, default="": "")
    monkeypatch.setattr(release_mcp, "_gh_auth_email", lambda: "")
    monkeypatch.setattr(release_mcp, "_acli_auth_email", lambda: "")
    monkeypatch.setattr(release_mcp, "_git_user_email", lambda: "git@example.com")

    assert release_mcp.resolve_okta_email() == "git@example.com"


def test_acli_auth_email_parses_status_output(monkeypatch) -> None:
    class _Result:
        returncode = 0
        stdout = "✓ Authenticated\n  Site: guardanthealth.atlassian.net\n  Email: mshyam@guardanthealth.com\n"
        stderr = ""

    monkeypatch.setattr(release_mcp, "run_cmd", lambda *args, **kwargs: _Result())

    assert release_mcp._acli_auth_email() == "mshyam@guardanthealth.com"


def test_gh_auth_email_soft_fails_when_gh_is_missing(monkeypatch) -> None:
    def _missing(*_args, **_kwargs):
        raise PlatformError("Command not found: gh", code="E_CMD_NOT_FOUND", reason="gh")

    monkeypatch.setattr(release_mcp, "run_cmd", _missing)

    assert release_mcp._gh_auth_email() == ""


def test_acli_auth_email_soft_fails_when_acli_is_missing(monkeypatch) -> None:
    def _missing(*_args, **_kwargs):
        raise PlatformError("Command not found: acli", code="E_CMD_NOT_FOUND", reason="acli")

    monkeypatch.setattr(release_mcp, "run_cmd", _missing)

    assert release_mcp._acli_auth_email() == ""


def test_git_user_email_soft_fails_when_git_is_missing(monkeypatch) -> None:
    def _missing(*_args, **_kwargs):
        raise PlatformError("Command not found: git", code="E_CMD_NOT_FOUND", reason="git")

    monkeypatch.setattr(release_mcp, "run_cmd", _missing)

    assert release_mcp._git_user_email() == ""


def test_resolve_github_api_token_prefers_env_without_gh(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_API_TOKEN", "env-gh-token")
    monkeypatch.delenv("GHDP_GITHUB_API_TOKEN", raising=False)
    monkeypatch.setattr(
        release_mcp,
        "_gh_auth_token",
        lambda: (_ for _ in ()).throw(AssertionError("_gh_auth_token should not run")),
    )

    assert release_mcp.resolve_github_api_token() == "env-gh-token"


def test_resolve_github_api_token_soft_fails_when_gh_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_API_TOKEN", raising=False)
    monkeypatch.delenv("GHDP_GITHUB_API_TOKEN", raising=False)

    def _missing(*_args, **_kwargs):
        raise PlatformError("Command not found: gh", code="E_CMD_NOT_FOUND", reason="gh")

    monkeypatch.setattr(release_mcp, "run_cmd", _missing)

    assert release_mcp.resolve_github_api_token() == ""


def test_resolve_release_credentials_requires_jenkins_token(monkeypatch) -> None:
    monkeypatch.delenv("JENKINS_API_TOKEN", raising=False)
    monkeypatch.setattr(release_mcp, "get_value", lambda key, default="": "")
    monkeypatch.setattr(release_mcp, "_gh_auth_email", lambda: "configured@example.com")
    monkeypatch.setattr(release_mcp, "_gh_auth_token", lambda: "gh-token")

    try:
        release_mcp.resolve_release_credentials(require_github_token=True)
    except PlatformError as exc:
        assert exc.code == "E_RELEASE_JENKINS_TOKEN_MISSING"
    else:
        raise AssertionError("Expected PlatformError for missing Jenkins token")


def test_run_feature_to_dev_resolves_pull_request_via_github_cli(monkeypatch) -> None:
    class _Result:
        def __init__(self, *, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _fake_call_jenkins_mcp_tool(*, tool_name, arguments, timeout_s):
        assert tool_name == "create_pull_request"
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Pull request creation job triggered successfully. Build URL: https://jenkins.example/job/a/job/b/123/",
                }
            ]
        }

    def _fake_run_cmd(cmd, check=False):
        if cmd[:3] == ["gh", "repo", "view"]:
            return _Result(stdout=json.dumps({"nameWithOwner": "gh-org-data-platform/dp-tools-local-setup"}))
        if cmd[:3] == ["gh", "pr", "list"]:
            return _Result(
                stdout=json.dumps(
                    [
                        {
                            "url": "https://github.com/gh-org-data-platform/dp-tools-local-setup/pull/41",
                            "number": 41,
                            "state": "OPEN",
                            "updatedAt": "2026-04-15T10:00:00Z",
                        }
                    ]
                )
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(release_mcp, "call_jenkins_mcp_tool", _fake_call_jenkins_mcp_tool)
    monkeypatch.setattr(release_mcp, "run_cmd", _fake_run_cmd)

    creds = release_mcp.ReleaseCredentials(
        email="svc@example.com",
        jenkins_api_token="jenkins-token",
        github_api_token="github-token",
    )

    result = release_mcp.run_feature_to_dev(
        repo_name="dp-tools-local-setup",
        branch="feature/EPPE-7239-smart-release",
        credentials=creds,
        deploy_on_sqa=False,
        timeout_s=5,
    )

    assert result.build_url == "https://jenkins.example/job/a/job/b/123/"
    assert result.pull_request_url == "https://github.com/gh-org-data-platform/dp-tools-local-setup/pull/41"
    assert "resolved via GitHub CLI" in result.message


def test_run_feature_to_dev_reports_github_lookup_status(monkeypatch) -> None:
    messages: list[str] = []

    def _fake_call_jenkins_mcp_tool(*, tool_name, arguments, timeout_s):
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Pull request creation job triggered successfully. Build URL: https://jenkins.example/job/a/job/b/123/",
                }
            ]
        }

    monkeypatch.setattr(release_mcp, "call_jenkins_mcp_tool", _fake_call_jenkins_mcp_tool)
    monkeypatch.setattr(
        release_mcp,
        "_find_pull_request_url_via_gh",
        lambda **kwargs: "https://github.com/gh-org-data-platform/dp-tools-local-setup/pull/41",
    )

    creds = release_mcp.ReleaseCredentials(
        email="svc@example.com",
        jenkins_api_token="jenkins-token",
        github_api_token="github-token",
    )

    release_mcp.run_feature_to_dev(
        repo_name="dp-tools-local-setup",
        branch="feature/EPPE-7239-smart-release",
        credentials=creds,
        deploy_on_sqa=False,
        timeout_s=5,
        status_printer=messages.append,
    )

    assert messages == [
        "Triggering Jenkins job...",
        "Resolving pull request via GitHub CLI...",
    ]


def test_run_feature_to_dev_reports_diagnostic_when_no_pull_request_is_found(monkeypatch) -> None:
    class _Result:
        def __init__(self, *, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _fake_call_jenkins_mcp_tool(*, tool_name, arguments, timeout_s):
        assert tool_name == "create_pull_request"
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Pull request creation job triggered successfully. Build URL: https://jenkins.example/job/a/job/b/123/",
                }
            ]
        }

    def _fake_run_cmd(cmd, check=False):
        if cmd[:3] == ["gh", "repo", "view"]:
            return _Result(stdout=json.dumps({"nameWithOwner": "gh-org-data-platform/dp-tools-local-setup"}))
        if cmd[:3] == ["gh", "pr", "list"]:
            return _Result(stdout="[]")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(release_mcp, "call_jenkins_mcp_tool", _fake_call_jenkins_mcp_tool)
    monkeypatch.setattr(release_mcp, "run_cmd", _fake_run_cmd)
    monkeypatch.setattr(
        release_mcp,
        "_feature_to_dev_diagnostic_message",
        lambda **kwargs: "No pull request was created because Jenkins reported no commits between develop and feature/EPPE-7239-smart-release.",
    )

    creds = release_mcp.ReleaseCredentials(
        email="svc@example.com",
        jenkins_api_token="jenkins-token",
        github_api_token="github-token",
    )

    result = release_mcp.run_feature_to_dev(
        repo_name="dp-tools-local-setup",
        branch="feature/EPPE-7239-smart-release",
        credentials=creds,
        deploy_on_sqa=False,
        timeout_s=5,
    )

    assert result.build_url == "https://jenkins.example/job/a/job/b/123/"
    assert result.pull_request_url == ""
    assert "no commits between develop" in result.message.lower()


def test_is_probable_jenkins_auth_failure_detects_http_auth_errors() -> None:
    error = PlatformError("Jenkins MCP request failed (403): Forbidden", code="E_RELEASE_MCP_HTTP", reason="403")

    assert release_mcp.is_probable_jenkins_auth_failure(error) is True


def test_is_probable_jenkins_auth_failure_detects_direct_jenkins_auth_errors() -> None:
    error = PlatformError("Jenkins API request failed (401): unauthorized", code="E_RELEASE_JENKINS_AUTH", reason="401")

    assert release_mcp.is_probable_jenkins_auth_failure(error) is True


def test_normalize_release_type_rejects_invalid() -> None:
    try:
        release_mcp.normalize_release_type("weekly")
    except PlatformError as exc:
        assert exc.code == "E_RELEASE_TYPE_INVALID"
    else:
        raise AssertionError("Expected PlatformError for invalid release type")


def test_run_feature_to_dev_uses_direct_backend_when_contract_requests_it(monkeypatch) -> None:
    messages: list[str] = []

    class _FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def trigger_job(self, *, job_path, params):
            assert job_path == "job/UDP/job/github-tools/job/dp-tools-release-management/job/1-promote-feature-to-dev"
            assert params["REPOSITORY"] == "dp-tools-local-setup"
            return type("Handle", (), {"job_path": job_path, "job_url": "https://jenkins.example/job/path", "queue_url": "https://jenkins.example/queue/1", "queue_api_url": "https://jenkins.example/queue/1/api/json"})()

        def wait_for_build_number(self, handle, *, timeout_s, poll_interval_s):
            return type("Observation", (), {"job_path": handle.job_path, "job_url": handle.job_url, "build_number": 123, "build_url": "https://jenkins.example/job/path/123", "result": "SUCCESS", "building": False, "artifact_urls": (), "metadata": {}})()

        def wait_for_build_completion(self, *, job_path, build_number, timeout_s, poll_interval_s):
            return type("Observation", (), {"job_path": job_path, "job_url": "https://jenkins.example/job/path", "build_number": build_number, "build_url": "https://jenkins.example/job/path/123", "result": "SUCCESS", "building": False, "artifact_urls": (), "metadata": {}})()

        def get_console_text(self, *, job_path, build_number):
            return "Build Result: SUCCESS\nPull Request Link: Not found in Jenkins logs."

    monkeypatch.setattr(release_mcp, "JenkinsApiClient", _FakeClient)
    monkeypatch.setattr(
        release_mcp,
        "_find_pull_request_url_via_gh",
        lambda **kwargs: "https://github.com/gh-org-data-platform/dp-tools-local-setup/pull/41",
    )

    creds = release_mcp.ReleaseCredentials(
        email="svc@example.com",
        jenkins_api_token="jenkins-token",
        github_api_token="github-token",
    )
    contract = {
        "flows": {
            "feature_to_dev": {
                "execution_backend": "jenkins_api",
                "job_path": "job/UDP/job/github-tools/job/dp-tools-release-management/job/1-promote-feature-to-dev",
                "queue_timeout_s": 30,
                "build_timeout_s": 30,
                "poll_interval_s": 1,
            }
        }
    }

    result = release_mcp.run_feature_to_dev(
        repo_name="dp-tools-local-setup",
        branch="feature/EPPE-7239-smart-release",
        credentials=creds,
        deploy_on_sqa=False,
        contract=contract,
        timeout_s=10,
        status_printer=messages.append,
    )

    assert result.build_number == 123
    assert result.pull_request_url == "https://github.com/gh-org-data-platform/dp-tools-local-setup/pull/41"
    assert messages == [
        "Triggering Jenkins job...",
        "Waiting for Jenkins to assign a build...",
        "Jenkins build started: https://jenkins.example/job/path/123",
        "Inspecting Jenkins build outcome...",
        "Resolving pull request via GitHub CLI...",
    ]


def test_run_feature_to_dev_accepts_legacy_shared_job_path_contract_values(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            captured["init"] = kwargs

        def trigger_job(self, *, job_path, params):
            captured["job_path"] = job_path
            return type("Handle", (), {"job_path": job_path, "job_url": "https://jenkins.example/job/path", "queue_url": "https://jenkins.example/queue/1", "queue_api_url": "https://jenkins.example/queue/1/api/json"})()

        def wait_for_build_number(self, handle, *, timeout_s, poll_interval_s):
            return type("Observation", (), {"job_path": handle.job_path, "job_url": handle.job_url, "build_number": 123, "build_url": "https://jenkins.example/job/path/123", "result": "SUCCESS", "building": False, "artifact_urls": (), "metadata": {}})()

        def wait_for_build_completion(self, *, job_path, build_number, timeout_s, poll_interval_s):
            captured["completion_job_path"] = job_path
            return type("Observation", (), {"job_path": job_path, "job_url": "https://jenkins.example/job/path", "build_number": build_number, "build_url": "https://jenkins.example/job/path/123", "result": "SUCCESS", "building": False, "artifact_urls": (), "metadata": {}})()

        def get_console_text(self, *, job_path, build_number):
            captured["console_job_path"] = job_path
            return "Build Result: SUCCESS\nPull Request Link: Not found in Jenkins logs."

    monkeypatch.setattr(release_mcp, "JenkinsApiClient", _FakeClient)
    monkeypatch.setattr(
        release_mcp,
        "_find_pull_request_url_via_gh",
        lambda **kwargs: "https://github.com/gh-org-data-platform/dp-tools-local-setup/pull/41",
    )

    creds = release_mcp.ReleaseCredentials(
        email="svc@example.com",
        jenkins_api_token="jenkins-token",
        github_api_token="github-token",
    )
    contract = {
        "flows": {
            "feature_to_dev": {
                "execution_backend": "jenkins_api",
                "job_path": "UDP/job/github-tools/job/dp-tools-release-management/job/1-promote-feature-to-dev",
                "queue_timeout_s": 30,
                "build_timeout_s": 30,
                "poll_interval_s": 1,
            }
        }
    }

    result = release_mcp.run_feature_to_dev(
        repo_name="dp-tools-local-setup",
        branch="feature/EPPE-7239-smart-release",
        credentials=creds,
        deploy_on_sqa=False,
        contract=contract,
        timeout_s=10,
    )

    assert captured["job_path"] == "UDP/job/github-tools/job/dp-tools-release-management/job/1-promote-feature-to-dev"
    assert captured["completion_job_path"] == "UDP/job/github-tools/job/dp-tools-release-management/job/1-promote-feature-to-dev"
    assert captured["console_job_path"] == "UDP/job/github-tools/job/dp-tools-release-management/job/1-promote-feature-to-dev"
    assert result.pull_request_url == "https://github.com/gh-org-data-platform/dp-tools-local-setup/pull/41"


def test_run_make_release_uses_direct_backend_when_contract_requests_it(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            captured["init"] = kwargs

        def trigger_job(self, *, job_path, params):
            captured["job_path"] = job_path
            captured["params"] = dict(params)
            return type("Handle", (), {"job_path": job_path, "job_url": "https://jenkins.example/job/path", "queue_url": "https://jenkins.example/queue/1", "queue_api_url": "https://jenkins.example/queue/1/api/json"})()

        def wait_for_build_number(self, handle, *, timeout_s, poll_interval_s):
            return type("Observation", (), {"job_path": handle.job_path, "job_url": handle.job_url, "build_number": 456, "build_url": "https://jenkins.example/job/path/456", "result": "QUEUED", "building": False, "artifact_urls": (), "metadata": {}})()

    monkeypatch.setattr(release_mcp, "JenkinsApiClient", _FakeClient)
    creds = release_mcp.ReleaseCredentials(email="svc@example.com", jenkins_api_token="jenkins-token")
    contract = {
        "flows": {
            "make_release": {
                "execution_backend": "jenkins_api",
                "job_path": "job/UDP/job/github-tools/job/dp-tools-release-management/job/2-make-release",
                "queue_timeout_s": 30,
                "poll_interval_s": 1,
            }
        }
    }

    result = release_mcp.run_make_release(
        repo_name="dp-tools-local-setup",
        credentials=creds,
        release_type="bugfix",
        parent="REL-123>Summary",
        params={"TARGET_WORKSPACE": "dev", "APPLY": "false"},
        contract=contract,
        timeout_s=10,
    )

    assert captured["job_path"] == "job/UDP/job/github-tools/job/dp-tools-release-management/job/2-make-release"
    assert captured["params"]["PARENT"] == "REL-123>Summary"
    assert captured["params"]["RELEASE_TYPE"] == "bugfix"
    assert captured["params"]["REPOSITORY"] == "dp-tools-local-setup"
    assert captured["params"]["SOURCE_BRANCH"] == "develop"
    assert captured["params"]["APPLY"] == "false"
    assert result.build_number == 456


def test_feature_to_dev_diagnostic_from_logs_detects_no_diff() -> None:
    message = release_mcp._feature_to_dev_diagnostic_from_logs(
        console_text="No commits between develop and feature/EPPE-7239-smart-release, hence PR won't be raised.",
        branch="feature/EPPE-7239-smart-release",
    )

    assert "no commits between develop" in message.lower()


def test_release_parity_compare_ignores_transport_noise() -> None:
    left = {
        "flow": "feature_to_dev",
        "status": "SUCCESS",
        "build_number": 101,
        "pull_request_url": "https://github.com/example/repo/pull/1",
        "diagnostic_reason": "",
        "timestamp": "2026-04-16T10:00:00Z",
    }
    right = {
        "flow": "feature_to_dev",
        "status": "SUCCESS",
        "build_number": 101,
        "pull_request_url": "https://github.com/example/repo/pull/1",
        "diagnostic_reason": "",
        "timestamp": "2026-04-16T10:01:00Z",
    }

    diff = release_parity.compare_release_semantics(
        left,
        right,
        include_fields=["flow", "status", "build_number", "pull_request_url", "diagnostic_reason", "timestamp"],
        ignore_fields=["timestamp"],
    )

    assert diff == {}


def test_load_release_parity_fixture_reads_json(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"flow": "feature_to_dev"}), encoding="utf-8")

    payload = release_parity.load_release_parity_fixture(fixture)

    assert payload["flow"] == "feature_to_dev"
