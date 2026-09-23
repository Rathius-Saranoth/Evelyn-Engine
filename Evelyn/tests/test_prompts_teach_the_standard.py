# test_prompts_teach_the_standard.py
# date created: 2026-09-23 08:00:00
# date modified: 2026-09-23 17:15:17
# tags: #test, #prompts, #taxonomy, #regression

"""Every prompt that asks a model for tags must teach the §5 format (v000.006.210).

v000.006.204 rewrote the extraction prompts, but two of them were only half-changed: the
few-shot examples became flat while the numbered rule above them still said "MULTI-TIER
DOMAIN TAXONOMY ... `Tech/Python/FastAPI`". The rule won. Overnight the split and merge
pipelines reintroduced 101 unregistered terms across 70 entries, undoing most of the memory
reconciliation.

A prose sweep found the first instance and missed the other two, so this is a deterministic
check instead: no module that builds a tag-producing prompt may contain the retired
vocabulary, in a rule or an example.
"""

import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]

# Modules that build a prompt asking a model to produce tags.
PROMPT_MODULES = [
    "Evelyn/tools/fact_extractor.py",
    "Evelyn/tools/fact_splitter.py",
    "Evelyn/tools/fact_deduplicator.py",
    "Evelyn/tools/context_manager.py",
    "Evelyn/tools/procedure_consolidator.py",
    "evelyn_server.py",
]

# Phrases that teach the pre-coordinate hierarchy §3.3/§5 abolished.
FORBIDDEN_PHRASES = [
    "MULTI-TIER DOMAIN",
    "domain hierarchy",
    "hierarchical domain",
    "domain tree",
    "slashes joining levels",
    "TitleCase with underscores",
]

# Concrete retired examples. `type/`, `motif/`, `setting/` and `event/` are the permitted
# facet prefixes, so only the abolished domain paths are listed.
FORBIDDEN_EXAMPLES = [
    "Tech/Python/FastAPI",
    "Home/Coffee/Espresso",
    "Lore/Dungeon_Crawler_Carl",
    "Health/Sleep/Routine",
    "tech/python/fastapi",
    "home/coffee/espresso",
    "health/sleep/routine",
    "John_Smith",
]


# Citing a retired form in order to forbid it is good prompt writing, so an example is only
# an offence on a line that is not negating it.
NEGATIONS = ("never", "not ", "n't", "avoid", "instead of", "rather than", "no longer")


@pytest.mark.parametrize("relpath", PROMPT_MODULES)
def test_no_prompt_teaches_the_retired_hierarchy(relpath):
    path = REPO / relpath
    assert path.exists(), f"{relpath} moved; update PROMPT_MODULES"
    text = path.read_text(encoding="utf-8")

    # A rule heading is never legitimate, negated or not.
    offenders = [f"phrase {p!r}" for p in FORBIDDEN_PHRASES if p.lower() in text.lower()]

    for lineno, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        if any(n in low for n in NEGATIONS):
            continue
        offenders += [f"{e!r} at line {lineno}" for e in FORBIDDEN_EXAMPLES if e in line]

    assert not offenders, (
        f"{relpath} still teaches the retired tag format: {offenders}. "
        "Tags are flat subject terms (.agents/rules/vault-tag-taxonomy.md §5); "
        "fix the rule text, not only the few-shot example."
    )
