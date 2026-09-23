# tag_librarian.py
# date created: 2026-08-02 11:53:00
# date modified: 2026-09-22 19:31:21
# tags: #tag, #librarian, #taxonomy, #indexing, #obsidian, #idle_time, #rag, #chromadb

"""
tag_librarian.py — Incremental Obsidian Tag Maintenance & Taxonomy Management.

Exports:
    is_excluded_tag()                     — Checks if a tag matches protected exclusion rules (e.g. CY-YYYY/MM/DD).
    canonicalize_date_tag()               — Resolves EDTF date anchors (reduced precision / unspecified digits).
    read_document_for_classification()    — Full text when it fits; chunked subject extraction when it does not.
    classify_document_subjects()          — Blind extraction, then deterministic reconciliation (§6.1).
    verify_tags_still_apply()             — Positive-assertion staleness check, with a removal ceiling.
    determine_document_class()            — Identify a document's form from the closed class list (§4).
    apply_application_profile()           — Enforce a class's required/forbidden facets (§4).
    normalize_tag_format()                — Standardizes tags to lowercase-hyphen-slash form (taxonomy §5).
    strip_subject_duplicate_tags()        — Drops tags that merely restate a record's own subject.
    audit_single_document()               — Audits one vault note against the Master Tag Taxonomy during idle windows using Tag RAG.
    sync_master_tags_to_vector_db()       — Syncs all SQLite master tags into Chroma vector store for Tag RAG.
    index_master_tag_in_chroma()          — Upserts an individual master tag into Chroma vector store.
    delete_tag_from_chroma()              — Removes a tag from Chroma vector store.
    maintain_master_taxonomy()            — Cleans up stale/unused master tags and keeps tag counts balanced.
    seed_master_taxonomy_from_vault()     — Seeds initial master tags from current vault index.

Key config: evelyn_config.py (TAG_LIBRARIAN_EXCLUSIONS, TAG_LIBRARIAN_FORMAT_RULES, CHROMA_TAG_COLLECTION)
See also: reference/engine_architecture.md
"""

import json
import logging
import os
import re
import sqlite3
import sys
from typing import Any

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "../.."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import evelyn_config as cfg
from Evelyn.tools import backlog_drainer, chroma_rag, taxonomy_db, vault_db

logger = logging.getLogger("evelyn.tag_librarian")
from Evelyn.tools.frontmatter_utils import (
    parse_frontmatter,
    update_frontmatter_field,
    write_file_with_frontmatter,
)
from Evelyn.tools.ollama_client import query_ollama as _canonical_query_ollama
from Evelyn.tools.path_utils import to_vault_abspath

VAULT_ROOT = getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")
TAG_COLLECTION_NAME = getattr(cfg, "CHROMA_TAG_COLLECTION", "evelyn_tag_taxonomy")


def is_excluded_tag(tag: str) -> bool:
    """Check if a tag matches any protected exclusion pattern.

    Args:
        tag: Tag string to evaluate (e.g. 'CY-2026/08/02' or 'tech/python').

    Returns:
        bool: True if the tag is protected from modification/removal, False otherwise.
    """
    clean_tag = tag.strip().lstrip("#")
    exclusions = getattr(cfg, "TAG_LIBRARIAN_EXCLUSIONS", [r"^CY-\d{4}/\d{2}/\d{2}$"])

    return any(re.search(pattern, clean_tag, re.IGNORECASE) for pattern in exclusions)


def is_excluded_document(path: str) -> bool:
    """Check if a document path is excluded from Tag Librarian auditing.

    Args:
        path: Relative or absolute path to evaluate.

    Returns:
        bool: True if the document path is configured in TAG_LIBRARIAN_EXCLUDED_DOCUMENTS.
    """
    clean_path = path.replace("\\", "/").strip()
    # Exclude non-note folders and hidden files
    for prefix in ("Templates/", "templates/", "Attachments/", "attachments/", "Bases/", "bases/", "."):
        if clean_path.startswith(prefix) or f"/{prefix}" in clean_path:
            return True

    excluded_paths = getattr(cfg, "TAG_LIBRARIAN_EXCLUDED_DOCUMENTS", [])
    for ex in excluded_paths:
        clean_ex = ex.replace("\\", "/").strip()
        if clean_path == clean_ex or clean_path.endswith("/" + clean_ex):
            return True
    return False


# EDTF date-anchor recognition (taxonomy §3.8). Deliberately also matches legacy
# spellings (Cy_Yyyy/11/16, cy-2025) so every variant converges on one canonical form.
_DATE_TAG_RE = re.compile(
    r"^cy[-_]?([0-9x]{4}|yyyy)(?:[/_-]([0-9x]{2}|mm))?(?:[/_-]([0-9x]{2}|dd))?$",
    re.IGNORECASE,
)
_DATE_PLACEHOLDERS = {"YYYY": "XXXX", "MM": "XX", "DD": "XX"}


def canonicalize_date_tag(tag: str) -> str | None:
    """Resolve a tag to its canonical EDTF date-anchor form.

    Follows EDTF (ISO 8601-2:2019): reduced precision and unspecified digits are
    distinct. 'CY-2026/05' means May 2026 (no day was intended); 'CY-2026/05/XX'
    would mean a specific unknown day in May 2026. Unexpanded template literals
    (YYYY/MM/DD) are treated as unspecified digits and become X.

    Args:
        tag: Raw tag string, with or without a leading '#'.

    Returns:
        str | None: Canonical 'CY-...' form, or None if the tag is not a date anchor.
    """
    m = _DATE_TAG_RE.match(tag.strip().lstrip("#").strip())
    if not m:
        return None

    parts: list[str] = []
    for group in m.groups():
        if group is None:
            break  # Reduced precision: stop at the first absent component.
        token = group.upper()
        parts.append(_DATE_PLACEHOLDERS.get(token, token))

    return "CY-" + "/".join(parts) if parts else None


def normalize_tag_format(tag: str) -> str:
    """Normalize a tag to the canonical vault format.

    Implements .agents/rules/vault-tag-taxonomy.md §5: lowercase always, hyphens
    join words, slashes join levels. There is deliberately no entity/concept
    branch — proper nouns follow the same rule as concepts, which is what removes
    any way for one term to fork into 'ai' and 'Ai'.

    Date anchors (§3.8) are the sole exemption and are routed to
    canonicalize_date_tag(). Administrative namespaces listed in
    TAG_LIBRARIAN_EXCLUSIONS are returned untouched.

    Args:
        tag: Raw tag string (e.g. '#Tech/Ai', 'DungeonCrawlerCarl', 'Cy_Yyyy/11/16').

    Returns:
        str: Normalized tag string, or '' if nothing survives normalization.
    """
    clean = tag.strip().lstrip("#").strip()
    if not clean:
        return ""

    # 1. Date anchors bypass §5 entirely.
    date_form = canonicalize_date_tag(clean)
    if date_form:
        return date_form
    if is_excluded_tag(clean):
        return clean

    # 2. Strip legacy noise prefixes (kw/, ctx/).
    lowered = clean.lower()
    if lowered.startswith("kw/"):
        clean = clean[3:].strip()
    elif lowered.startswith("ctx/"):
        clean = clean[4:].strip()

    if not clean or is_excluded_tag(clean):
        return clean

    # 3. Apply §5 to each hierarchy level.
    norm_parts: list[str] = []
    for part in clean.split("/"):
        p = part.strip()
        if not p:
            continue
        # Split CamelCase first. Two rules, in order: an acronym run followed by a
        # word ('UVMapping' -> 'UV Mapping'), then a lowercase/uppercase boundary
        # ('DungeonCrawler' -> 'Dungeon Crawler'). Digits are deliberately NOT a
        # boundary, so '3DPrinting' yields '3d-printing' rather than '3-d-printing'.
        p = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", p)
        p = re.sub(r"([a-z])([A-Z])", r"\1 \2", p)
        words: list[str] = []
        for raw_word in re.split(r"[\s_-]+", p):
            # Drop characters outside the permitted set; unicode letters survive.
            word = "".join(ch for ch in raw_word if ch.isalnum()).lower()
            if word:
                words.append(word)
        if words:
            norm_parts.append("-".join(words))

    return "/".join(norm_parts)


def strip_subject_duplicate_tags(tags: list[str], subject: str) -> list[str]:
    """Drop tags that merely restate the record's own subject.

    The subject field is the single source of truth for who or what a record concerns.
    A tag repeating it stores the same fact twice — and because a column holds one value
    while a free-text tag does not, that duplication is how one identity fragments into
    several spellings in the vocabulary.

    The test is relational, not a name list: a tag is dropped only when it matches *this*
    record's subject. A record about one party tagged with another party's name is a real
    cross-reference the subject field cannot express, and survives.

    Args:
        tags: Tag strings, raw or normalized.
        subject: The record's subject value.

    Returns:
        list[str]: Tags with subject-duplicates removed, order preserved.
    """
    subject_form = normalize_tag_format(subject or "")
    if not subject_form:
        return list(tags)
    return [t for t in tags if normalize_tag_format(t) != subject_form]


def _extract_document_skeleton(body: str, gist: str = "") -> str:
    """Build a structured document skeleton for LLM classification.

    Instead of a raw character slice (which cuts off mid-sentence on large docs),
    this extracts the semantic outline: all headings, a stored gist, and the
    opening prose paragraph. Gives the model full thematic coverage in far fewer
    tokens — critical for multi-section overview documents.

    Args:
        body: Raw markdown body text (post-frontmatter).
        gist: Pre-computed semantic summary from vault DB (may be empty).

    Returns:
        str: Structured skeleton string for inclusion in the LLM prompt.
    """
    parts: list[str] = []

    # 1. Vault gist (semantic summary already computed by indexer)
    # Skip if the gist is clearly a markdown link list (ToC noise from indexer)
    if gist and gist.strip() and not gist.strip().startswith("- ["):
        parts.append(f"[Semantic Summary]: {gist.strip()}")

    # 2. Headings outline (full topic map — cheap in tokens, high signal)
    headings = re.findall(r"^(#{1,3} .+)", body, re.MULTILINE)
    if headings:
        # Deduplicate while preserving order (e.g. ToC anchor dupes)
        seen: set[str] = set()
        unique_headings: list[str] = []
        for h in headings:
            if h not in seen:
                seen.add(h)
                unique_headings.append(h)
        parts.append("[Document Structure]:\n" + "\n".join(unique_headings))

    # 3. Opening paragraph (document intent / introduction)
    # Skip headings, list items, and markdown link lines (ToC / bullet noise)
    non_heading_lines = [
        line for line in body.splitlines()
        if line.strip()
        and not line.startswith("#")
        and not line.lstrip().startswith("-")
        and not line.lstrip().startswith("[")
        and not line.lstrip().startswith("|")
    ]
    if non_heading_lines:
        opening = " ".join(non_heading_lines[:6])  # ~2-3 sentences
        parts.append(f"[Opening Content]: {opening[:400]}")

    # 4. Fallback: raw slice if no structure could be extracted
    if not parts:
        parts.append(body[:1200])

    return "\n\n".join(parts)


# A document is classified from what it says, not from a guess at what it says.
# Short notes go whole; long ones are read in chunks and their subjects merged. The
# previous approach sent a skeleton of headings for every document regardless of size,
# which threw away the full text of the ~73% that would have fitted comfortably.
FULL_TEXT_LIMIT = 6000      # chars a single prompt carries comfortably
MAX_CHUNKS_READ = 12        # ceiling on map calls for one document
CHUNK_SIZE = 4000


def _extract_chunk_subjects(chunk: str, title: str) -> list[str]:
    """Ask for the subjects present in one chunk of a document.

    Deliberately narrow: this pass names what the chunk is about and nothing else. It
    does not see the vocabulary, the existing tags, or the format rules, so it cannot be
    pulled into deciding the document's tags from a fragment of it.

    Args:
        chunk: A slice of the document body.
        title: Document title, for orientation only.

    Returns:
        list[str]: Lowercase subject phrases, empty on any failure.
    """
    system = (
        "List the distinct subjects this text covers. Output ONLY a JSON array of short "
        'lowercase noun phrases, e.g. ["floodplain management", "drone operations"]. '
        "No commentary. If the text covers one subject, return one item."
    )
    try:
        raw = _canonical_query_ollama(
            prompt=f"Document: {title}\n\n{chunk}",
            system=system,
            options={"temperature": 0.0, "num_predict": 256},
            timeout=45,
            think=False,
        )
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return []
        return [str(x).strip().lower() for x in json.loads(match.group(0)) if str(x).strip()]
    except Exception:  # noqa: BLE001
        return []


def read_document_for_classification(body: str, gist: str, title: str) -> str:
    """Build the document view the classifier reasons over.

    Three sizes, and the whole point is that the small ones stop being guessed at:

    - **Fits in one prompt** — send the body verbatim. Most notes are here.
    - **Too long** — chunk it, name the subjects in each chunk, and present the merged
      list alongside the structural skeleton. The model then reasons over what the
      document actually covers rather than over its table of contents.
    - **Very long** — the same, but chunks are sampled evenly to a fixed ceiling, so the
      number of calls stays bounded no matter how large the note is.

    Each call sees a bounded slice, which is what keeps this away from the timeout that
    the previous design hit and then "fixed" by degrading the classifier.

    Args:
        body: Markdown body, post-frontmatter.
        gist: Stored semantic summary, may be empty.
        title: Document title.

    Returns:
        str: The document view for the classification prompt.
    """
    from Evelyn.tools.web_reader import chunk_text

    body = (body or "").strip()
    if not body:
        return _extract_document_skeleton(body, gist)

    if len(body) <= FULL_TEXT_LIMIT:
        return f"[Full document]\n{body}"

    chunks = chunk_text(body, chunk_size=CHUNK_SIZE, overlap=400)
    if len(chunks) > MAX_CHUNKS_READ:
        step = len(chunks) / MAX_CHUNKS_READ
        chunks = [chunks[int(i * step)] for i in range(MAX_CHUNKS_READ)]

    subjects: list[str] = []
    for chunk in chunks:
        for subject in _extract_chunk_subjects(chunk, title):
            if subject not in subjects:
                subjects.append(subject)

    parts = [_extract_document_skeleton(body, gist)]
    if subjects:
        parts.append(
            "[Subjects found by reading the document in full]\n"
            + "\n".join(f"- {s}" for s in subjects)
        )
    return "\n\n".join(parts)


# Reconciliation thresholds. A phrase near an existing term becomes that term; a phrase
# near nothing is a proposal, never an automatic addition (§6.1).
SUBJECT_LOOKUP_TOP_K = getattr(cfg, "TAG_LIBRARIAN_TOP_K_TAGS", 10)

# Acceptance bands for vector matching. A single threshold cannot express the difference
# between "certainly this term" and "possibly this term", and the middle is where every
# wrong link lived: at 0.35, measured, 70% of genuinely off-topic phrases were force-linked
# to some term. Below ACCEPT a match is taken outright; above REJECT it is a new concept and
# becomes a proposal; between them the phrase is ambiguous and needs a decision rather than
# a number. Re-measure both against a real evaluation set — these are starting points.
SUBJECT_ACCEPT_DISTANCE = getattr(cfg, "TAG_SUBJECT_ACCEPT_DISTANCE", 0.10)
SUBJECT_REJECT_DISTANCE = getattr(cfg, "TAG_SUBJECT_REJECT_DISTANCE", 0.30)
# Two candidates this close together are not distinguishable by distance; measured, the
# margin does not separate right from wrong (0.108 correct vs 0.092 wrong), so it routes to
# review rather than deciding.
SUBJECT_MARGIN_GUARD = getattr(cfg, "TAG_SUBJECT_MARGIN_GUARD", 0.02)
# Whole-string similarity above which a near-exact surface form is accepted without vectors.
SUBJECT_FUZZY_CUTOFF = getattr(cfg, "TAG_SUBJECT_FUZZY_CUTOFF", 92)
SUBJECT_SUGGESTION_HEADROOM = 5    # new terms a single document may contribute


def normalize_subject_phrase(phrase: str) -> str:
    """Format a natural-language subject phrase as a tag, without decomposing it.

    Deliberately does NOT split multi-word phrases into atoms. "obstructive sleep apnea" is
    one diagnosis, not three coordinates, and lexical splitting destroys exactly the terms
    of art worth keeping (§5's test: are the halves independently meaningful?). Whether a
    phrase collapses onto existing atoms is reconciliation's decision, made against the
    registry, not a guess made from the string.

    Args:
        phrase: A subject phrase as written by the extraction pass.

    Returns:
        str: The phrase in §5 format, or '' if nothing survives.
    """
    return normalize_tag_format(phrase.replace("/", " ").strip())


def _registry_fingerprint() -> tuple[int, float]:
    """Cheap signature of the registry's current state, for cache invalidation.

    Returns:
        tuple[int, float]: (term count, latest update timestamp).
    """
    try:
        conn = sqlite3.connect(getattr(cfg, "VAULT_DB_PATH", ""), timeout=30.0)
        try:
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(updated_at), 0) FROM master_tag_taxonomy"
            ).fetchone()
        finally:
            conn.close()
        return (int(row[0]), float(row[1])) if row else (0, 0.0)
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return (0, 0.0)


_SURFACE_CACHE: tuple[tuple[int, float], dict[str, str]] | None = None


def _registry_surface_forms() -> dict[str, str]:
    """Map every searchable surface form to the term it denotes.

    Both preferred terms and their recorded equivalences are keys, because a document that
    phrases a subject the retired way should still reach the preferred term. Keys are
    skeletons — separators and inflection removed — so `sleep-hygiene`, `sleep hygiene` and
    `Sleep Hygiene` are one entry.

    Cached against a (count, latest-update) fingerprint rather than an explicit invalidation
    call, so a write path that forgets to invalidate cannot serve a stale vocabulary — the
    failure mode would be silent and would look exactly like a classifier bug.

    Returns:
        dict[str, str]: surface skeleton -> canonical term.
    """
    from Evelyn.tools.tag_synonym import singularize, skeleton

    global _SURFACE_CACHE
    fingerprint = _registry_fingerprint()
    if _SURFACE_CACHE is not None and _SURFACE_CACHE[0] == fingerprint:
        return _SURFACE_CACHE[1]

    forms: dict[str, str] = {}
    try:
        for entry in taxonomy_db.get_master_tags():
            term = entry.get("tag") or ""
            if not term or is_excluded_tag(term):
                continue
            for key in (skeleton(term), singularize(skeleton(term))):
                forms.setdefault(key, term)
        for alias, canonical in taxonomy_db.get_aliases().items():
            if not alias or not canonical:
                continue
            for key in (skeleton(alias), singularize(skeleton(alias))):
                forms.setdefault(key, canonical)
    except (sqlite3.Error, OSError, ValueError, RuntimeError) as exc:
        logger.warning("[TAG LIBRARIAN] Could not load registry surface forms: %s", exc)
    _SURFACE_CACHE = (fingerprint, forms)
    return forms


def _lexical_lookup(candidate: str, surfaces: dict[str, str]) -> str | None:
    """Resolve a phrase against the vocabulary by string, not by embedding.

    A controlled vocabulary with recorded equivalences is a dictionary, and a term matching
    itself is a lookup rather than a nearest-neighbour search. Skipping this step was the
    original defect: the hot path went straight to the vector store, so a registry holding
    the exact term still returned whatever happened to embed closest.

    Two passes. Exact on the skeleton, then whole-string fuzzy for typos and inflections the
    skeleton misses. Fuzzy is bounded by a high cutoff because a loose match here is
    indistinguishable from a wrong one, and wrong is worse than unresolved — an unresolved
    phrase becomes a proposal a human sees.

    Args:
        candidate: A normalized phrase.
        surfaces: Surface skeleton -> canonical term.

    Returns:
        str | None: The term, or None if the dictionary does not hold it.
    """
    from rapidfuzz import fuzz, process

    from Evelyn.tools.tag_synonym import singularize, skeleton

    if not surfaces:
        return None
    key = skeleton(candidate)
    if key in surfaces:
        return surfaces[key]
    if singularize(key) in surfaces:
        return surfaces[singularize(key)]

    hit = process.extractOne(
        key, surfaces.keys(), scorer=fuzz.WRatio, score_cutoff=SUBJECT_FUZZY_CUTOFF
    )
    return surfaces[hit[0]] if hit else None


def _vector_lookup(phrase: str) -> tuple[str | None, float, float]:
    """Retrieve the nearest term to a phrase, with the margin over its runner-up.

    Retrieval only — this reports what is near and how clearly, and does not decide. The
    margin matters because a confident match and a coin-flip between two plausible terms are
    indistinguishable from the top distance alone.

    Candidates are pooled by canonical term, so a term indexed under several surface forms
    competes once, at its best-matching form.

    Args:
        phrase: The raw extracted phrase.

    Returns:
        tuple[str | None, float, float]: (nearest term, its distance, margin over the next
        distinct term). Distance is 1.0 and margin 0.0 when nothing is retrieved.
    """
    try:
        results = chroma_rag.query_collection(
            phrase, TAG_COLLECTION_NAME, n_results=SUBJECT_LOOKUP_TOP_K
        )
    except (sqlite3.Error, OSError, ValueError, RuntimeError) as exc:
        logger.warning("[TAG LIBRARIAN] Reconciliation lookup failed for %r: %s", phrase, exc)
        return None, 1.0, 0.0

    best: dict[str, float] = {}
    for r in results:
        tag = (r.get("metadata") or {}).get("tag")
        if not tag:
            continue
        distance = float(r.get("distance", 1.0))
        if distance < best.get(tag, 2.0):
            best[tag] = distance
    if not best:
        return None, 1.0, 0.0

    ranked = sorted(best.items(), key=lambda kv: kv[1])
    top_tag, top_distance = ranked[0]
    margin = (ranked[1][1] - top_distance) if len(ranked) > 1 else 1.0
    return top_tag, top_distance, margin


def reconcile_subjects(
    phrases: list[str], existing: list[str], match_distance: float | None = None
) -> tuple[list[str], list[str]]:
    """Map extracted phrases onto the registry, or hold them back as proposals.

    The extraction pass never sees the vocabulary, so its output is free of the
    pre-coordinate shapes it would otherwise imitate — but it is also unaligned. This is
    where alignment happens, and it happens by measurement rather than by asking a model to
    comply with a format.

    A phrase close to a registered term *becomes* that term. A phrase close to nothing is a
    proposal for review, never an automatic addition: a vocabulary any document can extend
    is not a controlled vocabulary (§6.1).

    Args:
        phrases: Subject phrases from the extraction pass.
        existing: Tags already on the document; matches against these are not re-added.
        match_distance: Cosine distance below which a phrase is considered an existing term.

    Returns:
        tuple[list[str], list[str]]: (terms to apply, phrases to propose).
    """
    from Evelyn.tools.tag_synonym import FUNCTION_WORDS

    reject = SUBJECT_REJECT_DISTANCE if match_distance is None else match_distance
    known = set(existing)
    applied: list[str] = []
    proposals: list[str] = []
    surfaces = _registry_surface_forms()

    for phrase in phrases:
        candidate = normalize_subject_phrase(phrase)
        if not candidate or is_excluded_tag(candidate) or candidate in known:
            continue

        # Stage 1 — the vocabulary is a dictionary. Look the phrase up in it.
        match = _lexical_lookup(candidate, surfaces)

        # Stage 1b — post-coordination applies to the query too. The registry holds atoms,
        # but extraction emits phrases, so `dream-journaling` was being matched whole against
        # a vocabulary that holds `dream` and `journaling` separately and merely looked
        # *near* something. Splitting the phrase and resolving each part recovers both.
        #
        # This can only ever apply terms already in the registry: a part that resolves to
        # nothing is dropped rather than proposed, so the curated vocabulary is itself the
        # filter. `personal-reflection` contributes `reflection` and discards `personal`,
        # which was rejected from the vocabulary precisely because it names nothing.
        if match is None and "-" in candidate:
            parts = [
                resolved
                for word in candidate.split("-")
                if word and word not in FUNCTION_WORDS and len(word) > 2
                and (resolved := _lexical_lookup(word, surfaces))
            ]
            if parts:
                for term in parts:
                    if term not in known and term not in applied:
                        applied.append(term)
                continue

        # Stage 2 — vectors, only for what the dictionary missed.
        if match is None:
            match, distance, margin = _vector_lookup(phrase)
            if match is not None and (
                distance > reject or margin < SUBJECT_MARGIN_GUARD
                or distance > SUBJECT_ACCEPT_DISTANCE
            ):
                # Near enough to retrieve, not near enough to assert. Deciding this by
                # distance is what force-linked off-topic phrases to plausible neighbours.
                match = None

        if match:
            if match not in known and match not in applied:
                applied.append(match)
        elif candidate not in proposals:
            proposals.append(candidate)

    return applied, proposals


def classify_document_subjects(
    body: str, gist: str, title: str, existing: list[str], headroom: int | None = None
) -> tuple[list[str], list[str]]:
    """Derive a document's subjects, then align them to the vocabulary.

    Two stages, and the separation is the point. The model is asked only what the document
    is *about* — it never sees the existing tags or the registry, so it cannot imitate their
    shapes. Measured on this vault, the same model shown existing tags produced eight
    different terms for sleep across five documents and never the bare atom; shown only the
    documents, it produced the identical phrase three times.

    Alignment is then deterministic. Format is enforced rather than requested, and new terms
    are held back for approval rather than minted.

    Note that this pass can only ADD. Asking a document what it is about yields no signal
    about what it is *not* about, so removal is not inferable here and stays a supervised
    operation — which removes the failure mode that disabled this librarian by construction.

    Args:
        body: Document body.
        gist: Stored summary, may be empty.
        title: Document title.
        existing: Tags already on the document.
        headroom: New terms this document may contribute beyond what it already has.

    Returns:
        tuple[list[str], list[str]]: (terms to apply, proposals for review).
    """
    cap = SUBJECT_SUGGESTION_HEADROOM if headroom is None else headroom
    view = read_document_for_classification(body, gist, title)

    phrases = _extract_chunk_subjects(view, title)
    if not phrases:
        return [], []

    applied, proposals = reconcile_subjects(phrases, existing)
    return applied[: len(existing) + cap], proposals[:cap]


# A pass may retire at most this share of a document's tags. Beyond it the verdict is
# treated as a malfunction rather than an instruction — no legitimate edit retires most
# of a document's catalogue at once. A small absolute allowance sits underneath it, because
# a ratio is meaningless on a three-tag note where two are genuinely wrong.
STALE_REMOVAL_MAX_RATIO = 0.34
STALE_REMOVAL_ALWAYS_ALLOWED = 2


def verify_tags_still_apply(body: str, title: str, tags: list[str]) -> list[str]:
    """Ask which of a document's existing tags no longer describe it.

    Separate from subject extraction on purpose. Extraction answers "what is this about",
    which says nothing about what a document is *not* about — so removal cannot be inferred
    from it, and inferring it from silence is what collapsed a 36-tag document to three.

    This pass asks the opposite question and requires a **positive assertion**: the model
    names the tags that are stale. A tag it fails to mention is kept, so a truncated,
    malformed or empty answer removes nothing.

    Args:
        body: Document body.
        title: Document title.
        tags: Every tag currently on the document. All of them are shown (§7.1 rule 2).

    Returns:
        list[str]: Tags the model asserts no longer apply, empty on any failure.
    """
    auditable = [t for t in tags if t and not is_excluded_tag(t)]
    if not auditable:
        return []

    system = (
        "For each tag, decide whether the document is genuinely about that subject. "
        "Be conservative: keep a tag if it is even loosely relevant. Only list a tag as "
        "stale when the document clearly has nothing to do with it.\n"
        'Output ONLY JSON: {"stale": []}'
    )
    listing = "\n".join(f"- {t}" for t in auditable)
    # The whole document, by the same route classification uses. Judging 33 tags against the
    # first quarter of a long note marks everything covered later as stale — measured on a
    # 23,000-character overview, a truncated view condemned ten legitimate sections. §7.1
    # rule 2 is about the document as much as the tag list.
    view = read_document_for_classification(body, "", title)
    user = (
        f"Document: {title}\n\n--- DOCUMENT ---\n{view}\n\n"
        f"--- TAGS ({len(auditable)}) ---\n{listing}\n\nOutput JSON now."
    )

    try:
        raw = _canonical_query_ollama(
            prompt=user, system=system,
            options={"temperature": 0.0, "num_predict": 512}, timeout=90, think=False,
        )
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return []
        claimed = json.loads(match.group(0)).get("stale", []) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TAG LIBRARIAN] Staleness check failed for %s: %s", title, exc)
        return []

    # Only tags the document actually carries, and never a protected one.
    stale = [t for t in (normalize_tag_format(str(c)) for c in claimed) if t in set(auditable)]

    over_ratio = len(stale) / len(auditable) > STALE_REMOVAL_MAX_RATIO
    if stale and over_ratio and len(stale) > STALE_REMOVAL_ALWAYS_ALLOWED:
        logger.warning(
            "[TAG LIBRARIAN] %s: %d/%d tags called stale, above the %.0f%% ceiling — "
            "treating as a malfunction and removing nothing.",
            title, len(stale), len(auditable), STALE_REMOVAL_MAX_RATIO * 100,
        )
        return []
    return stale


# The application profile (§4) in the DCMI sense: which facets a class of document requires,
# permits, or forbids. The class is the only part a model decides; everything downstream is a
# table lookup, so "forbidden" is enforced rather than requested.
REQUIRED, OPTIONAL, FORBIDDEN = "required", "optional", "forbidden"

FACET_PROFILE: dict[str, dict[str, str]] = {
    "reference":     {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": FORBIDDEN, "time": OPTIONAL},
    "guide":         {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": FORBIDDEN, "time": OPTIONAL},
    "manual":        {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": FORBIDDEN, "time": OPTIONAL},
    "overview":      {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": FORBIDDEN, "time": OPTIONAL},
    "moc":           {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": FORBIDDEN, "time": OPTIONAL},
    "list":          {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": FORBIDDEN, "time": OPTIONAL},
    "log":           {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": OPTIONAL,  "time": REQUIRED},
    "report":        {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": OPTIONAL,  "time": REQUIRED},
    "journal-entry": {"motif": OPTIONAL,  "setting": OPTIONAL,  "event": OPTIONAL,  "time": REQUIRED},
    "dream":         {"motif": REQUIRED,  "setting": REQUIRED,  "event": OPTIONAL,  "time": REQUIRED},
    "creative":      {"motif": REQUIRED,  "setting": OPTIONAL,  "event": OPTIONAL,  "time": OPTIONAL},
    # A film or novel carries motif; a scanned tax return or court order is `media` too (a PDF
    # wrapper card, DCMI sub-type Text) and carries none. So motif is permitted, not demanded.
    "media":         {"motif": OPTIONAL,  "setting": OPTIONAL,  "event": OPTIONAL,  "time": OPTIONAL},
    "recipe":        {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": FORBIDDEN, "time": OPTIONAL},
    "notes":         {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": FORBIDDEN, "time": OPTIONAL},
    # Entity cards ("what is it / how does it relate to me"): contacts, pets, personas, D&D
    # characters and places, software. The second-largest class in the vault (Pass 1 review).
    "profile":       {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": FORBIDDEN, "time": OPTIONAL},
    # Auto-generated ghost stubs: the type tag and nothing else until a human fills them in.
    "stub":          {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": FORBIDDEN, "time": OPTIONAL},
}
DOCUMENT_CLASSES = sorted(FACET_PROFILE)


def determine_document_class(body: str, title: str, path: str = "") -> str:
    """Identify which class of document this is, from a closed list (§4).

    The narrowest question in the pipeline and the only part of the profile a model decides:
    everything the class implies is then a table lookup. Asked separately from subject
    extraction because form and subject are orthogonal — conflating them is what produced
    tags describing a note's shape as though they were topics.

    Args:
        body: Document body.
        title: Document title.
        path: Vault-relative path; folders are often the strongest signal of form.

    Returns:
        str: A class from DOCUMENT_CLASSES, or '' if it could not be determined.
    """
    options = "  ".join(DOCUMENT_CLASSES)
    system = (
        "Classify the FORM of this document — what kind of thing it is, not what it is about.\n"
        f"Choose exactly one of: {options}\n"
        "Output ONLY that single word. No punctuation, no explanation."
    )
    user = f"Path: {path}\nTitle: {title}\n\n{body[:2000]}\n\nOne word:"

    try:
        raw = _canonical_query_ollama(
            prompt=user, system=system,
            options={"temperature": 0.0, "num_predict": 24}, timeout=45, think=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[TAG LIBRARIAN] Class determination failed for %s: %s", title, exc)
        return ""

    answer = normalize_tag_format(raw.strip().split()[0] if raw.strip() else "")
    return answer if answer in FACET_PROFILE else ""


def apply_application_profile(
    doc_class: str, tags: list[str]
) -> tuple[list[str], list[str], list[str]]:
    """Enforce a class's facet profile against a document's tags (§4).

    Returns what the profile requires be added, what it forbids and must go, and which
    required facets the document still lacks. The last of those is a gap to report rather
    than something to invent: a `dream` with no motif needs one, but guessing which motif
    is subject analysis, not cataloguing.

    Args:
        doc_class: A class from DOCUMENT_CLASSES.
        tags: The document's current tags.

    Returns:
        tuple[list[str], list[str], list[str]]: (to add, to remove, unmet requirements).
    """
    profile = FACET_PROFILE.get(doc_class)
    if not profile:
        return [], [], []

    add: list[str] = []
    remove: list[str] = []
    gaps: list[str] = []

    type_tag = f"type/{doc_class}"
    # A DCMI sub-type (`type/media/text`) is the class's type tag in a more specific form.
    if not any(t == type_tag or t.startswith(type_tag + "/") for t in tags):
        add.append(type_tag)
    # One type facet only — any other is wrong about the document's form.
    remove.extend(
        t for t in tags
        if t.startswith("type/") and t != type_tag and not t.startswith(type_tag + "/")
    )

    for facet, rule in profile.items():
        if facet == "time":
            present = any(t.startswith("CY-") for t in tags)
        else:
            present = any(t.startswith(f"{facet}/") for t in tags)

        if rule == FORBIDDEN and present:
            remove.extend(
                t for t in tags
                if (t.startswith("CY-") if facet == "time" else t.startswith(f"{facet}/"))
            )
        elif rule == REQUIRED and not present:
            gaps.append(facet)

    return add, [t for t in remove if not is_excluded_tag(t)], gaps


def parse_frontmatter_tags(content: str) -> tuple[list[str], str]:
    """Extract frontmatter tags and return (tags_list, body_content).

    Args:
        content: Raw markdown note text.

    Returns:
        tuple[list[str], str]: List of current tags and remaining document text.
    """
    meta, body = parse_frontmatter(content)
    raw_tags = meta.get("tags", [])
    if isinstance(raw_tags, str):
        tags = [t.strip().strip("'\"#") for t in raw_tags.split(",") if t.strip().strip("'\"#")]
    elif isinstance(raw_tags, (list, set, tuple)):
        tags = [str(t).strip().strip("'\"#") for t in raw_tags if str(t).strip().strip("'\"#")]
    else:
        tags = []
    return tags, body


def update_frontmatter_tags(content: str, updated_tags: list[str]) -> str:
    """Update or inject YAML frontmatter tags in markdown content cleanly.

    Args:
        content: Original markdown file content.
        updated_tags: Deduplicated list of normalized tag strings.

    Returns:
        str: Updated markdown content with formatted frontmatter.
    """
    return update_frontmatter_field(content, "tags", updated_tags)


# =============================================================================
# Tag RAG Vector Store & Chroma Synchronization
# =============================================================================

def _build_tag_embedding_doc(tag: str) -> str:
    """Return the text a term is embedded as: its bare surface form, and nothing else.

    This function previously emitted four labelled lines — `Tag:`, `Category:`, `Hierarchy:`,
    `Scope & Scope Description:`. That boilerplate is identical across every term, so it
    contributes a large shared component to every vector which is orthogonal to a one- or
    two-word query. It drags every cosine distance down and compresses the spread the caller
    then thresholds on.

    Measured over the full registry, querying each term with its own surface form:

    | indexed as              | rank-1 | self-distance | terms failing to match themselves |
    |-------------------------|--------|---------------|-----------------------------------|
    | labelled prose block    | 0.879  | 0.335         | 31.5%                             |
    | bare surface form       | 0.999  | 0.000         | 0%                                |

    `sleep` scored 0.370 against itself under the old format — outside the acceptance
    threshold. Worse, correct matches averaged a *higher* distance (0.353) than wrong ones
    (0.345): the number carried no discriminative signal at all.

    The category and description are deliberately excluded rather than merely shortened. Both
    are generated rather than authored, and were measurably wrong — the description stored for
    `sleep` read "Obsidian notes tagged under sleep/apnea-troubleshooting", which embedded a
    term for `sleep` partly about apnea troubleshooting. They remain in SQLite for display and
    for prompting; they do not belong in the vector.

    Args:
        tag: A registry term or alias.

    Returns:
        str: De-slugified lowercase surface form, e.g. `sleep-hygiene` -> "sleep hygiene".
    """
    return tag.replace("/", " ").replace("-", " ").replace("_", " ").strip().lower()


def index_tag_in_chroma(tag: str, category: str = "", description: str = "",
                        usage_count: int = 0) -> bool:
    """Index a single tag into the Chroma vector database via the staging queue.

    Args:
        tag: The raw or formatted tag string to index.
        category: The top-level category domain for the tag.
        description: Brief semantic description of the tag concept.
        usage_count: How many times this tag is currently referenced.

    Returns:
        bool: True on successful enqueue, False on failure.
    """
    clean_tag = normalize_tag_format(tag)
    if not clean_tag or is_excluded_tag(clean_tag):
        return False

    try:
        doc_id = f"tag::{clean_tag}"
        doc_text = _build_tag_embedding_doc(clean_tag)
        meta = {
            "tag": clean_tag,
            "category": category or (clean_tag.split("/")[0] if "/" in clean_tag else "general"),
            "description": description or "",
            "usage_count": usage_count,
            "type": "master_tag"
        }
        return chroma_rag.enqueue_upsert(doc_id, doc_text, collection_name=TAG_COLLECTION_NAME, extra_metadata=meta)
    except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
        print(f"[TAG LIBRARIAN] Chroma tag indexing enqueue failed for #{clean_tag}: {e}")
        return False


index_master_tag_in_chroma = index_tag_in_chroma


def delete_tag_from_chroma(tag: str) -> bool:
    """Remove a tag from the Chroma tag taxonomy collection via staging queue.

    Args:
        tag: Tag string to delete.

    Returns:
        bool: True on success, False on failure.
    """
    clean_tag = normalize_tag_format(tag)
    try:
        doc_id = f"tag::{clean_tag}"
        return chroma_rag.enqueue_delete(doc_id, collection_name=TAG_COLLECTION_NAME)
    except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
        print(f"[TAG LIBRARIAN] Chroma tag deletion enqueue failed for #{clean_tag}: {e}")
        return False


def sync_master_tags_to_vector_db() -> int:
    """Rebuild the vector index from the registry, one vector per surface form.

    The index is a **derived cache**, not a store. It is rebuilt from SQLite and never
    repaired in place: a vector can outlive its metadata record, and a collection patched
    term-by-term accumulates orphans that stay invisible to `get()` while still ranking in
    `query()`. If it disagrees with `master_tag_taxonomy`, the registry wins and this is
    re-run.

    Every equivalence is indexed as its own vector carrying the *canonical* term in metadata,
    so a document phrased like a retired variant still retrieves the preferred term. A term's
    several surface forms therefore compete independently, and the caller keeps the best.

    Returns:
        int: Surface forms enqueued (terms plus equivalences).
    """
    master_tags = taxonomy_db.get_master_tags()
    if not master_tags:
        return 0

    enqueued_count = 0
    canonical_terms: set[str] = set()
    for m in master_tags:
        tag = normalize_tag_format(m["tag"])
        if not tag or is_excluded_tag(tag):
            continue
        canonical_terms.add(tag)
        meta = {
            "tag": tag,
            "category": m.get("category", tag.split("/")[0] if "/" in tag else "general"),
            "description": m.get("description", ""),
            "usage_count": m.get("usage_count", 0),
            "type": "master_tag",
            "surface": "preferred",
        }
        if chroma_rag.enqueue_upsert(
            f"tag::{tag}", _build_tag_embedding_doc(tag),
            collection_name=TAG_COLLECTION_NAME, extra_metadata=meta,
        ):
            enqueued_count += 1

    for alias, canonical in taxonomy_db.get_aliases().items():
        variant = normalize_tag_format(alias)
        target = normalize_tag_format(canonical or "")
        if not variant or not target or target not in canonical_terms:
            continue  # an equivalence pointing at no live term indexes nothing
        if variant in canonical_terms:
            continue  # already indexed as a preferred form
        meta = {
            "tag": target,
            "category": target.split("/")[0] if "/" in target else "general",
            "description": "",
            "usage_count": 0,
            "type": "master_tag",
            "surface": "alternate",
        }
        if chroma_rag.enqueue_upsert(
            f"alias::{variant}", _build_tag_embedding_doc(variant),
            collection_name=TAG_COLLECTION_NAME, extra_metadata=meta,
        ):
            enqueued_count += 1

    return enqueued_count


def query_ollama(prompt: str, system_prompt: str = "") -> str:
    """Query local Ollama instance synchronously for LLM reasoning.

    Args:
        prompt: User prompt string.
        system_prompt: Optional system instruction.

    Returns:
        str: Raw response text from model.
    """
    # think=False deliberately. Classification here is rule-following extraction — the
    # reasoning lives in the prompt — and with thinking enabled this model spends the whole
    # token budget deliberating and returns EMPTY content: measured at 2048/2048 tokens
    # consumed, 40s, nothing emitted. That is not a quality-versus-speed trade (§7.1 rule 5
    # forbids those); it is the difference between an answer and no answer.
    return _canonical_query_ollama(
        prompt=prompt,
        system=system_prompt if system_prompt else None,
        options={"temperature": 0.1, "num_predict": 1024},
        timeout=90,
        think=False,
    )


def audit_document_tags(
    content: str,
    path: str = "",
    vault_root: str | None = None,
    enable_llm: bool = False,
    parent_tags: list[str] | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Audit and normalize tags in markdown content in-memory.

    Deterministic rules:
    - Normalizes multi-word formatting (e.g. concept hyphens, entity TitleCase underscores).
    - Cleans noise prefixes ('kw/', 'ctx/').
    - Inherits parent collection tags if provided and missing.
    - Preserves protected date tags (CY-YYYY/MM/DD).

    Semantic LLM Tagging (when enable_llm=True):
    - Retrieves candidate master tags via Tag RAG.
    - Evaluates nested hierarchy classification via Ollama.
    - Updates Master Tag Taxonomy in SQLite and Chroma if new tags minted.

    Args:
        content: Raw markdown note text.
        path: Relative path of the document.
        vault_root: Optional vault root directory.
        enable_llm: Whether to invoke Ollama for semantic Tag RAG auditing.
        parent_tags: Optional list of tags inherited from parent collection/index.

    Returns:
        tuple[bool, str, dict[str, Any]]: (changed, updated_content, details_dict)
    """
    if not content:
        return False, content, {"status": "empty"}

    current_tags, body = parse_frontmatter_tags(content)

    # 1. Deterministic normalization
    protected_tags = [t for t in current_tags if is_excluded_tag(t)]
    auditable_tags = [normalize_tag_format(t) for t in current_tags if not is_excluded_tag(t)]

    # Inherit parent collection tags if present
    if parent_tags:
        clean_parents = [
            normalize_tag_format(pt)
            for pt in parent_tags
            if pt and not is_excluded_tag(pt)
        ]
        for cpt in clean_parents:
            if cpt and cpt not in auditable_tags:
                auditable_tags.append(cpt)

    # Reconstruct normalized tag set
    normalized_set = set(protected_tags)
    for t in auditable_tags:
        if t:
            normalized_set.add(t)

    final_tags_list = sorted(normalized_set)
    details: dict[str, Any] = {
        "previous_tags": current_tags,
        "final_tags": final_tags_list,
        "llm_evaluated": False,
    }

    # 2. Subject classification (if enabled)
    #
    # The model is asked only what the document is about — never shown the existing tags or
    # the registry, because shown those it imitates their shapes. Measured on this vault: the
    # same model shown existing tags produced eight different terms for "sleep" across five
    # documents and never the bare atom; shown only the documents, it produced the identical
    # phrase three times. Alignment happens afterwards, by measurement (§6.1).
    if enable_llm:
        title = os.path.basename(path).replace(".md", "") if path else "Untitled"
        doc_info = vault_db.get_document(path) if path else None
        gist = doc_info.get("gist", "") if doc_info else ""

        # PASS 1 — application profile (§4). The class is the only judgement; everything it
        # implies is a table lookup, so "forbidden" is enforced rather than requested. These
        # removals are rule violations, not opinions, and so are not subject to the
        # staleness ceiling below.
        doc_class = determine_document_class(body, title, path)
        if doc_class:
            add, drop, gaps = apply_application_profile(doc_class, final_tags_list)
            if drop:
                final_tags_list = [t for t in final_tags_list if t not in set(drop)]
            final_tags_list.extend(t for t in add if t not in final_tags_list)
            details["document_class"] = doc_class
            details["profile_violations"] = drop
            details["profile_gaps"] = gaps
            if gaps:
                logger.info(
                    "[TAG LIBRARIAN] %s is a %s but has no %s — required by §4.",
                    path, doc_class, ", ".join(gaps),
                )

        # PASS 2 — subject indexing.
        applied, proposals = classify_document_subjects(
            body=body, gist=gist, title=title, existing=final_tags_list
        )

        # This pass can only ADD. Asking a document what it is about yields no signal about
        # what it is not about, so removal is not inferable here and stays supervised — which
        # retires the failure mode that disabled this librarian, by construction rather than
        # by rule.
        for tag in applied:
            if tag and tag not in final_tags_list:
                final_tags_list.append(tag)

        # PASS 3 — staleness. A separate question, because it cannot be inferred from what
        # a document is about. Protected tags are never candidates.
        stale = verify_tags_still_apply(body, title, final_tags_list)
        if stale:
            final_tags_list = [t for t in final_tags_list if t not in set(stale)]
            details["tags_removed"] = stale

        final_tags_list = sorted(taxonomy_db.canonicalize_tags(final_tags_list))
        details["final_tags"] = final_tags_list
        details["llm_evaluated"] = True
        details["tags_added"] = applied
        details["proposals"] = proposals
        if proposals:
            logger.info(
                "[TAG LIBRARIAN] %s: %d term(s) proposed for review: %s",
                path, len(proposals), ", ".join(proposals),
            )


    modified = (set(final_tags_list) != set(current_tags))
    new_content = content
    if modified:
        new_content = update_frontmatter_tags(content, final_tags_list)

    return modified, new_content, details


def audit_single_document_semantic(
    doc_path: str | None = None,
    vault_root: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Audit a single vault document against the Master Tag Taxonomy using Tag RAG.

    Args:
        doc_path: Optional relative path of document to audit. If None, fetches next prioritized doc.
        vault_root: Optional vault root directory override.
        dry_run: If True, simulates transformations without writing to disk or database.

    Returns:
        dict[str, Any]: Result summary dict with status, path, tags_added, tags_removed.
    """
    root = vault_root or VAULT_ROOT
    if not doc_path:
        docs = vault_db.fetch_next_documents_for_semantic_tag_audit(batch_size=1)
        if not docs:
            return {"status": "empty", "message": "No documents found in vault DB."}
        doc_path = docs[0]["path"]

    assert doc_path is not None

    # Check document path exclusions
    if is_excluded_document(doc_path):
        if not dry_run:
            vault_db.update_document_semantic_tag_audit(doc_path)
        return {"status": "skipped", "path": doc_path, "message": "Document path is excluded from tag auditing."}

    # Resolve absolute file path
    if vault_root:
        abs_path = doc_path if os.path.isabs(doc_path) else os.path.join(vault_root, doc_path)
    else:
        try:
            abs_path = str(to_vault_abspath(doc_path))
        except (ValueError, TypeError):
            abs_path = doc_path if os.path.isabs(doc_path) else os.path.join(root, doc_path)
    if not os.path.exists(abs_path):
        if not dry_run:
            vault_db.update_document_semantic_tag_audit(doc_path)
        return {"status": "error", "path": doc_path, "message": "File not found on disk."}

    try:
        with open(abs_path, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        if not dry_run:
            vault_db.update_document_semantic_tag_audit(doc_path)
        return {"status": "error", "path": doc_path, "message": f"Read error: {e}"}

    changed, new_content, details = audit_document_tags(
        content=content,
        path=doc_path,
        vault_root=root,
        enable_llm=True,
    )

    if changed and not dry_run:
        try:
            write_file_with_frontmatter(abs_path, new_content, preserve_mtime=True)
            tags_str = ", ".join(details.get("final_tags", []))
            target_col = getattr(cfg, "CHROMA_MEMORY_COLLECTION", "evelyn_memory")
            try:
                chroma_rag.ingest_markdown_file(
                    file_path=abs_path,
                    content=new_content,
                    collection_name=target_col,
                    extra_metadata={"tags": tags_str},
                )
            except Exception as ve:  # noqa: BLE001
                logger.warning(f"[TAG LIBRARIAN] Single-file vector update skipped: {ve}")
        except OSError as e:
            vault_db.update_document_semantic_tag_audit(doc_path)
            return {"status": "error", "path": doc_path, "message": f"Write error: {e}"}

    tags_str = ", ".join(details.get("final_tags", []))
    if not dry_run:
        vault_db.update_document_semantic_tag_audit(doc_path, tags=tags_str)

    return {
        "status": "success",
        "path": doc_path,
        "modified": changed,
        "previous_tags": details.get("previous_tags", []),
        "final_tags": details.get("final_tags", []),
        "document_class": details.get("document_class", ""),
        "profile_gaps": details.get("profile_gaps", []),
        "proposals": details.get("proposals", []),
    }


def audit_single_document(doc_path: str | None = None) -> dict[str, Any]:
    """Backward-compatible wrapper for single document semantic audit."""
    return audit_single_document_semantic(doc_path=doc_path)


def run_semantic_tag_audit(
    batch_size: int = 2,
    max_batches: int = 1,
    deadline: float | None = None,
    delay_between_items: float = 0.5,
    auto_re_enqueue: bool = True,
    cooldown_seconds: int | None = None,
) -> backlog_drainer.DrainResult:
    """Execute a batched semantic Tag RAG audit pass using backlog_drainer.

    Args:
        batch_size: Documents per batch (default: 2).
        max_batches: Maximum batches per run (default: 1).
        deadline: Optional epoch deadline timestamp.
        delay_between_items: Pause between notes in seconds (default: 0.5s).
        auto_re_enqueue: Whether to re-enqueue in task_manager when yielding.
        cooldown_seconds: Minimum seconds before re-auditing notes (default: 24h).

    Returns:
        backlog_drainer.DrainResult: Outcome summary.
    """
    cooldown = (
        cooldown_seconds
        if cooldown_seconds is not None
        else getattr(cfg, "TAG_LIBRARIAN_COOLDOWN_SECONDS", 86400)
    )

    drain_cfg = backlog_drainer.DrainConfig(
        batch_size=batch_size,
        max_batches=max_batches,
        delay_between_items=delay_between_items,
        deadline=deadline,
        yield_check_interval=1,
        auto_re_enqueue=auto_re_enqueue,
        manage_task_lifecycle=True,
    )

    def _fetch(limit: int) -> list[dict[str, Any]]:
        return vault_db.fetch_next_documents_for_semantic_tag_audit(
            batch_size=limit,
            cooldown_seconds=cooldown,
        )

    def _process(doc: dict[str, Any]) -> None:
        audit_single_document_semantic(doc_path=doc["path"])

    return backlog_drainer.drain_backlog(
        task_name="tag_librarian",
        fetch_batch_fn=_fetch,
        process_item_fn=_process,
        config=drain_cfg,
    )


async def run_semantic_tag_audit_async(
    batch_size: int = 2,
    max_batches: int = 1,
    deadline: float | None = None,
    delay_between_items: float = 0.5,
    auto_re_enqueue: bool = True,
    cooldown_seconds: int | None = None,
) -> backlog_drainer.DrainResult:
    """Execute a batched semantic Tag RAG audit pass asynchronously with cooperative yield."""
    cooldown = (
        cooldown_seconds
        if cooldown_seconds is not None
        else getattr(cfg, "TAG_LIBRARIAN_COOLDOWN_SECONDS", 86400)
    )

    drain_cfg = backlog_drainer.DrainConfig(
        batch_size=batch_size,
        max_batches=max_batches,
        delay_between_items=delay_between_items,
        deadline=deadline,
        yield_check_interval=1,
        auto_re_enqueue=auto_re_enqueue,
        manage_task_lifecycle=True,
    )

    def _fetch(limit: int) -> list[dict[str, Any]]:
        return vault_db.fetch_next_documents_for_semantic_tag_audit(
            batch_size=limit,
            cooldown_seconds=cooldown,
        )

    def _process(doc: dict[str, Any]) -> None:
        audit_single_document_semantic(doc_path=doc["path"])

    return await backlog_drainer.drain_backlog_async(
        task_name="tag_librarian",
        fetch_batch_fn=_fetch,
        process_item_fn=_process,
        config=drain_cfg,
    )


def seed_master_taxonomy_from_vault() -> int:
    """Seed initial master tag taxonomy from all existing vault notes and sync to Chroma.

    Returns:
        int: Number of unique tags seeded into master_tag_taxonomy.
    """
    docs = vault_db.get_all_documents()
    tag_counts: dict[str, int] = {}

    for doc in docs:
        raw_tags = doc.get("tags") or ""
        if not raw_tags:
            continue
        for t in raw_tags.split(","):
            clean = normalize_tag_format(t)
            if clean and not is_excluded_tag(clean):
                tag_counts[clean] = tag_counts.get(clean, 0) + 1

    for tag, count in tag_counts.items():
        category = tag.split("/")[0] if "/" in tag else "general"
        desc = f"Obsidian notes tagged under {tag}"
        taxonomy_db.upsert_master_tag(tag, category=category, description=desc, usage_count=count)

    # Sync all seeded tags into Chroma vector store
    sync_master_tags_to_vector_db()

    return len(tag_counts)


TAG_ADMISSION_PROPOSAL = "tag_admission"

# A flood of identical proposals is not review material. One pending proposal per term is
# enough to decide on it, however many documents or facts request it.
TAG_ADMISSION_MAX_PENDING = 200


def propose_tag_admission(
    terms: list[str], origin: str = "", reason: str = ""
) -> list[str]:
    """Raise a review proposal for each term the controlled vocabulary does not hold.

    This is the quarantine route for an unregistered term: rather than a writer silently
    minting vocabulary, or the term being dropped with no record of what wanted it, the
    term goes to the same review queue that already carries merges and stubs (taxonomy §6).

    Proposing does **not** admit the term and does not decide whether the caller stores it.
    Approval registers it, unprotected, so ordinary maintenance can still prune it later if
    nothing uses it.

    Already-pending terms are skipped, so a term requested by fifty facts yields one
    proposal, and the queue is capped so a misbehaving writer cannot bury the review UI.

    Args:
        terms: Candidate terms, already in canonical §5 format.
        origin: Where the term came from (a vault path, a fact id, a subsystem name).
        reason: Why it was requested, shown to the reviewer.

    Returns:
        list[str]: Terms newly proposed by this call.
    """
    from Evelyn.tools import memory_db

    _, unregistered = taxonomy_db.partition_by_admission(
        [t for t in (normalize_tag_format(str(t)) for t in terms) if t and not is_excluded_tag(t)]
    )
    if not unregistered:
        return []

    try:
        pending = memory_db.get_pending_proposals(TAG_ADMISSION_PROPOSAL)
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[TAG LIBRARIAN] Could not read pending tag proposals: %s", exc)
        return []

    already = {(p.get("topic") or "").strip() for p in pending}
    room = TAG_ADMISSION_MAX_PENDING - len(pending)
    if room <= 0:
        logger.warning(
            "[TAG LIBRARIAN] %d tag admission proposals already pending; not adding more.",
            len(pending),
        )
        return []

    proposed: list[str] = []
    for term in dict.fromkeys(unregistered):
        if term in already or len(proposed) >= room:
            continue
        facet = term.split("/")[0] if "/" in term else "general"
        try:
            memory_db.insert_proposal(
                type=TAG_ADMISSION_PROPOSAL,
                source_ids=[],
                topic=term,
                suggested_category=facet,
                reason=reason or f"Requested by {origin or 'an unnamed writer'}; not in the controlled vocabulary.",
                merged_observation=origin,
                confidence="low",
            )
        except (sqlite3.Error, OSError) as exc:
            logger.warning("[TAG LIBRARIAN] Could not propose '%s': %s", term, exc)
            continue
        proposed.append(term)

    if proposed:
        logger.info(
            "[TAG LIBRARIAN] Proposed %d term(s) for admission from %s: %s",
            len(proposed), origin or "unknown origin", ", ".join(proposed),
        )
    return proposed


def admit_proposed_term(term: str, category: str = "") -> bool:
    """Register a term that a reviewer approved.

    Registered unprotected: the term entered by inference rather than from the reviewed
    vocabulary, so it stays subject to ordinary zero-usage pruning. Only curated terms
    carry ``protected`` (see migration 000.006.196).

    Args:
        term: The approved term, in canonical §5 format.
        category: Optional facet/category; derived from the term when omitted.

    Returns:
        bool: True when the term is registered.
    """
    clean = normalize_tag_format(term)
    if not clean:
        return False
    taxonomy_db.upsert_master_tag(
        clean,
        category=category or (clean.split("/")[0] if "/" in clean else "general"),
        description="Admitted through review.",
    )
    index_master_tag_in_chroma(clean, category=category, description="Admitted through review.")
    return True


def maintain_master_taxonomy() -> dict[str, Any]:
    """Perform periodic maintenance on the master tag taxonomy table and sync to Chroma.

    Updates tag usage counts across the vault and safely removes zero-usage tags.
    Includes safety circuit breakers to prevent accidental taxonomy wipes.

    Returns:
        Dict[str, Any]: Summary of maintenance pass.
    """
    docs = vault_db.get_all_documents()
    if not docs:
        print("[TAG LIBRARIAN] [SAFETY CIRCUIT BREAKER] Aborting taxonomy maintenance: vault_documents table is empty.")
        return {
            "status": "aborted",
            "reason": "empty_vault_documents",
            "updated_master_tags": 0,
            "removed_master_tags": 0,
        }

    current_counts: dict[str, int] = {}

    for doc in docs:
        raw_tags = doc.get("tags") or ""
        if not raw_tags:
            continue
        for t in raw_tags.split(","):
            clean = normalize_tag_format(t)
            if clean and not is_excluded_tag(clean):
                current_counts[clean] = current_counts.get(clean, 0) + 1

    if not current_counts:
        print("[TAG LIBRARIAN] [SAFETY CIRCUIT BREAKER] Aborting taxonomy maintenance: 0 active tags found in vault documents.")
        return {
            "status": "aborted",
            "reason": "zero_active_tags",
            "updated_master_tags": 0,
            "removed_master_tags": 0,
        }

    master_tags = taxonomy_db.get_master_tags()
    if not master_tags:
        return {
            "status": "success",
            "updated_master_tags": 0,
            "removed_master_tags": 0,
        }

    tags_to_delete: list[str] = []
    tags_to_update: list[tuple[str, str, str, int]] = []
    protected_skipped = 0

    for m in master_tags:
        t = m["tag"]
        count = current_counts.get(t, 0)
        if count == 0:
            # Zero usage is not evidence of staleness in a controlled vocabulary. Curated
            # terms are registered deliberately and may be reserved long before a document
            # uses one — the DCMI media sub-types and the reserved `event/` values are
            # exactly that. Only terms that entered by inference are prunable on usage.
            if m.get("protected"):
                protected_skipped += 1
                continue
            tags_to_delete.append(t)
        elif count != m.get("usage_count", 0):
            tags_to_update.append((t, m.get("category", "general"), m.get("description", ""), count))

    if protected_skipped:
        print(
            f"[TAG LIBRARIAN] Retained {protected_skipped} curated term(s) with zero current usage "
            f"(protected vocabulary)."
        )

    # Safety Circuit Breaker: Abort if proposed deletions exceed safe threshold
    max_prune_ratio = getattr(cfg, "TAG_LIBRARIAN_MAX_PRUNE_RATIO", 0.15)
    if len(master_tags) > 20 and len(tags_to_delete) > max(10, int(len(master_tags) * max_prune_ratio)):
        print(
            f"[TAG LIBRARIAN] [SAFETY CIRCUIT BREAKER] Aborting taxonomy maintenance: "
            f"Proposed deletion of {len(tags_to_delete)}/{len(master_tags)} tags exceeds safety threshold ({max_prune_ratio:.0%})."
        )
        return {
            "status": "aborted",
            "reason": "prune_threshold_exceeded",
            "proposed_deletions": len(tags_to_delete),
            "total_master_tags": len(master_tags),
            "updated_master_tags": 0,
            "removed_master_tags": 0,
        }

    for t in tags_to_delete:
        taxonomy_db.delete_master_tag(t)
        delete_tag_from_chroma(t)

    for t, cat, desc, count in tags_to_update:
        taxonomy_db.upsert_master_tag(t, category=cat, description=desc, usage_count=count)
        index_master_tag_in_chroma(t, category=cat, description=desc, usage_count=count)

    return {
        "status": "success",
        "updated_master_tags": len(tags_to_update),
        "removed_master_tags": len(tags_to_delete),
        "protected_retained": protected_skipped,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Obsidian Tag Librarian CLI")
    parser.add_argument("--audit-one", action="store_true", help="Audit next eligible vault document")
    parser.add_argument("--audit-batch", type=int, default=0, help="Audit N eligible vault documents")
    parser.add_argument("--seed-taxonomy", action="store_true", help="Seed master taxonomy from vault index")
    parser.add_argument("--maintain-taxonomy", action="store_true", help="Perform taxonomy maintenance pass")
    parser.add_argument("--sync-vector-tags", action="store_true", help="Sync SQLite master tags to Chroma vector store")

    args = parser.parse_args()

    if args.seed_taxonomy:
        count = seed_master_taxonomy_from_vault()
        print(f"[TAG LIBRARIAN] Seeded {count} tags into master_tag_taxonomy and Chroma vector store.")
    elif args.sync_vector_tags:
        count = sync_master_tags_to_vector_db()
        print(f"[TAG LIBRARIAN] Synced {count} master tags into Chroma collection '{TAG_COLLECTION_NAME}'.")
    elif args.maintain_taxonomy:
        res = maintain_master_taxonomy()
        print(f"[TAG LIBRARIAN] Taxonomy maintenance: {res}")
    elif args.audit_one or args.audit_batch > 0:
        n = 1 if args.audit_one else args.audit_batch
        for i in range(n):
            res = audit_single_document()
            print(f"[TAG LIBRARIAN] Pass {i+1}/{n}: {res}")
    else:
        parser.print_help()
