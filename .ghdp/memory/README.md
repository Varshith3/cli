# Phase 1 Native Memory

Phase 1 intentionally does not depend on Mem0 or Graphify.

Instead, it uses repo-local and user-global folders to provide the minimum practical memory behavior needed today:

- shared run and branch context,
- portable handoff/resume material,
- repo-local code/context summaries,
- and enough durable state to reduce repeated re-explaining.

## Repo-Local Partitions

- `shared/`
  - durable, branch-aware memory meant to survive pause/resume and human handoff
- `context/`
  - code and architecture summaries used as the interim Graphify substitute

## Boundaries

- Repo-local memory lives here under `.ghdp/memory/`
- Noisy machine-local execution details still belong under `~/.ghdp/`
- Runtime branch state continues to live under `.ghdp/orchestrate/`
