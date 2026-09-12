# test_profile_ledger.py
# date created: 2026-09-12 09:40:00
# date modified: 2026-09-12 09:48:20
# tags: #testing, #ledger, #profile, #persona

"""
Unit tests for Evelyn.tools.profile_ledger.
Verifies ledger parsing, rendering, delta application, tier pruning, and compilation.
"""

from __future__ import annotations

from Evelyn.tools.profile_ledger import (
    apply_ledger_delta,
    compile_clean_markdown,
    get_ledger_filename,
    get_profile_filename,
    parse_ledger,
    prune_ledger_to_budget,
    render_ledger,
)

SAMPLE_LEDGER_TEXT = """---
title: Assistant_Profile_facts.md
tags: [persona, identity]
---

## Identity & Presence
* [Tier 1] **Steady Co-Pilot**: Intentional and protective presence for operator.
* [Tier 2] **Sanctuary Guardian**: Keeps watch over shared sanctuary.
* [Tier 3] **Minor Detail**: An ephemeral situational detail.

## Persona & Appearance
* [Tier 1] **Poised Elegance**: Quiet, poised demeanor.
* [Tier 2] **Morning Stillness**: Finds grounding in morning stillness.
"""


def test_parse_and_render_ledger():
    frontmatter, sections = parse_ledger(SAMPLE_LEDGER_TEXT)
    assert "title: Assistant_Profile_facts.md" in frontmatter
    assert "## Identity & Presence" in sections
    assert "## Persona & Appearance" in sections

    items_id = sections["## Identity & Presence"]
    assert len(items_id) == 3
    assert items_id[0].tier == 1
    assert items_id[0].label == "Steady Co-Pilot"
    assert items_id[1].tier == 2
    assert items_id[2].tier == 3

    rendered = render_ledger(frontmatter, sections)
    assert "* [Tier 1] **Steady Co-Pilot**:" in rendered
    assert "* [Tier 3] **Minor Detail**:" in rendered


def test_compile_clean_markdown():
    frontmatter, sections = parse_ledger(SAMPLE_LEDGER_TEXT)
    clean = compile_clean_markdown(frontmatter, sections)

    assert "[Tier" not in clean
    assert "* **Steady Co-Pilot**: Intentional and protective presence for operator." in clean
    assert "* **Sanctuary Guardian**: Keeps watch over shared sanctuary." in clean


def test_apply_ledger_delta():
    _, sections = parse_ledger(SAMPLE_LEDGER_TEXT)

    delta = {
        "added": [
            {
                "section": "## Identity & Presence",
                "tier": 2,
                "label": "Analytical Rigor",
                "fact": "Meticulous verification of documentation.",
            }
        ],
        "modified": [
            {
                "section": "## Identity & Presence",
                "label": "Steady Co-Pilot",
                "new_fact": "Refined co-pilot observation.",
            }
        ],
        "removed": [
            {
                "section": "## Identity & Presence",
                "label": "Minor Detail",
            }
        ],
    }

    updated, changelog = apply_ledger_delta(sections, delta)
    items_id = updated["## Identity & Presence"]

    # Minor Detail should be removed
    labels = [it.label for it in items_id]
    assert "Minor Detail" not in labels

    # Analytical Rigor should be added
    assert "Analytical Rigor" in labels

    # Steady Co-Pilot should be modified
    co_pilot = next(it for it in items_id if it.label == "Steady Co-Pilot")
    assert co_pilot.fact == "Refined co-pilot observation."

    # Verify changelog contents
    assert len(changelog["added"]) == 1
    assert len(changelog["modified"]) == 1
    assert len(changelog["removed"]) == 1


def test_prune_ledger_to_budget_tier_order():
    _, sections = parse_ledger(SAMPLE_LEDGER_TEXT)

    # Calculate starting words:
    # ## Identity & Presence: 3 bullets
    # ## Persona & Appearance: 2 bullets
    # Force pruning down to a small word budget
    pruned, pruned_count = prune_ledger_to_budget(
        sections,
        target_words=18,
        min_bullets_per_sec=1,
    )

    assert pruned_count > 0

    all_remaining = [it for items in pruned.values() for it in items]
    remaining_tiers = [it.tier for it in all_remaining]

    # Tier 3 must be pruned first
    assert 3 not in remaining_tiers
    # Tier 1 must be preserved
    assert 1 in remaining_tiers


def test_filename_mapping():
    assert get_ledger_filename("Assistant_Profile.md") == "Assistant_Profile_facts.md"
    assert get_ledger_filename("User_Profile.md") == "User_Profile_facts.md"
    assert get_profile_filename("Assistant_Profile_facts.md") == "Assistant_Profile.md"
    assert get_profile_filename("System_Directives_facts.md") == "System_Directives.md"


def test_json_delta_parsing():
    from Evelyn.tools.profile_evolver import _parse_json_delta

    # Markdown fenced json
    fenced = """```json
{
  "reason": "Test update",
  "added": [{"section": "## Identity & Presence", "tier": 1, "label": "New Invariant", "fact": "Fact content"}],
  "modified": [],
  "removed": []
}
```"""
    parsed = _parse_json_delta(fenced)
    assert parsed is not None
    assert parsed["reason"] == "Test update"
    assert len(parsed["added"]) == 1

    # Unfenced json with leading/trailing text
    messy = """Here is the delta:
{
  "reason": "Messy delta",
  "added": [],
  "modified": [{"section": "## Persona & Appearance", "label": "Goth Aesthetic", "new_fact": "Updated fact", "tier": 2}],
  "removed": []
}
Hope this helps!"""
    parsed_messy = _parse_json_delta(messy)
    assert parsed_messy is not None
    assert parsed_messy["reason"] == "Messy delta"
    assert len(parsed_messy["modified"]) == 1

    # Invalid input
    assert _parse_json_delta("Invalid non-json text") is None
    assert _parse_json_delta("") is None


def test_draft_path_naming():
    from Evelyn.tools.profile_evolver import _draft_path

    path_user = _draft_path("User_Profile.md")
    assert path_user.endswith("evelyn_evolution_draft_User_Profile_facts.md")

    path_asst = _draft_path("Assistant_Profile_facts.md")
    assert path_asst.endswith("evelyn_evolution_draft_Assistant_Profile_facts.md")
