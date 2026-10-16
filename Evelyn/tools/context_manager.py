# context_manager.py
# date created: 2026-02-12 19:08:42
# date modified: 2026-10-16 00:13:19
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

import contextlib
import datetime
import sqlite3
from typing import Any

import evelyn_config as cfg
from Evelyn.tools import memory_db, tag_librarian
from Evelyn.tools.tag_librarian import normalize_tag_format


def append_context_log(
    category_code: str | None = None,
    summary: str = "",
    secondary_cats: list[str] | str | None = None,
    subject: str | None = None,
    tags: str | None = None,
    **kwargs: Any,
) -> str:
    """Creates a new Context Entry in the SQLite memory DB, pending review.

    Args:
        category_code: Deprecated legacy parameter (used only for backward-compatible subject inference if subject is None).
        summary: The fact or event to log. Should be a concise, self-contained statement.
        secondary_cats: Optional secondary category codes or cross-refs.
        subject: Optional entity name (defaults to inferred entity or configured USER_NAME).
        tags: Optional comma-separated tags.

    Returns:
        str: A human-readable confirmation message with the ID that was created.
    """
    today = datetime.datetime.now(datetime.UTC).astimezone().strftime("%Y-%m-%d")

    if not summary and category_code:
        summary = category_code
        category_code = None

    # Determine and normalize subject
    if not subject or not subject.strip():
        if category_code and str(category_code).strip().upper().endswith("-A"):
            inferred = cfg.ASSISTANT_NAME
        else:
            inferred = cfg.USER_NAME
        subject = memory_db.normalize_subject_attribution(inferred, summary)
    else:
        subject = memory_db.normalize_subject_attribution(subject, summary)

    # Clean and combine tags / secondary categories.
    raw_tag_parts: list[str] = []
    if tags:
        raw_tag_parts.extend(tags.split(","))
    if secondary_cats:
        raw_tag_parts.extend(
            secondary_cats if isinstance(secondary_cats, list) else secondary_cats.split(",")
        )
    combined_tags_list = [
        norm for norm in (normalize_tag_format(str(t)) for t in raw_tag_parts) if norm
    ]

    final_tags = ", ".join(dict.fromkeys(combined_tags_list)) if combined_tags_list else None

    try:
        row_id = memory_db.insert_entry(
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

    if combined_tags_list:
        with contextlib.suppress(sqlite3.Error, OSError):
            kept = tag_librarian.withhold_unregistered_tags(
                row_id,
                combined_tags_list,
                origin=f"memory fact ({subject})",
                reason="Tag attached to a memory fact but absent from the controlled vocabulary.",
            )
            if kept != combined_tags_list:
                memory_db.update_entry(row_id, tags=", ".join(kept))

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
