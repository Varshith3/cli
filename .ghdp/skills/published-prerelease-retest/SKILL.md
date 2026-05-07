# Published Prerelease Retest

Purpose:
- validate the actual published prerelease artifact for the current host before PR progression

When to use:
- after prerelease creation succeeds
- before PR creation and external communication are finalized

Prompt contract:
- use the recorded prerelease tag and repo from the prerelease stage artifacts
- download the real published artifact for the current host
- execute the artifact directly or through the lightest safe local install path
- record the exact artifact name and smoke commands used
- block downstream PR progression if the published artifact does not behave correctly

Expected outputs:
- `published_prerelease_prompt.md`
- `published_prerelease_bindings.json`
- `published_prerelease_validation.md`
- `published_prerelease_summary.md`
