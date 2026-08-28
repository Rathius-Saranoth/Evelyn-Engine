# test_research_intent.py
# date created: 2026-08-28
# date modified: 2026-08-28 08:46:31
# tags: #research, #intent, #prompts, #classification, #testing

import pytest
from Evelyn.tools.research_prompts import (
    classify_intent_mode,
    is_valid_search_gap,
    build_search_query_prompt,
    is_atomic_query,
)
from Evelyn.tools.research_engine import _truncate_query_fallback


def test_classify_intent_mode_technical_queries():
    technical_cases = [
        "How do I process 5Hz HR data in Python?",
        "Bluetooth Low Energy GATT characteristics sensor stream",
        "Techniques for balancing narrative flow with LLM context window",
        "FastAPI background worker SQLite database setup",
        "Arduino GPIO pinout configuration for I2C OLED display",
        "LangChain buffer memory token compression",
        "Docker container setup for Ollama model server",
    ]
    for q in technical_cases:
        assert classify_intent_mode(q) == "technical", f"Expected 'technical' for: '{q}'"


def test_classify_intent_mode_academic_queries():
    academic_cases = [
        "What are the physiological risks of high heart rate?",
        "Historical consensus on respiratory sinus arrhythmia",
        "Origins of classical republican political theory in Renaissance Italy",
        "Evolutionary benefits of bipedalism in early hominids",
        "Sociological impacts of urbanization in 19th century Europe",
    ]
    for q in academic_cases:
        assert classify_intent_mode(q) == "academic", f"Expected 'academic' for: '{q}'"


def test_classify_intent_mode_with_intent_frame():
    # Ambiguous query, but technical intent frame
    query = "Heart rate variability dynamics"
    intent_frame = "The user wants a Python script to stream Polar H10 BLE sensor data."
    assert classify_intent_mode(query, intent_frame=intent_frame) == "technical"

    # Ambiguous query, but academic intent frame
    query_2 = "Heart rate variability dynamics"
    intent_frame_2 = "Understanding the clinical and physiological mechanisms of parasympathetic autonomic regulation."
    assert classify_intent_mode(query_2, intent_frame=intent_frame_2) == "academic"


def test_is_valid_search_gap_meta_phrases():
    invalid_gaps = [
        "Insufficient evidence collected.",
        "insufficient evidence",
        "more evidence needed",
        "more information needed",
        "need more information",
        "need more info",
        "need more sources",
        "more sources needed",
        "no specific gaps",
        "no specific gaps identified",
        "no gaps identified",
        "none identified",
        "not enough evidence",
        "not enough data",
        "none",
        "n/a",
        "unknown",
        "tbd",
        "",
        "   ",
        "no",
    ]
    for g in invalid_gaps:
        assert is_valid_search_gap(g) is False, f"Expected gap '{g}' to be rejected as invalid meta-text."


def test_is_valid_search_gap_real_concepts():
    valid_gaps = [
        "Polar H10 BLE GATT service UUID",
        "RSA frequency band in Hz",
        "LangGraph checkpointer SQLite schema",
        "FastAPI background task concurrency limits",
        "Vagal tone sympathetic autonomic ratio",
    ]
    for g in valid_gaps:
        assert is_valid_search_gap(g) is True, f"Expected gap '{g}' to be accepted as a valid search concept."


def test_build_search_query_prompt_modes():
    # Technical mode
    tech_prompt = build_search_query_prompt(
        "How to stream Polar H10 heart rate data in Python",
        intent_mode="technical",
        intent_frame="Build a Python Bluetooth BLE reader for Polar H10",
    )
    assert "Target Intent: TECHNICAL / IMPLEMENTATION" in tech_prompt
    assert "FEW-SHOT TECHNICAL EXAMPLES" in tech_prompt
    assert "ACADEMIC / SCHOLARLY CONSENSUS" not in tech_prompt

    # Academic mode
    acad_prompt = build_search_query_prompt(
        "Physiological causes of sinus tachycardia",
        intent_mode="academic",
        intent_frame="Understand medical causes of elevated heart rate",
    )
    assert "Target Intent: ACADEMIC / SCHOLARLY CONSENSUS" in acad_prompt
    assert "FEW-SHOT ACADEMIC EXAMPLES" in acad_prompt
    assert "TECHNICAL / IMPLEMENTATION" not in acad_prompt


def test_truncate_query_fallback_modes():
    academic_query = "An overview of comparative physiological mechanisms between HRV metrics"
    tech_fallback = _truncate_query_fallback(academic_query, max_words=4, intent_mode="technical")
    # In technical mode, academic prefixes ('An overview of', 'comparative') and 'mechanisms'/'physiological' are stripped
    assert "overview" not in tech_fallback.lower()
    assert "comparative" not in tech_fallback.lower()
    assert "physiological" not in tech_fallback.lower()
    assert len(tech_fallback.split()) <= 4
