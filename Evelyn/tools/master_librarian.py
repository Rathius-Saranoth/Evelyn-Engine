# master_librarian.py
# date created: 2026-09-05 17:48:00
# date modified: 2026-09-05 20:02:07
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
    index_librarian,
    link_librarian,
    path_utils,
    tag_librarian,
    vault_db,
)

logger = logging.getLogger("evelyn.master_librarian")


def audit_single_document(
    doc_path: str | None = None,
    vault_root: str | None = None,
    dry_run: bool = False,
    include_tags: bool = True,
    enable_llm_tags: bool = False,
    inherit_parent_tags: bool = True,
    auto_create_ghost_stubs: bool = True,
) -> dict[str, Any]:
    """Audit and normalize a single vault document in a single read-transform-write pass.

    Pipeline:
        1. Load content and frontmatter once.
        2. Format_Librarian: schema validation, flow arrays, clean icon brackets.
        3. Tag_Librarian: casing normalization, noise prefix removal, parent collection inheritance,
           and optional Tag RAG evaluation.
        4. Link_Librarian: spurious code array wrapping, bare attachments, parent breadcrumbs, ghost links.
        5. Ghost Link Resolution: Tier 1 stub generation or Tier 2 proposal logging.
        6. Atomic write via sibling temporary file + os.replace if changes occurred.
        7. Update vault_documents audit timestamps, tags, and log to librarian_activity_log.

    Args:
        doc_path: Optional relative path of document. If None, queries vault_db queue.
        vault_root: Optional vault root directory.
        dry_run: If True, simulates transformations without writing to disk or database.
        include_tags: Whether to include tag normalization pass.
        enable_llm_tags: Whether to invoke Ollama for semantic tagging.
        inherit_parent_tags: Whether to inherit domain tags from parent _index.md.
        auto_create_ghost_stubs: Whether to synthesize Tier 1 ghost link stubs.

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

    # 2. Tag Librarian pass (with collection inheritance)
    tag_changed = False
    tag_details: dict[str, Any] = {}
    if include_tags:
        parent_tags: list[str] = []
        if inherit_parent_tags and "/" in doc_path:
            dirpath = os.path.dirname(doc_path)
            parent_index = os.path.join(root, dirpath, "_index.md")
            if not os.path.exists(parent_index):
                folder_name = os.path.basename(dirpath)
                parent_index = os.path.join(root, dirpath, f"{folder_name}_index.md")
            if os.path.exists(parent_index):
                try:
                    with open(parent_index, encoding="utf-8") as pif:
                        p_meta, _ = tag_librarian.parse_frontmatter(pif.read())
                        raw_pt = p_meta.get("tags", [])
                        if isinstance(raw_pt, list):
                            parent_tags = [str(t) for t in raw_pt if str(t).lower() not in ("moc", "index")]
                except OSError:
                    pass

        tag_changed, content, tag_details = tag_librarian.audit_document_tags(
            content,
            path=doc_path,
            vault_root=root,
            enable_llm=enable_llm_tags,
            parent_tags=parent_tags,
        )

    # 3. Link Librarian pass
    link_changed, content, link_details = link_librarian.audit_document_links(
        content, path=doc_path, vault_root=root
    )

    # 4. Optional Tier 1 Ghost Link Stub Synthesis
    ghost_targets = link_details.get("ghost_targets", [])
    stubs_created = []
    if auto_create_ghost_stubs and ghost_targets and not dry_run:
        min_refs = getattr(cfg, "LIBRARIAN_GHOST_STUB_MIN_REFS", 2)
        for gt in ghost_targets:
            res = link_librarian.create_ghost_link_stub(
                target_name=gt,
                source_path=doc_path,
                context_excerpt=content[:250],
                vault_root=root,
                min_refs=min_refs,
            )
            if res.get("status") == "created_stub":
                stubs_created.append(gt)

    # 5. Index Librarian pass (Folder Table of Contents synchronization)
    index_changed = False
    index_details: dict[str, Any] = {}
    is_index_doc = os.path.basename(doc_path).endswith("_index.md") or os.path.basename(doc_path) == "_index.md"
    parent_index_synced_count = 0
    if is_index_doc:
        dirpath = os.path.dirname(doc_path)
        index_changed, content, index_details = index_librarian.audit_folder_index(
            folder_relpath=dirpath,
            vault_root=root,
            content=content,
            dry_run=dry_run,
        )
    elif "/" in doc_path:
        dirpath = os.path.dirname(doc_path)
        possible_idx1 = os.path.join(root, dirpath, "_index.md")
        possible_idx2 = os.path.join(root, dirpath, f"{os.path.basename(dirpath)}_index.md")
        if os.path.exists(possible_idx1) or os.path.exists(possible_idx2):
            idx_changed, _, idx_details = index_librarian.audit_folder_index(
                folder_relpath=dirpath,
                vault_root=root,
                dry_run=dry_run,
            )
            if idx_changed:
                parent_index_synced_count = len(idx_details.get("added_notes", []))

    post_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    modified = pre_hash != post_hash

    ghost_count = link_details.get("ghost_links_count", 0)

    # Build actions summary for activity log
    actions = []
    if format_changed:
        actions.extend(format_details.get("format_fixes", ["format_updated"]))
    if tag_changed:
        actions.append(f"tags_updated:{len(tag_details.get('final_tags', []))}")
    if link_changed:
        actions.extend(link_details.get("actions", ["links_updated"]))
    if stubs_created:
        actions.append(f"synthesized_stubs:{len(stubs_created)}")
    if index_changed:
        actions.append(f"index_synced:{len(index_details.get('added_notes', []))}")
    if parent_index_synced_count:
        actions.append(f"parent_index_synced:{parent_index_synced_count}")

    tags_str = ", ".join(tag_details.get("final_tags", [])) if tag_details else None

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
            doc_path,
            ghost_count=ghost_count,
            tags=tags_str,
            mtime=new_mtime,
        )

        category = (
            os.path.dirname(doc_path).split("/")[0] if "/" in doc_path else "General"
        )
        lines = [
            l.strip()
            for l in content.splitlines()
            if l.strip() and not l.startswith("---")
        ]
        excerpt = " ".join(lines[:3])[:300] if lines else ""

        summary = f"Tended note '{title or doc_path}': {', '.join(actions[:3])}"
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
        vault_db.update_document_librarian_audit(
            doc_path,
            ghost_count=ghost_count,
            tags=tags_str,
        )

    elapsed_ms = int((time.time() - t0) * 1000)

    return {
        "status": "ok",
        "path": doc_path,
        "modified": modified,
        "changed": modified,
        "actions": actions,
        "elapsed_ms": elapsed_ms,
        "format_details": format_details,
        "tag_details": tag_details,
        "link_details": link_details,
        "index_details": index_details,
        "stubs_created": stubs_created,
    }


def run_master_librarian_audit(
    batch_size: int = 5,
    max_batches: int = 1,
    deadline: float | None = None,
    auto_re_enqueue: bool = True,
    cooldown_seconds: int | None = None,
    folder_cap: int | None = None,
    include_tags: bool = True,
    enable_llm_tags: bool = False,
) -> backlog_drainer.DrainResult:
    """Execute a batched Master Librarian audit run over the vault documents queue.

    Args:
        batch_size: Documents per batch.
        max_batches: Maximum batches per idle window (1 by default).
        deadline: Optional epoch deadline timestamp.
        auto_re_enqueue: Whether to re-enqueue in task_manager when yielding.
        cooldown_seconds: Minimum seconds before re-auditing a clean note.
        folder_cap: Maximum documents processed per folder cluster per run.
        include_tags: Whether to include tag normalization.
        enable_llm_tags: Whether to invoke Ollama for semantic Tag RAG.

    Returns:
        backlog_drainer.DrainResult: Outcome summary.
    """
    cooldown = (
        cooldown_seconds
        if cooldown_seconds is not None
        else getattr(cfg, "LIBRARIAN_AUDIT_COOLDOWN_SECONDS", 3600)
    )
    cap = (
        folder_cap
        if folder_cap is not None
        else getattr(cfg, "LIBRARIAN_FOLDER_BATCH_CAP", 5)
    )

    def _get_folder(item: dict[str, Any]) -> str:
        p = item.get("path", "")
        return os.path.dirname(p) if "/" in p else ""

    drain_cfg = backlog_drainer.DrainConfig(
        batch_size=batch_size,
        max_batches=max_batches,
        deadline=deadline,
        auto_re_enqueue=auto_re_enqueue,
        manage_task_lifecycle=True,
        group_by_fn=_get_folder,
        max_items_per_group=cap,
    )

    def _fetch(limit: int) -> list[dict[str, Any]]:
        return vault_db.fetch_next_document_for_librarian_audit(
            batch_size=limit,
            cooldown_seconds=cooldown,
        )

    def _process(doc: dict[str, Any]) -> None:
        path = doc["path"]
        audit_single_document(
            doc_path=path,
            include_tags=include_tags,
            enable_llm_tags=enable_llm_tags,
        )

    return backlog_drainer.drain_backlog(
        task_name="master_librarian",
        fetch_batch_fn=_fetch,
        process_item_fn=_process,
        config=drain_cfg,
    )
