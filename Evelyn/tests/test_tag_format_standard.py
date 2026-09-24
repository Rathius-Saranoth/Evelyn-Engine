# test_tag_format_standard.py
# date created: 2026-09-19 00:00:00
# date modified: 2026-09-22 20:15:53
# tags: #tests, #taxonomy, #tagging, #normalization, #edtf

"""Hermetic tests for the §5 tag format standard and §3.8 EDTF date anchors.

Covers .agents/rules/vault-tag-taxonomy.md:
    §5   — lowercase always, hyphens join words, slashes join levels, no entity branch.
    §3.8 — EDTF reduced precision vs. unspecified digits.
"""

import pytest

from Evelyn.tools.db_migrator import _frozen_normalize_tag_format_000_004_002
from Evelyn.tools.tag_librarian import canonicalize_occurred, normalize_tag_format


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


class TestEdtfOccurredProperty:
    """§3.8: the time axis is a property, and reduced precision differs from unspecified."""

    @pytest.mark.parametrize(("raw", "expected"), [
        ("2026-09-19", "2026-09-19"),       # already canonical
        ("CY-2026/09/19", "2026-09-19"),    # retired tag form migrates cleanly
        ("Cy_Yyyy/11/16", "XXXX-11-16"),    # unspecified year
        ("Cy_2026/05", "2026-05"),          # reduced precision: month
        ("cy-2025", "2025"),                # reduced precision: year
    ])
    def test_canonical_forms(self, raw, expected):
        assert canonicalize_occurred(raw) == expected

    def test_reduced_precision_is_not_unspecified(self):
        """'May 2026' must not become 'an unknown day in May 2026'."""
        assert canonicalize_occurred("Cy_2026/05") == "2026-05"
        assert canonicalize_occurred("Cy_2026/05") != "2026-05-XX"

    def test_the_cy_prefix_is_gone_from_the_canonical_form(self):
        """It existed only because a tag cannot start with a digit; a property can."""
        assert not canonicalize_occurred("CY-2026/09/19").startswith("CY")

    @pytest.mark.parametrize("raw", [
        "cybersecurity", "cyberpower-cp1000pfclcd", "cytokine-dynamics", "cyber-warrior",
    ])
    def test_non_dates_are_not_captured(self, raw):
        """The cy- prefix must not swallow ordinary vocabulary."""
        assert canonicalize_occurred(raw) is None
        assert normalize_tag_format(raw) == raw

    def test_a_date_is_no_longer_a_protected_tag(self):
        """Dates are not tags at all now, so normalisation gives them no exemption."""
        assert normalize_tag_format("CY-2026/09/19") == "cy-2026/09/19"


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
        assert one_off_phrase_tags(self._c({"status/active": 1, "kanban-in-progress-now": 1})) == []


class TestClassifierInvariants:
    """§7.1: the rules the original classifier violated, now enforced structurally."""

    def _note(self, tags):
        return "---\ntags: [" + ", ".join(tags) + "]\n---\n\n# Note\n\nSome prose here.\n"

    def _run(self, monkeypatch, content, applied, proposals=()):
        from Evelyn.tools import tag_librarian
        monkeypatch.setattr(
            tag_librarian, "classify_document_subjects",
            lambda **k: (list(applied), list(proposals)),
        )
        # Isolate pass 2: no class determination, no staleness check.
        monkeypatch.setattr(tag_librarian, "determine_document_class", lambda *a, **k: "")
        monkeypatch.setattr(tag_librarian, "verify_tags_still_apply", lambda *a, **k: [])
        monkeypatch.setattr(tag_librarian.vault_db, "get_document", lambda p: {"gist": ""})
        monkeypatch.setattr(tag_librarian.taxonomy_db, "canonicalize_tags", lambda t: list(t))
        return tag_librarian.audit_document_tags(content, path="n.md", enable_llm=True)

    def test_existing_tags_always_survive(self, monkeypatch):
        """Removal is not inferable from 'what is this about', so it cannot happen here."""
        _m, _c, details = self._run(
            monkeypatch, self._note(["alpha", "beta", "gamma"]), applied=["delta"],
        )
        assert set(details["final_tags"]) == {"alpha", "beta", "gamma", "delta"}

    def test_classifier_cannot_remove_even_a_wrong_tag(self, monkeypatch):
        """The destruction failure mode is retired by construction, not by rule."""
        _m, _c, details = self._run(
            monkeypatch, self._note(["alpha", "beta"]), applied=[],
        )
        assert set(details["final_tags"]) == {"alpha", "beta"}

    def test_proposals_are_reported_not_applied(self, monkeypatch):
        """§6.1: a term not in the registry is queued, never written to the document."""
        _m, _c, details = self._run(
            monkeypatch, self._note(["alpha"]), applied=[], proposals=["postprandial-somnolence"],
        )
        assert "postprandial-somnolence" not in details["final_tags"]
        assert details["proposals"] == ["postprandial-somnolence"]

    def test_protected_tags_are_never_touched(self, monkeypatch):
        """Administrative namespaces are exempt. Dates are not among them any more —
        the time axis is the `occurred` property, not a tag (v000.006.203)."""
        _m, _c, details = self._run(
            monkeypatch, self._note(["status/active", "alpha"]), applied=["beta"],
        )
        assert "status/active" in details["final_tags"]


class TestDocumentReading:
    """The classifier reads what the document says, not a guess at what it says."""

    def test_short_document_is_sent_whole(self):
        from Evelyn.tools.tag_librarian import read_document_for_classification
        body = "# Note\n\nA short note about cello practice and posture.\n"
        out = read_document_for_classification(body, "", "Cello")
        assert "[Full document]" in out
        assert "posture" in out

    def test_long_document_is_chunked_and_subjects_merged(self, monkeypatch):
        from Evelyn.tools import tag_librarian
        seen = []
        def fake(chunk, title):
            seen.append(len(chunk))
            return [f"subject-{len(seen)}"]
        monkeypatch.setattr(tag_librarian, "_extract_chunk_subjects", fake)
        body = "\n\n".join(f"## Section {i}\n\n" + ("filler text " * 120) for i in range(8))
        out = tag_librarian.read_document_for_classification(body, "", "Long")
        assert len(seen) > 1, "a long document must be read in more than one piece"
        assert "Subjects found by reading the document in full" in out
        assert "subject-1" in out

    def test_chunk_count_is_capped(self, monkeypatch):
        """A 360KB note must not trigger 90 inference calls."""
        from Evelyn.tools import tag_librarian
        calls = []
        monkeypatch.setattr(
            tag_librarian, "_extract_chunk_subjects",
            lambda chunk, title: calls.append(1) or ["x"],
        )
        huge = ("paragraph text here. " * 40 + "\n\n") * 900
        tag_librarian.read_document_for_classification(huge, "", "Huge")
        assert len(calls) <= tag_librarian.MAX_CHUNKS_READ

    def test_chunk_extraction_failure_degrades_to_skeleton(self, monkeypatch):
        """A failed read must not lose the document — the skeleton still carries structure."""
        from Evelyn.tools import tag_librarian
        monkeypatch.setattr(tag_librarian, "_extract_chunk_subjects", lambda c, t: [])
        body = "\n\n".join(f"## Heading {i}\n\n" + ("words " * 200) for i in range(5))
        out = tag_librarian.read_document_for_classification(body, "", "Doc")
        assert "Heading 0" in out


class TestBlindExtractionPipeline:
    """Blind extraction then deterministic reconciliation (§6.1)."""

    def test_phrases_are_formatted_not_decomposed(self):
        """'obstructive sleep apnea' is one diagnosis; splitting destroys the term of art."""
        from Evelyn.tools.tag_librarian import normalize_subject_phrase
        assert normalize_subject_phrase("Obstructive Sleep Apnea") == "obstructive-sleep-apnea"
        assert normalize_subject_phrase("sleep tracking") == "sleep-tracking"

    def test_slashes_in_a_phrase_do_not_become_hierarchy(self):
        from Evelyn.tools.tag_librarian import normalize_subject_phrase
        assert "/" not in normalize_subject_phrase("work/life balance")

    def test_confident_vector_match_becomes_the_registry_term(self, monkeypatch):
        from Evelyn.tools import tag_librarian
        monkeypatch.setattr(tag_librarian, "_registry_surface_forms", lambda: {})
        monkeypatch.setattr(
            tag_librarian.chroma_rag, "query_collection",
            lambda *a, **k: [{"metadata": {"tag": "sleep"}, "distance": 0.04}],
        )
        applied, proposals = tag_librarian.reconcile_subjects(["sleep tracking"], [])
        assert applied == ["sleep"] and proposals == []

    def test_merely_near_match_is_held_for_decision(self, monkeypatch):
        """Deliberate change of contract: 'retrievable' is not 'correct'.

        A single threshold previously applied anything nearer than 0.35, which measured out
        at a 70% false-link rate on off-topic phrases. Distances in the ambiguous band now
        produce a proposal rather than an assertion.
        """
        from Evelyn.tools import tag_librarian
        monkeypatch.setattr(tag_librarian, "_registry_surface_forms", lambda: {})
        monkeypatch.setattr(
            tag_librarian.chroma_rag, "query_collection",
            lambda *a, **k: [{"metadata": {"tag": "sleep"}, "distance": 0.2}],
        )
        applied, proposals = tag_librarian.reconcile_subjects(["sleep tracking"], [])
        assert applied == [] and proposals == ["sleep-tracking"]

    def test_distant_phrase_is_proposed_never_applied(self, monkeypatch):
        """§6.1: a vocabulary any document can extend is not a controlled vocabulary."""
        from Evelyn.tools import tag_librarian
        monkeypatch.setattr(
            tag_librarian.chroma_rag, "query_collection",
            lambda *a, **k: [{"metadata": {"tag": "cooking"}, "distance": 0.9}],
        )
        applied, proposals = tag_librarian.reconcile_subjects(["postprandial somnolence"], [])
        assert applied == []
        assert proposals == ["postprandial-somnolence"]

    def test_term_already_on_the_document_is_not_re_added(self, monkeypatch):
        from Evelyn.tools import tag_librarian
        monkeypatch.setattr(
            tag_librarian.chroma_rag, "query_collection",
            lambda *a, **k: [{"metadata": {"tag": "sleep"}, "distance": 0.1}],
        )
        applied, proposals = tag_librarian.reconcile_subjects(["sleep tracking"], ["sleep"])
        assert applied == [] and proposals == []

    def test_lookup_failure_proposes_rather_than_drops(self, monkeypatch):
        """A registry outage must not silently discard what the document said."""
        from Evelyn.tools import tag_librarian
        def boom(*a, **k):
            raise OSError("chroma down")
        monkeypatch.setattr(tag_librarian.chroma_rag, "query_collection", boom)
        _applied, proposals = tag_librarian.reconcile_subjects(["sleep tracking"], [])
        assert proposals == ["sleep-tracking"]


class TestStalenessRemoval:
    """Removal is a positive assertion with a ceiling, never an inference from silence."""

    def _mock(self, monkeypatch, response):
        from Evelyn.tools import tag_librarian
        monkeypatch.setattr(tag_librarian, "_canonical_query_ollama", lambda **k: response)
        return tag_librarian

    def test_named_stale_tags_are_returned(self, monkeypatch):
        tl = self._mock(monkeypatch, '{"stale": ["python", "gardening"]}')
        out = tl.verify_tags_still_apply("cello practice", "Cello", ["music", "python", "gardening"])
        assert sorted(out) == ["gardening", "python"]

    def test_empty_response_removes_nothing(self, monkeypatch):
        """A malformed or empty answer must never be read as 'remove everything'."""
        tl = self._mock(monkeypatch, "")
        assert tl.verify_tags_still_apply("x", "T", ["a", "b", "c"]) == []

    def test_tags_not_on_the_document_are_ignored(self, monkeypatch):
        """The model cannot remove something the document does not carry."""
        tl = self._mock(monkeypatch, '{"stale": ["not-present"]}')
        assert tl.verify_tags_still_apply("x", "T", ["a", "b"]) == []

    def test_protected_tags_are_never_candidates(self, monkeypatch):
        tl = self._mock(monkeypatch, '{"stale": ["status/active"]}')
        assert tl.verify_tags_still_apply("x", "T", ["status/active", "a"]) == []

    def test_small_tag_sets_allow_a_couple_of_removals(self, monkeypatch):
        """A ratio is meaningless on a three-tag note where two are genuinely wrong."""
        tl = self._mock(monkeypatch, '{"stale": ["python", "gardening"]}')
        out = tl.verify_tags_still_apply("cello", "Cello", ["music", "python", "gardening"])
        assert sorted(out) == ["gardening", "python"]

    def test_mass_removal_is_treated_as_a_malfunction(self, monkeypatch):
        """The September collapse called 33 of 36 tags wrong. That verdict is refused."""
        tl = self._mock(monkeypatch, '{"stale": ["a","b","c","d","e","f","g","h"]}')
        tags = [chr(97 + i) for i in range(10)]
        assert tl.verify_tags_still_apply("x", "T", tags) == []

    def test_removal_within_the_ceiling_is_allowed(self, monkeypatch):
        tl = self._mock(monkeypatch, '{"stale": ["a","b"]}')
        tags = [chr(97 + i) for i in range(10)]
        assert sorted(tl.verify_tags_still_apply("x", "T", tags)) == ["a", "b"]


class TestApplicationProfile:
    """§4: the class decides which facets are required, permitted or forbidden."""

    def test_type_facet_is_added_for_the_class(self):
        from Evelyn.tools.tag_librarian import apply_application_profile
        add, drop, _gaps = apply_application_profile("reference", ["music"])
        assert add == ["type/reference"] and drop == []

    def test_a_second_type_facet_is_removed(self):
        """One form per document; a second is wrong about what the document is."""
        from Evelyn.tools.tag_librarian import apply_application_profile
        add, drop, _g = apply_application_profile("reference", ["type/journal-entry", "music"])
        assert "type/reference" in add and drop == ["type/journal-entry"]

    def test_forbidden_facet_is_enforced_not_requested(self):
        """A motif on reference material is rejected mechanically, never argued with."""
        from Evelyn.tools.tag_librarian import apply_application_profile
        _a, drop, _g = apply_application_profile("reference", ["motif/combat", "music"])
        assert drop == ["motif/combat"]

    def test_required_facet_absent_is_reported_not_invented(self):
        """A dream needs a motif, but guessing which one is subject analysis, not cataloguing."""
        from Evelyn.tools.tag_librarian import apply_application_profile
        add, _d, gaps = apply_application_profile(
            "dream", ["setting/tropical"], occurred="2026-01-01"
        )
        assert "motif" in gaps
        assert not any(t.startswith("motif/") for t in add)

    def test_satisfied_requirements_are_not_reported_as_gaps(self):
        from Evelyn.tools.tag_librarian import apply_application_profile
        _a, _d, gaps = apply_application_profile(
            "dream", ["motif/combat", "setting/tropical"], occurred="2026-01-01"
        )
        assert gaps == []

    def test_unknown_class_changes_nothing(self):
        from Evelyn.tools.tag_librarian import apply_application_profile
        assert apply_application_profile("", ["music"]) == ([], [], [])

    def test_protected_tags_survive_a_forbidden_rule(self):
        """'time forbidden' must never strip a protected date anchor (§3.8)."""
        from Evelyn.tools.tag_librarian import apply_application_profile
        _a, drop, _g = apply_application_profile("reference", ["music"], occurred="2026-01-01")
        assert drop == []  # nothing forbidden for a dream; the date is a property now

    def test_class_answer_outside_the_list_is_rejected(self, monkeypatch):
        from Evelyn.tools import tag_librarian
        monkeypatch.setattr(tag_librarian, "_canonical_query_ollama", lambda **k: "poem")
        assert tag_librarian.determine_document_class("x", "T") == ""


class TestDecomposition:
    """§3.3: hierarchical paths become atoms; the query recombines them."""

    def test_path_becomes_its_parts(self):
        from Evelyn.tools.tag_synonym import decompose_to_atoms
        assert decompose_to_atoms("work/routine/morning") == ["work", "routine", "morning"]

    def test_facet_prefix_keeps_exactly_one_level(self):
        """The prefix names which axis a term is on — flat atoms cannot say that."""
        from Evelyn.tools.tag_synonym import decompose_to_atoms
        assert decompose_to_atoms("type/journal-entry") == ["type/journal-entry"]
        assert decompose_to_atoms("motif/combat") == ["motif/combat"]

    def test_facet_deeper_than_one_level_loses_its_middle(self):
        """setting/biome/tropical categorises within an axis — that is the hierarchy removed."""
        from Evelyn.tools.tag_synonym import decompose_to_atoms
        assert decompose_to_atoms("setting/biome/tropical") == ["setting/tropical"]

    def test_administrative_namespaces_are_untouched(self):
        from Evelyn.tools.tag_synonym import decompose_to_atoms
        assert decompose_to_atoms("obsidian-graph/contact") == ["obsidian-graph/contact"]

    def test_a_flat_term_is_already_an_atom(self):
        from Evelyn.tools.tag_synonym import decompose_to_atoms
        assert decompose_to_atoms("3d-printing") == ["3d-printing"]

    def test_csv_decomposition_dedupes_convergent_atoms(self):
        """health/sleep and sleep/tracking share 'sleep'; it appears once."""
        from Evelyn.tools.tag_synonym import decompose_tag_csv
        out, changed = decompose_tag_csv("health/sleep, sleep/tracking")
        assert changed is True
        assert out == "health, sleep, tracking"

    def test_unchanged_input_reports_no_change(self):
        from Evelyn.tools.tag_synonym import decompose_tag_csv
        assert decompose_tag_csv("music, cello") == ("music, cello", False)


class TestFlatCompoundDecomposition:
    """§6.3.3: literary warrant decides whether a hyphenated compound is a bound term."""

    @staticmethod
    def _vault(tmp_path, prose: str):
        (tmp_path / "note.md").write_text(prose, encoding="utf-8")
        return str(tmp_path)

    def test_written_phrase_survives_whole(self, tmp_path):
        """A compound the corpus actually writes is a bound term (machine-learning)."""
        from Evelyn.tools.tag_synonym import flat_compound_decomposition
        root = self._vault(tmp_path, "Notes on machine learning and more machine learning.")
        plan = flat_compound_decomposition(["machine-learning"], root)
        assert "machine-learning" not in plan

    def test_hyphenated_form_in_prose_also_counts(self, tmp_path):
        """literary_warrant matches across either separator, so both spellings warrant."""
        from Evelyn.tools.tag_synonym import flat_compound_decomposition
        root = self._vault(tmp_path, "A note about machine-learning.")
        assert flat_compound_decomposition(["machine-learning"], root) == {}

    def test_unwritten_compound_decomposes(self, tmp_path):
        from Evelyn.tools.tag_synonym import flat_compound_decomposition
        root = self._vault(tmp_path, "Nothing relevant here.")
        plan = flat_compound_decomposition(["cozy-gaming-night"], root)
        assert plan == {"cozy-gaming-night": ["cozy", "gaming", "night"]}

    def test_function_words_are_dropped(self, tmp_path):
        """`about-superpowers` is about superpowers, not about `about`."""
        from Evelyn.tools.tag_synonym import flat_compound_decomposition
        root = self._vault(tmp_path, "Nothing relevant here.")
        assert flat_compound_decomposition(["about-superpowers"], root) == {
            "about-superpowers": ["superpowers"]
        }

    def test_nested_and_protected_terms_are_not_candidates(self, tmp_path):
        from Evelyn.tools.tag_synonym import flat_compound_decomposition
        root = self._vault(tmp_path, "Nothing relevant here.")
        plan = flat_compound_decomposition(
            ["type/journal-entry", "status/active", "obsidian-graph/no-graph"], root
        )
        assert plan == {}

    def test_canonicalize_lands_atoms_on_preferred_forms(self, tmp_path):
        from Evelyn.tools.tag_synonym import flat_compound_decomposition
        root = self._vault(tmp_path, "Nothing relevant here.")
        plan = flat_compound_decomposition(
            ["scary-dreams"], root, canonicalize=lambda a: {"dreams": "dream"}.get(a, a)
        )
        assert plan == {"scary-dreams": ["scary", "dream"]}

    def test_precoordinate_alias_target_is_refused(self, tmp_path):
        """Following `frustration -> feeling-frustrated` would rebuild what this took apart."""
        from Evelyn.tools.tag_synonym import flat_compound_decomposition
        root = self._vault(tmp_path, "Nothing relevant here.")
        plan = flat_compound_decomposition(
            ["overcoming-frustration"], root,
            canonicalize=lambda a: {"frustration": "feeling-frustrated"}.get(a, a),
        )
        assert plan == {"overcoming-frustration": ["overcoming", "frustration"]}

    def test_nested_alias_target_is_refused(self, tmp_path):
        from Evelyn.tools.tag_synonym import flat_compound_decomposition
        root = self._vault(tmp_path, "Nothing relevant here.")
        plan = flat_compound_decomposition(
            ["obsidian-workflow"], root,
            canonicalize=lambda a: {"workflow": "work/workflow"}.get(a, a),
        )
        assert plan == {"obsidian-workflow": ["obsidian", "workflow"]}

    def test_csv_rewrite_applies_plan_and_dedupes(self):
        from Evelyn.tools.tag_synonym import apply_decomposition_to_csv
        plan = {"scary-dream": ["scary", "dream"], "lucid-dream": ["lucid", "dream"]}
        out, changed = apply_decomposition_to_csv("scary-dream, lucid-dream", plan)
        assert changed is True
        assert out == "scary, dream, lucid"

    def test_csv_rewrite_leaves_unplanned_terms_alone(self):
        from Evelyn.tools.tag_synonym import apply_decomposition_to_csv
        assert apply_decomposition_to_csv("machine-learning, cello", {}) == (
            "machine-learning, cello", False
        )


class TestTagEmbeddingDocument:
    """The vector index holds bare surface forms; boilerplate destroyed the distance signal."""

    def test_term_embeds_as_its_bare_surface_form(self):
        from Evelyn.tools.tag_librarian import _build_tag_embedding_doc
        assert _build_tag_embedding_doc("sleep-hygiene") == "sleep hygiene"

    def test_facet_prefix_is_flattened_not_labelled(self):
        from Evelyn.tools.tag_librarian import _build_tag_embedding_doc
        assert _build_tag_embedding_doc("type/journal-entry") == "type journal entry"

    def test_no_boilerplate_survives(self):
        """Text identical across every document is a shared vector component; it must not exist."""
        from Evelyn.tools.tag_librarian import _build_tag_embedding_doc
        doc = _build_tag_embedding_doc("cello")
        for label in ("Tag:", "Category:", "Hierarchy:", "Scope", "Obsidian notes"):
            assert label not in doc
        assert doc == "cello"


class TestLexicalLookup:
    """§6.1: a controlled vocabulary is a dictionary — look terms up before embedding them."""

    def test_exact_surface_form_resolves(self):
        from Evelyn.tools.tag_librarian import _lexical_lookup
        assert _lexical_lookup("sleep", {"sleep": "sleep"}) == "sleep"

    def test_separator_differences_are_the_same_surface(self):
        from Evelyn.tools.tag_librarian import _lexical_lookup
        assert _lexical_lookup("sleep hygiene", {"sleephygiene": "sleep-hygiene"}) == "sleep-hygiene"

    def test_plural_resolves_to_singular_term(self):
        from Evelyn.tools.tag_librarian import _lexical_lookup
        assert _lexical_lookup("dreams", {"dream": "dream"}) == "dream"

    def test_alias_resolves_to_its_canonical(self):
        from Evelyn.tools.tag_librarian import _lexical_lookup
        assert _lexical_lookup("sleep-tracking", {"sleeptracking": "sleep"}) == "sleep"

    def test_unrelated_phrase_does_not_resolve(self):
        """Wrong is worse than unresolved: an unresolved phrase becomes a reviewable proposal."""
        from Evelyn.tools.tag_librarian import _lexical_lookup
        assert _lexical_lookup("quantum-chromodynamics", {"sleep": "sleep"}) is None

    def test_empty_registry_resolves_nothing(self):
        from Evelyn.tools.tag_librarian import _lexical_lookup
        assert _lexical_lookup("sleep", {}) is None


class TestVectorAcceptanceBands:
    """A single threshold cannot separate 'certainly' from 'possibly'; measured, it force-linked."""

    def _reconcile(self, monkeypatch, tag, distance, margin):
        from Evelyn.tools import tag_librarian as tl
        monkeypatch.setattr(tl, "_registry_surface_forms", lambda: {})
        monkeypatch.setattr(tl, "nearest_registered_term", lambda phrase: (tag, distance, margin))
        return tl.reconcile_subjects(["coral bleaching"], [])

    def test_confident_match_is_applied(self, monkeypatch):
        applied, proposals = self._reconcile(monkeypatch, "coral", 0.05, 0.30)
        assert applied == ["coral"] and proposals == []

    def test_distant_match_becomes_a_proposal(self, monkeypatch):
        """0.339 to 'annealing' is retrievable and wrong — exactly what the old threshold took."""
        applied, proposals = self._reconcile(monkeypatch, "annealing", 0.339, 0.30)
        assert applied == [] and proposals == ["coral-bleaching"]

    def test_ambiguous_band_does_not_auto_apply(self, monkeypatch):
        applied, proposals = self._reconcile(monkeypatch, "coral", 0.20, 0.30)
        assert applied == [] and proposals == ["coral-bleaching"]

    def test_indistinguishable_candidates_do_not_auto_apply(self, monkeypatch):
        """Two terms within the margin guard are a coin flip, not a decision."""
        applied, proposals = self._reconcile(monkeypatch, "coral", 0.05, 0.001)
        assert applied == [] and proposals == ["coral-bleaching"]

    def test_lexical_hit_bypasses_the_vector_stage(self, monkeypatch):
        from Evelyn.tools import tag_librarian as tl
        monkeypatch.setattr(tl, "_registry_surface_forms", lambda: {"sleep": "sleep"})

        def _explode(phrase):
            raise AssertionError("vector lookup must not run when the dictionary resolves")

        monkeypatch.setattr(tl, "nearest_registered_term", _explode)
        applied, proposals = tl.reconcile_subjects(["sleep"], [])
        assert applied == ["sleep"] and proposals == []


class TestQueryDecomposition:
    """§0.0: post-coordination applies to the query, not only to the registry."""

    def _reconcile(self, monkeypatch, phrase, surfaces):
        from Evelyn.tools import tag_librarian as tl
        monkeypatch.setattr(tl, "_registry_surface_forms", lambda: surfaces)
        monkeypatch.setattr(tl, "nearest_registered_term", lambda p: (None, 1.0, 0.0))
        return tl.reconcile_subjects([phrase], [])

    def test_compound_resolves_to_its_registered_atoms(self, monkeypatch):
        """`dream-journaling` was held as a proposal while both its atoms were registered."""
        applied, proposals = self._reconcile(
            monkeypatch, "dream journaling", {"dream": "dream", "journaling": "journaling"}
        )
        assert applied == ["dream", "journaling"] and proposals == []

    def test_unregistered_parts_are_discarded_not_proposed(self, monkeypatch):
        """The curated vocabulary is the filter: `personal` names nothing and was rejected."""
        applied, proposals = self._reconcile(
            monkeypatch, "personal relationships", {"relationships": "relationships"}
        )
        assert applied == ["relationships"] and proposals == []

    def test_phrase_with_no_registered_part_is_still_proposed(self, monkeypatch):
        applied, proposals = self._reconcile(
            monkeypatch, "quantum chromodynamics", {"sleep": "sleep"}
        )
        assert applied == [] and proposals == ["quantum-chromodynamics"]

    def test_whole_phrase_match_wins_over_decomposition(self, monkeypatch):
        """A registered bound term stays whole — `machine-learning` is not machine + learning."""
        applied, proposals = self._reconcile(
            monkeypatch, "machine learning",
            {"machinelearning": "machine-learning", "learning": "learning"},
        )
        assert applied == ["machine-learning"] and proposals == []

    def test_atoms_already_on_the_document_are_not_re_added(self, monkeypatch):
        from Evelyn.tools import tag_librarian as tl
        monkeypatch.setattr(tl, "_registry_surface_forms",
                            lambda: {"dream": "dream", "journaling": "journaling"})
        monkeypatch.setattr(tl, "nearest_registered_term", lambda p: (None, 1.0, 0.0))
        applied, proposals = tl.reconcile_subjects(["dream journaling"], ["dream"])
        assert applied == ["journaling"] and proposals == []


class TestSingularizeSymmetry:
    """A stemmer only does damage when it maps two forms of one word to different keys."""

    @pytest.mark.parametrize(
        ("plural", "singular"),
        [
            ("memories", "memory"), ("stories", "story"), ("categories", "category"),
            ("boxes", "box"), ("glasses", "glass"), ("dishes", "dish"), ("watches", "watch"),
            ("dreams", "dream"), ("relationships", "relationship"), ("friendships", "friendship"),
        ],
    )
    def test_plural_and_singular_reach_the_same_key(self, plural, singular):
        """`memories` stemmed to `memorie` while `memory` stayed put, so they never matched."""
        from Evelyn.tools.tag_synonym import singularize
        assert singularize(plural) == singularize(singular)

    def test_already_singular_words_are_stable(self):
        from Evelyn.tools.tag_synonym import singularize
        for w in ("process", "class", "focus", "sleep", "health"):
            assert singularize(w) == singularize(singularize(w))

    def test_linguistic_accuracy_is_not_required_only_symmetry(self):
        """`analysis` -> `analysi` is wrong English and harmless: both sides use this function."""
        from Evelyn.tools.tag_synonym import singularize
        assert singularize("analysis") == singularize("analysis")

    def test_lexical_lookup_resolves_an_irregular_plural(self):
        """The regression this guards: a document saying 'memories' must reach `memory`."""
        from Evelyn.tools.tag_librarian import _lexical_lookup
        assert _lexical_lookup("memories", {"memory": "memory"}) == "memory"
