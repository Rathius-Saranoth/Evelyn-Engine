# test_taxonomy_census_runs.py
# date created: 2026-09-25
# date modified: 2026-09-25 20:36:40
# tags: #taxonomy, #census, #scheduling, #testing

"""The vocabulary's usage census has to actually run (G6).

`maintain_master_taxonomy()` re-counts every registered term against vault, memory and
procedures. `.221` fixed it to span all three and corrected 278 stale counts. What nobody
checked is whether anything *calls* it:

* `scripts/master_librarian.py` runs it behind `--rebalance-taxonomy`, and the only scheduled
  route to that script calls `run_master_librarian_task()` with no arguments — so the flag
  defaults to `False` and **the census is skipped even with the master librarian enabled**.
* The other route is a manual endpoint nothing calls.

Measured 2026-09-25: **585 of 702 registry counts disagreed with the live corpus.** Two things
read those counts and were therefore wrong — the vocabulary view (F5), and
`propose_tag_retirement`, which runs *inside* the census, meaning the retirement path had
never executed and no term has ever been proposed for retirement.

Same shape as G1, G2, G4, G5 and F7a: written, correct, reachable by nobody.
"""

from pathlib import Path

import pytest

import evelyn_config as cfg
from Evelyn.tools import tag_librarian


@pytest.fixture(autouse=True)
def _reset_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    """The throttle is module state; each test starts from 'never run'."""
    monkeypatch.setattr(tag_librarian, "_last_census_ts", 0.0)


@pytest.fixture
def _fresh_throttle(monkeypatch: pytest.MonkeyPatch) -> list:
    """Counts census runs without doing one. Deliberately *not* autouse — the test that
    checks the census's own behaviour has to reach the real function."""
    calls: list[int] = []
    monkeypatch.setattr(
        tag_librarian, "maintain_master_taxonomy",
        lambda: (calls.append(1), {"updated_master_tags": 3, "unused_terms": 1,
                                   "retirement_proposed": 0})[1],
    )
    return calls


def test_the_census_runs_when_it_has_never_run(_fresh_throttle) -> None:
    assert tag_librarian.run_taxonomy_census_if_due() is not None
    assert _fresh_throttle == [1]


def test_a_second_call_inside_the_interval_does_nothing(_fresh_throttle) -> None:
    """It is a full scan of both substrates; the tag pass runs far more often than daily."""
    tag_librarian.run_taxonomy_census_if_due()
    assert tag_librarian.run_taxonomy_census_if_due() is None
    assert _fresh_throttle == [1]


def test_force_overrides_the_throttle(_fresh_throttle) -> None:
    tag_librarian.run_taxonomy_census_if_due()
    assert tag_librarian.run_taxonomy_census_if_due(force=True) is not None
    assert _fresh_throttle == [1, 1]


def test_a_zero_interval_disables_it(monkeypatch: pytest.MonkeyPatch, _fresh_throttle) -> None:
    monkeypatch.setattr(cfg, "TAXONOMY_CENSUS_INTERVAL_HOURS", 0)

    assert tag_librarian.run_taxonomy_census_if_due() is None
    assert _fresh_throttle == []


def test_the_interval_is_read_from_config_not_hardcoded(
    monkeypatch: pytest.MonkeyPatch, _fresh_throttle
) -> None:
    monkeypatch.setattr(cfg, "TAXONOMY_CENSUS_INTERVAL_HOURS", 9999)
    tag_librarian.run_taxonomy_census_if_due()

    assert tag_librarian.run_taxonomy_census_if_due() is None, "a longer interval must hold"


def test_the_scheduled_tag_pass_calls_it() -> None:
    """The whole point of the item. A census nothing reaches is the defect, not the fix."""
    server = Path(__file__).resolve().parents[2] / "evelyn_server.py"
    src = server.read_text(encoding="utf-8")
    task = src[src.index("async def run_tag_librarian_task"):]
    task = task[:task.index("\nasync def ", 1)]

    assert "run_taxonomy_census_if_due" in task


def test_enabling_the_master_librarian_would_not_have_fixed_this() -> None:
    """Pins the root cause, so nobody 'fixes' G6 by turning D3 on.

    The scheduled dispatch calls `run_master_librarian_task()` bare, and `rebalance_taxonomy`
    defaults to False — the census is skipped on that path whatever the enable flag says.
    """
    server = Path(__file__).resolve().parents[2] / "evelyn_server.py"
    src = server.read_text(encoding="utf-8")

    assert "rebalance_taxonomy: bool = False" in src
    assert 'run_master_librarian_task()' in src, "the bare scheduled call"


def test_the_census_itself_is_still_the_one_that_proposes_retirement() -> None:
    """If this moves, the retirement path silently stops running again.

    Read from the file rather than the attribute: the fixture above replaces the function, so
    `inspect.getsource` would return the stub and the assertion would be about the test.
    """
    module = Path(tag_librarian.__file__)
    src = module.read_text(encoding="utf-8")
    body = src[src.index("def maintain_master_taxonomy("):]
    # It is the last top-level function in the module, so there is no following `def` to
    # bound on — stop at the CLI block instead, and fall back to end-of-file.
    for end in ("\nif __name__", "\ndef "):
        if end in body[1:]:
            body = body[: body.index(end, 1)]
            break

    assert "propose_tag_retirement" in body


def test_a_term_that_fell_out_of_use_has_its_count_zeroed() -> None:
    """Not deleting an unused term is the rule; leaving its count wrong is not part of it.

    Four terms read `usage_count = 1` against a true zero on 2026-09-25 — exactly the set a
    reviewer would consult when deciding what to retire, and exactly the set F5 would render.
    """
    from Evelyn.tools import taxonomy_db, vault_db

    # The census refuses to run against an empty corpus — a circuit breaker, so a broken or
    # half-built index cannot zero the whole vocabulary. Give it one real document.
    vault_db.upsert_document("Notes/Live.md", title="Live", mtime=0.0, tags="live-term")
    taxonomy_db.upsert_master_tag("live-term", category="test", usage_count=0)
    taxonomy_db.upsert_master_tag("ghost-term", category="test", usage_count=7)

    result = tag_librarian.maintain_master_taxonomy()
    assert result["status"] == "success", result

    by_tag = {t["tag"]: t for t in taxonomy_db.get_master_tags()}
    assert by_tag["ghost-term"]["usage_count"] == 0, "the row stays, the number tells the truth"
    assert by_tag["ghost-term"]["tag"] == "ghost-term", "and the row really does stay"
    assert by_tag["live-term"]["usage_count"] == 1, "a used term is still counted upward"
