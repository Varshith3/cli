# Artifact Validation Result

- Status: `completed`
- Owner agent: `binary-validation`
- Package root: `C:\Users\Hi\Downloads\git-repos\dp-tools-local-setup\platform-cli`
- Installed CLI version: `ghdp 0.0.0 (beta)`

## Smoke Commands
- `pipx uninstall ghdp`
- `pipx install C:\Users\Hi\Downloads\git-repos\dp-tools-local-setup\platform-cli`
- `ghdp --version`
- `ghdp --json orchestrate status --repo-root C:\Users\Hi\Downloads\git-repos\dp-tools-local-setup`

## Installed CLI Status Output
```json
{
  "active_run_key": "20260504-223548-ist__codex__stage-a",
  "agents_count": 19,
  "branch_name": "feature/EPPE-7391-TECHNICAL-agentic-framework-foundation-skills-plugins-subagents-native-memory",
  "branch_runtime_ready": true,
  "branch_runtime_root": "C:\\Users\\Hi\\Downloads\\git-repos\\dp-tools-local-setup\\.ghdp\\orchestrate\\branches\\feature-eppe-7391-technical-agentic-framework-foundation-skills-plugins-subagents-native-memory",
  "branch_slug": "feature-eppe-7391-technical-agentic-framework-foundation-skills-plugins-subagents-native-memory",
  "contract_ready": true,
  "file_checks": [
    {
      "exists": true,
      "kind": "agents_manifest",
      "messages": [],
      "rel_path": ".ghdp/agents/manifest.json"
    },
    {
      "exists": true,
      "kind": "agents_doc",
      "messages": [],
      "rel_path": ".ghdp/agents/AGENTS.md"
    },
    {
      "exists": true,
      "kind": "skills_manifest",
      "messages": [],
      "rel_path": ".ghdp/skills/manifest.json"
    },
    {
      "exists": true,
      "kind": "skills_doc",
      "messages": [],
      "rel_path": ".ghdp/skills/SKILLS.md"
    },
    {
      "exists": true,
      "kind": "plugins_manifest",
      "messages": [],
      "rel_path": ".ghdp/plugins/manifest.json"
    },
    {
      "exists": true,
      "kind": "plugins_doc",
      "messages": [],
      "rel_path": ".ghdp/plugins/PLUGINS.md"
    },
    {
      "exists": true,
      "kind": "memory_manifest",
      "messages": [],
      "rel_path": ".ghdp/memory/manifest.json"
    },
    {
      "exists": true,
      "kind": "memory_doc",
      "messages": [],
      "rel_path": ".ghdp/memory/README.md"
    },
    {
      "exists": true,
      "kind": "orchestrate_doc",
      "messages": [],
      "rel_path": ".ghdp/orchestrate/README.md"
    },
    {
      "exists": true,
      "kind": "stages_manifest",
      "messages": [],
      "rel_path": ".ghdp/orchestrate/stages/manifest.json"
    },
    {
      "exists": true,
      "kind": "stages_doc",
      "messages": [],
      "rel_path": ".ghdp/orchestrate/stages/STAGES.md"
    },
    {
      "exists": true,
      "kind": "agent_contract",
      "messages": [],
      "rel_path": ".ghdp/agents/orchestrator.json"
    },
    {
      "exists": true,
      "kind": "agent_contract",
      "messages": [],
      "rel_path": ".ghdp/agents/ticket-intake.json"
    },
    {
      "exists": true,
      "kind": "agent_contract",
      "messages": [],
      "rel_path": ".ghdp/agents/work-type-classifier.json"
    },
    {
      "exists": true,
      "kind": "agent_contract",
      "messages": [],
      "rel_path": ".ghdp/agents/autonomy-assessor.json"
    },
    {
      "exists": true,
      "kind": "agent_contract",
      "messages": [],
      "rel_path": ".ghdp/agents/context-capability-discovery.json"
    },
    {
      "exists": true,
      "kind": "agent_contract",
      "messages": [],
      "rel_path": ".ghdp/agents/parallel-work-awareness.json"
    },
    {
      "exists": true,
      "kind": "agent_contract",
      "messages": [],
      "rel_path": ".ghdp/agents/blueprint-planner.json"
    },
    {
      "exists": true,
      "kind": "agent_contract",
      "messages": [],
      "rel_path": ".ghdp/agents/architecture-review.json"
    },
    {
      "exists": true,
      "kind": "agent_contract",
      "messages": [],
      "rel_path": ".ghdp/agents/ux-dx-review.json"
    },
    {
      "exists": true,
      "kind": "agent_contract",
      "messages": [],
      "rel_path": ".ghdp/agents/implementation.json"
    },
    {
      "exists": true,
      "kind": "agent_contract",
      "messages": [],
      "rel_path": ".ghdp/agents/qa-scenario-design.json"
    },
    {
      "exists": true,
      "kind": "agent_contract",
      "messages": [],
      "rel_path": ".ghdp/agents/regression-validation.json"
    },
    {
      "exists": true,
      "kind": "agent_contract",
      "messages": [],
      "rel_path": ".ghdp/agents/test-coverage-authoring.json"
    },
    {
      "exists": true,
      "kind": "agent_contract",
      "messages": [],
      "rel_path": ".ghdp/agents/developer-test-execution.json"
    },
    {
      "exists": true,
      "kind": "agent_contract",
      "messages": [],
      "rel_path": ".ghdp/agents/binary-validation.json"
    },
    {
      "exists": true,
      "kind": "agent_contract",
      "messages": [],
      "rel_path": ".ghdp/agents/release-readiness.json"
    },
    {
      "exists": true,
      "kind": "agent_contract",
      "messages": [],
      "rel_path": ".ghdp/agents/release-prerelease.json"
    },
    {
      "exists": true,
      "kind": "agent_contract",
      "messages": [],
      "rel_path": ".ghdp/agents/pr-external-integration.json"
    },
    {
      "exists": true,
      "kind": "agent_contract",
      "messages": [],
      "rel_path": ".ghdp/agents/traceability-historian.json"
    },
    {
      "exists": true,
      "kind": "stage_contract",
      "messages": [],
      "rel_path": ".ghdp/orchestrate/stages/stage_c_front_door_gates.json"
    },
    {
      "exists": true,
      "kind": "stage_contract",
      "messages": [],
      "rel_path": ".ghdp/orchestrate/stages/stage_d_review_layer.json"
    },
    {
      "exists": true,
      "kind": "stage_contract",
      "messages": [],
      "rel_path": ".ghdp/orchestrate/stages/stage_e_execution_prep.json"
    },
    {
      "exists": true,
      "kind": "stage_contract",
      "messages": [],
      "rel_path": ".ghdp/orchestrate/stages/stage11_implementation.json"
    },
    {
      "exists": true,
      "kind": "stage_contract",
      "messages": [],
      "rel_path": ".ghdp/orchestrate/stages/stage12_commit_push.json"
    },
    {
      "exists": true,
      "kind": "stage_contract",
      "messages": [],
      "rel_path": ".ghdp/orchestrate/stages/stage13_qa_scenario_design.json"
    },
    {
      "exists": true,
      "kind": "stage_contract",
      "messages": [],
      "rel_path": ".ghdp/orchestrate/stages/stage14_touched_scope_regression.json"
    },
    {
      "exists": true,
      "kind": "stage_contract",
      "messages": [],
      "rel_path": ".ghdp/orchestrate/stages/stage15_new_test_coverage.json"
    },
    {
      "exists": true,
      "kind": "stage_contract",
      "messages": [],
      "rel_path": ".ghdp/orchestrate/stages/stage16_developer_test_execution.json"
    },
    {
      "exists": true,
      "kind": "stage_contract",
      "messages": [],
      "rel_path": ".ghdp/orchestrate/stages/stage17_packaged_artifact_validation.json"
    }
  ],
  "memory_partition_count": 2,
  "missing": [],
  "plugins_count": 7,
  "repo_contract_ready": true,
  "repo_root": "C:\\Users\\Hi\\Downloads\\git-repos\\dp-tools-local-setup",
  "skills_count": 19,
  "ticket_key": "EPPE-7391",
  "warnings": []
}
```

