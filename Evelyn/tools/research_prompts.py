# research_prompts.py
# date created: 2026-05-26
# tags: #research, #prompts, #planning, #extraction, #evaluation, #synthesis

"""research_prompts.py — LLM Prompt Templates for Evelyn's Deep Research.

Defines the system and user prompts used across all research phases (Plan,
Extract, Evaluate, and Synthesize). Designed for structured, high-accuracy,
stateless execution using Ollama. Prompts explicitly enforce clean output formats
(Markdown and JSON) to facilitate downstream parsing by the orchestrator.
"""

from typing import List, Dict, Any


def get_system_prompt() -> str:
    """Return the base system prompt for research execution.

    Directs the model to act as a precise, objective research assistant. Strips
    Evelyn's conversational persona to save context tokens and focus resources
    on raw data extraction and synthesis.

    Returns:
        str: Base system prompt.
    """
    return (
        "You are an expert, objective AI Research Assistant. Your task is to perform "
        "thorough, evidence-based investigation, extract precise factual data, evaluate "
        "information completeness, and synthesize highly structured reports. "
        "You always prioritize concrete facts, empirical numbers, dates, and direct evidence "
        "over vague generalizations. You strictly base all claims on the provided context "
        "and track source citations meticulously. Do not engage in small talk, meta-commentary, "
        "or emotional conversational filler."
    )


def build_plan_prompt(query: str, scope: str, max_sub_questions: int) -> str:
    """Build the prompt for the PLAN phase.

    Asks the model to decompose the original query into a numbered list of
    mutually exclusive, searchable sub-questions.

    Args:
        query: The main research question.
        scope: Research scope ('quick', 'standard', 'deep').
        max_sub_questions: Maximum number of sub-questions allowed.

    Returns:
        str: Formatted prompt.
    """
    return (
        f"You are formulating a research strategy for the query: \"{query}\"\n"
        f"Research Scope: {scope.upper()}\n"
        f"Maximum allowed sub-questions: {max_sub_questions}\n\n"
        "Your task is to decompose this query into a logical sequence of highly specific, "
        "independent, and searchable sub-questions. Each sub-question should target a "
        "distinct aspect of the main query so that answering all of them thoroughly "
        "will fully resolve the original research goal.\n\n"
        "Output ONLY a markdown block in the following format, containing a numbered list "
        "of sub-questions. Do not write any introduction, explanation, or concluding remarks.\n\n"
        "```markdown\n"
        "1. [Sub-question 1 - specific, searchable question]\n"
        "2. [Sub-question 2]\n"
        "...\n"
        "```"
    )


def build_extract_prompt(sub_question: str, source_id: str, source_title: str, source_url: str, page_content: str, current_notes: str) -> str:
    """Build the prompt for the EXTRACT phase.

    Asks the model to extract all facts relevant to the sub-question from a raw web page
    and merge them into the existing working notes, maintaining citation tags.

    Args:
        sub_question: The current sub-question.
        source_id: Unique string identifier for the source (e.g. "src_001").
        source_title: Title of the source web page.
        source_url: URL of the source web page.
        page_content: Clean extracted text from the page.
        current_notes: Existing compiled notes for this sub-question (can be empty).

    Returns:
        str: Formatted prompt.
    """
    notes_block = (
        f"### Current Working Notes:\n{current_notes}\n"
        if current_notes.strip()
        else "### Current Working Notes:\n*(No notes recorded yet — start fresh)*\n"
    )

    return (
        f"Sub-question under investigation: \"{sub_question}\"\n\n"
        f"We are reading a new source:\n"
        f"Source ID: {source_id}\n"
        f"Source Title: {source_title}\n"
        f"Source URL: {source_url}\n\n"
        f"{notes_block}\n"
        "-----------------------------------------\n"
        f"NEW SOURCE CONTENT:\n{page_content}\n"
        "-----------------------------------------\n\n"
        "TASK:\n"
        "1. Extract all facts, figures, statistics, names, dates, and key claims from the NEW SOURCE CONTENT "
        "that are directly relevant to answering the sub-question.\n"
        f"2. Integrate these new facts seamlessly into the 'Current Working Notes'. Add new findings under logical "
        "sub-headings. Meticulously tag every extracted fact with its citation label [{source_id}] at the end of the sentence.\n"
        "3. Preserve all facts and citations that were already present in 'Current Working Notes' — do NOT delete or summarize away "
        "prior findings from other sources. Only add, refine, or update them with the new source data.\n"
        "4. Keep observations highly factual, objective, and dense with details. Use bullet points under headings.\n\n"
        "Output ONLY the complete, updated markdown notes. Do not include any introductory chat or conversational meta-commentary."
    )


def build_evaluate_prompt(sub_question: str, current_notes: str, confidence_threshold: int) -> str:
    """Build the prompt for the EVALUATE phase.

    Instructs the model to evaluate the completeness of the collected notes against the
    sub-question, assigning a confidence score (0-100) and identifying specific gaps if needed.

    Args:
        sub_question: The sub-question being evaluated.
        current_notes: Compiled notes for this sub-question.
        confidence_threshold: The target confidence score (e.g., 80) to consider it resolved.

    Returns:
        str: Formatted prompt.
    """
    return (
        f"Sub-question: \"{sub_question}\"\n\n"
        f"Here are the compiled working notes collected from multiple sources:\n"
        f"```markdown\n{current_notes}\n```\n\n"
        "TASK:\n"
        "Evaluate the adequacy of these notes to fully, accurately, and comprehensively answer the sub-question.\n"
        "1. Assign a subjective confidence score from 0 to 100 on how thoroughly the notes resolve the sub-question. "
        "Be self-critical. If key details are missing, contradictory, or unverified, score it lower.\n"
        f"2. If your confidence is below the target threshold of {confidence_threshold}%, list the specific gaps, "
        "questions, or missing details that need to be searched for next.\n\n"
        "Output ONLY a valid, single JSON block containing exactly the keys 'confidence' and 'gaps'. "
        "Do not include markdown code fence formatting blocks inside or outside the JSON. "
        "Do not output any introductory or concluding text.\n\n"
        "Expected Format:\n"
        "{\n"
        "  \"confidence\": 85,  // an integer from 0 to 100\n"
        "  \"gaps\": [\"List of specific search queries or questions to address remaining gaps\"]  // array of strings, empty if confidence is high\n"
        "}"
    )


def build_synthesize_prompt(query: str, all_notes: Dict[str, str], sources_registry: List[Dict[str, Any]]) -> str:
    """Build the prompt for the SYNTHESIZE phase.

    Compiles all sub-question notes into a comprehensive, final markdown report
    with frontmatter, inline citations, and an overall confidence score.

    Args:
        query: The original research query.
        all_notes: A dictionary mapping sub-question strings to their notes.
        sources_registry: List of sources used, each containing id, url, and title.

    Returns:
        str: Formatted prompt.
    """
    notes_text = ""
    for sq, notes in all_notes.items():
        notes_text += f"### Sub-Question: {sq}\n{notes}\n\n"

    sources_text = ""
    for src in sources_registry:
        if src.get("failed"):
            continue
        sources_text += f"- [{src['id']}] {src['title']} ({src['url']})\n"

    return (
        f"Original Research Query: \"{query}\"\n\n"
        "Here are the compiled working notes for each sub-question researched by our agent:\n"
        "==================================================\n"
        f"{notes_text}"
        "==================================================\n\n"
        "Here is the registry of sources consulted during the research:\n"
        f"{sources_text}\n\n"
        "TASK:\n"
        "Synthesize these working notes into a definitive, highly professional research report resolving the original query.\n\n"
        "REQUIREMENTS:\n"
        "1. Write a structured, detailed report. Use markdown formatting with clear headings, sub-headings, lists, and tables where appropriate. "
        "Do not write a short summary — be comprehensive and capture all numbers, dates, statistics, and concrete details.\n"
        "2. Ensure all statements are strictly backed by the evidence in the notes. Maintain absolute citation integrity. "
        "Add inline citation tags like [src_001] or [src_002] to every factual assertion in the body of your report.\n"
        "3. Dedicate a final section of your report to 'Sources' listing the full citation registry.\n"
        "4. Assign an overall subjective confidence score from 0 to 100 on how thoroughly the research resolved the original query. "
        "Explain any limitations, weak evidence, or remaining areas of uncertainty in your analysis.\n"
        "5. Output the final report with a standard YAML frontmatter containing the keys: `title`, `date`, `confidence`, `sources_count`.\n\n"
        "Output ONLY the final markdown report starting with the YAML frontmatter. Do not write any conversational preamble or meta-commentary."
    )
