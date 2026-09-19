---
description: A structured self-review checklist based on the "Notes to Live By" engineering standards
---

Read and follow the canonical workflow at `.agents/workflows/quality-review.md`, executing every
step in order against this repository.

That file is the single source of truth for this workflow — do not reproduce or
paraphrase its steps here. Read it first, then carry it out.

Notes for this repo:
- Use `/home/rathius/evelyn/venv/bin/python` and `/home/rathius/evelyn/venv/bin/pytest`, prefixed with `PYTHONPATH=.`.
- Never run an unbounded `pytest Evelyn/tests`; run targeted per-subsystem batches (WSL2 memory).
- Every checklist item requires evidence from actual tool output, not recall.

$ARGUMENTS
