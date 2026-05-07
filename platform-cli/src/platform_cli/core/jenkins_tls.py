"""Jenkins TLS helpers.

NOTE: Architectural rules in ARCHITECTURE.md — do not refactor cross-layer.
"""

from __future__ import annotations

import os
import ssl
import sys
from dataclasses import dataclass
from pathlib import Path

from platform_cli.core.errors import PlatformError

JENKINS_CA_BUNDLE_ENV = "GHDP_JENKINS_CA_BUNDLE"
_SSL_CERT_FILE_ENV = "SSL_CERT_FILE"
_MACOS_CA_BUNDLE_CANDIDATES = (
    "/opt/homebrew/etc/openssl@3/cert.pem",
    "/usr/local/etc/openssl@3/cert.pem",
    "/opt/homebrew/etc/openssl@1.1/cert.pem",
    "/usr/local/etc/openssl@1.1/cert.pem",
    "/private/etc/ssl/cert.pem",
    "/etc/ssl/cert.pem",
)


@dataclass(frozen=True)
class JenkinsTlsConfig:
    context: ssl.SSLContext
    source: str
    cafile: str = ""


def build_jenkins_tls_config() -> JenkinsTlsConfig:
    source, cafile = resolve_jenkins_ca_bundle()
    if cafile:
        try:
            context = ssl.create_default_context(cafile=cafile)
        except ssl.SSLError as exc:
            raise PlatformError(
                f"{source} points to '{cafile}', but OpenSSL could not load it as a CA bundle: {exc}",
                code="E_RELEASE_JENKINS_CA_BUNDLE_INVALID",
                reason=source,
            ) from exc
        return JenkinsTlsConfig(context=context, source=source, cafile=cafile)
    return JenkinsTlsConfig(
        context=ssl.create_default_context(),
        source="system_default",
        cafile="",
    )


def resolve_jenkins_ca_bundle() -> tuple[str, str]:
    explicit = _normalize_path(os.getenv(JENKINS_CA_BUNDLE_ENV))
    if explicit:
        return JENKINS_CA_BUNDLE_ENV, _validate_bundle_path(explicit, env_name=JENKINS_CA_BUNDLE_ENV)

    ssl_cert_file = _normalize_path(os.getenv(_SSL_CERT_FILE_ENV))
    if ssl_cert_file:
        return _SSL_CERT_FILE_ENV, _validate_bundle_path(ssl_cert_file, env_name=_SSL_CERT_FILE_ENV)

    default_paths = ssl.get_default_verify_paths()
    default_cafile = _normalize_path(default_paths.cafile)
    if default_cafile and Path(default_cafile).is_file():
        return "openssl_default", default_cafile

    if sys.platform == "darwin":
        for candidate in _MACOS_CA_BUNDLE_CANDIDATES:
            normalized = _normalize_path(candidate)
            if normalized and Path(normalized).is_file():
                return "macos_fallback", normalized

    return "", ""


def jenkins_tls_help_text(*, cafile: str, source: str) -> str:
    parts: list[str] = []
    if cafile:
        parts.append(f"GHDP used CA bundle '{cafile}' from {source}.")
    else:
        parts.append("GHDP used the runtime default trust store.")
    parts.append(
        f"Set {JENKINS_CA_BUNDLE_ENV}=/path/to/cert.pem to point GHDP at a PEM bundle that includes your Jenkins issuer."
    )
    return " ".join(parts)


def _validate_bundle_path(path: str, *, env_name: str) -> str:
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        raise PlatformError(
            f"{env_name} points to '{resolved}', but that file does not exist or is not a regular file.",
            code="E_RELEASE_JENKINS_CA_BUNDLE_INVALID",
            reason=env_name,
        )
    return str(resolved)


def _normalize_path(value: str | None) -> str:
    return str(value or "").strip()
