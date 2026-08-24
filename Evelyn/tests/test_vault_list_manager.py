# test_vault_list_manager.py
# date created: 2026-08-23
# tags: #test, #vault, #lists, #groceries, #checklists, #tools

"""Unit tests for vault_list_manager.py and manage_vault_list tool."""

import os
import tempfile
import unittest

import evelyn_config as cfg
from Evelyn.tools import evelyn_tools, vault_list_manager


class TestVaultListManager(unittest.TestCase):
    """Test suite for Obsidian Vault list and checklist management."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.orig_lists_dir = getattr(cfg, "LISTS_DIR", None)
        cfg.LISTS_DIR = os.path.join(self.temp_dir.name, "Lists")
        os.makedirs(cfg.LISTS_DIR, exist_ok=True)

    def tearDown(self):
        if self.orig_lists_dir:
            cfg.LISTS_DIR = self.orig_lists_dir
        self.temp_dir.cleanup()

    def test_item_formatting_and_parsing(self):
        """Test item-first formatting and structured parsing."""
        # Item with qty and unit
        line_1 = vault_list_manager.format_item_line("Whole Milk", quantity=1, unit="gal")
        self.assertEqual(line_1, "- [ ] Whole Milk (1 gal)")

        parsed_1 = vault_list_manager.parse_item_line(line_1)
        self.assertIsNotNone(parsed_1)
        self.assertEqual(parsed_1["name"], "Whole Milk")
        self.assertEqual(parsed_1["quantity"], 1)
        self.assertEqual(parsed_1["unit"], "gal")
        self.assertEqual(parsed_1["status"], "pending")

        # Item with multiplier
        line_2 = vault_list_manager.format_item_line("Greek Yogurt", quantity=2)
        self.assertEqual(line_2, "- [ ] Greek Yogurt (2x)")
        parsed_2 = vault_list_manager.parse_item_line(line_2)
        self.assertEqual(parsed_2["quantity"], 2)
        self.assertEqual(parsed_2["unit"], "")

        # Item with no quantity
        line_3 = vault_list_manager.format_item_line("Olive Oil")
        self.assertEqual(line_3, "- [ ] Olive Oil")
        parsed_3 = vault_list_manager.parse_item_line(line_3)
        self.assertEqual(parsed_3["name"], "Olive Oil")
        self.assertIsNone(parsed_3["quantity"])

        # Completed item
        line_4 = "- [x] Honeycrisp Apples (4 count)"
        parsed_4 = vault_list_manager.parse_item_line(line_4)
        self.assertEqual(parsed_4["status"], "completed")
        self.assertEqual(parsed_4["name"], "Honeycrisp Apples")
        self.assertEqual(parsed_4["quantity"], 4)
        self.assertEqual(parsed_4["unit"], "count")

    def test_ensure_list_exists_and_groceries_template(self):
        """Test template instantiation for Groceries."""
        path = vault_list_manager.ensure_list_exists("Groceries")
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("title: Groceries", content)
        self.assertIn("## Produce", content)
        self.assertIn("## Dairy & Refrigerated", content)

    def test_add_items_with_category_routing(self):
        """Test adding items categorized under matching headers."""
        items = [
            {"name": "Honeycrisp Apples", "category": "Produce", "quantity": 4, "unit": "count"},
            {"name": "Whole Milk", "category": "Dairy & Refrigerated", "quantity": 1, "unit": "gal"},
            {"name": "Olive Oil", "category": "Pantry & Dry Goods", "quantity": 1, "unit": "bottle"},
        ]
        res = vault_list_manager.add_to_list(name="Groceries", items=items)
        self.assertEqual(res["status"], "success")
        self.assertEqual(len(res["added"]), 3)

        read_res = vault_list_manager.read_list("Groceries")
        self.assertEqual(read_res["total_pending"], 3)
        self.assertEqual(read_res["total_completed"], 0)
        self.assertIn("Honeycrisp Apples (4 count)", read_res["summary"])
        self.assertIn("Whole Milk (1 gal)", read_res["summary"])
        self.assertIn("Olive Oil (1 bottle)", read_res["summary"])

    def test_quantity_incrementing(self):
        """Test that adding existing item increments quantity rather than creating duplicates."""
        vault_list_manager.add_to_list(
            name="Groceries",
            items=[{"name": "Whole Milk", "category": "Dairy", "quantity": 1, "unit": "gal"}],
        )

        # Add another gallon
        inc_res = vault_list_manager.add_to_list(
            name="Groceries",
            items=[{"name": "Whole Milk", "category": "Dairy", "quantity": 1, "unit": "gal"}],
        )
        self.assertEqual(inc_res["status"], "success")
        self.assertEqual(len(inc_res["updated"]), 1)

        read_res = vault_list_manager.read_list("Groceries")
        self.assertEqual(read_res["total_pending"], 1)
        self.assertIn("Whole Milk (2 gal)", read_res["summary"])

    def test_toggle_and_clear_completed(self):
        """Test checking items off, unchecking, and clearing completed items."""
        vault_list_manager.add_to_list(
            name="Groceries",
            items=[
                {"name": "Spinach", "category": "Produce"},
                {"name": "Oat Milk", "category": "Dairy & Refrigerated", "quantity": 1, "unit": "gal"},
                {"name": "Bread", "category": "Bakery"},
            ],
        )

        # Check off Oat Milk
        chk_res = vault_list_manager.toggle_list_items(name="Groceries", items=["oat milk"], completed=True)
        self.assertEqual(chk_res["status"], "success")

        read_1 = vault_list_manager.read_list("Groceries")
        self.assertEqual(read_1["total_pending"], 2)
        self.assertEqual(read_1["total_completed"], 1)
        self.assertIn("- [x] Oat Milk (1 gal)", read_1["summary"])

        # Uncheck Oat Milk
        unchk_res = vault_list_manager.toggle_list_items(name="Groceries", items=["oat milk"], completed=False)
        self.assertEqual(unchk_res["status"], "success")
        read_2 = vault_list_manager.read_list("Groceries")
        self.assertEqual(read_2["total_pending"], 3)
        self.assertEqual(read_2["total_completed"], 0)

        # Re-check Bread and clear completed
        vault_list_manager.toggle_list_items(name="Groceries", items=["bread"], completed=True)
        clear_res = vault_list_manager.clear_completed_items("Groceries")
        self.assertEqual(clear_res["cleared_count"], 1)

        read_3 = vault_list_manager.read_list("Groceries")
        self.assertEqual(read_3["total_pending"], 2)
        self.assertEqual(read_3["total_completed"], 0)
        self.assertNotIn("Bread", read_3["summary"])

    def test_remove_item(self):
        """Test explicit removal of items."""
        vault_list_manager.add_to_list(name="Packing", items=["Passport", "Charger", "Toothbrush"])
        rem_res = vault_list_manager.remove_from_list(name="Packing", items=["Charger"])
        self.assertEqual(rem_res["status"], "success")

        read_res = vault_list_manager.read_list("Packing")
        self.assertEqual(read_res["total_pending"], 2)
        self.assertNotIn("Charger", read_res["summary"])

    def test_evelyn_tools_manage_vault_list(self):
        """Test model tool wrapper manage_vault_list."""
        # Add items
        add_res = evelyn_tools.manage_vault_list(
            name="Groceries",
            action="add",
            items=[
                {"name": "Honeycrisp Apples", "category": "Produce", "quantity": 3, "unit": "count"},
                {"name": "Shredded Mini Wheats", "category": "Pantry", "quantity": 1, "unit": "box"},
            ],
        )
        self.assertIn("Added 2 item(s)", add_res)

        # Read list
        read_res = evelyn_tools.manage_vault_list(name="Groceries", action="read")
        self.assertIn("Honeycrisp Apples (3 count)", read_res)
        self.assertIn("Shredded Mini Wheats (1 box)", read_res)

        # Check item
        chk_res = evelyn_tools.manage_vault_list(name="Groceries", action="check", items=["apples"])
        self.assertIn("Checked off", chk_res)

        # List all lists
        lists_res = evelyn_tools.manage_vault_list(action="list_all")
        self.assertIn("Groceries", lists_res)


if __name__ == "__main__":
    unittest.main()
