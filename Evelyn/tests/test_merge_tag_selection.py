# test_merge_tag_selection.py
# date created: 2026-09-24
# date modified: 2026-09-24 19:05:36
# tags: #memory, #merge, #taxonomy, #testing

"""Merging must let the model leave a tag out.

`apply_fact_merge` unioned every source entry's tags and then added the model's `merged_tags`
on top, so the answer to "which tags does the consolidated fact carry?" could only ever grow.
A term the model deliberately omitted was put straight back by the union, which made the
prompt's rule 7 — prefer existing terms, never name the container — unenforceable at the point
it mattered.

The corpus shows the result: eleven merges auto-applied in one morning produced facts carrying
eight and nine tags, most seen exactly once. Merging is where a vocabulary is supposed to get
smaller.
"""

import pytest

from Evelyn.tools import memory_db


@pytest.fixture
def sources() -> list[dict]:
    a = memory_db.insert_entry(
        category="Cat05-U", subject="Tester", observation="A likes espresso.",
        tags="coffee, espresso, home",
    )
    b = memory_db.insert_entry(
        category="Cat05-U", subject="Tester", observation="A likes pour-over.",
        tags="coffee, pour-over, kitchen",
    )
    return [memory_db.get_entry(a), memory_db.get_entry(b)]


def _tags_of(entry_id: int) -> list[str]:
    return [t.strip() for t in (memory_db.get_entry(entry_id)["tags"] or "").split(",") if t.strip()]


def test_the_chosen_tags_are_the_result(sources: list[dict]) -> None:
    """Not the chosen tags plus everything the sources happened to carry."""
    master = memory_db.apply_fact_merge(
        sources, "A likes espresso and pour-over.", "Cat05-U", merged_tags="coffee, brewing",
    )

    assert _tags_of(master) == ["brewing", "coffee"]


def test_a_container_word_left_out_stays_out(sources: list[dict]) -> None:
    """The exact failure: `home` and `kitchen` were dropped on purpose and the union restored them."""
    master = memory_db.apply_fact_merge(
        sources, "A likes espresso and pour-over.", "Cat05-U", merged_tags="coffee, espresso, pour-over",
    )

    assert "home" not in _tags_of(master)
    assert "kitchen" not in _tags_of(master)


def test_merging_does_not_grow_the_tag_list(sources: list[dict]) -> None:
    """Consolidation should narrow a vocabulary, not accumulate one."""
    before = max(len(_tags_of(s["id"])) for s in sources)

    master = memory_db.apply_fact_merge(
        sources, "A likes espresso and pour-over.", "Cat05-U", merged_tags="coffee, brewing",
    )

    assert len(_tags_of(master)) <= before


def test_without_a_choice_the_union_still_stands(sources: list[dict]) -> None:
    """A caller with no opinion loses nothing; the fallback is unchanged."""
    master = memory_db.apply_fact_merge(sources, "A likes coffee.", "Cat05-U", merged_tags=None)

    assert _tags_of(master) == ["coffee", "espresso", "home", "kitchen", "pour-over"]


def test_duplicates_in_the_choice_collapse(sources: list[dict]) -> None:
    master = memory_db.apply_fact_merge(
        sources, "A likes coffee.", "Cat05-U", merged_tags="coffee, coffee , brewing",
    )

    assert _tags_of(master) == ["brewing", "coffee"]
