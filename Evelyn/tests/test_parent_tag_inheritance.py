# test_parent_tag_inheritance.py
# date created: 2026-09-23 18:40:00
# date modified: 2026-09-23 18:27:00
# tags: #test, #taxonomy, #librarian, #inheritance

"""Parent-tag inheritance is opt-in and never carries a facet (v000.006.214).

`audit_single_document` copied a folder's `_index.md` tags onto every document inside it, and
defaulted to on. After v000.006.213 every index note carries `type/moc`, so inheritance would
have given each chapter a second form-axis tag — §3.4 allows exactly one per document. The old
filter only dropped the literal strings "moc" and "index", not the facet-prefixed form.
"""

import inspect

from Evelyn.tools import master_librarian


def test_inheritance_is_off_by_default():
    sig = inspect.signature(master_librarian.audit_single_document)
    assert sig.parameters["inherit_parent_tags"].default is False


class TestFacetsAreNotInherited:
    """The filter lives inline in audit_single_document; this asserts its rule directly."""

    FACETS = ("type/", "motif/", "setting/", "event/")

    @staticmethod
    def _inheritable(raw):
        return [
            str(t) for t in raw
            if not str(t).startswith(("type/", "motif/", "setting/", "event/"))
            and str(t).lower() not in ("moc", "index")
        ]

    def test_a_form_axis_tag_is_never_inherited(self):
        """The case that forced this: an index note is a `type/moc`, its chapters are not."""
        assert self._inheritable(["type/moc", "psychology", "personality"]) == [
            "psychology", "personality",
        ]

    def test_conditional_facets_are_not_inherited_either(self):
        raw = ["motif/darkness", "setting/forest", "event/move", "fantasy"]

        assert self._inheritable(raw) == ["fantasy"]

    def test_subject_terms_still_pass_through(self):
        raw = ["relationships", "love-languages", "marriage"]

        assert self._inheritable(raw) == raw

    def test_the_legacy_literal_filter_is_kept(self):
        assert self._inheritable(["MOC", "Index", "genealogy"]) == ["genealogy"]

    def test_the_inline_filter_matches_this_rule(self):
        """Guards against the source drifting from the assertions above."""
        src = inspect.getsource(master_librarian.audit_single_document)

        assert 'startswith(("type/", "motif/", "setting/", "event/"))' in src
