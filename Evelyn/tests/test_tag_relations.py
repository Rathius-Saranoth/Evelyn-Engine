# test_tag_relations.py
# date created: 2026-09-23 19:20:00
# date modified: 2026-09-23 19:20:38
# tags: #test, #taxonomy, #relations, #vocabulary

"""The associative (`RT`) layer (v000.006.216, taxonomy §6.4).

Flattening the hierarchy deleted relational information without replacing it: `health/sleep`
stated that sleep belongs with health, and `health` + `sleep` states nothing. This table is
where that knowledge now lives. Relations are symmetric, stored once, and approved by hand.
"""

import pytest

import evelyn_config as cfg
from Evelyn.tools import taxonomy_db, vault_db


@pytest.fixture
def registry(monkeypatch, tmp_path):
    monkeypatch.setattr(vault_db, "DB_PATH", str(tmp_path / "vault.db"))
    monkeypatch.setattr(cfg, "VAULT_DB_PATH", str(tmp_path / "vault.db"))
    vault_db.init_db()
    return taxonomy_db


class TestSymmetry:
    def test_a_relation_reads_from_either_side(self, registry):
        registry.record_relation("cat", "pet")

        assert [r["tag"] for r in registry.get_related_terms("cat")] == ["pet"]
        assert [r["tag"] for r in registry.get_related_terms("pet")] == ["cat"]

    def test_recording_the_reverse_does_not_duplicate(self, registry):
        registry.record_relation("cat", "pet")
        registry.record_relation("pet", "cat")

        assert len(registry.get_related_terms("cat")) == 1

    def test_a_term_is_not_related_to_itself(self, registry):
        registry.record_relation("cat", "cat")

        assert registry.get_related_terms("cat") == []

    def test_blank_terms_are_ignored(self, registry):
        registry.record_relation("", "pet")
        registry.record_relation("cat", "   ")

        assert registry.get_related_terms("pet") == []


class TestWeightAndTier:
    def test_the_default_weight_ranks_below_a_direct_match(self, registry):
        """§6.4: expansion through RT returns below direct matches. 0.4 is the start value."""
        registry.record_relation("storm", "weather")

        assert registry.get_related_terms("storm")[0]["weight"] == 0.4

    def test_re_recording_updates_rather_than_duplicating(self, registry):
        registry.record_relation("storm", "weather", weight=0.4, tier="candidate")
        registry.record_relation("weather", "storm", weight=0.6, tier="reviewed")

        rel = registry.get_related_terms("storm")
        assert len(rel) == 1
        assert rel[0]["weight"] == 0.6
        assert rel[0]["tier"] == "reviewed"

    def test_tier_distinguishes_a_curated_relation_from_a_proposed_one(self, registry):
        """The standard forbids inferring relations and activating them automatically, so a
        candidate must be tellable from an approved one."""
        registry.record_relation("gis", "it-support", tier="candidate")

        assert registry.get_related_terms("gis")[0]["tier"] == "candidate"


def test_relations_are_ordered_heaviest_first(registry):
    registry.record_relation("dream", "lucid-dreaming", weight=0.8)
    registry.record_relation("dream", "subconscious", weight=0.3)

    assert [r["tag"] for r in registry.get_related_terms("dream")] == [
        "lucid-dreaming", "subconscious",
    ]


def test_an_unrelated_term_returns_nothing(registry):
    assert registry.get_related_terms("zzz-nothing") == []
