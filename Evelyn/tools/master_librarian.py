# master_librarian.py
# date created: 2026-09-05 17:48:00
# date modified: 2026-09-05 17:42:38
# tags: #librarian, #master_librarian, #governance, #orchestrator, #vault, #single_pass

"""
master_librarian.py — Master Vault Health & Governance Orchestrator.

Unifies format, tag, link, and index maintenance into a single-pass pipeline
driven by the canonical backlog_drainer engine.

Exports:
    audit_single_document()         — Audits and normalizes one document in a single read-transform-write pass.
    run_master_librarian_audit()    — Executes a batched audit pass using backlog_drainer.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any

import evelyn_config as cfg
from Evelyn.tools import (
    backlog_drainer,
    format_librarian,
    link_librarian,
    path_utils,
    vault_db,
)

logger = logging.getLogger("evelyn.master_librarian")


def audit_single_document(
    doc_path: str | None = None,
    vault_root: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Audit and normalize a single vault document in a single pass.

    Pipeline:
        1. Load content and frontmatter once.
        2. Format_Librarian: schema validation, flow arrays, clean icon brackets.
        3. Link_Librarian: spurious code array wrapping, bare attachments, alias hygiene.
        4. Atomic write via sibling temporary file + os.replace if changes occurred.
        5. Update vault_documents audit timestamps and log to librarian_activity_log.

    Args:
        doc_path: Optional relative path of document. If None, queries vault_db queue.
        vault_root: Optional vault root directory.
        dry_run: If True, simulates transformations without writing to disk or database.

    Returns:
        dict[str, Any]: Execution summary dict.
    """
    t0 = time.time()
    root = vault_root or getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")

    if not doc_path:
        docs = vault_db.fetch_next_document_for_librarian_audit(1)
        if not docs:
            return {"status": "empty", "message": "No documents found in vault DB."}
        doc_info = docs[0]
        doc_path = doc_info["path"]
        title = doc_info.get("title", "")
    else:
        doc_info = vault_db.get_document(doc_path)
        title = doc_info.get("title", "") if doc_info else os.path.basename(doc_path)

    if vault_root:
        abs_path = os.path.join(root, doc_path)
    else:
        try:
            abs_path = str(path_utils.to_vault_abspath(doc_path))
        except (ValueError, TypeError):
            abs_path = os.path.join(root, doc_path)

    if not os.path.exists(abs_path):
        if not dry_run:
            vault_db.update_document_librarian_audit(doc_path)
        return {"status": "error", "path": doc_path, "message": "File not found on disk."}

    try:
        with open(abs_path, encoding="utf-8") as f:
            original_content = f.read()
    except OSError as e:
        if not dry_run:
            vault_db.update_document_librarian_audit(doc_path)
        return {"status": "error", "path": doc_path, "message": f"Read error: {e}"}

    pre_hash = hashlib.sha256(original_content.encode("utf-8")).hexdigest()
    content = original_content

    # 1. Format Librarian pass
    format_changed, content, format_details = format_librarian.audit_document_format(
        content, path=doc_path
    )

    # 2. Link Librarian pass
    link_changed, content, link_details = link_librarian.audit_document_links(
        content, path=doc_path, vault_root=root
    )

    post_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    modified = pre_hash != post_hash

    ghost_count = link_details.get("ghost_links_count", 0)

    # Build actions summary for activity log
    actions = []
    if format_changed:
        actions.extend(format_details.get("format_fixes", ["format_updated"]))
    if link_changed:
        actions.extend(link_details.get("actions", ["links_updated"]))

    if modified and not dry_run:
        # Atomic sibling file replacement
        tmp_path = f"{abs_path}.tmp_{os.getpid()}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, abs_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        new_mtime = os.path.getmtime(abs_path)
        vault_db.update_document_librarian_audit(
            doc_path, ghost_count=ghost_count, mtime=new_mtime
        )

        category = (
            os.path.dirname(doc_path).split("/")[0] if "/" in doc_path else "General"
        )
        # Extract short excerpt from content body (up to 300 chars)
        lines = [
            l.strip()
            for l in content.splitlines()
            if l.strip() and not l.startswith("---")
        ]
        excerpt = " ".join(lines[:3])[:300] if lines else ""

        summary = f"Tidied frontmatter and links in '{title or doc_path}': {', '.join(actions[:3])}"
        vault_db.log_librarian_activity(
            path=doc_path,
            title=title or doc_path,
            category=category,
            actions=actions,
            summary=summary,
            excerpt=excerpt,
        )
        logger.info(
            f"[MASTER LIBRARIAN] Cleaned '{doc_path}' ({len(actions)} actions)."
        )
    elif not dry_run:
        vault_db.update_document_librarian_audit(doc_path, ghost_count=ghost_count)

    elapsed_ms = int((time.time() - t0) * 1000)

    return {
        "status": "ok",
        "path": doc_path,
        "modified": modified,
        "changed": modified,
        "actions": actions,
        "elapsed_ms": elapsed_ms,
        "format_details": format_details,
        "link_details": link_details,
    }


def run_master_librarian_audit(
    batch_size: int = 5,
    max_batches: int = 1,
    deadline: float | None = None,
    auto_re_enqueue: bool = True,
) -> backlog_drainer.DrainResult:
    """Execute a batched Master Librarian audit run over the vault documents queue.

    Args:
        batch_size: Documents per batch.
        max_batches: Maximum batches per idle window (1 by default).
        deadline: Optional epoch deadline timestamp.
        auto_re_enqueue: Whether to re-enqueue in task_manager when yielding.

    Returns:
        backlog_drainer.DrainResult: Outcome summary.
    """
    drain_cfg = backlog_drainer.DrainConfig(
        batch_size=batch_size,
        max_batches=max_batches,
        deadline=deadline,
        auto_re_enqueue=auto_re_enqueue,
        manage_task_lifecycle=True,
    )

    def _fetch(limit: int) -> list[dict[str, Any]]:
        return vault_db.fetch_next_document_for_librarian_audit(limit)

    def _process(doc: dict[str, Any]) -> None:
        path = doc["path"]
        audit_single_document(path)

    return backlog_drainer.drain_backlog(
        task_name="master_librarian",
        fetch_batch_fn=_fetch,
        process_item_fn=_process,
        config=drain_cfg,
    )
