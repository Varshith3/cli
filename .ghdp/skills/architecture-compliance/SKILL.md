# Architecture Compliance

Purpose:
- review plans and implementations against repo architecture, layering, and capability-first rules.

When to use:
- during architecture review
- during release readiness when design drift is possible

Prompt contract:
- check that manifests own load/validate concerns
- check that tools own runtime behavior
- block duplicated capability surfaces and hidden coupling

Expected outputs:
- `architecture_review_findings`
- `release_readiness_summary`
