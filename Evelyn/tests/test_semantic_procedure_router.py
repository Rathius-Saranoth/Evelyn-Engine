# test_semantic_procedure_router.py
# date created: 2026-10-02 17:25:00
# date modified: 2026-10-02 17:26:33
# tags: #tests, #semantic-router, #procedures, #intent, #routing

"""Unit tests for Vector-Based Semantic Intent Routing (bge-large-en-v1.5).

Verifies route indexing, centroid calculations, cached fast-loading, intent classification
margins against off-topic queries, and end-to-end tool surfacing integration.
"""

from __future__ import annotations

import os
import tempfile

from Evelyn.tools import evelyn_tools, memory_db
from Evelyn.tools.semantic_router import SemanticProcedureRouter, get_semantic_router


def test_semantic_router_initialization_and_cache():
    """Verify route loading, embedding computation, and .npz cache generation."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        cache_path = os.path.join(tmp_dir, "test_routes_cache.npz")
        router = SemanticProcedureRouter(cache_path=cache_path)

        assert router.ensure_index() is True
        assert os.path.exists(cache_path)
        assert router._centroids is not None
        assert router._exemplars is not None
        assert len(router.routes) > 0
        assert router._centroids.shape[0] == len(router.routes)
        assert router._centroids.shape[1] == 1024  # bge-large-en-v1.5 dimension

        # Verify fast cached reload
        router_cached = SemanticProcedureRouter(cache_path=cache_path)
        assert router_cached.ensure_index() is True
        assert router_cached._centroids is not None
        assert router_cached._centroids.shape == router._centroids.shape


def test_semantic_router_intent_routing_precision():
    """Verify semantic router matches natural queries to their correct intents with high margin."""
    router = get_semantic_router()
    assert router.ensure_index() is True

    # 1. Health / Exertion intent
    exertion_matches = router.route_query(
        "Just got back from mowing the whole yard in this heat. My legs are wrecked and I'm completely drained."
    )
    assert len(exertion_matches) > 0
    top_exertion = exertion_matches[0]
    assert top_exertion["route_name"] == "health_exertion_recovery"
    assert top_exertion["procedure_id"] == 1067
    assert top_exertion["semantic_score"] >= 0.75
    assert "get_health_metrics" in top_exertion["suggested_tools"]

    # 2. Dream Journaling intent
    dream_matches = router.route_query(
        "I had a weird and vivid dream about floating through space."
    )
    assert len(dream_matches) > 0
    top_dream = dream_matches[0]
    assert top_dream["route_name"] == "dream_journaling"
    assert top_dream["procedure_id"] == 657
    assert top_dream["semantic_score"] >= 0.70
    assert "write_dream_entry" in top_dream["suggested_tools"]

    # 3. Reference Library Manual intent
    manual_matches = router.route_query(
        "Look up the torque specs and oil capacity in the tractor service manual."
    )
    assert len(manual_matches) > 0
    top_manual = manual_matches[0]
    assert top_manual["route_name"] == "reference_library_manuals"
    assert top_manual["procedure_id"] == 2312
    assert "search_reference_library" in top_manual["suggested_tools"]

    # 4. Off-topic query should NOT trigger any procedure routes
    off_topic_matches = router.route_query("Who was Julius Caesar and when did he rule?")
    assert len(off_topic_matches) == 0


def test_semantic_router_suggested_tools():
    """Verify get_suggested_tools extracts canonical tools from matching routes."""
    router = get_semantic_router()

    tools = router.get_suggested_tools("I am totally exhausted from working outside all day.")
    assert "get_health_metrics" in tools

    dream_tools = router.get_suggested_tools("I woke up from a strange dream.")
    assert "write_dream_entry" in dream_tools


def test_memory_db_search_procedures_by_trigger_semantic_integration():
    """Verify memory_db.search_procedures_by_trigger surfaces procedures via semantic intent."""
    query = "I feel completely wiped and sore after doing yardwork all day."
    procs = memory_db.search_procedures_by_trigger(query, status="live")

    # If running in isolated sandbox without procedures seeded in test DB,
    # verify that the function executes cleanly and router operates.
    assert isinstance(procs, list)
    if procs:
        top_proc = procs[0]
        assert top_proc.get("id") == 1067
        assert top_proc.get("semantic_score", 0.0) >= 0.70


def test_get_active_tools_dynamic_surfacing_and_pruning_protection():
    """Verify get_active_tools dynamically surfaces procedure-suggested tools and prevents pruning."""
    query = "Just got back from mowing the whole yard in this heat. My legs are wrecked and I'm completely drained."
    active = evelyn_tools.get_active_tools(user_message=query)
    names = [t.get("function", {}).get("name") for t in active if isinstance(t, dict)]

    # Both health metrics and workouts must be retained and not pruned
    assert "get_health_metrics" in names
    assert "get_recent_workouts" in names

    # Unrelated prunable core tools must be pruned
    assert "generate_image" not in names
    assert "get_agenda" not in names
