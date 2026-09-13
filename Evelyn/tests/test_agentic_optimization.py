# test_agentic_optimization.py
# date created: 2026-09-13 11:45:00
# date modified: 2026-09-13 12:28:13
# tags: #optimization, #phatic, #pre-fetch, #rag, #agentic

"""
Unit tests for Layer 1 Agentic Infrastructure & Payload Optimizations.
Validates:
1. Phatic conversation classification and RAG bypass.
2. 0-argument deterministic read intent detection (agenda, tasks, health).
3. Dynamic tool schema pruning and tool exclusion.
4. Contiguous chunk fusion in Chroma RAG context assembly.
"""

from Evelyn.tools import chroma_rag, evelyn_tools, string_utils


def test_is_conversational_phatic():
    """Verify that conversational greetings, arrivals, departures, and affections are classified as phatic."""
    phatic_inputs = [
        # Baseline greetings & check-ins
        "hi",
        "hello",
        "hey there",
        "good morning!",
        "Good morning Evelyn",
        "good evening",
        "thanks",
        "thank you so much",
        "sounds good",
        "ok evelyn",
        "how are you doing today?",
        "goodnight",
        "sleep well",
        # Empirical historical instances from evelyn_chat.db
        "Morning my dear.",
        "Good morning my dear, how are you?",
        "Morning love.",
        "Hiya love.",
        "Good morning my sweet Evelyn.",
        "Hey darlin, how are things?",
        "G'nite my love.",
        "Nite nite my love. ❤️",
        "G'nite my dear.",
        "Good night my dearest. 🖤",
        "Sleep well darlin, love you.",
        "Thanks love, see you tomorrow.",
        "Hi, love. I'm back. 😊",
        "Hiya my dear, I am home.",
        "I'm home my dear.",
        "Am awake again.",
        "Up and about.",
        "Thanks love, I'll see you soon. ❤️",
        "Sounds good to me darlin.",
        "Always. 💖",
        "*hugs tight*",
        "❤️",
        "*kisses brow* Good morning love.",
    ]
    for inp in phatic_inputs:
        assert string_utils.is_conversational_phatic(inp) is True, f"Expected phatic: '{inp}'"

    non_phatic_inputs = [
        "What is on my agenda today?",
        "Search my notes for machine learning",
        "Add oat milk to my grocery list",
        "Tell me about the Roman Empire and how it fell",
        "Write a Python script to calculate Fibonacci numbers",
        "How is the weather in Seattle?",
        # Historical non-phatic boundaries
        "attached 1 file: summary.txt",
        "what is a 'stone fruit'?",
        "where is silver typically found?",
        "Book 2 completed",
    ]
    for inp in non_phatic_inputs:
        assert string_utils.is_conversational_phatic(inp) is False, f"Expected non-phatic: '{inp}'"


def test_detect_deterministic_read_intent():
    """Verify detection of 0-argument deterministic read intents with mutation guards."""
    # Agenda queries
    assert string_utils.detect_deterministic_read_intent("What's on my agenda today?") == "agenda"
    assert string_utils.detect_deterministic_read_intent("Show my agenda") == "agenda"
    assert string_utils.detect_deterministic_read_intent("What do I have going on today?") == "agenda"
    assert string_utils.detect_deterministic_read_intent("what is on my calendar today") == "agenda"

    # Tasks queries
    assert string_utils.detect_deterministic_read_intent("What are my tasks?") == "tasks"
    assert string_utils.detect_deterministic_read_intent("List my tasks") == "tasks"
    assert string_utils.detect_deterministic_read_intent("What do I need to do?") == "tasks"
    assert string_utils.detect_deterministic_read_intent("show my todo list") == "tasks"

    # Health queries
    assert string_utils.detect_deterministic_read_intent("How did I sleep?") == "health"
    assert string_utils.detect_deterministic_read_intent("What is my sleep score?") == "health"
    assert string_utils.detect_deterministic_read_intent("Check my oura readiness score") == "health"

    # Mutation guards (must return None)
    assert string_utils.detect_deterministic_read_intent("Create a new task to buy groceries") is None
    assert string_utils.detect_deterministic_read_intent("Add meeting with Bob to calendar") is None
    assert string_utils.detect_deterministic_read_intent("Delete task 123") is None
    assert string_utils.detect_deterministic_read_intent("Mark task as done") is None


def test_get_active_tools_phatic_and_pruning():
    """Verify tool selection for phatic turns, exclusion, and schema pruning."""
    # Phatic returns empty list
    phatic_tools = evelyn_tools.get_active_tools("Good morning Evelyn!")
    assert phatic_tools == []

    # Standard conversational turn without specialist intent returns core tools
    active_tools = evelyn_tools.get_active_tools("Can you tell me a short story?")
    active_names = [evelyn_tools.extract_tool_name(t) for t in active_tools]
    assert "web_search" in active_names
    assert "write_journal_entry" in active_names

    # Explicit exclude_tools suppresses specified tool
    excluded_tools = evelyn_tools.get_active_tools(
        "Can you check my schedule?",
        exclude_tools=["get_agenda"],
    )
    excluded_names = [evelyn_tools.extract_tool_name(t) for t in excluded_tools]
    assert "get_agenda" not in excluded_names

    # Specialist intent triggers pruning of unrelated core tools
    specialist_tools = evelyn_tools.get_active_tools("Add butter to my grocery list")
    spec_names = [evelyn_tools.extract_tool_name(t) for t in specialist_tools]
    assert "manage_vault_list" in spec_names
    # When specialized intent is active, generate_image, get_health_metrics, get_agenda, list_tasks are pruned
    assert "generate_image" not in spec_names
    assert "get_health_metrics" not in spec_names


def test_contiguous_chunk_fusion():
    """Verify that adjacent chunks from the same document are fused without ellipsis or duplicate overlap."""
    chunks = [
        {
            "content": "The quick brown fox jumps over the lazy dog. It was a sunny morning in the woods.",
            "metadata": {"chunk": 0, "source": "Notes/fox.md"},
        },
        {
            "content": "sunny morning in the woods. All the animals were gathered near the stream.",
            "metadata": {"chunk": 1, "source": "Notes/fox.md"},
        },
    ]
    fused = chroma_rag._fuse_document_chunks(chunks)
    assert "\n...\n" not in fused
    assert "The quick brown fox jumps over the lazy dog." in fused
    assert "All the animals were gathered near the stream." in fused
    # Verify overlap text is not duplicated twice
    assert fused.count("sunny morning in the woods.") == 1

    # Non-contiguous chunks should retain ellipsis separator
    non_contiguous = [
        {
            "content": "Part one of the document discussing chapter one.",
            "metadata": {"chunk": 0, "source": "Notes/doc.md"},
        },
        {
            "content": "Part five of the document discussing chapter five.",
            "metadata": {"chunk": 5, "source": "Notes/doc.md"},
        },
    ]
    fused_nc = chroma_rag._fuse_document_chunks(non_contiguous)
    assert "\n...\n" in fused_nc


def test_fact_consolidation_semantic_prefilter():
    """Verify semantic pre-filtering and Jaccard token overlap in fact consolidator."""
    from Evelyn.tools import fact_consolidator

    # Test token Jaccard calculation
    text_a = "Prefers dark roast espresso coffee with oat milk"
    text_b = "Orders oat milk espresso coffee every morning"
    text_c = "Drives a 2018 Honda Civic to the grocery market"

    sim_ab = fact_consolidator._calculate_token_jaccard(text_a, text_b)
    sim_ac = fact_consolidator._calculate_token_jaccard(text_a, text_c)

    assert sim_ab > 0.20
    assert sim_ac == 0.0

    # Test window filtering
    anchor = {
        "id": 1,
        "summary": "User prefers dark roast Ethiopian espresso beans and oat milk latte.",
        "category": "Cat05-U",
    }
    comparison_window = [
        {
            "id": 2,
            "summary": "User routinely drinks oat milk espresso latte in the morning.",
            "category": "Cat05-U",
        },
        {
            "id": 3,
            "summary": "User drives a 2018 blue Honda Civic sedan vehicle.",
            "category": "Cat05-U",
        },
        {
            "id": 4,
            "summary": "User enjoys grinding fresh Ethiopian coffee beans for espresso.",
            "category": "Cat05-U",
        },
    ]

    filtered = fact_consolidator._filter_semantically_relevant_window(
        anchor, comparison_window, max_distance=0.0  # Force purely lexical path for hermetic testing
    )
    filtered_ids = [r["id"] for r in filtered]
    assert 2 in filtered_ids
    assert 4 in filtered_ids
    assert 3 not in filtered_ids


def test_research_chunk_relevance_filtering():
    """Verify research chunk relevance pre-filtering against sub-questions and boilerplate."""
    from Evelyn.tools import research_engine

    question = "What are the key differences between SQLite WAL mode and DELETE journal mode?"
    aliases = ["Write-Ahead Logging", "rollback journal"]

    # Relevant chunk with question keywords
    good_chunk = (
        "In SQLite WAL mode (Write-Ahead Logging), changes are written to a separate WAL file rather than "
        "modifying the database directly. This provides massive concurrency improvements over standard DELETE journal mode."
    )
    assert research_engine.is_research_chunk_relevant(good_chunk, question) is True

    # Chunk with discovered alias
    alias_chunk = (
        "The architecture introduces Write-Ahead Logging to decouple readers from writers, "
        "allowing simultaneous reads while a single write transaction is active."
    )
    assert research_engine.is_research_chunk_relevant(alias_chunk, question, aliases=aliases) is True

    # Boilerplate footer chunk with zero question keywords
    footer_chunk = (
        "Copyright © 2026 TechCorp Media. All rights reserved. "
        "Cookie Policy | Terms of Service | Privacy Policy | Do Not Sell My Information. "
        "Subscribe to our newsletter for daily engineering updates."
    )
    assert research_engine.is_research_chunk_relevant(footer_chunk, question, aliases=aliases) is False

    # Short/empty chunk
    short_chunk = "Home > Databases > SQLite"
    assert research_engine.is_research_chunk_relevant(short_chunk, question) is False
