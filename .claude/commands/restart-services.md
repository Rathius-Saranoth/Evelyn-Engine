---
description: How to cleanly and safely restart Evelyn's core services, flush WAL logs, and verify engine readiness
---

Read and follow the canonical workflow at `.agents/workflows/restart-services.md`, executing every
step in order against this repository.

That file is the single source of truth for this workflow — do not reproduce or
paraphrase its steps here. Read it first, then carry it out.

Notes for this repo:
- Use `/home/rathius/evelyn/venv/bin/python` and `/home/rathius/evelyn/venv/bin/pytest`, prefixed with `PYTHONPATH=.`.
- Never run an unbounded `pytest Evelyn/tests`; run targeted per-subsystem batches (WSL2 memory).
- Every checklist item requires evidence from actual tool output, not recall.

$ARGUMENTS
