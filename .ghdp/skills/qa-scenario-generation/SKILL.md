# QA Scenario Generation

Purpose:
- design acceptance-linked and failure-sensitive scenarios for the current branch run.

When to use:
- after review is accepted
- before regression and developer test execution begin

Prompt contract:
- read `.ghdp/frbr/intent.json`
- read the branch `poa.md`
- produce 10-15 realistic scenarios when the change is large
- keep scenarios traceable to acceptance criteria and touched areas

Expected outputs:
- `qa_scenario_plan.md`
- edge-case list
- expected outcome per scenario

