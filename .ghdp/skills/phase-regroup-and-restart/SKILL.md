# Phase Regroup And Restart

Purpose:
- formalize phase slicing, regroup decisions, and restart destinations when the current scope grows too large or becomes blocked

When to use:
- during front-door planning
- when review, testing, or readiness blockers suggest the current phase should restart from an earlier point

Prompt contract:
- explicitly decide whether the work stays single-phase or should split into multiple phases
- record the regroup reason, restart trigger, and restart destination when a restart is recommended
- keep the phase plan lightweight but durable enough for later resumption

Expected outputs:
- `phase_plan.json`
- `phase_regroup_summary.md`
