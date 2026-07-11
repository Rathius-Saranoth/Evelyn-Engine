# research_prompts.py
# date created: 2026-05-26
# date modified: 2026-07-09 18:18:33
# tags: #research, #prompts, #planning, #extraction, #evaluation, #synthesis

"""research_prompts.py — LLM Prompt Templates for Evelyn's Deep Research.

Defines the system and user prompts used across all research phases (Plan,
Extract, Evaluate, and Synthesize). Designed for structured, high-accuracy,
stateless execution using Ollama. Prompts explicitly enforce clean output formats
(Markdown and JSON) to facilitate downstream parsing by the orchestrator.

Exports:
  classify_research_query() — Keyword-heuristic task type classification (zero LLM cost).
  get_skill_template()      — Returns structured guidance block for a given task type.
  classify_domain_level()   — Keyword-heuristic domain-level classification (zero LLM cost).
  is_time_sensitive_query() — Zero-LLM-cost gate forcing full research on time-sensitive queries.
  get_system_prompt()       — Base system prompt for all research phases.
  build_necessity_check_prompt() — Necessity pre-filter: is research even needed?
  build_seed_subquestion_prompt() — Generates the single starting sub-question (no batch plan).
  build_search_query_prompt() — Formulates a short, atomic search-engine query from a sub-question/gap.
  is_atomic_query()          — Zero-LLM-cost validator for formulated search queries.
  build_extract_prompt()    — EXTRACT phase prompt (accepts optional skill template).
  build_evaluate_prompt()   — EVALUATE phase prompt.
  build_coverage_check_prompt() — Post-SQ check: is the original query covered, or is one more SQ needed?
  build_synthesize_prompt() — SYNTHESIZE phase prompt.
  build_rewrite_prompt()    — Sub-question auto-rewrite prompt.
  build_post_synthesis_triage_prompt() — Post-synthesis triage prompt.
"""

from typing import List, Dict, Any, Tuple, Optional


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
    # Food storage & shelf life
    "fridge", "refrigerator", "refrigerate", "spoil", "spoiled", "expire",
    "expiration", "shelf life", "go bad", "gone bad", "how long does",
    "how long will", "leftovers", "food storage", "keep fresh", "freezer",
    "freeze", "pantry", "best by", "use by",
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


# ---------------------------------------------------------------------------
# Time-sensitivity gate (necessity pre-filter safety net)
# ---------------------------------------------------------------------------

# Keywords that signal a query could have a stale or currently-changing answer.
# Any match forces full research regardless of what the necessity-check LLM
# call claims -- a confidently-wrong "I already know this" is far more
# dangerous for time-sensitive facts than for stable ones.
_TIME_SENSITIVE_KEYWORDS: List[str] = [
    "current", "currently", "latest", "newest", "recent", "recently",
    "as of", "right now", "these days", "nowadays", "today", "this year",
    "this week", "this month", "who is the president", "who is the ceo",
    "who is the current", "price of", "cost of", "stock price",
    "exchange rate", "release date", "when will", "upcoming", "still",
    "still airing", "still exist", "still around", "is there a new",
    "version of", "latest version",
]


def is_time_sensitive_query(query: str) -> bool:
    """Detect whether a query concerns something that could have changed recently.

    Zero-LLM-cost deterministic gate. Any match forces full research regardless
    of the necessity-check LLM's self-assessment -- a stable-knowledge claim
    from the model is far riskier to trust for time-sensitive topics (current
    office holders, prices, versions, ongoing status) than for settled facts.

    Args:
        query: The raw research query string.

    Returns:
        bool: True if the query matches any time-sensitive pattern.
    """
    q_lower = query.lower()
    return any(kw in q_lower for kw in _TIME_SENSITIVE_KEYWORDS)


# ---------------------------------------------------------------------------
# Query atomicity constraint (shared across plan/evaluate/rewrite/triage/search)
# ---------------------------------------------------------------------------

ATOMIC_QUERY_CONSTRAINT = (
    "## Query Atomicity Rules (mandatory)\n"
    "- Deconstruct, don't correlate: never force a single query to cross-reference "
    "multiple mechanisms, conditions, entities, or systems at once.\n"
    "- Target the core variable: if several specific items apply (e.g. three named "
    "proteins, three named libraries), collapse them into the single most relevant "
    "one or their shared parent category — do not list them all in one query.\n"
    "- Search the step, not the thesis: pick the single most useful next fact to "
    "look up, not the final synthesized answer to the whole sub-question.\n"
    "- One concrete marker per query: name one specific thing (a term, a product, "
    "a mechanism, an error code) — never a vague category standing in for several things.\n"
    "- Write it like a person typing into a search box, not like an academic paper "
    "title. No \"An Analysis of...\", no \"Regulatory Mechanisms Underlying...\".\n"
    "- Stay anchored to the parent topic's scope — do not wander into an adjacent "
    "general topic.\n\n"
    "BAD (compound / thesis-style): \"regulatory mechanisms linking cortisol, "
    "adrenaline, and BDNF to mental fatigue\"\n"
    "GOOD (atomic, searchable): \"cortisol mental fatigue\"\n"
)

# Deterministic heuristics used to validate that a formulated query is actually
# atomic before it is spent on a search engine call. Cheap, code-only checks —
# no LLM cost — per the "push control-flow into code" architecture principle.
_COMPOUND_MARKERS: List[str] = [" and ", " vs ", " versus ", " as well as ", " along with "]
_THESIS_PHRASE_MARKERS: List[str] = [
    "regulatory mechanisms", "underlying mechanisms", "an analysis of",
    "an overview of", "an examination of", "the relationship between",
    "the role of", "a comparison of", "a study of", "a review of",
    "mechanisms linking", "interplay between", "interaction between",
]
_MAX_QUERY_WORDS = 10


def is_atomic_query(query: str) -> Tuple[bool, Optional[str]]:
    """Validate that a formulated search query is atomic (one concept, search-shaped).

    Zero-LLM-cost deterministic check run against LLM-formulated search strings
    before they are spent on a real search engine call. Catches the two dominant
    failure patterns observed in practice: compound/multi-concept queries (joined
    by "and"/"vs"/lists) and thesis-style academic phrasing that search engines
    rank poorly. This is a validator, not a generator — it never rewrites the
    query itself, it only reports whether the caller should retry formulation.

    Args:
        query: The candidate search query string to validate.

    Returns:
        Tuple[bool, Optional[str]]: (True, None) if the query passes all checks,
        otherwise (False, reason) where reason names the specific failed check.
    """
    q = query.strip()
    if not q:
        return False, "empty query"

    q_lower = q.lower()
    word_count = len(q.split())
    if word_count > _MAX_QUERY_WORDS:
        return False, f"too long/compound ({word_count} words, max {_MAX_QUERY_WORDS})"

    for marker in _COMPOUND_MARKERS:
        if marker in q_lower:
            return False, f"contains compound conjunction ('{marker.strip()}')"

    if q_lower.count(",") >= 2:
        return False, "contains a list of 3+ items (2+ commas)"

    for phrase in _THESIS_PHRASE_MARKERS:
        if phrase in q_lower:
            return False, f"thesis-style phrasing ('{phrase}')"

    return True, None


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


def build_necessity_check_prompt(query: str, evidence_text: str) -> str:
    """Build the prompt for the necessity pre-filter's LLM self-assessment.

    Asks whether a research query can be considered already resolved --
    either because it was already answered in the reviewed conversation
    history, already recorded as a settled fact in memory, or is simple/
    stable enough that a multi-source cited report would be needless
    overhead. Deliberately biased toward "needs_research: true" on any doubt,
    since a false "already resolved" silently discards the entire task with
    zero external corroboration.

    Args:
        query: The research query or topic under consideration.
        evidence_text: Formatted block of recent chat history and/or matching
                       live memory entries to check the query against. May be
                       empty if no relevant evidence was found.

    Returns:
        str: Formatted prompt.
    """
    evidence_block = (
        evidence_text.strip()
        if evidence_text.strip()
        else "(No related conversation history or memory entries found.)"
    )

    return (
        f"Proposed research topic: \"{query}\"\n\n"
        "Here is potentially relevant prior context (recent conversation history "
        "and/or recorded memory facts):\n"
        "-----------------------------------------\n"
        f"{evidence_block}\n"
        "-----------------------------------------\n\n"
        "TASK:\n"
        "Decide whether launching a full research task for this topic is actually "
        "necessary, or whether it can be considered already resolved.\n\n"
        "It counts as already resolved if EITHER is true:\n"
        "1. The evidence above already contains a complete, direct answer to this "
        "exact question -- not a related tangent, a partial answer, or something "
        "that merely mentions the topic in passing.\n"
        "2. The topic is simple, casual, or well-established enough (a basic "
        "how-to, a common food-safety/storage fact, an everyday definition) that "
        "a multi-source cited research report would be pointless overhead -- the "
        "kind of thing that gets a short direct answer in conversation, not a report.\n\n"
        "Be conservative. If you have ANY real doubt, answer needs_research: true -- "
        "it is always safe to say true, since nothing bad happens if research proceeds "
        "on a topic that turns out to be simple. The failure mode being guarded "
        "against is the opposite: confidently discarding a task that actually needed "
        "real investigation.\n\n"
        "Output ONLY a valid JSON object with exactly the keys 'needs_research' and "
        "'confidence'. Do not include markdown code fences. Do not output any "
        "introductory or concluding text.\n\n"
        "Expected Format:\n"
        "{\n"
        "  \"needs_research\": false,  // true if real research is needed, false if already resolved\n"
        "  \"confidence\": 92  // integer 0-100, confidence in this needs_research judgment\n"
        "}"
    )


def build_seed_subquestion_prompt(query: str, domain_level: str = "specialist") -> str:
    """Build the prompt for generating the single starting sub-question.

    Replaces the old batch planner: rather than decomposing the full query
    into a fixed list up front, this generates only the most foundational
    first sub-question needed to begin investigating. Later sub-questions
    (if any) are generated one at a time by build_coverage_check_prompt()
    based on actual gaps found, not planned in advance. No count or ceiling
    is mentioned here -- the model is never told how many sub-questions
    "should" exist, so it has no quota to anchor toward.

    Args:
        query: The main research question.
        domain_level: One of 'everyday' or 'specialist'. Controls phrasing
            style. Defaults to 'specialist'.

    Returns:
        str: Formatted prompt.
    """
    if domain_level == "everyday":
        style_instruction = (
            "Phrase it in plain, natural language — the way someone would "
            "actually type it into a search engine or ask a knowledgeable "
            "friend. Avoid academic or overly technical phrasing."
        )
    else:
        style_instruction = (
            "Phrase it as a SHORT, single-concept search term or question "
            "— the kind of thing a person would type directly into a search "
            "engine, not an academic paper title."
        )

    return (
        f"You are beginning research on the query: \"{query}\"\n\n"
        "TASK:\n"
        "Identify the single most foundational sub-question needed to start "
        "investigating this topic — the first, most immediately useful thing "
        f"to look up. {style_instruction}\n\n"
        "Do not attempt to enumerate a full research plan or cover every angle "
        "of the topic up front. Additional sub-questions will be generated "
        "later, one at a time, only if a genuine gap remains once this one is "
        "answered.\n\n"
        f"{ATOMIC_QUERY_CONSTRAINT}\n"
        "Output ONLY the sub-question text on a single line. No numbering, no "
        "markdown, no explanation, no quotes."
    )


def build_search_query_prompt(
    question_text: str,
    task_type: str = "factual",
    retry_reason: Optional[str] = None,
) -> str:
    """Build the prompt for formulating a single search-engine-ready query.

    Takes a sub-question or gap string (which may itself be thesis-style or
    multi-concept, since it was authored for reasoning/notes, not for a search
    box) and asks the model to produce ONE short, atomic query suitable for an
    actual search engine call. The sub-question itself is left unchanged for
    notes, citations, and evaluation — only this formulated string is sent to
    the search engine.

    Args:
        question_text: The sub-question or gap string driving this search round.
        task_type: Classified task type ('factual', 'comparison', 'troubleshooting',
                   'opinion'). Currently informational only; reserved for future
                   type-specific query phrasing.
        retry_reason: If this is a retry after is_atomic_query() rejected a prior
                      formulation attempt, the specific failure reason to correct.
                      None on the first attempt.

    Returns:
        str: Formatted prompt.
    """
    retry_block = ""
    if retry_reason:
        retry_block = (
            f"\nYour previous attempt was rejected for this reason: {retry_reason}\n"
            "Produce a different query that specifically fixes this problem — do not "
            "repeat the same structure.\n"
        )

    return (
        f"Research sub-question or gap under investigation: \"{question_text}\"\n\n"
        "TASK:\n"
        "Formulate ONE short, concrete search-engine query that will surface useful "
        "sources for this. This is NOT the sub-question restated — it is the exact "
        "string a person would type into a search box to find this specific piece "
        "of information.\n\n"
        f"{ATOMIC_QUERY_CONSTRAINT}"
        f"{retry_block}\n"
        "Output ONLY the search query text on a single line. No quotes, no "
        "explanation, no numbering, no markdown formatting."
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
        f"2. If your confidence is below the target threshold of {confidence_threshold}%, list the specific gaps "
        "that need to be searched for next. Each gap must be phrased as a single, atomic, search-ready fragment "
        "— NOT a restatement of the whole sub-question, and not a compound clause combining multiple gaps.\n\n"
        f"{ATOMIC_QUERY_CONSTRAINT}\n"
        "Output ONLY a valid, single JSON block containing exactly the keys 'confidence' and 'gaps'. "
        "Do not include markdown code fence formatting blocks inside or outside the JSON. "
        "Do not output any introductory or concluding text.\n\n"
        "Expected Format:\n"
        "{\n"
        "  \"confidence\": 85,  // an integer from 0 to 100\n"
        "  \"gaps\": [\"List of specific, atomic search fragments to address remaining gaps\"]  // array of strings, empty if confidence is high\n"
        "}"
    )


def build_coverage_check_prompt(
    query: str,
    completed_sqs: List[Dict[str, Any]],
    domain_level: str = "specialist",
) -> str:
    """Build the prompt for the post-sub-question coverage check.

    Called every time a sub-question resolves successfully. Asks whether the
    original query is now adequately covered by everything resolved so far,
    or whether one more targeted sub-question is needed. No sub-question
    count or ceiling is ever mentioned — the caller enforces the hard
    sub_questions_limit in code, independent of this judgment, so the model
    is never anchored toward a target number of questions.

    Args:
        query: The original research query.
        completed_sqs: List of dicts with keys 'question', 'confidence', and
                       'notes_summary' for each sub-question resolved so far.
        domain_level: One of 'everyday' or 'specialist'. Controls phrasing
            style for any generated next_question. Defaults to 'specialist'.

    Returns:
        str: Formatted prompt.
    """
    if domain_level == "everyday":
        style_instruction = (
            "If another sub-question is needed, phrase it in plain, natural "
            "language — the way someone would actually type it into a search "
            "engine, not an academic paper title."
        )
    else:
        style_instruction = (
            "If another sub-question is needed, phrase it as a SHORT, "
            "single-concept search term or question, not an academic paper title."
        )

    covered_text = ""
    for sq in completed_sqs:
        covered_text += (
            f"- Sub-question: \"{sq['question']}\"\n"
            f"  Confidence: {sq.get('confidence', 0)}%\n"
            f"  Findings: {sq.get('notes_summary', '(no notes)')}\n\n"
        )

    return (
        f"Original research query: \"{query}\"\n\n"
        "Here is what has been investigated so far:\n"
        f"{covered_text}\n"
        "TASK:\n"
        "Decide whether the original query is now adequately answered by "
        "everything above, or whether one more sub-question is genuinely needed.\n\n"
        "Favor stopping. Only request another sub-question if there is a "
        "specific, concrete gap that would leave the original query meaningfully "
        "unanswered without it — not to add thoroughness for its own sake, cover "
        "a tangential angle, or reach a particular depth. Most queries need only "
        "a small number of sub-questions; needing several more is the exception, "
        "not the norm.\n\n"
        f"{style_instruction}\n\n"
        f"{ATOMIC_QUERY_CONSTRAINT}\n"
        "Output ONLY a valid JSON object with exactly the keys 'sufficient' and "
        "'next_question'. Do not include markdown code fences. Do not output "
        "any introductory or concluding text.\n\n"
        "Expected Format:\n"
        "{\n"
        "  \"sufficient\": false,  // true if the original query is adequately answered now\n"
        "  \"next_question\": \"...\"  // the single next sub-question if sufficient is false, else null\n"
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
        "4. The rewritten question must stay atomic — narrow the scope, do not broaden it into a "
        "compound question covering multiple gaps at once.\n\n"
        f"{ATOMIC_QUERY_CONSTRAINT}\n"
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
        "   - Provide 2-3 specific, searchable child questions that narrow the scope. Each child "
        "must be atomic — one concept per question, not a compound restatement of the parent.\n\n"
        f"{ATOMIC_QUERY_CONSTRAINT}\n"
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
