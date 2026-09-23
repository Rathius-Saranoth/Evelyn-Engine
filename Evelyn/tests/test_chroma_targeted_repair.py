# test_chroma_targeted_repair.py
# date created: 2026-09-22 19:05:00
# date modified: 2026-09-22 19:24:25
# tags: #test, #chroma, #repair, #regression, #vector

"""Regression cover for targeted Chroma repair.

Before v000.006.198 a single failed health probe caused `repair_corrupted_chroma()` to
`shutil.rmtree` the entire vector store and re-sync everything — destroying healthy
collections to fix one bad segment, unattended, at boot. Repair must now touch only the
collections named, must refuse any collection it cannot regenerate, and must never remove
the store directory.
"""

import os

import pytest

from Evelyn.tools import chroma_rag


@pytest.fixture(autouse=True)
def _no_real_children(monkeypatch):
    """Fail loudly rather than touching the production vector store.

    `check_chroma_health()` spawns a child that calls `get_or_create_collection`, which
    *creates* a collection that does not exist. A test that patched the wrong seam once
    left two empty collections behind in the live store (AGENTS.md §2). Any unpatched
    spawn now raises instead; tests that need a child override this with their own patch.
    """
    def _refuse(*args, **kwargs):
        raise AssertionError(
            "test attempted to spawn a real probe child — patch _probe_batch or subprocess.run"
        )
    monkeypatch.setattr(chroma_rag.subprocess, "run", _refuse)
    monkeypatch.setattr(chroma_rag.subprocess, "Popen", _refuse)


@pytest.fixture
def spawned(monkeypatch):
    """Capture dispatched rebuild commands instead of running them."""
    calls = []
    monkeypatch.setattr(chroma_rag.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(chroma_rag.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    return calls


def test_repair_dispatches_only_the_named_collection(spawned):
    """Only the broken collection is rebuilt; the store directory is left in place."""
    dispatched = chroma_rag.repair_corrupted_chroma(collections=["evelyn_memory"], background=True)

    assert dispatched == ["evelyn_memory"]
    assert len(spawned) == 1
    cmd = spawned[0]
    assert "--collection" in cmd and cmd[cmd.index("--collection") + 1] == "evelyn_memory"
    assert "--execute" in cmd
    assert cmd[1].endswith("rebuild_chroma_collection.py")
    # The healthy collections live in this directory; it must survive a repair.
    assert os.path.isdir(chroma_rag._CHROMA_DIR)


def test_repair_refuses_a_collection_it_cannot_regenerate(spawned):
    """A collection with no rebuild strategy is reported, never dropped."""
    dispatched = chroma_rag.repair_corrupted_chroma(collections=["evelyn_media"], background=True)

    assert dispatched == []
    assert spawned == []
    assert os.path.isdir(chroma_rag._CHROMA_DIR)


def test_repair_is_a_no_op_when_nothing_is_broken(spawned):
    """An empty unhealthy list must not trigger any rebuild."""
    assert chroma_rag.repair_corrupted_chroma(collections=[], background=True) == []
    assert spawned == []


def test_repair_skips_unrebuildable_but_still_fixes_the_rest(spawned):
    """A mixed list rebuilds what it can and leaves the rest alone."""
    dispatched = chroma_rag.repair_corrupted_chroma(
        collections=["evelyn_media", "evelyn_tag_taxonomy"], background=True
    )

    assert dispatched == ["evelyn_tag_taxonomy"]
    assert len(spawned) == 1
    assert spawned[0][spawned[0].index("--collection") + 1] == "evelyn_tag_taxonomy"


def test_probe_reports_a_native_crash_instead_of_dying(monkeypatch):
    """A child killed by SIGSEGV is reported, not propagated."""
    class _Killed:
        returncode = -11
        stdout = ""
        stderr = ""

    monkeypatch.setattr(chroma_rag.subprocess, "run", lambda *a, **k: _Killed())
    ok, detail = chroma_rag.probe_collection_health("evelyn_memory")

    assert ok is False
    assert "signal 11" in detail


def test_health_check_flags_the_failing_collection_only(monkeypatch):
    """check_chroma_health names the unreadable collection and keeps the rest healthy."""
    monkeypatch.setattr(chroma_rag, "list_collection_names", lambda: ["good_one", "bad_one"])
    monkeypatch.setattr(
        chroma_rag, "_probe_batch",
        lambda names: ({"good_one": "count=5"}, "bad_one") if "bad_one" in names else ({}, None),
    )

    health = chroma_rag.check_chroma_health()

    assert health["status"] == "corrupt"
    assert health["unhealthy"] == ["bad_one"]
    assert health["collections"]["good_one"] == "count=5"


class TestBatchedProbeIsolatesTheCulprit:
    """One child probes every collection; a crash must not hide the collections behind it."""

    @staticmethod
    def _child(stdout: str, returncode: int):
        class _Result:
            pass
        r = _Result()
        r.stdout, r.stderr, r.returncode = stdout, "", returncode
        return r

    def test_all_healthy_in_a_single_child(self, monkeypatch):
        out = "OPEN\ta\nDONE\ta\tcount=1\nOPEN\tb\nDONE\tb\tcount=2\n"
        monkeypatch.setattr(chroma_rag, "list_collection_names", lambda: ["a", "b"])
        monkeypatch.setattr(chroma_rag.subprocess, "run", lambda *a, **k: self._child(out, 0))

        health = chroma_rag.check_chroma_health()

        assert health["status"] == "healthy"
        assert health["collections"] == {"a": "count=1", "b": "count=2"}

    def test_crash_names_the_opened_collection(self, monkeypatch):
        """The last announced name with no result is the one that killed the child."""
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if len(calls) == 1:
                # 'a' probed fine, 'b' opened then aborted; 'c' never reached.
                return self._child("OPEN\ta\nDONE\ta\tcount=1\nOPEN\tb\n", -11)
            return self._child("OPEN\tc\nDONE\tc\tcount=3\n", 0)

        monkeypatch.setattr(chroma_rag, "list_collection_names", lambda: ["a", "b", "c"])
        monkeypatch.setattr(chroma_rag.subprocess, "run", fake_run)

        health = chroma_rag.check_chroma_health()

        assert health["unhealthy"] == ["b"]
        assert health["collections"]["a"] == "count=1"
        # The crash must not have hidden 'c'; the batch resumes after the culprit.
        assert health["collections"]["c"] == "count=3"
        assert len(calls) == 2

    def test_memory_count_is_reported_for_existing_callers(self, monkeypatch):
        out = f"OPEN\t{chroma_rag.cfg.CHROMA_MEMORY_COLLECTION}\nDONE\t{chroma_rag.cfg.CHROMA_MEMORY_COLLECTION}\tcount=42\n"
        monkeypatch.setattr(chroma_rag, "list_collection_names",
                            lambda: [chroma_rag.cfg.CHROMA_MEMORY_COLLECTION])
        monkeypatch.setattr(chroma_rag.subprocess, "run", lambda *a, **k: self._child(out, 0))

        assert chroma_rag.check_chroma_health()["count"] == 42
