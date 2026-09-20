# test_tag_format_standard.py
# date created: 2026-09-19 00:00:00
# date modified: 2026-09-19 00:00:00
# tags: #tests, #taxonomy, #tagging, #normalization, #edtf

"""Hermetic tests for the §5 tag format standard and §3.8 EDTF date anchors.

Covers .agents/rules/vault-tag-taxonomy.md:
    §5   — lowercase always, hyphens join words, slashes join levels, no entity branch.
    §3.8 — EDTF reduced precision vs. unspecified digits.
"""

import pytest

from Evelyn.tools.db_migrator import _frozen_normalize_tag_format_000_004_002
from Evelyn.tools.tag_librarian import canonicalize_date_tag, normalize_tag_format


class TestFormatStandard:
    """§5: one rule, no exceptions."""

    @pytest.mark.parametrize(("raw", "expected"), [
        ("Tech/Ai", "tech/ai"),
        ("  #Tech/GIS  ", "tech/gis"),
        ("Home_Sanctuary", "home-sanctuary"),
        ("Dungeon_Crawler_Carl", "dungeon-crawler-carl"),
        ("3DPrinting/UVMapping", "3d-printing/uv-mapping"),
        ("AIModel", "ai-model"),
        ("NG911", "ng911"),
        ("Gemma4", "gemma4"),
        ("kw/Foo_Bar", "foo-bar"),
        ("ctx/home improvement", "home-improvement"),
        ("home`", "home"),
        ("", ""),
    ])
    def test_normalizes_to_canonical_form(self, raw, expected):
        assert normalize_tag_format(raw) == expected

    def test_case_collision_classes_converge(self):
        """The ai/Ai and tech/Tech forks must collapse to one term each."""
        assert normalize_tag_format("Ai") == normalize_tag_format("ai") == "ai"
        assert normalize_tag_format("Tech/Python") == normalize_tag_format("tech/python")

    def test_no_entity_branch_survives(self):
        """Proper nouns follow the same rule as concepts — no underscores, no TitleCase."""
        result = normalize_tag_format("Jane_Doe")
        assert result == "jane-doe"
        assert "_" not in result and result.islower()

    @pytest.mark.parametrize("raw", [
        "Tech/Ai", "3DPrinting/UVMapping", "Cy_Yyyy/11/16", "status/Active", "home`",
    ])
    def test_idempotent(self, raw):
        once = normalize_tag_format(raw)
        assert normalize_tag_format(once) == once

    @pytest.mark.parametrize("raw", ["status/Active", "kanban-todo", "obsidian-graph/contact"])
    def test_administrative_namespaces_untouched(self, raw):
        """§3.2: administrative tags are outside the descriptive vocabulary."""
        assert normalize_tag_format(raw) == raw


class TestEdtfDateAnchors:
    """§3.8: reduced precision and unspecified digits are distinct."""

    @pytest.mark.parametrize(("raw", "expected"), [
        ("CY-2026/09/19", "CY-2026/09/19"),   # full date, already canonical
        ("Cy_Yyyy/11/16", "CY-XXXX/11/16"),   # unspecified year
        ("Cy_2026/05", "CY-2026/05"),         # reduced precision: month
        ("cy-2025", "CY-2025"),               # reduced precision: year
    ])
    def test_canonical_forms(self, raw, expected):
        assert canonicalize_date_tag(raw) == expected
        assert normalize_tag_format(raw) == expected

    def test_reduced_precision_is_not_unspecified(self):
        """'May 2026' must not become 'an unknown day in May 2026'."""
        assert canonicalize_date_tag("Cy_2026/05") == "CY-2026/05"
        assert canonicalize_date_tag("Cy_2026/05") != "CY-2026/05/XX"

    @pytest.mark.parametrize("raw", [
        "cybersecurity", "cyberpower-cp1000pfclcd", "cytokine-dynamics", "cyber-warrior",
    ])
    def test_non_dates_are_not_captured(self, raw):
        """The cy- prefix must not swallow ordinary vocabulary."""
        assert canonicalize_date_tag(raw) is None
        assert normalize_tag_format(raw) == raw


class TestFrozenMigrationNormalizer:
    """AGENTS.md §5: an applied migration must keep producing what it produced."""

    @pytest.mark.parametrize(("raw", "expected"), [
        ("kw/Tech_Ai", "Tech_Ai"),
        ("Tech/Ai", "Tech/Ai"),
        ("home_improvement", "home-improvement"),
    ])
    def test_preserves_pre_standard_behaviour(self, raw, expected):
        assert _frozen_normalize_tag_format_000_004_002(raw) == expected

    def test_diverges_from_current_normalizer(self):
        """If these ever agree, the freeze has been broken."""
        assert _frozen_normalize_tag_format_000_004_002("Tech/Ai") != normalize_tag_format("Tech/Ai")


class TestNamespaceRetirement:
    """§9 step 2: deterministic namespace moves (vault scope only)."""

    @pytest.mark.parametrize(("raw", "expected"), [
        ("topic/hardware", "hardware"),          # §3.3 domains are bare-rooted
        ("topic/tech/ar", "tech/ar"),
        ("contact/friend", "obsidian-graph/contact"),
        ("contact/mother", "obsidian-graph/contact"),
        ("location/biome/tropical", "setting/biome/tropical"),
        ("tech/ai", "tech/ai"),                  # untouched
        ("location/florida", "location/florida"),  # entity — left for step 3
    ])
    def test_tag_mapping(self, raw, expected):
        from Evelyn.tools.db_migrator import _step2_transform_tag
        assert _step2_transform_tag(raw) == expected

    def test_relationship_namespace_is_retired(self):
        from Evelyn.tools.db_migrator import _step2_transform_tag
        assert _step2_transform_tag("relationship/goals") is None

    def test_contact_roles_collapse_to_one_flag(self):
        """15 role subdomains carry no information the graph flag doesn't."""
        from Evelyn.tools.db_migrator import _step2_transform_tags
        assert _step2_transform_tags(["contact/dad", "contact/father", "contact/mom"]) == [
            "obsidian-graph/contact"
        ]

    def test_prefix_drop_dedupes_against_existing_bare_term(self):
        from Evelyn.tools.db_migrator import _step2_transform_tags
        assert _step2_transform_tags(["topic/tech/ar", "tech/ar"]) == ["tech/ar"]

    def test_named_places_survive_for_entity_extraction(self):
        """§2: named individuals become links at step 3, not tags retired at step 2."""
        from Evelyn.tools.db_migrator import _step2_transform_tags
        assert _step2_transform_tags(["location/usa/kansas"]) == ["location/usa/kansas"]


class TestRegistryOwnership:
    """§0 / §6.1: one registry, owned by taxonomy_db, shared by both substrates."""

    def test_registry_api_lives_in_taxonomy_db(self):
        from Evelyn.tools import taxonomy_db
        for fn in ("get_master_tags", "upsert_master_tag", "delete_master_tag"):
            assert callable(getattr(taxonomy_db, fn))

    def test_vault_db_no_longer_exposes_the_registry(self):
        """Ownership is singular — vault_db must not re-expose the taxonomy API."""
        from Evelyn.tools import vault_db
        for fn in ("get_master_tags", "upsert_master_tag", "delete_master_tag"):
            assert not hasattr(vault_db, fn), f"vault_db still exposes {fn}"

    def test_memory_subsystem_does_not_reach_into_the_vault_store(self):
        """fact_extractor reads the shared registry, not the vault's database module."""
        import Evelyn.tools.fact_extractor as fe
        assert not hasattr(fe, "vault_db"), "fact_extractor re-imported vault_db"
        assert hasattr(fe, "taxonomy_db")


class TestSubjectDuplicateTags:
    """§2 / §9 step 4: the subject field is the source of truth for who a record concerns."""

    def test_tag_matching_own_subject_is_dropped(self):
        from Evelyn.tools.tag_librarian import strip_subject_duplicate_tags
        assert strip_subject_duplicate_tags(["jane-doe", "health/sleep"], "Jane Doe") == ["health/sleep"]

    def test_cross_reference_to_another_party_survives(self):
        """A fact about one party tagged with another's name is real information."""
        from Evelyn.tools.tag_librarian import strip_subject_duplicate_tags
        assert strip_subject_duplicate_tags(["jane-doe", "mood"], "John Smith") == ["jane-doe", "mood"]

    def test_matching_is_format_insensitive(self):
        """Jane_Doe, jane-doe and JaneDoe all restate the same subject."""
        from Evelyn.tools.tag_librarian import strip_subject_duplicate_tags
        for variant in ("Jane_Doe", "jane-doe", "JaneDoe", "#Jane Doe"):
            assert strip_subject_duplicate_tags([variant], "Jane Doe") == []

    def test_empty_subject_is_a_noop(self):
        from Evelyn.tools.tag_librarian import strip_subject_duplicate_tags
        assert strip_subject_duplicate_tags(["a", "b"], "") == ["a", "b"]

    def test_order_preserved(self):
        from Evelyn.tools.tag_librarian import strip_subject_duplicate_tags
        assert strip_subject_duplicate_tags(["z", "jane-doe", "a"], "Jane Doe") == ["z", "a"]


class TestLexicalEquivalence:
    """§6.2: UF equivalence detection, tiered by required judgement."""

    def _eq(self, counts):
        import collections

        from Evelyn.tools.tag_synonym import lexical_equivalences
        return lexical_equivalences(collections.Counter(counts))

    def test_separator_variants_collapse_to_most_used(self):
        r = self._eq({"3d-printing": 31, "3dprinting": 9, "3-d-printing": 7})
        assert sorted(r["auto"]) == [("3d-printing", "3-d-printing"), ("3d-printing", "3dprinting")]
        assert r["deferred"] == []

    def test_deeper_form_wins_when_also_more_used(self):
        r = self._eq({"relationship/dynamics": 377, "relationship-dynamics": 86})
        assert r["auto"] == [("relationship/dynamics", "relationship-dynamics")]

    def test_rare_deep_variant_never_renames_a_dominant_flat_term(self):
        """A 1-use term must not absorb a 47-use term just for having a slash."""
        r = self._eq({"personal-growth": 47, "personal/growth": 1})
        assert r["auto"] == []
        assert r["deferred"] == [["personal-growth", "personal/growth"]]

    def test_singular_plural_collapses_at_equal_depth(self):
        r = self._eq({"habit": 94, "habits": 25})
        assert r["auto"] == [("habit", "habits")]

    def test_unrelated_terms_are_not_grouped(self):
        r = self._eq({"tech/ai": 100, "hardware": 50, "cooking": 5})
        assert r["auto"] == [] and r["deferred"] == []

    def test_mapping_is_star_shaped_not_transitive(self):
        """Every variant attaches to exactly one canonical — no chaining."""
        r = self._eq({"a-b": 10, "ab": 5, "a/b": 2})
        variants = [v for _c, v in r["auto"]]
        assert len(variants) == len(set(variants))
        canon = {c for c, _v in r["auto"]}
        assert len(canon) <= 1


class TestAliasCanonicalization:
    """§6.2: recording an alias is useless unless the write path consults it."""

    def test_canonicalize_maps_through_cache(self, monkeypatch):
        from Evelyn.tools import taxonomy_db
        monkeypatch.setattr(taxonomy_db, "_ALIAS_CACHE", {"3dprinting": "3d-printing"})
        assert taxonomy_db.canonicalize_tags(["3dprinting", "music"]) == ["3d-printing", "music"]

    def test_canonicalize_dedupes_when_variants_converge(self, monkeypatch):
        from Evelyn.tools import taxonomy_db
        monkeypatch.setattr(taxonomy_db, "_ALIAS_CACHE", {"3dprinting": "3d-printing", "3-d-printing": "3d-printing"})
        assert taxonomy_db.canonicalize_tags(["3dprinting", "3-d-printing", "3d-printing"]) == ["3d-printing"]

    def test_unknown_tags_pass_through(self, monkeypatch):
        from Evelyn.tools import taxonomy_db
        monkeypatch.setattr(taxonomy_db, "_ALIAS_CACHE", {})
        assert taxonomy_db.canonicalize_tags(["anything"]) == ["anything"]
