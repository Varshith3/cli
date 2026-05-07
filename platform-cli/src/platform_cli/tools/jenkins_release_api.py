from __future__ import annotations

import base64
import html
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

from platform_cli.core.errors import PlatformError
from platform_cli.core.jenkins_tls import build_jenkins_tls_config, jenkins_tls_help_text

DEFAULT_JENKINS_BASE_URL = "https://jenkins.npdata.guardanthealth.com"
_LEGACY_SHARED_JOB_PREFIX = "UDP/job/"
_CANONICAL_SHARED_JOB_PREFIX = "job/UDP/job/"


@dataclass(frozen=True)
class JenkinsBuildHandle:
    job_path: str
    job_url: str
    queue_url: str
    queue_api_url: str


@dataclass(frozen=True)
class JenkinsBuildObservation:
    job_path: str
    job_url: str
    build_number: int
    build_url: str
    result: str = ""
    building: bool = False
    console_text: str = ""
    artifact_urls: tuple[str, ...] = ()
    metadata: dict[str, Any] | None = None


class JenkinsApiClient:
    def __init__(
        self,
        *,
        user: str,
        token: str,
        base_url: str = DEFAULT_JENKINS_BASE_URL,
        timeout_s: int = 30,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self.user = (user or "").strip()
        self.token = (token or "").strip()
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.ssl_context_source = "custom"
        self.ssl_context_cafile = ""
        if ssl_context is None:
            tls_config = build_jenkins_tls_config()
            self.ssl_context = tls_config.context
            self.ssl_context_source = tls_config.source
            self.ssl_context_cafile = tls_config.cafile
        else:
            self.ssl_context = ssl_context
        self._crumb_field: str | None = None
        self._crumb_value: str | None = None

    def trigger_job(self, *, job_path: str, params: Mapping[str, Any] | None = None) -> JenkinsBuildHandle:
        endpoint = "buildWithParameters" if params else "build"
        normalized_job_path = self._normalize_job_path(job_path)
        job_url = self._job_url(normalized_job_path)
        crumb_field, crumb_value = self._crumb()
        headers = {crumb_field: crumb_value}
        if params:
            payload = urllib.parse.urlencode(
                {key: self._normalize_param_value(value) for key, value in dict(params).items()},
                doseq=True,
            ).encode("utf-8")
            data = payload
        else:
            data = b""
        response_headers = self._request(
            f"{job_url}/{endpoint}",
            method="POST",
            data=data,
            headers=headers,
            accepted_statuses={201},
            expect_json=False,
        )
        location = str(response_headers.get("Location") or response_headers.get("location") or "").strip()
        if not location:
            raise PlatformError(
                "Jenkins did not return a queue location after triggering the job.",
                code="E_RELEASE_JENKINS_QUEUE_MISSING",
                reason="queue_location",
            )
        queue_url = location.rstrip("/")
        return JenkinsBuildHandle(
            job_path=normalized_job_path,
            job_url=job_url,
            queue_url=queue_url,
            queue_api_url=f"{queue_url}/api/json",
        )

    def wait_for_build_number(
        self,
        handle: JenkinsBuildHandle,
        *,
        timeout_s: int,
        poll_interval_s: int,
    ) -> JenkinsBuildObservation:
        deadline = time.time() + max(timeout_s, 0)
        while True:
            payload = self._request_json(handle.queue_api_url)
            executable = payload.get("executable") if isinstance(payload, dict) else None
            if isinstance(executable, dict):
                number = executable.get("number")
                url = str(executable.get("url") or "").strip()
                if isinstance(number, int) and url:
                    return JenkinsBuildObservation(
                        job_path=handle.job_path,
                        job_url=handle.job_url,
                        build_number=number,
                        build_url=url.rstrip("/"),
                        metadata=payload if isinstance(payload, dict) else None,
                    )
            if time.time() >= deadline:
                raise PlatformError(
                    "Timed out while waiting for Jenkins to assign a build number.",
                    code="E_RELEASE_JENKINS_QUEUE_TIMEOUT",
                    reason="queue_timeout",
                )
            time.sleep(max(poll_interval_s, 1))

    def wait_for_build_completion(
        self,
        *,
        job_path: str,
        build_number: int,
        timeout_s: int,
        poll_interval_s: int,
    ) -> JenkinsBuildObservation:
        deadline = time.time() + max(timeout_s, 0)
        build_api_url = self._build_api_url(job_path, build_number)
        while True:
            payload = self._request_json(build_api_url)
            building = bool(payload.get("building")) if isinstance(payload, dict) else False
            if not building:
                return self._build_observation(job_path=job_path, build_number=build_number, payload=payload)
            if time.time() >= deadline:
                raise PlatformError(
                    f"Timed out while waiting for Jenkins build #{build_number} to complete.",
                    code="E_RELEASE_JENKINS_BUILD_TIMEOUT",
                    reason="build_timeout",
                )
            time.sleep(max(poll_interval_s, 1))

    def get_build_observation(self, *, job_path: str, build_number: int) -> JenkinsBuildObservation:
        payload = self._request_json(self._build_api_url(job_path, build_number))
        return self._build_observation(job_path=job_path, build_number=build_number, payload=payload)

    def get_console_text(self, *, job_path: str, build_number: int) -> str:
        url = f"{self._job_url(job_path)}/{build_number}/consoleText"
        return self._request_text(url)

    def get_artifact_urls(self, *, job_path: str, build_number: int) -> tuple[str, ...]:
        observation = self.get_build_observation(job_path=job_path, build_number=build_number)
        return observation.artifact_urls

    def _crumb(self) -> tuple[str, str]:
        if self._crumb_field and self._crumb_value:
            return self._crumb_field, self._crumb_value
        payload = self._request_json(f"{self.base_url}/crumbIssuer/api/json")
        field = str(payload.get("crumbRequestField") or "").strip()
        value = str(payload.get("crumb") or "").strip()
        if not field or not value:
            raise PlatformError(
                "Jenkins crumb issuer response was missing crumb data.",
                code="E_RELEASE_JENKINS_CRUMB_INVALID",
                reason="crumb",
            )
        self._crumb_field = field
        self._crumb_value = value
        return field, value

    def _build_observation(self, *, job_path: str, build_number: int, payload: Mapping[str, Any]) -> JenkinsBuildObservation:
        build_url = str(payload.get("url") or f"{self._job_url(job_path)}/{build_number}/").strip().rstrip("/")
        artifacts: list[str] = []
        raw_artifacts = payload.get("artifacts")
        if isinstance(raw_artifacts, list):
            for item in raw_artifacts:
                if not isinstance(item, dict):
                    continue
                rel = str(item.get("relativePath") or "").strip()
                if rel:
                    artifacts.append(f"{build_url}/artifact/{rel}")
        return JenkinsBuildObservation(
            job_path=job_path,
            job_url=self._job_url(job_path),
            build_number=build_number,
            build_url=build_url,
            result=str(payload.get("result") or "").strip(),
            building=bool(payload.get("building")),
            artifact_urls=tuple(artifacts),
            metadata=dict(payload),
        )

    def _job_url(self, job_path: str) -> str:
        cleaned = self._normalize_job_path(job_path)
        if not cleaned:
            raise PlatformError(
                "Jenkins job path is required for release execution.",
                code="E_RELEASE_JENKINS_JOB_MISSING",
                reason="job_path",
            )
        return f"{self.base_url}/{cleaned}"

    @staticmethod
    def _normalize_job_path(job_path: str) -> str:
        cleaned = str(job_path or "").strip().strip("/")
        if cleaned.startswith(_CANONICAL_SHARED_JOB_PREFIX):
            return cleaned
        if cleaned.startswith(_LEGACY_SHARED_JOB_PREFIX):
            return f"job/{cleaned}"
        return cleaned

    def _build_api_url(self, job_path: str, build_number: int) -> str:
        return f"{self._job_url(job_path)}/{build_number}/api/json"

    def _request_json(self, url: str, *, method: str = "GET", data: bytes | None = None, headers: Mapping[str, str] | None = None) -> Mapping[str, Any]:
        payload = self._request(
            url,
            method=method,
            data=data,
            headers=headers,
            accepted_statuses={200},
            expect_json=True,
        )
        if not isinstance(payload, dict):
            raise PlatformError(
                f"Expected JSON object from Jenkins at {url}.",
                code="E_RELEASE_JENKINS_RESPONSE_INVALID",
                reason="jenkins_json",
            )
        return payload

    def _request_text(self, url: str) -> str:
        payload = self._request(
            url,
            method="GET",
            accepted_statuses={200},
            expect_json=False,
        )
        if isinstance(payload, dict):
            return json.dumps(payload, indent=2, sort_keys=True)
        return str(payload or "")

    def _request(
        self,
        url: str,
        *,
        method: str,
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        accepted_statuses: set[int],
        expect_json: bool,
    ) -> Any:
        request_headers = {"Authorization": self._basic_auth_header()}
        if headers:
            request_headers.update(dict(headers))
        if data is not None:
            request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        request = urllib.request.Request(
            url,
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s, context=self.ssl_context) as response:
                body = response.read().decode("utf-8", errors="replace")
                if response.status not in accepted_statuses:
                    raise PlatformError(
                        f"Unexpected Jenkins response ({response.status}) from {url}.",
                        code="E_RELEASE_JENKINS_HTTP",
                        reason=str(response.status),
                    )
                if expect_json:
                    try:
                        return json.loads(body or "{}")
                    except json.JSONDecodeError as exc:
                        raise PlatformError(
                            f"Invalid JSON returned by Jenkins: {exc}",
                            code="E_RELEASE_JENKINS_RESPONSE_INVALID",
                            reason="jenkins_json",
                        ) from exc
                if method.upper() == "POST":
                    return dict(response.headers.items())
                return body
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace").strip()
            except Exception:
                detail = ""
            message = _summarize_http_error_body(detail) or str(exc)
            code = "E_RELEASE_JENKINS_HTTP"
            if exc.code in {401, 403}:
                code = "E_RELEASE_JENKINS_AUTH"
            raise PlatformError(
                f"Jenkins API request failed ({exc.code}) at {url}: {message}",
                code=code,
                reason=str(exc.code),
            ) from exc
        except urllib.error.URLError as exc:
            if _is_tls_verification_error(exc.reason):
                raise PlatformError(
                    f"Jenkins TLS verification failed: {exc.reason}. "
                    f"{jenkins_tls_help_text(cafile=self.ssl_context_cafile, source=self.ssl_context_source)}",
                    code="E_RELEASE_JENKINS_TLS",
                    reason="jenkins_tls",
                ) from exc
            raise PlatformError(
                f"Could not reach Jenkins API: {exc.reason}",
                code="E_RELEASE_JENKINS_UNREACHABLE",
                reason="jenkins_network",
            ) from exc
        except ssl.SSLCertVerificationError as exc:
            raise PlatformError(
                f"Jenkins TLS verification failed: {exc}. "
                f"{jenkins_tls_help_text(cafile=self.ssl_context_cafile, source=self.ssl_context_source)}",
                code="E_RELEASE_JENKINS_TLS",
                reason="jenkins_tls",
            ) from exc
        except TimeoutError as exc:
            raise PlatformError(
                "Jenkins API request timed out.",
                code="E_RELEASE_JENKINS_TIMEOUT",
                reason="jenkins_timeout",
            ) from exc

    def _basic_auth_header(self) -> str:
        raw = f"{self.user}:{self.token}".encode("utf-8")
        return f"Basic {base64.b64encode(raw).decode('ascii')}"

    @staticmethod
    def _normalize_param_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, (dict, list, tuple, set)):
            return json.dumps(value, sort_keys=True)
        return str(value)


def _summarize_http_error_body(detail: str) -> str:
    raw = str(detail or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered.startswith("<!doctype html") or lowered.startswith("<html") or "<html" in lowered[:256]:
        title_start = lowered.find("<title>")
        title_end = lowered.find("</title>")
        if title_start != -1 and title_end != -1 and title_end > title_start:
            title = html.unescape(raw[title_start + 7:title_end]).strip()
            if title:
                return f"Jenkins returned an HTML error page ({title})."
        return "Jenkins returned an HTML error page."
    compact = " ".join(raw.split())
    if len(compact) > 400:
        return f"{compact[:397]}..."
    return compact


def _is_tls_verification_error(reason: object) -> bool:
    if isinstance(reason, ssl.SSLCertVerificationError):
        return True
    if isinstance(reason, ssl.SSLError):
        return "certificate verify failed" in str(reason).lower()
    return "certificate verify failed" in str(reason or "").lower()
