from __future__ import annotations

import io
import ssl
import urllib.error
from pathlib import Path

import pytest

from platform_cli.core.errors import PlatformError
from platform_cli.core import jenkins_tls
from platform_cli.tools.jenkins_release_api import JenkinsApiClient


def test_normalize_job_path_prepends_job_prefix_for_legacy_shared_paths() -> None:
    client = JenkinsApiClient(user="svc@example.com", token="token")

    url = client._job_url("UDP/job/github-tools/job/dp-tools-release-management/job/1-promote-feature-to-dev")

    assert url == (
        "https://jenkins.npdata.guardanthealth.com/"
        "job/UDP/job/github-tools/job/dp-tools-release-management/job/1-promote-feature-to-dev"
    )


def test_normalize_job_path_does_not_double_prefix_valid_paths() -> None:
    client = JenkinsApiClient(user="svc@example.com", token="token")

    url = client._job_url("job/UDP/job/github-tools/job/dp-tools-release-management/job/2-make-release")

    assert url == (
        "https://jenkins.npdata.guardanthealth.com/"
        "job/UDP/job/github-tools/job/dp-tools-release-management/job/2-make-release"
    )


def test_normalize_job_path_does_not_rewrite_unrelated_repo_local_paths() -> None:
    client = JenkinsApiClient(user="svc@example.com", token="token")

    url = client._job_url("job/data-platform/job/sample-repo/job/develop-build")

    assert url == "https://jenkins.npdata.guardanthealth.com/job/data-platform/job/sample-repo/job/develop-build"


def test_request_summarizes_html_http_errors(monkeypatch) -> None:
    client = JenkinsApiClient(user="svc@example.com", token="token")

    def _raise_http_error(*args, **kwargs):
        raise urllib.error.HTTPError(
            url="https://jenkins.npdata.guardanthealth.com/job/UDP/job/github-tools/job/dp-tools-release-management/job/1-promote-feature-to-dev/buildWithParameters",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=io.BytesIO(b"<!DOCTYPE html><html><head><title>Not Found - Jenkins</title></head><body>\xe2\x87\x92</body></html>"),
        )

    monkeypatch.setattr("urllib.request.urlopen", _raise_http_error)

    with pytest.raises(PlatformError) as excinfo:
        client._request(
            "https://jenkins.npdata.guardanthealth.com/job/UDP/job/github-tools/job/dp-tools-release-management/job/1-promote-feature-to-dev/buildWithParameters",
            method="POST",
            data=b"",
            headers={},
            accepted_statuses={201},
            expect_json=False,
        )

    assert excinfo.value.code == "E_RELEASE_JENKINS_HTTP"
    assert "Not Found - Jenkins" in str(excinfo.value)
    assert "<html" not in str(excinfo.value).lower()


def test_build_jenkins_tls_config_uses_explicit_ca_bundle_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cafile = tmp_path / "jenkins.pem"
    cafile.write_text("dummy", encoding="utf-8")

    created: dict[str, str] = {}

    def _fake_create_default_context(*, cafile: str | None = None, **_kwargs: object) -> ssl.SSLContext:
        created["cafile"] = str(cafile or "")
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

    monkeypatch.setenv(jenkins_tls.JENKINS_CA_BUNDLE_ENV, str(cafile))
    monkeypatch.setattr(jenkins_tls.ssl, "create_default_context", _fake_create_default_context)

    config = jenkins_tls.build_jenkins_tls_config()

    assert config.source == jenkins_tls.JENKINS_CA_BUNDLE_ENV
    assert config.cafile == str(cafile)
    assert created["cafile"] == str(cafile)


def test_build_jenkins_tls_config_rejects_missing_explicit_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.pem"
    monkeypatch.setenv(jenkins_tls.JENKINS_CA_BUNDLE_ENV, str(missing))

    with pytest.raises(PlatformError) as excinfo:
        jenkins_tls.build_jenkins_tls_config()

    assert excinfo.value.code == "E_RELEASE_JENKINS_CA_BUNDLE_INVALID"
    assert jenkins_tls.JENKINS_CA_BUNDLE_ENV in str(excinfo.value)


def test_build_jenkins_tls_config_rejects_invalid_explicit_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    invalid = tmp_path / "invalid.pem"
    invalid.write_text("not a pem bundle", encoding="utf-8")
    monkeypatch.setenv(jenkins_tls.JENKINS_CA_BUNDLE_ENV, str(invalid))

    with pytest.raises(PlatformError) as excinfo:
        jenkins_tls.build_jenkins_tls_config()

    assert excinfo.value.code == "E_RELEASE_JENKINS_CA_BUNDLE_INVALID"
    assert str(invalid) in str(excinfo.value)


def test_request_surfaces_tls_verification_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    client = JenkinsApiClient(user="svc@example.com", token="token")

    def _raise_tls_error(*args: object, **kwargs: object) -> None:
        raise urllib.error.URLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")

    monkeypatch.setattr("urllib.request.urlopen", _raise_tls_error)

    with pytest.raises(PlatformError) as excinfo:
        client._request(
            "https://jenkins.npdata.guardanthealth.com/job/UDP/job/github-tools/job/dp-tools-release-management/job/1-promote-feature-to-dev/buildWithParameters",
            method="POST",
            data=b"",
            headers={},
            accepted_statuses={201},
            expect_json=False,
        )

    assert excinfo.value.code == "E_RELEASE_JENKINS_TLS"
    assert "GHDP_JENKINS_CA_BUNDLE" in str(excinfo.value)
