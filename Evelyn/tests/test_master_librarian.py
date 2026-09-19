# test_master_librarian.py
# date created: 2026-09-05 17:50:00
# date modified: 2026-09-19 10:40:54
# tags: #test, #master_librarian, #format_librarian, #link_librarian, #unit_test

"""Hermetic unit tests for the Master Librarian pipeline and sub-librarians."""

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import evelyn_config as cfg
from Evelyn.tools import (
    format_librarian,
    link_librarian,
    master_librarian,
    string_utils,
    vault_db,
)


class TestMasterLibrarianPipeline(unittest.TestCase):
    """Hermetic unit tests for format, link, and master orchestrator modules."""

    def test_format_librarian_flow_arrays_and_icons(self):
        """Verify single-line flow array formatting and icon bracket cleaning."""
        raw_note = """---
title: Test Note
tags:
  - tech
  - ai/llm
aliases:
  - "Hello: World"
  - NormalAlias
icon: ["[[avatar.png]]"]
---
# Test Note Body
Here is some text.
"""
        changed, updated, _details = format_librarian.audit_document_format(raw_note)
        self.assertTrue(changed)
        self.assertIn('icon: "Attachments/Icons/avatar.png"', updated)
        self.assertIn("tags: [tech, ai/llm]", updated)
        self.assertIn('aliases: ["Hello: World", NormalAlias]', updated)

    def test_link_librarian_spurious_arrays_outside_code_blocks(self):
        """Verify spurious array wrapping outside code blocks and immunity inside code blocks."""
        raw_text = """
Here is an un-fenced output:
array([[0.33149648]], dtype=float32)

Here is a fenced code block that MUST NOT be touched:
```python
x = array([[1.0, 2.0]], dtype=float32)
```

And inline code that MUST NOT be touched:
`tensor([[5.0]])`
"""
        masked, placeholders = string_utils.protect_code_blocks(raw_text)
        changed, masked = link_librarian.wrap_spurious_code_arrays(masked)
        restored = string_utils.restore_code_blocks(masked, placeholders)

        self.assertTrue(changed)
        # Unfenced should be wrapped in backticks
        self.assertIn("`array([[0.33149648]], dtype=float32)`", restored)
        # Fenced code block must remain unchanged (not double-wrapped)
        self.assertIn("```python\nx = array([[1.0, 2.0]], dtype=float32)\n```", restored)
        # Inline code must remain unchanged (not double-wrapped)
        self.assertIn("`tensor([[5.0]])`", restored)

    def test_qualified_array_calls_are_fenced_whole(self):
        """Verify np./torch. qualifiers stay inside the fence instead of being split off."""
        for src, want in [
            ("cube = torch.tensor([[[1, 2], [3, 4]], [[5, 6], [7, 8]]])",
             "`torch.tensor([[[1, 2], [3, 4]], [[5, 6], [7, 8]]])`"),
            ("x = np.array([[1, 2], [3, 4]])", "`np.array([[1, 2], [3, 4]])`"),
            ("array([[0.33149648]], dtype=float32)", "`array([[0.33149648]], dtype=float32)`"),
        ]:
            changed, out = link_librarian.wrap_spurious_code_arrays(src)
            self.assertTrue(changed, f"Failed to wrap: {src}")
            self.assertIn(want, out)

        # An already-fenced span must not be wrapped twice
        changed, out = link_librarian.wrap_spurious_code_arrays("already `tensor([[5.0]])` fenced")
        self.assertFalse(changed)

    def test_reattach_split_array_qualifiers_heals_prior_damage(self):
        """Verify the pre-mask repair rejoins split qualifiers and strips doubled fences."""
        changed, out = link_librarian.reattach_split_array_qualifiers(
            "xs = torch.`tensor([[-1.0], [0.0]], dtype=torch.float32)`"
        )
        self.assertTrue(changed)
        self.assertIn("`torch.tensor([[-1.0], [0.0]], dtype=torch.float32)`", out)

        changed, out = link_librarian.reattach_split_array_qualifiers("out ``[[0.33149648]]`` here")
        self.assertTrue(changed)
        self.assertIn("[[0.33149648]]", out)
        self.assertNotIn("``", out)

        # Prose spans using doubled backticks must survive untouched
        prose = "prose ``bash pip install langchain `` stays"
        changed, out = link_librarian.reattach_split_array_qualifiers(prose)
        self.assertFalse(changed)
        self.assertEqual(out, prose)

    def test_nested_numeric_literals_are_fenced(self):
        """Verify nested numeric lists are wrapped whole, with wikilinks left alone."""
        changed, out = link_librarian.wrap_spurious_code_arrays("X_new = [[2, 0.5], [3, 1]] y = 1")
        self.assertTrue(changed)
        self.assertIn("`[[2, 0.5], [3, 1]]`", out)

        changed, out = link_librarian.wrap_spurious_code_arrays("tp = [[0.0, 1.0], None, [0.0, 1.0]]")
        self.assertTrue(changed)
        self.assertIn("`[[0.0, 1.0], None, [0.0, 1.0]]`", out)

        for untouched in ["A link [[Voron StealthBurner]] here", "Italic _[[Sad Machine]] here"]:
            changed, out = link_librarian.wrap_spurious_code_arrays(untouched)
            self.assertFalse(changed)
            self.assertEqual(out, untouched)

    def test_link_librarian_alias_hygiene_and_doc_types(self):
        """Verify possessive alias pruning and doc-type alias tag migration."""
        aliases = ["Ricky", "Ricky's", "User Manual", "CustomTool"]
        tags = ["dnd", "character"]
        title = "Ricky"

        changed, clean_aliases, clean_tags, actions = link_librarian.prune_redundant_aliases(
            aliases=aliases,
            tags=tags,
            title=title,
        )

        self.assertTrue(changed)
        self.assertIn("Ricky", clean_aliases)
        self.assertNotIn("Ricky's", clean_aliases)
        self.assertNotIn("User Manual", clean_aliases)
        self.assertIn("user-manual", clean_tags)
        self.assertIn("dnd", clean_tags)
        self.assertTrue(any("pruned_possessive_alias" in a for a in actions))
        self.assertTrue(any("migrated_doc_type_alias" in a for a in actions))

    def test_link_librarian_bare_attachment_resolution(self):
        """Verify bare attachment links are resolved against vault attachments directory."""
        with tempfile.TemporaryDirectory() as tmp_vault:
            # Create mock Attachments folder structure
            fin_dir = os.path.join(tmp_vault, "Attachments", "Source Material", "Financial")
            os.makedirs(fin_dir, exist_ok=True)
            doc_path = os.path.join(fin_dir, "Federal Tax 2024.pdf")
            with open(doc_path, "w") as f:
                f.write("mock pdf")

            text = "Please inspect [[Federal Tax 2024.pdf]] for details."
            changed, updated, count = link_librarian.resolve_bare_attachments(text, vault_root=tmp_vault)

            self.assertTrue(changed)
            self.assertEqual(count, 1)
            self.assertIn("[[Attachments/Source Material/Financial/Federal Tax 2024.pdf]]", updated)

    def test_master_librarian_single_pass_atomic_execution(self):
        """Verify single-pass read-transform-write atomic execution in temp directory."""
        with tempfile.TemporaryDirectory() as tmp_vault:
            note_content = """---
title: Gadget Spec
aliases: ["Gadget", "Gadget's", "Specification Sheet"]
tags: [hardware]
---
# Gadget Spec
Here is raw output:
array([[1.5, 2.5]])
"""
            note_path = os.path.join(tmp_vault, "Notes", "Gadget Spec.md")
            os.makedirs(os.path.dirname(note_path), exist_ok=True)
            with open(note_path, "w", encoding="utf-8") as f:
                f.write(note_content)

            with patch("Evelyn.tools.vault_db.update_document_librarian_audit") as mock_update, \
                 patch("Evelyn.tools.vault_db.log_librarian_activity") as mock_log:

                res = master_librarian.audit_single_document(
                    doc_path="Notes/Gadget Spec.md",
                    vault_root=tmp_vault,
                )

                self.assertEqual(res["status"], "ok")
                self.assertTrue(res["modified"])

                # Read updated file on disk
                with open(note_path, encoding="utf-8") as f:
                    updated_text = f.read()

                # Possessive should be pruned, spec-sheet migrated to tags, array wrapped
                self.assertNotIn("Gadget's", updated_text)
                self.assertIn("spec-sheet", updated_text)
                self.assertIn("`array([[1.5, 2.5]])`", updated_text)

                mock_update.assert_called_once()
                mock_log.assert_called_once()

    def test_master_librarian_tag_normalization_and_collection_inheritance(self):
        """Verify that Master Librarian normalizes tags and inherits parent collection tags."""
        with tempfile.TemporaryDirectory() as tmp_vault:
            # Create a mock parent collection with an _index.md
            book_dir = os.path.join(tmp_vault, "Manuals", "Voron")
            os.makedirs(book_dir, exist_ok=True)
            index_path = os.path.join(book_dir, "_index.md")
            with open(index_path, "w", encoding="utf-8") as f:
                f.write("---\ntitle: Voron Manual\ntags: [3d-printing, hardware/voron, moc]\n---\n# Voron Manual\n")

            # Create a chapter note with flat and inconsistent tags
            ch_path = os.path.join(book_dir, "001 - Wiring.md")
            with open(ch_path, "w", encoding="utf-8") as f:
                f.write("---\ntitle: 001 - Wiring\ntags: [kw/electronics, bad_tag_format]\n---\n# 001 - Wiring\nContent here.\n")

            with patch("Evelyn.tools.vault_db.update_document_librarian_audit"), \
                 patch("Evelyn.tools.vault_db.log_librarian_activity"):

                res = master_librarian.audit_single_document(
                    doc_path="Manuals/Voron/001 - Wiring.md",
                    vault_root=tmp_vault,
                    inherit_parent_tags=True,
                )

                self.assertEqual(res["status"], "ok")
                self.assertTrue(res["modified"])

                with open(ch_path, encoding="utf-8") as f:
                    updated_text = f.read()

                # Should have cleaned kw/ prefix, normalized format, and inherited 3d-printing & hardware/voron
                self.assertNotIn("kw/electronics", updated_text)
                self.assertIn("electronics", updated_text)
                self.assertIn("3d-printing", updated_text)
                self.assertIn("hardware/voron", updated_text)
                # 'moc' should NOT be inherited
                self.assertNotIn("tags: [moc", updated_text)

    def test_link_librarian_ghost_stub_guardrail(self):
        """Verify Tier 1 vs Tier 2 ghost link stub guardrail and XML envelope payload."""
        with tempfile.TemporaryDirectory() as tmp_vault:
            # 1. Single reference should return below_threshold
            res_below = link_librarian.create_ghost_link_stub(
                target_name="ObscureConcept",
                source_path="Notes/Source.md",
                context_excerpt="Mentioning [[ObscureConcept]] once in a note.",
                vault_root=tmp_vault,
                min_refs=2,
                min_context_chars=200,
            )
            self.assertEqual(res_below["status"], "below_threshold")
            self.assertFalse(os.path.exists(os.path.join(tmp_vault, "ObscureConcept.md")))

            # 2. When min_refs and context thresholds are met and auto-stubs enabled
            with patch.object(cfg, "MASTER_LIBRARIAN_AUTO_STUBS", True):
                res_tier1 = link_librarian.create_ghost_link_stub(
                    target_name="KnownEntity",
                    source_path="Notes/Source.md",
                    context_excerpt="A recurring entity in the vault.",
                    vault_root=tmp_vault,
                    min_refs=0,
                    min_context_chars=0,
                    min_snippet_chars=0,
                )
            self.assertEqual(res_tier1["status"], "created_stub")
            self.assertIn("xml_payload", res_tier1)
            stub_file = os.path.join(tmp_vault, "KnownEntity.md")
            self.assertTrue(os.path.exists(stub_file))
            with open(stub_file, encoding="utf-8") as f:
                stub_content = f.read()
            self.assertIn("[!ABSTRACT]", stub_content)
            self.assertIn("[[Source]]", stub_content)
            self.assertIn('- **[[Source]]**: "A recurring entity in the vault."', stub_content)

    def test_stub_xml_envelope_roundtrip_and_rendering(self):
        """Verify XML stub serialization, deserialization, and callout line safety."""
        payload = link_librarian.StubPayload(
            target_name="QuantumProcessor",
            source_path="Research/Hardware.md",
            context_excerpt="Testing the new [[QuantumProcessor]] in the lab.\nSecond line should be flattened.",
            domain="hardware",
            tags=["stub", "concept", "hardware"],
            min_refs=2,
            ref_count=3,
        )
        xml_str = link_librarian.render_stub_xml(payload)
        self.assertIn('<entity_stub target="QuantumProcessor"', xml_str)
        self.assertIn("<source_path>Research/Hardware.md</source_path>", xml_str)

        # Deserialize back to payload
        parsed = link_librarian.parse_stub_xml(xml_str)
        self.assertEqual(parsed.target_name, "QuantumProcessor")
        # Parsed references must carry 'snippet' parity with harvest_entity_references
        for ref in parsed.references:
            self.assertEqual(ref.get("snippet"), ref.get("context"))
        self.assertEqual(parsed.source_path, "Research/Hardware.md")
        self.assertEqual(parsed.ref_count, 3)

        # Render markdown and verify callout integrity
        md_text = link_librarian.render_stub_markdown(parsed, now_str="2026-09-06 12:00:00")
        self.assertIn("title: QuantumProcessor", md_text)
        self.assertIn("> [!ABSTRACT]", md_text)
        self.assertIn("> Conceptual entity stub for [[QuantumProcessor]]", md_text)
        # Verify EVERY line in the abstract block starts with '> '
        abstract_start = md_text.index("> [!ABSTRACT]")
        abstract_end = md_text.index("## 🧭 Context & Mentions") if "## 🧭 Context & Mentions" in md_text else md_text.index("## 🔗 References")
        callout_lines = [l for l in md_text[abstract_start:abstract_end].splitlines() if l.strip()]
        for line in callout_lines:
            self.assertTrue(line.startswith(">"), f"Line broke callout boundary: {line}")

    def test_target_sanitization_and_exclusion(self):
        """Verify that .md extensions are stripped and chapter/TOC names are rejected."""
        # .md stripping for valid entity
        valid, clean = link_librarian.is_valid_entity_target("Voron StealthBurner.md")
        self.assertTrue(valid)
        self.assertEqual(clean, "Voron StealthBurner")

        # Project files and docs rejected
        for doc_file in ["AGENTS.md", "ROADMAP.md", "README.md", "SUPPORT.md"]:
            valid, _ = link_librarian.is_valid_entity_target(doc_file)
            self.assertFalse(valid, f"Failed to reject project file: {doc_file}")

        # Chapter prefix rejection
        valid, _ = link_librarian.is_valid_entity_target("01 - Introduction")
        self.assertFalse(valid)
        valid, _ = link_librarian.is_valid_entity_target("002 - Bottom Freezer")
        self.assertFalse(valid)

        # Blacklisted generic headings
        for blacklisted in ["Features", "Safety", "Other", "Table of Contents", "_index", "Preface"]:
            valid, _ = link_librarian.is_valid_entity_target(blacklisted)
            self.assertFalse(valid, f"Failed to reject blacklisted target: {blacklisted}")

        # Index notes and MOCs
        for index_name in ["Visualizing Generative AI_index", "Samsung NE59J7630SS_index", "Core_Architecture_moc", "_index"]:
            valid, _ = link_librarian.is_valid_entity_target(index_name)
            self.assertFalse(valid, f"Failed to reject index/MOC target: {index_name}")

        # OCR private-use glyphs
        valid, _ = link_librarian.is_valid_entity_target("01 - \uf0ea !")
        self.assertFalse(valid)

        # Source code fragments captured by double-bracket syntax collisions
        for code_frag in [
            '"petal length (cm)", "petal width (cm)"',
            '"housing_median_age"',
            '"latitude", "longitude"',
            "${link}",
            'review["label"',
        ]:
            valid, _ = link_librarian.is_valid_entity_target(code_frag)
            self.assertFalse(valid, f"Failed to reject code fragment target: {code_frag}")

        # Legitimate stems carrying colons or parentheses must survive
        for legit in ["Clair Obscur: Expedition 33", "NieR: Automata", "Oberon (warframe)"]:
            valid, clean = link_librarian.is_valid_entity_target(legit)
            self.assertTrue(valid, f"Wrongly rejected legitimate target: {legit}")
            self.assertEqual(clean, legit)

    def test_code_subscript_not_counted_as_ghost_link(self):
        """Verify pandas/NumPy double-subscripts are never harvested as ghost links."""
        with tempfile.TemporaryDirectory() as tmp_vault:
            test_body = (
                "Loading the iris dataset (introduced in Chapter 4):\n"
                'X = iris.data[["petal length (cm)", "petal width (cm)"]].values\n'
                'housing_num = housing[["housing_median_age"]]\n'
                "An italicized real link _[[Sad Machine]] must still register.\n"
                "And a plain ghost link [[Completely Unknown Note]].\n"
            )

            def mock_resolver(target, vault_root=None):
                return False, target

            with patch("Evelyn.tools.link_librarian.resolve_canonical_link_target", side_effect=mock_resolver):
                _, _, details = link_librarian.audit_document_links(
                    content=test_body,
                    path="Notes/Perceptron.md",
                    vault_root=tmp_vault,
                )

            self.assertEqual(
                sorted(details["ghost_targets"]),
                ["Completely Unknown Note", "Sad Machine"],
            )
            self.assertEqual(details["ghost_links_count"], 2)

    def test_extract_link_context_clean_isolation(self):
        """Verify extract_link_context slices surrounding sentence and omits YAML headers."""
        raw_body = """
The system architecture incorporates [[QuantumCore]] for accelerated vector computation.
Additional bench tests confirmed 4x speedup over baseline models.
"""
        context = master_librarian.extract_link_context(raw_body, "QuantumCore")
        self.assertIn("QuantumCore", context)
        self.assertIn("accelerated vector computation", context)
        self.assertNotIn("---", context)
        self.assertNotIn("\n", context)

    def test_index_librarian_toc_synchronization(self):
        """Verify that Master Librarian synchronizes folder table of contents (_index.md)."""
        with tempfile.TemporaryDirectory() as tmp_vault:
            vol_dir = os.path.join(tmp_vault, "Manuals", "Guide")
            os.makedirs(vol_dir, exist_ok=True)
            index_path = os.path.join(vol_dir, "_index.md")
            with open(index_path, "w", encoding="utf-8") as f:
                f.write("---\ntitle: Guide Index\ntags: [manual]\n---\n# Guide Index\n- [[001 - Setup]]\n")

            # Create existing linked note and unlinked note
            ch1_path = os.path.join(vol_dir, "001 - Setup.md")
            with open(ch1_path, "w", encoding="utf-8") as f:
                f.write("# Setup\nContent")
            ch2_path = os.path.join(vol_dir, "002 - Advanced.md")
            with open(ch2_path, "w", encoding="utf-8") as f:
                f.write("# Advanced\nContent")

            with patch("Evelyn.tools.vault_db.update_document_librarian_audit"), \
                 patch("Evelyn.tools.vault_db.log_librarian_activity"):

                # Audit the index document itself
                res = master_librarian.audit_single_document(
                    doc_path="Manuals/Guide/_index.md",
                    vault_root=tmp_vault,
                )

                self.assertEqual(res["status"], "ok")
                self.assertTrue(res["modified"])
                self.assertTrue(any("index_synced" in a for a in res["actions"]))

                with open(index_path, encoding="utf-8") as f:
                    updated_index = f.read()

                # Missing note 002 - Advanced should be added under Additional Notes
                self.assertIn("[[002 - Advanced]]", updated_index)
                self.assertIn("## 📑 Additional Notes", updated_index)


    def test_harvest_entity_references(self):
        """Verify harvesting entity references across multiple vault files with aliases and casing."""
        with tempfile.TemporaryDirectory() as tmp_vault:
            # Note 1: Direct link
            n1_dir = os.path.join(tmp_vault, "Lore", "History")
            os.makedirs(n1_dir, exist_ok=True)
            with open(os.path.join(n1_dir, "Chronicles.md"), "w", encoding="utf-8") as f:
                f.write("---\ntitle: Chronicles\n---\n# Chronicles\nPrince [[Caladorn]] led the defense of the western frontier during the summer campaign.\n")

            # Note 2: Aliased link
            n2_dir = os.path.join(tmp_vault, "People")
            os.makedirs(n2_dir, exist_ok=True)
            with open(os.path.join(n2_dir, "Queen_Elora.md"), "w", encoding="utf-8") as f:
                f.write("---\ntitle: Queen Elora\n---\n# Queen Elora\nShe consulted with [[Caladorn|Prince Caladorn]] regarding the diplomatic treaty.\n")

            # Note 3: Case-insensitive link
            with open(os.path.join(tmp_vault, "Dispatches.md"), "w", encoding="utf-8") as f:
                f.write("---\ntitle: Dispatches\n---\nUrgent missive from [[caladorn]] arrived at dawn requesting grain supplies.\n")

            # Note 4: Unrelated note
            with open(os.path.join(tmp_vault, "Unrelated.md"), "w", encoding="utf-8") as f:
                f.write("---\ntitle: Unrelated\n---\nJust general notes without target.\n")

            refs = link_librarian.harvest_entity_references("Caladorn", vault_root=tmp_vault)
            self.assertEqual(len(refs), 3)
            sources = [r["source"] for r in refs]
            self.assertIn("Lore/History/Chronicles.md", sources)
            self.assertIn("People/Queen_Elora.md", sources)
            self.assertIn("Dispatches.md", sources)

            for r in refs:
                self.assertTrue(len(r["snippet"]) > 20)
                self.assertNotIn("---", r["snippet"])

    def test_ghost_link_stub_below_threshold_gate(self):
        """Verify stubs below ref count or context character threshold are rejected as below_threshold."""
        with tempfile.TemporaryDirectory() as tmp_vault:
            n1_dir = os.path.join(tmp_vault, "Notes")
            os.makedirs(n1_dir, exist_ok=True)
            # Only one citation with short context
            with open(os.path.join(n1_dir, "Short.md"), "w", encoding="utf-8") as f:
                f.write("See [[TrivialTarget]] for more info.")

            res = link_librarian.create_ghost_link_stub(
                target_name="TrivialTarget",
                source_path="Notes/Short.md",
                context_excerpt="See [[TrivialTarget]] for more info.",
                vault_root=tmp_vault,
                min_refs=2,
                min_context_chars=200,
            )

            self.assertEqual(res["status"], "below_threshold")
            self.assertIn("Context threshold not met", res["reason"])
            self.assertEqual(res["ref_count"], 1)
            self.assertFalse(os.path.exists(os.path.join(tmp_vault, "TrivialTarget.md")))

    def test_ghost_link_stub_meets_threshold_harvest(self):
        """Verify stubs meeting multi-ref and context thresholds harvest all sources and build XML."""
        with tempfile.TemporaryDirectory() as tmp_vault:
            # File 1: Substantial context (>120 chars)
            with open(os.path.join(tmp_vault, "NoteA.md"), "w", encoding="utf-8") as f:
                f.write("# Note A\nThe legendary hero [[Aurelius]] guarded the crystal citadel against all incursions during the long siege of the northern frontier.\n")

            # File 2: Substantial context (>120 chars)
            with open(os.path.join(tmp_vault, "NoteB.md"), "w", encoding="utf-8") as f:
                f.write("# Note B\nAccording to ancient records, [[Aurelius|Commander Aurelius]] established the first council of guardians to maintain peace across the five provinces.\n")

            with patch("Evelyn.tools.memory_db.insert_proposal", return_value=123):
                res = link_librarian.create_ghost_link_stub(
                    target_name="Aurelius",
                    source_path="NoteA.md",
                    vault_root=tmp_vault,
                    min_refs=2,
                    min_context_chars=200,
                    min_snippet_chars=40,
                )

            self.assertEqual(res["status"], "tier_2_proposal")
            self.assertEqual(res["ref_count"], 2)
            self.assertGreaterEqual(res["total_context_chars"], 200)

            # Parse returned XML payload
            payload = link_librarian.parse_stub_xml(res["xml_payload"])
            self.assertEqual(payload.target_name, "Aurelius")
            self.assertEqual(len(payload.sources), 2)
            self.assertIn("NoteA.md", payload.sources)
            self.assertIn("NoteB.md", payload.sources)
            self.assertEqual(len(payload.references), 2)
            self.assertTrue(len(payload.synthesized_abstract) > 0)

            # Markdown rendering check
            md = link_librarian.render_stub_markdown(payload)
            self.assertIn("> [!ABSTRACT]", md)
            self.assertIn("## 🧭 Context & Mentions", md)
            self.assertIn("- **[[NoteA]]**:", md)
            self.assertIn("- **[[NoteB]]**:", md)
            self.assertIn("## 🔗 References", md)
            self.assertIn("- [[NoteA]]", md)
            self.assertIn("- [[NoteB]]", md)

    def test_synthesize_entity_abstract_fallback(self):
        """Verify fallback abstract synthesis when LLM is unavailable."""
        references = [
            {"source": "Chapter1.md", "snippet": "Lord Gregory defended the castle walls with courage and distinction during the conflict."},
            {"source": "Chapter2.md", "snippet": "Gregory negotiated trade terms with foreign dignitaries to restore local prosperity."},
        ]
        # Test fallback directly with synthesis disabled
        abstract, mode = link_librarian.synthesize_entity_abstract("Gregory", references, use_llm=False)
        self.assertEqual(mode, "fallback")
        self.assertIn("cited across 2 notes in the vault", abstract)
        self.assertIn("Chapter1", abstract)
        self.assertIn("Chapter2", abstract)

    def test_synthesis_mode_survives_xml_roundtrip(self):
        """Verify the stub envelope reports honestly whether Ollama wrote the abstract."""
        payload = link_librarian.StubPayload(
            target_name="QuantumProcessor",
            synthesized_abstract="A processor used for lab benchmarking of vector workloads.",
            synthesis_mode="llm",
        )
        xml_str = link_librarian.render_stub_xml(payload)
        self.assertIn('synthesis_mode="llm"', xml_str)
        self.assertEqual(link_librarian.parse_stub_xml(xml_str).synthesis_mode, "llm")

        # Payloads written before this field existed must degrade to 'fallback'
        legacy = '<entity_stub target="Legacy" ref_count="2" min_refs="2" context_chars="0" />'
        self.assertEqual(link_librarian.parse_stub_xml(legacy).synthesis_mode, "fallback")

    def test_notation_title_healing_and_idempotency(self):
        """Verify format librarian repairs notation titles and subsequent runs are strictly idempotent."""
        with tempfile.TemporaryDirectory() as tmp_vault:
            doc_rel = "20 - sharp œ œ ˙ œ œ œ œ œ œ ˙.md"
            doc_file = os.path.join(tmp_vault, doc_rel)
            initial_content = (
                "---\n"
                "title: 20 - sharp œ œ ˙ œ œ œ œ œ œ ˙.md\n"
                "aliases: [\"20 - sharp œ œ ˙\", \"Twinkle Star\"]\n"
                "tags: [reference-library]\n"
                "---\n"
                "# Cello Method — ? œ œ ˙ œ œ œ œ œ œ ˙\n\n"
                "### TWINKLE TWINKLE LITTLE STAR\n\n"
                "Twin - kle twin - kle lit - tle star, how I won - der what you are.\n"
            )
            with open(doc_file, "w", encoding="utf-8") as f:
                f.write(initial_content)

            # First audit run: title should be repaired from body heading, and alias leak purged
            res1 = master_librarian.audit_single_document(
                doc_path=doc_rel,
                vault_root=tmp_vault,
                include_tags=False,
            )
            self.assertTrue(res1.get("modified"), "First run should modify note to heal notation title")
            self.assertIn("repaired_notation_title", res1.get("format_details", {}).get("format_fixes", []))
            self.assertIn("purged_notation_aliases", res1.get("format_details", {}).get("format_fixes", []))

            with open(doc_file, encoding="utf-8") as f:
                updated_content = f.read()

            self.assertIn("title: Twinkle Twinkle Little Star", updated_content)
            self.assertIn("# Twinkle Twinkle Little Star", updated_content)
            self.assertNotIn(".md", updated_content.split("---")[1])  # No .md in frontmatter
            self.assertIn('aliases: ["Twinkle Star"]', updated_content)
            self.assertNotIn("sharp œ œ ˙", updated_content.split("---")[1])

            # Second audit run: must be 100% idempotent (zero changes)
            res2 = master_librarian.audit_single_document(
                doc_path=doc_rel,
                vault_root=tmp_vault,
                include_tags=False,
            )
            self.assertFalse(res2.get("modified"), "Second run on repaired note must be strictly idempotent")

    def test_tokenize_wikilink(self):
        """Verify wikilink tokenization into stem, subpath, and display text."""
        self.assertEqual(
            link_librarian.tokenize_wikilink("Oura Ring"),
            ("Oura Ring", "", ""),
        )
        self.assertEqual(
            link_librarian.tokenize_wikilink("Oura Ring|ring"),
            ("Oura Ring", "", "ring"),
        )
        self.assertEqual(
            link_librarian.tokenize_wikilink("Oberon#Abilities"),
            ("Oberon", "#Abilities", ""),
        )
        self.assertEqual(
            link_librarian.tokenize_wikilink("Oberon#Abilities|kit"),
            ("Oberon", "#Abilities", "kit"),
        )
        self.assertEqual(
            link_librarian.tokenize_wikilink("Note#^block123|display text"),
            ("Note", "#^block123", "display text"),
        )

    def test_canonicalize_document_wikilinks_and_ghost_count(self):
        """Verify canonical link target rewriting and ghost link filtering."""
        with tempfile.TemporaryDirectory() as tmp_vault:
            # Create a disambiguated note and a note with aliases
            os.makedirs(os.path.join(tmp_vault, "Notes"), exist_ok=True)
            with open(os.path.join(tmp_vault, "Notes", "Oura.md"), "w", encoding="utf-8") as f:
                f.write("---\ntitle: Oura\naliases: [\"Oura Ring\"]\n---\n# Oura\n")
            with open(os.path.join(tmp_vault, "Notes", "Oberon (warframe).md"), "w", encoding="utf-8") as f:
                f.write("---\ntitle: Oberon (warframe)\naliases: []\n---\n# Oberon (Warframe)\n")

            test_body = (
                "Here is an unaliased link [[Oura Ring]].\n"
                "Here is an aliased link [[Oura Ring|ring]].\n"
                "Here is a disambiguated link [[Oberon#Abilities|Oberon kit]].\n"
                "Here is a code block that must NOT change:\n"
                "```markdown\n[[Oura Ring]]\n```\n"
                "Here is a real ghost link: [[Completely Unknown Note]].\n"
            )

            # Mock resolve_canonical_link_target using the temporary vault structure
            def mock_resolver(target, vault_root=None):
                t_lower = target.lower()
                if t_lower == "oura ring":
                    return True, "Oura"
                elif t_lower == "oberon":
                    return True, "Oberon (warframe)"
                elif t_lower in ("oura", "oberon (warframe)"):
                    return True, target
                return False, target

            with patch("Evelyn.tools.link_librarian.resolve_canonical_link_target", side_effect=mock_resolver):
                changed, updated_doc, details = link_librarian.audit_document_links(
                    content=test_body,
                    path="Notes/TestRef.md",
                    vault_root=tmp_vault,
                )

            self.assertTrue(changed)
            self.assertIn("[[Oura|Oura Ring]]", updated_doc)
            self.assertIn("[[Oura|ring]]", updated_doc)
            self.assertIn("[[Oberon (warframe)#Abilities|Oberon kit]]", updated_doc)
            self.assertIn("```markdown\n[[Oura Ring]]\n```", updated_doc)  # Code block preserved!
            self.assertEqual(details["ghost_links_count"], 1)
            self.assertEqual(details["ghost_targets"], ["Completely Unknown Note"])

    def test_resolve_canonical_link_target_hierarchy(self):
        """Verify strict 5-stage hierarchy of canonical target resolution including ambiguity guards."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "test_vault.db")
            import sqlite3
            con = sqlite3.connect(db_path)
            con.execute("""
                CREATE TABLE vault_documents (
                    path TEXT PRIMARY KEY,
                    title TEXT,
                    aliases TEXT
                )
            """)
            con.execute("INSERT INTO vault_documents VALUES (?, ?, ?)", ("Notes/Oura.md", "Oura", "Oura Ring,Oura Ring 4"))
            con.execute("INSERT INTO vault_documents VALUES (?, ?, ?)", ("Notes/Programs/Antigravity (app).md", "Antigravity", ""))
            con.execute("INSERT INTO vault_documents VALUES (?, ?, ?)", ("Notes/The Vault.md", "The Vault", "Vault"))
            con.execute("INSERT INTO vault_documents VALUES (?, ?, ?)", ("Notes/Gem-Compass.md", "Gem-Compass", ""))
            # Add an ambiguous case: 2 notes matching "Zeus (*)"
            con.execute("INSERT INTO vault_documents VALUES (?, ?, ?)", ("Mythology/Zeus (myth).md", "Zeus (myth)", ""))
            con.execute("INSERT INTO vault_documents VALUES (?, ?, ?)", ("Games/Zeus (game).md", "Zeus (game)", ""))
            con.commit()
            con.close()

            with patch("Evelyn.tools.vault_db.get_db", side_effect=lambda: sqlite3.connect(db_path)):
                # 1. Direct stem
                self.assertEqual(link_librarian.resolve_canonical_link_target("Oura", vault_root=tmp_dir), (True, "Oura"))
                # 2. Alias match
                self.assertEqual(link_librarian.resolve_canonical_link_target("Oura Ring", vault_root=tmp_dir), (True, "Oura"))
                # 3. Disambiguation match (single)
                self.assertEqual(link_librarian.resolve_canonical_link_target("Antigravity", vault_root=tmp_dir), (True, "Antigravity (app)"))
                # 4. Ambiguity guard (multiple Zeus (*))
                self.assertEqual(link_librarian.resolve_canonical_link_target("Zeus", vault_root=tmp_dir), (False, "Zeus"))
                # 5. Hyphen/underscore normalization
                self.assertEqual(link_librarian.resolve_canonical_link_target("Gem Compass", vault_root=tmp_dir), (True, "Gem-Compass"))
            con.close()

    def test_master_librarian_tier_2_proposal_logging(self):
        """Verify Master Librarian routes recurring ghost links to Tier 2 proposals when auto_create is False."""
        with tempfile.TemporaryDirectory() as tmp_vault:
            doc1_path = os.path.join(tmp_vault, "Source1.md")
            doc2_path = os.path.join(tmp_vault, "Source2.md")
            with open(doc1_path, "w", encoding="utf-8") as f:
                f.write(
                    "# Source 1\n"
                    "The legendary artifact [[Excalibur]] was discovered in the mystical cave during the exploration.\n"
                )
            with open(doc2_path, "w", encoding="utf-8") as f:
                f.write(
                    "# Source 2\n"
                    "Ancient manuscripts describe [[Excalibur]] as a weapon of great power forged by mystical beings.\n"
                )

            with patch("Evelyn.tools.memory_db.get_pending_proposals", return_value=[]), \
                 patch("Evelyn.tools.memory_db.insert_proposal", return_value=999) as mock_insert, \
                 patch("Evelyn.tools.vault_db.update_document_librarian_audit"), \
                 patch("Evelyn.tools.vault_db.log_librarian_activity"), \
                 patch.object(cfg, "MASTER_LIBRARIAN_AUTO_STUBS", False), \
                 patch.object(cfg, "LIBRARIAN_GHOST_STUB_MIN_REFS", 2), \
                 patch.object(cfg, "LIBRARIAN_GHOST_STUB_MIN_CONTEXT_CHARS", 50), \
                 patch.object(cfg, "LIBRARIAN_GHOST_STUB_MIN_SNIPPET_CHARS", 20):

                res = master_librarian.audit_single_document(
                    doc_path="Source1.md",
                    vault_root=tmp_vault,
                    auto_create_ghost_stubs=False,
                )

                self.assertEqual(res["status"], "ok")
                self.assertIn("proposed_stubs:1", res["actions"])
                mock_insert.assert_called_once()
                call_kwargs = mock_insert.call_args[1]
                self.assertEqual(call_kwargs["type"], "ghost_link_stub")
                self.assertEqual(call_kwargs["topic"], "Excalibur")

    def test_vault_db_librarian_audit_updates_tag_timestamp(self):
        """Verify update_document_librarian_audit sets last_tag_audit along with librarian/link/format audits."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "vault.db")
            con = sqlite3.connect(db_path)
            con.execute(
                "CREATE TABLE vault_documents ("
                "path TEXT PRIMARY KEY, title TEXT, mtime REAL, tags TEXT, aliases TEXT, "
                "last_librarian_audit REAL DEFAULT 0, last_link_audit REAL DEFAULT 0, "
                "last_format_audit REAL DEFAULT 0, last_tag_audit REAL DEFAULT 0, ghost_link_count INTEGER DEFAULT 0)"
            )
            con.execute("INSERT INTO vault_documents (path, title) VALUES ('test.md', 'Test Note')")
            con.commit()
            con.close()

            with patch("Evelyn.tools.vault_db.get_db", side_effect=lambda: sqlite3.connect(db_path)), \
                 patch.object(cfg, "VAULT_DB_PATH", db_path):
                vault_db.update_document_librarian_audit("test.md", ghost_count=3)

            con = sqlite3.connect(db_path)
            row = con.execute(
                "SELECT last_librarian_audit, last_link_audit, last_format_audit, last_tag_audit, ghost_link_count "
                "FROM vault_documents WHERE path = 'test.md'"
            ).fetchone()
            con.close()

            self.assertIsNotNone(row)
            self.assertGreater(row[0], 0)  # last_librarian_audit
            self.assertGreater(row[1], 0)  # last_link_audit
            self.assertGreater(row[2], 0)  # last_format_audit
            self.assertGreater(row[3], 0)  # last_tag_audit
            self.assertEqual(row[4], 3)    # ghost_link_count


if __name__ == "__main__":
    unittest.main()

