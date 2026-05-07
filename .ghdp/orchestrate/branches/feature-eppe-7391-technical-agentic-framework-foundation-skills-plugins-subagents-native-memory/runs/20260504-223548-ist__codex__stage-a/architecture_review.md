# Architecture Review

- ACCEPTED: Repo-level capability contracts remain separated from runtime state under `.ghdp/orchestrate/`.
- ACCEPTED: Manifest loading and validation continue to live under `src/platform_cli/manifests/`, which aligns with the repo architecture rules.
- RESIDUAL_RISK: Capability discovery is still heuristic and may need tightening once more Stage E implementation history exists.
