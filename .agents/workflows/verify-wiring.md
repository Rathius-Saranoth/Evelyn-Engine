---
description: Deterministic AST wiring and compiler-level dead-code verification to eliminate uncalled functions and unwired code
title: verify-wiring.md
date created: 2026-09-06 18:46:25
date modified: 2026-09-06 18:46:25
tags: [wiring, dead-code, hygiene, verification, ast, vulture, workflow, evelyn]
---

# Deterministic Wiring & Code Hygiene Workflow

> Navigation: [[AGENTS.md]] · [[quality-review.md]] · [[engine_architecture.md]] · [[README.md]]

Run this workflow after completing any feature, tool addition, refactor, or configuration change to ensure every newly created or modified component is actively hooked up and free of unwired code or dead functions.

> [!IMPORTANT]
> Large Language Models suffer from **semantic plausibility bias** and **attention dilution** across large codebases. Never rely on probabilistic LLM reviews to verify function call paths or configuration wiring. Always enforce deterministic AST and compiler gates.

---

## 1. Unified Mechanical Gate

Execute the deterministic hygiene and wiring suite:

```bash
PYTHONPATH=. /home/rathius/evelyn/venv/bin/python scripts/check_code_hygiene.py
```

This runner deterministically validates three layers:

1. **Ruff Static Analysis**: Lints modern Python syntax, async safety, bugbear patterns, and import ordering.
2. **AST Config-Wiring Verification (`test_config_wiring.py`)**: Traverses the Python Abstract Syntax Tree across the repository and fails immediately if any uppercase constant declared in `evelyn_config.py` lacks active consumers.
3. **Vulture Compiler-Level Dead-Code Detection**: Builds concrete syntax trees and symbol graphs across `evelyn_server.py`, `evelyn_config.py`, and `Evelyn/` to flag uncalled functions, unused variables, and unreachable blocks.

If Ruff finds autofixable formatting or import ordering issues, pass `--fix`:
```bash
/home/rathius/evelyn/venv/bin/python scripts/check_code_hygiene.py --fix
```

---

## 2. Standard Operating Procedure for Additions

Whenever introducing new configuration constants, tools, or architectural logic, enforce the four-stage gate:

```
+------------------------------------------------------------------------+
| 1. DECLARE WITH CONSUMER: Never commit a setting or helper in isolation.|
| 2. RUN AST WIRING GATE: Verify test_config_wiring.py passes.           |
| 3. RUN VULTURE DEAD-CODE AUDIT: Ensure 0 orphaned functions exist.     |
| 4. TWO-FILE CONTRACT AUDITING: Verify producer-consumer call site diff.|
+------------------------------------------------------------------------+
```

### Stage 1: Declare with Consumer
Never add a configuration constant in `evelyn_config.py` or a helper in `Evelyn/tools/` without writing the downstream consumer logic in the exact same change set.

### Stage 2: AST Config Wiring Test
If a constant is intentionally reserved for environment templates or future roadmap scopes, register it explicitly in `CONFIG_WHITELIST` within `Evelyn/tests/test_config_wiring.py`.

### Stage 3: Vulture Whitelist Maintenance
FastAPI endpoints, Pydantic response models, and dynamic tool definitions registered in `MODEL_TOOL_DEFINITIONS` lack static in-repo call sites. Dynamic framework entry points are declared in `.vulture_whitelist.py`.
- If introducing an external route or framework hook, register its mock symbol in `.vulture_whitelist.py` (`_.new_endpoint_or_model`).
- Internal business logic and utility helpers must have genuine in-repo callers and must NOT be whitelisted.

### Stage 4: Two-File Contract Auditing
When performing code reviews, constrain the review strictly to isolated producer-consumer file pairs rather than asking broad repository questions:

```
+------------------------------------------------------------------------+
| ❌ INEFFECTIVE BROAD PROMPT:                                           |
| "Review the repository and see if the new search tool is wired."       |
+------------------------------------------------------------------------+
| ✔ DETERMINISTIC TWO-FILE CONTRACT:                                    |
| "Compare @evelyn_tools.py and @evelyn_server.py. Trace every reference |
| to `search_obsidian_notes` from definition in MODEL_TOOL_DEFINITIONS to|
| dispatch in handle_chat_stream(). Output the exact diff to hook it up."|
+------------------------------------------------------------------------+
```

---

## 3. Verification Checklist

Before closing an implementation task:

- [ ] `scripts/check_code_hygiene.py` executed and exited with `0` (all stages green).
- [ ] Pytest suite executed and passing: `PYTHONPATH=. /home/rathius/evelyn/venv/bin/pytest Evelyn/tests`.
- [ ] No unconsumed functions, variables, or configuration parameters left behind.
- [ ] Two-file producer-consumer diff inspected for explicit end-to-end invocation.
