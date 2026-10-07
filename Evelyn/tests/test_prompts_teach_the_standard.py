# test_prompts_teach_the_standard.py
# date created: 2026-09-23 08:00:00
# date modified: 2026-10-06 21:47:29
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

**A denylist only catches the wording you already thought of.** This file listed exact strings,
and in v000.006.224 a fourth offender was found that none of them matched: the extraction
prompt's novelty directive said "parent domain *hierarchies*" (the list held the singular) and
gave its examples as `#Domain/Subtopic` and `'Tech/...'` (the list held `Tech/Python/FastAPI`).
It had been telling the model to mint `#Domain/Category/Subtopic` trees the whole time, one
paragraph above the rule forbidding them.

So there is now a second, structural check that does not depend on knowing the phrasing: in a
string that is talking about tags, a slashed path is the retired format unless it is one of the
four permitted facet prefixes. Written as a shape rather than a vocabulary, it catches wordings
nobody has invented yet.
"""

import ast
import pathlib
import re

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
    "skill/x",
    "procedure/y",
    "skill/z",
    "procedure/w",
    "skill/",
    "procedure/",
    "protocol/",
    "workflow/",
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


# The four facet prefixes are the sole permitted slash in a tag (§5). Two more are excluded
# from the librarian entirely and appear in prompts as things *not* to touch.
PERMITTED_PREFIXES = ("type/", "motif/", "setting/", "event/", "status/", "obsidian-graph/")

# A retired tag path has one of two shapes, and ordinary prose has neither:
#   - a capitalised or digit-led segment followed by a slash  (`Tech/`, `#Domain/`, `3D-Printing/`)
#   - a lowercase path three or more segments deep            (`home/coffee/espresso`)
# A two-segment lowercase pair is left alone because that is how English writes an
# alternative — `chunks/facts`, `friend/family`, `and/or` — and flagging those would train
# people to silence the check. The lookbehind excludes a leading dot so a repository path
# such as `.agents/rules/…` is not mistaken for a tag.
TITLED_PATH = re.compile(r"(?<![\w/.])[A-Z0-9][A-Za-z0-9_-]*/")
DEEP_PATH = re.compile(r"(?<![\w/.])[a-z][a-z0-9_-]*(?:/[a-z0-9][a-z0-9_-]*){2,}")
FILE_SUFFIXES = (".md", ".py", ".json", ".html", ".txt", ".yml", ".yaml", ".http")


def _tag_paths(line: str) -> list[str]:
    """Return the retired-format tag paths on a line, ignoring file paths and facets."""
    found = []
    for pattern in (TITLED_PATH, DEEP_PATH):
        for match in pattern.finditer(line):
            fragment = match.group(0)
            if fragment.lower().startswith(PERMITTED_PREFIXES):
                continue
            tail = line[match.start():].split()[0] if line[match.start():].split() else ""
            if tail.rstrip("\"'`,);").endswith(FILE_SUFFIXES):
                continue
            found.append(fragment)
    return found


@pytest.mark.parametrize("relpath", PROMPT_MODULES)
def test_no_tag_instruction_contains_a_slashed_path(relpath):
    """Structural companion to the denylist above: check the shape, not the wording.

    Scoped to string literals that mention tags, because that is where a slashed example does
    damage — it is read as the format to produce. Elsewhere in these modules a slash is just a
    slash.
    """
    path = REPO / relpath
    tree = ast.parse(path.read_text(encoding="utf-8"))

    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if "tag" not in node.value.lower() or len(node.value) < 30:
            continue
        for line in node.value.splitlines():
            if any(n in line.lower() for n in NEGATIONS):
                continue  # citing a retired form in order to forbid it is good prompt writing
            offenders += [f"{p!r} in the string at line {node.lineno}" for p in _tag_paths(line)]

    assert not offenders, (
        f"{relpath} shows a slashed tag path in an instruction about tags: {offenders}. "
        "Tags are flat subject terms; the only permitted slash is a facet prefix "
        f"({', '.join(PERMITTED_PREFIXES[:4])}). See .agents/rules/vault-tag-taxonomy.md §5."
    )
