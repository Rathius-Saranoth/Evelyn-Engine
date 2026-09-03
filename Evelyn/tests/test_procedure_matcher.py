import pytest
from Evelyn.tools import memory_db
from Evelyn.tools.procedure_matcher import (
    calculate_procedure_similarity,
    extract_procedure_keywords,
    find_best_master_candidate,
    identify_cluster_master,
    is_duplicate_procedure,
)


def test_extract_procedure_keywords():
    """Verify stopword stripping and domain marker expansion."""
    # Test sleep/journaling tokens
    text = "When the user says they are going to sleep at bedtime"
    kws = extract_procedure_keywords(text)
    assert "sleep" in kws
    assert "bedtime" in kws
    assert "domain_journal" in kws
    assert "when" not in kws
    assert "the" not in kws
    assert "user" not in kws


def test_calculate_procedure_similarity_exact_and_disjoint():
    """Test exact match yields 1.0, unrelated triggers yield low score."""
    p1 = "When the user asks to write a journal entry for the day"
    p2 = "When the user asks to write a journal entry for the day"
    p3 = "When generating 3D textures for blender assets"

    assert calculate_procedure_similarity(p1, p2) == 1.0
    assert calculate_procedure_similarity(p1, p3) < 0.20


def test_calculate_procedure_similarity_tool_bonus():
    """Test that matching suggested_tools adds concordance bonus."""
    p1 = "When user logs bedtime reflections"
    p2 = "When user prepares for evening sleep wrap-up"

    sim_without_tools = calculate_procedure_similarity(p1, p2)
    sim_with_tools = calculate_procedure_similarity(
        p1, p2, tools1="write_journal_entry", tools2="write_journal_entry"
    )
    assert sim_with_tools > sim_without_tools


def test_is_duplicate_procedure():
    """Test duplicate detection threshold."""
    target = "When logging daily progress and reflections"
    existing = [
        "When logging daily progress and reflections for the day",
        "When scheduling a meeting in Google Calendar",
    ]
    assert is_duplicate_procedure(target, existing, threshold=0.70) is True

    unrelated = "When the user asks for weather conditions in Seattle"
    assert is_duplicate_procedure(unrelated, existing, threshold=0.70) is False


def test_find_best_master_candidate():
    """Test matching candidate procedure to existing live master procedure."""
    candidate = {
        "id": 2001,
        "trigger_pattern": "When the user mentions writing down dreams or wants to log dream details",
        "suggested_tools": "write_dream_entry",
    }
    live_procs = [
        {
            "id": 1034,
            "trigger_pattern": "When the user is winding down, ending the day, preparing for sleep/rest, or asks to complete the daily journal entry / reflection",
            "suggested_tools": "write_journal_entry",
        },
        {
            "id": 657,
            "trigger_pattern": "When the user shares, describes, or asks to log or analyze a dream entry",
            "suggested_tools": "write_dream_entry",
        },
    ]

    best_proc, score = find_best_master_candidate(candidate, live_procs, min_threshold=0.35)
    assert best_proc is not None
    assert best_proc["id"] == 657
    assert score >= 0.35

    # Ensure candidate does not match itself
    self_cand = {
        "id": 657,
        "trigger_pattern": "When the user shares, describes, or asks to log or analyze a dream entry",
        "suggested_tools": "write_dream_entry",
    }
    best_proc_self, _ = find_best_master_candidate(self_cand, [live_procs[1]], min_threshold=0.35)
    assert best_proc_self is None


def test_identify_cluster_master_precedence():
    """Test master identification logic: merged children count > live status > lowest id."""
    cluster = [
        {"id": 1205, "status": "extracted", "trigger_pattern": "Journaling A"},
        {"id": 1034, "status": "live", "trigger_pattern": "Master Journaling"},
        {"id": 1206, "status": "live", "trigger_pattern": "Journaling B"},
    ]
    # 1034 has 5 merged children
    master_counts = {1034: 5, 1206: 0}
    master = identify_cluster_master(cluster, master_id_counts=master_counts)
    assert master is not None
    assert master["id"] == 1034

    # When no merged children exist, prefer live over extracted, then lowest id
    cluster2 = [
        {"id": 500, "status": "extracted", "trigger_pattern": "Old extracted"},
        {"id": 600, "status": "live", "trigger_pattern": "Newer live"},
    ]
    master2 = identify_cluster_master(cluster2, master_id_counts={})
    assert master2 is not None
    assert master2["id"] == 600


@pytest.mark.asyncio
async def test_procedure_proposal_merge_into_master_endpoint(monkeypatch):
    """Test in-place update of master procedure and status='merged' on sources."""
    import yaml
    from evelyn_server import ProposalActionRequest, action_proposal

    # Mock in-memory procedures
    master_proc = {
        "id": 1034,
        "trigger_pattern": "Original master trigger",
        "steps": "Original steps",
        "pitfalls": None,
        "verification": None,
        "tags": "journal, master",
        "suggested_tools": "write_journal_entry",
        "status": "live",
        "merged_into_id": None,
    }
    source_proc = {
        "id": 1234,
        "trigger_pattern": "Extracted trigger variant",
        "steps": "Extracted steps",
        "pitfalls": None,
        "verification": None,
        "tags": "journal, evening",
        "suggested_tools": "write_journal_entry",
        "status": "extracted",
        "merged_into_id": None,
    }

    procs_db = {1034: dict(master_proc), 1234: dict(source_proc)}
    proposals_db = {
        999: {
            "id": 999,
            "type": "procedure_merge",
            "source_ids": [1034, 1234],
            "merged_observation": yaml.dump({
                "trigger_pattern": "Refined consolidated master trigger",
                "steps": "1. Merged step one\n2. Merged step two",
                "suggested_tools": "write_journal_entry",
                "tags": "journal, routine",
            }),
            "suggested_category": "1034",
            "status": "pending",
        }
    }

    monkeypatch.setattr(memory_db, "get_pending_proposals", lambda: list(proposals_db.values()))
    monkeypatch.setattr(memory_db, "get_procedure", lambda pid: procs_db.get(pid))

    def mock_update_procedure(pid, **fields):
        if pid in procs_db:
            procs_db[pid].update(fields)
            return True
        return False

    def mock_merge_procedure(sid, tid):
        if sid in procs_db:
            procs_db[sid]["status"] = "merged"
            procs_db[sid]["merged_into_id"] = tid
            return True
        return False

    def mock_apply_proposal(prop_id):
        if prop_id in proposals_db:
            proposals_db[prop_id]["status"] = "applied"
            return True
        return False

    monkeypatch.setattr(memory_db, "update_procedure", mock_update_procedure)
    monkeypatch.setattr(memory_db, "merge_procedure", mock_merge_procedure)
    monkeypatch.setattr(memory_db, "apply_proposal", mock_apply_proposal)

    # Execute merge_into_master
    res = await action_proposal(
        id=999,
        action="merge_into_master",
        req=ProposalActionRequest(target_id=1034),
        _=None,
    )
    assert res["status"] == "ok"
    assert res["merged_into_id"] == 1034

    # Verify master procedure updated in place
    assert procs_db[1034]["trigger_pattern"] == "Refined consolidated master trigger"
    assert "Merged step one" in str(procs_db[1034]["steps"])
    assert procs_db[1034]["status"] == "live"

    # Verify source procedure marked as merged pointing to master 1034
    assert procs_db[1234]["status"] == "merged"
    assert procs_db[1234]["merged_into_id"] == 1034

    # Verify proposal applied
    assert proposals_db[999]["status"] == "applied"
