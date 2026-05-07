from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict

from platform_cli.core.errors import PlatformError
from platform_cli.manifests.orchestrate_kernel_load import load_kernel_contract, load_provider_plugin_contract, load_topology_contract
from platform_cli.tools.ai_provider import detect_provider_statuses


@dataclass
class ProviderAdapterResolution:
    provider_plugin: str
    effective_plugin: str
    requested_host: str
    effective_host: str
    effective_provider: str
    executor: str
    model: str
    fallback_used: bool
    available: bool
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def resolve_provider_adapter(
    *,
    provider_plugin_id: str,
    requested_host: str | None = None,
    repo_root: Path | None = None,
) -> ProviderAdapterResolution:
    kernel = load_kernel_contract(repo_root=repo_root)
    topology = load_topology_contract(repo_root=repo_root)
    plugin = load_provider_plugin_contract(plugin_id=provider_plugin_id, repo_root=repo_root)
    plugin_host = str(plugin.get("host_mode", "")).strip()
    effective_requested_host = str(requested_host or plugin_host or "").strip()
    if not effective_requested_host:
        raise PlatformError(
            f"Provider plugin '{provider_plugin_id}' does not define a host_mode and no requested host was supplied.",
            code="E_ORCHESTRATE_PROVIDER_INVALID",
            reason=provider_plugin_id,
        )

    host_preferences = topology.get("host_preferences", {})
    if not isinstance(host_preferences, dict):
        raise PlatformError(
            "Topology host_preferences must be an object.",
            code="E_ORCHESTRATE_TOPOLOGY_INVALID",
            reason="host_preferences",
        )
    preferred_hosts = host_preferences.get(provider_plugin_id, [])
    if not isinstance(preferred_hosts, list) or not preferred_hosts:
        preferred_hosts = [effective_requested_host]

    supported_plugins = kernel.get("provider_resolution", {}).get("supported_provider_plugins", [])
    if provider_plugin_id not in supported_plugins:
        raise PlatformError(
            f"Provider plugin '{provider_plugin_id}' is not enabled by the kernel contract.",
            code="E_ORCHESTRATE_PROVIDER_INVALID",
            reason=provider_plugin_id,
        )

    headless_fallbacks = kernel.get("provider_resolution", {}).get("headless_host_fallbacks", {})
    fallback_provider = str(headless_fallbacks.get(effective_requested_host, "")).strip()
    statuses = detect_provider_statuses(refresh=False)

    effective_provider = "codex" if "codex" in provider_plugin_id else "claude"
    fallback_used = False
    if effective_requested_host.startswith("vscode_") and fallback_provider:
        effective_provider = fallback_provider
        fallback_used = True
    effective_plugin = provider_plugin_id
    if fallback_used:
        effective_plugin = f"provider-{effective_provider}"

    status = statuses.get(effective_provider)
    if status is None:
        raise PlatformError(
            f"Provider '{effective_provider}' is not supported by the current runtime.",
            code="E_ORCHESTRATE_PROVIDER_INVALID",
            reason=effective_provider,
        )

    effective_host = effective_requested_host
    if fallback_used and fallback_provider:
        effective_host = fallback_provider

    return ProviderAdapterResolution(
        provider_plugin=provider_plugin_id,
        effective_plugin=effective_plugin,
        requested_host=effective_requested_host,
        effective_host=effective_host,
        effective_provider=effective_provider,
        executor=str(plugin.get("executor", "")).strip() or effective_provider,
        model=str(plugin.get("model", "")).strip(),
        fallback_used=fallback_used,
        available=bool(status.available),
        detail=str(status.detail or ""),
    )
