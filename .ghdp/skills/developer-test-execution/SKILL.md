# Developer Test Execution

Purpose:
- execute the planned validation flow and capture iteration-worthy failures.

When to use:
- after implementation, regression, and coverage plans exist

Prompt contract:
- run the selected tests first
- respect shared local resources such as pipx or repo-global lock-sensitive paths
- record exact commands, outcomes, and retry decisions

Expected outputs:
- test execution log
- pass/fail summary
- iteration recommendation

