# test_templates_are_registered.py
# date created: 2026-09-25
# date modified: 2026-09-25 20:49:55
# tags: #taxonomy, #templates, #vocabulary, #testing

"""A template's tags seed every note made from it, and nothing else ever checks them (F2).

`Templates/` is excluded from both indexing and the semantic tag audit, and correctly so — a
template is not a document about anything, and auditing one would propose subjects for
placeholder prose. The cost is that **no producer and no pass will ever notice a template
carrying tags the registry does not hold**, so the drift found on 2026-09-25 (6 of 9) had gone
unseen since the `.186` reset, which also skipped excluded directories.

Every unregistered tag there was the same mistake: a statement about document *class* written
as a flat word or a pre-coordinate compound, when §3.4 puts class on the `type/` axis —
`moc`, `index`, `reference-card`, `External_Resource`, `system/disambiguation`,
`dnd/session-recap`. Two were worse: `{{slug}}` and `module-X`, literal placeholders that
were never meant to survive rendering and would have been written onto real notes verbatim.

This test is the only guard. It skips where the vault is absent, so a clone does not fail on
somebody else's directory.
"""

import os

import pytest

import evelyn_config as cfg
from Evelyn.tools import taxonomy_db
from Evelyn.tools.tag_librarian import is_wellformed_term, parse_frontmatter_tags

TEMPLATE_DIR = os.path.join(getattr(cfg, "VAULT_BASE_DIR", ""), "Templates")

pytestmark = pytest.mark.skipif(
    not os.path.isdir(TEMPLATE_DIR), reason="no vault Templates/ directory on this machine"
)


def _templates() -> list[str]:
    return sorted(f for f in os.listdir(TEMPLATE_DIR) if f.endswith(".md"))


def _tags(name: str) -> list[str]:
    with open(os.path.join(TEMPLATE_DIR, name), encoding="utf-8") as fh:
        return parse_frontmatter_tags(fh.read())[0]


@pytest.fixture(scope="module")
def known() -> set[str]:
    """The live registry plus its aliases — a retired surface form still resolves (§6.2)."""
    aliases = taxonomy_db.get_aliases()
    return {t["tag"] for t in taxonomy_db.get_master_tags()} | set(
        dict(aliases.items() if isinstance(aliases, dict) else aliases)
    )


def test_every_template_tag_is_in_the_vocabulary(known: set[str]) -> None:
    offenders = {n: [t for t in _tags(n) if t not in known] for n in _templates()}
    offenders = {n: bad for n, bad in offenders.items() if bad}

    assert not offenders, f"templates carry unregistered tags: {offenders}"


def test_no_template_carries_an_unrendered_placeholder() -> None:
    """`{{slug}}` was a real tag on a real template, and would land on every list note."""
    offenders = {
        n: [t for t in _tags(n) if "{{" in t or "}}" in t or t.endswith("-X")]
        for n in _templates()
    }
    offenders = {n: bad for n, bad in offenders.items() if bad}

    assert not offenders, f"templates carry placeholder tags: {offenders}"


def test_no_template_carries_a_pre_coordinate_compound() -> None:
    """A slash belongs to a facet axis; anything else is the retired hierarchy (§3.3)."""
    offenders = {n: [t for t in _tags(n) if not is_wellformed_term(t)] for n in _templates()}
    offenders = {n: bad for n, bad in offenders.items() if bad}

    assert not offenders, f"templates carry hierarchical tags: {offenders}"


def test_the_audit_still_excludes_templates() -> None:
    """The exclusion is correct and should stay — this test exists *because* of it.

    If `Templates/` ever enters the audit, the pass will propose subjects for placeholder
    prose, and this guard stops being the only thing watching.
    """
    prefixes = [p.lower() for p in getattr(cfg, "TAG_LIBRARIAN_EXCLUDED_PREFIXES", [])]
    assert any(p.startswith("templates/") for p in prefixes)
