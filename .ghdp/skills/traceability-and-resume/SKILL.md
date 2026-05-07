# Traceability and Resume

Purpose:
- keep the branch run resumable, auditable, and understandable across humans and agents.

When to use:
- at every orchestrator stage transition

Prompt contract:
- update repo-local branch artifacts together
- preserve next action and current stage explicitly
- avoid hidden state that only lives in chat memory

Expected outputs:
- `branch_state.json`
- `handoff.md`
- `resume_context.md`
- `decisions.json`

