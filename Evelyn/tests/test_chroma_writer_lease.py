# test_chroma_writer_lease.py
# date created: 2026-09-23 22:30:00
# date modified: 2026-09-23 21:46:27
# tags: #test, #chroma, #concurrency, #regression, #vector

"""Regression cover for the ChromaDB single-writer lease (v000.006.218).

On 2026-09-23 a rebuild script drained `chroma_sync_queue` beside the engine's custodian.
Each process persisted its own in-memory copy of the `evelyn_reference` HNSW index,
`length.bin` filled with vector bytes, and `link_lists.bin` grew to 891GB. The "only the
custodian writes" guarantee was a convention nothing enforced. These tests pin the lease that
enforces it: a second writer is refused *before* it touches a segment or claims a queue row,
and the refusal names who holds the lease.
"""

import fcntl
import os

import pytest

from Evelyn.tools import chroma_rag


@pytest.fixture(autouse=True)
def _sandboxed_lease(monkeypatch, tmp_path):
    """Point the lock file at a temp store and leave no lease behind."""
    monkeypatch.setattr(chroma_rag.cfg, "CHROMA_DB_PATH", str(tmp_path))
    chroma_rag.release_chroma_writer()
    yield
    chroma_rag.release_chroma_writer()


@pytest.fixture
def other_writer(tmp_path):
    """Hold the lease through a separate open file, as another process would."""
    path = os.path.join(str(tmp_path), ".chroma_write.lock")
    fh = open(path, "a+")  # noqa: SIM115 — held for the test's duration
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    fh.write("pid 4242 (evelyn engine custodian) since 2026-09-23 19:51:02\n")
    fh.flush()
    yield
    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    fh.close()


def test_claim_is_idempotent_and_recorded():
    chroma_rag.claim_chroma_writer("unit test")
    chroma_rag.claim_chroma_writer("unit test")  # second call is a no-op, not a self-conflict

    assert chroma_rag.owns_chroma_writer()
    with open(chroma_rag._writer_lock_path()) as fh:
        assert f"pid {os.getpid()} (unit test)" in fh.read()


def test_second_writer_is_refused_by_name(other_writer):
    with pytest.raises(chroma_rag.ChromaWriterBusy, match="evelyn engine custodian"):
        chroma_rag.claim_chroma_writer("rebuild script")
    assert not chroma_rag.owns_chroma_writer()


def test_drain_is_refused_before_claiming_any_row(other_writer, monkeypatch):
    """A refused drainer must not open the queue, or it would strand rows in 'processing'."""
    def _queue_touched():
        raise AssertionError("drain opened the queue without holding the writer lease")
    monkeypatch.setattr(chroma_rag, "_get_queue_db", _queue_touched)

    with pytest.raises(chroma_rag.ChromaWriterBusy):
        chroma_rag.drain_sync_queue(50)


def test_direct_writes_are_refused(other_writer, monkeypatch):
    def _segment_touched(name):
        raise AssertionError("direct write opened a collection without the writer lease")
    monkeypatch.setattr(chroma_rag, "get_or_create_collection", _segment_touched)

    with pytest.raises(chroma_rag.ChromaWriterBusy):
        chroma_rag.direct_upsert("note.md", "text", "evelyn_memory")
    with pytest.raises(chroma_rag.ChromaWriterBusy):
        chroma_rag.direct_delete("note.md", "evelyn_memory")
    with pytest.raises(chroma_rag.ChromaWriterBusy):
        chroma_rag.direct_remap("old.md", "new.md", "evelyn_memory")


def test_remap_falls_back_to_reingest_when_another_process_writes(other_writer):
    """The vault watcher runs beside the engine: remap reports False so the note is re-queued."""
    assert chroma_rag.remap_document("old.md", "new.md", "evelyn_memory") is False


def test_release_lets_the_next_writer_in():
    chroma_rag.claim_chroma_writer("startup repair")
    chroma_rag.release_chroma_writer()

    with open(chroma_rag._writer_lock_path(), "a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises if still held
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class TestOfflineWriterGuard:
    """Maintenance scripts run only with the engine stopped, or as the engine's own repair."""

    def test_refused_while_engine_unit_is_up(self, monkeypatch, capsys):
        monkeypatch.setattr(chroma_rag, "_engine_unit_state", lambda: ("active", 999999))
        monkeypatch.setattr(chroma_rag.syslog, "syslog", lambda *a: None)

        code, engine_invoked = chroma_rag.acquire_offline_writer("rebuild")

        assert code == chroma_rag.WRITER_REFUSED and engine_invoked is False
        assert not chroma_rag.owns_chroma_writer()
        err = capsys.readouterr().err
        assert "[REFUSED]" in err and "systemctl stop evelyn.service" in err

    def test_refused_between_restarts_too(self, monkeypatch):
        """A crash-looping unit holds no lease between attempts but may start at any moment."""
        monkeypatch.setattr(chroma_rag, "_engine_unit_state", lambda: ("activating", 0))
        monkeypatch.setattr(chroma_rag.syslog, "syslog", lambda *a: None)

        assert chroma_rag.acquire_offline_writer("rebuild")[0] == chroma_rag.WRITER_REFUSED

    def test_engine_startup_repair_is_allowed(self, monkeypatch):
        monkeypatch.setattr(chroma_rag, "_engine_unit_state", lambda: ("activating", os.getppid()))

        code, engine_invoked = chroma_rag.acquire_offline_writer("startup repair")

        assert code == 0 and engine_invoked is True
        assert chroma_rag.owns_chroma_writer()

    def test_allowed_when_engine_is_stopped(self, monkeypatch):
        monkeypatch.setattr(chroma_rag, "_engine_unit_state", lambda: ("inactive", 0))

        assert chroma_rag.acquire_offline_writer("rebuild") == (0, False)

    def test_refused_when_lease_is_held_without_systemd(self, monkeypatch, other_writer, capsys):
        monkeypatch.setattr(chroma_rag, "_engine_unit_state", lambda: ("unknown", 0))
        monkeypatch.setattr(chroma_rag.syslog, "syslog", lambda *a: None)

        assert chroma_rag.acquire_offline_writer("rebuild")[0] == chroma_rag.WRITER_REFUSED
        assert "evelyn engine custodian" in capsys.readouterr().err
