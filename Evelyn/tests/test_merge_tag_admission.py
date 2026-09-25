# test_merge_tag_admission.py
# date created: 2026-09-25
# date modified: 2026-09-25 07:08:56
# tags: #memory, #merge, #taxonomy, #admission, #testing

"""A fact merge may not mint vocabulary.

The extraction writers have routed their tags through admission since .227, and `.225` stopped
`apply_fact_merge` unioning every source's tags on top of the model's answer. Neither touched
the merge path's *admission*: `fact_deduplicator` imported `normalize_tag_format` only, which
cleans format, and `canonicalize_tags`, which resolves aliases — both documented as not gates.
So whatever the model picked went onto the master fact verbatim.

It showed the night after B3 hand-curated all 10,335 live facts to zero unregistered terms:
one run of 50 merges put 102 back — `videogame` beside the registered `video-games`, `dnd` and
`tabletop-rpg` beside `ttrpg`, the container word `work`, and one-offs like `muffin` — and not
one of them reached the review queue.

The fallback matters as much as the gate. If every chosen term is withheld, leaving `merged_tags`
empty sends `apply_fact_merge` to its union branch, which would both restore the bloat and put
back the terms just withheld.
"""

import asyncio

import pytest

import evelyn_config as cfg
from Evelyn.tools import fact_deduplicator, memory_db, tag_librarian, taxonomy_db


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> None:
    for term in ("coffee", "routine", "video-games"):
        taxonomy_db.upsert_master_tag(term, category="domestic-life")
    taxonomy_db.invalidate_alias_cache()
    monkeypatch.setattr(tag_librarian, "index_master_tag_in_chroma", lambda *a, **k: None)
    monkeypatch.setattr(cfg, "TAG_WITHHOLD_UNREGISTERED", True)


def _sources(tags_a: str = "coffee", tags_b: str = "routine") -> list[dict]:
    a = memory_db.insert_entry(
        category="Cat05-U", subject="Tester", observation="A drinks espresso.", tags=tags_a,
    )
    b = memory_db.insert_entry(
        category="Cat05-U", subject="Tester", observation="A drinks pour-over.", tags=tags_b,
    )
    return [memory_db.get_entry(a), memory_db.get_entry(b)]


def _gate(
    monkeypatch: pytest.MonkeyPatch, records: list[dict], merged_tags: str
) -> str | None:
    """Drive the real `generate_consolidation_proposal` and return the master's stored tags.

    Reimplementing the gate here would pin the intent and not the wiring, which is the failure
    this whole file exists to catch: the logic was never wrong, it was never called.
    """
    monkeypatch.setattr(fact_deduplicator, "load_cat00_index", lambda *a, **k: "")

    async def _fake_ollama(*_a: object, **_k: object) -> str:
        return (
            "verdict: merge\n"
            "merged_summary: A drinks coffee every morning.\n"
            f'merged_tags: "{merged_tags}"\n'
            "confidence: high\n"          # high => auto_applied, so the write really happens
            "target_category: Cat05-U\n"
            "reasoning: Same habit stated twice.\n"
        )

    cluster = {"records": records, "category": "Cat05-U", "topic": "Coffee"}
    assert asyncio.run(
        fact_deduplicator.generate_consolidation_proposal(cluster, _fake_ollama)
    ), "the merge must have been recorded"

    master_id = int(memory_db.select_merge_master(records)["id"])
    return str(memory_db.get_entry(master_id)["tags"] or "").strip() or None


def _pending() -> set[str]:
    return {
        (p.get("topic") or "").strip()
        for p in memory_db.get_pending_proposals(tag_librarian.TAG_ADMISSION_PROPOSAL)
    }


def test_master_selection_is_shared_with_apply_fact_merge(registry: None) -> None:
    """The proposal must point at the row the tag actually lands on."""
    records = _sources()
    expected = int(memory_db.select_merge_master(records)["id"])
    master_id = memory_db.apply_fact_merge(
        source_entries=records, merged_text="A drinks coffee.",
        target_category="Cat05-U", merged_tags="coffee",
    )
    assert master_id == expected


def test_unregistered_merge_tag_is_withheld_and_proposed(
    registry: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = _sources()
    kept = _gate(monkeypatch, records, "coffee, videogame")

    assert kept == "coffee", "an unregistered term must not reach the merged fact"
    assert "videogame" in _pending(), "and it must be recoverable from review"


def test_container_word_is_dropped_not_proposed(
    registry: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = _sources()
    kept = _gate(monkeypatch, records, "coffee, work")

    assert kept == "coffee"
    assert "work" not in _pending(), "a container word carries no information to review"


def test_all_withheld_falls_back_to_master_tags_not_the_union(
    registry: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The union branch would restore the bloat *and* the withheld terms."""
    records = _sources(tags_a="coffee", tags_b="routine")
    kept = _gate(monkeypatch, records, "videogame, dnd")

    master_tags = str(memory_db.select_merge_master(records).get("tags") or "")
    assert kept == master_tags == "coffee"
    assert "routine" not in (kept or ""), "the secondary's tags are not unioned back in"
    assert {"videogame", "dnd"} <= _pending()


def test_registered_tags_pass_through_untouched(
    registry: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = _sources()
    before = _pending()

    assert _gate(monkeypatch, records, "coffee, video-games") == "coffee, video-games"
    assert _pending() == before, "a fully registered merge raises nothing"
