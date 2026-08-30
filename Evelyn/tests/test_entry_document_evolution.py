# test_entry_document_evolution.py
# date created: 2026-08-30
# date modified: 2026-08-30 08:06:00
# tags: #test, #profile_evolver, #evolution, #database, #memory_db

"""Unit tests for per-document evolution tracking, cross-document isolation, and dirty record re-qualification."""

import os
import sqlite3
import sys
import tempfile
import time
import unittest

# Ensure repo root and Evelyn/tools are on python path
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
tools_dir = os.path.join(repo_root, "Evelyn/tools")
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
if tools_dir not in sys.path:
    sys.path.insert(0, tools_dir)

import db_migrator
import memory_db

import evelyn_config as cfg


class TestEntryDocumentEvolution(unittest.TestCase):
    def setUp(self):
        """Create a temporary SQLite memory database for isolated testing."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_memory.db")
        self.orig_db_path = cfg.MEMORY_DB_PATH
        cfg.MEMORY_DB_PATH = self.db_path

        # Initialize schema
        memory_db.init_db()

    def tearDown(self):
        """Restore original config and clean up temporary directory."""
        cfg.MEMORY_DB_PATH = self.orig_db_path
        self.temp_dir.cleanup()

    def test_foreign_keys_pragma_enabled(self):
        """Verify get_db enables foreign keys and CASCADE deletes work."""
        con = memory_db.get_db()
        fk_status = con.execute("PRAGMA foreign_keys").fetchone()[0]
        con.close()
        self.assertEqual(fk_status, 1)

        # Insert entry
        eid = memory_db.insert_entry(
            category=f"Cat01-{cfg.SUBJECT_CODE_USER}",
            subject=cfg.USER_NAME,
            observation="Testing cascade delete.",
        )
        memory_db.touch_entry_evolved(eid, cfg.PERSONA_FILE_USER, 1000.0)

        # Check evolution record exists
        evolutions = memory_db.get_entry_document_evolutions(eid)
        self.assertIn(cfg.PERSONA_FILE_USER, evolutions)

        # Soft delete excludes entry from live queries
        memory_db.delete_entry(eid)
        un_evolved_live = memory_db.get_entries_by_category_for_document(f"Cat01-{cfg.SUBJECT_CODE_USER}", cfg.PERSONA_FILE_USER, status="live")
        self.assertEqual(len(un_evolved_live), 0)

        # Hard delete entry from context_entries
        memory_db.hard_delete_entry(eid)

        # Verify entry_document_evolution row was cascaded via FK ON DELETE CASCADE
        con = memory_db.get_db()
        row = con.execute("SELECT * FROM entry_document_evolution WHERE entry_id = ?", (eid,)).fetchone()
        con.close()
        self.assertIsNone(row)

    def test_cross_document_isolation(self):
        """Verify that stamping Document A does NOT mark entry evolved for Document B or C."""
        cat = f"Cat06-{cfg.SUBJECT_CODE_USER}"
        eid = memory_db.insert_entry(
            category=cat,
            subject=cfg.USER_NAME,
            observation="Ricky and Evelyn share deep collaborative dynamics.",
        )

        doc_asst = cfg.PERSONA_FILE_ASSISTANT
        doc_user = cfg.PERSONA_FILE_USER
        doc_dirs = cfg.PERSONA_FILE_DIRECTIVES

        # Initially, entry qualifies for all 3 documents
        entries_asst = memory_db.get_entries_by_category_for_document(cat, doc_asst)
        entries_user = memory_db.get_entries_by_category_for_document(cat, doc_user)
        entries_dirs = memory_db.get_entries_by_category_for_document(cat, doc_dirs)

        self.assertEqual(len(entries_asst), 1)
        self.assertEqual(len(entries_user), 1)
        self.assertEqual(len(entries_dirs), 1)

        # Stamp evolved for Assistant Persona only
        stamp_time = 1000.0
        memory_db.touch_entry_evolved(eid, doc_asst, stamp_time)

        # Document A (Assistant) should now have 0 qualifying entries
        entries_asst_after = memory_db.get_entries_by_category_for_document(cat, doc_asst)
        self.assertEqual(len(entries_asst_after), 0)

        # Document B and Document C must STILL have 1 qualifying entry!
        entries_user_after = memory_db.get_entries_by_category_for_document(cat, doc_user)
        entries_dirs_after = memory_db.get_entries_by_category_for_document(cat, doc_dirs)
        self.assertEqual(len(entries_user_after), 1)
        self.assertEqual(len(entries_dirs_after), 1)
        self.assertEqual(entries_user_after[0]["id"], eid)
        self.assertEqual(entries_dirs_after[0]["id"], eid)

    def test_dirty_record_requalification(self):
        """Verify that modifying an entry (updated_at > evolved_at) re-qualifies it for evolution."""
        cat = f"Cat01-{cfg.SUBJECT_CODE_USER}"
        eid = memory_db.insert_entry(
            category=cat,
            subject=cfg.USER_NAME,
            observation="Original fact observation.",
        )

        doc_user = cfg.PERSONA_FILE_USER

        # Evolve entry at t = 1000.0
        memory_db.touch_entry_evolved(eid, doc_user, 1000.0)

        # Should not qualify
        un_evolved = memory_db.get_entries_by_category_for_document(cat, doc_user)
        self.assertEqual(len(un_evolved), 0)

        # User edits context entry during review at t = 2000.0
        time.sleep(0.01)
        memory_db.update_entry(eid, observation="Updated refined observation.")

        # Entry should now re-qualify because updated_at > evolved_at
        re_qualified = memory_db.get_entries_by_category_for_document(cat, doc_user)
        self.assertEqual(len(re_qualified), 1)
        self.assertEqual(re_qualified[0]["observation"], "Updated refined observation.")

    def test_migration_000_006_027_backfill(self):
        """Verify migration 000.006.027 creates the table and backfills legacy last_evolved_at."""
        # Create an un-migrated database without entry_document_evolution
        legacy_db_path = os.path.join(self.temp_dir.name, "legacy_memory.db")
        con = sqlite3.connect(legacy_db_path)
        con.executescript(db_migrator.BASELINE_MEMORY_SQL)
        con.execute("PRAGMA foreign_keys = ON")

        # Insert legacy entries with last_evolved_at
        now = time.time()
        con.execute(
            "INSERT INTO context_entries (category, subject, observation, created_at, updated_at, last_evolved_at) VALUES (?, ?, ?, ?, ?, ?)",
            (f"Cat01-{cfg.SUBJECT_CODE_ASSISTANT}", cfg.ASSISTANT_NAME, "Evelyn legacy trait", now, now, now),
        )
        con.execute(
            "INSERT INTO context_entries (category, subject, observation, created_at, updated_at, last_evolved_at) VALUES (?, ?, ?, ?, ?, ?)",
            (f"Cat01-{cfg.SUBJECT_CODE_USER}", cfg.USER_NAME, "Ricky legacy trait", now, now, now),
        )
        con.execute(
            "INSERT INTO context_entries (category, subject, observation, created_at, updated_at, last_evolved_at) VALUES (?, ?, ?, ?, ?, ?)",
            (f"Cat16-{cfg.SUBJECT_CODE_USER}", cfg.USER_NAME, "Directives legacy routine", now, now, now),
        )
        con.commit()

        # Run migration callable
        db_migrator.migrate_000_006_027_entry_document_evolution(con, {"memory": legacy_db_path}, cfg)

        # Check entry_document_evolution table contents
        rows = con.execute("SELECT entry_id, document_name, evolved_at FROM entry_document_evolution ORDER BY entry_id ASC").fetchall()
        con.close()

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0][1], cfg.PERSONA_FILE_ASSISTANT)
        self.assertEqual(rows[1][1], cfg.PERSONA_FILE_USER)
        self.assertEqual(rows[2][1], cfg.PERSONA_FILE_DIRECTIVES)


if __name__ == "__main__":
    unittest.main()
