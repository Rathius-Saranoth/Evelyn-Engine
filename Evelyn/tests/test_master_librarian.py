# test_master_librarian.py
# date created: 2026-09-05 17:50:00
# date modified: 2026-09-08 18:27:14
# tags: #test, #master_librarian, #format_librarian, #link_librarian, #unit_test

"""Hermetic unit tests for the Master Librarian pipeline and sub-librarians."""

import os
import tempfile
import unittest
from unittest.mock import patch

import evelyn_config as cfg
from Evelyn.tools import format_librarian, link_librarian, master_librarian, string_utils


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
            self.assertIn('Context: "A recurring entity in the vault."', stub_content)

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
        abstract = link_librarian.synthesize_entity_abstract("Gregory", references, use_llm=False)
        self.assertIn("cited across 2 notes in the vault", abstract)
        self.assertIn("Chapter1", abstract)
        self.assertIn("Chapter2", abstract)

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


if __name__ == "__main__":
    unittest.main()

