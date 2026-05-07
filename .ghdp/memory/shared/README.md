# Shared Memory Partition

Use this partition for durable repo-shared memory that should survive branch handoff and cross-machine resume.

Examples:
- compact feature history
- accepted anomalies
- shared enhancement follow-ups
- durable operator notes
- merge-safe orchestrator closeouts promoted out of `.ghdp/orchestrate/branches/...`
