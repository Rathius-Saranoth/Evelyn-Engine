---
title: docstring_guide.md
date created: 2026-03-14 22:22:20
date modified: 2026-08-28 14:41:24
tags: [markdown, reference, docstring, guide, formatting, pipeline, evelyn]
---

# The Docstring Guide: LLMs, Agents, and Best Practices

> Navigation: [[README.md]] · [[AGENTS.md]] · [[engine_architecture.md]]

Docstrings are more than just "fancy comments." While comments are meant for humans reading the source code, **Docstrings** are a programmatic part of the Python language that external tools (including AI Agents like me) use to understand your code.

## 1. Docstrings vs. Comments

| Feature         | Comments (`#`)                | Docstrings (`"""`)                                 |
| :-------------- | :---------------------------- | :------------------------------------------------- |
| **Visibility**  | Only in the source code file. | Readable by the Python interpreter at runtime.     |
| **Object Link** | Floating text in the file.    | Attached directly to a function, class, or module. |
| **Discovery**   | Hidden from automated tools.  | Accessible via `obj.__doc__` or `help(obj)`.       |
| **LLM Utility** | Good for "why" logic exists.  | **Critical** for "how" to use a tool.              |

## 2. Why Docstrings are "LLM Candy"

When I (or any LLM) look at your scripts, I don't just "read the text" like a human does. I use programmatic tools to scan your workspace.

### A. Tool Discovery

If I need to "Refactor the Gist Ingestion," I look for functions that handle ingestion. If you have a docstring, Python tells me exactly what that function does without me having to read and "guess" from the 100 lines of code inside it.

### B. Automated RAG

If you use a system like Open WebUI or LangChain, these platforms often index your Python files. They specifically look for docstrings to create "embeddings" (search indexes).

- **No Docstring:** The AI might index the variable names and hope for the best.
- **Good Docstring:** The AI knows exactly when to call that script based on your description.

### C. Type Safety

LLMs are much better at writing code when they know the **Types**. Docstrings (especially in Google or NumPy format) tell the LLM that `file_id` is a `str` and `mtime` is an `int`.

## 3. Best Practice: The "Google Style" Format

This is the most readable format for both humans and LLMs. It follows the "Do this, Return this" mantra but adds structural clarity.

```python
def process_vault_file(path: str, force: bool = False) -> dict:
    """Processes a single markdown file from the Obsidian vault.

    This function reads the frontmatter, generates a summary using the 
    local LLM, and returns a structured dictionary for the vault map.

    Args:
        path: The absolute path to the .md file.
        force: If True, re-processes even if the file hasn't changed.

    Returns:
        A dictionary containing 'summary', 'mtime', and 'word_count'.
    """
    # Logic goes here...
```

## 4. References and Mentions (`@[filename]`)

You can (and should!) use references in docstrings just like you do in comments.

In fact, placing a reference like `@[reference/related_projects_locations.md]` in a **Docstring** is often **more effective** for an LLM:

- **In a Comment:** I see the reference while reading the "internal logic" (the "how").
- **In a Docstring:** I see the reference while inspecting the "interface" (the "what").

If I'm deciding *which* script to run, I'll see the docstring first. If it points to another file for context, I can immediately pivot to that file before I even finish reading the current one.

## 5. Pro Tip: Instructions in Markdown

In Markdown files instead of using Python docstring syntax, you can use **GitHub Alerts**. These are specifically designed to catch the "attention" of both humans and AI models like me.

```markdown
> [!IMPORTANT]
> Always refer to these absolute paths for external projects.
```

- **[!NOTE]**: General useful info.
- **[!TIP]**: Better ways to do things.
- **[!IMPORTANT]**: Essential "don't miss this" info.
- **[!WARNING]**: Dangerous or tricky logic.

## 6. How to Implement Them Usefully

1. **Start every script** with a module-level docstring (at the very top).
2. **Document the "Intent"**: Don't just say what the code does (the code already says that). Say *why* it exists and *how* to use it.
3. **Include References**: Link related documents or dependencies in the docstring so the LLM has the full context immediately.
4. **Use specific parameter names**: Be clear about units (e.g., "timeout in seconds").

---

## 7. Pipeline Internals Reference

Developer-level reference for the Evelyn background processing pipeline modules.
Extracted from oversized module docstrings to maintain the 15-line navigation-aid ceiling defined in §2.

### fact_consolidator.py — Function Index

**Purpose:** Idle-time deduplication and category correction for Evelyn's context memory database.

#### Preamble Helpers
- `_extracting_elsewhere()`: Mutual-exclusion check against `fact_extractor.py`.
- `_heavy_tasks_running()`: Delegates to `task_manager.is_any_running(exclude="consolidator")` — see `engine_architecture.md §5`.
- `_set_status_in_server()`: Delegates to `task_manager.set_running()` / `task_manager.clear_running()`.
- `_load_scan_state()` / `_save_scan_state()`: Manage per-category anchor pointers on disk.
- `_call_ollama()`: Shared non-streaming Ollama call primitive.

#### Public API
- `cancel_pending_consolidation()`: Called on new chat request to free Ollama.
- `run_consolidation()`: Top-level coroutine for idle-time scheduling.

#### Step 1: Scan
- `scan_context_entries()`: Fetch live entries from SQLite.

#### Step 2: Detection
- **Consolidation Detection**: `_CONSOL_DETECT_PROMPT`, `_build_consol_prompt()`, `_detect_consol_in_group()`, `_parse_consol_yaml()`.
- **Recategorization Detection**: `_RECAT_DETECT_PROMPT`, `_build_recat_prompt()`, `_detect_recat_in_group()`, `_parse_recat_yaml()`.

#### Step 3: Proposals
- `generate_consolidation_proposal()`: LLM-driven merge verdict (`think=True`).
- `_write_recategorization_proposal()`: Create recategorization proposal record.

---

### fact_extractor.py — Architecture Notes

**Purpose:** Idle-time personal-fact extraction from chat history into memory.

- Reads directly from `evelyn_chat.db` using a persistent high-water mark (`_last_extracted_id`).
- Only new messages since the last successful run are processed.
- Runs as an idle-time background task to avoid competing with the chat loop.

---

### query_reformulator.py — Design Rationale

**Purpose:** Converts conversational user messages into embedding-optimized search queries.

- Raw user messages are converted into concise keyword queries before passing to the embedding model.
- Uses identical model/options as main chat loop to avoid VRAM eviction/swap overhead.
