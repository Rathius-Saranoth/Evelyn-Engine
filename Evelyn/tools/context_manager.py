# context_manager.py
# date created: 2026-02-12 19:08:42
# date modified: 2026-09-22 19:31:21
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

import evelyn_config as cfg
from Evelyn.tools import memory_db, tag_librarian
from Evelyn.tools.fact_consolidator import validate_and_normalize_category
from Evelyn.tools.tag_librarian import normalize_tag_format


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

    # Clean and combine tags / secondary categories.
    # Everything is put through the canonical normaliser (taxonomy §5) before storage.
    # This path previously stored whatever string it was handed, so the memory store could
    # accumulate 'Tech/AI' beside 'tech/ai' as two unrelated terms. Date anchors and the
    # administrative namespaces are exempted inside normalize_tag_format itself.
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

    # Terms the controlled vocabulary does not hold go to the review queue rather than
    # entering it silently. The tag is still stored: the memory store predates the
    # controlled vocabulary and almost none of its terms are registered yet, so withholding
    # unregistered tags today would strip nearly every fact. Proposing records what wants
    # admission; withholding becomes appropriate once the memory vocabulary is reconciled.
    if combined_tags_list:
        with contextlib.suppress(sqlite3.Error, OSError):
            tag_librarian.propose_tag_admission(
                combined_tags_list,
                origin=f"memory fact ({norm_cat})",
                reason="Tag attached to a memory fact but absent from the controlled vocabulary.",
            )

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
