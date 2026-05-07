from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from platform_cli.core.errors import PlatformError
from platform_cli.core.repo_roots import resolve_repo_root
from platform_cli.manifests.orchestrate_load import load_orchestrate_json_file
from platform_cli.manifests.orchestrate_stage_load import load_stage_contract
from platform_cli.tools.orchestrate_contract import runtime_branch_folder_name, slugify_branch_name
from platform_cli.tools.repo_ready_generation import current_branch_name


_BRANCHES_ROOT = Path(".ghdp/orchestrate/branches")
_STAGE_REVIEW = "stage_d_review_layer"
_STAGE_ARCH_REVIEW = "stage9_architecture_review"
_STAGE_UXDX_REVIEW = "stage10_ux_dx_review"
_POA_REVIEW_BEGIN = "<!-- GHDP:BEGIN STAGE_D_REVIEW -->"
_POA_REVIEW_END = "<!-- GHDP:END STAGE_D_REVIEW -->"


@dataclass
class OrchestrateReviewResult:
    repo_root: str
    branch_name: str
    branch_slug: str
    ticket_key: str
    active_run_key: str
    action: str
    scope: str
    status: str
    current_stage: str
    next_action: str
    branch_runtime_root: str
    blocking_findings: int
    architecture_findings: List[str]
    uxdx_findings: List[str]
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_review_layer(*, scope: str = "all", repo_root: Path | None = None) -> OrchestrateReviewResult:
    normalized_scope = scope.strip().lower()
    if normalized_scope not in {"all", "architecture", "uxdx"}:
        raise PlatformError(
            "orchestrate review scope must be one of: all, architecture, uxdx.",
            code="E_ORCHESTRATE_REVIEW_SCOPE_INVALID",
            reason=scope,
        )

    resolved_root = resolve_repo_root(repo_root)
    branch_name = current_branch_name(resolved_root)
    if not branch_name:
        raise PlatformError(
            "Could not resolve the current branch for orchestrate review.",
            code="E_ORCHESTRATE_BRANCH_UNRESOLVED",
            reason="current_branch",
        )

    runtime_root = resolved_root / _BRANCHES_ROOT / runtime_branch_folder_name(resolved_root, branch_name)
    branch_state_path = runtime_root / "branch_state.json"
    if not branch_state_path.exists():
        raise PlatformError(
            "No branch runtime state exists yet. Start the orchestrator and run the front-door gates before review.",
            code="E_ORCHESTRATE_RUN_MISSING",
            reason=str(branch_state_path),
        )

    branch_state = load_orchestrate_json_file(branch_state_path)
    stage_contract = load_stage_contract(stage_id=_STAGE_REVIEW, repo_root=resolved_root)
    active_run_key = str(branch_state.get("active_run_key", "")).strip()
    if not active_run_key:
        raise PlatformError(
            "Branch runtime state does not contain an active run key.",
            code="E_ORCHESTRATE_RUN_MISSING",
            reason="active_run_key",
        )

    poa_path = runtime_root / "poa.md"
    poa_text = poa_path.read_text(encoding="utf-8") if poa_path.exists() else ""
    architecture_findings = _architecture_review_findings(resolved_root, runtime_root, branch_state, poa_text)
    uxdx_findings = _uxdx_review_findings(runtime_root, branch_state, poa_text)

    selected_findings: List[str] = []
    if normalized_scope in {"all", "architecture"}:
        selected_findings.extend(architecture_findings)
    if normalized_scope in {"all", "uxdx"}:
        selected_findings.extend(uxdx_findings)

    blocking_findings = sum(1 for finding in selected_findings if finding.startswith("BLOCKING:"))
    if blocking_findings:
        branch_status = "blocked"
        next_action = _stage_text(stage_contract, "next_actions", "blocked")
        message = _stage_text(stage_contract, "messages", "blocked")
    else:
        branch_status = "paused"
        next_action = _stage_text(stage_contract, "next_actions", "completed")
        message = _stage_text(stage_contract, "messages", "completed")

    branch_state["status"] = branch_status
    branch_state["current_stage"] = _STAGE_REVIEW
    branch_state["next_action"] = next_action
    branch_state["anomaly_flag"] = blocking_findings > 0
    branch_state["last_updated_at"] = _iso_now()
    branch_state["last_updated_by"] = "codex"
    _write_json(branch_state_path, branch_state)

    run_root = runtime_root / "runs" / active_run_key
    _write_review_file(run_root / "architecture_review.md", "Architecture Review", architecture_findings)
    _write_review_file(run_root / "uxdx_review.md", "UX/DX Review", uxdx_findings)
    if normalized_scope in {"all", "architecture"}:
        _upsert_stage_status(
            run_root / "stage_status.json",
            stage_name=_STAGE_ARCH_REVIEW,
            status="blocked" if any(item.startswith("BLOCKING:") for item in architecture_findings) else "completed",
            owner_agent="architecture-review",
            summary="Architecture review executed against the Stage C POA and runtime contract.",
            artifacts=["architecture_review.md", "poa.md"],
        )
    if normalized_scope in {"all", "uxdx"}:
        _upsert_stage_status(
            run_root / "stage_status.json",
            stage_name=_STAGE_UXDX_REVIEW,
            status="blocked" if any(item.startswith("BLOCKING:") for item in uxdx_findings) else "completed",
            owner_agent="ux-dx-review",
            summary="UX/DX review executed against the Stage C POA and current orchestrate command surface.",
            artifacts=["uxdx_review.md", "poa.md"],
        )
    _upsert_stage_status(
        run_root / "stage_status.json",
        stage_name=_STAGE_REVIEW,
        status="blocked" if blocking_findings else "completed",
        owner_agent="orchestrator",
        summary="Stage D review layer completed." if not blocking_findings else "Stage D review layer blocked on findings.",
        artifacts=["architecture_review.md", "uxdx_review.md", "poa.md"],
    )
    _upsert_decisions(
        run_root / "decisions.json",
        decisions=[
            {
                "id": "stage_d_architecture_review",
                "decision": "Architecture review completed.",
                "status": "blocked" if any(item.startswith("BLOCKING:") for item in architecture_findings) else "accepted",
                "source": "stage_d_review_layer",
            },
            {
                "id": "stage_d_uxdx_review",
                "decision": "UX/DX review completed.",
                "status": "blocked" if any(item.startswith("BLOCKING:") for item in uxdx_findings) else "accepted",
                "source": "stage_d_review_layer",
            },
        ],
    )
    _update_poa(poa_path, architecture_findings=architecture_findings, uxdx_findings=uxdx_findings)
    _write_handoff(
        runtime_root / "handoff.md",
        summary=_stage_text(stage_contract, "handoff_summaries", "blocked" if blocking_findings else "completed") or message,
        next_action=next_action,
        status=branch_status,
        at=_iso_now(),
    )
    _write_resume_context(
        run_root / "resume_context.md",
        active_run_key=active_run_key,
        current_stage=_STAGE_REVIEW,
        next_action=next_action,
        notes=[
            f"Architecture review findings: {len(architecture_findings)} recorded.",
            f"UX/DX review findings: {len(uxdx_findings)} recorded.",
            f"Blocking findings: {blocking_findings}.",
        ],
    )

    return OrchestrateReviewResult(
        repo_root=str(resolved_root),
        branch_name=branch_name,
        branch_slug=slugify_branch_name(branch_name),
        ticket_key=str(branch_state.get("ticket_key", "")).strip(),
        active_run_key=active_run_key,
        action="review",
        scope=normalized_scope,
        status=branch_status,
        current_stage=_STAGE_REVIEW,
        next_action=next_action,
        branch_runtime_root=str(runtime_root),
        blocking_findings=blocking_findings,
        architecture_findings=architecture_findings,
        uxdx_findings=uxdx_findings,
        message=message,
    )


def _architecture_review_findings(
    repo_root: Path,
    runtime_root: Path,
    branch_state: Dict[str, Any],
    poa_text: str,
) -> List[str]:
    findings: List[str] = []
    if not (repo_root / ".ghdp" / "agents" / "manifest.json").exists():
        findings.append("BLOCKING: Repo-level agents manifest is missing.")
    if not (repo_root / ".ghdp" / "skills" / "manifest.json").exists():
        findings.append("BLOCKING: Repo-level skills manifest is missing.")
    if not (repo_root / ".ghdp" / "plugins" / "manifest.json").exists():
        findings.append("BLOCKING: Repo-level plugins manifest is missing.")
    if not (repo_root / ".ghdp" / "memory" / "manifest.json").exists():
        findings.append("BLOCKING: Repo-level memory manifest is missing.")
    if "## Stage C Front-Door Gate Outputs" not in poa_text:
        findings.append("BLOCKING: The POA is missing the Stage C front-door output block.")
    if str(branch_state.get("current_stage", "")).strip() != "stage_c_front_door_gates":
        findings.append(
            "BLOCKING: The branch runtime must remain paused on stage_c_front_door_gates before review begins."
        )
    if not (repo_root / "platform-cli" / "src" / "platform_cli" / "manifests" / "orchestrate_validate.py").exists():
        findings.append("BLOCKING: Manifest validation file for orchestrator policy/contracts is missing.")
    if not (repo_root / "platform-cli" / "src" / "platform_cli" / "tools" / "orchestrate_front_door.py").exists():
        findings.append("BLOCKING: Front-door runtime implementation file is missing.")

    if not findings:
        findings.append(
            "ACCEPTED: Repo-level capability contracts remain separated from runtime state under `.ghdp/orchestrate/`."
        )
        findings.append(
            "ACCEPTED: Manifest loading and validation continue to live under `src/platform_cli/manifests/`, which aligns with the repo architecture rules."
        )
        findings.append(
            "RESIDUAL_RISK: Capability discovery is still heuristic and may need tightening once more Stage E implementation history exists."
        )
    return findings


def _uxdx_review_findings(runtime_root: Path, branch_state: Dict[str, Any], poa_text: str) -> List[str]:
    findings: List[str] = []
    handoff_path = runtime_root / "handoff.md"
    if not handoff_path.exists():
        findings.append("BLOCKING: Handoff context is missing, so resume guidance would be opaque for the next owner.")
    else:
        handoff_text = handoff_path.read_text(encoding="utf-8")
        if "## Next Steps" not in handoff_text:
            findings.append("BLOCKING: Handoff context is missing an explicit next step block.")

    if "## Watchpoints" not in poa_text:
        findings.append("BLOCKING: The POA is missing watchpoints for future contributors and agents.")

    next_action = str(branch_state.get("next_action", "")).strip()
    if not next_action:
        findings.append("BLOCKING: Branch runtime state is missing the next_action guidance.")

    if not findings:
        findings.append(
            "ACCEPTED: The orchestrate command surface remains explicit and human-readable across status, start, resume, handoff, and front-door flows."
        )
        findings.append(
            "ACCEPTED: Repo-local POA, handoff, and resume artifacts provide enough operator context for pause/resume without hidden session memory."
        )
        findings.append(
            "RESIDUAL_RISK: Stage-by-stage commands are still verbose for end users until a higher-level orchestrate run path exists."
        )
    return findings


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
        existing = {"started_at": _iso_now(), "retry_count": 0}
    existing["status"] = status
    existing["owner_agent"] = owner_agent
    existing["summary"] = summary
    existing["artifacts"] = list(artifacts)
    if status in {"completed", "blocked"}:
        existing["ended_at"] = _iso_now()
        if not existing.get("started_at"):
            existing["started_at"] = _iso_now()
    payload[stage_name] = existing
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


def _update_poa(path: Path, *, architecture_findings: Sequence[str], uxdx_findings: Sequence[str]) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    managed_block = "\n".join(
        [
            _POA_REVIEW_BEGIN,
            "## Stage D Review Findings",
            "",
            "### Architecture Review",
            *[f"- {item}" for item in architecture_findings],
            "",
            "### UX/DX Review",
            *[f"- {item}" for item in uxdx_findings],
            "",
            _POA_REVIEW_END,
            "",
        ]
    )
    path.write_text(_replace_managed_block(existing, managed_block), encoding="utf-8")


def _write_review_file(path: Path, title: str, findings: Sequence[str]) -> None:
    path.write_text(
        "\n".join(
            [
                f"# {title}",
                "",
                *[f"- {item}" for item in findings],
                "",
            ]
        ),
        encoding="utf-8",
    )


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
    path.write_text(
        "\n".join(
            [
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
        ),
        encoding="utf-8",
    )


def _replace_managed_block(existing: str, replacement: str) -> str:
    if _POA_REVIEW_BEGIN in existing and _POA_REVIEW_END in existing:
        pattern = re.compile(rf"{re.escape(_POA_REVIEW_BEGIN)}.*?{re.escape(_POA_REVIEW_END)}\n?", re.DOTALL)
        return pattern.sub(replacement, existing)
    if existing and not existing.endswith("\n"):
        existing += "\n"
    return existing + ("\n" if existing else "") + replacement


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _stage_text(contract: Dict[str, Any], section: str, key: str) -> str:
    payload = contract.get(section, {})
    if not isinstance(payload, dict):
        return ""
    return str(payload.get(key, "")).strip()


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
