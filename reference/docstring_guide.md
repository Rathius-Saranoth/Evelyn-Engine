# The Docstring Guide: LLMs, Agents, and Best Practices

Docstrings are more than just "fancy comments." While comments are meant for humans reading the source code, **Docstrings** are a programmatic part of the Python language that external tools (including AI Agents like me) use to understand your code.

## 1. Docstrings vs. Comments

| Feature | Comments (`#`) | Docstrings (`"""`) |
| :--- | :--- | :--- |
| **Visibility** | Only in the source code file. | Readable by the Python interpreter at runtime. |
| **Object Link** | Floating text in the file. | Attached directly to a function, class, or module. |
| **Discovery** | Hidden from automated tools. | Accessible via `obj.__doc__` or `help(obj)`. |
| **LLM Utility** | Good for "why" logic exists. | **Critical** for "how" to use a tool. |

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

In Markdown files (like [.md](file:///c:/Projects/LocalAI/Evelyn_Project_Tasks.md) files), instead of using Python docstring syntax, you can use **GitHub Alerts**. These are specifically designed to catch the "attention" of both humans and AI models like me.

```markdown
> [!IMPORTANT]
> Always refer to these absolute paths for external projects.
```

- **[!NOTE]**: General useful info.
- **[!TIP]**: Better ways to do things.
- **[!IMPORTANT]**: Essential "don't miss this" info.
- **[!WARNING]**: Dangerous or tricky logic.

## 6. How to Implement Them Usefully
1.  **Start every script** with a module-level docstring (at the very top).
2.  **Document the "Intent"**: Don't just say what the code does (the code already says that). Say *why* it exists and *how* to use it.
3.  **Include References**: Link related documents or dependencies in the docstring so the LLM has the full context immediately.
4.  **Use specific parameter names**: Be clear about units (e.g., "timeout in seconds").
