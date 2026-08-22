# test_update_frontmatter.py
# date created: 2026-08-21 21:05:00
# date modified: 2026-08-21 21:04:14
# tags: #test, #frontmatter, #pytest, #utility

import os
import tempfile
import subprocess
import pytest
from scripts.update_frontmatter import update_file_frontmatter, SUPPORTED_EXTENSIONS, YAML_EXTENSIONS, COMMENT_EXTENSIONS

def test_unsupported_xml_file():
    """Verify that XML files are completely untouched by frontmatter updater."""
    xml_content = '<?xml version="1.0" encoding="UTF-8"?>\n<root><item id="1">test</item></root>\n'
    with tempfile.NamedTemporaryFile(suffix=".xml", mode="w", delete=False) as f:
        f.write(xml_content)
        path = f.name

    try:
        updated = update_file_frontmatter(path)
        assert updated is False
        with open(path, "r") as f:
            assert f.read() == xml_content
    finally:
        os.remove(path)

def test_unsupported_html_file():
    """Verify that HTML files are completely untouched by frontmatter updater."""
    html_content = '<!DOCTYPE html>\n<html>\n<head><title>Test</title></head>\n<body><h1>Hello</h1></body>\n</html>\n'
    with tempfile.NamedTemporaryFile(suffix=".html", mode="w", delete=False) as f:
        f.write(html_content)
        path = f.name

    try:
        updated = update_file_frontmatter(path)
        assert updated is False
        with open(path, "r") as f:
            assert f.read() == html_content
    finally:
        os.remove(path)

def test_unsupported_json_css_files():
    """Verify that JSON and CSS files are ignored."""
    json_content = '{\n  "key": "value",\n  "count": 42\n}\n'
    css_content = 'body {\n  background: #000;\n  color: #fff;\n}\n'
    
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f_json, \
         tempfile.NamedTemporaryFile(suffix=".css", mode="w", delete=False) as f_css:
        f_json.write(json_content)
        f_css.write(css_content)
        path_json = f_json.name
        path_css = f_css.name

    try:
        assert update_file_frontmatter(path_json) is False
        assert update_file_frontmatter(path_css) is False
        with open(path_json, "r") as f:
            assert f.read() == json_content
        with open(path_css, "r") as f:
            assert f.read() == css_content
    finally:
        os.remove(path_json)
        os.remove(path_css)

def test_supported_markdown_file():
    """Verify that Markdown files have YAML frontmatter properly injected."""
    md_content = "# Hello World\n\nThis is a markdown document.\n"
    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
        f.write(md_content)
        path = f.name

    try:
        updated = update_file_frontmatter(path)
        assert updated is True
        with open(path, "r") as f:
            result = f.read()
        assert result.startswith("---")
        assert f"title: {os.path.basename(path)}" in result
        assert "date created:" in result
        assert "date modified:" in result
        assert "tags: " in result
        assert "# Hello World" in result
    finally:
        os.remove(path)

def test_supported_python_file():
    """Verify that Python files have comment headers properly injected."""
    py_content = "def test_func():\n    return 42\n"
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(py_content)
        path = f.name

    try:
        updated = update_file_frontmatter(path)
        assert updated is True
        with open(path, "r") as f:
            result = f.read()
        assert result.startswith(f"# {os.path.basename(path)}")
        assert "# date created:" in result
        assert "# date modified:" in result
        assert "def test_func():" in result
    finally:
        os.remove(path)

def test_cli_mixed_files():
    """Verify CLI behavior when invoked with multiple mixed files."""
    xml_content = '<data><test/></data>'
    md_content = '# Test Markdown'
    
    with tempfile.NamedTemporaryFile(suffix=".xml", mode="w", delete=False) as f_xml, \
         tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f_md:
        f_xml.write(xml_content)
        f_md.write(md_content)
        path_xml = f_xml.name
        path_md = f_md.name

    try:
        cmd = [
            "/home/rathius/evelyn/venv/bin/python",
            "scripts/update_frontmatter.py",
            path_xml,
            path_md
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode == 0
        
        # XML untouched
        with open(path_xml, "r") as f:
            assert f.read() == xml_content
            
        # Markdown updated
        with open(path_md, "r") as f:
            assert f.read().startswith("---")
    finally:
        os.remove(path_xml)
        os.remove(path_md)
