# test_master_librarian.py
# date created: 2026-09-05 17:50:00
# date modified: 2026-09-05 17:50:00
# tags: #test, #master_librarian, #format_librarian, #link_librarian, #unit_test

"""Hermetic unit tests for the Master Librarian pipeline and sub-librarians."""

import os
import tempfile
import unittest
from unittest.mock import patch

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
        """Verify Tier 1 vs Tier 2 ghost link stub guardrail."""
        with tempfile.TemporaryDirectory() as tmp_vault:
            # 1. Single reference should return Tier 2 proposal
            res_tier2 = link_librarian.create_ghost_link_stub(
                target_name="ObscureConcept",
                source_path="Notes/Source.md",
                context_excerpt="Mentioning [[ObscureConcept]] once.",
                vault_root=tmp_vault,
                min_refs=2,
            )
            self.assertEqual(res_tier2["status"], "tier_2_proposal")
            self.assertFalse(os.path.exists(os.path.join(tmp_vault, "ObscureConcept.md")))

            # 2. When min_refs threshold is met (e.g. min_refs=1 or simulated >= 2)
            res_tier1 = link_librarian.create_ghost_link_stub(
                target_name="KnownEntity",
                source_path="Notes/Source.md",
                context_excerpt="A recurring entity in the vault.",
                vault_root=tmp_vault,
                min_refs=0,  # Force Tier 1 creation
            )
            self.assertEqual(res_tier1["status"], "created_stub")
            stub_file = os.path.join(tmp_vault, "KnownEntity.md")
            self.assertTrue(os.path.exists(stub_file))
            with open(stub_file, encoding="utf-8") as f:
                stub_content = f.read()
            self.assertIn("[!ABSTRACT]", stub_content)
            self.assertIn("Linked from [[Source]]", stub_content)


if __name__ == "__main__":
    unittest.main()
