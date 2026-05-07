# Complexity Autonomy Assessment

Purpose:
- decide the safest autonomy level for the run based on ambiguity and blast radius.

When to use:
- before implementation planning or execution begins

Prompt contract:
- score ambiguity, risk, and reversibility
- escalate when confidence is not high enough for the requested autonomy

Expected outputs:
- `autonomy_decision`
- `gating_rationale`
