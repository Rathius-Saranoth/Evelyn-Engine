# test_procedure_tag_librarian.py
# date created: 2026-09-29
# date modified: 2026-09-29 20:55:44
# tags: #[procedures, #taxonomy, #librarian, #testing]

"""Targeted unit tests for Procedure Tag Librarian Governance (v000.006.289)."""

from unittest.mock import patch

import pytest

from Evelyn.tools import memory_db, tag_librarian, taxonomy_db


@pytest.fixture
def vocabulary(monkeypatch: pytest.MonkeyPatch) -> None:
    for term in ("exercise", "debugging", "python", "routine"):
        taxonomy_db.upsert_master_tag(term, category="technical")
    taxonomy_db.record_alias("workout", "exercise")
    taxonomy_db.invalidate_alias_cache()
    monkeypatch.setattr(tag_librarian, "index_master_tag_in_chroma", lambda *a, **k: None)


def _procedure(trigger: str, steps: str = "Step 1", tags: str | None = None, status: str = "live") -> int:
    return memory_db.insert_procedure(
        trigger_pattern=trigger,
        steps=steps,
        pitfalls="None",
        verification="Done",
        tags=tags,
        suggested_tools=None,
        status=status,
    )


class TestProcedureTagQueue:
    def test_untagged_procedures_come_first(self, vocabulary: None) -> None:
        """Untagged procedures take precedence over tagged procedures in the audit queue."""
        tagged_id = _procedure("When user exercises", tags="exercise")
        untagged_id = _procedure("When user starts routine")

        queue = [p["id"] for p in memory_db.fetch_next_procedures_for_tag_audit(batch_size=10)]

        assert queue.index(untagged_id) < queue.index(tagged_id)

    def test_audited_procedure_leaves_the_queue(self, vocabulary: None) -> None:
        """Marking a procedure audited removes it from the pending queue."""
        proc_id = _procedure("When debugging a script")
        memory_db.mark_procedure_tag_audited(proc_id, tags="debugging")

        queue_ids = {p["id"] for p in memory_db.fetch_next_procedures_for_tag_audit(batch_size=10)}
        assert proc_id not in queue_ids

    def test_procedure_matching_nothing_still_leaves_queue(self, vocabulary: None) -> None:
        """A procedure yielding no new tags is stamped so it does not loop forever."""
        proc_id = _procedure("When doing something obscure")
        memory_db.mark_procedure_tag_audited(proc_id)

        queue_ids = {p["id"] for p in memory_db.fetch_next_procedures_for_tag_audit(batch_size=10)}
        assert proc_id not in queue_ids
        fetched = memory_db.get_procedure(proc_id)
        assert fetched is not None
        assert fetched.get("last_tag_audit_at") is not None

    def test_count_procedures_awaiting_audit(self, vocabulary: None) -> None:
        """Counts separate untagged procedures from total pending procedures."""
        _procedure("Tagged proc", tags="python")
        _procedure("Untagged proc")

        untagged, total = memory_db.count_procedures_awaiting_tag_audit()
        assert untagged >= 1
        assert total >= 2


class TestProcedureTagAuditor:
    def test_strips_container_prefixes_and_canonicalizes_aliases(self, vocabulary: None) -> None:
        """Verifies container prefixes (skill/, procedure/) are pruned and aliases canonicalized."""
        proc_id = _procedure(
            trigger="When starting a workout session",
            steps="1. Stretch\n2. Lift weights",
            tags="skill/workout, procedure/exercise",
        )
        proc = memory_db.get_procedure(proc_id)
        assert proc is not None

        with patch.object(tag_librarian, "classify_document_subjects", return_value=(["exercise"], [])):
            res = tag_librarian.audit_single_procedure_tags(proc, dry_run=False)

        assert "workout" not in res["applied"]
        assert "skill/workout" not in res["applied"]
        assert "procedure/exercise" not in res["applied"]
        assert "exercise" in res["applied"]

        updated = memory_db.get_procedure(proc_id)
        assert updated is not None
        assert updated["tags"] == "exercise"
        assert updated["last_tag_audit_at"] is not None

    def test_proposes_unregistered_candidate_terms(self, vocabulary: None, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unregistered subjects are quarantined as tag_admission proposals with origin='procedure #X'."""
        monkeypatch.setattr(tag_librarian.cfg, "TAG_ADMISSION_MIN_SOURCES", 1)
        monkeypatch.setattr(tag_librarian.cfg, "TAG_ADMISSION_AUTO_ADMIT", False)
        proc_id = _procedure(
            trigger="When calibrating a 3d printer",
            steps="1. Heat nozzle\n2. Level bed",
            tags=None,
        )
        proc = memory_db.get_procedure(proc_id)
        assert proc is not None

        with patch.object(
            tag_librarian,
            "classify_document_subjects",
            return_value=(["python"], ["additive-manufacturing"]),
        ):
            res = tag_librarian.audit_single_procedure_tags(proc, dry_run=False)

        assert "python" in res["applied"]
        assert "additive-manufacturing" in res["proposed"]

        pending = memory_db.get_pending_proposals(tag_librarian.TAG_ADMISSION_PROPOSAL)
        topics = [p["topic"] for p in pending]
        assert "additive-manufacturing" in topics

        matched_prop = next(p for p in pending if p["topic"] == "additive-manufacturing")
        assert f"procedure #{proc_id}" in matched_prop["merged_observation"]

    def test_dry_run_leaves_database_unmodified(self, vocabulary: None) -> None:
        """Dry-run returns planned changes without writing tags or stamping audit timestamp."""
        proc_id = _procedure(
            trigger="When running unit tests",
            steps="Run pytest",
            tags="python",
        )
        proc = memory_db.get_procedure(proc_id)
        assert proc is not None

        with patch.object(
            tag_librarian,
            "classify_document_subjects",
            return_value=(["debugging"], []),
        ):
            res = tag_librarian.audit_single_procedure_tags(proc, dry_run=True)

        assert res["status"] == "dry_run"
        assert "debugging" in res["applied"]

        unmodified = memory_db.get_procedure(proc_id)
        assert unmodified is not None
        assert unmodified["tags"] == "python"
        assert unmodified["last_tag_audit_at"] is None
