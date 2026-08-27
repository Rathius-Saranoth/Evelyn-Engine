import pytest
from Evelyn.tools import memory_db
from Evelyn.tools import fact_extractor
from Evelyn.tools import chroma_rag


def test_procedure_crud_with_suggested_tools():
    """Verify that insert, get, search, and update preserve suggested_tools."""
    # Insert test procedure
    proc_id = memory_db.insert_procedure(
        trigger_pattern="When testing procedures mechanic",
        steps="1. Test step one.\n2. Test step two.",
        pitfalls="Do not skip verification.",
        verification="Check DB output.",
        source="test",
        status="live",
        tags="test/procedure",
        suggested_tools="write_file, run_command"
    )
    assert proc_id > 0

    # Retrieve
    proc = memory_db.get_procedure(proc_id)
    assert proc is not None
    assert proc["trigger_pattern"] == "When testing procedures mechanic"
    assert proc["suggested_tools"] == "write_file, run_command"
    assert proc["status"] == "live"

    # Update suggested_tools
    updated = memory_db.update_procedure(proc_id, suggested_tools="create_task")
    assert updated is True

    proc_after = memory_db.get_procedure(proc_id)
    assert proc_after["suggested_tools"] == "create_task"

    # Cleanup
    memory_db.hard_delete_procedure(proc_id)


def test_procedure_queues_lifecycle():
    """Verify enqueuing, checking, and dequeuing procedure merge and split requests."""
    # Insert 2 dummy procedures
    p1 = memory_db.insert_procedure(
        trigger_pattern="Dummy procedure 1 for merge queue",
        steps="1. Step A.",
        status="live"
    )
    p2 = memory_db.insert_procedure(
        trigger_pattern="Dummy procedure 2 for merge queue",
        steps="1. Step B.",
        status="live"
    )

    try:
        # Merge queue
        q_id = memory_db.enqueue_procedure_merge([p1, p2])
        assert q_id > 0

        queued_ids = memory_db.get_all_queued_procedure_merge_ids()
        assert p1 in queued_ids
        assert p2 in queued_ids

        pending_merges = memory_db.get_procedure_merge_queue(status="pending")
        matching = [m for m in pending_merges if m["id"] == q_id]
        assert len(matching) == 1
        assert matching[0]["proc_id_list"] == [p1, p2]

        memory_db.dequeue_procedure_merge(q_id)
        queued_ids_after = memory_db.get_all_queued_procedure_merge_ids()
        assert p1 not in queued_ids_after

        # Split queue
        success = memory_db.enqueue_procedure_split(p1)
        assert success is True

        queued_splits = memory_db.get_all_queued_procedure_split_ids()
        assert p1 in queued_splits

        pending_splits = memory_db.get_procedure_split_queue(status="pending")
        matching_s = [s for s in pending_splits if s["proc_id"] == p1]
        assert len(matching_s) == 1

        memory_db.dequeue_procedure_split(p1)
        queued_splits_after = memory_db.get_all_queued_procedure_split_ids()
        assert p1 not in queued_splits_after

    finally:
        memory_db.hard_delete_procedure(p1)
        memory_db.hard_delete_procedure(p2)


def test_parse_procedures_yaml_with_suggested_tools():
    """Verify parsing of procedures YAML extracts suggested_tools correctly (both comma string and YAML list)."""
    sample_yaml = """```procedures
procedures:
  - trigger_pattern: "When the user asks for a dream journal entry"
    steps: |
      1. Read existing dream notes.
      2. Use write_file to save the dream journal entry into the Dream Journal/ folder.
      3. Verify formatting.
    suggested_tools:
      - read_file
      - write_file
    pitfalls: "Do not use write_journal_entry which is reserved for evening recaps."
    verification: "File exists in Dream Journal/ folder."
    tags: "skill/dream, procedure/journal"
  - trigger_pattern: "When reviewing grocery inventory"
    steps: |
      1. Check list then query search.
    suggested_tools: "manage_vault_list, web_search"
    tags: "procedure/groceries"
```"""
    parsed = fact_extractor._parse_procedures_yaml(sample_yaml)
    assert len(parsed) == 2
    item1 = parsed[0]
    assert item1["trigger_pattern"] == "When the user asks for a dream journal entry"
    assert item1["suggested_tools"] == "read_file, write_file"

    item2 = parsed[1]
    assert item2["suggested_tools"] == "manage_vault_list, web_search"


def test_chroma_rag_procedure_formatting(monkeypatch):
    """Verify chroma_rag formats suggested_tools in retrieved procedures."""
    import sys
    from Evelyn.tools import query_reformulator
    monkeypatch.setattr(query_reformulator, "reformulate_query", lambda q: q)

    # Insert a temporary procedure with a completely unique trigger keyword
    proc_id = memory_db.insert_procedure(
        trigger_pattern="When testing xylophonic_unique_operation_xyz",
        steps="1. Write markdown file to Dream Journal/ folder.",
        suggested_tools="write_file",
        pitfalls="Never use write_journal_entry for dream records.",
        verification="Check vault file.",
        status="live"
    )

    try:
        # Mock chromadb query functions
        monkeypatch.setattr(chroma_rag, "query_collection", lambda *args, **kwargs: [])
        monkeypatch.setattr(chroma_rag, "_fetch_pinned_chunks", lambda *args, **kwargs: [])

        context = chroma_rag.build_rag_context(
            query="When testing xylophonic_unique_operation_xyz"
        )

        assert "--- Active Operational Protocols (Actionable Instructions) ---" in context
        assert "[Operational Protocol: When testing xylophonic_unique_operation_xyz]" in context
        assert "Suggested Tool(s): write_file" in context
        assert "Pitfalls to Avoid: Never use write_journal_entry for dream records." in context
    finally:
        memory_db.hard_delete_procedure(proc_id)


def test_hard_deletion_primitives():
    """Verify hard_delete_procedure, delete_proposal, and hard_delete_entry permanently remove rows."""
    # 1. Procedure hard delete
    p_id = memory_db.insert_procedure(
        trigger_pattern="Dummy procedure to be hard deleted",
        steps="1. Temporary step.",
        status="live"
    )
    assert p_id > 0
    assert memory_db.get_procedure(p_id) is not None
    assert memory_db.hard_delete_procedure(p_id) is True
    assert memory_db.get_procedure(p_id) is None

    # 2. Context entry hard delete
    e_id = memory_db.insert_entry(
        category="Cat01-U",
        subject="User",
        observation="Temporary test entry for hard deletion",
        status="extracted"
    )
    assert e_id > 0
    assert memory_db.get_entry(e_id) is not None
    assert memory_db.hard_delete_entry(e_id) is True
    assert memory_db.get_entry(e_id) is None

    # 3. Proposal hard delete
    prop_id = memory_db.insert_proposal(
        type="recategorize",
        source_ids=[],
        suggested_category="Cat02-U",
        reason="Test recategorize proposal to delete"
    )
    assert prop_id > 0
    props = memory_db.get_pending_proposals()
    assert any(p["id"] == prop_id for p in props)
    assert memory_db.delete_proposal(prop_id) is True
    props_after = memory_db.get_pending_proposals()
    assert not any(p["id"] == prop_id for p in props_after)



