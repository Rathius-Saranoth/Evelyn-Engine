# test_decompose_respects_the_registry.py
# date created: 2026-09-25
# date modified: 2026-09-25 20:15:28
# tags: #taxonomy, #decomposition, #facets, #testing

"""Decomposition may not invent a term the vocabulary does not hold.

`decompose_to_atoms` flattens a hierarchical tag into the atoms a post-coordinate vocabulary
holds, and for a facet it kept prefix + leaf — dropping the middle, because
`setting/biome/tropical` is categorising *within* an axis.

That is right everywhere except where the standard requires a second level. §3.4: *"Sub-typing
is required on `type/media` and unavailable elsewhere."* So `type/media/text` became
`type/text`, which **destroys the required level and invents an unregistered term in one
step** — the output could not be applied to anything, and would have raised a bogus admission
proposal for a term the vocabulary had deliberately not registered.

Decided from the registry rather than a hardcoded `type/media` exception: a registered term is
canonical by definition, so decomposing one is a failure of this function's own purpose. The
rule then holds for whatever the standard requires next, without anyone remembering to add it
here.

Dormant when found — the only callers were the `.181`/`.182` migrations, long applied, and the
live corpus was intact (28 documents on `type/media/text`, zero on `type/text`). The module
advertises this as a general utility, which is how it would have bitten.
"""

import pytest

from Evelyn.tools import tag_synonym

REGISTRY = {"type/media/text", "type/reference", "work", "routine", "morning"}


def test_a_registered_multi_level_facet_survives_intact() -> None:
    assert tag_synonym.decompose_to_atoms("type/media/text", known=REGISTRY) == ["type/media/text"]


def test_an_unregistered_deep_facet_still_loses_its_middle() -> None:
    """The original rule has to keep working — this is not a blanket exemption for slashes."""
    assert tag_synonym.decompose_to_atoms("setting/biome/tropical", known=REGISTRY) == [
        "setting/tropical"
    ]


def test_an_unregistered_hierarchy_still_decomposes() -> None:
    assert tag_synonym.decompose_to_atoms("work/routine/morning", known=REGISTRY) == [
        "work", "routine", "morning"
    ]


def test_a_registered_flat_term_is_untouched() -> None:
    assert tag_synonym.decompose_to_atoms("type/reference", known=REGISTRY) == ["type/reference"]


def test_the_csv_path_carries_the_same_rule() -> None:
    """The migrations go through here, so the guard has to survive the wrapper."""
    out, changed = tag_synonym.decompose_tag_csv(
        "type/media/text, work/routine", known=REGISTRY
    )
    assert out == "type/media/text, work, routine"
    assert changed


def test_the_registry_is_read_once_per_csv_not_once_per_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """These run over whole corpora; a read per tag is a read per corpus-tag."""
    calls = []
    monkeypatch.setattr(tag_synonym, "_registered_terms", lambda: (calls.append(1), REGISTRY)[1])

    tag_synonym.decompose_tag_csv("type/media/text, work/routine, a/b/c")

    assert len(calls) == 1


def test_an_unreadable_registry_falls_back_rather_than_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It must not start preserving everything, or decomposition quietly stops happening."""
    monkeypatch.setattr(tag_synonym, "_registered_terms", set)

    assert tag_synonym.decompose_to_atoms("work/routine/morning") == ["work", "routine", "morning"]
