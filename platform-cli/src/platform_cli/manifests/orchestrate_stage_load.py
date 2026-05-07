from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from platform_cli.core.errors import PlatformError
from platform_cli.core.repo_roots import resolve_repo_root
from platform_cli.manifests.orchestrate_load import load_orchestrate_json_file
from platform_cli.manifests.orchestrate_validate import validate_orchestrate_manifest, validate_orchestrate_stage_contract


_STAGES_MANIFEST = Path(".ghdp/orchestrate/stages/manifest.json")


def load_stage_contracts(*, repo_root: Path | None = None) -> List[Dict[str, Any]]:
    resolved_root = resolve_repo_root(repo_root)
    payload = load_orchestrate_json_file(resolved_root / _STAGES_MANIFEST)
    entries, messages = validate_orchestrate_manifest(
        payload,
        collection_key="stages",
        source=_STAGES_MANIFEST.as_posix(),
    )
    if messages:
        raise PlatformError(
            "; ".join(messages),
            code="E_ORCHESTRATE_STAGE_CONTRACT_INVALID",
            reason=_STAGES_MANIFEST.as_posix(),
        )

    contracts: List[Dict[str, Any]] = []
    for entry in entries:
        stage_id = str(entry.get("id", "")).strip()
        contract_path_str = str(entry.get("contract_path", "")).strip()
        if not stage_id or not contract_path_str:
            raise PlatformError(
                "Stage manifest entries must define id and contract_path.",
                code="E_ORCHESTRATE_STAGE_CONTRACT_INVALID",
                reason=_STAGES_MANIFEST.as_posix(),
            )
        contract_path = resolved_root / Path(contract_path_str)
        contract_payload = load_orchestrate_json_file(contract_path)
        contract_messages = validate_orchestrate_stage_contract(
            contract_payload,
            source=contract_path_str,
        )
        if str(contract_payload.get("id", "")).strip() != stage_id:
            contract_messages.append(f"{contract_path_str} id does not match manifest entry '{stage_id}'.")
        if contract_messages:
            raise PlatformError(
                "; ".join(contract_messages),
                code="E_ORCHESTRATE_STAGE_CONTRACT_INVALID",
                reason=contract_path_str,
            )
        contracts.append(contract_payload)
    return contracts


def load_stage_contract(*, stage_id: str, repo_root: Path | None = None) -> Dict[str, Any]:
    normalized = str(stage_id).strip()
    for contract in load_stage_contracts(repo_root=repo_root):
        if str(contract.get("id", "")).strip() == normalized:
            return contract
    raise PlatformError(
        f"Stage contract '{normalized}' is not defined in the repo-level orchestrate contracts.",
        code="E_ORCHESTRATE_STAGE_CONTRACT_MISSING",
        reason=normalized,
    )
