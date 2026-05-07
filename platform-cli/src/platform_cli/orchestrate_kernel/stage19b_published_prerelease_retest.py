from __future__ import annotations

import os
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List

from platform_cli.core.errors import PlatformError
from platform_cli.exec.runner import run_cmd
from platform_cli.manifests.orchestrate_stage_load import load_stage_contract
from platform_cli.orchestrate_kernel.runtime_support import (
    assert_stage_completed,
    iso_now,
    render_templates,
    resolve_active_run_context,
    stage_text,
    update_poa_section,
    upsert_decisions,
    upsert_stage_status,
    write_handoff,
    write_json,
    write_markdown,
    write_resume_context,
)
from platform_cli.tools.orchestrate_contract import load_agent_contract


_STAGE_PRERELEASE = "stage19_prerelease_creation"
_STAGE_PUBLISHED = "stage19b_published_prerelease_retest"
_POA_BEGIN = "<!-- GHDP:BEGIN STAGE19B_PUBLISHED_PRERELEASE -->"
_POA_END = "<!-- GHDP:END STAGE19B_PUBLISHED_PRERELEASE -->"


@dataclass
class PublishedPrereleaseRetestResult:
    repo_root: str
    branch_name: str
    active_run_key: str
    status: str
    current_stage: str
    next_action: str
    validation_agent: str
    downloaded_asset: str
    prerelease_url: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_published_prerelease_retest_stage(*, repo_root: Path | None = None) -> PublishedPrereleaseRetestResult:
    context = resolve_active_run_context(repo_root=repo_root)
    assert_stage_completed(context.stage_status, _STAGE_PRERELEASE)
    stage_contract = load_stage_contract(stage_id=_STAGE_PUBLISHED, repo_root=context.repo_root)
    agent_contract = load_agent_contract(agent_id="published-prerelease-validation", repo_root=context.repo_root)
    allowed_skills = _normalize_list(agent_contract.get("allowed_skills", []))
    allowed_plugins = _normalize_list(agent_contract.get("allowed_plugins", []))

    prerelease_plan = _load_prerelease_plan(context.run_root / "prerelease_plan.json")
    release_plan = prerelease_plan["release_plan"]
    repo_name = str(release_plan.get("repo_name_with_owner", "")).strip()
    tag = str(release_plan.get("tag", "")).strip()
    asset_name = str(release_plan.get("build_target", {}).get("asset", "")).strip()
    prerelease_url = f"https://github.com/{repo_name}/releases/tag/{tag}" if repo_name and tag else ""

    download_dir = context.run_root / "published-prerelease"
    download_dir.mkdir(parents=True, exist_ok=True)
    asset_path = _download_release_asset(repo_name=repo_name, tag=tag, asset_name=asset_name, download_dir=download_dir)
    validation = _validate_downloaded_asset(asset_path=asset_path, repo_root=context.repo_root)

    write_markdown(
        context.run_root / "published_prerelease_validation_prompt.md",
        [
            "# Stage 19B Published Prerelease Retest Prompt",
            "",
            f"- Agent: `{agent_contract['id']}`",
            f"- Branch: `{context.branch_name}`",
            f"- Ticket: `{context.ticket_key or '(missing)'}`",
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
        ],
    )
    write_json(
        context.run_root / "published_prerelease_validation_bindings.json",
        {
            "schema_version": "1.0",
            "agent_id": agent_contract["id"],
            "allowed_skills": allowed_skills,
            "allowed_plugins": allowed_plugins,
            "repo_name_with_owner": repo_name,
            "tag": tag,
            "asset_name": asset_name,
            "downloaded_asset": str(asset_path),
            "prerelease_url": prerelease_url,
        },
    )
    write_markdown(
        context.run_root / "published_prerelease_validation_result.md",
        [
            "# Published Prerelease Validation Result",
            "",
            "- Status: `completed`",
            f"- Downloaded asset: `{asset_path}`",
            f"- Prerelease URL: `{prerelease_url or '(missing)'}`",
            "",
            "## Validation Commands",
            f"- `{' '.join(validation['version'].cmd)}`",
            f"- `{' '.join(validation['status'].cmd)}`",
            "",
            "## Version Output",
            "```text",
            (validation["version"].stdout or validation["version"].stderr or "").strip(),
            "```",
            "",
            "## Status Output",
            "```json",
            (validation["status"].stdout or validation["status"].stderr or "").strip(),
            "```",
        ],
    )

    upsert_stage_status(
        context.stage_status_path,
        stage_name=_STAGE_PUBLISHED,
        status="completed",
        owner_agent="published-prerelease-validation",
        summary="Stage 19B downloaded the published prerelease asset for the current host and validated it directly.",
        artifacts=[
            "published_prerelease_validation_prompt.md",
            "published_prerelease_validation_bindings.json",
            "published_prerelease_validation_result.md",
        ],
    )
    upsert_decisions(
        context.decisions_path,
        [
            {
                "id": _STAGE_PUBLISHED,
                "decision": "Validated the published prerelease asset directly before PR progression.",
                "status": "completed",
                "source": _STAGE_PUBLISHED,
            }
        ],
    )

    next_action = stage_text(stage_contract, "next_actions", "completed")
    context.branch_state["status"] = "paused"
    context.branch_state["current_stage"] = _STAGE_PUBLISHED
    context.branch_state["next_action"] = next_action
    context.branch_state["anomaly_flag"] = False
    context.branch_state["last_updated_at"] = iso_now()
    context.branch_state["last_updated_by"] = "published-prerelease-validation"
    write_json(context.branch_state_path, context.branch_state)

    update_poa_section(
        context.poa_path,
        begin_marker=_POA_BEGIN,
        end_marker=_POA_END,
        lines=[
            "## Stage 19B Published Prerelease Retest",
            f"- Owner agent: `{agent_contract['id']}`",
            f"- Downloaded asset: `{asset_path}`",
            f"- Prerelease URL: `{prerelease_url or '(missing)'}`",
            f"- Allowed skills: {', '.join(f'`{item}`' for item in allowed_skills) or '(none)'}",
            f"- Allowed plugins: {', '.join(f'`{item}`' for item in allowed_plugins) or '(none)'}",
        ],
    )
    write_handoff(
        context.handoff_path,
        summary=stage_text(stage_contract, "handoff_summaries", "completed"),
        next_action=next_action,
        status="paused",
        at=iso_now(),
    )
    write_resume_context(
        context.resume_context_path,
        active_run_key=context.active_run_key,
        current_stage=_STAGE_PUBLISHED,
        next_action=next_action,
        notes=render_templates(
            stage_contract.get("resume_note_templates", []),
            validation_agent=agent_contract["id"],
            asset_name=asset_name,
            prerelease_url=prerelease_url or "(missing)",
            downloaded_asset=str(asset_path),
        ),
    )
    return PublishedPrereleaseRetestResult(
        repo_root=str(context.repo_root),
        branch_name=context.branch_name,
        active_run_key=context.active_run_key,
        status="paused",
        current_stage=_STAGE_PUBLISHED,
        next_action=next_action,
        validation_agent=agent_contract["id"],
        downloaded_asset=str(asset_path),
        prerelease_url=prerelease_url,
        message=stage_text(stage_contract, "messages", "completed") or "Published prerelease validation completed.",
    )


def _load_prerelease_plan(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise PlatformError(
            "Stage 19B requires prerelease_plan.json from Stage 19.",
            code="E_ORCHESTRATE_STAGE_ORDER",
            reason="prerelease_plan",
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PlatformError(
            "Stage 19B could not parse prerelease_plan.json.",
            code="E_ORCHESTRATE_STAGE_ORDER",
            reason="prerelease_plan",
        )
    if not isinstance(payload.get("release_plan"), dict):
        raise PlatformError(
            "Stage 19B requires release_plan details inside prerelease_plan.json.",
            code="E_ORCHESTRATE_STAGE_ORDER",
            reason="release_plan",
        )
    return payload


def _download_release_asset(*, repo_name: str, tag: str, asset_name: str, download_dir: Path) -> Path:
    if not repo_name or not tag or not asset_name:
        raise PlatformError(
            "Stage 19B could not resolve repo, tag, or asset name from Stage 19 prerelease planning.",
            code="E_ORCHESTRATE_STAGE_ORDER",
            reason="published_prerelease_plan",
        )
    run_cmd(
        [
            "gh",
            "release",
            "download",
            tag,
            "--repo",
            repo_name,
            "--pattern",
            asset_name,
            "--dir",
            str(download_dir),
            "--clobber",
        ],
        check=True,
    )
    asset_path = download_dir / asset_name
    if not asset_path.exists():
        raise PlatformError(
            f"Stage 19B downloaded release assets but could not find '{asset_name}' in {download_dir}.",
            code="E_ORCHESTRATE_PUBLISHED_ASSET_MISSING",
            reason=asset_name,
        )
    if os.name != "nt":
        asset_path.chmod(asset_path.stat().st_mode | 0o111)
    return asset_path


def _validate_downloaded_asset(*, asset_path: Path, repo_root: Path) -> Dict[str, Any]:
    version = run_cmd([str(asset_path), "--version"], check=True, cwd=repo_root)
    status = run_cmd(
        [str(asset_path), "--json", "orchestrate", "status", "--repo-root", str(repo_root)],
        check=True,
        cwd=repo_root,
    )
    return {"version": version, "status": status}


def _normalize_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
