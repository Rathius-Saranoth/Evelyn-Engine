# test_subject_pass_facet_guard.py
# date created: 2026-09-24 17:10:00
# date modified: 2026-09-24 17:05:19
# tags: #test, #taxonomy, #librarian, #facets

"""Subject indexing may not emit a facet, and one form axis survives (v000.006.219).

`audit_document_tags` runs the §4 profile pass first, which correctly settles a single
`type/` tag and drops any other. The subject pass then appended whatever it resolved, with no
facet guard — and since the registry holds facet values as terms, a phrase like "reference"
looks up `type/reference` successfully. Two Reference Library chapters ended up carrying both
`type/guide` and `type/reference`, which §3.4 forbids.

What facet a document carries is decided by its class, never by what it is about.
"""

import pytest

from Evelyn.tools import tag_librarian


class TestSubjectPassRejectsFacets:
    """`reconcile_subjects` resolves phrases against the registry; facets are not subjects."""

    @pytest.fixture(autouse=True)
    def _no_vectors(self, monkeypatch):
        """Stage 2 is a vector lookup; this test is about stage 1's lexical resolution."""
        monkeypatch.setattr(tag_librarian, "_vector_lookup", lambda _p: (None, 1.0, 0.0))

    @pytest.mark.parametrize(
        "facet", ["type/reference", "motif/storm", "setting/forest", "event/move"]
    )
    def test_a_phrase_resolving_to_a_facet_is_not_applied(self, facet, monkeypatch):
        monkeypatch.setattr(tag_librarian, "_registry_surface_forms", lambda: {facet: facet})
        monkeypatch.setattr(tag_librarian, "_lexical_lookup", lambda _c, _s: facet)

        applied, _proposals = tag_librarian.reconcile_subjects([facet], existing=[])

        assert applied == [], f"{facet} is a facet, not a subject"

    def test_a_subject_term_still_applies(self, monkeypatch):
        monkeypatch.setattr(tag_librarian, "_registry_surface_forms", lambda: {"coffee": "coffee"})
        monkeypatch.setattr(tag_librarian, "_lexical_lookup", lambda _c, _s: "coffee")

        applied, _ = tag_librarian.reconcile_subjects(["coffee"], existing=[])

        assert applied == ["coffee"]

    def test_a_decomposed_phrase_drops_its_facet_part(self, monkeypatch):
        """The hyphen-splitting branch resolves each part separately and needs the same guard."""
        table = {"reference": "type/reference", "guide": "type/guide", "python": "python"}
        monkeypatch.setattr(tag_librarian, "_registry_surface_forms", lambda: table)
        monkeypatch.setattr(
            tag_librarian, "_lexical_lookup",
            lambda c, _s: table.get(c) if c in table else None,
        )

        applied, _ = tag_librarian.reconcile_subjects(["python-reference"], existing=[])

        assert applied == ["python"], "the facet part must be dropped, the subject kept"
