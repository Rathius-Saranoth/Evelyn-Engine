# procedure_matcher.py
# date created: 2026-09-03 18:09:14
# date modified: 2026-09-04 17:44:24
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

# Domain synonym dictionary mapping colloquial words to normalized domain markers
SYNONYM_GROUPS: dict[str, str] = {
    # Sleep & Journaling
    "sleep": "domain_journal",
    "sleeping": "domain_journal",
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
    "diary": "domain_journal",
    # Dreams
    "dream": "domain_dream",
    "dreams": "domain_dream",
    "dreaming": "domain_dream",
    "nightmare": "domain_dream",
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
    containment = len(intersection) / min(len(kws1), len(kws2)) if kws1 and kws2 else 0.0

    # Balanced blend: smooths out asymmetric length penalties while requiring strong token overlap
    score = max(jaccard, 0.50 * containment + 0.50 * jaccard)

    # Tool concordance bonus: If suggested_tools match exactly and non-empty, add bonus
    if tools1 and tools2:
        t1_set = {t.strip().lower() for t in tools1.split(",") if t.strip()}
        t2_set = {t.strip().lower() for t in tools2.split(",") if t.strip()}
        if t1_set and t2_set and (t1_set & t2_set):
            score = min(1.0, score + 0.15)

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

    def _sort_key(p: dict) -> tuple[int, int, int]:
        pid = p.get("id") or 999999
        merged_count = master_id_counts.get(pid, 0)
        is_live = 1 if p.get("status") == "live" else 0
        # Highest merged_count first (-merged_count), live first (-is_live), lowest id first (pid)
        return (-merged_count, -is_live, pid)

    sorted_cluster = sorted(cluster, key=_sort_key)
    return sorted_cluster[0]
