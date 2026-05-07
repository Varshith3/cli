from __future__ import annotations

import json
from pathlib import Path


def seed_stage_contracts(repo_root: Path) -> None:
    stages_root = repo_root / ".ghdp" / "orchestrate" / "stages"
    stages_root.mkdir(parents=True, exist_ok=True)
    (stages_root / "STAGES.md").write_text("# stage recipes\n", encoding="utf-8")
    (stages_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "stages": [
                    {"id": "stage_c_front_door_gates", "contract_path": ".ghdp/orchestrate/stages/stage_c_front_door_gates.json"},
                    {"id": "stage_d_review_layer", "contract_path": ".ghdp/orchestrate/stages/stage_d_review_layer.json"},
                    {"id": "stage_e_execution_prep", "contract_path": ".ghdp/orchestrate/stages/stage_e_execution_prep.json"},
                    {"id": "stage11_implementation", "contract_path": ".ghdp/orchestrate/stages/stage11_implementation.json"},
                    {"id": "stage12_commit_push", "contract_path": ".ghdp/orchestrate/stages/stage12_commit_push.json"},
                    {"id": "stage13_qa_scenario_design", "contract_path": ".ghdp/orchestrate/stages/stage13_qa_scenario_design.json"},
                    {"id": "stage14_touched_scope_regression", "contract_path": ".ghdp/orchestrate/stages/stage14_touched_scope_regression.json"},
                    {"id": "stage15_new_test_coverage", "contract_path": ".ghdp/orchestrate/stages/stage15_new_test_coverage.json"},
                    {"id": "stage16_developer_test_execution", "contract_path": ".ghdp/orchestrate/stages/stage16_developer_test_execution.json"},
                    {"id": "stage17_packaged_artifact_validation", "contract_path": ".ghdp/orchestrate/stages/stage17_packaged_artifact_validation.json"},
                    {"id": "stage18_release_readiness", "contract_path": ".ghdp/orchestrate/stages/stage18_release_readiness.json"},
                    {"id": "stage19_prerelease_creation", "contract_path": ".ghdp/orchestrate/stages/stage19_prerelease_creation.json"},
                    {"id": "stage19b_published_prerelease_retest", "contract_path": ".ghdp/orchestrate/stages/stage19b_published_prerelease_retest.json"},
                    {"id": "stage20_release_notes_refresh", "contract_path": ".ghdp/orchestrate/stages/stage20_release_notes_refresh.json"},
                    {"id": "stage21_pr_external_integration", "contract_path": ".ghdp/orchestrate/stages/stage21_pr_external_integration.json"},
                    {"id": "stage22_traceability_capture", "contract_path": ".ghdp/orchestrate/stages/stage22_traceability_capture.json"},
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    contracts = {
        "stage_c_front_door_gates.json": {
            "schema_version": "1.0",
            "id": "stage_c_front_door_gates",
            "title": "Stage C Front-Door Gates",
            "owner_agent": "orchestrator",
            "messages": {
                "completed": "Front-door gates completed and the branch is ready for review-layer orchestration.",
                "intake_insufficient": "Front-door gates paused because the initiating context is not yet sufficient.",
                "autonomy_blocked": "Front-door gates paused because autonomy cannot be granted safely yet.",
                "parallel_blocked": "Front-door gates paused because overlapping parallel work was detected.",
                "asset_only": "Front-door gates detected an asset-only request and routed it to the lightweight asset lifecycle path.",
            },
            "next_actions": {
                "completed": "Run Stage D architecture review and UX/DX review using the refreshed POA.",
                "intake_insufficient": "Answer the clarification questions and rerun the front-door gates.",
                "autonomy_blocked": "Clarify the ambiguous scope and rerun the front-door gates.",
                "parallel_blocked": "Resolve the overlapping in-flight branch work before continuing.",
                "asset_only": "Run the independent asset lifecycle path for the requested asset operation, then return to SDLC only if broader code or release work is still needed.",
            },
            "stage_status_summary_template": "Front-door gates classified this run as {work_type} with {autonomy_level} autonomy and a parallel-work decision of {parallel_work_decision}.",
            "resume_note_templates": [
                "Intake sufficiency: {intake_confidence:.2f} ({intake_state}).",
                "Work type: {work_type}.",
                "Autonomy: {autonomy_level} at confidence {autonomy_confidence:.2f}.",
                "Parallel-work decision: {parallel_work_decision}.",
            ],
            "watchpoints": ["Keep review personas explicit.", "Do not force asset-only requests through full SDLC when a lighter asset lifecycle path is sufficient."],
        },
        "stage_d_review_layer.json": {
            "schema_version": "1.0",
            "id": "stage_d_review_layer",
            "title": "Stage D Review Layer",
            "owner_agent": "orchestrator",
            "messages": {
                "completed": "Review layer completed and the branch is ready for execution prep.",
                "blocked": "Review layer found blocking issues that must be resolved before execution prep.",
            },
            "next_actions": {
                "completed": "Proceed into Stage E implementation, regression planning, and test coverage authoring.",
                "blocked": "Resolve the blocking review findings and rerun the review layer.",
            },
            "handoff_summaries": {
                "completed": "Stage D review completed with the current architecture and UX/DX findings recorded in repo artifacts.",
                "blocked": "Stage D review is blocked on outstanding architecture or UX/DX findings.",
            },
        },
        "stage_e_execution_prep.json": {
            "schema_version": "1.0",
            "id": "stage_e_execution_prep",
            "title": "Stage E Execution Prep",
            "owner_agent": "orchestrator",
            "messages": {
                "completed": "Execution prep completed and the branch now has repo-backed implementation, QA, regression, and coverage artifacts."
            },
            "next_actions": {
                "completed": "Start stage11 implementation using implementation_plan.md, then execute regression, coverage, and developer test plans."
            },
            "handoff_summaries": {
                "completed": "Stage E execution prep completed with repo-backed bindings for implementation, QA, regression, coverage, and developer test execution."
            },
        },
        "stage11_implementation.json": {
            "schema_version": "1.0",
            "id": "stage11_implementation",
            "title": "Stage 11 Implementation",
            "owner_agent": "implementation",
            "messages": {"active": "Stage 11 implementation is active and the branch has a repo-backed implementation packet."},
            "next_actions": {"active": "Execute the implementation work using implementation_prompt.md, then update implementation_summary.md before commit/push."},
            "handoff_summaries": {"active": "Stage 11 implementation was activated with explicit agent bindings and a runnable implementation prompt."},
            "delivery_posture": ["Apply the approved plan without drifting from the reviewed scope."],
            "summary_ready_inputs": ["implementation_plan.md", "implementation_prompt.md", "implementation_bindings.json"],
            "summary_expected_next_step": "Apply the planned code and artifact changes, then update this summary with observed work completed.",
            "resume_note_templates": [
                "Implementation agent: {implementation_agent}.",
                "Allowed skills: {allowed_skill_count}.",
                "Allowed plugins: {allowed_plugin_count}.",
                "Implementation targets: {implementation_target_count}.",
            ],
        },
        "stage12_commit_push.json": {
            "schema_version": "1.0",
            "id": "stage12_commit_push",
            "title": "Stage 12 Commit Push",
            "owner_agent": "implementation",
            "messages": {"completed": "Stage 12 commit/push completed and the branch has been pushed."},
            "next_actions": {"completed": "Proceed into Stage 13 QA scenario design, Stage 14 regression validation, and Stage 15 test coverage authoring."},
            "handoff_summaries": {"completed": "Stage 12 commit/push completed and the branch was pushed with the current implementation checkpoint."},
            "resume_note_templates": [
                "Commit message: {commit_message}.",
                "Files committed: {files_committed_count}.",
                "Head SHA: {head_sha}.",
                "Pushed to {remote_name}/{branch_name}.",
            ],
        },
        "stage13_qa_scenario_design.json": {
            "schema_version": "1.0",
            "id": "stage13_qa_scenario_design",
            "title": "Stage 13 QA Scenario Design",
            "owner_agent": "qa-scenario-design",
            "messages": {"completed": "Stage 13 QA scenario design completed with a repo-backed scenario packet."},
            "next_actions": {"completed": "Proceed into Stage 14 touched-scope regression validation and Stage 15 new test coverage authoring using qa_scenario_plan.md."},
            "handoff_summaries": {"completed": "Stage 13 QA scenario design completed with acceptance-linked scenarios and explicit edge cases for downstream validation."},
            "scenario_design_posture": ["Tie every scenario back to the acceptance and touched scope of this branch run."],
            "summary_ready_inputs": ["qa_scenario_prompt.md", "qa_scenario_bindings.json", "qa_scenario_plan.md"],
            "summary_expected_next_step": "Consume these scenarios in Stage 14 regression validation and Stage 16 developer test execution.",
            "resume_note_templates": [
                "QA scenario agent: {qa_agent}.",
                "Allowed skills: {allowed_skill_count}.",
                "Allowed plugins: {allowed_plugin_count}.",
                "Scenarios designed: {scenario_count}.",
                "Edge cases captured: {edge_case_count}.",
            ],
        },
        "stage14_touched_scope_regression.json": {
            "schema_version": "1.0",
            "id": "stage14_touched_scope_regression",
            "title": "Stage 14 Touched Scope Regression",
            "owner_agent": "regression-validation",
            "messages": {"completed": "Stage 14 touched-scope regression validation completed with a repo-backed regression packet."},
            "next_actions": {"completed": "Proceed into Stage 15 new test coverage authoring using regression_selection.md and qa_scenario_plan.md."},
            "handoff_summaries": {"completed": "Stage 14 touched-scope regression validation completed with selected tests, rationale, and downstream execution guidance."},
            "selection_posture": ["Protect already-working behavior before expanding into new implementation work."],
            "summary_ready_inputs": ["regression_prompt.md", "regression_bindings.json", "regression_selection.md", "regression_summary.md"],
            "summary_expected_next_step": "Use the selected regression set while authoring new coverage and later during developer test execution.",
            "resume_note_templates": [
                "Regression agent: {regression_agent}.",
                "Allowed skills: {allowed_skill_count}.",
                "Allowed plugins: {allowed_plugin_count}.",
                "Selected tests: {selected_test_count}.",
                "Selection reasons recorded: {selection_reason_count}.",
            ],
        },
        "stage15_new_test_coverage.json": {
            "schema_version": "1.0",
            "id": "stage15_new_test_coverage",
            "title": "Stage 15 New Test Coverage",
            "owner_agent": "test-coverage-authoring",
            "messages": {"completed": "Stage 15 new test coverage authoring completed with a repo-backed coverage backlog."},
            "next_actions": {"completed": "Proceed into Stage 16 developer test execution using coverage_backlog.md, regression_selection.md, and qa_scenario_plan.md."},
            "handoff_summaries": {"completed": "Stage 15 new test coverage authoring completed with a focused backlog for the next execution stage."},
            "coverage_posture": [
                "Add only the smallest new or expanded tests needed to protect the current changed behavior.",
                "Prefer repo-owned orchestrator tests over broad churn outside the touched surface.",
                "Keep the backlog explicit enough that developer test execution can run it without rediscovering intent."
            ],
            "summary_ready_inputs": ["coverage_prompt.md", "coverage_bindings.json", "coverage_backlog.md", "coverage_summary.md"],
            "summary_expected_next_step": "Execute the authored coverage backlog alongside the selected regression set during Stage 16 developer test execution.",
            "resume_note_templates": [
                "Coverage agent: {coverage_agent}.",
                "Allowed skills: {allowed_skill_count}.",
                "Allowed plugins: {allowed_plugin_count}.",
                "Authored tests: {authored_test_count}.",
                "Coverage goals anchored: {coverage_goal_count}.",
            ],
        },
        "stage16_developer_test_execution.json": {
            "schema_version": "1.0",
            "id": "stage16_developer_test_execution",
            "title": "Stage 16 Developer Test Execution",
            "owner_agent": "developer-test-execution",
            "execution_mode": "sequential",
            "messages": {
                "completed": "Stage 16 developer test execution completed successfully with a repo-backed execution log.",
                "failed": "Stage 16 developer test execution found failing validation and blocked the downstream release path."
            },
            "next_actions": {
                "completed": "Proceed into Stage 17 packaged artifact validation and binary-focused verification.",
                "failed": "Resolve the failing validation and rerun Stage 16 before any release-facing stages continue."
            },
            "handoff_summaries": {
                "completed": "Stage 16 developer test execution completed with the selected regression and authored coverage backlog executed in a reproducible order.",
                "failed": "Stage 16 developer test execution failed and recorded the blocking validation output for the next owner."
            },
            "execution_posture": [
                "Run the focused regression and authored coverage backlog in a deterministic order.",
                "Serialize this stage when local runtime artifacts or locks could interfere with one another.",
                "Capture the exact command and output so a later owner can replay the same validation path."
            ],
            "summary_ready_inputs": ["test_execution_prompt.md", "test_execution_bindings.json", "test_execution_log.md", "test_execution_summary.md"],
            "summary_expected_next_step": "Use the completed test execution evidence to decide whether packaged artifact validation can proceed.",
            "resume_note_templates": [
                "Execution agent: {execution_agent}.",
                "Allowed skills: {allowed_skill_count}.",
                "Allowed plugins: {allowed_plugin_count}.",
                "Execution mode: {execution_mode}.",
                "Executed tests: {executed_test_count}.",
                "Failed tests: {failed_test_count}.",
            ],
        },
        "stage17_packaged_artifact_validation.json": {
            "schema_version": "1.0",
            "id": "stage17_packaged_artifact_validation",
            "title": "Stage 17 Packaged Artifact Validation",
            "owner_agent": "binary-validation",
            "messages": {
                "completed": "Stage 17 packaged artifact validation completed with a repo-backed pipx install-and-smoke result."
            },
            "next_actions": {
                "completed": "Proceed into Stage 18 release readiness review using the execution and artifact validation evidence."
            },
            "handoff_summaries": {
                "completed": "Stage 17 packaged artifact validation completed with an installed CLI smoke path and recorded install evidence."
            },
            "validation_posture": [
                "Validate the packaged CLI path in isolation instead of relying only on source-mode commands.",
                "Serialize pipx operations so other sessions cannot corrupt the validation install.",
                "Record the exact smoke commands and observed outputs for later release confidence."
            ],
            "summary_ready_inputs": ["binary_validation_prompt.md", "binary_validation_bindings.json", "artifact_validation_result.md", "artifact_validation_summary.md"],
            "summary_expected_next_step": "Use the install-and-smoke evidence while deciding whether the branch is release-ready.",
            "resume_note_templates": [
                "Validation agent: {validation_agent}.",
                "Allowed skills: {allowed_skill_count}.",
                "Allowed plugins: {allowed_plugin_count}.",
                "Package root: {package_root}.",
                "Installed CLI version: {installed_cli_version}.",
                "Smoke commands recorded: {smoke_command_count}.",
            ],
        },
        "stage18_release_readiness.json": {
            "schema_version": "1.0",
            "id": "stage18_release_readiness",
            "title": "Stage 18 Release Readiness",
            "owner_agent": "release-readiness",
            "messages": {
                "completed": "Stage 18 release readiness review accepted the current branch evidence for prerelease progression.",
                "blocked": "Stage 18 release readiness review found blocking issues that must be resolved before prerelease creation."
            },
            "next_actions": {
                "completed": "Proceed into Stage 19 prerelease creation with the current execution and artifact evidence.",
                "blocked": "Resolve the blocking findings and rerun Stage 18 before any prerelease step continues."
            },
            "handoff_summaries": {
                "completed": "Stage 18 release readiness review completed with a clear go/no-go decision recorded in repo artifacts.",
                "blocked": "Stage 18 release readiness review blocked downstream release work until the listed findings are resolved."
            },
            "readiness_posture": [
                "Review the accumulated evidence instead of assuming that passing tests alone means release-ready.",
                "Block prerelease creation if traceability, artifact validation, or execution evidence is weak or missing.",
                "Keep the findings explicit enough that a later owner can resolve them without rediscovering context."
            ],
            "summary_ready_inputs": ["release_readiness_prompt.md", "release_readiness_bindings.json", "release_readiness_summary.md"],
            "summary_expected_next_step": "Use this go/no-go decision immediately before prerelease creation.",
            "resume_note_templates": [
                "Readiness agent: {readiness_agent}.",
                "Allowed skills: {allowed_skill_count}.",
                "Allowed plugins: {allowed_plugin_count}.",
                "Blocking findings: {blocking_finding_count}.",
            ],
        },
        "stage19_prerelease_creation.json": {
            "schema_version": "1.0",
            "id": "stage19_prerelease_creation",
            "title": "Stage 19 Prerelease Creation",
            "owner_agent": "release-prerelease",
            "messages": {
                "completed": "Stage 19 prerelease creation completed and recorded the prerelease link.",
                "blocked": "Stage 19 prerelease creation was blocked by the external release engine and recorded the reason."
            },
            "next_actions": {
                "completed": "Proceed into Stage 19B published prerelease retest using the real released asset for this host.",
                "blocked": "Run Stage 20 release-notes assembly or resolve the external prerelease blocker, then retry Stage 19."
            },
            "handoff_summaries": {
                "completed": "Stage 19 prerelease creation completed with a recorded tag and prerelease location.",
                "blocked": "Stage 19 prerelease creation blocked and captured the release-engine failure for follow-up."
            },
            "prerelease_posture": [
                "Use the existing release engine instead of inventing a second prerelease path.",
                "If the release engine blocks, record the exact blocker rather than hiding it behind a generic failure.",
                "Keep the prerelease packet small and factual so later stages can communicate it cleanly."
            ],
            "summary_ready_inputs": ["prerelease_prompt.md", "prerelease_plan.json", "prerelease_summary.md"],
            "summary_expected_next_step": "If blocked, repair the release-note or auth issue before retrying prerelease creation. If successful, validate the published artifact directly in Stage 19B before PR progression.",
            "resume_note_templates": [
                "Prerelease agent: {prerelease_agent}.",
                "Allowed skills: {allowed_skill_count}.",
                "Allowed plugins: {allowed_plugin_count}.",
                "Planned prerelease tag: {prerelease_tag}.",
                "Blocked reason: {blocked_reason}.",
            ],
        },
        "stage19b_published_prerelease_retest.json": {
            "schema_version": "1.0",
            "id": "stage19b_published_prerelease_retest",
            "title": "Stage 19B Published Prerelease Retest",
            "owner_agent": "published-prerelease-validation",
            "messages": {"completed": "Stage 19B validated the actual published prerelease artifact for this host."},
            "next_actions": {"completed": "Proceed into Stage 21 PR and external integration using the validated published prerelease evidence."},
            "handoff_summaries": {"completed": "Stage 19B downloaded the released artifact and validated it directly before PR progression."},
            "delivery_posture": ["Validate the published artifact instead of relying only on local package-root validation."],
            "summary_ready_inputs": [
                "published_prerelease_validation_prompt.md",
                "published_prerelease_validation_bindings.json",
                "published_prerelease_validation_result.md"
            ],
            "summary_expected_next_step": "Carry the validated prerelease link and artifact evidence into PR progression.",
            "resume_note_templates": [
                "Validation agent: {validation_agent}.",
                "Downloaded asset: {downloaded_asset}.",
                "Asset name: {asset_name}.",
                "Prerelease URL: {prerelease_url}.",
            ],
        },
        "stage20_release_notes_refresh.json": {
            "schema_version": "1.0",
            "id": "stage20_release_notes_refresh",
            "title": "Stage 20 Release Notes Refresh",
            "owner_agent": "release-prerelease",
            "messages": {"completed": "Stage 20 refreshed release notes and created the freshness commit."},
            "next_actions": {"completed": "Rerun Stage 19 prerelease creation now that notes are fresh.", "blocked": "Resolve the notes refresh blocker and rerun Stage 20."},
            "handoff_summaries": {"completed": "Stage 20 refreshed notes.md from run artifacts.", "blocked": "Stage 20 could not refresh notes.md cleanly."},
            "delivery_posture": ["Refresh branch notes from repo-backed run artifacts."],
            "summary_ready_inputs": ["release_notes_refresh.md", "release_notes_commit.json", "release_notes_context.json"],
            "summary_expected_next_step": "Retry Stage 19 prerelease creation.",
            "resume_note_templates": [
                "Release-note owner agent: {release_agent}.",
                "Notes path: {notes_path}.",
                "Freshness commit: {freshness_commit}.",
                "Blocked reason: {blocked_reason}.",
            ],
        },
        "stage21_pr_external_integration.json": {
            "schema_version": "1.0",
            "id": "stage21_pr_external_integration",
            "title": "Stage 21 PR and External Integration",
            "owner_agent": "pr-external-integration",
            "messages": {"completed": "Stage 21 PR integration completed.", "blocked": "Stage 21 PR integration blocked."},
            "next_actions": {"completed": "Proceed into Stage 22 traceability capture.", "blocked": "Resolve the PR/Jira blocker and rerun Stage 21."},
            "handoff_summaries": {"completed": "Stage 21 recorded PR/Jira evidence.", "blocked": "Stage 21 recorded a PR/Jira blocker."},
            "delivery_posture": ["Use portable GitHub CLI and Jira integration paths."],
            "summary_ready_inputs": ["pr_integration_summary.md", "pr_integration_bindings.json", "jira_update_summary.md"],
            "summary_expected_next_step": "Finalize traceability in Stage 22.",
            "resume_note_templates": [
                "PR integration agent: {integration_agent}.",
                "PR link: {pr_link}.",
                "Jira ticket: {ticket_key}.",
                "Blocked reason: {blocked_reason}.",
            ],
        },
        "stage22_traceability_capture.json": {
            "schema_version": "1.0",
            "id": "stage22_traceability_capture",
            "title": "Stage 22 Traceability Capture",
            "owner_agent": "traceability-historian",
            "messages": {"completed": "Stage 22 traceability capture completed."},
            "next_actions": {"completed": "Run the repo-defined sub-agent scenario validation or hand off the completed run."},
            "handoff_summaries": {"completed": "Stage 22 finalized the run packet."},
            "summary_ready_inputs": ["historian_closeout.md", "subagent_execution_plan.json", "subagent_execution_result.json"],
            "summary_expected_next_step": "Use the final run packet for the next orchestration cycle.",
            "resume_note_templates": [
                "Historian agent: {historian_agent}.",
                "Scenario id: {scenario_id}.",
                "Executed sub-agents: {executed_agent_count}.",
                "Final run status: {final_status}.",
            ],
        },
    }

    for filename, payload in contracts.items():
        (stages_root / filename).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _seed_kernel_topology_and_scenarios(repo_root)
    _seed_plugin_contracts(repo_root)


def _seed_kernel_topology_and_scenarios(repo_root: Path) -> None:
    orchestrate_root = repo_root / ".ghdp" / "orchestrate"
    orchestrate_root.mkdir(parents=True, exist_ok=True)
    (orchestrate_root / "merge-hygiene.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "id": "test-merge-hygiene",
                "retained_memory": {"shared_closeout_dir": ".ghdp/memory/shared/orchestrate-closeouts"},
                "archive": {
                    "destination_mode": "local",
                    "local": {"output_dir": "tmp/orchestrate-merge-archives", "retention_days": 7},
                },
                "merge_blockers": {
                    "require_stage22_closeout": True,
                    "block_active_runtime_state": True,
                    "require_promoted_memory_receipt": True,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (orchestrate_root / "kernel.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "id": "test-kernel",
                "scheduler_mode": "repo_contract_driven",
                "execution_kernel": {
                    "type": "ghdp_kernel_adapter",
                    "host_entrypoints": ["platform_cli", "codex_cli", "claude_cli", "vscode_codex", "vscode_claude"],
                    "side_effects_owned_by_kernel": ["git", "process"],
                },
                "provider_resolution": {
                    "default_provider": "codex",
                    "supported_provider_plugins": ["provider-codex", "provider-claude", "provider-vscode-codex", "provider-vscode-claude"],
                    "headless_host_fallbacks": {"vscode_codex": "codex", "vscode_claude": "claude"},
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (orchestrate_root / "topology.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "id": "test-topology",
                "default_execution_mode": "sequential",
                "parallel_groups": [{"id": "quality", "mode": "parallel", "agents": ["qa-scenario-design", "regression-validation", "test-coverage-authoring"]}],
                "sequential_groups": [{"id": "delivery", "mode": "sequential", "agents": ["orchestrator", "implementation", "developer-test-execution", "binary-validation", "release-readiness", "release-prerelease", "published-prerelease-validation", "pr-external-integration", "traceability-historian"]}],
                "dependencies": [
                    {"before": "implementation", "after": "developer-test-execution"},
                    {"before": "release-prerelease", "after": "published-prerelease-validation"},
                    {"before": "published-prerelease-validation", "after": "pr-external-integration"}
                ],
                "host_preferences": {
                    "provider-codex": ["codex"],
                    "provider-claude": ["claude"],
                    "provider-vscode-codex": ["vscode_codex", "codex"],
                    "provider-vscode-claude": ["vscode_claude", "claude"],
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    scenarios_root = orchestrate_root / "scenarios"
    scenarios_root.mkdir(parents=True, exist_ok=True)
    (scenarios_root / "manifest.json").write_text(
        json.dumps(
            {"schema_version": "1.0", "scenarios": [{"id": "new_feature_subagent_smoke", "contract_path": ".ghdp/orchestrate/scenarios/new_feature_subagent_smoke.json"}]},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (scenarios_root / "new_feature_subagent_smoke.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "id": "new_feature_subagent_smoke",
                "title": "New Feature Sub-Agent Smoke",
                "goal": "Test repo-defined sub-agent packets.",
                "host_mode": "vscode_codex",
                "provider_plugin": "provider-vscode-codex",
                "requested_agents": ["qa-scenario-design", "regression-validation"],
                "prompt_brief": ["Use repo-defined contracts only."],
                "expected_artifacts": ["subagent_execution_plan.json", "subagent_execution_result.json"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _seed_plugin_contracts(repo_root: Path) -> None:
    manifest_path = repo_root / ".ghdp" / "plugins" / "manifest.json"
    if not manifest_path.exists():
        return
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    plugins = payload.get("plugins", [])
    for entry in plugins:
        if not isinstance(entry, dict):
            continue
        plugin_id = str(entry.get("id", "")).strip()
        if not plugin_id:
            continue
        plugin_root = repo_root / ".ghdp" / "plugins" / plugin_id
        plugin_root.mkdir(parents=True, exist_ok=True)
        executor = "filesystem"
        if "codex" in plugin_id:
            executor = "codex_cli"
        elif "claude" in plugin_id:
            executor = "claude_cli"
        elif "jira" in plugin_id:
            executor = "acli"
        elif "github" in plugin_id:
            executor = "gh_cli"
        (plugin_root / "plugin.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "id": plugin_id,
                    "executor": executor,
                    "login_required": False,
                    "setup_contract": ["test plugin contract"],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
