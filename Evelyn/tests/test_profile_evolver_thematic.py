"""Unit tests for Profile Evolver thematic clustering, entity pre-aggregation, and proofreading pass."""

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

# Ensure repo root and Evelyn/tools are on python path
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
tools_dir = os.path.join(repo_root, "Evelyn/tools")
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
if tools_dir not in sys.path:
    sys.path.insert(0, tools_dir)

import profile_evolver

import evelyn_config as cfg


@pytest.fixture
def sample_user_entries():
    """Sample raw memory entries representing interspersed facts."""
    return [
        {
            "id": 1,
            "category": f"Cat01-{cfg.SUBJECT_CODE_USER}",
            "observation": "Ricky is an analytical thinker who values deep focus.",
            "tags": "identity, traits",
            "date": "2026-08-01",
            "created_at": 1000.0,
            "updated_at": 1000.0,
        },
        {
            "id": 2,
            "category": f"Cat06-{cfg.SUBJECT_CODE_USER}",
            "observation": "Schyler visited for the weekend and cooked dinner.",
            "tags": "schyler, social, cooking",
            "date": "2026-08-02",
            "created_at": 1010.0,
            "updated_at": 1010.0,
        },
        {
            "id": 3,
            "category": f"Cat09-{cfg.SUBJECT_CODE_USER}",
            "observation": "Prefers quiet morning routines with coffee.",
            "tags": "routines, morning",
            "date": "2026-08-03",
            "created_at": 1020.0,
            "updated_at": 1020.0,
        },
        {
            "id": 4,
            "category": f"Cat06-{cfg.SUBJECT_CODE_USER}",
            "observation": "Schyler called to discuss upcoming travel plans.",
            "tags": "schyler, travel",
            "date": "2026-08-04",
            "created_at": 1030.0,
            "updated_at": 1030.0,
        },
        {
            "id": 5,
            "category": f"Cat04-{cfg.SUBJECT_CODE_USER}",
            "observation": "Values autonomy and integrity above compliance.",
            "tags": "values, ethics",
            "date": "2026-08-05",
            "created_at": 1040.0,
            "updated_at": 1040.0,
        },
    ]


def test_cluster_entries_by_theme_grouping(sample_user_entries):
    """Test that entries are clustered by canonical theme and grouped by entity."""
    filename = cfg.PERSONA_FILE_USER
    thematic_batches = profile_evolver._cluster_entries_by_theme(filename, sample_user_entries, batch_size=40)

    assert len(thematic_batches) >= 3

    theme_names = [b["theme_name"] for b in thematic_batches]
    assert "Identity & Core Values" in theme_names
    assert "Relationship Dynamics & Social Connections" in theme_names
    assert "Interaction Preferences & Constraints" in theme_names

    # Check Relationship Dynamics batch (entries 2 and 4 with tag 'schyler')
    rel_batch = next(b for b in thematic_batches if "Relationship Dynamics" in b["theme_name"])
    assert len(rel_batch["entries"]) == 2
    assert "[Topic / Subject: Schyler]" in rel_batch["evidence_text"]
    assert "cooked dinner" in rel_batch["evidence_text"]
    assert "upcoming travel plans" in rel_batch["evidence_text"]


def test_cluster_entries_by_theme_sub_batching():
    """Test that large entry sets within a single theme are split into sub-batches."""
    filename = cfg.PERSONA_FILE_USER
    large_entries = [
        {
            "id": i,
            "category": f"Cat06-{cfg.SUBJECT_CODE_USER}",
            "observation": f"Social interaction note {i}",
            "tags": "social",
            "date": "2026-08-10",
            "created_at": 2000.0 + i,
            "updated_at": 2000.0 + i,
        }
        for i in range(1, 15)
    ]

    # With batch_size=5, 14 entries should produce 3 sub-batches
    thematic_batches = profile_evolver._cluster_entries_by_theme(filename, large_entries, batch_size=5)
    assert len(thematic_batches) == 3
    assert thematic_batches[0]["theme_name"] == "Relationship Dynamics & Social Connections (Part 1)"
    assert thematic_batches[1]["theme_name"] == "Relationship Dynamics & Social Connections (Part 2)"
    assert thematic_batches[2]["theme_name"] == "Relationship Dynamics & Social Connections (Part 3)"
    assert len(thematic_batches[0]["entries"]) == 5
    assert len(thematic_batches[1]["entries"]) == 5
    assert len(thematic_batches[2]["entries"]) == 4


def test_cluster_entries_unassigned_fallback():
    """Test that unclassified categories are collected into a general batch."""
    filename = cfg.PERSONA_FILE_USER
    unclassified_entries = [
        {
            "id": 99,
            "category": "Cat99-X",
            "observation": "Unclassified observation.",
            "tags": "misc",
            "date": "2026-08-15",
            "created_at": 3000.0,
            "updated_at": 3000.0,
        }
    ]
    batches = profile_evolver._cluster_entries_by_theme(filename, unclassified_entries, batch_size=40)
    assert len(batches) == 1
    assert "General & Unclassified" in batches[0]["theme_name"]


@pytest.mark.asyncio
async def test_proofread_document_success():
    """Test that _proofread_document fixes typos and preserves markdown structure."""
    filename = cfg.PERSONA_FILE_USER
    input_body = (
        "## Identity & Core Values\n\n"
        "Ricky is an analytical thinker. While he navigms complex systems, he preserves his core values.\n\n"
        "## Relationship Dynamics\n\n"
        "He values open communication with Evelyn."
    )
    mock_llm_response = (
        "## Identity & Core Values\n\n"
        "Ricky is an analytical thinker. While he navigates complex systems, he preserves his core values.\n\n"
        "## Relationship Dynamics\n\n"
        "He values open communication with Evelyn."
    )

    with patch("profile_evolver._call_ollama", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = mock_llm_response
        result = await profile_evolver._proofread_document(filename, input_body)

        assert "navigates" in result
        assert "## Identity & Core Values" in result
        assert "## Relationship Dynamics" in result
        mock_call.assert_called_once()
        # Verify low temperature was passed
        _, kwargs = mock_call.call_args
        assert kwargs.get("temperature_override") == 0.1
        assert kwargs.get("think_override") is False


@pytest.mark.asyncio
async def test_proofread_document_safety_fallback():
    """Test that proofreading safely falls back to original body if truncated or missing headers."""
    filename = cfg.PERSONA_FILE_USER
    input_body = (
        "## Identity & Core Values\n\n"
        "Ricky is an analytical thinker.\n\n"
        "## Relationship Dynamics\n\n"
        "He values open communication."
    )
    # Severe truncation
    bad_truncated_response = "Ricky is an analytical thinker."

    with patch("profile_evolver._call_ollama", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = bad_truncated_response
        result = await profile_evolver._proofread_document(filename, input_body)

        # Should fall back to input_body to prevent catastrophic deletion
        assert result == input_body
        assert "## Relationship Dynamics" in result


@pytest.mark.asyncio
async def test_proofread_disabled(monkeypatch):
    """Test that proofreading is skipped when disabled in config."""
    monkeypatch.setattr(cfg, "PROFILE_EVOLUTION_PROOFREAD_ENABLED", False)
    input_body = "## Identity & Core Values\n\nSome text."

    with patch("profile_evolver._call_ollama", new_callable=AsyncMock) as mock_call:
        result = await profile_evolver._proofread_document(cfg.PERSONA_FILE_USER, input_body)
        assert result == input_body
        mock_call.assert_not_called()
