# test_dynamic_discovery_and_paged_docs.py
# date created: 2026-09-13 13:55:00
# date modified: 2026-09-13 14:00:38
# tags: #fuzzy, #discovery, #scratchpad, #pagination, #upload, #epistemic

"""Unit tests for Dynamic Tool Discovery, Paged Document Reading, and Chat Uploads.

Validates:
1. Token-fuzzy scoring with word transposition tolerance and digit discrepancy guards.
2. Paged read_file operations with start_line and end_line precedence.
3. Algorithmic read_document_scratchpad heading outlining without LLM deadlocks.
4. search_available_tools discovery and dynamic activation into active_tool_map.
5. Server text and PDF extraction with scanned document detection.
6. Positive epistemic grounding in system prompt.
"""

from unittest.mock import MagicMock, patch

import evelyn_config as cfg
from Evelyn.tools import evelyn_tools, terminal_agent
from Evelyn.tools.string_utils import calculate_token_fuzzy_score


def test_token_fuzzy_score_transposition_and_typo():
    """Verify that calculate_token_fuzzy_score tolerates token permutations and minor typos."""
    # 1. Exact match
    assert calculate_token_fuzzy_score("GIS Technician", "GIS Technician") == 1.0

    # 2. Out-of-order tokens
    permuted_score = calculate_token_fuzzy_score("GIS Technician Tasks Overview", "Tasks Overview GIS Technician")
    assert permuted_score >= 0.95

    # 3. Minor typo
    typo_score = calculate_token_fuzzy_score("GIS Technitian Tasks", "GIS Technician Tasks")
    assert typo_score >= 0.85

    # 4. Completely unrelated strings
    low_score = calculate_token_fuzzy_score("Quantum Mechanics", "Grocery Shopping List")
    assert low_score < 0.3


def test_token_fuzzy_score_digit_discrepancy_guard():
    """Verify that mismatched dates or numbers prevent false positive auto-resolution."""
    # High lexical similarity but differing dates
    date_score = calculate_token_fuzzy_score("Meeting Notes 2026-03-01", "Meeting Notes 2026-03-02")
    assert date_score <= 0.50

    # High lexical similarity but differing version / chapter numbers
    num_score = calculate_token_fuzzy_score("Protocol Spec v1", "Protocol Spec v2")
    assert num_score <= 0.50

    # Matching numbers retain high score
    match_num_score = calculate_token_fuzzy_score("Protocol Spec v2", "Protocol Spec v2")
    assert match_num_score == 1.0


def test_read_file_start_and_end_line(tmp_path):
    """Verify read_file slicing with start_line and end_line."""
    test_file = tmp_path / "sample_doc.md"
    lines = [f"Line {i}: Content of line {i}\n" for i in range(1, 21)]
    test_file.write_text("".join(lines), encoding="utf-8")

    with patch.object(cfg, "TERMINAL_ALLOWED_PATHS", [str(tmp_path)]):
        # Read lines 5 to 10
        res = terminal_agent.read_file(str(test_file), start_line=5, end_line=10)
        assert "Line 5:" in res
        assert "Line 10:" in res
        assert "Line 4:" not in res
        assert "Line 11:" not in res
        assert "Showing lines 5–10 of 20" in res

        # Read starting at line 18 with max_lines default -> reaches end without truncation hint
        res_end = terminal_agent.read_file(str(test_file), start_line=18, max_lines=5)
        assert "Line 18:" in res_end
        assert "Line 20:" in res_end
        assert "Showing lines 18–20 of 20" in res_end


def test_read_document_scratchpad(tmp_path):
    """Verify deterministic algorithmic chunking and outline extraction without sub-LLM calls."""
    test_file = tmp_path / "project_guide.md"
    content = (
        "# Overview\nThis is the project overview.\n"
        "## Installation\nStep 1: Install dependencies.\nStep 2: Run setup.\n"
        "## Architecture\nModule A interacts with Module B.\n"
        "## Deployment\nDeploy to cloud container.\n"
    )
    test_file.write_text(content, encoding="utf-8")

    with patch.object(cfg, "TERMINAL_ALLOWED_PATHS", [str(tmp_path)]):
        res = evelyn_tools.read_document_scratchpad(str(test_file), max_sections=3)
        assert "Document Scratchpad:" in res
        assert "Table of Contents & Section Map:" in res
        assert "Overview" in res
        assert "Installation" in res
        assert "Architecture" in res
        assert "Preview (First 3 sections):" in res
        assert "Tip: To read subsequent sections, call read_file" in res


def test_search_available_tools_discovery():
    """Verify that search_available_tools finds tools by domain and formats activation notices."""
    # Search for file operations
    res_file = evelyn_tools.search_available_tools(query="read file")
    assert "[System: Discovered" in res_file
    assert "read_file" in res_file
    assert "start_line" in res_file
    assert "Activated for Round N+1:" in res_file

    # Search for research tools
    res_research = evelyn_tools.search_available_tools(query="deep research")
    assert "start_research" in res_research or "list_research_tasks" in res_research

    # Search for non-existent tool
    res_none = evelyn_tools.search_available_tools(query="quantum teleportation warp drive")
    assert "No tools found matching query" in res_none


def test_server_document_extraction_and_scan_detection():
    """Verify text extraction and scanned PDF detection helper."""
    import evelyn_server

    # Plain text extraction
    raw_text = b"Hello world! This is a test script.\ndef foo(): pass\n"
    txt, d_type = evelyn_server._extract_document_text_sync(raw_text, "test.py")
    assert d_type == "py"
    assert "Hello world!" in txt

    # Simulated scanned PDF (< 50 chars)
    scanned_bytes = b"%PDF-1.4 empty simulated"
    with patch("pymupdf.open") as mock_pymupdf:
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_page.get_text.return_value = "   "
        mock_doc.__len__.return_value = 1
        mock_doc.__getitem__.return_value = mock_page
        mock_pymupdf.return_value = mock_doc

        pdf_txt, pdf_type = evelyn_server._extract_document_text_sync(scanned_bytes, "scan.pdf")
        assert pdf_type == "pdf"
        assert "[Scanned/Image PDF: No extractable text detected" in pdf_txt


def test_system_prompt_positive_epistemic_grounding():
    """Verify that load_system_prompt contains the positive epistemic grounding directive."""
    import evelyn_server

    sys_prompt = evelyn_server.load_system_prompt()
    assert "<system_telemetry_directives>" in sys_prompt
    assert "<uploaded_document>" in sys_prompt
    assert "Ground observations strictly in facts explicitly stated in the current turn" in sys_prompt
    assert "without attributing unverified activities, physical state changes, or routine assumptions" in sys_prompt
