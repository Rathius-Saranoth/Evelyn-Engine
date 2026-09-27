# test_excerpt_link_truncation.py
# date created: 2026-09-26 19:52:23
# date modified: 2026-09-26 19:53:18
# tags:

"""Tests that a context excerpt never ends mid-wikilink.

`extract_link_context` takes a fixed-width slice around a match, so the cut lands wherever
the character budget runs out — regularly inside a `[[…]]`. The excerpt is then written
verbatim into a stub note, so the fragment becomes part of the vault.

The trailing case is the damaging one: an unclosed `[[` runs on into whatever follows it in
the rendered note, which is the excerpt's closing quote and then the next reference's own
link. Measured 2026-09-26: 307 such fragments across 134 stub notes.
"""

import pytest

from Evelyn.tools.string_utils import extract_link_context, trim_partial_wikilinks

# --------------------------------------------------------------------------------------
# trim_partial_wikilinks
# --------------------------------------------------------------------------------------


def test_unclosed_trailing_link_is_dropped():
    assert trim_partial_wikilinks("played [[Valheim]] then [[Beat Saber") == "played [[Valheim]] then "


def test_orphan_leading_close_is_dropped():
    assert trim_partial_wikilinks("Riverton]] went to [[Utah]]") == " went to [[Utah]]"


def test_both_ends_are_trimmed_together():
    assert trim_partial_wikilinks("lstrom]] and [[Lego]] and [[Star Wa") == " and [[Lego]] and "


def test_balanced_text_is_untouched():
    text = "a [[One]] b [[Two|second]] c"
    assert trim_partial_wikilinks(text) == text


def test_text_with_no_links_is_untouched():
    text = "no links here at all"
    assert trim_partial_wikilinks(text) == text


def test_empty_input():
    assert trim_partial_wikilinks("") == ""


def test_a_lone_open_fragment_becomes_empty():
    assert trim_partial_wikilinks("[[Half A Name") == ""


def test_piped_link_is_not_mistaken_for_a_fragment():
    text = "see [[Real Note|what I called it]] here"
    assert trim_partial_wikilinks(text) == text


def test_only_the_last_open_is_trimmed_not_earlier_valid_ones():
    """Three good links and one fragment: the good ones survive."""
    out = trim_partial_wikilinks("[[A]] [[B]] [[C]] [[D")
    assert out == "[[A]] [[B]] [[C]] "


# --------------------------------------------------------------------------------------
# extract_link_context — the caller
# --------------------------------------------------------------------------------------


def test_excerpt_does_not_end_inside_a_link():
    """The regression: the window closes mid-link, one character into the next target."""
    body = (
        "Gaming partner notes. " + ("filler words to push the window along. " * 6)
        + "We play [[Terraria]] together, and also [[Beat Saber]] on weekends."
    )
    out = extract_link_context(body, "Terraria", window_chars=60)
    assert "[[" not in out or out.count("[[") == out.count("]]"), out


def test_excerpt_does_not_begin_with_an_orphan_close():
    body = (
        "[[Sam Hallstrom]] and others. " + ("more filler text here. " * 6)
        + "The note mentions [[Valheim]] once."
    )
    out = extract_link_context(body, "Valheim", window_chars=60)
    assert not out.lstrip().startswith("]]"), out


@pytest.mark.parametrize("window", [20, 40, 80, 160, 400])
def test_brackets_stay_balanced_at_every_window_size(window):
    """Whatever the budget, the excerpt is never left holding half a link."""
    body = (
        "intro [[Alpha]] " + ("padding padding padding. " * 10)
        + "middle [[Bravo]] and [[Charlie|the third]] tail "
        + ("trailing text. " * 10) + "end [[Delta]]"
    )
    out = extract_link_context(body, "Bravo", window_chars=window)
    assert out.count("[[") == out.count("]]"), f"window={window}: {out!r}"


def test_the_target_link_itself_survives():
    body = "some text [[Target Note]] more text"
    out = extract_link_context(body, "Target Note")
    assert "[[Target Note]]" in out


def test_alias_pipe_inside_the_kept_link_survives():
    """A blanket pipe-strip would manufacture the ghost links this excerpt feeds."""
    body = "context around [[Target Note|shown text]] and after"
    out = extract_link_context(body, "Target Note")
    assert "[[Target Note|shown text]]" in out


def test_no_match_returns_empty():
    assert extract_link_context("nothing relevant", "Missing") == ""


def test_trailing_close_bracket_survives_the_punctuation_strip():
    """The larger of the two producers: `]]` is all non-word characters.

    A blanket trailing `[\\W_]+$` strip turned a well-formed `... [[Beat Saber]]` into an
    unclosed `... [[Beat Saber`, so the excerpt manufactured the defect even when the slice
    had been cut cleanly.
    """
    body = "we played [[Beat Saber]]"
    out = extract_link_context(body, "Beat Saber")
    assert out.endswith("[[Beat Saber]]"), out


def test_ordinary_trailing_punctuation_is_still_stripped():
    body = "context around [[Target]] and then a sentence ends here."
    out = extract_link_context(body, "Target")
    assert not out.endswith("."), out
