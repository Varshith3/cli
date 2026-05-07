You are generating or updating `.ghdp/architecture.md` for GHDP repo readiness.

Your job is to turn stable repository facts into a concise, human-reviewable architecture document that helps agents and developers understand the repo safely.

Inputs you may receive:
- Existing `.ghdp/architecture.md` content, if any
- Repo tree summary
- Important manifests and entrypoints
- Key modules, packages, folders, workflows
- CI/build/deploy information (if present)
- Optional `.ghdp/frbr/intent.json`
- Optional ownership hints from docs or repo metadata
- Optional architecture or contributor guidance documents already present in the repo

Generation rules:
- Return only Markdown. Do not wrap the output in code fences.
- Be factual, concise, and grounded in evidence.
- First identify the repository's primary architectural subject and optimize the document around that subject. Examples: layered CLI, service/module graph, pipeline stages, application domains, infra topology, or a dominant deployable subproject.
- Prefer architecture over inventory: explain major responsibilities, boundaries, interactions, and invariants instead of listing directories.
- Prefer internal code structure, runtime boundaries, and design rules over repo-level scaffolding when both exist.
- If a dominant product surface exists, spend most of the detail budget on that product's internal architecture and compress wrappers, packaging, bootstrap scripts, and repo governance into brief supporting notes.
- When one primary software surface clearly dominates, allocate roughly 70-80% of the document's detail budget to that system's internal architecture, execution model, design constraints, and contributor rules.
- Use workflows, release assets, setup scripts, and build outputs as supporting evidence unless they are the actual architecture.
- When the evidence shows one clear primary application, CLI, library, service, or deployable subproject inside a subdirectory, center the document on that system's internal architecture rather than the repository root.
- Preserve useful human-authored content when an existing document is provided.
- Prefer filling scaffold TODO sections over rewriting the whole document.
- If the existing file is primarily scaffold text, template boilerplate, or TODO placeholders, treat it as empty and replace it completely.
- Do not reorder or rewrite good manual sections unnecessarily.
- If a richer architecture or contributor guidance document already exists for the repo's primary software surface, mirror its level of abstraction, recurring themes, and validated terminology without copying repo-specific wording.
- If a nested `ARCHITECTURE.md` or equivalent design document clearly describes the primary software surface, treat it as the highest-signal source for terminology, boundaries, invariants, and architectural concepts unless contradicted by closer implementation evidence.
- When a strong architecture document already exists, preserve its high-signal themes such as mental model, layer responsibilities, execution flow, design rules, or contributor constraints when those themes remain supported by evidence.
- When a strong architecture document already exists, do not stop at repeating its headings; extract the underlying architectural philosophy and restate it in the required GHDP structure.
- When a strong architecture or contributor guidance document exists, capture the important ideas that may appear later in that document as well, such as goals, mental model, execution flow, error-handling rules, design constraints, or contributor rules. Do not stop after only the first few layer descriptions if richer architecture guidance is available in evidence.
- If the strongest evidence reads like a design guide for an internal system, the output should also read like a design guide for that system rather than a polished repo synopsis.
- If the strongest evidence reads like a design guide for an internal system, the output should explain how to reason about and safely change that system, not just summarize what exists.
- Distinguish primary product code from supporting areas such as packaging, setup scripts, repo governance files, release artifacts, and CI automation.
- Do not invent architecture, ownership, boundaries, or risks not supported by repo evidence.
- If ownership is uncertain, state "needs confirmation" instead of guessing.
- Call out sensitive or generated areas conservatively.
- Avoid marketing language and generic filler text.
- Keep the document lean enough to maintain over time.
- It is acceptable to add a small number of extra evidence-backed sections beyond the required minimum when they materially improve architectural understanding, such as goals, high-level mental model, execution flow, error-handling rules, or contributor constraints.
- If strong evidence supports them, prefer 2-4 extra sections that preserve the most architecturally important ideas from a richer design document rather than flattening everything into the minimum required sections.
- When a strong internal architecture document exists, extra sections that preserve its core architectural ideas are expected, not optional.
- When a strong internal architecture document exists, include at least two extra sections that preserve its conceptual signal. Good candidates include `## Goals`, `## High-Level Mental Model`, `## Error Handling`, `## Design Constraints`, or `## Rules For Contributors`.
- When a strong internal architecture document exists, prefer at least three extra sections if that is needed to preserve the system's real architectural shape.
- When a strong internal architecture document exists, strongly prefer preserving its architectural control principles as first-class content, such as execution flow, error propagation, layer placement rules, subprocess boundaries, persistence boundaries, extension rules, or contributor do/don't guidance.

Required minimum sections:
- `# GHDP Architecture`
- `## Module Map`
- `## Key Entry Points`
- `## Critical Flows`
- `## Validation`
- `## Ownership`
- `## Do Not Touch`
- `## Open Questions`

Content guidance:
- Module Map:
  - describe the major subsystems, layers, modules, folders, or packages of the primary software surface
  - explain what each major area is responsible for
  - explain what does not belong there when the evidence makes the boundary meaningful
  - explain why the most important boundaries exist
  - prefer subsystem-level descriptions over a repo tour
  - if one subsystem dominates, make that subsystem the default frame of reference
  - include the dominant mental model or layering scheme when the evidence explains one
  - mention setup, workflow, packaging, and governance surfaces only briefly unless they are architecturally central
  - if support surfaces are secondary, compress them into one brief item instead of multiple detailed bullets
  - if a strong internal architecture doc exists, make most Module Map bullets about the internal system, not supporting repo surfaces
  - keep architecturally distinct control points separate when the evidence treats them as separate boundaries; do not merge them just to save space
  - cite evidence for non-obvious claims

- Key Entry Points:
  - list real runtime, CLI, package, service, validation, or build entry files/scripts/config entrypoints only if evidence exists
  - prefer concrete files over directory names when possible
  - prefer entry points that actually start important user, runtime, extension, validation, CI, or release flows
  - avoid padding this section with wrapper or support entrypoints when stronger internal entrypoints are available
  - if a dominant internal system exists, list its entrypoints first and keep support entrypoints secondary
  - if secondary repo entrypoints are weakly supported or add little architectural value, omit them

- Critical Flows:
  - describe 2-5 important execution flows likely to be affected by changes
  - explain how responsibility changes hands across layers or subsystems
  - prefer flows that explain how the primary software surface operates, is validated, or is extended
  - include architectural invariants, error-handling rules, layer placement rules, or contributor constraints when evidence supports them
  - prefer at least one representative end-to-end runtime or orchestration flow over isolated operational steps
  - when a strong internal architecture document exists, at least one flow should reconstruct the internal execution model of the primary software surface
  - when choosing between internal system flows and outer repo automation flows, prefer internal system flows unless the automation is itself architecturally central
  - if a strong architecture guide exists, preserve at least one flow that explains how a contributor-added change moves through the system or where responsibility belongs
  - cite evidence for non-obvious claims

- Validation:
  - reference `.ghdp/runbook.yaml` (by name) for how to validate
  - do not invent commands; keep it short

- Ownership:
  - list teams/maintainers/channels if evidence exists
  - otherwise write "needs confirmation"

- Do Not Touch:
  - list sensitive folders, generated code, workflows, environment-specific assets, high-risk areas, or policy-controlled seams
  - prefer areas with real architectural or operational blast radius
  - include explicitly documented architectural boundaries that contributors are told not to violate

- Open Questions:
  - list concrete unresolved architectural uncertainties that matter for understanding or changing the repo
  - avoid filler questions caused only by weak evidence selection
  - do not ask questions that are already answered by a strong architecture or contributor guidance document in evidence
  - omit speculative questions if the core architecture is already well supported by evidence

Evidence hints (lightweight):
- Where helpful, mention the specific file paths that support a statement (e.g., "Evidence: package.json", "Evidence: .github/workflows/ci.yml") without adding links or long excerpts.
- If multiple evidence sources disagree in granularity, prefer the source that is closer to the actual implementation architecture.
- Prefer evidence in this order when available: architecture/design docs, internal source layout, package/build manifests, concrete entrypoint files, then workflow/bootstrap files.

Output target:
- The final output must be a complete `.ghdp/architecture.md` document that GHDP can write directly after user review.
