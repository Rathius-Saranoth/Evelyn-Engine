# research_prompts.py
# date created: 2026-05-26
# date modified: 2026-07-03 10:48:16
# tags: #research, #prompts, #planning, #extraction, #evaluation, #synthesis

"""research_prompts.py — LLM Prompt Templates for Evelyn's Deep Research.

Defines the system and user prompts used across all research phases (Plan,
Extract, Evaluate, and Synthesize). Designed for structured, high-accuracy,
stateless execution using Ollama. Prompts explicitly enforce clean output formats
(Markdown and JSON) to facilitate downstream parsing by the orchestrator.

Exports:
  classify_research_query() — Keyword-heuristic task type classification (zero LLM cost).
  get_skill_template()      — Returns structured guidance block for a given task type.
  get_system_prompt()       — Base system prompt for all research phases.
  build_plan_prompt()       — PLAN phase prompt.
  build_extract_prompt()    — EXTRACT phase prompt (accepts optional skill template).
  build_evaluate_prompt()   — EVALUATE phase prompt.
  build_synthesize_prompt() — SYNTHESIZE phase prompt.
  build_rewrite_prompt()    — Sub-question auto-rewrite prompt.
  build_post_synthesis_triage_prompt() — Post-synthesis triage prompt.
"""

from typing import List, Dict, Any


# ---------------------------------------------------------------------------
# Task-type classification + skill templates (Hermes Tier 2 #8b)
# ---------------------------------------------------------------------------

# Keyword sets for zero-cost heuristic classification. Order matters: more
# specific patterns come first so they shadow broader ones.
_TASK_TYPE_KEYWORDS: Dict[str, List[str]] = {
    "troubleshooting": [
        "fix", "error", "bug", "broken", "not working", "crash", "fail",
        "problem", "issue", "debug", "troubleshoot", "resolve", "stuck",
        "exception", "traceback", "why is", "why does",
    ],
    "comparison": [
        "vs", "versus", "compare", "comparison", "difference between",
        "better", "best", "pros and cons", "trade-off", "tradeoff",
        "which is", "which should", "alternatives to",
    ],
    "opinion": [
        "opinion", "recommend", "recommendation", "review", "worth it",
        "worth learning", "worth using", "worth buying", "worth switching",
        "should i", "is it good", "thoughts on", "experience with",
        "community", "reddit", "forum", "people say",
        "what do people think", "what does everyone think",
        "what do users think", "what do people say",
    ],
    # Factual is the default catch-all — no keywords needed.
}

# Structured guidance blocks injected into extract prompts per task type.
# Each block sets: Research Goal, Key Procedures, Common Pitfalls, Verification.
RESEARCH_SKILL_TEMPLATES: Dict[str, str] = {
    "factual": (
        "## Research Task Type: Factual Lookup\n"
        "**Research Goal**: Establish precise, verifiable facts (dates, figures, names, "
        "specifications, definitions). Every claim must cite a source.\n"
        "**Key Procedures**:\n"
        "- Prioritize primary sources: official documentation, academic papers, manufacturer specs.\n"
        "- Record exact numbers and dates — never round or approximate without noting it.\n"
        "- Cross-reference between at least two independent sources before treating a fact as confirmed.\n"
        "**Common Pitfalls**:\n"
        "- Do not infer facts from analogies or comparisons — only cite stated data.\n"
        "- Distinguish between official claims and third-party reports.\n"
        "**Verification Criteria**: Answer is complete when exact figures, dates, and sources "
        "are recorded with ≥2 corroborating citations.\n"
    ),
    "comparison": (
        "## Research Task Type: Comparison Analysis\n"
        "**Research Goal**: Produce a balanced, multi-dimensional comparison. Identify key "
        "dimensions, collect data on each option per dimension, then evaluate trade-offs.\n"
        "**Key Procedures**:\n"
        "- Identify 3–6 comparison dimensions relevant to the query (performance, cost, ease-of-use, etc.).\n"
        "- Collect data for each option on each dimension from independent sources.\n"
        "- Note version/date of data — comparisons rot quickly.\n"
        "- Prefer benchmarks and quantitative data over subjective claims.\n"
        "**Common Pitfalls**:\n"
        "- Avoid letting one strongly opinionated source skew the analysis.\n"
        "- Flag when a dimension lacks comparable data rather than leaving it blank.\n"
        "**Verification Criteria**: Answer is complete when ≥3 dimensions are covered with "
        "data for each option and trade-offs are explicitly stated.\n"
    ),
    "troubleshooting": (
        "## Research Task Type: Troubleshooting\n"
        "**Research Goal**: Identify the root cause and verified solution(s) for a specific "
        "technical problem. Prioritize confirmed fixes over speculative suggestions.\n"
        "**Key Procedures**:\n"
        "- Note the exact error message, version numbers, and environment details when available.\n"
        "- Collect multiple candidate causes — do not stop at the first plausible explanation.\n"
        "- Prioritize solutions confirmed by official maintainers or reproducible by multiple users.\n"
        "- Record the exact steps/commands for each fix — no hand-waving.\n"
        "**Common Pitfalls**:\n"
        "- 'Try reinstalling' is not a root cause analysis — document WHY it fixes it.\n"
        "- Distinguish between workarounds (symptom relief) and root-cause fixes.\n"
        "- Flag solutions that are version-specific or environment-specific.\n"
        "**Verification Criteria**: Answer is complete when at least one confirmed fix with "
        "exact steps is documented, with the root cause explained.\n"
    ),
    "opinion": (
        "## Research Task Type: Opinion Synthesis\n"
        "**Research Goal**: Aggregate community experience and expert sentiment, then synthesize "
        "a representative consensus with its distribution (majority vs. minority views).\n"
        "**Key Procedures**:\n"
        "- Sample from diverse sources: official reviews, forum threads, academic critiques.\n"
        "- Quantify sentiment where possible (e.g., '80% of reviewers note X').\n"
        "- Explicitly represent dissenting or minority views — do not flatten to a single opinion.\n"
        "- Note recency of sources — community opinion shifts with product versions.\n"
        "**Common Pitfalls**:\n"
        "- Do not present a single review as representative consensus.\n"
        "- Distinguish promotional content from genuine user experience.\n"
        "- Flag when sources are affiliated with the subject being reviewed.\n"
        "**Verification Criteria**: Answer is complete when majority and minority views are both "
        "documented with citations and a synthesis statement is provided.\n"
    ),
}


def classify_research_query(query: str) -> str:
    """Classify a research query into one of four task types using keyword heuristics.

    Uses a zero-LLM-cost keyword scan. Falls back to 'factual' when no specific
    pattern is matched. Order of the keyword dict defines precedence — more
    specific types (troubleshooting, comparison) shadow the generic catch-all.

    Args:
        query: The raw research query string.

    Returns:
        str: One of 'factual', 'comparison', 'troubleshooting', 'opinion'.
    """
    q_lower = query.lower()
    for task_type, keywords in _TASK_TYPE_KEYWORDS.items():
        if any(kw in q_lower for kw in keywords):
            return task_type
    return "factual"


def get_skill_template(task_type: str) -> str:
    """Return the structured guidance block for a given task type.

    Args:
        task_type: One of 'factual', 'comparison', 'troubleshooting', 'opinion'.
                   Defaults to 'factual' for unknown types.

    Returns:
        str: Multiline structured guidance block to prepend to extract prompts.
    """
    return RESEARCH_SKILL_TEMPLATES.get(task_type, RESEARCH_SKILL_TEMPLATES["factual"])


# ---------------------------------------------------------------------------
# Domain-level classification (Hermes Tier 2 #8b register extension)
# ---------------------------------------------------------------------------

# Keywords that strongly suggest a topic belongs to the "everyday" domain:
# tasks that a non-specialist person regularly encounters and for which
# plain, step-oriented language is more useful than academic prose.
_EVERYDAY_KEYWORDS: List[str] = [
    # Home improvement & DIY
    "fix", "repair", "install", "replace", "patch", "paint", "hang",
    "mount", "assemble", "wire", "plumb", "tile", "caulk", "grout",
    "insulate", "drywall", "refinish", "unclog", "drain", "faucet",
    "toilet", "shower", "sink", "gutter", "roof", "fence", "deck",
    "diy", "home improvement", "home repair", "hardware",
    # Cooking & food
    "cook", "bake", "recipe", "ingredient", "grill", "roast", "simmer",
    "marinate", "season", "meal prep", "kitchen",
    # Gardening & outdoors
    "plant", "grow", "garden", "prune", "fertilize", "weed", "compost",
    "lawn", "mow", "mulch", "water",
    # Crafts & making
    "sew", "knit", "crochet", "craft", "woodworking", "upholster",
    # Everyday life & organisation
    "organize", "declutter", "clean", "laundry", "budget", "meal plan",
    # Basic automotive
    "change oil", "flat tire", "car wash", "wiper blade", "headlight",
    "brake pad", "air filter",
    # General how-to phrasing
    "how to", "step by step", "beginner", "for beginners", "easy way",
    "at home", "without a",
]


def classify_domain_level(query: str) -> str:
    """Classify a query's inherent domain expertise level.

    Determines how much specialist knowledge the topic requires, independent
    of who is asking. This controls prompt formality and report style:

    - 'everyday': Topics anyone might research without a professional background
      (home repair, cooking, gardening, basic how-tos). Use plain, step-oriented
      language that matches how people actually search and think about these tasks.
    - 'specialist': Topics that inherently require domain expertise to interpret
      correctly (engineering, medicine, law, advanced programming, science).
      Use precise vocabulary and a structured analytical format.

    Uses zero-LLM-cost keyword detection on the query. Defaults to 'specialist'
    when no everyday keywords are matched, preserving the current behavior for
    all technical and ambiguous queries.

    Args:
        query: The raw research query string.

    Returns:
        str: One of 'everyday' or 'specialist'.
    """
    q_lower = query.lower()
    if any(kw in q_lower for kw in _EVERYDAY_KEYWORDS):
        return "everyday"
    return "specialist"


def get_system_prompt(domain_level: str = "specialist") -> str:
    """Return the base system prompt for research execution.

    Adjusts persona and tone based on the inherent expertise level of the topic.
    Specialist topics get a formal, evidence-focused research analyst persona.
    Everyday topics get a knowledgeable-friend persona that prioritizes clarity
    and practical actionability over academic structure.

    Args:
        domain_level: One of 'everyday' or 'specialist'. Defaults to 'specialist'.

    Returns:
        str: Base system prompt calibrated to the domain level.
    """
    if domain_level == "everyday":
        return (
            "You are a knowledgeable, practical research assistant. Your job is to find "
            "clear, actionable information and present it in plain, accessible language "
            "that anyone can follow. Prioritize step-by-step instructions, real-world "
            "tips, and common pitfalls over academic precision. Write the way a "
            "knowledgeable friend would explain something — not the way a textbook would. "
            "Base all claims on the provided context and cite sources where they add "
            "useful detail. Skip jargon unless it is necessary and explained."
        )
    return (
        "You are an expert, objective AI Research Assistant. Your task is to perform "
        "thorough, evidence-based investigation, extract precise factual data, evaluate "
        "information completeness, and synthesize highly structured reports. "
        "You always prioritize concrete facts, empirical numbers, dates, and direct evidence "
        "over vague generalizations. You strictly base all claims on the provided context "
        "and track source citations meticulously. Do not engage in small talk, meta-commentary, "
        "or emotional conversational filler."
    )


def build_plan_prompt(query: str, scope: str, max_sub_questions: int, domain_level: str = "specialist") -> str:
    """Build the prompt for the PLAN phase.

    Asks the model to decompose the original query into a numbered list of
    searchable sub-questions. The phrasing style of those sub-questions is
    calibrated to the domain level: everyday topics use plain, natural-language
    questions; specialist topics use precise, technical decomposition.

    Args:
        query: The main research question.
        scope: Research scope ('quick', 'standard', 'deep').
        max_sub_questions: Maximum number of sub-questions allowed.
        domain_level: One of 'everyday' or 'specialist'. Controls sub-question
            phrasing style. Defaults to 'specialist'.

    Returns:
        str: Formatted prompt.
    """
    if domain_level == "everyday":
        style_instruction = (
            "Write the sub-questions in plain, natural language — the way someone "
            "would actually type them into a search engine or ask a knowledgeable friend. "
            "Focus on practical steps, what to buy or gather, common mistakes to avoid, "
            "and how to know when the job is done right. Avoid academic or overly technical phrasing."
        )
    else:
        style_instruction = (
            "Each sub-question must be a SHORT, single-concept search term or question "
            "— the kind of thing a person would type directly into a search engine. "
            "One question = one topic. Do NOT combine multiple concepts, comparisons, or "
            "qualifiers into a single question using 'and', 'versus', or long prepositional "
            "clauses. If a topic has multiple angles, give each angle its own separate question.\n\n"
            "EXAMPLE — Bad (compound, verbose, unsearchable):\n"
            "  'What vector databases and embedding models offer the best performance and "
            "local deployment options for embedding source code repositories at scale?'\n\n"
            "EXAMPLE — Good (atomic, plain, searchable):\n"
            "  'best local vector databases for code embeddings'\n"
            "  'embedding models for source code similarity search'\n\n"
            "Write every sub-question at this level of brevity and focus."
        )

    return (
        f"You are formulating a research strategy for the query: \"{query}\"\n"
        f"Research Scope: {scope.upper()}\n"
        f"Maximum allowed sub-questions: {max_sub_questions}\n\n"
        f"Your task is to decompose this query into a logical sequence of sub-questions. "
        f"{style_instruction}\n\n"
        "Output ONLY a markdown block in the following format, containing a numbered list "
        "of sub-questions. Do not write any introduction, explanation, or concluding remarks.\n\n"
        "```markdown\n"
        "1. [Sub-question 1]\n"
        "2. [Sub-question 2]\n"
        "...\n"
        "```"
    )


def build_extract_prompt(
    sub_question: str,
    source_id: str,
    source_title: str,
    source_url: str,
    page_content: str,
    current_notes: str,
    skill_template: str = "",
) -> str:
    """Build the prompt for the EXTRACT phase.

    Asks the model to extract all facts relevant to the sub-question from a raw web page
    and merge them into the existing working notes, maintaining citation tags.
    An optional skill_template block (from get_skill_template()) is prepended to
    give the model structured guidance on what quality of answer is expected for
    this research task type.

    Args:
        sub_question: The current sub-question.
        source_id: Unique string identifier for the source (e.g. "src_001").
        source_title: Title of the source web page.
        source_url: URL of the source web page.
        page_content: Clean extracted text from the page.
        current_notes: Existing compiled notes for this sub-question (can be empty).
        skill_template: Optional structured guidance block from get_skill_template().
                        Prepended to the prompt header when provided.

    Returns:
        str: Formatted prompt.
    """
    notes_block = (
        f"### Current Working Notes:\n{current_notes}\n"
        if current_notes.strip()
        else "### Current Working Notes:\n*(No notes recorded yet — start fresh)*\n"
    )

    template_header = f"{skill_template}\n\n" if skill_template.strip() else ""

    return (
        f"{template_header}"
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


def build_notes_summary_prompt(sub_question: str, notes: str, task_type: str = "factual") -> str:
    """Build a prompt for compressing oversized SQ notes before synthesis.

    Called when a single sub-question's notes exceed RESEARCH_NOTES_SUMMARY_THRESHOLD
    characters. Produces a condensed version that preserves everything the synthesizer
    needs (key facts, source IDs, confidence signals, gaps) while stripping redundant
    narrative and repetition.

    The prompt is task-type aware so the model knows which signal to prioritise:
    - factual/troubleshooting: preserve numbers, dates, source citations.
    - comparison: preserve structured contrasts and attribute tables.
    - opinion: preserve distinct viewpoints and named sources.

    Args:
        sub_question: The sub-question whose notes are being compressed.
        notes: Full raw notes text for this sub-question.
        task_type: Classified task type ('factual', 'comparison', 'troubleshooting',
                   'opinion'). Determines preservation emphasis.

    Returns:
        str: Prompt instructing the model to produce compressed notes.
    """
    type_guidance = {
        "factual": (
            "Preserve all numbers, dates, statistics, named entities, and inline source "
            "citation tags (e.g. [src_001]). These are the core evidence units."
        ),
        "comparison": (
            "Preserve all named items being compared, their key differentiating attributes, "
            "and quantitative values. Retain table or list structures where they appear. "
            "Keep inline source citation tags (e.g. [src_001])."
        ),
        "troubleshooting": (
            "Preserve all identified causes, symptoms, and fix procedures. Keep step-by-step "
            "sequences intact. Retain inline source citation tags (e.g. [src_001])."
        ),
        "opinion": (
            "Preserve distinct viewpoints, the names or affiliations of their holders, and "
            "any supporting arguments. Retain inline source citation tags (e.g. [src_001])."
        ),
    }.get(task_type, (
        "Preserve all factual claims, numbers, and inline source citation tags (e.g. [src_001])."
    ))

    return (
        f"You are a research notes compressor. The following notes were collected to answer "
        f"the sub-question: \"{sub_question}\"\n\n"
        "These notes are too long to fit efficiently into the final synthesis prompt. "
        "Your task is to compress them into a dense, information-rich summary that retains "
        "ALL evidence needed for a final research report writer to produce accurate, "
        "well-cited output.\n\n"
        f"PRESERVATION RULE for this task type ({task_type}):\n{type_guidance}\n\n"
        "COMPRESSION RULES:\n"
        "1. Remove all meta-commentary, filler phrases, and repetition "
        "(e.g. 'Based on the source above...', 'As mentioned earlier...').\n"
        "2. Do NOT introduce any new information not present in the original notes.\n"
        "3. Do NOT remove inline source citation tags — they are mandatory for the report.\n"
        "4. Keep identified knowledge gaps if any are listed at the end of the notes.\n"
        "5. Target length: roughly one-third of the original. Shorter is better if "
        "all key evidence is retained.\n\n"
        "Output ONLY the compressed notes. No preamble, no explanation.\n\n"
        "=== NOTES TO COMPRESS ===\n"
        f"{notes}\n"
        "=== END NOTES ==="
    )


def build_synthesize_prompt(
    query: str,
    all_notes: Dict[str, str],
    sources_registry: List[Dict[str, Any]],
    domain_level: str = "specialist",
    scope: str = "standard",
) -> str:
    """Build the prompt for the SYNTHESIZE phase.

    Compiles all sub-question notes into a final written output. The format and
    tone are calibrated to the domain level:

    - 'specialist': A formal, structured research report with frontmatter, inline
      citations, and a confidence score. Appropriate for technical, scientific, or
      complex analytical topics.
    - 'everyday': A clear, practical guide written in plain language. Step-oriented,
      conversational, focused on actionability. Still cites sources but without
      academic formality.

    Args:
        query: The original research query.
        all_notes: A dictionary mapping sub-question strings to their notes.
        sources_registry: List of sources used, each containing id, url, and title.
        domain_level: One of 'everyday' or 'specialist'. Defaults to 'specialist'.
        scope: Research scope determining depth and tag count. Defaults to 'standard'.

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

    if scope == "quick":
        tag_count_instruction = "1-3"
    elif scope == "deep":
        tag_count_instruction = "6-9"
    else:
        tag_count_instruction = "3-6"

    if domain_level == "everyday":
        task_instruction = (
            "Synthesize these research notes into a clear, practical guide that directly "
            "answers the original query. Write for someone who wants to get the job done, "
            "not for an academic audience."
        )
        requirements = (
            "1. Use plain, conversational language throughout. Avoid jargon; if a technical "
            "term is necessary, explain it in plain English immediately after.\n"
            "2. Organise the content as a practical guide: lead with what matters most "
            "(materials needed, key steps, safety notes), then walk through the process "
            "in logical order. Use numbered steps, bullet lists, and short paragraphs.\n"
            "3. Include concrete, actionable tips sourced from the notes. Where it adds "
            "real value, reference a source naturally in-text (e.g. 'according to [src_001]') "
            "rather than tagging every sentence.\n"
            "4. Close with a brief 'Things to watch out for' or 'Common mistakes' section "
            "if the notes contain relevant warnings.\n"
            "5. End with a short 'Sources' list. No need for a formal confidence score.\n\n"
            f"Output ONLY the final markdown guide starting with a YAML frontmatter block containing the keys: "
            f"`title`, `short_title` (a concise 2-5 word alternative title), `date`, `sources_count`, "
            f"and `topic_tags` (a YAML list of {tag_count_instruction} specific, lowercase, hyphenated topic tags representing the subject matter).\n\n"
            "Do not write any conversational preamble or meta-commentary."
        )
    else:
        task_instruction = (
            "Synthesize these working notes into a definitive, highly professional "
            "research report resolving the original query."
        )
        requirements = (
            "1. Write a structured, detailed report. Use markdown formatting with clear "
            "headings, sub-headings, lists, and tables where appropriate. "
            "Do not write a short summary — be comprehensive and capture all numbers, "
            "dates, statistics, and concrete details.\n"
            "2. Ensure all statements are strictly backed by the evidence in the notes. "
            "Maintain absolute citation integrity. Add inline citation tags like [src_001] "
            "or [src_002] to every factual assertion in the body of your report.\n"
            "3. Dedicate a final section of your report to 'Sources' listing the full "
            "citation registry.\n"
            "4. Assign an overall subjective confidence score from 0 to 100 on how "
            "thoroughly the research resolved the original query. Explain any limitations, "
            "weak evidence, or remaining areas of uncertainty in your analysis.\n"
            f"5. Output the final report with a standard YAML frontmatter containing the keys: "
            f"`title`, `short_title` (a concise 2-5 word alternative title), `date`, `confidence`, `sources_count`, "
            f"and `topic_tags` (a YAML list of {tag_count_instruction} specific, lowercase, hyphenated topic tags representing the subject matter).\n\n"
            "Output ONLY the final markdown report starting with the YAML frontmatter. "
            "Do not write any conversational preamble or meta-commentary."
        )

    return (
        f"Original Research Query: \"{query}\"\n\n"
        "Here are the compiled working notes for each sub-question researched by our agent:\n"
        "==================================================\n"
        f"{notes_text}"
        "==================================================\n\n"
        "Here is the registry of sources consulted during the research:\n"
        f"{sources_text}\n\n"
        f"TASK:\n{task_instruction}\n\n"
        f"REQUIREMENTS:\n{requirements}"
    )


def build_rewrite_prompt(original_question: str, current_notes: str, gaps: List[str]) -> str:
    """Build the prompt for auto-rewriting a low-confidence sub-question.

    Instructs the model to produce a single, semantically divergent search
    question that targets the identified gaps. Enforces meaningful deviation
    from the original phrasing to avoid burning search iterations on the same
    barren query space.

    Args:
        original_question: The current sub-question text.
        current_notes: Compiled notes collected so far for this sub-question.
        gaps: List of identified knowledge gaps from the evaluate step.

    Returns:
        str: Formatted prompt.
    """
    gaps_text = "\n".join(f"- {g}" for g in gaps) if gaps else "- No specific gaps identified."

    notes_block = (
        f"### Existing Research Notes:\n{current_notes}\n"
        if current_notes.strip()
        else "### Existing Research Notes:\n*(No substantial evidence collected)*\n"
    )

    return (
        f"The following research sub-question yielded LOW CONFIDENCE results after searching:\n\n"
        f"Original Sub-Question: \"{original_question}\"\n\n"
        f"{notes_block}\n"
        f"### Identified Knowledge Gaps:\n{gaps_text}\n\n"
        "TASK:\n"
        "Rewrite this sub-question into a single, more targeted search question that directly "
        "addresses the identified gaps.\n\n"
        "CRITICAL RULES:\n"
        "1. You MUST NOT repeat the same phrasing as the original sub-question. The original "
        "query already failed to find adequate results — repeating it will fail again.\n"
        "2. Perform a SEMANTIC PIVOT: change the angle of approach. Use alternative terminology, "
        "synonyms, adjacent concepts, or narrow the scope to a specific aspect mentioned in the gaps.\n"
        "3. If the gaps suggest the search space is barren (no sources found at all), try reframing "
        "the question using different technical vocabulary or targeting a closely related concept "
        "that would indirectly answer the original question.\n"
        "4. The rewritten question must be a single, concise, web-searchable question.\n\n"
        "Output ONLY the rewritten question on a single line. No explanation, no numbering, "
        "no meta-commentary, no quotes."
    )


def build_post_synthesis_triage_prompt(
    gap_analysis_text: str,
    low_confidence_sqs: List[Dict[str, Any]]
) -> str:
    """Build the prompt for post-synthesis sub-question triage.

    Instructs the model to decide, per low-confidence sub-question, whether to
    REMOVE it (dead end) or SPLIT it into more targeted child sub-questions.

    Args:
        gap_analysis_text: The limitations/gaps section extracted from the report.
        low_confidence_sqs: List of dicts with keys 'id', 'question', 'confidence',
                            and 'notes_summary'.

    Returns:
        str: Formatted prompt.
    """
    sq_entries = ""
    for sq in low_confidence_sqs:
        sq_entries += (
            f"- ID: {sq['id']}\n"
            f"  Question: \"{sq['question']}\"\n"
            f"  Confidence: {sq['confidence']}%\n"
            f"  Notes Summary: {sq.get('notes_summary', '(no notes)')}\n\n"
        )

    return (
        "A research report has been synthesized, but some sub-questions still have low confidence. "
        "The report identifies the following gaps and limitations:\n\n"
        f"### Report Gap Analysis:\n{gap_analysis_text}\n\n"
        "### Low-Confidence Sub-Questions:\n"
        f"{sq_entries}\n"
        "TASK:\n"
        "For each low-confidence sub-question, decide ONE of two actions:\n\n"
        "1. **REMOVE** — The sub-question is a dead end. Use this when:\n"
        "   - No credible sources exist for the topic\n"
        "   - The sub-question is unfulfillable (too niche, no public data)\n"
        "   - The confidence is near zero and further searching would be futile\n"
        "   - Provide a brief reason explaining why it should be removed.\n\n"
        "2. **SPLIT** — The sub-question was too broad and needs to be decomposed. Use this when:\n"
        "   - The gap analysis reveals the question needs OS-specific, language-specific, "
        "or domain-specific variants\n"
        "   - A more targeted set of 2-3 child questions would succeed where the broad one failed\n"
        "   - Provide 2-3 specific, searchable child questions that narrow the scope.\n\n"
        "Output ONLY a valid JSON array. Do not wrap in markdown code fences. "
        "Do not include any introductory or concluding text.\n\n"
        "Expected Format:\n"
        "[\n"
        "  {\n"
        '    "sq_id": "sq_01",\n'
        '    "action": "remove",\n'
        '    "reason": "No credible sources exist for this highly niche subtopic."\n'
        "  },\n"
        "  {\n"
        '    "sq_id": "sq_03",\n'
        '    "action": "split",\n'
        '    "reason": "Question was too broad.",\n'
        '    "children": [\n'
        '      "Specific child question 1",\n'
        '      "Specific child question 2"\n'
        "    ]\n"
        "  }\n"
        "]"
    )
