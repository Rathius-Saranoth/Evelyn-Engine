# test_subject_entity_unification.py
# date created: 2026-10-16 00:52:00
# date modified: 2026-10-16 00:57:50
# tags: #testing, #entities, #taxonomy, #memory, #subjects

"""Tests for unifying Memory Subjects with Taxonomy Entities (tag_entities)."""

from __future__ import annotations

import tempfile
from unittest.mock import patch

import pytest

import evelyn_config as cfg
from Evelyn.tools import fact_extractor, memory_db, tag_entities, vault_db


@pytest.fixture
def isolated_vault_db():
    """Create an isolated temporary vault database with tag_entities table."""
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        db_path = f.name
        with patch.object(cfg, "VAULT_DB_PATH", db_path):
            vault_db.init_db()
            yield db_path


def test_tag_entities_resolution(isolated_vault_db):
    """Test get_entity and resolve_entity_label for slug, label, and case-insensitivity."""
    # Record core test entities
    tag_entities.record_entity("evelyn-engine", label="Evelyn Engine", kind="project")
    tag_entities.record_entity("project-sanctum", label="Project Sanctum", kind="project")
    tag_entities.record_entity("open-webui", label="Open WebUI", kind="product")

    # Resolve by term
    assert tag_entities.resolve_entity_label("evelyn-engine") == "Evelyn Engine"
    assert tag_entities.resolve_entity_label("open-webui") == "Open WebUI"

    # Resolve by label
    assert tag_entities.resolve_entity_label("Evelyn Engine") == "Evelyn Engine"
    assert tag_entities.resolve_entity_label("project sanctum") == "Project Sanctum"

    # Non-existent
    assert tag_entities.resolve_entity_label("NonExistentTopic") is None

    # all_entity_labels
    labels = tag_entities.all_entity_labels()
    assert "Evelyn Engine" in labels
    assert "Project Sanctum" in labels
    assert "Open WebUI" in labels


def test_normalize_subject_attribution_core_and_entities(isolated_vault_db):
    """Test normalize_subject_attribution with core identities and registered entities."""
    tag_entities.record_entity("evelyn-engine", label="Evelyn Engine", kind="project")

    # User & Assistant casing
    user_name = getattr(cfg, "USER_NAME", "User")
    asst_name = getattr(cfg, "ASSISTANT_NAME", "Evelyn")

    assert memory_db.normalize_subject_attribution(user_name.lower()) == user_name
    assert memory_db.normalize_subject_attribution(asst_name.lower()) == asst_name

    # Registered entity
    assert memory_db.normalize_subject_attribution("evelyn-engine") == "Evelyn Engine"
    assert memory_db.normalize_subject_attribution("Evelyn Engine") == "Evelyn Engine"


def test_normalize_subject_attribution_topic_reattribution(isolated_vault_db):
    """Test re-attributing abstract non-entity topics to valid entities based on observation."""
    tag_entities.record_entity("evelyn-engine", label="Evelyn Engine", kind="project")

    user_name = getattr(cfg, "USER_NAME", "User")
    asst_name = getattr(cfg, "ASSISTANT_NAME", "Evelyn")

    # Relationship topic with User observation
    obs_user = f"{user_name} values transparency and open communication in the partnership."
    assert memory_db.normalize_subject_attribution("Relationship", obs_user) == user_name

    # Relationship topic with Assistant observation
    obs_asst = f"{asst_name} perceives the shared space as a calm sanctuary during rest."
    assert memory_db.normalize_subject_attribution("Relationship", obs_asst) == asst_name

    # System topic with engine observation
    obs_engine = "The system migrated Chroma DB embeddings to CPU to free up VRAM."
    assert memory_db.normalize_subject_attribution("System", obs_engine) == "Evelyn Engine"

    # Environment topic with weather observation
    obs_weather = "Experienced severe thunderstorm and hail event ending at 4:00 AM."
    assert memory_db.normalize_subject_attribution("Environment", obs_weather) == user_name

    # Compound subject
    obs_pair = f"{user_name} and {asst_name} collaborated on prompt restructuring."
    assert memory_db.normalize_subject_attribution(f"{user_name} & {asst_name}", obs_pair) == user_name


def test_fact_extractor_known_entity_roster(isolated_vault_db):
    """Test _known_entity_roster includes core identities and registered taxonomy entities."""
    tag_entities.record_entity("evelyn-engine", label="Evelyn Engine", kind="project")
    tag_entities.record_entity("the-artificium", label="The Artificium", kind="work")

    roster = fact_extractor._known_entity_roster()
    assert getattr(cfg, "USER_NAME", "User") in roster
    assert getattr(cfg, "ASSISTANT_NAME", "Evelyn") in roster
    assert "Evelyn Engine" in roster
    assert "The Artificium" in roster
