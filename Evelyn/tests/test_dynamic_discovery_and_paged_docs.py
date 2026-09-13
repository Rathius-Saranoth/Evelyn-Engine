# test_dynamic_discovery_and_paged_docs.py
# date created: 2026-09-13 13:55:00
# date modified: 2026-09-13 15:57:25
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
        mock_page.get_label.return_value = ""
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
    assert "<user_attachments_directive>" in sys_prompt
    assert "<proactive_tool_discovery>" in sys_prompt
    # Verify uploaded_document is decoupled from system_telemetry_directives
    telemetry_section = sys_prompt.split("<system_telemetry_directives>")[1].split("</system_telemetry_directives>")[0]
    assert "`<uploaded_document>`" not in telemetry_section
    assert "Sequential Execution Constraint" in sys_prompt
    assert "Ground observations strictly in facts explicitly stated in the current turn" in sys_prompt
    assert "without attributing unverified activities, physical state changes, or routine assumptions" in sys_prompt


def test_chat_upload_dynamic_context_budget():
    """Verify get_chat_upload_max_chars scales purely with NUM_CTX and file type multipliers."""
    with patch.object(cfg, "NUM_CTX", 16384), patch.object(cfg, "CHAT_UPLOAD_CONTEXT_RATIO", 0.35):
        code_budget = cfg.get_chat_upload_max_chars("py")
        assert code_budget == int(16384 * 0.35 * 2.5)

        prose_budget = cfg.get_chat_upload_max_chars("pdf")
        assert prose_budget == int(16384 * 0.35 * 4.0)
        assert prose_budget > code_budget

    with patch.object(cfg, "NUM_CTX", 32768), patch.object(cfg, "CHAT_UPLOAD_CONTEXT_RATIO", 0.35):
        scaled_code = cfg.get_chat_upload_max_chars("json")
        assert scaled_code == int(32768 * 0.35 * 2.5)
        assert scaled_code == code_budget * 2


def test_core_tool_names_promotion():
    """Verify read_file is promoted to CORE_TOOL_NAMES and excluded from intent patterns."""
    assert "read_file" in cfg.CORE_TOOL_NAMES
    assert "read_file" not in cfg.SPECIALIST_TOOL_INTENT_PATTERNS


def test_read_file_page_parameter(tmp_path):
    """Verify read_file with page parameter on delimited text and simulated PDF."""
    delimited_file = tmp_path / "extracted_manual.txt"
    content = (
        "--- [PDF Page 1] ---\nFirst page content.\n\n"
        "--- [PDF Page 2 | Folio: ii] ---\nSecond page preface.\n\n"
        "--- [PDF Page 3] ---\nThird page chapter 1.\n"
    )
    delimited_file.write_text(content, encoding="utf-8")

    with patch.object(cfg, "TERMINAL_ALLOWED_PATHS", [str(tmp_path)]):
        # Read page 2
        res_p2 = terminal_agent.read_file(str(delimited_file), page=2)
        assert "Second page preface." in res_p2
        assert "First page content." not in res_p2
        assert "Third page chapter 1." not in res_p2
        assert "Extracted Page 2 of 3" in res_p2

        # Page out of range
        res_p99 = terminal_agent.read_file(str(delimited_file), page=99)
        assert "Error: Page 99 not found" in res_p99


def test_active_tools_followup_turn_retention():
    """Verify read_file remains active on ambiguous follow-up turns without file keywords."""
    from Evelyn.tools.evelyn_tools import get_active_tools

    tools = get_active_tools("It's gotta be a context limit... Start at line 87 this time.")
    tool_names = [t["function"]["name"] for t in tools]
    assert "read_file" in tool_names


def test_real_multipage_pdf_generation_and_reading(tmp_path):
    """Verify real multi-page PDF generation, folio extraction, and single-page reading."""
    import pymupdf

    pdf_file = tmp_path / "manual.pdf"
    doc = pymupdf.open()
    for i in range(1, 6):
        p = doc.new_page()
        p.insert_text((50, 72), f"Content for manual page {i}")
    doc.set_page_labels([
        {"startpage": 0, "prefix": "intro-", "style": "r"},
        {"startpage": 2, "prefix": "body-", "style": "D"},
    ])
    doc.save(str(pdf_file))
    doc.close()

    with patch.object(cfg, "TERMINAL_ALLOWED_PATHS", [str(tmp_path)]):
        # 1. Default read (page 1)
        res_p1 = terminal_agent.read_file(str(pdf_file))
        assert "PDF Page 1 of 5 | Folio: intro-i" in res_p1
        assert "Content for manual page 1" in res_p1

        # 2. Page 2 (folio intro-ii)
        res_p2 = terminal_agent.read_file(str(pdf_file), page=2)
        assert "PDF Page 2 of 5 | Folio: intro-ii" in res_p2
        assert "Content for manual page 2" in res_p2

        # 3. Page 3 (folio body-1)
        res_p3 = terminal_agent.read_file(str(pdf_file), page=3)
        assert "PDF Page 3 of 5 | Folio: body-1" in res_p3
        assert "Content for manual page 3" in res_p3

        # 4. Out of range
        res_err = terminal_agent.read_file(str(pdf_file), page=10)
        assert "Error: Requested page 10 is out of range" in res_err
