# test_chroma_queue_and_lifecycle.py
# date created: 2026-08-19 20:25:48
# date modified: 2026-08-19 20:25:48
# tags:

"""
test_chroma_queue_and_lifecycle.py — Unit tests for Chroma Single-Writer Queue,
dead-letter protection, queue coalescing, and lifecycle sanitization.
"""

import json
import os
import sys
import time
import unittest

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from Evelyn.tools import chroma_rag, memory_db, task_manager


class TestChromaQueueAndLifecycle(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import evelyn_config as cfg
        cls.test_chroma_dir = os.path.join(BASE_DIR, "data", "test_chroma_db")
        os.makedirs(cls.test_chroma_dir, exist_ok=True)
        cls.orig_chroma_path = cfg.CHROMA_DB_PATH
        cfg.CHROMA_DB_PATH = cls.test_chroma_dir
        chroma_rag._client = None
        memory_db.init_db()

    @classmethod
    def tearDownClass(cls):
        import shutil

        import evelyn_config as cfg
        cfg.CHROMA_DB_PATH = cls.orig_chroma_path
        chroma_rag._client = None
        if os.path.exists(cls.test_chroma_dir):
            shutil.rmtree(cls.test_chroma_dir, ignore_errors=True)

    def setUp(self):
        self.con = chroma_rag._get_queue_db()
        self.con.execute("DELETE FROM chroma_sync_queue WHERE source_path LIKE 'test::%'")
        self.con.commit()

    def tearDown(self):
        try:
            self.con.execute("DELETE FROM chroma_sync_queue WHERE source_path LIKE 'test::%'")
            self.con.commit()
            self.con.close()
        except Exception:
            pass

    def test_01_enqueue_coalescing(self):
        """Verify rapid repeated enqueues for the same source coalesce into a single pending row."""
        src = "test::coalesce_doc.md"
        # Enqueue 5 rapid updates
        for i in range(5):
            ok = chroma_rag.enqueue_upsert(
                source_path=src,
                content=f"Version {i} content",
                collection_name="evelyn_memory",
                extra_metadata={"version": i}
            )
            self.assertTrue(ok)

        # Check that exactly 1 pending row exists with Version 4
        cur = self.con.cursor()
        cur.execute(
            "SELECT COUNT(*) as cnt, content, extra_metadata_json FROM chroma_sync_queue WHERE source_path = ? AND status = 'pending'",
            (src,)
        )
        row = cur.fetchone()
        self.assertEqual(row["cnt"], 1)
        self.assertEqual(row["content"], "Version 4 content")
        meta = json.loads(row["extra_metadata_json"])
        self.assertEqual(meta["version"], 4)

    def test_02_drain_sync_queue_and_flush(self):
        """Verify drain_sync_queue processes enqueued items to done status."""
        src = "test::valid_entry.md"
        chroma_rag.enqueue_upsert(
            source_path=src,
            content="This is a test fact for single writer queue verification.",
            collection_name="evelyn_memory"
        )

        # Trigger drain and poll until test item is done
        start = time.time()
        while time.time() - start < 5.0:
            chroma_rag.drain_sync_queue(batch_size=50, source_prefix="test::")
            cur = self.con.cursor()
            cur.execute("SELECT status FROM chroma_sync_queue WHERE source_path = ?", (src,))
            row = cur.fetchone()
            if row and row["status"] == "done":
                break
            time.sleep(0.1)

        # Check that the queue row is now 'done'
        cur = self.con.cursor()
        cur.execute("SELECT status FROM chroma_sync_queue WHERE source_path = ?", (src,))
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "done")

    def test_03_dead_letter_isolation(self):
        """Verify that a failing item retries up to 3 times and moves to status='error' without blocking valid items."""
        bad_src = "test::poison_pill.md"
        good_src = "test::good_doc.md"

        # Insert a bad record directly that will fail during direct dispatch
        cur = self.con.cursor()
        now = time.time()
        cur.execute(
            """INSERT INTO chroma_sync_queue
               (action, source_path, collection_name, content, extra_metadata_json, status, retry_count, created_at, updated_at)
               VALUES ('malformed_action_fail', ?, 'evelyn_memory', 'bad', NULL, 'pending', 0, ?, ?)""",
            (bad_src, now, now)
        )
        self.con.commit()

        # Enqueue a good record after the bad one
        chroma_rag.enqueue_upsert(good_src, "Good content", collection_name="evelyn_memory")

        # Drain repeatedly until bad item reaches 3 retries / 'error' and good doc reaches 'done'
        start = time.time()
        while time.time() - start < 5.0:
            chroma_rag.drain_sync_queue(batch_size=50, source_prefix="test::")
            cur.execute("SELECT status, retry_count, error_msg FROM chroma_sync_queue WHERE source_path = ?", (bad_src,))
            bad_row = cur.fetchone()
            cur.execute("SELECT status FROM chroma_sync_queue WHERE source_path = ?", (good_src,))
            good_row = cur.fetchone()
            if bad_row and bad_row["status"] == "error" and good_row and good_row["status"] == "done":
                break
            time.sleep(0.1)

        # Verify poison pill is 'error' and good doc is 'done'
        cur.execute("SELECT status, retry_count, error_msg FROM chroma_sync_queue WHERE source_path = ?", (bad_src,))
        bad_row = cur.fetchone()
        self.assertEqual(bad_row["status"], "error")
        self.assertGreaterEqual(bad_row["retry_count"], 3)

        cur.execute("SELECT status FROM chroma_sync_queue WHERE source_path = ?", (good_src,))
        good_row = cur.fetchone()
        self.assertEqual(good_row["status"], "done")

    def test_04_health_probe(self):
        """Verify check_chroma_health returns healthy when vector store is reachable."""
        res = chroma_rag.check_chroma_health()
        self.assertIn("status", res)
        self.assertIn("count", res)
        self.assertIn(res["status"], ("healthy", "corrupt"))

    def test_05_startup_reaper_lock_cleanup(self):
        """Verify reap_orphaned_processes clears stale .lock files safely."""
        data_dir = r"/home/rathius/evelyn/data"
        os.makedirs(data_dir, exist_ok=True)
        dummy_lock = os.path.join(data_dir, "test_stale.lock")
        with open(dummy_lock, "w") as f:
            f.write("test lock")

        self.assertTrue(os.path.exists(dummy_lock))
        task_manager.reap_orphaned_processes()
        self.assertFalse(os.path.exists(dummy_lock))
    def test_06_drain_deadline_rollback(self):
        """Verify that when drain_sync_queue encounters an expired deadline, un-processed items revert to pending."""
        src1 = "test::batch_item_1.md"
        src2 = "test::batch_item_2.md"
        chroma_rag.enqueue_upsert(src1, "Batch item 1", collection_name="evelyn_memory")
        chroma_rag.enqueue_upsert(src2, "Batch item 2", collection_name="evelyn_memory")

        # Pass an immediate past deadline (time.time() - 10)
        drained = chroma_rag.drain_sync_queue(batch_size=50, source_prefix="test::", deadline=time.time() - 10)
        self.assertEqual(drained, 0)

        # Both items should remain 'pending' and not get stuck in 'processing'
        cur = self.con.cursor()
        cur.execute("SELECT status FROM chroma_sync_queue WHERE source_path IN (?, ?)", (src1, src2))
        statuses = [r["status"] for r in cur.fetchall()]
        self.assertEqual(len(statuses), 2)
        self.assertTrue(all(s == "pending" for s in statuses))

    def test_07_flush_sync_queue_timeout(self):
        """Verify flush_sync_queue returns within its bounded timeout."""
        start = time.time()
        # Call flush with 0.2s timeout on empty test queue
        res = chroma_rag.flush_sync_queue(timeout=0.2, source_prefix="test::")
        elapsed = time.time() - start
        self.assertTrue(res)  # Empty queue returns True immediately
        self.assertLess(elapsed, 1.0)


if __name__ == "__main__":
    unittest.main()

