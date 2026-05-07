from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from platform_cli.core.errors import PlatformError
from platform_cli.core.repo_roots import resolve_repo_root
from platform_cli.manifests.orchestrate_load import load_orchestrate_json_file
from platform_cli.manifests.orchestrate_policy_load import load_orchestrate_policy
from platform_cli.manifests.orchestrate_stage_load import load_stage_contract
from platform_cli.manifests.orchestrate_validate import validate_orchestrate_policy
from platform_cli.tools.orchestrate_contract import runtime_branch_folder_name, slugify_branch_name
from platform_cli.tools.repo_ready_generation import current_branch_name


_BRANCHES_ROOT = Path(".ghdp/orchestrate/branches")
_INTENT_PATH = Path(".ghdp/frbr/intent.json")
_PHASE_POLICY_PATH = Path(".ghdp/orchestrate/phases.json")
_STAGE_FRONT_DOOR = "stage_c_front_door_gates"
_DEFAULT_CLARIFICATION_QUESTIONS = (
    "What exact user-visible outcome must this run produce?",
    "Which acceptance criteria are non-negotiable for this branch?",
    "Are there any sync-managed, release-managed, or policy-owned surfaces this change must avoid or include?",
)
_STAGE_FRONT_DOOR_BEGIN = "<!-- GHDP:BEGIN STAGE_C_FRONT_DOOR -->"
_STAGE_FRONT_DOOR_END = "<!-- GHDP:END STAGE_C_FRONT_DOOR -->"
_STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "through",
    "under",
    "about",
    "have",
    "will",
    "they",
    "them",
    "their",
    "then",
    "than",
    "must",
    "should",
    "could",
    "would",
    "does",
    "done",
    "work",
    "phase",
    "agentic",
}


@dataclass
class OrchestrateFrontDoorResult:
    repo_root: str
    branch_name: str
    branch_slug: str
    ticket_key: str
    active_run_key: str
    action: str
    status: str
    current_stage: str
    next_action: str
    branch_runtime_root: str
    policy_source: str
    work_type: str
    autonomy_level: str
    autonomy_confidence: float
    intake_sufficient: bool
    intake_confidence: float
    spec_action: str
    delivery_route: str
    asset_operation: str
    phase_mode: str
    phase_count: int
    restart_recommendation: str
    parallel_work_decision: str
    capability_matches: List[str]
    impacted_areas: List[str]
    clarification_questions: List[str]
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_front_door_gates(*, repo_root: Path | None = None) -> OrchestrateFrontDoorResult:
    resolved_root = resolve_repo_root(repo_root)
    policy, policy_source = _load_policy()
    branch_name = current_branch_name(resolved_root)
    if not branch_name:
        raise PlatformError(
            "Could not resolve the current branch for front-door orchestration.",
            code="E_ORCHESTRATE_BRANCH_UNRESOLVED",
            reason="current_branch",
        )

    runtime_root = resolved_root / _BRANCHES_ROOT / runtime_branch_folder_name(resolved_root, branch_name)
    branch_state_path = runtime_root / "branch_state.json"
    if not branch_state_path.exists():
        raise PlatformError(
            "No branch runtime state exists yet. Start the orchestrator before running the front-door gates.",
            code="E_ORCHESTRATE_RUN_MISSING",
            reason=str(branch_state_path),
        )

    branch_state = load_orchestrate_json_file(branch_state_path)
    stage_contract = load_stage_contract(stage_id=_STAGE_FRONT_DOOR, repo_root=resolved_root)
    active_run_key = str(branch_state.get("active_run_key", "")).strip()
    if not active_run_key:
        raise PlatformError(
            "Branch runtime state does not contain an active run key.",
            code="E_ORCHESTRATE_RUN_MISSING",
            reason="active_run_key",
        )

    intent_payload = _load_intent_payload(resolved_root)
    front_door_policy = _load_front_door_policy(policy)
    intake = _assess_intake(intent_payload, minimum_confidence=front_door_policy["minimum_intake_confidence"])
    work_type = _classify_work_type(intent_payload)
    autonomy = _assess_autonomy(
        intent_payload=intent_payload,
        work_type=work_type,
        intake_confidence=intake["confidence"],
        front_door_policy=front_door_policy,
    )
    capability_matches, impacted_areas = _discover_context_and_capabilities(resolved_root, intent_payload)
    asset_lifecycle = _detect_asset_lifecycle(intent_payload)
    phase_policy = _load_phase_policy(resolved_root)
    phase_plan = _plan_phase_regroup(
        intent_payload=intent_payload,
        work_type=work_type,
        capability_matches=capability_matches,
        impacted_areas=impacted_areas,
        asset_lifecycle=asset_lifecycle,
        phase_policy=phase_policy,
    )
    parallel_work = _assess_parallel_work(resolved_root, branch_name)
    spec_action = _decide_spec_action(work_type, asset_lifecycle=asset_lifecycle)

    gate_status = "completed"
    gate_recipe_key = "completed"
    branch_status = "paused"
    next_action = _stage_text(stage_contract, "next_actions", "completed")
    message = _stage_text(stage_contract, "messages", "completed")

    if not intake["sufficient"]:
        gate_status = "needs_clarification"
        gate_recipe_key = "intake_insufficient"
        branch_status = "blocked"
        next_action = _stage_text(stage_contract, "next_actions", gate_recipe_key)
        message = _stage_text(stage_contract, "messages", gate_recipe_key)
    elif autonomy["level"] == "human_clarification_required":
        gate_status = "needs_clarification"
        gate_recipe_key = "autonomy_blocked"
        branch_status = "blocked"
        next_action = _stage_text(stage_contract, "next_actions", gate_recipe_key)
        message = _stage_text(stage_contract, "messages", gate_recipe_key)
    elif parallel_work["decision"] == "defer":
        gate_status = "blocked"
        gate_recipe_key = "parallel_blocked"
        branch_status = "blocked"
        next_action = _stage_text(stage_contract, "next_actions", gate_recipe_key)
        message = _stage_text(stage_contract, "messages", gate_recipe_key)
    elif asset_lifecycle["route"] == "asset_only":
        gate_status = "completed"
        gate_recipe_key = "asset_only"
        branch_status = "paused"
        next_action = _stage_text(stage_contract, "next_actions", gate_recipe_key) or "Run the independent asset lifecycle path for the requested asset operation."
        message = _stage_text(stage_contract, "messages", gate_recipe_key) or "Front-door gates detected an asset-only request and routed it to the lightweight asset lifecycle path."

    branch_state["status"] = branch_status
    branch_state["current_stage"] = _STAGE_FRONT_DOOR
    branch_state["next_action"] = next_action
    branch_state["anomaly_flag"] = gate_status != "completed"
    branch_state["last_updated_at"] = _iso_now()
    branch_state["last_updated_by"] = "codex"
    _write_json(branch_state_path, branch_state)

    run_root = runtime_root / "runs" / active_run_key
    _upsert_stage_status(
        run_root / "stage_status.json",
        stage_name=_STAGE_FRONT_DOOR,
        status=gate_status,
        owner_agent="orchestrator",
        summary=_render_template(
            str(stage_contract.get("stage_status_summary_template", "")).strip()
            or "Front-door gates classified this run as {work_type} with {autonomy_level} autonomy and a parallel-work decision of {parallel_work_decision}.",
            work_type=work_type,
            autonomy_level=autonomy["level"],
            parallel_work_decision=parallel_work["decision"],
        ),
        artifacts=["poa.md", "decisions.json", "resume_context.md"],
    )
    _finalize_prerequisite_stages(run_root / "stage_status.json")
    _upsert_decisions(
        run_root / "decisions.json",
        decisions=[
            {
                "id": "front_door_intake",
                "decision": f"Input sufficiency confidence is {intake['confidence']:.2f}.",
                "status": "accepted" if intake["sufficient"] else "needs_clarification",
                "source": "stage_c_front_door",
            },
            {
                "id": "front_door_work_type",
                "decision": f"Classify this run as {work_type}.",
                "status": "accepted",
                "source": "stage_c_front_door",
            },
            {
                "id": "front_door_autonomy",
                "decision": f"Proceed with {autonomy['level']} autonomy.",
                "status": "accepted" if autonomy["level"] != "human_clarification_required" else "needs_clarification",
                "source": "stage_c_front_door",
            },
            {
                "id": "front_door_parallel_work",
                "decision": f"Parallel-work decision: {parallel_work['decision']}.",
                "status": "accepted" if parallel_work["decision"] != "defer" else "blocked",
                "source": "stage_c_front_door",
            },
            {
                "id": "front_door_spec_action",
                "decision": f"Use spec action {spec_action}.",
                "status": "accepted",
                "source": "stage_c_front_door",
            },
            {
                "id": "front_door_delivery_route",
                "decision": f"Delivery route: {asset_lifecycle['route']} with asset operation {asset_lifecycle['operation']}.",
                "status": "accepted",
                "source": "stage_c_front_door",
            },
            {
                "id": "front_door_phase_plan",
                "decision": f"Phase mode: {phase_plan['phase_mode']} with {phase_plan['phase_count']} phase(s). Restart recommendation: {phase_plan['restart_recommendation']}.",
                "status": "accepted",
                "source": "stage_c_front_door",
            },
        ],
    )
    _write_json(
        run_root / "phase_plan.json",
        {
            "schema_version": "1.0",
            "phase_mode": phase_plan["phase_mode"],
            "phase_count": phase_plan["phase_count"],
            "restart_recommendation": phase_plan["restart_recommendation"],
            "restart_trigger": phase_plan["restart_trigger"],
            "reason": phase_plan["reason"],
            "impacted_area_count": len(impacted_areas),
            "capability_count": len(capability_matches),
            "delivery_route": asset_lifecycle["route"],
        },
    )
    write_phase_regroup_summary = [
        "# Phase Regroup Summary",
        "",
        f"- Phase mode: `{phase_plan['phase_mode']}`",
        f"- Phase count: `{phase_plan['phase_count']}`",
        f"- Restart recommendation: `{phase_plan['restart_recommendation']}`",
        f"- Restart trigger: `{phase_plan['restart_trigger']}`",
        f"- Reason: {phase_plan['reason']}",
    ]
    _write_markdown(run_root / "phase_regroup_summary.md", write_phase_regroup_summary)
    _update_poa(
        runtime_root / "poa.md",
        stage_contract=stage_contract,
        ticket_key=str(intent_payload.get("ticket_key", "")).strip() or str(branch_state.get("ticket_key", "")).strip(),
        branch_name=branch_name,
        work_type=work_type,
        intake=intake,
        autonomy=autonomy,
        spec_action=spec_action,
        delivery_route=asset_lifecycle["route"],
        asset_operation=asset_lifecycle["operation"],
        phase_plan=phase_plan,
        capability_matches=capability_matches,
        impacted_areas=impacted_areas,
        parallel_work=parallel_work,
    )
    _write_handoff(
        runtime_root / "handoff.md",
        summary=message,
        next_action=next_action,
        status=branch_status,
        at=_iso_now(),
    )
    _write_resume_context(
        run_root / "resume_context.md",
        active_run_key=active_run_key,
        current_stage=_STAGE_FRONT_DOOR,
        next_action=next_action,
        notes=_render_templates(
            stage_contract.get("resume_note_templates", []),
            intake_confidence=intake["confidence"],
            intake_state="sufficient" if intake["sufficient"] else "needs clarification",
            work_type=work_type,
            autonomy_level=autonomy["level"],
            autonomy_confidence=autonomy["confidence"],
            parallel_work_decision=parallel_work["decision"],
            delivery_route=asset_lifecycle["route"],
            asset_operation=asset_lifecycle["operation"],
            phase_mode=phase_plan["phase_mode"],
            phase_count=phase_plan["phase_count"],
            restart_recommendation=phase_plan["restart_recommendation"],
        ),
    )

    return OrchestrateFrontDoorResult(
        repo_root=str(resolved_root),
        branch_name=branch_name,
        branch_slug=slugify_branch_name(branch_name),
        ticket_key=str(intent_payload.get("ticket_key", "")).strip() or str(branch_state.get("ticket_key", "")).strip(),
        active_run_key=active_run_key,
        action="front_door",
        status=branch_status,
        current_stage=_STAGE_FRONT_DOOR,
        next_action=next_action,
        branch_runtime_root=str(runtime_root),
        policy_source=policy_source,
        work_type=work_type,
        autonomy_level=autonomy["level"],
        autonomy_confidence=autonomy["confidence"],
        intake_sufficient=intake["sufficient"],
        intake_confidence=intake["confidence"],
        spec_action=spec_action,
        delivery_route=asset_lifecycle["route"],
        asset_operation=asset_lifecycle["operation"],
        phase_mode=phase_plan["phase_mode"],
        phase_count=phase_plan["phase_count"],
        restart_recommendation=phase_plan["restart_recommendation"],
        parallel_work_decision=parallel_work["decision"],
        capability_matches=capability_matches,
        impacted_areas=impacted_areas,
        clarification_questions=list(intake["clarification_questions"]),
        message=message,
    )


def _load_policy() -> Tuple[Dict[str, Any], str]:
    policy, source = load_orchestrate_policy()
    validate_orchestrate_policy(policy)
    return policy, source


def _load_front_door_policy(policy: Dict[str, Any]) -> Dict[str, float]:
    front_door = policy.get("front_door", {})
    if not isinstance(front_door, dict):
        return {
            "minimum_intake_confidence": 0.70,
            "semi_autonomous_confidence_threshold": 0.78,
            "autonomous_confidence_threshold": 0.92,
        }
    return {
        "minimum_intake_confidence": float(front_door.get("minimum_intake_confidence", 0.70)),
        "semi_autonomous_confidence_threshold": float(front_door.get("semi_autonomous_confidence_threshold", 0.78)),
        "autonomous_confidence_threshold": float(front_door.get("autonomous_confidence_threshold", 0.92)),
    }


def _load_intent_payload(repo_root: Path) -> Dict[str, Any]:
    intent_path = repo_root / _INTENT_PATH
    if not intent_path.exists():
        raise PlatformError(
            "Intent file is missing for the current branch. Refresh the branch intent before running the front-door gates.",
            code="E_ORCHESTRATE_INTENT_MISSING",
            reason=str(intent_path),
        )
    return load_orchestrate_json_file(intent_path)


def _load_phase_policy(repo_root: Path) -> Dict[str, Any]:
    path = repo_root / _PHASE_POLICY_PATH
    if not path.exists():
        return {
            "single_phase_default": True,
            "max_impacted_areas_per_phase": 5,
            "max_capabilities_per_phase": 4,
            "force_multi_phase_keywords": [],
            "restart_destinations": {},
        }
    return load_orchestrate_json_file(path)


def _assess_intake(intent_payload: Dict[str, Any], *, minimum_confidence: float) -> Dict[str, Any]:
    summary = str(intent_payload.get("summary", "")).strip()
    intent = str(intent_payload.get("intent", "")).strip()
    text = f"{summary}\n{intent}".strip()
    hits = 0
    if str(intent_payload.get("ticket_key", "")).strip():
        hits += 1
    if summary:
        hits += 2
    if intent:
        hits += 2
    lowered = text.lower()
    if "acceptance criteria" in lowered:
        hits += 2
    if "scope" in lowered:
        hits += 1
    if "out of scope" in lowered:
        hits += 1

    confidence = min(0.99, hits / 8)
    clarification_questions: List[str] = []
    if not summary:
        clarification_questions.append("What short summary should describe the change in one sentence?")
    if "acceptance criteria" not in lowered:
        clarification_questions.append("Which acceptance criteria should the orchestrator treat as the definition of done?")
    if "scope" not in lowered:
        clarification_questions.append("What exact in-scope surfaces should this branch change first?")
    if not clarification_questions and confidence < minimum_confidence:
        clarification_questions.extend(_DEFAULT_CLARIFICATION_QUESTIONS)

    return {
        "sufficient": confidence >= minimum_confidence and not clarification_questions,
        "confidence": round(confidence, 2),
        "clarification_questions": clarification_questions,
    }


def _classify_work_type(intent_payload: Dict[str, Any]) -> str:
    text = f"{intent_payload.get('summary', '')}\n{intent_payload.get('intent', '')}".lower()
    if any(token in text for token in ("bug", "fix", "broken", "regression", "failure")):
        return "bug_fix"
    if any(token in text for token in ("cleanup", "maintain", "maintenance", "stale", "remove dead")):
        return "maintenance"
    if any(token in text for token in ("improve", "extend", "enhance", "refine", "existing")):
        return "enhancement"
    return "new_feature"


def _detect_asset_lifecycle(intent_payload: Dict[str, Any]) -> Dict[str, str]:
    text = f"{intent_payload.get('summary', '')}\n{intent_payload.get('intent', '')}".lower()
    asset_terms = (
        "asset",
        "manifest",
        "content-index",
        "content index",
        "toolset",
        "allowlist",
        "marketplace",
        "capability entry",
        "version requirement",
        "release asset",
        "team toolset",
    )
    broader_sdlc_terms = (
        "command",
        "runtime",
        "workflow",
        "test",
        "implementation",
        "feature",
        "bug",
        "refactor",
        "stage",
        "agent",
        "orchestrator",
    )
    asset_hits = sum(1 for term in asset_terms if term in text)
    broader_hits = sum(1 for term in broader_sdlc_terms if term in text)
    if any(term in text for term in ("remove asset", "retire asset", "delete asset")):
        operation = "remove"
    elif any(term in text for term in ("create asset", "new asset", "add capability asset")):
        operation = "create"
    elif any(term in text for term in ("version bump", "bump version", "minimum version", "version requirement", "update version")):
        operation = "update_versioned_asset"
    elif asset_hits:
        operation = "revise"
    else:
        operation = "none"

    asset_only_hint = any(term in text for term in ("only update", "just update", "simple update", "asset only"))
    if asset_hits >= 2 and (asset_only_hint or broader_hits <= 2):
        route = "asset_only"
    elif asset_hits >= 2:
        route = "asset_with_sdlc"
    else:
        route = "full_sdlc"
    return {"route": route, "operation": operation}


def _plan_phase_regroup(
    *,
    intent_payload: Dict[str, Any],
    work_type: str,
    capability_matches: Sequence[str],
    impacted_areas: Sequence[str],
    asset_lifecycle: Dict[str, str],
    phase_policy: Dict[str, Any],
) -> Dict[str, Any]:
    text = f"{intent_payload.get('summary', '')}\n{intent_payload.get('intent', '')}".lower()
    force_keywords = [str(item).strip().lower() for item in phase_policy.get("force_multi_phase_keywords", []) if str(item).strip()]
    keyword_hit = next((item for item in force_keywords if item and item in text), "")
    capability_count = len(capability_matches)
    impacted_area_count = len(impacted_areas)
    max_impacted = int(phase_policy.get("max_impacted_areas_per_phase", 5) or 5)
    max_capabilities = int(phase_policy.get("max_capabilities_per_phase", 4) or 4)
    restart_triggers = phase_policy.get("restart_triggers", {})
    restart_destinations = phase_policy.get("restart_destinations", {})

    phase_mode = "single_phase"
    phase_count = 1
    restart_trigger = "none"
    restart_recommendation = "continue_current_phase"
    reasons: List[str] = []

    if keyword_hit:
        phase_mode = "multi_phase"
        phase_count = max(2, min(4, (capability_count // max(max_capabilities, 1)) + 1))
        reasons.append(f"Intent explicitly references phased delivery via keyword '{keyword_hit}'.")
    if impacted_area_count > max_impacted:
        phase_mode = "multi_phase"
        phase_count = max(phase_count, min(4, (impacted_area_count // max(max_impacted, 1)) + 1))
        if bool(restart_triggers.get("too_many_impacted_areas", False)):
            restart_trigger = "too_many_impacted_areas"
            restart_recommendation = str(restart_destinations.get(restart_trigger, "stage_c_front_door_gates")).strip() or "stage_c_front_door_gates"
        reasons.append(
            f"Impacted areas exceeded the single-phase threshold ({impacted_area_count} > {max_impacted})."
        )
    if capability_count > max_capabilities:
        phase_mode = "multi_phase"
        phase_count = max(phase_count, min(4, (capability_count // max(max_capabilities, 1)) + 1))
        if bool(restart_triggers.get("too_many_asset_targets", False)):
            restart_trigger = "too_many_asset_targets"
            restart_recommendation = str(restart_destinations.get(restart_trigger, "independent_asset_lifecycle")).strip() or "independent_asset_lifecycle"
        reasons.append(
            f"Capability count exceeded the single-phase threshold ({capability_count} > {max_capabilities})."
        )

    if asset_lifecycle.get("route") == "asset_only":
        restart_trigger = restart_trigger if restart_trigger != "none" else "asset_only"
        restart_recommendation = "independent_asset_lifecycle"
        reasons.append("The request can start on the lightweight asset lifecycle path.")
    elif not reasons:
        reasons.append(f"{work_type} work fits within the current single-phase thresholds.")

    return {
        "phase_mode": phase_mode,
        "phase_count": phase_count,
        "restart_recommendation": restart_recommendation,
        "restart_trigger": restart_trigger,
        "reason": " ".join(reasons).strip(),
    }


def _assess_autonomy(
    *,
    intent_payload: Dict[str, Any],
    work_type: str,
    intake_confidence: float,
    front_door_policy: Dict[str, float],
) -> Dict[str, Any]:
    text = f"{intent_payload.get('summary', '')}\n{intent_payload.get('intent', '')}".lower()
    complexity_terms = (
        "framework",
        "orchestrator",
        "architecture",
        "release",
        "plugin",
        "skill",
        "memory",
        "sub-agent",
        "cross-cutting",
        "integration",
    )
    complexity_hits = sum(1 for term in complexity_terms if term in text)
    confidence = intake_confidence
    if work_type == "new_feature":
        confidence = min(confidence, 0.86)
    if complexity_hits >= 4:
        confidence = min(confidence, 0.84)
    elif work_type in {"maintenance", "bug_fix"} and intake_confidence >= 0.9:
        confidence = max(confidence, 0.94)

    if intake_confidence < front_door_policy["minimum_intake_confidence"]:
        level = "human_clarification_required"
    elif confidence >= front_door_policy["autonomous_confidence_threshold"]:
        level = "autonomous"
    elif confidence >= front_door_policy["semi_autonomous_confidence_threshold"]:
        level = "semi_autonomous"
    else:
        level = "human_clarification_required"

    return {"level": level, "confidence": round(confidence, 2)}


def _discover_context_and_capabilities(repo_root: Path, intent_payload: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    text_tokens = _tokenize(f"{intent_payload.get('summary', '')}\n{intent_payload.get('intent', '')}")
    scored_matches: List[Tuple[int, str]] = []
    impacted_areas: List[str] = []

    for manifest_path, collection_key, label_key in (
        (repo_root / ".ghdp" / "agents" / "manifest.json", "agents", "id"),
        (repo_root / ".ghdp" / "skills" / "manifest.json", "skills", "id"),
        (repo_root / ".ghdp" / "plugins" / "manifest.json", "plugins", "id"),
    ):
        if not manifest_path.exists():
            continue
        payload = load_orchestrate_json_file(manifest_path)
        for item in payload.get(collection_key, []):
            if not isinstance(item, dict):
                continue
            haystack = " ".join(str(item.get(key, "")) for key in item.keys()).lower()
            candidate_id = str(item.get(label_key, "")).strip()
            candidate_tokens = _tokenize(haystack)
            score = len(text_tokens & candidate_tokens)
            if candidate_id and (score >= 2 or candidate_id.replace("-", " ") in haystack and any(token in text_tokens for token in _tokenize(candidate_id))):
                scored_matches.append((score, candidate_id))

    text = f"{intent_payload.get('summary', '')}\n{intent_payload.get('intent', '')}".lower()
    if any(token in text for token in ("orchestrator", "agent", "sub-agent", "skill", "plugin")):
        impacted_areas.extend(
            [
                ".ghdp/agents/manifest.json",
                ".ghdp/skills/manifest.json",
                ".ghdp/plugins/manifest.json",
                "platform-cli/src/platform_cli/commands/orchestrate.py",
                "platform-cli/src/platform_cli/tools/orchestrate_front_door.py",
                "platform-cli/src/platform_cli/tools/orchestrate_runtime.py",
            ]
        )
    if "memory" in text:
        impacted_areas.extend(
            [
                ".ghdp/memory/manifest.json",
                ".ghdp/memory/shared/README.md",
                ".ghdp/memory/context/README.md",
            ]
        )
    if "policy" in text or "autonomy" in text:
        impacted_areas.extend(
            [
                "platform-cli/src/platform_cli/resources/policy/orchestrate_policy.json",
                "platform-cli/src/platform_cli/manifests/orchestrate_validate.py",
            ]
        )
    if any(token in text for token in ("asset", "manifest", "content index", "content-index", "toolset", "allowlist", "marketplace", "version requirement")):
        impacted_areas.extend(
            [
                ".ghdp/skills/asset-capability-discovery/SKILL.md",
                ".ghdp/skills/asset-lifecycle-operations/SKILL.md",
                ".ghdp/plugins/asset-lifecycle-sync/plugin.json",
                "platform-cli/src/platform_cli/core/release_content.py",
                "platform-cli/src/platform_cli/core/sync_providers.py",
                "platform-cli/src/platform_cli/resources/manifests/toolset.json",
                "platform-cli/release-assets/team_toolset/toolset.json",
            ]
        )

    capability_matches = [
        item
        for item in dict.fromkeys(
            candidate_id for _, candidate_id in sorted(scored_matches, key=lambda item: (-item[0], item[1]))
        )
    ][:12]
    impacted_areas = sorted(dict.fromkeys(impacted_areas))
    return capability_matches, impacted_areas


def _assess_parallel_work(repo_root: Path, branch_name: str) -> Dict[str, Any]:
    branches_root = repo_root / _BRANCHES_ROOT
    current_slug = slugify_branch_name(branch_name)
    active_others: List[str] = []
    if branches_root.exists():
        for branch_root in branches_root.iterdir():
            if not branch_root.is_dir() or branch_root.name == current_slug:
                continue
            branch_state_path = branch_root / "branch_state.json"
            if not branch_state_path.exists():
                continue
            state = load_orchestrate_json_file(branch_state_path)
            status = str(state.get("status", "")).strip()
            if status in {"in_progress", "paused"}:
                active_others.append(str(state.get("branch_name", branch_root.name)).strip())

    if active_others:
        return {
            "decision": "proceed_with_warning",
            "related_branches": active_others,
        }
    return {"decision": "proceed", "related_branches": []}


def _decide_spec_action(work_type: str, *, asset_lifecycle: Dict[str, str]) -> str:
    if asset_lifecycle.get("route") == "asset_only":
        return f"route_asset_lifecycle:{asset_lifecycle.get('operation', 'revise')}"
    if work_type == "new_feature":
        return "create_new_spec"
    if work_type == "enhancement":
        return "retrieve_and_update_existing_spec"
    if work_type == "bug_fix":
        return "identify_broken_spec_segment"
    return "lightweight_maintenance_note"


def _update_poa(
    path: Path,
    *,
    stage_contract: Dict[str, Any],
    ticket_key: str,
    branch_name: str,
    work_type: str,
    intake: Dict[str, Any],
    autonomy: Dict[str, Any],
    spec_action: str,
    delivery_route: str,
    asset_operation: str,
    phase_plan: Dict[str, Any],
    capability_matches: Sequence[str],
    impacted_areas: Sequence[str],
    parallel_work: Dict[str, Any],
) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    managed_block = "\n".join(
        [
            _STAGE_FRONT_DOOR_BEGIN,
            "## Stage C Front-Door Gate Outputs",
            "",
            f"- Ticket: `{ticket_key or '(missing)'}`",
            f"- Branch: `{branch_name}`",
            f"- Work type: `{work_type}`",
            f"- Intake sufficient: `{intake['sufficient']}` at confidence `{intake['confidence']:.2f}`",
            f"- Autonomy level: `{autonomy['level']}` at confidence `{autonomy['confidence']:.2f}`",
            f"- Spec action: `{spec_action}`",
            f"- Delivery route: `{delivery_route}`",
            f"- Asset operation: `{asset_operation}`",
            f"- Phase mode: `{phase_plan['phase_mode']}` with `{phase_plan['phase_count']}` phase(s)",
            f"- Restart recommendation: `{phase_plan['restart_recommendation']}`",
            f"- Parallel work decision: `{parallel_work['decision']}`",
            "",
            "### Capability Matches",
            *([f"- `{item}`" for item in capability_matches] or ["- `(none matched explicitly yet)`"]),
            "",
            "### Impacted Areas",
            *([f"- `{item}`" for item in impacted_areas] or ["- `(to be refined during implementation planning)`"]),
            "",
            _STAGE_FRONT_DOOR_END,
            "",
        ]
    )
    updated = _replace_managed_block(existing, managed_block)
    if "## Watchpoints" not in updated:
        watchpoints = list(stage_contract.get("watchpoints", []))
        watchpoints_block = "\n".join(
            [
                "## Watchpoints",
                "",
                *[f"- {line}" for line in watchpoints],
                "",
            ]
        )
        if updated and not updated.endswith("\n"):
            updated += "\n"
        updated = updated + ("\n" if updated else "") + watchpoints_block
    path.write_text(updated, encoding="utf-8")


def _upsert_stage_status(
    path: Path,
    *,
    stage_name: str,
    status: str,
    owner_agent: str,
    summary: str,
    artifacts: Sequence[str],
) -> None:
    payload = load_orchestrate_json_file(path) if path.exists() else {}
    existing = payload.get(stage_name)
    if not isinstance(existing, dict):
        existing = {
            "started_at": _iso_now(),
            "ended_at": "",
            "retry_count": 0,
        }
    existing["status"] = status
    existing["owner_agent"] = owner_agent
    existing["summary"] = summary
    existing["artifacts"] = list(artifacts)
    if status in {"completed", "blocked", "needs_clarification"}:
        existing["ended_at"] = _iso_now()
        if not existing.get("started_at"):
            existing["started_at"] = _iso_now()
    payload[stage_name] = existing
    _write_json(path, payload)


def _finalize_prerequisite_stages(path: Path) -> None:
    payload = load_orchestrate_json_file(path) if path.exists() else {}
    changed = False
    for stage_name, summary in (
        ("stage_a_contract_foundation", "Stage A contract foundation completed and frozen as the repo-level orchestration baseline."),
        ("stage_b_core_runtime", "Stage B core runtime bootstrap commands and policy loading were implemented and validated."),
        ("stage_b_runtime_bootstrap", "Stage B runtime bootstrap completed and handed off to the front-door orchestration layer."),
    ):
        stage_payload = payload.get(stage_name)
        if not isinstance(stage_payload, dict):
            continue
        if stage_payload.get("status") != "completed":
            stage_payload["status"] = "completed"
            stage_payload["ended_at"] = _iso_now()
            if not stage_payload.get("started_at"):
                stage_payload["started_at"] = _iso_now()
            stage_payload["summary"] = summary
            changed = True
    if changed:
        _write_json(path, payload)


def _upsert_decisions(path: Path, *, decisions: Sequence[Dict[str, Any]]) -> None:
    payload = load_orchestrate_json_file(path) if path.exists() else {"schema_version": "1.0", "decisions": []}
    existing = payload.get("decisions", [])
    indexed = {str(item.get("id", "")).strip(): item for item in existing if isinstance(item, dict)}
    for decision in decisions:
        indexed[str(decision.get("id", "")).strip()] = decision
    payload["schema_version"] = str(payload.get("schema_version", "1.0")).strip() or "1.0"
    payload["decisions"] = list(indexed.values())
    _write_json(path, payload)


def _write_handoff(path: Path, *, summary: str, next_action: str, status: str, at: str) -> None:
    path.write_text(
        "\n".join(
            [
                "# Handoff",
                "",
                f"- Updated at: `{at}`",
                f"- Status: {status}",
                "",
                "## Summary",
                summary,
                "",
                "## Next Steps",
                f"- {next_action}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_resume_context(path: Path, *, active_run_key: str, current_stage: str, next_action: str, notes: Sequence[str]) -> None:
    lines = [
        "# Resume Context",
        "",
        f"Active run: `{active_run_key}`",
        f"Current focus: `{current_stage}`",
        f"Next action: {next_action}",
        "",
        "## Activity Log",
        *[f"- {note}" for note in notes],
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _replace_managed_block(existing: str, replacement: str) -> str:
    if _STAGE_FRONT_DOOR_BEGIN in existing and _STAGE_FRONT_DOOR_END in existing:
        pattern = re.compile(
            rf"{re.escape(_STAGE_FRONT_DOOR_BEGIN)}.*?{re.escape(_STAGE_FRONT_DOOR_END)}\n?",
            re.DOTALL,
        )
        return pattern.sub(replacement, existing)
    if existing and not existing.endswith("\n"):
        existing += "\n"
    return existing + ("\n" if existing else "") + replacement


def _tokenize(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if token and token not in _STOP_WORDS}


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _stage_text(contract: Dict[str, Any], section: str, key: str) -> str:
    payload = contract.get(section, {})
    if not isinstance(payload, dict):
        return ""
    return str(payload.get(key, "")).strip()


def _render_template(template: str, **context: Any) -> str:
    return template.format(**context)


def _render_templates(templates: Sequence[str], **context: Any) -> List[str]:
    return [str(template).format(**context) for template in templates if str(template).strip()]


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_markdown(path: Path, lines: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
