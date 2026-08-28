# test_frontmatter_utils.py
# date created: 2026-08-28 12:25:00
# date modified: 2026-08-28 12:25:00
# tags: #tests, #frontmatter_utils, #yaml

"""Unit tests for Evelyn.tools.frontmatter_utils."""

import os
import tempfile
import time

from Evelyn.tools.frontmatter_utils import (
    format_yaml_array,
    parse_frontmatter,
    render_frontmatter,
    update_frontmatter_field,
    write_file_with_frontmatter,
)


class TestFrontmatterUtils:
    def test_format_yaml_array(self):
        assert format_yaml_array(None) == "[]"
        assert format_yaml_array([]) == "[]"
        assert format_yaml_array(["tag1", "tag2"]) == "[tag1, tag2]"
        assert format_yaml_array("tag1, tag2, tag1") == "[tag1, tag2]"
        assert format_yaml_array(["has space", "colon:key"]) == '["has space", "colon:key"]'

    def test_parse_frontmatter_strict_bounds(self):
        # Starts on index 0
        content = "---\ntitle: Hello\ntags: [a, b]\n---\n# Body heading\nBody text"
        meta, body = parse_frontmatter(content)
        assert meta["title"] == "Hello"
        assert meta["tags"] == ["a", "b"]
        assert body == "# Body heading\nBody text"

        # Not at index 0 (preceded by spaces/newlines)
        bad_content = "\n---\ntitle: Ignored\n---\nBody"
        b_meta, b_body = parse_frontmatter(bad_content)
        assert b_meta == {}
        assert b_body == bad_content

    def test_parse_frontmatter_multiline_list(self):
        content = "---\ntitle: Multi\ntags:\n  - alpha\n  - beta\n---\nContent"
        meta, body = parse_frontmatter(content)
        assert meta["title"] == "Multi"
        assert meta["tags"] == ["alpha", "beta"]
        assert body == "Content"

    def test_render_frontmatter(self):
        meta = {
            "title": "My Note",
            "tags": ["concept/ai", "pkm"],
            "date created": "2026-08-28 12:00:00",
            "rag_pinned": True,
        }
        rendered = render_frontmatter(meta, body="# Title\nNote body")
        assert rendered.startswith("---")
        assert "tags: [concept/ai, pkm]" in rendered
        assert "rag_pinned: true" in rendered
        assert rendered.endswith("# Title\nNote body")

    def test_update_frontmatter_field_existing_and_new(self):
        initial = "---\ntitle: Old Title\n# comment here\ntags: [t1]\n---\nBody"
        # Update existing
        up1 = update_frontmatter_field(initial, "title", "New Title")
        assert "title: New Title" in up1
        assert "# comment here" in up1  # Comments preserved!
        assert "tags: [t1]" in up1

        # Replace multiline list with single-line flow array
        multiline = "---\ntitle: Note\ntags:\n  - item1\n  - item2\n---\nBody"
        up2 = update_frontmatter_field(multiline, "tags", ["item1", "item2", "item3"])
        assert "tags: [item1, item2, item3]" in up2
        assert "- item1" not in up2

        # Insert new key
        up3 = update_frontmatter_field(initial, "rag_pinned", True)
        assert "rag_pinned: true" in up3

    def test_write_file_with_preserve_mtime(self):
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tf:
            tf.write(b"Initial content")
            tmp_path = tf.name

        try:
            # Set old mtime
            old_time = time.time() - 5000
            os.utime(tmp_path, (old_time, old_time))
            st_before = os.stat(tmp_path).st_mtime

            # Write with preservation
            write_file_with_frontmatter(tmp_path, "Updated content", preserve_mtime=True)
            st_after = os.stat(tmp_path).st_mtime

            assert abs(st_after - st_before) < 1.0
            with open(tmp_path, encoding="utf-8") as f:
                assert f.read() == "Updated content"
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
