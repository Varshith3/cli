# Audit Export Persistence

Purpose:
- export orchestration evidence to the configured storage destination without changing the evidence contract

When to use:
- during final historian closeout
- when run evidence must be mirrored outside `.ghdp/orchestrate`

Prompt contract:
- support local export as the default path
- support AWS S3 as a configured destination mode without hardwiring bucket details into code
- keep the exported packet stable regardless of destination backend

Expected outputs:
- `audit_export_summary.json`
- `audit_export_markdown.md`
