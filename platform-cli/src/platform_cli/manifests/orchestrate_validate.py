# NOTE: Architectural rules in ARCHITECTURE.md - do not refactor cross-layer.
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from platform_cli.core.errors import PlatformError


def validate_orchestrate_manifest(
    payload: Dict[str, Any],
    *,
    collection_key: str,
    source: str,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    messages: List[str] = []

    if not str(payload.get("schema_version", "")).strip():
        messages.append("schema_version is missing.")

    raw = payload.get(collection_key, [])
    if not isinstance(raw, list):
        messages.append(f"{source} field '{collection_key}' is not a list.")
        return [], messages

    entries = [item for item in raw if isinstance(item, dict)]
    if len(entries) != len(raw):
        messages.append(f"{source} contains non-object entries under '{collection_key}'.")
    if not entries:
        messages.append(f"{source} does not define any '{collection_key}'.")

    return entries, messages


def validate_orchestrate_agent_contract(
    payload: Dict[str, Any],
    *,
    source: str,
) -> List[str]:
    messages: List[str] = []

    if not str(payload.get("schema_version", "")).strip():
        messages.append(f"{source} is missing schema_version.")
    if not str(payload.get("id", "")).strip():
        messages.append(f"{source} is missing id.")
    if not str(payload.get("role", "")).strip():
        messages.append(f"{source} is missing role.")

    for key in ("stages_owned", "allowed_skills", "allowed_plugins", "produces_artifacts", "prompt_contract"):
        value = payload.get(key)
        if not isinstance(value, list):
            messages.append(f"{source} field '{key}' is not a list.")
            continue
        if any(not str(item).strip() for item in value):
            messages.append(f"{source} field '{key}' contains empty values.")

    for key in ("can_block", "can_retry"):
        if not isinstance(payload.get(key, None), bool):
            messages.append(f"{source} field '{key}' is not a boolean.")

    if not str(payload.get("approval_mode", "")).strip():
        messages.append(f"{source} is missing approval_mode.")

    return messages


def validate_orchestrate_stage_contract(
    payload: Dict[str, Any],
    *,
    source: str,
) -> List[str]:
    messages: List[str] = []

    if not str(payload.get("schema_version", "")).strip():
        messages.append(f"{source} is missing schema_version.")
    if not str(payload.get("id", "")).strip():
        messages.append(f"{source} is missing id.")
    if not str(payload.get("title", "")).strip():
        messages.append(f"{source} is missing title.")
    if not str(payload.get("owner_agent", "")).strip():
        messages.append(f"{source} is missing owner_agent.")

    for key in ("messages", "next_actions", "handoff_summaries"):
        value = payload.get(key)
        if value is None:
            continue
        if not isinstance(value, dict):
            messages.append(f"{source} field '{key}' is not an object.")
            continue
        if any(not str(item_key).strip() or not str(item_value).strip() for item_key, item_value in value.items()):
            messages.append(f"{source} field '{key}' contains empty keys or values.")

    for key in (
        "resume_note_templates",
        "delivery_posture",
        "summary_ready_inputs",
        "scenario_design_posture",
        "watchpoints",
    ):
        value = payload.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            messages.append(f"{source} field '{key}' is not a list.")
            continue
        if any(not str(item).strip() for item in value):
            messages.append(f"{source} field '{key}' contains empty values.")

    for key in ("summary_expected_next_step", "stage_status_summary_template"):
        value = payload.get(key)
        if value is None:
            continue
        if not str(value).strip():
            messages.append(f"{source} field '{key}' is empty.")

    return messages


def validate_orchestrate_plugin_contract(
    payload: Dict[str, Any],
    *,
    source: str,
) -> List[str]:
    messages: List[str] = []
    if not str(payload.get("schema_version", "")).strip():
        messages.append(f"{source} is missing schema_version.")
    if not str(payload.get("id", "")).strip():
        messages.append(f"{source} is missing id.")
    if not str(payload.get("executor", "")).strip():
        messages.append(f"{source} is missing executor.")
    setup_contract = payload.get("setup_contract", [])
    if not isinstance(setup_contract, list) or any(not str(item).strip() for item in setup_contract):
        messages.append(f"{source} field 'setup_contract' must be a non-empty list of strings.")
    if not isinstance(payload.get("login_required", None), bool):
        messages.append(f"{source} field 'login_required' is not a boolean.")
    model = payload.get("model")
    if model is not None and not str(model).strip():
        messages.append(f"{source} field 'model' is empty.")
    return messages


def validate_orchestrate_kernel_contract(
    payload: Dict[str, Any],
    *,
    source: str,
) -> List[str]:
    messages: List[str] = []
    if not str(payload.get("schema_version", "")).strip():
        messages.append(f"{source} is missing schema_version.")
    if not str(payload.get("id", "")).strip():
        messages.append(f"{source} is missing id.")
    execution_kernel = payload.get("execution_kernel", {})
    if not isinstance(execution_kernel, dict):
        messages.append(f"{source} field 'execution_kernel' is not an object.")
    else:
        if not str(execution_kernel.get("type", "")).strip():
            messages.append(f"{source} execution_kernel.type is missing.")
        host_entrypoints = execution_kernel.get("host_entrypoints", [])
        if not isinstance(host_entrypoints, list) or any(not str(item).strip() for item in host_entrypoints):
            messages.append(f"{source} execution_kernel.host_entrypoints must be a non-empty list of strings.")
    provider_resolution = payload.get("provider_resolution", {})
    if not isinstance(provider_resolution, dict):
        messages.append(f"{source} field 'provider_resolution' is not an object.")
    else:
        supported = provider_resolution.get("supported_provider_plugins", [])
        if not isinstance(supported, list) or any(not str(item).strip() for item in supported):
            messages.append(f"{source} provider_resolution.supported_provider_plugins must be a non-empty list of strings.")
    return messages


def validate_orchestrate_topology_contract(
    payload: Dict[str, Any],
    *,
    source: str,
) -> List[str]:
    messages: List[str] = []
    if not str(payload.get("schema_version", "")).strip():
        messages.append(f"{source} is missing schema_version.")
    if not str(payload.get("id", "")).strip():
        messages.append(f"{source} is missing id.")
    if str(payload.get("default_execution_mode", "")).strip() not in {"sequential", "parallel", "auto"}:
        messages.append(f"{source} default_execution_mode must be sequential, parallel, or auto.")
    for key in ("parallel_groups", "sequential_groups", "dependencies"):
        value = payload.get(key)
        if not isinstance(value, list):
            messages.append(f"{source} field '{key}' is not a list.")
    execution_waves = payload.get("execution_waves")
    if execution_waves is not None and not isinstance(execution_waves, list):
        messages.append(f"{source} field 'execution_waves' is not a list.")
    host_preferences = payload.get("host_preferences", {})
    if not isinstance(host_preferences, dict):
        messages.append(f"{source} field 'host_preferences' is not an object.")
    return messages


def validate_orchestrate_scenario_contract(
    payload: Dict[str, Any],
    *,
    source: str,
) -> List[str]:
    messages: List[str] = []
    if not str(payload.get("schema_version", "")).strip():
        messages.append(f"{source} is missing schema_version.")
    if not str(payload.get("id", "")).strip():
        messages.append(f"{source} is missing id.")
    for key in ("title", "goal", "host_mode", "provider_plugin"):
        if not str(payload.get(key, "")).strip():
            messages.append(f"{source} field '{key}' is missing.")
    for key in ("requested_agents", "prompt_brief", "expected_artifacts"):
        value = payload.get(key, [])
        if not isinstance(value, list) or any(not str(item).strip() for item in value):
            messages.append(f"{source} field '{key}' must be a non-empty list of strings.")
    return messages


def validate_orchestrate_merge_hygiene_config(
    payload: Dict[str, Any],
    *,
    source: str,
) -> List[str]:
    messages: List[str] = []
    if not str(payload.get("schema_version", "")).strip():
        messages.append(f"{source} is missing schema_version.")
    if not str(payload.get("id", "")).strip():
        messages.append(f"{source} is missing id.")

    retained_memory = payload.get("retained_memory", {})
    if not isinstance(retained_memory, dict):
        messages.append(f"{source} field 'retained_memory' is not an object.")
    elif not str(retained_memory.get("shared_closeout_dir", "")).strip():
        messages.append(f"{source} retained_memory.shared_closeout_dir is missing.")

    archive = payload.get("archive", {})
    if not isinstance(archive, dict):
        messages.append(f"{source} field 'archive' is not an object.")
    else:
        destination_mode = str(archive.get("destination_mode", "")).strip()
        if destination_mode not in {"local", "aws_s3"}:
            messages.append(f"{source} archive.destination_mode must be local or aws_s3.")
        local = archive.get("local", {})
        if not isinstance(local, dict):
            messages.append(f"{source} archive.local is not an object.")
        else:
            if not str(local.get("output_dir", "")).strip():
                messages.append(f"{source} archive.local.output_dir is missing.")
            retention_days = local.get("retention_days", 0)
            if not isinstance(retention_days, int) or retention_days < 1:
                messages.append(f"{source} archive.local.retention_days must be an integer >= 1.")

    merge_blockers = payload.get("merge_blockers", {})
    if not isinstance(merge_blockers, dict):
        messages.append(f"{source} field 'merge_blockers' is not an object.")
    else:
        for key in (
            "require_stage22_closeout",
            "block_active_runtime_state",
            "require_promoted_memory_receipt",
        ):
            if not isinstance(merge_blockers.get(key, None), bool):
                messages.append(f"{source} merge_blockers.{key} must be a boolean.")
    return messages


def validate_orchestrate_policy(policy: Dict[str, Any]) -> None:
    schema_version = str(policy.get("schema_version", "")).strip()
    if not schema_version:
        raise PlatformError(
            "orchestrate policy schema_version is missing.",
            code="E_ORCHESTRATE_POLICY_INVALID",
            reason="schema_version",
        )

    runtime = policy.get("runtime", {})
    if not isinstance(runtime, dict):
        raise PlatformError(
            "orchestrate policy runtime must be an object.",
            code="E_ORCHESTRATE_POLICY_INVALID",
            reason="runtime",
        )
    execution_mode = str(runtime.get("default_execution_mode", "")).strip()
    if execution_mode not in {"auto", "sequential", "parallel"}:
        raise PlatformError(
            "orchestrate policy runtime.default_execution_mode must be auto, sequential, or parallel.",
            code="E_ORCHESTRATE_POLICY_INVALID",
            reason="runtime.default_execution_mode",
        )
    provider_mode = str(runtime.get("default_provider_mode", "")).strip()
    if provider_mode not in {"auto", "codex", "claude", "mixed"}:
        raise PlatformError(
            "orchestrate policy runtime.default_provider_mode must be auto, codex, claude, or mixed.",
            code="E_ORCHESTRATE_POLICY_INVALID",
            reason="runtime.default_provider_mode",
        )

    front_door = policy.get("front_door", {})
    if not isinstance(front_door, dict):
        raise PlatformError(
            "orchestrate policy front_door must be an object.",
            code="E_ORCHESTRATE_POLICY_INVALID",
            reason="front_door",
        )
    minimum_intake_confidence = front_door.get("minimum_intake_confidence", 0.7)
    semi_autonomous_confidence_threshold = front_door.get("semi_autonomous_confidence_threshold", 0.78)
    autonomous_confidence_threshold = front_door.get("autonomous_confidence_threshold", 0.92)
    for key, value in (
        ("front_door.minimum_intake_confidence", minimum_intake_confidence),
        ("front_door.semi_autonomous_confidence_threshold", semi_autonomous_confidence_threshold),
        ("front_door.autonomous_confidence_threshold", autonomous_confidence_threshold),
    ):
        if not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
            raise PlatformError(
                f"orchestrate policy {key} must be a number between 0 and 1.",
                code="E_ORCHESTRATE_POLICY_INVALID",
                reason=key,
            )
    if float(semi_autonomous_confidence_threshold) < float(minimum_intake_confidence):
        raise PlatformError(
            "orchestrate policy front_door.semi_autonomous_confidence_threshold must be greater than or equal to front_door.minimum_intake_confidence.",
            code="E_ORCHESTRATE_POLICY_INVALID",
            reason="front_door.semi_autonomous_confidence_threshold",
        )
    if float(autonomous_confidence_threshold) < float(semi_autonomous_confidence_threshold):
        raise PlatformError(
            "orchestrate policy front_door.autonomous_confidence_threshold must be greater than or equal to front_door.semi_autonomous_confidence_threshold.",
            code="E_ORCHESTRATE_POLICY_INVALID",
            reason="front_door.autonomous_confidence_threshold",
        )

    branch = policy.get("branch", {})
    if not isinstance(branch, dict):
        raise PlatformError(
            "orchestrate policy branch must be an object.",
            code="E_ORCHESTRATE_POLICY_INVALID",
            reason="branch",
        )
    if not isinstance(branch.get("single_active_run_per_branch", None), bool):
        raise PlatformError(
            "orchestrate policy branch.single_active_run_per_branch must be a boolean.",
            code="E_ORCHESTRATE_POLICY_INVALID",
            reason="branch.single_active_run_per_branch",
        )
