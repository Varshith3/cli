# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from platform_cli.core.errors import PlatformError
from platform_cli.core.repo_roots import resolve_repo_root
from platform_cli.manifests.orchestrate_load import load_orchestrate_json_file
from platform_cli.manifests.orchestrate_validate import (
    validate_orchestrate_kernel_contract,
    validate_orchestrate_plugin_contract,
    validate_orchestrate_scenario_contract,
    validate_orchestrate_topology_contract,
)


_KERNEL_CONTRACT = Path(".ghdp/orchestrate/kernel.json")
_TOPOLOGY_CONTRACT = Path(".ghdp/orchestrate/topology.json")
_SCENARIOS_MANIFEST = Path(".ghdp/orchestrate/scenarios/manifest.json")
_PLUGINS_ROOT = Path(".ghdp/plugins")


def load_kernel_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    resolved_root = resolve_repo_root(repo_root)
    payload = load_orchestrate_json_file(resolved_root / _KERNEL_CONTRACT)
    messages = validate_orchestrate_kernel_contract(payload, source=_KERNEL_CONTRACT.as_posix())
    if messages:
        raise PlatformError(
            "; ".join(messages),
            code="E_ORCHESTRATE_KERNEL_INVALID",
            reason=_KERNEL_CONTRACT.as_posix(),
        )
    return payload


def load_topology_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    resolved_root = resolve_repo_root(repo_root)
    payload = load_orchestrate_json_file(resolved_root / _TOPOLOGY_CONTRACT)
    messages = validate_orchestrate_topology_contract(payload, source=_TOPOLOGY_CONTRACT.as_posix())
    if messages:
        raise PlatformError(
            "; ".join(messages),
            code="E_ORCHESTRATE_TOPOLOGY_INVALID",
            reason=_TOPOLOGY_CONTRACT.as_posix(),
        )
    return payload


def load_provider_plugin_contract(*, plugin_id: str, repo_root: Path | None = None) -> Dict[str, Any]:
    resolved_root = resolve_repo_root(repo_root)
    path = resolved_root / _PLUGINS_ROOT / plugin_id / "plugin.json"
    payload = load_orchestrate_json_file(path)
    messages = validate_orchestrate_plugin_contract(payload, source=str(path.relative_to(resolved_root)).replace("\\", "/"))
    if messages:
        raise PlatformError(
            "; ".join(messages),
            code="E_ORCHESTRATE_PLUGIN_INVALID",
            reason=str(path.relative_to(resolved_root)).replace("\\", "/"),
        )
    if str(payload.get("id", "")).strip() != str(plugin_id).strip():
        raise PlatformError(
            f"Provider plugin contract id does not match requested plugin '{plugin_id}'.",
            code="E_ORCHESTRATE_PLUGIN_INVALID",
            reason=str(path.relative_to(resolved_root)).replace("\\", "/"),
        )
    return payload


def load_scenario_contracts(*, repo_root: Path | None = None) -> List[Dict[str, Any]]:
    resolved_root = resolve_repo_root(repo_root)
    manifest = load_orchestrate_json_file(resolved_root / _SCENARIOS_MANIFEST)
    scenarios = manifest.get("scenarios", [])
    if not isinstance(scenarios, list) or not scenarios:
        raise PlatformError(
            "Orchestrate scenarios manifest must define a non-empty 'scenarios' list.",
            code="E_ORCHESTRATE_SCENARIO_INVALID",
            reason=_SCENARIOS_MANIFEST.as_posix(),
        )

    contracts: List[Dict[str, Any]] = []
    for entry in scenarios:
        if not isinstance(entry, dict):
            raise PlatformError(
                "Scenario manifest entries must be objects.",
                code="E_ORCHESTRATE_SCENARIO_INVALID",
                reason=_SCENARIOS_MANIFEST.as_posix(),
            )
        scenario_id = str(entry.get("id", "")).strip()
        contract_path_str = str(entry.get("contract_path", "")).strip()
        if not scenario_id or not contract_path_str:
            raise PlatformError(
                "Scenario manifest entries must define id and contract_path.",
                code="E_ORCHESTRATE_SCENARIO_INVALID",
                reason=_SCENARIOS_MANIFEST.as_posix(),
            )
        path = resolved_root / Path(contract_path_str)
        payload = load_orchestrate_json_file(path)
        messages = validate_orchestrate_scenario_contract(payload, source=contract_path_str)
        if str(payload.get("id", "")).strip() != scenario_id:
            messages.append(f"{contract_path_str} id does not match manifest entry '{scenario_id}'.")
        if messages:
            raise PlatformError(
                "; ".join(messages),
                code="E_ORCHESTRATE_SCENARIO_INVALID",
                reason=contract_path_str,
            )
        contracts.append(payload)
    return contracts


def load_scenario_contract(*, scenario_id: str, repo_root: Path | None = None) -> Dict[str, Any]:
    normalized = str(scenario_id).strip()
    for contract in load_scenario_contracts(repo_root=repo_root):
        if str(contract.get("id", "")).strip() == normalized:
            return contract
    raise PlatformError(
        f"Scenario '{scenario_id}' is not defined under .ghdp/orchestrate/scenarios.",
        code="E_ORCHESTRATE_SCENARIO_MISSING",
        reason=normalized,
    )
