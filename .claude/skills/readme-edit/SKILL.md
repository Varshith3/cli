---
name: readme-edit
description: Make a safe README improvement and verify it with a delegated subagent, Use when user wants to update or change README file
---

# README Tiny Edit

Use this skill for:
- smoke tests
- validating Claude Code edits
- validating subagent delegation
- safe documentation updates

Rules:
- Only modify README.md
- Keep the diff extremely small
- Do not modify runtime code
- Do not create new files
- Do not delete files
- Avoid formatting rewrites
- Prefer a single small clarification or wording improvement

Workflow:
1. Read README.md
2. Find one tiny improvement
3. Apply the minimal change
4. Use the Agent tool to delegate verification to a subagent
5. The delegated subagent should:
   - inspect the README diff
   - verify formatting
   - verify no unrelated changes exist
6. Apply tiny fixes only if required
7. Summarize the final result
