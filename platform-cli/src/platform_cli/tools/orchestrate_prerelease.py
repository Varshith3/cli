from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Sequence

from platform_cli.core.errors import PlatformError
from platform_cli.core.repo_roots import resolve_repo_root
from platform_cli.manifests.orchestrate_load import load_orchestrate_json_file
from platform_cli.manifests.orchestrate_stage_load import load_stage_contract
from platform_cli.tools.orchestrate_contract import load_agent_contract, runtime_branch_folder_name, slugify_branch_name
from platform_cli.tools.release import ensure_binaries_release, plan_binaries_release
from platform_cli.tools.repo_ready_generation import current_branch_name


_BRANCHES_ROOT = Path(".ghdp/orchestrate/branches")
_SKILLS_ROOT = Path(".ghdp/skills")
_PLUGINS_ROOT = Path(".ghdp/plugins")
_STAGE_RELEASE_READINESS = "stage18_release_readiness"
_STAGE_PRERELEASE = "stage19_prerelease_creation"
_POA_PRERELEASE_BEGIN = "<!-- GHDP:BEGIN STAGE19_PRERELEASE -->"
_POA_PRERELEASE_END = "<!-- GHDP:END STAGE19_PRERELEASE -->"


@dataclass
class OrchestratePrereleaseResult:
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
    prerelease_agent: str
    allowed_skills: List[str]
    allowed_plugins: List[str]
    prerelease_tag: str
    prerelease_url: str
    blocked_reason: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_prerelease_stage(*, repo_root: Path | None = None) -> OrchestratePrereleaseResult:
    resolved_root = resolve_repo_root(repo_root)
    branch_name = current_branch_name(resolved_root)
    if not branch_name:
        raise PlatformError(
            "Could not resolve the current branch for orchestrate prerelease creation.",
            code="E_ORCHESTRATE_BRANCH_UNRESOLVED",
            reason="current_branch",
        )

    runtime_root = resolved_root / _BRANCHES_ROOT / runtime_branch_folder_name(resolved_root, branch_name)
    branch_state_path = runtime_root / "branch_state.json"
    if not branch_state_path.exists():
        raise PlatformError(
            "No branch runtime state exists yet. Complete the earlier orchestrator stages before prerelease creation begins.",
            code="E_ORCHESTRATE_RUN_MISSING",
            reason=str(branch_state_path),
        )

    branch_state = load_orchestrate_json_file(branch_state_path)
    active_run_key = str(branch_state.get("active_run_key", "")).strip()
    if not active_run_key:
        raise PlatformError(
            "Branch runtime state does not contain an active run key.",
            code="E_ORCHESTRATE_RUN_MISSING",
            reason="active_run_key",
        )

    run_root = runtime_root / "runs" / active_run_key
    stage_status_path = run_root / "stage_status.json"
    stage_status = load_orchestrate_json_file(stage_status_path)
    if str(stage_status.get(_STAGE_RELEASE_READINESS, {}).get("status", "")).strip() != "completed":
        raise PlatformError(
            "Stage 18 release readiness review must complete before Stage 19 prerelease creation begins.",
            code="E_ORCHESTRATE_STAGE_ORDER",
            reason=_STAGE_RELEASE_READINESS,
        )

    stage_contract = load_stage_contract(stage_id=_STAGE_PRERELEASE, repo_root=resolved_root)
    agent_contract = load_agent_contract(agent_id="release-prerelease", repo_root=resolved_root)
    allowed_skills = _normalize_list(agent_contract.get("allowed_skills", []))
    allowed_plugins = _normalize_list(agent_contract.get("allowed_plugins", []))
    _assert_skill_payloads(resolved_root, allowed_skills)
    _assert_plugin_payloads(resolved_root, allowed_plugins)

    plan = plan_binaries_release(
        repo_root=resolved_root,
        source_ref=branch_name,
        workdir=None,
        install_flavor="standard",
        release_visibility="auto",
        release_channel="auto",
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
    )
    prerelease_url = ""
    blocked_reason = ""
    blocked_error = ""
    status_key = "completed"
    branch_status = "paused"
    try:
        release_result = ensure_binaries_release(plan)
        prerelease_url = f"https://github.com/{plan.repo_name_with_owner}/releases/tag/{release_result['tag']}"
    except PlatformError as exc:
        status_key = "blocked"
        branch_status = "blocked"
        blocked_reason = f"{exc.code}:{exc.reason}"
        blocked_error = str(exc)

    _write_markdown(
        run_root / "prerelease_prompt.md",
        [
            "# Stage 19 Prerelease Prompt",
            "",
            f"- Agent: `{agent_contract['id']}`",
            f"- Role: `{agent_contract['role']}`",
            f"- Branch: `{branch_name}`",
            f"- Ticket: `{str(branch_state.get('ticket_key', '')).strip() or '(missing)'}`",
            "",
            "## Prompt Contract",
            *[f"- {line}" for line in agent_contract.get("prompt_contract", [])],
            "",
            "## Allowed Skills",
            *[f"- `{item}`" for item in allowed_skills],
            "",
            "## Allowed Plugins",
            *[f"- `{item}`" for item in allowed_plugins],
            "",
            "## Prerelease Posture",
            *[f"- {line}" for line in stage_contract.get("prerelease_posture", [])],
            "",
        ],
    )
    _write_json(
        run_root / "prerelease_plan.json",
        {
            "schema_version": "1.0",
            "agent_id": agent_contract["id"],
            "allowed_skills": [
                {
                    "id": skill_id,
                    "path": str((resolved_root / _SKILLS_ROOT / skill_id / "SKILL.md").relative_to(resolved_root)).replace("\\", "/"),
                }
                for skill_id in allowed_skills
            ],
            "allowed_plugins": [
                {
                    "id": plugin_id,
                    "path": str((resolved_root / _PLUGINS_ROOT / plugin_id / "plugin.json").relative_to(resolved_root)).replace("\\", "/"),
                }
                for plugin_id in allowed_plugins
            ],
            "release_plan": plan.to_dict(),
            "blocked_reason": blocked_reason,
        },
    )
    _write_markdown(
        run_root / "prerelease_summary.md",
        [
            "# Prerelease Summary",
            "",
            f"- Status: `{'blocked' if blocked_reason else 'completed'}`",
            "- Owner agent: `release-prerelease`",
            f"- Planned tag: `{plan.tag}`",
            f"- Prerelease URL: `{prerelease_url or '(not created)'}`",
            "",
            "## Ready Inputs",
            *[f"- `{item}`" for item in stage_contract.get("summary_ready_inputs", [])],
            "",
            "## Blocked Reason",
            f"- {blocked_reason or 'None.'}",
            "",
            "## Expected Next Step",
            f"- {str(stage_contract.get('summary_expected_next_step', '')).strip()}",
            "",
        ],
    )

    _upsert_stage_status(
        stage_status_path,
        stage_name=_STAGE_PRERELEASE,
        status="blocked" if blocked_reason else "completed",
        owner_agent="release-prerelease",
        summary=(
            "Stage 19 prerelease creation is blocked and recorded the external release failure for follow-up."
            if blocked_reason
            else "Stage 19 prerelease creation completed and recorded the prerelease link."
        ),
        artifacts=[
            "prerelease_prompt.md",
            "prerelease_plan.json",
            "prerelease_summary.md",
        ],
    )
    _upsert_decisions(
        run_root / "decisions.json",
        [
            {
                "id": "stage19_prerelease_creation",
                "decision": (
                    "Stage 19 created or updated the prerelease successfully."
                    if not blocked_reason
                    else "Stage 19 blocked prerelease creation and recorded the failure reason."
                ),
                "status": "blocked" if blocked_reason else "completed",
                "source": _STAGE_PRERELEASE,
            }
        ],
    )

    next_action = _stage_text(stage_contract, "next_actions", status_key)
    branch_state["status"] = branch_status
    branch_state["current_stage"] = _STAGE_PRERELEASE
    branch_state["next_action"] = next_action
    branch_state["anomaly_flag"] = bool(blocked_reason)
    branch_state["last_updated_at"] = _iso_now()
    branch_state["last_updated_by"] = "release-prerelease"
    _write_json(branch_state_path, branch_state)

    _update_poa(
        runtime_root / "poa.md",
        prerelease_agent=agent_contract["id"],
        allowed_skills=allowed_skills,
        allowed_plugins=allowed_plugins,
        prerelease_tag=plan.tag,
        blocked_reason=blocked_reason,
    )
    _write_handoff(
        runtime_root / "handoff.md",
        summary=_stage_text(stage_contract, "handoff_summaries", status_key),
        next_action=next_action,
        status=branch_status,
        at=_iso_now(),
    )
    _write_resume_context(
        run_root / "resume_context.md",
        active_run_key=active_run_key,
        current_stage=_STAGE_PRERELEASE,
        next_action=next_action,
        notes=_render_templates(
            stage_contract.get("resume_note_templates", []),
            prerelease_agent=agent_contract["id"],
            allowed_skill_count=len(allowed_skills),
            allowed_plugin_count=len(allowed_plugins),
            prerelease_tag=plan.tag,
            blocked_reason=blocked_reason or "none",
        ),
    )

    return OrchestratePrereleaseResult(
        repo_root=str(resolved_root),
        branch_name=branch_name,
        branch_slug=slugify_branch_name(branch_name),
        ticket_key=str(branch_state.get("ticket_key", "")).strip(),
        active_run_key=active_run_key,
        action="prerelease",
        status=branch_status,
        current_stage=_STAGE_PRERELEASE,
        next_action=next_action,
        branch_runtime_root=str(runtime_root),
        prerelease_agent=str(agent_contract["id"]),
        allowed_skills=allowed_skills,
        allowed_plugins=allowed_plugins,
        prerelease_tag=plan.tag,
        prerelease_url=prerelease_url,
        blocked_reason=blocked_reason,
        message=_stage_text(stage_contract, "messages", status_key) or blocked_error,
    )


def _normalize_list(items: Sequence[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for item in items:
        normalized = str(item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _assert_skill_payloads(repo_root: Path, skill_ids: Sequence[str]) -> None:
    missing = [skill_id for skill_id in skill_ids if not (repo_root / _SKILLS_ROOT / skill_id / "SKILL.md").exists()]
    if missing:
        raise PlatformError(
            "Stage 19 prerelease creation requires repo-level skill payloads under .ghdp/skills/<id>/SKILL.md.",
            code="E_ORCHESTRATE_SKILL_PAYLOAD_MISSING",
            reason=", ".join(missing),
        )


def _assert_plugin_payloads(repo_root: Path, plugin_ids: Sequence[str]) -> None:
    missing = [plugin_id for plugin_id in plugin_ids if not (repo_root / _PLUGINS_ROOT / plugin_id / "plugin.json").exists()]
    if missing:
        raise PlatformError(
            "Stage 19 prerelease creation requires repo-level plugin payloads under .ghdp/plugins/<id>/plugin.json.",
            code="E_ORCHESTRATE_PLUGIN_PAYLOAD_MISSING",
            reason=", ".join(missing),
        )


def _update_poa(
    path: Path,
    *,
    prerelease_agent: str,
    allowed_skills: Sequence[str],
    allowed_plugins: Sequence[str],
    prerelease_tag: str,
    blocked_reason: str,
) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    managed_block = "\n".join(
        [
            _POA_PRERELEASE_BEGIN,
            "## Stage 19 Prerelease Creation",
            "",
            f"- Prerelease agent: `{prerelease_agent}`",
            f"- Allowed skill count: `{len(allowed_skills)}`",
            f"- Allowed plugin count: `{len(allowed_plugins)}`",
            f"- Planned tag: `{prerelease_tag}`",
            f"- Blocked reason: `{blocked_reason or 'none'}`",
            "",
            "### Allowed Skills",
            *[f"- `{item}`" for item in allowed_skills],
            "",
            "### Allowed Plugins",
            *[f"- `{item}`" for item in allowed_plugins],
            "",
            _POA_PRERELEASE_END,
            "",
        ]
    )
    if _POA_PRERELEASE_BEGIN in existing and _POA_PRERELEASE_END in existing:
        pattern = re.compile(rf"{re.escape(_POA_PRERELEASE_BEGIN)}.*?{re.escape(_POA_PRERELEASE_END)}\n?", re.DOTALL)
        updated = pattern.sub(lambda _match: managed_block, existing)
    else:
        updated = existing + ("\n\n" if existing and not existing.endswith("\n\n") else "") + managed_block
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
        existing = {"started_at": _iso_now(), "retry_count": 0}
    if "started_at" not in existing or not str(existing.get("started_at", "")).strip():
        existing["started_at"] = _iso_now()
    existing["status"] = status
    existing["owner_agent"] = owner_agent
    existing["summary"] = summary
    existing["artifacts"] = list(artifacts)
    existing["ended_at"] = _iso_now()
    payload[stage_name] = existing
    _write_json(path, payload)


def _upsert_decisions(path: Path, decisions: Sequence[Dict[str, Any]]) -> None:
    payload = load_orchestrate_json_file(path) if path.exists() else {"schema_version": "1.0", "decisions": []}
    existing = payload.get("decisions", [])
    indexed = {str(item.get("id", "")).strip(): item for item in existing if isinstance(item, dict)}
    for decision in decisions:
        indexed[str(decision.get("id", "")).strip()] = decision
    payload["schema_version"] = str(payload.get("schema_version", "1.0")).strip() or "1.0"
    payload["decisions"] = list(indexed.values())
    _write_json(path, payload)


def _write_markdown(path: Path, lines: Sequence[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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


def _stage_text(contract: Dict[str, Any], section: str, key: str) -> str:
    payload = contract.get(section, {})
    if not isinstance(payload, dict):
        return ""
    return str(payload.get(key, "")).strip()


def _render_templates(templates: Sequence[str], **context: Any) -> List[str]:
    return [str(template).format(**context) for template in templates if str(template).strip()]


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
