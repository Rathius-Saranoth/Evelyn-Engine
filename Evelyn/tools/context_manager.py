# context_manager.py
# date created: 2026-02-12 19:08:42
# date modified: 2026-09-06 08:52:26
# tags: #context, #entities, #facts, #lifecycle, #updates

"""
context_manager.py — Context Category management for Evelyn's memory system.

Provides two sets of functionality:
  - Standalone helper functions used by ``evelyn_tools.py`` and other modules:
      append_context_log()  — Create a new context entry in evelyn_memory.db pending review.
      update_context_log()  — Create an update proposal for existing context entries.
  - Context entries are stored in SQLite (evelyn_memory.db) with lifecycle statuses
('extracted', 'pending_review', 'live', 'archived', 'deleted').
"""

import datetime
import sqlite3

import evelyn_config as cfg
from Evelyn.tools import memory_db
from Evelyn.tools.fact_consolidator import validate_and_normalize_category


def append_context_log(
    category_code: str,
    summary: str,
    secondary_cats: list[str] | str | None = None,
    subject: str | None = None,
    tags: str | None = None,
) -> str:
    """Creates a new Context Entry in the SQLite memory DB, pending review.

    Args:
        category_code: Primary category identifier, e.g. "Cat08-U" or "Cat01-A".
        summary: The fact or event to log. Should be a concise, self-contained statement.
        secondary_cats: Optional secondary category codes or cross-refs.
        subject: Optional entity name (defaults to inferred entity from category code).
        tags: Optional comma-separated tags.

    Returns:
        str: A human-readable confirmation message with the ID that was created.
    """
    today = datetime.datetime.now(datetime.UTC).astimezone().strftime("%Y-%m-%d")

    # Normalize category
    norm_cat = validate_and_normalize_category(category_code, subject) or category_code

    # Determine subject if not explicitly supplied
    if not subject or not subject.strip():
        subject_code = norm_cat[-1].upper() if norm_cat else ""
        subject = (
            cfg.ASSISTANT_NAME
            if subject_code == cfg.SUBJECT_CODE_ASSISTANT
            else (cfg.USER_NAME if subject_code == cfg.SUBJECT_CODE_USER else "Unknown")
        )

    # Clean and combine tags / secondary categories
    combined_tags_list = []
    if tags:
        combined_tags_list.extend([t.strip() for t in tags.split(",") if t.strip()])
    if secondary_cats:
        if isinstance(secondary_cats, list):
            combined_tags_list.extend([c.strip() for c in secondary_cats if c.strip()])
        else:
            combined_tags_list.extend([c.strip() for c in secondary_cats.split(",") if c.strip()])
    final_tags = ", ".join(dict.fromkeys(combined_tags_list)) if combined_tags_list else None

    try:
        row_id = memory_db.insert_entry(
            category=norm_cat,
            subject=subject,
            observation=summary,
            confidence="medium",
            source="manual",
            status="pending_review",
            date=today,
            tags=final_tags,
        )
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"Error writing context entry: {e}"

    return f"Created Context Entry (ID: {row_id}) pending review."


def update_context_log(target_filepaths: list, new_summary: str) -> str:
    """
    Creates an update proposal for existing context entries.

    Args:
        target_filepaths: List of target paths or IDs (currently passed as paths by LLM).
        new_summary: The new fact/summary data.

    Returns:
        str: Confirmation message.
    """
    import memory_db

    source_ids = []
    for p in target_filepaths:
        # Extract ID if RAG source format is passed
        if isinstance(p, str) and p.startswith("sqlite::context_entry::"):
            p = p.split("::")[-1]

        try:
            target_id = int(p)
        except ValueError:
            continue

        con = memory_db.get_db()
        row = con.execute("SELECT id FROM context_entries WHERE id = ?", (target_id,)).fetchone()
        con.close()
        if row:
            source_ids.append(row["id"])

    try:
        pid = memory_db.insert_proposal(
            type="update_request",
            source_ids=source_ids,
            reason=f"update requested by {cfg.ASSISTANT_NAME}",
            merged_observation=new_summary,
            status="pending"
        )
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"Error creating update proposal: {e}"

    return f"Update proposal created (ID: {pid}) for {cfg.USER_NAME} to review."
