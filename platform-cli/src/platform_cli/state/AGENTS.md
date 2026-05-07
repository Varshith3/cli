# Agent Notes (state)

Persistent GHDP state storage (tool versions, last actions, timestamps, flags).

## Rules
- Treat state as backward compatible: new keys OK, avoid breaking reads.
- State persists across reinstall unless user deletes it.
- Do not store secrets here.
