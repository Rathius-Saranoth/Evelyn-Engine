#!/usr/bin/env python3
# audit_and_remediate_past_merges.py
# date created: 2026-10-12 23:35:00
# date modified: 2026-10-17 12:44:40
# tags: #memory, #remediation, #merges, #splits, #deduplication

"""Audit and remediate historical memory merges in Evelyn's SQLite database.

Deconstructs past over-merging by:
1. Identifying orphaned secondary facts from the 159 master entries that were merged and then split.
2. Unmerging bloated compound master entries (> 35 words) back into their pristine atomic secondaries.
3. Automatically collapsing exact verbatim duplicates via fast_deduplicate_exact_matches.
4. Safely enqueuing Chroma vector updates (enqueue_upsert / enqueue_delete) following the single-writer rule.

Usage:
    python scripts/audit_and_remediate_past_merges.py --report
    python scripts/audit_and_remediate_past_merges.py --recover-orphaned [--execute]
    python scripts/audit_and_remediate_past_merges.py --unmerge-bloated [--word-threshold 35] [--execute]
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import evelyn_config as cfg
from Evelyn.tools import chroma_rag, fact_deduplicator, memory_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("evelyn.remediate_merges")


def generate_report(con: sqlite3.Connection, word_threshold: int = 35) -> dict:
    """Generate comprehensive audit metrics of past merges and splits."""
    cur = con.cursor()

    # 1. Total secondaries
    total_sec = cur.execute(
        "SELECT count(*) FROM context_entries WHERE merged_into_id IS NOT NULL"
    ).fetchone()[0]

    # 2. Master entries that were merged and subsequently split
    split_masters = cur.execute("""
        SELECT DISTINCT m.id as master_id
        FROM context_entries m
        JOIN context_entries s ON s.merged_into_id = m.id
        WHERE m.id IN (SELECT split_from_id FROM context_entries WHERE split_from_id IS NOT NULL)
    """).fetchall()

    orphaned_facts = []
    for (mid,) in split_masters:
        children = cur.execute(
            "SELECT id, observation FROM context_entries WHERE split_from_id = ?", (mid,)
        ).fetchall()
        secondaries = cur.execute(
            "SELECT id, observation, date, subject, tags FROM context_entries WHERE merged_into_id = ?", (mid,)
        ).fetchall()

        children_text = " ".join([c["observation"].lower() for c in children])
        for sec in secondaries:
            jaccard = fact_deduplicator.calculate_token_jaccard(sec["observation"].lower(), children_text)
            if jaccard < 0.20:
                orphaned_facts.append(dict(sec))

    # 3. Live master entries holding secondaries
    live_masters = cur.execute("""
        SELECT m.id, m.observation, count(s.id) as sec_count,
               (length(m.observation) - length(replace(m.observation, ' ', '')) + 1) as word_count
        FROM context_entries m
        JOIN context_entries s ON s.merged_into_id = m.id
        WHERE m.status = 'live'
        GROUP BY m.id
    """).fetchall()

    bloated_masters = [m for m in live_masters if m["word_count"] > word_threshold]

    return {
        "total_secondaries": total_sec,
        "split_masters_count": len(split_masters),
        "orphaned_facts_count": len(orphaned_facts),
        "orphaned_facts": orphaned_facts,
        "live_masters_count": len(live_masters),
        "bloated_masters_count": len(bloated_masters),
        "bloated_masters": bloated_masters,
    }


def recover_orphaned_facts(con: sqlite3.Connection, execute: bool = False) -> int:
    """Restore orphaned secondary facts whose content was omitted when the master entry was split."""
    report = generate_report(con)
    orphaned = report["orphaned_facts"]

    logger.info(f"Identified {len(orphaned)} orphaned secondary facts to recover.")
    if not execute:
        logger.info("[DRY-RUN] Pass --execute to restore these records.")
        return len(orphaned)

    cur = con.cursor()
    recovered = 0
    now = time.time()

    for sec in orphaned:
        sid = sec["id"]
        cur.execute(
            """UPDATE context_entries
               SET status = 'live', merged_into_id = NULL, updated_at = ?
               WHERE id = ?""",
            (now, sid),
        )
        recovered += 1

    con.commit()

    # Enqueue Chroma indexing in separate pass after write lock is released
    for sec in orphaned:
        sid = sec["id"]
        try:
            content = (
                f"Subject: {sec.get('subject', 'Unknown')}\n"
                f"Date: {sec.get('date') or 'Unknown'}\n"
                f"Tags: {sec.get('tags') or ''}\n"
                f"Observation: {sec['observation']}"
            )
            chroma_rag.enqueue_upsert(
                source_path=f"sqlite::context_entry::{sid}",
                content=content,
                extra_metadata={
                    "rag_priority": "high",
                    "rag_pinned": False,
                    "subject": sec.get("subject", ""),
                    "tags": sec.get("tags") or "",
                },
                collection_name=cfg.CHROMA_MEMORY_COLLECTION,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Could not enqueue Chroma upsert for #{sid}: {e}")

    logger.info(f"Successfully recovered and enqueued {recovered} orphaned fact(s).")
    return recovered



def unmerge_bloated_masters(con: sqlite3.Connection, word_threshold: int = 35, execute: bool = False) -> int:
    """Deconstruct bloated compound master entries back into their atomic secondary entries."""
    report = generate_report(con, word_threshold=word_threshold)
    bloated = report["bloated_masters"]

    logger.info(f"Found {len(bloated)} bloated master entries exceeding {word_threshold} words.")
    if not execute:
        logger.info("[DRY-RUN] Pass --execute to deconstruct these bloated entries.")
        return len(bloated)

    cur = con.cursor()
    unmerged_count = 0
    secondaries_revived = 0
    now = time.time()

    all_secs_to_upsert: list[dict] = []
    all_masters_to_delete: list[int] = []

    for m in bloated:
        mid = m["id"]
        # Fetch all secondaries attached to this master
        secs = cur.execute(
            "SELECT id, subject, date, tags, observation FROM context_entries WHERE merged_into_id = ?",
            (mid,),
        ).fetchall()

        if not secs:
            continue

        # Revive secondaries
        for s in secs:
            sid = s["id"]
            cur.execute(
                """UPDATE context_entries
                   SET status = 'live', merged_into_id = NULL, updated_at = ?
                   WHERE id = ?""",
                (now, sid),
            )
            secondaries_revived += 1
            all_secs_to_upsert.append(dict(s))

        # Soft-delete the bloated synthesized master entry
        cur.execute(
            "UPDATE context_entries SET status = 'deleted', updated_at = ? WHERE id = ?",
            (now, mid),
        )
        unmerged_count += 1
        all_masters_to_delete.append(mid)

    con.commit()
    logger.info(f"Unmerged {unmerged_count} bloated master(s), reviving {secondaries_revived} atomic secondary fact(s).")

    # Enqueue Chroma operations after SQLite write transaction is committed
    for s in all_secs_to_upsert:
        sid = s["id"]
        try:
            content = (
                f"Subject: {s['subject'] or 'Unknown'}\n"
                f"Date: {s['date'] or 'Unknown'}\n"
                f"Tags: {s['tags'] or ''}\n"
                f"Observation: {s['observation']}"
            )
            chroma_rag.enqueue_upsert(
                source_path=f"sqlite::context_entry::{sid}",
                content=content,
                extra_metadata={
                    "rag_priority": "high",
                    "rag_pinned": False,
                    "subject": s["subject"] or "",
                    "tags": s["tags"] or "",
                },
                collection_name=cfg.CHROMA_MEMORY_COLLECTION,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Could not enqueue Chroma upsert for #{sid}: {e}")

    for mid in all_masters_to_delete:
        try:
            chroma_rag.enqueue_delete(
                source_path=f"sqlite::context_entry::{mid}",
                collection_name=cfg.CHROMA_MEMORY_COLLECTION,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Could not enqueue Chroma deletion for #{mid}: {e}")

    # Run fast exact deduplication to collapse any identical twins among revived facts
    collapsed = fact_deduplicator.fast_deduplicate_exact_matches(con=con)
    logger.info(f"Fast exact deduplication collapsed {collapsed} verbatim duplicate(s).")

    return unmerged_count


def main():
    parser = argparse.ArgumentParser(description="Audit and remediate past memory merges.")
    parser.add_argument("--report", action="store_true", help="Print audit report.")
    parser.add_argument("--recover-orphaned", action="store_true", help="Recover dropped secondaries from split masters.")
    parser.add_argument("--unmerge-bloated", action="store_true", help="Unmerge bloated live master entries.")
    parser.add_argument("--word-threshold", type=int, default=35, help="Word count threshold for bloated entries (default: 35).")
    parser.add_argument("--execute", action="store_true", help="Execute database mutations (default: dry-run).")
    args = parser.parse_args()

    con = memory_db.get_db()
    try:
        if args.report or (not args.recover_orphaned and not args.unmerge_bloated):
            rep = generate_report(con, word_threshold=args.word_threshold)
            print("\n=== PAST MERGE AUDIT REPORT ===")
            print(f"Total Soft-Deleted Secondaries in DB: {rep['total_secondaries']}")
            print(f"Master Entries Merged & Later Split: {rep['split_masters_count']}")
            print(f"Orphaned Secondary Facts Dropped During Splits: {rep['orphaned_facts_count']}")
            print(f"Live Master Entries Holding Merged Facts: {rep['live_masters_count']}")
            print(f"Bloated Master Entries (> {args.word_threshold} words): {rep['bloated_masters_count']}")
            print("================================\n")

        if args.recover_orphaned:
            recover_orphaned_facts(con, execute=args.execute)

        if args.unmerge_bloated:
            unmerge_bloated_masters(con, word_threshold=args.word_threshold, execute=args.execute)

    finally:
        con.close()


if __name__ == "__main__":
    main()
