# PR Branch Hygiene

Purpose:
- enforce pre-PR branch hygiene such as rebasing onto the latest develop branch and avoiding merge commits

When to use:
- immediately before PR creation or PR reuse

Prompt contract:
- fetch the latest `origin/develop` before validating branch hygiene
- block when the branch is not rebased onto the latest `origin/develop`
- block when merge commits are present in the feature branch range
- write the exact git evidence used for the decision

Expected outputs:
- `pr_branch_hygiene.md`
- `pr_branch_hygiene.json`
