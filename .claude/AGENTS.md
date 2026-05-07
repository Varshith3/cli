<!-- GHDP:BEGIN MANAGED BLOCK -->
generated_by: ghdp
adapter_path: .claude/AGENTS.md
source_of_truth: .ghdp/*
warning: Do not edit the managed block by hand; update .ghdp contracts instead.
<!-- GHDP:END MANAGED BLOCK -->

# Claude Host Bootstrap

When Claude is working inside this repository, `.ghdp/` is the primary source of truth.

## Mandatory Behavior

- Read `.ghdp/frbr/intent.json` first.
- Use `.ghdp/agents/manifest.json` as the sub-agent index.
- Use `.ghdp/skills/manifest.json` as the skill index.
- Use `.ghdp/plugins/manifest.json` as the plugin index.
- Use `.ghdp/orchestrate/kernel.json` as the reusable kernel contract.
- Use `.ghdp/orchestrate/topology.json` as the authority for parallel versus sequential work.
- Use `.ghdp/orchestrate/stages/` for stage-owned messaging and handoff expectations.

## Sub-Agent Policy

- When work is substantial, prefer repo-defined sub-agents over ad hoc role invention.
- Each sub-agent must use only the `allowed_skills` and `allowed_plugins` declared in its agent contract.
- When native Claude-host sub-agent spawning is available, align spawned sub-agents to `.ghdp/agents/<agent-id>.json`.
- When the host cannot execute the repo-defined host mode directly, fall back to the compatible provider path declared in `.ghdp/plugins/`.

## Execution Topology

- Respect `execution_waves`, `parallel_groups`, and `sequential_groups` from `.ghdp/orchestrate/topology.json`.
- Do not derive scheduling from request wording or scenario ordering when `.ghdp` already defines it.
- Persist prompt packets, decisions, and resume state into `.ghdp/orchestrate/...` so another host can continue without hidden context.

## Work-Type Routing

For new work, route through the repo-defined flow:

1. intake sufficiency
2. work-type classification
3. autonomy assessment
4. capability discovery
5. parallel-work awareness
6. canonical planning
7. review
8. implementation and downstream validation/release stages

The work type must be treated as one of:

- `new_feature`
- `enhancement`
- `bug_fix`
- `maintenance`

If the request is mainly about an existing GHDP-managed asset:

- route to the repo-defined asset lifecycle path first
- use `.ghdp/agents/asset-lifecycle.json`
- use `.ghdp/plugins/asset-lifecycle-sync/plugin.json`
- only expand into full SDLC if the change clearly goes beyond asset lifecycle work

## Goal

The host should behave as a consumer of the GHDP orchestration system, not as a separate orchestration source.
