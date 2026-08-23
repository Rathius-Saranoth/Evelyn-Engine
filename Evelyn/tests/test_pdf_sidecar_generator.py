# test_pdf_sidecar_generator.py
# date created: 2026-08-22 19:10:00
# tags: #test, #pdf, #sidecar, #normalization, #obsidian

import pytest
from scripts.extract_pdf_library import (
    normalize_book_title,
    segment_concatenated_words,
    generate_sidecar_card,
    Section,
)


def test_segment_concatenated_words():
    """Verify concatenated lowercase words segment accurately into readable phrases."""
    assert segment_concatenated_words("aiandmlforcodersinpytorch") == "ai and ml for coders in pytorch"
    assert segment_concatenated_words("buildingapplicationswithaiagents") == "building applications with ai agents"
    assert segment_concatenated_words("craftingengineeringstrategy") == "crafting engineering strategy"
    assert segment_concatenated_words("developersplaybookforlargelanguagemodelsecurity") == "developers playbook for large language model security"
    assert segment_concatenated_words("llmops") == "llmops"


def test_normalize_book_title_clean_cases():
    """Verify title and subtitle normalization across typical PDF filename conventions."""
    t1, s1 = normalize_book_title("buildingapplicationswithaiagents_designingandimplementingmultiagentsystems.pdf")
    assert t1 == "Building Applications with AI Agents"
    assert s1 == "Designing and Implementing Multi-Agent Systems"

    t2, s2 = normalize_book_title("craftingengineeringstrategy_howthoughtfuldecisionssolvecomplexproblems.pdf")
    assert t2 == "Crafting Engineering Strategy"
    assert s2 == "How Thoughtful Decisions Solve Complex Problems"

    t3, s3 = normalize_book_title("hands-onmachinelearningwithscikit-learnandpytorch.pdf")
    assert "Machine Learning" in t3
    assert "PyTorch" in t3 or "Scikit-Learn" in t3

    t4, s4 = normalize_book_title("llmops.pdf")
    assert t4 == "LLMOps"
    assert s4 == ""


def test_normalize_book_title_with_metadata():
    """Verify embedded PDF metadata title takes precedence when clean."""
    meta = {"title": "Designing Data-Intensive Applications: The Big Ideas Behind Reliable Systems", "author": "Martin Kleppmann"}
    title, sub = normalize_book_title("ddia_messy_name.pdf", doc_metadata=meta)
    assert title == "Designing Data-Intensive Applications"
    assert sub == "The Big Ideas Behind Reliable Systems"


def test_generate_sidecar_card():
    """Verify sidecar index card generates clean frontmatter, embeds, TOC, and semantic links."""
    chapters = [
        Section(title="Introduction to Agents", level=1, content="Agent intro content", page_num=12),
        Section(title="Multi-Agent Protocols", level=1, content="Protocol details", page_num=35),
    ]
    gists = {
        "Introduction to Agents": "Covers foundational architecture of autonomous agents.",
        "Multi-Agent Protocols": "Explores consensus and message passing in agent clusters.",
    }
    sem_neighbors = [
        {"title": "Agent Orchestrator", "similarity": 0.88, "snippet": "Discussion of multi-agent dispatchers."},
        {"title": "Tool Calling Protocol", "similarity": 0.81, "snippet": "Specification for function execution."},
    ]
    entities = [
        {"title": "FastAPI Terminal", "path": "Projects/Terminal.md"},
        {"title": "Chroma RAG", "path": "Projects/RAG.md"},
    ]

    card = generate_sidecar_card(
        title="Building Applications with AI Agents",
        subtitle="Designing and Implementing Multi-Agent Systems",
        author="Michael Albano",
        attachment_rel_path="Attachments/Source Material/AI/buildingapplicationswithaiagents.pdf",
        chapters=chapters,
        gists=gists,
        overview_gist="A comprehensive guide to building resilient multi-agent software architectures.",
        tags=["Tech/AI/Agents", "Tech/Architecture"],
        aliases=["AI Agents Guide"],
        semantic_neighbors=sem_neighbors,
        referenced_entities=entities,
    )

    assert "title: \"Building Applications with AI Agents\"" in card
    assert "subtitle: \"Designing and Implementing Multi-Agent Systems\"" in card
    assert "source: \"[[Attachments/Source Material/AI/buildingapplicationswithaiagents.pdf]]\"" in card
    assert "authors: \"Michael Albano\"" in card
    assert "Tech/AI/Agents" in card
    assert "literature/reference" in card
    assert "![[Attachments/Source Material/AI/buildingapplicationswithaiagents.pdf]]" in card
    assert "Introduction to Agents" in card
    assert "[[Agent Orchestrator]]" in card
    assert "[[FastAPI Terminal]]" in card
