# test_procedures_upgrade.py
# date created: 2026-08-28 07:37:20
# date modified: 2026-08-29 16:04:22
# tags: 

import pytest

from Evelyn.tools import chroma_rag, fact_extractor, memory_db


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


def test_parse_procedures_yaml_unclosed_fence():
    """Verify procedures YAML parser handles unclosed markdown fences from stop sequences."""
    raw_yaml = """```procedures
procedures:
  - trigger_pattern: "When the user asks to bake bread"
    steps: |
      1. Mix flour, water, and yeast.
      2. Let it rise for 2 hours.
    suggested_tools: "write_file"
    tags: "procedure/baking"
"""
    parsed = fact_extractor._parse_procedures_yaml(raw_yaml)
    assert len(parsed) == 1
    assert parsed[0]["trigger_pattern"] == "When the user asks to bake bread"
    assert parsed[0]["suggested_tools"] == "write_file"


def test_chroma_rag_procedure_formatting(monkeypatch):
    """Verify chroma_rag formats suggested_tools in retrieved procedures."""
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
        # Mock chromadb query functions and telemetry logging
        monkeypatch.setattr(chroma_rag, "query_collection", lambda *args, **kwargs: [])
        monkeypatch.setattr(chroma_rag, "_fetch_pinned_chunks", lambda *args, **kwargs: [])
        monkeypatch.setattr(chroma_rag, "log_rag_retrieval", lambda *args, **kwargs: None)

        context = chroma_rag.build_rag_context(
            query="When testing xylophonic_unique_operation_xyz"
        )

        assert "<context_retrieval" in context
        assert 'trigger_pattern="When testing xylophonic_unique_operation_xyz"' in context
        assert "<suggested_tools>write_file</suggested_tools>" in context
        assert "<pitfalls>Never use write_journal_entry for dream records.</pitfalls>" in context
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


@pytest.mark.asyncio
async def test_procedure_merge_proposal_tag_preservation(monkeypatch):
    """Verify that generate_procedure_merge_proposal preserves source procedure domain tags."""
    import yaml

    from Evelyn.tools import procedure_consolidator

    p1 = memory_db.insert_procedure(
        trigger_pattern="When testing evening routines",
        steps="1. Wind down.",
        tags="evening-routine, sleep",
        suggested_tools="write_file",
        status="live"
    )
    p2 = memory_db.insert_procedure(
        trigger_pattern="When preparing for bed",
        steps="1. Review day.",
        tags="reflection, sleep",
        suggested_tools="write_file",
        status="live"
    )

    class MockResponse:
        status_code = 200
        def json(self):
            return {
                "message": {
                    "content": (
                        "```yaml\n"
                        "topic: \"Merged Evening Routine\"\n"
                        "reason: \"Consolidated evening procedures.\"\n"
                        "trigger_pattern: \"When ending the day or preparing for bed\"\n"
                        "steps: |\n"
                        "  1. Step one.\n"
                        "suggested_tools: \"write_file\"\n"
                        "pitfalls: \"None\"\n"
                        "verification: \"None\"\n"
                        "tags: \"procedure, merged\"\n"
                        "```"
                    )
                }
            }

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, *args, **kwargs):
            return MockResponse()

    monkeypatch.setattr("httpx.AsyncClient", MockAsyncClient)

    try:
        proc1 = memory_db.get_procedure(p1)
        proc2 = memory_db.get_procedure(p2)
        prop_id = await procedure_consolidator.generate_procedure_merge_proposal([proc1, proc2])
        assert prop_id is not None

        props = memory_db.get_pending_proposals(type="procedure_merge")
        matching_props = [p for p in props if p["id"] == prop_id]
        assert len(matching_props) == 1
        prop = matching_props[0]
        obs = yaml.safe_load(prop["merged_observation"])
        # Should fallback to inherited domain tags rather than generic 'procedure, merged'
        tags_set = {t.strip() for t in obs["tags"].split(",")}
        assert "evening-routine" in tags_set
        assert "reflection" in tags_set
        assert "sleep" in tags_set
    finally:
        memory_db.hard_delete_procedure(p1)
        memory_db.hard_delete_procedure(p2)
        if prop_id:
            memory_db.delete_proposal(prop_id)


def test_procedure_status_expansion_lifecycle():
    """Verify that reject_procedure, merge_procedure, and merged_into_id work as expected."""
    # 1. Master procedure
    master_id = memory_db.insert_procedure(
        trigger_pattern="When user triggers master operation test",
        steps="1. Master step.",
        status="live"
    )
    # 2. Extracted candidate
    cand1 = memory_db.insert_procedure(
        trigger_pattern="When user triggers candidate one test",
        steps="1. Cand step.",
        status="extracted",
        merged_into_id=master_id
    )
    # 3. Reject candidate 2
    cand2 = memory_db.insert_procedure(
        trigger_pattern="When user triggers candidate two to reject",
        steps="1. Reject step.",
        status="extracted"
    )

    try:
        # Verify initial
        p_cand1 = memory_db.get_procedure(cand1)
        assert p_cand1 is not None
        assert p_cand1["merged_into_id"] == master_id

        # Merge cand1 into master
        assert memory_db.merge_procedure(cand1, master_id) is True
        p_cand1_merged = memory_db.get_procedure(cand1)
        assert p_cand1_merged is not None
        assert p_cand1_merged["status"] == "merged"
        assert p_cand1_merged["merged_into_id"] == master_id

        # Reject cand2
        assert memory_db.reject_procedure(cand2) is True
        p_cand2_rej = memory_db.get_procedure(cand2)
        assert p_cand2_rej is not None
        assert p_cand2_rej["status"] == "rejected"

        # Verify live search filters them out
        live_procs = memory_db.get_all_procedures(status="live")
        assert any(p["id"] == master_id for p in live_procs)
        assert not any(p["id"] == cand1 for p in live_procs)
        assert not any(p["id"] == cand2 for p in live_procs)

        # Verify merged / rejected status filters
        merged_procs = memory_db.get_all_procedures(status="merged")
        assert any(p["id"] == cand1 for p in merged_procs)

        rejected_procs = memory_db.get_all_procedures(status="rejected")
        assert any(p["id"] == cand2 for p in rejected_procs)

    finally:
        memory_db.hard_delete_procedure(master_id)
        memory_db.hard_delete_procedure(cand1)
        memory_db.hard_delete_procedure(cand2)


def test_procedure_tag_hygiene_and_proposal_merge_linkage():
    """Verify that procedure merge proposals sanitize tags and link merged_into_id."""
    p1 = memory_db.insert_procedure(
        trigger_pattern="When test source alpha runs",
        steps="1. Step alpha.",
        status="live",
        tags="domain/alpha, test/tag"
    )
    p2 = memory_db.insert_procedure(
        trigger_pattern="When test source beta runs",
        steps="1. Step beta.",
        status="live",
        tags="domain/beta"
    )

    master_id = None
    try:
        # Simulate proposal merge logic with tag sanitation
        source_tags_set = set()
        for eid in [p1, p2]:
            p_old = memory_db.get_procedure(eid)
            if p_old and p_old.get("tags"):
                for t in str(p_old["tags"]).split(","):
                    ct = t.strip()
                    if ct and ct.lower() not in ("procedure", "merged", "merge", "split", "consolidated", "none"):
                        source_tags_set.add(ct)

        # Model output with some generic noise tags
        model_tags = "domain/alpha, domain/beta, merged, split"
        combined: set[str] = {t.strip() for t in model_tags.split(",") if t.strip()}
        for st in source_tags_set:
            combined.add(st)
        filtered_tags = {t for t in combined if t.lower() not in ("procedure", "merged", "merge", "split", "consolidated", "none")}
        final_tags = ", ".join(sorted(filtered_tags)) if filtered_tags else "procedure"

        # Assert no generic tags made it through
        assert "merged" not in final_tags
        assert "split" not in final_tags
        assert "domain/alpha" in final_tags
        assert "domain/beta" in final_tags

        master_id = memory_db.insert_procedure(
            trigger_pattern="When unified test master runs",
            steps="1. Unified step.",
            status="live",
            tags=final_tags
        )

        for eid in [p1, p2]:
            assert memory_db.merge_procedure(eid, master_id) is True

        p1_row = memory_db.get_procedure(p1)
        p2_row = memory_db.get_procedure(p2)
        assert p1_row is not None and p1_row["status"] == "merged" and p1_row["merged_into_id"] == master_id
        assert p2_row is not None and p2_row["status"] == "merged" and p2_row["merged_into_id"] == master_id

    finally:
        memory_db.hard_delete_procedure(p1)
        memory_db.hard_delete_procedure(p2)
        if master_id:
            memory_db.hard_delete_procedure(master_id)


def test_all_specific_purpose_tools_have_live_procedure_coverage():
    """Verify that all specific-purpose tools are covered by at least one live procedure."""
    import Evelyn.tools.evelyn_tools as et

    all_model_tools: list[str] = []
    for item in et.MODEL_TOOL_DEFINITIONS:
        if isinstance(item, dict):
            fn_obj = item.get("function")
            if isinstance(fn_obj, dict):
                fn_name = fn_obj.get("name")
                if isinstance(fn_name, str):
                    all_model_tools.append(fn_name)
    general_purpose = {"read_file", "write_file", "run_command", "web_search"}
    specific_tools = [t for t in all_model_tools if t not in general_purpose]

    live_procs = memory_db.get_all_procedures(status="live")
    covered_tools = set()
    for proc in live_procs:
        s_tools = proc.get("suggested_tools") or ""
        for t in s_tools.split(","):
            ct = t.strip()
            if ct:
                covered_tools.add(ct)

    missing_tools = [st for st in specific_tools if st not in covered_tools]
    assert not missing_tools, f"The following specific-purpose tools lack live starter procedure coverage: {missing_tools}"





