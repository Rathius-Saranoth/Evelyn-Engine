# test_search_by_tag.py
# date created: 2026-09-30
# date modified: 2026-09-30 18:29:09
# tags: #[test, #search, #taxonomy, #tools, #evelyn]

"""Unit tests for the model-facing search_by_tag tool and intent heuristics."""

import os
import sys
import unittest

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
tools_dir = os.path.join(repo_root, "Evelyn/tools")
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
if tools_dir not in sys.path:
    sys.path.insert(0, tools_dir)

import evelyn_config as cfg
from Evelyn.tools import memory_db, taxonomy_db, vault_db
from Evelyn.tools.evelyn_tools import (
    MODEL_TOOL_DEFINITIONS,
    TOOL_FUNCTIONS,
    TOOL_THINK_EFFORT,
    get_active_tools,
    search_by_tag,
)


class TestSearchByTag(unittest.TestCase):
    """Test suite for search_by_tag tool and its integration with taxonomy."""

    def setUp(self):
        """Seed hermetic test data into sandboxed test databases."""
        vault_db.init_db()
        memory_db.init_db()

        # Seed master tags
        for term in ("ttrpg", "campaign", "exercise", "fitness", "nutrition"):
            taxonomy_db.upsert_master_tag(term, category="test")

        # Seed alias
        taxonomy_db.record_alias("dnd", "ttrpg")
        taxonomy_db.invalidate_alias_cache()

        # Seed relation
        taxonomy_db.record_relation("ttrpg", "campaign", kind="related")

        import time
        # Seed vault note
        vault_db.upsert_document(
            path="Campaigns/Root.md",
            title="Root of the Problem",
            mtime=time.time(),
            tags="ttrpg, campaign",
            gist="A tabletop adventure log in Blackclaw Mountain.",
        )

        # Seed memory entry
        memory_db.insert_entry(
            category="Cat08-U",
            subject=cfg.USER_NAME,
            observation="Enjoys participating in tabletop roleplaying sessions.",
            tags="ttrpg",
            date="2026-09-30",
        )

        # Seed procedure
        memory_db.insert_procedure(
            trigger_pattern="When preparing a campaign adventure",
            steps="1. Review encounter tables. 2. Verify NPC tags.",
            tags="ttrpg, campaign",
            suggested_tools="search_by_tag",
        )

    def test_tool_registration(self):
        """Verify search_by_tag is properly registered across registries."""
        self.assertIn("search_by_tag", TOOL_FUNCTIONS)
        self.assertEqual(TOOL_FUNCTIONS["search_by_tag"], search_by_tag)
        self.assertEqual(TOOL_THINK_EFFORT.get("search_by_tag"), "low")

        model_tool_names = [
            t.get("function", {}).get("name") for t in MODEL_TOOL_DEFINITIONS
        ]
        self.assertIn("search_by_tag", model_tool_names)

        # Verify schema parameters
        tool_def = next(
            t for t in MODEL_TOOL_DEFINITIONS if t.get("function", {}).get("name") == "search_by_tag"
        )
        params = tool_def["function"]["parameters"]["properties"]
        self.assertIn("tags", params)
        self.assertIn("target", params)
        self.assertIn("match_all", params)
        self.assertIn("limit", params)

    def test_intent_heuristic_activation(self):
        """Verify regex heuristics activate search_by_tag on tag-filtering prompts."""
        prompts = [
            "Can you find notes tagged with exercise?",
            "Show me memories tagged as ttrpg",
            "Search by tag nutrition and health",
            "List procedures tagged with workflow",
            "Filter notes by tags",
        ]
        for prompt in prompts:
            active = get_active_tools(user_message=prompt)
            active_names = [t["function"]["name"] for t in active]
            self.assertIn(
                "search_by_tag",
                active_names,
                f"search_by_tag should be activated for prompt: '{prompt}'",
            )

    def test_empty_query_handling(self):
        """Verify search_by_tag returns a descriptive error when no tags are provided."""
        res = search_by_tag()
        self.assertIn("Error: search_by_tag called without any tags", res)

        res2 = search_by_tag(tags="   ")
        self.assertIn("Error: search_by_tag called without any tags", res2)

    def test_alias_canonicalization(self):
        """Verify aliases (like 'dnd' -> 'ttrpg') are canonicalized with explanatory notes."""
        res = search_by_tag(tags=["dnd"], limit=2)
        self.assertIn("Mapped alias 'dnd' → canonical term 'ttrpg'", res)
        self.assertIn("matching tag(s): 'ttrpg'", res)
        self.assertIn("Root of the Problem", res)

    def test_target_filtering_vault(self):
        """Verify target='vault' restricts results to vault notes only."""
        res = search_by_tag(tags=["ttrpg"], target="vault", limit=3)
        self.assertIn("target='vault'", res)
        self.assertIn("### 📄 Obsidian Vault Notes", res)
        self.assertNotIn("### 🧠 Long-Term Memory Facts", res)
        self.assertNotIn("### ⚙️ Operational Procedures", res)

    def test_target_filtering_memory(self):
        """Verify target='memory' restricts results to memory facts only."""
        res = search_by_tag(tags=["ttrpg"], target="memory", limit=3)
        self.assertIn("target='memory'", res)
        self.assertIn("### 🧠 Long-Term Memory Facts", res)
        self.assertNotIn("### 📄 Obsidian Vault Notes", res)
        self.assertNotIn("### ⚙️ Operational Procedures", res)

    def test_target_filtering_procedures(self):
        """Verify target='procedures' restricts results to operational procedures only."""
        res = search_by_tag(tags=["ttrpg"], target="procedures", limit=3)
        self.assertIn("target='procedures'", res)
        self.assertIn("### ⚙️ Operational Procedures", res)
        self.assertNotIn("### 📄 Obsidian Vault Notes", res)
        self.assertNotIn("### 🧠 Long-Term Memory Facts", res)

    def test_related_taxonomy_concepts_surface(self):
        """Verify related terms from master_tag_related are surfaced in the result footer."""
        res = search_by_tag(tags=["ttrpg"], limit=2)
        self.assertIn("💡 **Related Taxonomy Concepts**: campaign", res)

    def test_multi_tag_match_modes(self):
        """Verify match_all=True (intersection) and match_all=False (union) execute cleanly."""
        res_all = search_by_tag(tags=["ttrpg", "campaign"], match_all=True, limit=5)
        self.assertIn("match=ALL", res_all)
        self.assertIn("Found 2 item(s)", res_all)  # Vault note + Procedure

        res_any = search_by_tag(tags=["ttrpg", "nutrition"], match_all=False, limit=5)
        self.assertIn("match=ANY", res_any)
        self.assertIn("Found 3 item(s)", res_any)  # Vault note + Memory + Procedure


if __name__ == "__main__":
    unittest.main()
