# procedure_matcher.py
# date created: 2026-09-03 18:09:14
# date modified: 2026-09-07 14:33:51
# tags: 

"""Canonical utility for procedure tokenization, similarity scoring, deduplication, and master detection.

Provides single-source-of-truth matching routines shared across fact_extractor.py,
procedure_consolidator.py, and server review endpoints.
"""

from __future__ import annotations

import re
import sqlite3

# Standardized conversational stopwords for trigger patterns
STOPWORDS: set[str] = {
    "when", "the", "user", "says", "asks", "tells", "you", "for", "with",
    "that", "this", "and", "are", "your", "they", "into", "from", "about",
    "how", "what", "which", "there", "their", "want", "wants", "asked",
    "can", "could", "should", "would", "where", "whom", "will", "been",
    "have", "has", "had", "like", "onto", "over", "under", "make", "need",
    "some", "time", "just", "also", "were", "here", "more", "done", "know",
    "good", "well", "very", "hello", "hey", "them", "then", "our", "all",
}

# Specialized single-purpose tools where procedure trigger overlap should be strongly concordant
SPECIALIZED_TOOLS: set[str] = {
    "write_journal_entry",
    "write_dream_entry",
    "get_health_metrics",
    "get_recent_workouts",
    "sync_google_calendar",
    "create_calendar_event",
    "delete_calendar_event",
    "create_task",
    "complete_task",
    "delete_task",
    "list_tasks",
    "sync_google_tasks",
    "generate_image",
    "manage_vault_list",
    "start_research",
}

TOOL_CANONICAL_DOMAINS: dict[str, str] = {
    "write_journal_entry": "domain_journal",
    "write_dream_entry": "domain_dream",
    "generate_image": "domain_art",
    "create_task": "domain_task",
    "complete_task": "domain_task",
    "delete_task": "domain_task",
    "list_tasks": "domain_task",
    "sync_google_tasks": "domain_task",
    "create_calendar_event": "domain_agenda",
    "delete_calendar_event": "domain_agenda",
    "sync_google_calendar": "domain_agenda",
    "get_agenda": "domain_agenda",
    "get_health_metrics": "domain_health",
    "get_recent_workouts": "domain_exercise",
    "manage_vault_list": "domain_list",
    "start_research": "domain_research",
    "list_research_tasks": "domain_research",
    "inspect_research_task": "domain_research",
    "guide_research": "domain_research",
    "check_new_research": "domain_research",
    "read_url": "domain_web",
    "web_search": "domain_web",
    "search_history": "domain_history",
}

# Lean colloquial synonym overlay for natural spoken variants not present in tool signatures
COLLOQUIAL_SYNONYMS: dict[str, str] = {
    # Sleep & Journaling wind-downs
    "sleep": "domain_journal",
    "sleeping": "domain_journal",
    "bed": "domain_journal",
    "bedtime": "domain_journal",
    "goodnight": "domain_journal",
    "night": "domain_journal",
    "evening": "domain_journal",
    "journal": "domain_journal",
    "journaling": "domain_journal",
    "reflect": "domain_journal",
    "reflecting": "domain_journal",
    "reflection": "domain_journal",
    "reflections": "domain_journal",
    "winddown": "domain_journal",
    "winding": "domain_journal",
    "wind": "domain_journal",
    "closeout": "domain_journal",
    "closing": "domain_journal",
    "diary": "domain_journal",
    "downtime": "domain_journal",
    # Dreams
    "dream": "domain_dream",
    "dreams": "domain_dream",
    "dreaming": "domain_dream",
    "nightmare": "domain_dream",
    "nightmares": "domain_dream",
    "lucid": "domain_dream",
    "somnambulant": "domain_dream",
    "dreamscape": "domain_dream",
    "dreamer": "domain_dream",
    # Visuals & Art
    "image": "domain_art",
    "images": "domain_art",
    "art": "domain_art",
    "drawing": "domain_art",
    "illustration": "domain_art",
    "picture": "domain_art",
    "portrait": "domain_art",
    "dnd": "domain_dnd",
    "magic": "domain_dnd",
    "item": "domain_dnd",
    "saros": "domain_dnd",
    # Tasks & Scheduling
    "task": "domain_task",
    "tasks": "domain_task",
    "todo": "domain_task",
    "errand": "domain_task",
    "reminder": "domain_task",
    "reminders": "domain_task",
    "agenda": "domain_agenda",
    "calendar": "domain_agenda",
    "meeting": "domain_agenda",
    "appointment": "domain_agenda",
    # Health & Wellness
    "health": "domain_health",
    "biometrics": "domain_health",
    "fatigue": "domain_health",
    "pacing": "domain_health",
    "unwell": "domain_health",
    "exhaustion": "domain_health",
    "workout": "domain_exercise",
    "workouts": "domain_exercise",
    "exercise": "domain_exercise",
    # Research
    "research": "domain_research",
    "explore": "domain_research",
    "investigate": "domain_research",
}


def _build_tool_kwarg_synonyms() -> dict[str, str]:
    """Derive domain synonym tokens directly from MODEL_TOOL_DEFINITIONS schemas and kwargs."""
    tool_tokens: dict[str, str] = {}
    try:
        from Evelyn.tools.evelyn_tools import MODEL_TOOL_DEFINITIONS

        for defn in MODEL_TOOL_DEFINITIONS:
            if not isinstance(defn, dict):
                continue
            fn = defn.get("function")
            if not isinstance(fn, dict):
                continue
            name = fn.get("name")
            if not isinstance(name, str) or name not in TOOL_CANONICAL_DOMAINS:
                continue
            domain = TOOL_CANONICAL_DOMAINS[name]

            # 1. Tool name tokens (e.g. 'journal', 'dream', 'workout')
            for part in name.split("_"):
                if len(part) >= 3 and part not in STOPWORDS:
                    tool_tokens[part.lower()] = domain

            # 2. Kwarg / parameter names (e.g. 'mood', 'vibe', 'check', 'narrative', 'feelings', 'analysis')
            params = fn.get("parameters")
            props = params.get("properties") if isinstance(params, dict) else None
            if isinstance(props, dict):
                for p_name in props:
                    for sub in str(p_name).split("_"):
                        if (
                            len(sub) >= 3
                            and sub not in STOPWORDS
                            and sub
                            not in (
                                "date",
                                "days",
                                "type",
                                "mode",
                                "hours",
                                "action",
                                "query",
                                "command",
                            )
                        ):
                            tool_tokens[sub.lower()] = domain
    except (ImportError, AttributeError, KeyError):
        pass
    return tool_tokens


_TOOL_KWARG_SYNONYMS = _build_tool_kwarg_synonyms()
SYNONYM_GROUPS: dict[str, str] = {**COLLOQUIAL_SYNONYMS, **_TOOL_KWARG_SYNONYMS}


def extract_procedure_keywords(text: str) -> set[str]:
    """Extract lowercase semantic keywords and normalized domain markers from a trigger pattern.

    Args:
        text: The trigger pattern string.

    Returns:
        set[str]: Extracted tokens including normalized domain markers.
    """
    if not text:
        return set()
    words = re.findall(r"\b[a-z0-9_]{3,}\b", text.lower())
    kws = {w for w in words if w not in STOPWORDS}
    synonym_tokens = {SYNONYM_GROUPS[w] for w in kws if w in SYNONYM_GROUPS}
    return kws | synonym_tokens


def calculate_procedure_similarity(
    pattern1: str,
    pattern2: str,
    tools1: str | None = None,
    tools2: str | None = None,
) -> float:
    """Calculate normalized similarity between two procedure trigger patterns.

    Uses a balanced blend of Jaccard token overlap and directional containment
    (to account for short queries matching detailed master patterns with examples),
    plus a tool concordance bonus.

    Args:
        pattern1: First trigger pattern.
        pattern2: Second trigger pattern.
        tools1: Optional suggested_tools for the first procedure.
        tools2: Optional suggested_tools for the second procedure.

    Returns:
        float: Similarity score between 0.0 and 1.0.
    """
    p1 = (pattern1 or "").strip().lower()
    p2 = (pattern2 or "").strip().lower()

    if not p1 or not p2:
        return 0.0
    if p1 == p2:
        return 1.0

    kws1 = extract_procedure_keywords(p1)
    kws2 = extract_procedure_keywords(p2)

    if not kws1 or not kws2:
        return 0.0

    intersection = kws1 & kws2
    union = kws1 | kws2

    jaccard = len(intersection) / len(union) if union else 0.0
    containment = (
        len(intersection) / min(len(kws1), len(kws2)) if kws1 and kws2 else 0.0
    )

    # Balanced blend: smooths out asymmetric length penalties while requiring strong token overlap
    score = max(jaccard, 0.50 * containment + 0.50 * jaccard)

    # Tool concordance bonus: If suggested_tools match
    if tools1 and tools2:
        t1_set = {t.strip().lower() for t in tools1.split(",") if t.strip()}
        t2_set = {t.strip().lower() for t in tools2.split(",") if t.strip()}
        shared = t1_set & t2_set
        if shared:
            # If both procedures share a specialized single-purpose tool, apply a strong bonus
            score = (
                min(1.0, score + 0.35)
                if any(t in SPECIALIZED_TOOLS for t in shared)
                else min(1.0, score + 0.15)
            )

    return round(score, 4)


def is_duplicate_procedure(
    candidate_pattern: str,
    existing_patterns: list[str],
    threshold: float = 0.70,
) -> bool:
    """Check whether a candidate trigger pattern is a duplicate of any existing pattern.

    Args:
        candidate_pattern: Trigger pattern of the candidate procedure.
        existing_patterns: List of trigger patterns from existing procedures.
        threshold: Jaccard similarity threshold for duplication (default: 0.70).

    Returns:
        bool: True if duplicate exists, False otherwise.
    """
    c_clean = (candidate_pattern or "").strip().lower()
    if not c_clean:
        return False

    for ext in existing_patterns:
        e_clean = (ext or "").strip().lower()
        if not e_clean:
            continue
        if c_clean == e_clean:
            return True
        sim = calculate_procedure_similarity(candidate_pattern, ext)
        if sim >= threshold:
            return True

    return False


def find_best_master_candidate(
    candidate: dict | str,
    live_procs: list[dict],
    min_threshold: float = 0.35,
) -> tuple[dict | None, float]:
    """Find the best matching live/master procedure for a given candidate procedure.

    Args:
        candidate: Candidate procedure dict or trigger pattern string.
        live_procs: List of existing live procedure dictionaries.
        min_threshold: Minimum similarity threshold to qualify as a match (default: 0.35).

    Returns:
        tuple[dict | None, float]: (best_master_proc, similarity_score) or (None, 0.0).
    """
    if isinstance(candidate, dict):
        cand_pattern = candidate.get("trigger_pattern", "")
        cand_tools = candidate.get("suggested_tools")
    elif isinstance(candidate, str):
        cand_pattern = candidate
        cand_tools = None
    else:
        cand_pattern = str(candidate)
        cand_tools = None

    best_proc: dict | None = None
    best_score = 0.0

    for proc in live_procs:
        # Candidate cannot be its own master
        if isinstance(candidate, dict) and candidate.get("id") and candidate.get("id") == proc.get("id"):
            continue

        score = calculate_procedure_similarity(
            cand_pattern,
            proc.get("trigger_pattern", ""),
            cand_tools,
            proc.get("suggested_tools"),
        )
        if score >= min_threshold and score > best_score:
            best_score = score
            best_proc = proc

    return (best_proc, best_score) if best_proc is not None else (None, 0.0)


def identify_cluster_master(
    cluster: list[dict],
    all_live_procs: list[dict] | None = None,
    master_id_counts: dict[int, int] | None = None,
) -> dict | None:
    """Identify which procedure in a cluster should serve as the primary Master Procedure.

    Preference criteria:
    1. A procedure that has other procedures already merged into it (highest merged_into count).
    2. A procedure with status='live' over status='extracted'.
    3. Oldest / lowest ID (established canonical baseline).

    Args:
        cluster: List of procedure records in the cluster.
        all_live_procs: Optional full list of live procedures for external reference.
        master_id_counts: Optional precomputed dict of {proc_id: count_of_merged_children}.

    Returns:
        dict | None: The designated Master procedure dict, or None if cluster is empty.
    """
    if not cluster:
        return None
    if len(cluster) == 1:
        return cluster[0]

    # Precompute merged_into counts if not supplied
    if master_id_counts is None:
        master_id_counts = {}
        try:
            from Evelyn.tools import memory_db
            con = memory_db.get_db()
            cursor = con.execute(
                "SELECT merged_into_id, COUNT(*) as cnt FROM procedures WHERE merged_into_id IS NOT NULL GROUP BY merged_into_id"
            )
            for row in cursor.fetchall():
                mid = row["merged_into_id"]
                if mid is not None:
                    master_id_counts[mid] = row["cnt"]
            con.close()
        except (sqlite3.Error, OSError, KeyError):
            pass

    # If all_live_procs supplied and no procedure in cluster has established children,
    # inspect if an external live master is the canonical master for this cluster.
    if all_live_procs:
        has_established = any(master_id_counts.get(p.get("id") or 0, 0) > 0 for p in cluster)
        if not has_established:
            for item in cluster:
                cand, _score = find_best_master_candidate(item, all_live_procs, min_threshold=0.35)
                if cand and master_id_counts.get(cand["id"], 0) > 0:
                    return cand

    def _sort_key(p: dict) -> tuple[int, int, int]:
        pid = p.get("id") or 999999
        merged_count = master_id_counts.get(pid, 0)
        is_live = 1 if p.get("status") == "live" else 0
        # Highest merged_count first (-merged_count), live first (-is_live), lowest id first (pid)
        return (-merged_count, -is_live, pid)

    sorted_cluster = sorted(cluster, key=_sort_key)
    return sorted_cluster[0]
