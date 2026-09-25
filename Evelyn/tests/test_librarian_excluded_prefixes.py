# test_librarian_excluded_prefixes.py
# date created: 2026-09-25
# date modified: 2026-09-25 17:33:15
# tags: #taxonomy, #librarian, #vault, #testing

"""Whole subtrees the librarian does not audit, and why the list is in config.

The admission queue's 200-slot cap is **global**, not per-producer. When it fills,
`propose_tag_admission()` returns `[]` for everything — memory extraction, fact merges and
personal vault notes go silent together, and withholding then leaves unregistered terms *on*
facts because nothing pending covers them.

On 2026-09-25 it filled to exactly 200/200 in a single day, every slot from `Reference Library/`:
third-party book and manual text nominating `speculative-decoding`, `evol-instruct`,
`reverse-neutralization` — someone else's subject matter. Producers were refused for two hours
before anyone noticed, because a full queue logs a warning and otherwise looks like quiet.

Excluding the subtree cost almost nothing: all 2,838 reference notes already carried tags from
ingestion, and the semantic pass had only ever reached 243 of them.

The prefixes had been spelled out in the SQL, twice each for case. Config is the single place
that answers "what does the librarian not touch", and `Reference Library/` is on that list by
decision, so the decision needs somewhere to be read.
"""

import evelyn_config as cfg
from Evelyn.tools import vault_db


def test_reference_library_is_excluded() -> None:
    """The decision itself, pinned. Removing it re-opens the queue-jam path."""
    prefixes = [p.lower() for p in cfg.TAG_LIBRARIAN_EXCLUDED_PREFIXES]
    assert "reference library/" in prefixes


def test_the_previously_hardcoded_subtrees_survived_the_move() -> None:
    """Templates, Attachments, Bases and dotfiles were excluded before; they still are."""
    prefixes = [p.lower() for p in cfg.TAG_LIBRARIAN_EXCLUDED_PREFIXES]
    for expected in ("templates/", "attachments/", "bases/", "."):
        assert expected in prefixes, f"{expected} lost its exclusion"


def test_the_audit_queue_holds_no_excluded_subtree() -> None:
    """The end-to-end check: config is only a list until the query reads it."""
    rows = vault_db.fetch_next_documents_for_semantic_tag_audit(batch_size=200)
    offenders = [
        r["path"]
        for r in rows
        if any(
            str(r["path"]).lower().startswith(p.lower())
            for p in cfg.TAG_LIBRARIAN_EXCLUDED_PREFIXES
        )
    ]
    assert not offenders, f"excluded subtrees still queued: {offenders[:5]}"


def test_exclusion_is_case_insensitive() -> None:
    """`templates/` and `Templates/` were separate SQL clauses; one list must cover both."""
    import inspect

    src = inspect.getsource(vault_db.fetch_next_documents_for_semantic_tag_audit)
    assert "LOWER(path) NOT LIKE ?" in src
    assert "path NOT LIKE 'templates/%'" not in src, "the duplicated case variants came back"
