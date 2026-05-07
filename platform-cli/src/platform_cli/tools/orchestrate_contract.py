from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from platform_cli.core.errors import PlatformError
from platform_cli.core.repo_roots import resolve_repo_root
from platform_cli.manifests.orchestrate_load import load_orchestrate_json_file
from platform_cli.manifests.orchestrate_validate import (
    validate_orchestrate_agent_contract,
    validate_orchestrate_kernel_contract,
    validate_orchestrate_merge_hygiene_config,
    validate_orchestrate_manifest,
    validate_orchestrate_plugin_contract,
    validate_orchestrate_scenario_contract,
    validate_orchestrate_stage_contract,
    validate_orchestrate_topology_contract,
)
from platform_cli.tools.repo_ready_generation import current_branch_name


_AGENTS_MANIFEST = Path(".ghdp/agents/manifest.json")
_AGENTS_DOC = Path(".ghdp/agents/AGENTS.md")
_AGENTS_ROOT = Path(".ghdp/agents")
_SKILLS_MANIFEST = Path(".ghdp/skills/manifest.json")
_SKILLS_DOC = Path(".ghdp/skills/SKILLS.md")
_PLUGINS_MANIFEST = Path(".ghdp/plugins/manifest.json")
_PLUGINS_DOC = Path(".ghdp/plugins/PLUGINS.md")
_PLUGINS_ROOT = Path(".ghdp/plugins")
_MEMORY_MANIFEST = Path(".ghdp/memory/manifest.json")
_MEMORY_DOC = Path(".ghdp/memory/README.md")
_ORCHESTRATE_DOC = Path(".ghdp/orchestrate/README.md")
_KERNEL_CONTRACT = Path(".ghdp/orchestrate/kernel.json")
_TOPOLOGY_CONTRACT = Path(".ghdp/orchestrate/topology.json")
_MERGE_HYGIENE_CONTRACT = Path(".ghdp/orchestrate/merge-hygiene.json")
_STAGES_MANIFEST = Path(".ghdp/orchestrate/stages/manifest.json")
_STAGES_DOC = Path(".ghdp/orchestrate/stages/STAGES.md")
_SCENARIOS_MANIFEST = Path(".ghdp/orchestrate/scenarios/manifest.json")
_INTENT_PATH = Path(".ghdp/frbr/intent.json")
_BRANCHES_ROOT = Path(".ghdp/orchestrate/branches")


@dataclass
class ContractFileCheck:
    rel_path: str
    exists: bool
    kind: str
    messages: List[str] = field(default_factory=list)


@dataclass
class OrchestrateContractStatus:
    repo_root: str
    branch_name: str
    branch_slug: str
    ticket_key: str
    repo_contract_ready: bool
    branch_runtime_ready: bool
    contract_ready: bool
    agents_count: int
    skills_count: int
    plugins_count: int
    memory_partition_count: int
    active_run_key: str
    branch_runtime_root: str
    branch_runtime_mode: str
    file_checks: List[ContractFileCheck] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo_root": self.repo_root,
            "branch_name": self.branch_name,
            "branch_slug": self.branch_slug,
            "ticket_key": self.ticket_key,
            "repo_contract_ready": self.repo_contract_ready,
            "branch_runtime_ready": self.branch_runtime_ready,
            "contract_ready": self.contract_ready,
            "agents_count": self.agents_count,
            "skills_count": self.skills_count,
            "plugins_count": self.plugins_count,
            "memory_partition_count": self.memory_partition_count,
            "active_run_key": self.active_run_key,
            "branch_runtime_root": self.branch_runtime_root,
            "branch_runtime_mode": self.branch_runtime_mode,
            "file_checks": [asdict(item) for item in self.file_checks],
            "missing": list(self.missing),
            "warnings": list(self.warnings),
        }


def inspect_orchestrate_contract(*, repo_root: Path | None = None) -> OrchestrateContractStatus:
    resolved_root = resolve_repo_root(repo_root)
    branch_name = current_branch_name(resolved_root)
    branch_slug = slugify_branch_name(branch_name)
    runtime_folder = runtime_branch_folder_name(resolved_root, branch_name)
    ticket_key = _load_ticket_key(resolved_root / _INTENT_PATH)

    file_checks: List[ContractFileCheck] = []
    warnings: List[str] = []
    missing: List[str] = []
    repo_missing: List[str] = []

    agents_payload = _record_manifest_check(resolved_root, _AGENTS_MANIFEST, "agents_manifest", file_checks, missing, repo_missing)
    _record_file_check(resolved_root, _AGENTS_DOC, "agents_doc", file_checks, missing, repo_missing)
    skills_payload = _record_manifest_check(resolved_root, _SKILLS_MANIFEST, "skills_manifest", file_checks, missing, repo_missing)
    _record_file_check(resolved_root, _SKILLS_DOC, "skills_doc", file_checks, missing, repo_missing)
    plugins_payload = _record_manifest_check(resolved_root, _PLUGINS_MANIFEST, "plugins_manifest", file_checks, missing, repo_missing)
    _record_file_check(resolved_root, _PLUGINS_DOC, "plugins_doc", file_checks, missing, repo_missing)
    memory_payload = _record_manifest_check(resolved_root, _MEMORY_MANIFEST, "memory_manifest", file_checks, missing, repo_missing)
    _record_file_check(resolved_root, _MEMORY_DOC, "memory_doc", file_checks, missing, repo_missing)
    _record_file_check(resolved_root, _ORCHESTRATE_DOC, "orchestrate_doc", file_checks, missing, repo_missing)
    kernel_payload = _record_manifest_check(resolved_root, _KERNEL_CONTRACT, "kernel_contract", file_checks, missing, repo_missing)
    topology_payload = _record_manifest_check(resolved_root, _TOPOLOGY_CONTRACT, "topology_contract", file_checks, missing, repo_missing)
    merge_hygiene_payload = _record_manifest_check(resolved_root, _MERGE_HYGIENE_CONTRACT, "merge_hygiene_contract", file_checks, missing, repo_missing)
    stages_payload = _record_manifest_check(resolved_root, _STAGES_MANIFEST, "stages_manifest", file_checks, missing, repo_missing)
    _record_file_check(resolved_root, _STAGES_DOC, "stages_doc", file_checks, missing, repo_missing)
    scenarios_payload = _record_manifest_check(resolved_root, _SCENARIOS_MANIFEST, "scenarios_manifest", file_checks, missing, repo_missing)

    if kernel_payload:
        warnings.extend(validate_orchestrate_kernel_contract(kernel_payload, source=_KERNEL_CONTRACT.as_posix()))
    if topology_payload:
        warnings.extend(validate_orchestrate_topology_contract(topology_payload, source=_TOPOLOGY_CONTRACT.as_posix()))
    if merge_hygiene_payload:
        warnings.extend(validate_orchestrate_merge_hygiene_config(merge_hygiene_payload, source=_MERGE_HYGIENE_CONTRACT.as_posix()))

    agents = _manifest_entries(agents_payload, "agents", warnings, file_checks, _AGENTS_MANIFEST)
    agent_contracts_valid = _record_agent_contract_checks(
        resolved_root,
        agents,
        warnings=warnings,
        file_checks=file_checks,
        missing=missing,
        repo_missing=repo_missing,
    )
    stages = _manifest_entries(stages_payload, "stages", warnings, file_checks, _STAGES_MANIFEST)
    stage_contracts_valid = _record_stage_contract_checks(
        resolved_root,
        stages,
        warnings=warnings,
        file_checks=file_checks,
        missing=missing,
        repo_missing=repo_missing,
    )
    skills = _manifest_entries(skills_payload, "skills", warnings, file_checks, _SKILLS_MANIFEST)
    plugins = _manifest_entries(plugins_payload, "plugins", warnings, file_checks, _PLUGINS_MANIFEST)
    plugin_contracts_valid = _record_plugin_contract_checks(
        resolved_root,
        plugins,
        warnings=warnings,
        file_checks=file_checks,
        missing=missing,
        repo_missing=repo_missing,
    )
    memory_partitions = _manifest_entries(memory_payload, "partitions", warnings, file_checks, _MEMORY_MANIFEST)
    scenario_contracts_valid = _record_scenario_contract_checks(
        resolved_root,
        scenarios_payload,
        warnings=warnings,
        file_checks=file_checks,
        missing=missing,
        repo_missing=repo_missing,
    )

    if branch_name and branch_name.startswith("feature/"):
        runtime_root = resolved_root / _BRANCHES_ROOT / runtime_folder
        active_run_key = _load_active_run_key(runtime_root / "branch_state.json")
        if runtime_root.exists():
            branch_runtime_missing = _branch_runtime_missing(runtime_root, active_run_key)
            missing.extend(str(_BRANCHES_ROOT / runtime_folder / rel_path).replace("\\", "/") for rel_path in branch_runtime_missing)
            branch_runtime_ready = not branch_runtime_missing
            branch_runtime_mode = "active"
            for rel_path in branch_runtime_missing:
                file_checks.append(
                    ContractFileCheck(
                        rel_path=str((_BRANCHES_ROOT / runtime_folder / rel_path).as_posix()),
                        exists=False,
                        kind="branch_runtime",
                        messages=["Missing required branch runtime artifact for the active feature branch."],
                    )
                )
        else:
            closeout_receipt = _load_merge_hygiene_receipt(resolved_root, branch_slug, merge_hygiene_payload)
            if closeout_receipt:
                active_run_key = str(closeout_receipt.get("active_run_key", "")).strip()
                branch_runtime_ready = True
                branch_runtime_mode = "finalized"
                warnings.append("Branch runtime has already been finalized and pruned; merge-hygiene receipt is present.")
            else:
                branch_runtime_ready = False
                branch_runtime_mode = "missing"
                missing.append(str((_BRANCHES_ROOT / runtime_folder).as_posix()))
                file_checks.append(
                    ContractFileCheck(
                        rel_path=str((_BRANCHES_ROOT / runtime_folder).as_posix()),
                        exists=False,
                        kind="branch_runtime",
                        messages=["Missing branch runtime folder and no merge-hygiene closeout receipt was found."],
                    )
                )
    else:
        runtime_root = resolved_root / _BRANCHES_ROOT / runtime_folder if runtime_folder else resolved_root / _BRANCHES_ROOT
        active_run_key = ""
        branch_runtime_ready = True
        branch_runtime_mode = "not_applicable"
        if not branch_name:
            warnings.append("Current branch name could not be resolved; branch runtime validation was skipped.")

    repo_contract_ready = (
        not repo_missing
        and agent_contracts_valid
        and stage_contracts_valid
        and plugin_contracts_valid
        and scenario_contracts_valid
        and len(agents) > 0
        and len(stages) > 0
        and len(skills) > 0
        and len(plugins) > 0
    )
    contract_ready = repo_contract_ready and branch_runtime_ready

    if not ticket_key:
        warnings.append("Repo intent is missing ticket_key metadata.")
    if branch_name and ticket_key and ticket_key not in branch_name:
        warnings.append("Repo intent ticket_key does not match the current branch name.")

    return OrchestrateContractStatus(
        repo_root=str(resolved_root),
        branch_name=branch_name,
        branch_slug=branch_slug,
        ticket_key=ticket_key,
        repo_contract_ready=repo_contract_ready,
        branch_runtime_ready=branch_runtime_ready,
        contract_ready=contract_ready,
        agents_count=len(agents),
        skills_count=len(skills),
        plugins_count=len(plugins),
        memory_partition_count=len(memory_partitions),
        active_run_key=active_run_key,
        branch_runtime_root=str(runtime_root),
        branch_runtime_mode=branch_runtime_mode,
        file_checks=file_checks,
        missing=sorted(dict.fromkeys(missing)),
        warnings=warnings,
    )


def slugify_branch_name(branch_name: str) -> str:
    normalized = str(branch_name or "").strip().lower()
    normalized = normalized.replace("/", "-").replace("_", "-")
    normalized = re.sub(r"[^a-z0-9-]+", "-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
    return normalized


def runtime_branch_folder_name(repo_root: Path, branch_name: str) -> str:
    full_slug = slugify_branch_name(branch_name)
    if not full_slug:
        return ""
    existing_root = repo_root / _BRANCHES_ROOT / full_slug
    if existing_root.exists():
        return full_slug
    candidate = repo_root / _BRANCHES_ROOT / full_slug / "runs" / "20260504-223548__codex__ist__run" / "resume_context.md"
    if len(str(candidate)) <= 220:
        return full_slug

    ticket_match = re.search(r"([A-Z][A-Z0-9]+-\d+)", branch_name or "")
    ticket = ticket_match.group(1).lower() if ticket_match else "feature"
    digest = hashlib.sha1(full_slug.encode("utf-8")).hexdigest()[:10]
    compact = f"{ticket}-{digest}"
    return compact


def load_agent_contracts(*, repo_root: Path | None = None) -> List[Dict[str, Any]]:
    resolved_root = resolve_repo_root(repo_root)
    payload = load_orchestrate_json_file(resolved_root / _AGENTS_MANIFEST)
    entries, messages = validate_orchestrate_manifest(
        payload,
        collection_key="agents",
        source=_AGENTS_MANIFEST.as_posix(),
    )
    if messages:
        raise PlatformError(
            "; ".join(messages),
            code="E_ORCHESTRATE_AGENT_CONTRACT_INVALID",
            reason=_AGENTS_MANIFEST.as_posix(),
        )

    contracts: List[Dict[str, Any]] = []
    for entry in entries:
        agent_id = str(entry.get("id", "")).strip()
        contract_path_str = str(entry.get("contract_path", "")).strip()
        if not agent_id or not contract_path_str:
            raise PlatformError(
                "Agent manifest entries must define id and contract_path.",
                code="E_ORCHESTRATE_AGENT_CONTRACT_INVALID",
                reason=_AGENTS_MANIFEST.as_posix(),
            )
        contract_path = resolved_root / Path(contract_path_str)
        contract_payload = load_orchestrate_json_file(contract_path)
        contract_messages = validate_orchestrate_agent_contract(
            contract_payload,
            source=contract_path_str,
        )
        if str(contract_payload.get("id", "")).strip() != agent_id:
            contract_messages.append(f"{contract_path_str} id does not match manifest entry '{agent_id}'.")
        if contract_messages:
            raise PlatformError(
                "; ".join(contract_messages),
                code="E_ORCHESTRATE_AGENT_CONTRACT_INVALID",
                reason=contract_path_str,
            )
        contracts.append(contract_payload)
    return contracts


def load_agent_contract(*, agent_id: str, repo_root: Path | None = None) -> Dict[str, Any]:
    normalized = str(agent_id).strip()
    for contract in load_agent_contracts(repo_root=repo_root):
        if str(contract.get("id", "")).strip() == normalized:
            return contract
    raise PlatformError(
        f"Agent contract '{normalized}' is not defined in the repo-level orchestrate contracts.",
        code="E_ORCHESTRATE_AGENT_CONTRACT_MISSING",
        reason=normalized,
    )


def _record_file_check(
    repo_root: Path,
    rel_path: Path,
    kind: str,
    file_checks: List[ContractFileCheck],
    missing: List[str],
    repo_missing: List[str],
) -> None:
    path = repo_root / rel_path
    exists = path.exists()
    file_checks.append(
        ContractFileCheck(
            rel_path=rel_path.as_posix(),
            exists=exists,
            kind=kind,
            messages=[] if exists else ["Required orchestrate contract file is missing."],
        )
    )
    if not exists:
        missing.append(rel_path.as_posix())
        repo_missing.append(rel_path.as_posix())


def _record_manifest_check(
    repo_root: Path,
    rel_path: Path,
    kind: str,
    file_checks: List[ContractFileCheck],
    missing: List[str],
    repo_missing: List[str],
) -> Dict[str, Any]:
    path = repo_root / rel_path
    if not path.exists():
        file_checks.append(
            ContractFileCheck(
                rel_path=rel_path.as_posix(),
                exists=False,
                kind=kind,
                messages=["Required orchestrate manifest is missing."],
            )
        )
        missing.append(rel_path.as_posix())
        repo_missing.append(rel_path.as_posix())
        return {}

    payload = load_orchestrate_json_file(path)
    file_checks.append(
        ContractFileCheck(
            rel_path=rel_path.as_posix(),
            exists=True,
            kind=kind,
            messages=[],
        )
    )
    return payload


def _record_agent_contract_checks(
    repo_root: Path,
    entries: List[Dict[str, Any]],
    *,
    warnings: List[str],
    file_checks: List[ContractFileCheck],
    missing: List[str],
    repo_missing: List[str],
) -> bool:
    all_valid = True
    for entry in entries:
        agent_id = str(entry.get("id", "")).strip()
        contract_path_str = str(entry.get("contract_path", "")).strip()
        if not agent_id or not contract_path_str:
            warning = "Agent manifest entries must define id and contract_path."
            warnings.append(warning)
            all_valid = False
            file_checks.append(
                ContractFileCheck(
                    rel_path=_AGENTS_MANIFEST.as_posix(),
                    exists=True,
                    kind="agents_manifest",
                    messages=[warning],
                )
            )
            continue

        rel_path = Path(contract_path_str)
        path = repo_root / rel_path
        if not path.exists():
            file_checks.append(
                ContractFileCheck(
                    rel_path=rel_path.as_posix(),
                    exists=False,
                    kind="agent_contract",
                    messages=[f"Missing repo-level agent contract for '{agent_id}'."],
                )
            )
            missing.append(rel_path.as_posix())
            repo_missing.append(rel_path.as_posix())
            all_valid = False
            continue

        payload = load_orchestrate_json_file(path)
        messages = validate_orchestrate_agent_contract(payload, source=rel_path.as_posix())
        if str(payload.get("id", "")).strip() != agent_id:
            messages.append(f"{rel_path.as_posix()} id does not match manifest entry '{agent_id}'.")
        warnings.extend(messages)
        if messages:
            all_valid = False
        file_checks.append(
            ContractFileCheck(
                rel_path=rel_path.as_posix(),
                exists=True,
                kind="agent_contract",
                messages=messages,
            )
        )
    return all_valid


def _record_stage_contract_checks(
    repo_root: Path,
    entries: List[Dict[str, Any]],
    *,
    warnings: List[str],
    file_checks: List[ContractFileCheck],
    missing: List[str],
    repo_missing: List[str],
) -> bool:
    all_valid = True
    for entry in entries:
        stage_id = str(entry.get("id", "")).strip()
        contract_path_str = str(entry.get("contract_path", "")).strip()
        if not stage_id or not contract_path_str:
            warning = "Stage manifest entries must define id and contract_path."
            warnings.append(warning)
            all_valid = False
            file_checks.append(
                ContractFileCheck(
                    rel_path=_STAGES_MANIFEST.as_posix(),
                    exists=True,
                    kind="stages_manifest",
                    messages=[warning],
                )
            )
            continue

        rel_path = Path(contract_path_str)
        path = repo_root / rel_path
        if not path.exists():
            file_checks.append(
                ContractFileCheck(
                    rel_path=rel_path.as_posix(),
                    exists=False,
                    kind="stage_contract",
                    messages=[f"Missing repo-level stage contract for '{stage_id}'."],
                )
            )
            missing.append(rel_path.as_posix())
            repo_missing.append(rel_path.as_posix())
            all_valid = False
            continue

        payload = load_orchestrate_json_file(path)
        messages = validate_orchestrate_stage_contract(payload, source=rel_path.as_posix())
        if str(payload.get("id", "")).strip() != stage_id:
            messages.append(f"{rel_path.as_posix()} id does not match manifest entry '{stage_id}'.")
        warnings.extend(messages)
        if messages:
            all_valid = False
        file_checks.append(
            ContractFileCheck(
                rel_path=rel_path.as_posix(),
                exists=True,
                kind="stage_contract",
                messages=messages,
            )
        )
    return all_valid


def _record_plugin_contract_checks(
    repo_root: Path,
    entries: List[Dict[str, Any]],
    *,
    warnings: List[str],
    file_checks: List[ContractFileCheck],
    missing: List[str],
    repo_missing: List[str],
) -> bool:
    all_valid = True
    for entry in entries:
        plugin_id = str(entry.get("id", "")).strip()
        if not plugin_id:
            warnings.append(f"{_PLUGINS_MANIFEST.as_posix()} contains a plugin entry without an id.")
            all_valid = False
            continue
        rel_path = _PLUGINS_ROOT / plugin_id / "plugin.json"
        path = repo_root / rel_path
        if not path.exists():
            file_checks.append(
                ContractFileCheck(
                    rel_path=rel_path.as_posix(),
                    exists=False,
                    kind="plugin_contract",
                    messages=[f"Missing repo-level plugin contract for '{plugin_id}'."],
                )
            )
            missing.append(rel_path.as_posix())
            repo_missing.append(rel_path.as_posix())
            all_valid = False
            continue
        payload = load_orchestrate_json_file(path)
        messages = validate_orchestrate_plugin_contract(payload, source=rel_path.as_posix())
        if str(payload.get("id", "")).strip() != plugin_id:
            messages.append(f"{rel_path.as_posix()} id does not match manifest entry '{plugin_id}'.")
        warnings.extend(messages)
        if messages:
            all_valid = False
        file_checks.append(
            ContractFileCheck(
                rel_path=rel_path.as_posix(),
                exists=True,
                kind="plugin_contract",
                messages=messages,
            )
        )
    return all_valid


def _record_scenario_contract_checks(
    repo_root: Path,
    manifest_payload: Dict[str, Any],
    *,
    warnings: List[str],
    file_checks: List[ContractFileCheck],
    missing: List[str],
    repo_missing: List[str],
) -> bool:
    entries = _manifest_entries(manifest_payload, "scenarios", warnings, file_checks, _SCENARIOS_MANIFEST)
    all_valid = True
    for entry in entries:
        scenario_id = str(entry.get("id", "")).strip()
        contract_path_str = str(entry.get("contract_path", "")).strip()
        if not scenario_id or not contract_path_str:
            warnings.append("Scenario manifest entries must define id and contract_path.")
            all_valid = False
            continue
        rel_path = Path(contract_path_str)
        path = repo_root / rel_path
        if not path.exists():
            file_checks.append(
                ContractFileCheck(
                    rel_path=rel_path.as_posix(),
                    exists=False,
                    kind="scenario_contract",
                    messages=[f"Missing repo-level scenario contract for '{scenario_id}'."],
                )
            )
            missing.append(rel_path.as_posix())
            repo_missing.append(rel_path.as_posix())
            all_valid = False
            continue
        payload = load_orchestrate_json_file(path)
        messages = validate_orchestrate_scenario_contract(payload, source=rel_path.as_posix())
        if str(payload.get("id", "")).strip() != scenario_id:
            messages.append(f"{rel_path.as_posix()} id does not match manifest entry '{scenario_id}'.")
        warnings.extend(messages)
        if messages:
            all_valid = False
        file_checks.append(
            ContractFileCheck(
                rel_path=rel_path.as_posix(),
                exists=True,
                kind="scenario_contract",
                messages=messages,
            )
        )
    return all_valid


def _manifest_entries(
    payload: Dict[str, Any],
    key: str,
    warnings: List[str],
    file_checks: List[ContractFileCheck],
    rel_path: Path,
) -> List[Dict[str, Any]]:
    entries, messages = validate_orchestrate_manifest(
        payload,
        collection_key=key,
        source=rel_path.as_posix(),
    )
    warnings.extend(messages)
    for item in file_checks:
        if item.rel_path == rel_path.as_posix():
            item.messages.extend(messages)
            break
    return entries


def _load_ticket_key(intent_path: Path) -> str:
    if not intent_path.exists():
        return ""
    try:
        payload = load_orchestrate_json_file(intent_path)
    except PlatformError:
        return ""
    return str(payload.get("ticket_key", "")).strip()


def _load_active_run_key(branch_state_path: Path) -> str:
    if not branch_state_path.exists():
        return ""
    try:
        payload = load_orchestrate_json_file(branch_state_path)
    except PlatformError:
        return ""
    return str(payload.get("active_run_key", "")).strip()


def _branch_runtime_missing(runtime_root: Path, active_run_key: str) -> List[str]:
    missing: List[str] = []
    required = [
        Path("poa.md"),
        Path("branch_state.json"),
        Path("handoff.md"),
    ]
    for rel_path in required:
        if not (runtime_root / rel_path).exists():
            missing.append(rel_path)
    if not active_run_key:
        missing.append(Path("runs/<active-run-key>/run_state.json"))
        missing.append(Path("runs/<active-run-key>/stage_status.json"))
        missing.append(Path("runs/<active-run-key>/decisions.json"))
        missing.append(Path("runs/<active-run-key>/resume_context.md"))
        return missing

    run_root = runtime_root / "runs" / active_run_key
    for rel_path in (
        Path("run_state.json"),
        Path("stage_status.json"),
        Path("decisions.json"),
        Path("resume_context.md"),
    ):
        if not (run_root / rel_path).exists():
            missing.append(Path("runs") / active_run_key / rel_path)
    return missing


def _load_merge_hygiene_receipt(repo_root: Path, branch_slug: str, merge_hygiene_payload: Dict[str, Any]) -> Dict[str, Any]:
    retained_memory = merge_hygiene_payload.get("retained_memory", {}) if isinstance(merge_hygiene_payload, dict) else {}
    shared_dir = str(retained_memory.get("shared_closeout_dir", "")).strip() if isinstance(retained_memory, dict) else ""
    if not shared_dir:
        return {}
    receipt_path = repo_root / Path(shared_dir) / f"{branch_slug}.json"
    if not receipt_path.exists():
        return {}
    try:
        payload = load_orchestrate_json_file(receipt_path)
    except PlatformError:
        return {}
    if str(payload.get("branch_slug", "")).strip() != branch_slug:
        return {}
    if not bool(payload.get("runtime_pruned", False)):
        return {}
    return payload
