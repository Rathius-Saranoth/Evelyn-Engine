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

    def test_protected_date_anchors_survive_the_frozen_path(self):
        """Regression: the first version of this copy omitted its exclusion guards,
        so entity casing mangled CY-2025/03/12 into Cy_2025/03/12 on replay."""
        assert _frozen_normalize_tag_format_000_004_002("CY-2025/03/12") == "CY-2025/03/12"
        assert _frozen_normalize_tag_format_000_004_002("status/Active") == "status/Active"


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


class TestSiblingTest:
    """§6.3.1: a hierarchy level must have siblings."""

    def _resolve(self, deferred, counts):
        import collections

        from Evelyn.tools.tag_synonym import resolve_structural_nesting
        return resolve_structural_nesting(deferred, collections.Counter(counts))

    def test_nests_when_the_level_is_real(self):
        """'work' has many children, so work-stress becomes work/stress."""
        counts = {"work-stress": 15, "work/stress": 9, "work/a": 1, "work/b": 1, "work/c": 1}
        assert self._resolve([["work-stress", "work/stress"]], counts) == [
            ("work/stress", "work-stress")
        ]

    def test_keeps_compound_when_the_level_has_no_siblings(self):
        """'me' is not a category — ME/CFS is one disease name."""
        counts = {"me-cfs": 4, "me/cfs": 1}
        assert self._resolve([["me-cfs", "me/cfs"]], counts) == [("me-cfs", "me/cfs")]

    def test_tests_the_divergence_point_not_the_leaf(self):
        """home-maintenance/chores asks about 'home', not about 'chores'."""
        counts = {"home-maintenance/chores": 3, "home/maintenance/chores": 1,
                  "home/a": 1, "home/b": 1, "home/c": 1}
        assert self._resolve([["home-maintenance/chores", "home/maintenance/chores"]], counts) == [
            ("home/maintenance/chores", "home-maintenance/chores")
        ]

    def test_tests_the_level_the_nested_form_proposes(self):
        """health/self-care/routine proposes 'health/self-care', not 'health/self'."""
        counts = {"health/self-care-routine": 2, "health/self-care/routine": 1,
                  "health/self-care/a": 1, "health/self-care/b": 1}
        assert self._resolve(
            [["health/self-care-routine", "health/self-care/routine"]], counts
        ) == [("health/self-care/routine", "health/self-care-routine")]

    def test_falls_back_to_usage_when_there_is_no_compound(self):
        """'lifestyle' has no hyphen to split, so it is one word, not a hierarchy."""
        counts = {"lifestyle/fashion": 9, "life/style/fashion": 1, "life/a": 1, "life/b": 1}
        assert self._resolve([["lifestyle/fashion", "life/style/fashion"]], counts) == [
            ("lifestyle/fashion", "life/style/fashion")
        ]


class TestRootConsolidation:
    """§9 step 6a: consolidate roots that step 5's whole-term matching could not reach."""

    def _c(self, d):
        import collections
        return collections.Counter(d)

    def test_inflected_roots_fold_to_singular(self):
        """§6.3.2: singular wins, even when the plural root is larger."""
        from Evelyn.tools.tag_synonym import root_inflection_merges
        counts = self._c({"goal/career": 1, "goals/fitness": 9, "goals/health": 8})
        assert root_inflection_merges(counts) == {"goals": "goal"}

    def test_roots_with_no_inflection_twin_are_left_alone(self):
        from Evelyn.tools.tag_synonym import root_inflection_merges
        assert root_inflection_merges(self._c({"tech/ai": 5, "health/sleep": 3})) == {}

    def test_near_synonyms_are_not_treated_as_inflections(self):
        """tech/technology is a semantic judgement, not a mechanical one."""
        from Evelyn.tools.tag_synonym import root_inflection_merges
        assert root_inflection_merges(self._c({"tech/ai": 5, "technology/ai": 2})) == {}

    def test_sparse_root_with_warrant_is_preserved(self):
        """A root tagged once but written 800 times is an unfinished category, not a dead one."""
        from Evelyn.tools.tag_synonym import weak_root_resolution
        counts = self._c({"architecture/patterns": 1, "tech/ai": 5, "tech/gis": 4})
        assert weak_root_resolution(counts, warrant={"architecture": 1697}) == {}

    def test_sparse_root_without_warrant_is_dismantled(self):
        """Nobody writes 'social-relations', so nobody would search it."""
        from Evelyn.tools.tag_synonym import weak_root_resolution
        counts = self._c({"social-relations/family": 1, "tech/ai": 5, "tech/gis": 4})
        assert weak_root_resolution(counts, warrant={"social-relations": 0}) == {
            "social-relations/family": "social-relations-family"
        }

    def test_missing_warrant_defaults_to_keeping(self):
        """Dismantling without evidence is the failure mode, so absence means preserve."""
        from Evelyn.tools.tag_synonym import weak_root_resolution
        counts = self._c({"mystery/thing": 1, "tech/ai": 5, "tech/gis": 4})
        assert weak_root_resolution(counts, warrant={}) == {}

    def test_entity_never_becomes_a_domain_however_common(self):
        """§2: a name written 1,481 times is still an individual, not a category."""
        from Evelyn.tools.tag_synonym import weak_root_resolution
        counts = self._c({"someone/preferences": 1, "tech/ai": 5, "tech/gis": 4})
        assert weak_root_resolution(counts, warrant={"someone": -1}) == {
            "someone/preferences": "someone-preferences"
        }

    def test_compound_with_established_head_is_renested(self):
        """ai-behavior is a missed nesting when 'ai' is demonstrably a real level."""
        from Evelyn.tools.tag_synonym import weak_root_resolution
        counts = self._c({
            "ai-behavior/dialogue": 1,
            **{f"ai/{k}": 2 for k in ("rag", "tools", "ethics", "models", "agents")},
        })
        assert weak_root_resolution(counts) == {"ai-behavior/dialogue": "ai/behavior/dialogue"}

    def test_conjunction_needs_no_special_case(self):
        """'X and Y' is two categories a generator could not choose between — and warrant
        catches it without a rule of its own, because nobody writes the phrase."""
        from Evelyn.tools.tag_synonym import weak_root_resolution
        counts = self._c({"protocol-and-routine/daily": 1, "tech/ai": 5, "tech/gis": 4})
        assert weak_root_resolution(counts, warrant={"protocol-and-routine": 0}) == {
            "protocol-and-routine/daily": "protocol-and-routine-daily"
        }

    def test_compound_without_a_strong_parent_is_left_alone(self):
        """Guessing is worse than waiting for a later pass with more evidence."""
        from Evelyn.tools.tag_synonym import weak_root_resolution
        counts = self._c({"3d-modeling/topology": 1, "tech/ai": 5, "tech/gis": 4})
        assert weak_root_resolution(counts) == {}

    def test_ordering_matters_merge_before_flatten(self):
        """A root weak alone may clear the bar once its variants fold in."""
        import collections

        from Evelyn.tools.tag_synonym import root_inflection_merges, weak_root_resolution
        counts = self._c({"goal-setting/career": 1, "goals-setting/fitness": 2,
                          "goal/a": 1, "goal/b": 1, "goal/c": 1, "goal/d": 1, "goal/e": 1})
        merges = root_inflection_merges(counts)
        merged = collections.Counter()
        for tag, n in counts.items():
            root, rest = tag.split("/", 1)
            merged[f"{merges.get(root, root)}/{rest}"] += n
        # After merging, goals-setting folds into goal-setting, which then re-nests under
        # the established 'goal' root rather than being judged sparse in isolation.
        assert all(v.startswith("goal/") for v in weak_root_resolution(merged).values())


class TestFlatAdoption:
    """§9 step 6b: the sibling test applied to compounds with no nested twin."""

    def _c(self, d):
        import collections
        return collections.Counter(d)

    def test_compound_nests_under_an_established_head(self):
        from Evelyn.tools.tag_synonym import adopt_flat_compounds
        counts = self._c({
            "productivity-tips": 26,
            **{f"productivity/{k}": 2 for k in ("focus", "habits", "tools", "time", "energy")},
        })
        assert adopt_flat_compounds(counts) == {"productivity-tips": "productivity/tips"}

    def test_head_that_names_nothing_is_left_alone(self):
        """The bar is evidence, not plausibility — no speculative nesting."""
        from Evelyn.tools.tag_synonym import adopt_flat_compounds
        assert adopt_flat_compounds(self._c({"llmops-basics": 3, "tech/ai": 9})) == {}

    def test_single_words_cannot_adopt(self):
        from Evelyn.tools.tag_synonym import adopt_flat_compounds
        counts = self._c({"hardware": 419, **{f"hardware/{k}": 2 for k in "abcde"}})
        assert adopt_flat_compounds(counts) == {}

    def test_already_nested_terms_are_untouched(self):
        from Evelyn.tools.tag_synonym import adopt_flat_compounds
        counts = self._c({"tech/ai-models": 3, **{f"tech/{k}": 2 for k in "abcde"}})
        assert adopt_flat_compounds(counts) == {}

    def test_only_the_first_hyphen_becomes_a_slash(self):
        """ai-prompt-engineering nests as ai/prompt-engineering, not ai/prompt/engineering."""
        from Evelyn.tools.tag_synonym import adopt_flat_compounds
        counts = self._c({"ai-prompt-engineering": 4, **{f"ai/{k}": 2 for k in "abcde"}})
        assert adopt_flat_compounds(counts) == {"ai-prompt-engineering": "ai/prompt-engineering"}


class TestReviewParsing:
    """§9 step 6c: the reviewed document is the instruction, parsed literally."""

    def _parse(self, tmp_path, body):
        import sys
        sys.path.insert(0, "scripts")
        from parse_tag_merge_review import parse_review
        f = tmp_path / "review.md"
        f.write_text(body, encoding="utf-8")
        return parse_review(str(f))

    def test_ticked_line_in_a_merge_block_is_applied(self, tmp_path):
        r = self._parse(tmp_path, "### [merge] `routine` (10)\n- [x] `daily-routine` (4)  ->  `routine`\n")
        assert r["merges"] == {"daily-routine": "routine"}

    def test_unticked_line_is_a_rejection(self, tmp_path):
        r = self._parse(tmp_path, "### [merge] `routine` (10)\n- [ ] `daily-routine` (4)  ->  `routine`\n")
        assert r["merges"] == {} and r["rejected"] == ["daily-routine"]

    def test_remove_block_takes_the_canonical_too(self, tmp_path):
        """The whole group leaves — the canonical is not a survivor to merge into."""
        r = self._parse(tmp_path, "### [remove] `caring-for-partner` (14)\n- [x] `caring-for-someone` (5)  ->  `caring-for-partner`\n")
        assert r["merges"] == {}
        assert sorted(r["removals"]) == ["caring-for-partner", "caring-for-someone"]

    def test_removal_wins_over_a_merge_elsewhere(self, tmp_path):
        """A term marked for removal must not survive as some other block's target."""
        body = ("### [merge] `x` (9)\n- [x] `doomed` (3)  ->  `x`\n"
                "### [remove] `doomed` (3)\n")
        r = self._parse(tmp_path, body)
        assert "doomed" not in r["merges"]
        assert "doomed" in r["removals"]


class TestTransitiveCanonicalization:
    """§6.2: aliases chain across migration passes, so one hop is not enough."""

    def test_resolves_a_chain_to_its_end(self, monkeypatch):
        from Evelyn.tools import taxonomy_db
        monkeypatch.setattr(taxonomy_db, "_ALIAS_CACHE", {
            "sleep-tracking": "sleep/tracking",
            "sleep/tracking": "health/sleep/tracking",
        })
        assert taxonomy_db.canonicalize_tags(["sleep-tracking"]) == ["health/sleep/tracking"]

    def test_chain_ending_in_removal_drops_the_tag(self, monkeypatch):
        from Evelyn.tools import taxonomy_db
        monkeypatch.setattr(taxonomy_db, "_ALIAS_CACHE", {"a": "b", "b": ""})
        assert taxonomy_db.canonicalize_tags(["a", "keep"]) == ["keep"]

    def test_cycle_does_not_hang(self, monkeypatch):
        """Two passes disagreeing on direction must not loop forever."""
        from Evelyn.tools import taxonomy_db
        monkeypatch.setattr(taxonomy_db, "_ALIAS_CACHE", {"x": "y", "y": "x"})
        assert taxonomy_db.canonicalize_tags(["x"]) in (["y"], ["x"])

    def test_chains_converging_are_deduped(self, monkeypatch):
        from Evelyn.tools import taxonomy_db
        monkeypatch.setattr(taxonomy_db, "_ALIAS_CACHE", {"a": "mid", "b": "mid", "mid": "final"})
        assert taxonomy_db.canonicalize_tags(["a", "b"]) == ["final"]


class TestOneOffPhraseRetirement:
    """§6.3.4: verbose AND unshared together — either condition alone is wrong."""

    def _c(self, d):
        import collections
        return collections.Counter(d)

    def test_verbose_and_unshared_is_retired(self):
        from Evelyn.tools.tag_synonym import one_off_phrase_tags
        assert one_off_phrase_tags(self._c({"heartwarming-animal-encounters": 1})) == [
            "heartwarming-animal-encounters"
        ]

    def test_verbose_but_well_used_survives(self):
        """Length alone would condemn legitimate compound terms."""
        from Evelyn.tools.tag_synonym import one_off_phrase_tags
        assert one_off_phrase_tags(self._c({"work-life-balance": 40})) == []

    def test_unshared_but_short_survives(self):
        """Low use alone would condemn structure that is merely young (§6.3.3)."""
        from Evelyn.tools.tag_synonym import one_off_phrase_tags
        assert one_off_phrase_tags(self._c({"astronomy": 1, "sunday-vlog": 1})) == []

    def test_nested_terms_are_never_retired_however_verbose(self):
        """A slash means something placed it in the tree; that structure is the expensive part."""
        from Evelyn.tools.tag_synonym import one_off_phrase_tags
        assert one_off_phrase_tags(self._c({"home/chore/laundry-and-groceries": 1})) == []

    def test_protected_namespaces_are_untouched(self):
        from Evelyn.tools.tag_synonym import one_off_phrase_tags
        assert one_off_phrase_tags(self._c({"CY-2026/09/20": 1, "kanban-in-progress-now": 1})) == []
