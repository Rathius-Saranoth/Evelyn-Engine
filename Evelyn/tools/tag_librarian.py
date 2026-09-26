# tag_librarian.py
# date created: 2026-08-02 11:53:00
# date modified: 2026-09-25 20:36:40
# tags: #tag, #librarian, #taxonomy, #indexing, #obsidian, #idle_time, #rag, #chromadb

"""
tag_librarian.py — Incremental Obsidian Tag Maintenance & Taxonomy Management.

Exports:
    is_excluded_tag()                     — Checks if a tag matches protected exclusion rules (e.g. status/, obsidian-graph/).
    canonicalize_occurred()               — Resolves a date to canonical EDTF for the `occurred` property.
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
    seed_master_taxonomy_from_vault()     — Bootstraps an empty registry from the vault index.

Key config: evelyn_config.py (TAG_LIBRARIAN_EXCLUSIONS, TAG_LIBRARIAN_FORMAT_RULES, CHROMA_TAG_COLLECTION)
See also: reference/engine_architecture.md
"""

import contextlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
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
        tag: Tag string to evaluate (e.g. 'status/active' or 'tech/python').

    Returns:
        bool: True if the tag is protected from modification/removal, False otherwise.
    """
    clean_tag = tag.strip().lstrip("#")
    exclusions = getattr(cfg, "TAG_LIBRARIAN_EXCLUSIONS", [r"^status/", r"^obsidian-graph/"])

    return any(re.search(pattern, clean_tag, re.IGNORECASE) for pattern in exclusions)


def is_umbrella_term(term: str) -> bool:
    """True when a term is a container rather than a subject.

    `work`, `home`, `pets`, `tools` and their kin are the folder headings a flat vocabulary
    removes. They select nearly everything, so they narrow nothing, and admitting one puts a
    term in the registry that can never earn its place back.

    Every tag-producing prompt says this in prose. Prose is advice: a model reaches for the
    container anyway when nothing more specific comes to mind, and 17 of the 127 unregistered
    terms found on memory facts were exactly these words. This is the filter that makes the
    rule hold, placed where terms are held back for review rather than where they are asked
    for.

    Args:
        term: A candidate term, already in §5 format.

    Returns:
        bool: True if the term must not be proposed for admission.
    """
    return term.strip().lower() in getattr(cfg, "TAXONOMY_CONTAINER_TERMS", set())


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


_DATE_PLACEHOLDERS = {"YYYY": "XXXX", "MM": "XX", "DD": "XX"}


OCCURRED_PROPERTY = "occurred"

# Accepts the retired tag spellings (cy-2025, Cy_Yyyy/11/16) alongside plain EDTF, so every
# variant converges on one canonical property value.
_OCCURRED_RE = re.compile(
    r"^(?:cy[-_]?)?([0-9x]{4}|yyyy)(?:[/_-]([0-9x]{2}|mm))?(?:[/_-]([0-9x]{2}|dd))?$",
    re.IGNORECASE,
)


def canonicalize_occurred(value: str) -> str | None:
    """Resolve a date to its canonical EDTF form for the ``occurred`` property.

    Follows EDTF (ISO 8601-2:2019): reduced precision and unspecified digits are distinct.
    '2026-05' means May 2026 (no day was intended), while '2026-05-XX' means a specific but
    unknown day in May 2026. Unexpanded template literals (YYYY/MM/DD) are read as
    unspecified digits and become X.

    Accepts the retired ``CY-YYYY/MM/DD`` tag form as input so existing data migrates
    cleanly. That prefix only ever existed because an Obsidian tag cannot begin with a
    digit; a property has no such restriction, so the canonical form drops it and uses
    hyphens, which is what every date parser and Obsidian's own date type expect.

    Args:
        value: A date string, tag or property, with or without a leading '#'.

    Returns:
        str | None: Canonical EDTF form, or None if the value is not a date.
    """
    m = _OCCURRED_RE.match(str(value).strip().lstrip("#").strip())
    if not m:
        return None

    parts: list[str] = []
    for group in m.groups():
        if group is None:
            break  # Reduced precision: stop at the first absent component.
        token = group.upper()
        parts.append(_DATE_PLACEHOLDERS.get(token, token))

    return "-".join(parts) if parts else None


def normalize_tag_format(tag: str) -> str:
    """Normalize a tag to the canonical vault format.

    Implements .agents/rules/vault-tag-taxonomy.md §5: lowercase always, hyphens
    join words, slashes join levels. There is deliberately no entity/concept
    branch — proper nouns follow the same rule as concepts, which is what removes
    any way for one term to fork into 'ai' and 'Ai'.

    Dates are not tags at all — the time axis is the `occurred` property (§3.8), so there
    is no date branch here. Administrative namespaces listed in TAG_LIBRARIAN_EXCLUSIONS
    are returned untouched.

    Args:
        tag: Raw tag string (e.g. '#Tech/Ai', 'DungeonCrawlerCarl').

    Returns:
        str: Normalized tag string, or '' if nothing survives normalization.
    """
    clean = tag.strip().lstrip("#").strip()
    if not clean:
        return ""

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

# Facet prefixes keep exactly one level: the prefix names which axis a term belongs to,
# which flat atoms cannot express. Everything else decomposes (§3.3). Defined here rather
# than in `tag_synonym`, which imports from this module.
FACET_PREFIXES = ("type/", "motif/", "setting/", "event/")


def is_subject_term(term: str) -> bool:
    """A facet is not a subject, so no subject-level pass may resolve to one.

    The registry holds facet values as terms — `type/reference`, `motif/storm` — so a
    phrase like "reference" looks them up successfully. Applying the result puts a second
    form-axis tag on a document that §4 already gave one, undoing the profile pass that ran
    immediately before. Two Reference Library chapters gained `type/guide` beside
    `type/reference` this way. What facet a document carries is decided by its class (§4),
    never by what it is about.

    The same category error appears wherever subjects are compared to one another. The
    relation generator ranked `ttrpg:type/profile` highly because documents about ttrpg do
    tend to be profiles — a true statement about document *class*, and not an association
    between subjects, which is the only thing an `RT` relation may assert (§6.4).

    Args:
        term: A registered term or a phrase already normalised to tag form.

    Returns:
        bool: True when the term names a subject rather than a facet value.
    """
    return not term.startswith(FACET_PREFIXES)


def is_wellformed_term(term: str) -> bool:
    """A slash belongs to a facet axis, or it is the retired hierarchy (§3.3).

    Post-coordination left exactly one use for `/`: naming which axis a facet value sits on.
    Anything else is a pre-coordinate compound — `lore/campaign-narrative` is `lore` and
    `campaign-narrative` glued together by an indexer guessing which combination a future
    query would want, which is the structure this vocabulary removed.

    Nothing was checking. `normalize_tag_format` preserves the slash and `is_excluded_tag`
    ignores it, so two such terms reached the review queue on 2026-09-23 and the only thing
    stopping them returning was the reviewer's rejection — a permanent decision doing work a
    format rule should never have delegated to it.

    Args:
        term: A term already in canonical §5 format.

    Returns:
        bool: True when the term is flat, or a facet value on a known axis.
    """
    if "/" not in term:
        return True
    return term.startswith(FACET_PREFIXES)


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


def nearest_registered_term(phrase: str) -> tuple[str | None, float, float]:
    """Retrieve the nearest registered term to a phrase, with the margin over its runner-up.

    Public because it answers §6.1's "nearest existing terms" question for any caller holding
    a candidate term — subject reconciliation here, and the review card, which shows a
    reviewer what an unregistered tag is close to so approval is a comparison rather than a
    feat of recall.

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
                    if term not in known and term not in applied and is_subject_term(term):
                        applied.append(term)
                continue

        # Stage 2 — vectors, only for what the dictionary missed.
        if match is None:
            match, distance, margin = nearest_registered_term(phrase)
            if match is not None and (
                distance > reject or margin < SUBJECT_MARGIN_GUARD
                or distance > SUBJECT_ACCEPT_DISTANCE
            ):
                # Near enough to retrieve, not near enough to assert. Deciding this by
                # distance is what force-linked off-topic phrases to plausible neighbours.
                match = None

        if match:
            if match not in known and match not in applied and is_subject_term(match):
                applied.append(match)
        elif candidate not in proposals and not is_umbrella_term(candidate):
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
    doc_class: str, tags: list[str], occurred: str | None = None
) -> tuple[list[str], list[str], list[str]]:
    """Enforce a class's facet profile against a document's tags (§4).

    Returns what the profile requires be added, what it forbids and must go, and which
    required facets the document still lacks. The last of those is a gap to report rather
    than something to invent: a `dream` with no motif needs one, but guessing which motif
    is subject analysis, not cataloguing.

    Args:
        doc_class: A class from DOCUMENT_CLASSES.
        tags: The document's current tags.
        occurred: The document's `occurred` property, if it has one. The time facet is
            satisfied by this rather than by a tag.

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
        # The time axis lives in the `occurred` property, not in the tag list. It is the one
        # facet whose primary access pattern is a range ("notes between March and June"),
        # which a tag cannot answer without enumerating every day, and the only one every
        # tag consumer had to special-case.
        present = (
            bool(occurred) if facet == "time"
            else any(t.startswith(f"{facet}/") for t in tags)
        )

        if rule == FORBIDDEN and present:
            remove.extend(t for t in tags if t.startswith(f"{facet}/"))
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
            # No placeholder: an uncategorised term is uncategorised in the vector
            # metadata too, or the embedding disagrees with the registry row.
            "category": category or (clean_tag.split("/")[0] if "/" in clean_tag else ""),
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
            # Canonicalised on read, so a hand-typed '2026/05/18' or a legacy 'CY-2026/05'
            # still satisfies the time facet rather than being silently ignored.
            occurred = canonicalize_occurred(
                str(parse_frontmatter(content)[0].get(OCCURRED_PROPERTY, "") or "")
            ) or ""
            add, drop, gaps = apply_application_profile(doc_class, final_tags_list, occurred=occurred)
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

        # Backstop for §3.4: exactly one form-axis tag, whatever the passes above did. The
        # profile pass (1) already settles which one, so its answer wins and any other is
        # dropped here. This is belt-and-braces — pass 2 can no longer emit a facet — but the
        # rule is cheap to assert and was violated silently for a day without it.
        type_tags = [t for t in final_tags_list if t.startswith("type/")]
        if len(type_tags) > 1:
            keep = f"type/{doc_class}" if doc_class else type_tags[0]
            keep = next(
                (t for t in type_tags if t == keep or t.startswith(keep + "/")), type_tags[0]
            )
            dropped = [t for t in type_tags if t != keep]
            final_tags_list = [t for t in final_tags_list if t == keep or not t.startswith("type/")]
            details["type_conflict_resolved"] = {"kept": keep, "dropped": dropped}
            logger.warning(
                "[TAG LIBRARIAN] %s carried %d type/ tags (%s); kept %s.",
                path, len(type_tags), ", ".join(type_tags), keep,
            )

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


def audit_single_fact_tags(entry: dict[str, Any], dry_run: bool = False) -> dict[str, Any]:
    """Derive a memory fact's subjects and align them to the vocabulary.

    The memory half of the `000.006.187` tag reset. That migration cleared every tag in memory so
    the vocabulary could be regenerated from the standard; `000.006.186` did the same to the vault
    *and* reset each document's audit timestamp, so the vault re-entered the semantic queue and
    has been draining since. Memory was left with no way back, and 10,033 facts have been
    invisible to tag retrieval ever since.

    Deliberately the same machinery as the vault pass rather than a second implementation: a fact
    is a short document. `classify_document_subjects` asks a model only what the text is *about*
    — never showing it the registry, so it cannot imitate existing shapes — and then aligns the
    answer deterministically, applying only terms the vocabulary already holds and holding the
    rest back for review.

    Only terms that matched are written. An unmatched one becomes a `tag_admission` proposal
    naming this entry, so approval can put it back (`backfill_admitted_term`) — which is the same
    contract new facts have had since v000.006.227, applied to the backlog.

    Args:
        entry: A live context entry, as returned by `fetch_next_entries_for_tag_audit`.
        dry_run: Report what would change without writing.

    Returns:
        dict[str, Any]: `id`, `applied`, `proposed`, and `status`.
    """
    from Evelyn.tools import memory_db

    entry_id = int(entry["id"])
    observation = str(entry.get("observation") or "").strip()
    if not observation:
        if not dry_run:
            memory_db.mark_entry_tag_audited(entry_id)
        return {"id": entry_id, "applied": [], "proposed": [], "held_back": [], "status": "empty"}

    existing = [
        t for t in (normalize_tag_format(x) for x in str(entry.get("tags") or "").split(","))
        if t
    ]
    title = f"{entry.get('subject') or ''} — {entry.get('category') or ''}".strip(" —")

    try:
        applied, proposals = classify_document_subjects(
            body=observation, gist="", title=title, existing=existing
        )
    except (sqlite3.Error, OSError, ValueError, RuntimeError) as exc:
        logger.warning("[TAG LIBRARIAN] Fact #%s classification failed: %s", entry_id, exc)
        return {"id": entry_id, "applied": [], "proposed": [], "held_back": [], "status": "error"}

    final = list(dict.fromkeys([*existing, *applied]))
    if dry_run:
        return {"id": entry_id, "applied": final, "proposed": [], "held_back": list(proposals), "status": "dry_run"}

    proposed: list[str] = []
    if proposals:
        with contextlib.suppress(sqlite3.Error, OSError):
            proposed = propose_tag_admission(
                proposals,
                origin=f"memory fact #{entry_id}",
                reason="Subject named by a memory fact but not in the controlled vocabulary.",
                source_ids=[entry_id],
            )

    memory_db.mark_entry_tag_audited(entry_id, tags=", ".join(final) if final else None)
    return {
        "id": entry_id,
        "applied": final,
        "proposed": proposed,
        # Everything classification could not match, raised or not. A caller draining a backlog
        # needs this: past the pending cap no proposal is written, and the fact is stamped
        # regardless, so the terms would otherwise be gone with the fact marked done.
        "held_back": list(proposals),
        "status": "tagged" if final else "no_match",
    }


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

    # Send the terms this document wanted but the vocabulary does not hold to review.
    #
    # They were computed, stored in `details["proposals"]`, written to a logger the audit
    # subprocess does not surface, and returned in a dict the backlog drainer discards —
    # `propose_tag_admission` was never called from this module at all. So the pass audited 766
    # documents and proposed nothing, and the empty admission queue read as a clean pipeline
    # when it was really the largest producer writing nowhere.
    if not dry_run and details.get("proposals"):
        with contextlib.suppress(sqlite3.Error, OSError):
            propose_tag_admission(
                details["proposals"],
                origin=f"vault note ({doc_path})",
                reason="Subject named by a vault note but not in the controlled vocabulary.",
                source_path=doc_path,
            )

    # Leave a trace when the pass rewrote a document. Only the master librarian wrote to
    # librarian_activity_log, so this pass changed 115 files in one night and the log stayed
    # empty — which read as "it did nothing" and hid a rule violation for a day. A pass that
    # edits the vault unattended has to be reviewable afterwards.
    if changed and not dry_run:
        before = set(details.get("previous_tags", []))
        after = set(details.get("final_tags", []))
        actions = []
        if after - before:
            actions.append(f"added: {', '.join(sorted(after - before))}")
        if before - after:
            actions.append(f"removed: {', '.join(sorted(before - after))}")
        if details.get("type_conflict_resolved"):
            actions.append(f"type conflict: {details['type_conflict_resolved']}")
        with contextlib.suppress(sqlite3.Error, OSError, ValueError):
            vault_db.log_librarian_activity(
                path=doc_path,
                title=os.path.basename(doc_path).replace(".md", ""),
                category=doc_path.split("/")[0] if "/" in doc_path else "",
                actions=["semantic_tag_audit", *actions],
                summary=f"Semantic tag audit of '{os.path.basename(doc_path)}': "
                        f"{'; '.join(actions) if actions else 'no tag change'}",
                excerpt=", ".join(sorted(after))[:300],
            )

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


def _tally_tags(records: list[dict[str, Any]], counts: dict[str, int]) -> None:
    """Add each record's registered-format tags to a running census."""
    for rec in records:
        for raw in str(rec.get("tags") or "").split(","):
            clean = normalize_tag_format(raw)
            if clean and not is_excluded_tag(clean):
                counts[clean] = counts.get(clean, 0) + 1


def census_tag_usage() -> tuple[dict[str, int], dict[str, int]]:
    """Count every use of every term across the whole corpus.

    §0 of the standard treats the vault and memory as one corpus, so a term earns its
    place in the vocabulary from either substrate. A census that reads vault notes alone
    reports a term living entirely in memory facts as unused, and an unused term is
    eventually proposed for retirement — which is how a working term gets retired for
    being invisible to the count rather than for being unwanted.

    Procedures are counted with memory facts: they are tagged from the same controlled
    vocabulary (AGENTS §10) and stored in the same database.

    Returns:
        tuple[dict[str, int], dict[str, int]]: Uses per term, and the number of records
        read from each substrate (``vault``, ``memory``, ``procedures``). Callers use the
        second value to tell an honestly empty substrate from a failed read before acting
        on a zero.
    """
    from Evelyn.tools import memory_db

    docs = vault_db.get_all_documents()
    entries = memory_db.get_all_entries(statuses=["live"])
    procedures = memory_db.get_all_procedures(status="live")

    counts: dict[str, int] = {}
    for records in (docs, entries, procedures):
        _tally_tags(records, counts)

    sizes = {"vault": len(docs), "memory": len(entries), "procedures": len(procedures)}
    return counts, sizes


def seed_master_taxonomy_from_vault(allow_populated: bool = False) -> dict[str, Any]:
    """Bootstrap an empty controlled vocabulary from the terms the vault already uses.

    This is a **bootstrap**, not a refresh. It exists for one situation: the registry is empty
    — a fresh install, or a restore after loss — and the vault is the only surviving record of
    which terms were in use. Run against a curated registry it is not a seed at all, so two
    guards stand in the way.

    **It refuses a populated registry.** Once terms have been reviewed, an unregistered vault
    term is an admission question, not a bulk insert: it belongs in a `tag_admission` proposal
    where somebody decides whether it is a subject worth a term (§6). Bulk registration walks
    around that review for every term at once. `allow_populated` overrides the refusal for a
    deliberate restore.

    **It never rewrites an existing row.** Earlier it upserted every vault term with
    ``category="general"`` and ``description="Obsidian notes tagged under X"``, and
    `upsert_master_tag` overwrote `category` unconditionally — so a single run would have
    replaced all 675 curated categories with one shelf label and papered the scope notes with
    boilerplate. It now registers only terms the registry lacks, and leaves their category and
    description **empty** for a reviewer to fill rather than inventing a value that reads like
    a decision somebody made. Usage counts on existing rows are maintenance's job
    (`maintain_master_taxonomy`), not this function's.

    Args:
        allow_populated: Proceed even though the registry already holds terms. For a
            deliberate restore; it still does not overwrite anything.

    Returns:
        dict[str, Any]: `status` (`seeded` or `refused`), `registered`, `already_registered`,
        and `vault_terms`.
    """
    tag_counts: dict[str, int] = {}
    _tally_tags(vault_db.get_all_documents(), tag_counts)

    existing = {m["tag"] for m in taxonomy_db.get_master_tags()}
    missing = {t: c for t, c in tag_counts.items() if t not in existing}

    if existing and not allow_populated:
        print(
            f"[TAG LIBRARIAN] [REFUSED] The registry already holds {len(existing)} reviewed "
            f"term(s); seeding is for an empty one. {len(missing)} vault term(s) are "
            f"unregistered — raise them as tag_admission proposals rather than bulk-inserting, "
            f"or pass allow_populated=True for a deliberate restore."
        )
        return {
            "status": "refused",
            "reason": "registry_populated",
            "registered": 0,
            "already_registered": len(existing),
            "vault_terms": len(tag_counts),
        }

    for tag, count in missing.items():
        # No category, no description: an unreviewed term has neither, and boilerplate in
        # those fields is indistinguishable from a curated answer once it is written.
        taxonomy_db.upsert_master_tag(tag, usage_count=count)
        index_master_tag_in_chroma(tag, usage_count=count)

    return {
        "status": "seeded",
        "registered": len(missing),
        "already_registered": len(existing),
        "vault_terms": len(tag_counts),
    }


TAG_ADMISSION_PROPOSAL = "tag_admission"

# A flood of identical proposals is not review material. One pending proposal per term is
# enough to decide on it, however many documents or facts request it.
TAG_ADMISSION_MAX_PENDING = 200


def withhold_unregistered_tags(
    entry_id: int,
    tags: list[str],
    origin: str,
    reason: str,
) -> list[str]:
    """Propose the tags the vocabulary does not hold, and return the ones the entry keeps.

    §6.1 says an unregistered term is a proposal, not a silent addition. Memory writers stored
    it on the fact anyway and proposed it in parallel, so the vocabulary and the corpus drifted:
    127 terms accumulated that way and needed a hand curation pass to reconcile. The vault side
    already withholds — `reconcile_subjects` applies only what it can match — so this closes the
    memory half.

    **Nothing is dropped silently.** A term is withheld only once a pending proposal covers it,
    which includes one raised earlier by another fact. If the queue is at
    `TAG_ADMISSION_MAX_PENDING`, or the insert failed, the term is kept on the entry exactly as
    before: an unreviewed tag is untidy, a vanished one is unrecoverable.

    Call this *after* the row exists, so the proposal can record which entry wanted the term and
    approval can put it back (`backfill_admitted_term`).

    Args:
        entry_id: The memory entry the tags belong to.
        tags: Its tags, already normalized and alias-resolved.
        origin: Where the tags came from, shown to the reviewer.
        reason: Why the term was requested, shown to the reviewer.

    Returns:
        list[str]: The tags to store, in their original order.
    """
    from Evelyn.tools import memory_db

    if not tags:
        return []

    # A container word is dropped outright rather than stored or proposed. The "never drop"
    # guard below protects information; `work` and `pets` carry none, which is the definition
    # of the list they are on. Keeping them is how they reached 17 memory facts.
    containers = [t for t in tags if is_umbrella_term(t)]
    if containers:
        logger.info(
            "[TAG LIBRARIAN] Dropped container word(s) from %s: %s",
            origin, ", ".join(containers),
        )
        tags = [t for t in tags if not is_umbrella_term(t)]

    _admitted, unregistered = taxonomy_db.partition_by_admission(tags)
    if not unregistered:
        return list(tags)

    propose_tag_admission(unregistered, origin=origin, reason=reason, source_ids=[entry_id])

    if not getattr(cfg, "TAG_WITHHOLD_UNREGISTERED", False):
        return list(tags)

    try:
        covered = {
            (prop.get("topic") or "").strip()
            for prop in memory_db.get_pending_proposals(TAG_ADMISSION_PROPOSAL)
        }
        # A rejected term is covered too, and more firmly than a pending one: the reviewer has
        # already said no. Since G1 stopped re-proposing rejected terms, reading `pending` alone
        # would find no proposal covering them and leave them on the fact — turning "rejected"
        # into the one verdict that lets a term through. The rejected row is still the record
        # that makes it recoverable, which is the condition withholding has always needed.
        covered |= set(memory_db.get_rejected_topics(TAG_ADMISSION_PROPOSAL))
    except (sqlite3.Error, OSError) as exc:
        # Cannot prove a proposal covers anything, so withhold nothing.
        logger.warning("[TAG LIBRARIAN] Could not confirm pending proposals; keeping tags: %s", exc)
        return list(tags)

    kept = [t for t in tags if t not in covered]
    held = [t for t in tags if t in covered]
    if held:
        logger.info(
            "[TAG LIBRARIAN] Withheld %d unregistered tag(s) from %s pending review: %s",
            len(held), origin, ", ".join(held),
        )
    return kept


def propose_tag_admission(
    terms: list[str], origin: str = "", reason: str = "", source_ids: list[int] | None = None,
    source_path: str = "",
) -> list[str]:
    """Raise a review proposal for each term the controlled vocabulary does not hold.

    This is the quarantine route for an unregistered term: rather than a writer silently
    minting vocabulary, or the term being dropped with no record of what wanted it, the
    term goes to the same review queue that already carries merges and stubs (taxonomy §6).

    Proposing does **not** admit the term and does not decide whether the caller stores it.
    Approval registers it, unprotected: it entered by inference rather than from the reviewed
    vocabulary, so it is eligible for a retirement proposal once it has gone unused past the
    grace period, which a curated term never is.

    Already-pending terms are skipped, so a term requested by fifty facts yields one
    proposal, and the queue is capped so a misbehaving writer cannot bury the review UI.

    Args:
        terms: Candidate terms, already in canonical §5 format.
        origin: Where the term came from (a vault path, a fact id, a subsystem name).
        reason: Why it was requested, shown to the reviewer.
        source_ids: Memory entries that wanted the term. Recorded so approval can put the term
            back onto them; without the trail an approved term has no way to reach the facts
            that asked for it, which is what makes withholding unregistered tags possible.
        source_path: The vault note that wanted it, for the same reason. `source_ids` holds
            memory entry ids and cannot name a document, so a vault-sourced proposal used to
            approve into a vocabulary and reach nothing (G5).

    Returns:
        list[str]: Terms newly proposed by this call.
    """
    from Evelyn.tools import memory_db

    _, unregistered = taxonomy_db.partition_by_admission(
        [
            t for t in (normalize_tag_format(str(t)) for t in terms)
            if t and not is_excluded_tag(t) and not is_umbrella_term(t)
            and is_wellformed_term(t)
        ]
    )
    if not unregistered:
        return []

    try:
        pending = memory_db.get_pending_proposals(TAG_ADMISSION_PROPOSAL)
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[TAG LIBRARIAN] Could not read pending tag proposals: %s", exc)
        return []

    by_topic = {(p.get("topic") or "").strip(): p for p in pending}
    already = set(by_topic)

    # A rejection is permanent (G1). Re-proposing a term the reviewer has already turned down
    # offers them nothing but the chance to reject it a second time, which is what made the
    # queue feel like busywork — `support` was rejected and back inside a day. Each further
    # request counts against the rejected row instead, so a term the corpus keeps asking for
    # arrives at review with the number that argues for admitting it.
    try:
        rejected = memory_db.get_rejected_topics(TAG_ADMISSION_PROPOSAL)
    except (sqlite3.Error, OSError) as exc:
        # Cannot prove anything was rejected, so suppress nothing: a term that slips through is
        # one more review, a term wrongly suppressed is invisible.
        logger.warning("[TAG LIBRARIAN] Could not read rejected tag proposals: %s", exc)
        rejected = {}

    for term in dict.fromkeys(unregistered):
        if term not in rejected:
            continue
        with contextlib.suppress(sqlite3.Error, OSError):
            count = memory_db.record_rejected_request(TAG_ADMISSION_PROPOSAL, term)
            logger.info(
                "[TAG LIBRARIAN] '%s' was rejected; not re-proposing (asked for %d time(s) now), "
                "requested by %s.", term, count, origin or "an unnamed writer",
            )
    unregistered = [t for t in unregistered if t not in rejected]
    if not unregistered:
        return []

    # A term a second fact also wants must widen the existing proposal rather than be dropped,
    # or approval backfills only whichever fact happened to ask first.
    for term in dict.fromkeys(unregistered) if source_ids else ():
        prop = by_topic.get(term)
        if not prop:
            continue
        merged = list(dict.fromkeys([*(prop.get("source_ids") or []), *source_ids]))
        if merged != (prop.get("source_ids") or []):
            try:
                memory_db.update_proposal(prop["id"], source_ids=merged)
            except (sqlite3.Error, OSError) as exc:
                logger.warning("[TAG LIBRARIAN] Could not widen proposal for '%s': %s", term, exc)

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
        # Only a facet-prefixed term states its own axis. A flat term's category is a
        # curatorial judgement the reviewer makes, so it is left empty rather than filled
        # with a placeholder that would enter the registry as if it meant something.
        facet = term.split("/")[0] if "/" in term else ""
        try:
            memory_db.insert_proposal(
                type=TAG_ADMISSION_PROPOSAL,
                source_ids=list(source_ids or []),
                topic=term,
                suggested_category=facet,
                reason=reason or f"Requested by {origin or 'an unnamed writer'}; not in the controlled vocabulary.",
                merged_observation=origin,
                confidence="low",
                source_path=source_path or None,
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
    vocabulary, so it remains eligible for a retirement proposal if it goes unused. Only
    curated terms carry ``protected`` (see migration 000.006.196).

    Args:
        term: The approved term, in canonical §5 format.
        category: The reviewer's chosen category. A facet-prefixed term supplies its own
            when this is omitted; a flat term is left uncategorised rather than being
            given a placeholder, because no category can be inferred from the term alone.

    Returns:
        bool: True when the term is registered.
    """
    clean = normalize_tag_format(term)
    if not clean:
        return False
    # No derivation from the prefix: `motif/storm` does not need a category saying `motif`.
    # That auto-fill put a restatement in 96 of 700 rows and made the column read as a facet
    # placeholder. An uncategorised term stays uncategorised until a reviewer groups it.
    resolved = category
    taxonomy_db.upsert_master_tag(
        clean, category=resolved, description="Admitted through review."
    )
    # The vector copy must carry the same category as the row, or the two disagree about
    # what was admitted.
    index_master_tag_in_chroma(clean, category=resolved, description="Admitted through review.")
    return True


def backfill_admitted_term(term: str, source_ids: list[int]) -> int:
    """Put a newly admitted term back onto the memory entries that asked for it.

    Approval registers a term but does nothing to the facts that wanted it. While admission is
    propose-only that is harmless, because the writer stores the tag regardless. The moment
    unregistered tags are withheld instead, it stops being harmless: the tag never reaches the
    fact, and without this the approval cannot put it there.

    Args:
        term: The admitted term, in canonical §5 format.
        source_ids: Memory entry ids recorded on the proposal.

    Returns:
        int: How many entries gained the term.
    """
    from Evelyn.tools import memory_db

    clean = normalize_tag_format(term)
    if not clean or not source_ids:
        return 0

    updated = 0
    for eid in source_ids:
        try:
            entry = memory_db.get_entry(eid)
        except (sqlite3.Error, OSError) as exc:
            logger.warning("[TAG LIBRARIAN] Backfill could not read entry %s: %s", eid, exc)
            continue
        if not entry or entry.get("status") != "live":
            continue
        current = [t.strip() for t in str(entry.get("tags") or "").split(",") if t.strip()]
        if clean in current:
            continue
        try:
            memory_db.update_entry(eid, tags=", ".join([*current, clean]))
            updated += 1
        except (sqlite3.Error, OSError) as exc:
            logger.warning("[TAG LIBRARIAN] Backfill could not update entry %s: %s", eid, exc)

    if updated:
        logger.info("[TAG LIBRARIAN] Backfilled '%s' onto %d entr(ies).", clean, updated)
    return updated


TAG_RETIREMENT_PROPOSAL = "tag_retirement"



def backfill_admitted_term_to_note(term: str, source_path: str) -> bool:
    """Put a newly admitted term back onto the vault note that asked for it.

    The memory half of this has existed since `000.006.216`; the vault half never did, because
    `source_ids` holds `context_entries` ids and a note is not one. So approving a term a note
    proposed registered the word and stopped there: the vocabulary gained an entry, the note
    stayed unindexed for a subject it demonstrably concerns, and the queue emptied either way
    so it read as finished (G5).

    Writes through the same path the audit itself uses — frontmatter rewrite preserving mtime,
    then a Chroma re-ingest **by enqueue**, never a direct write, since the engine's custodian
    holds the single-writer lease.

    Args:
        term: The admitted term, in canonical §5 format.
        source_path: Vault-relative path recorded on the proposal.

    Returns:
        bool: True when the note gained the term.
    """
    clean = normalize_tag_format(term)
    if not clean or not source_path:
        return False

    # Resolved against the configured root at call time, not `path_utils.VAULT_ROOT`, which is
    # captured at import and so cannot follow a reconfigured vault — the same reason
    # `audit_single_document_semantic` takes an explicit root. The traversal guard is kept:
    # a path that escapes the vault is refused rather than written to.
    root = os.path.realpath(getattr(cfg, "VAULT_BASE_DIR", "") or "")
    if not root:
        logger.warning("[TAG LIBRARIAN] Backfill has no vault root configured.")
        return False
    abs_path = os.path.realpath(os.path.join(root, str(source_path).strip().lstrip("/\\")))
    if os.path.commonpath([abs_path, root]) != root:
        logger.warning("[TAG LIBRARIAN] Backfill path '%s' escapes the vault; refused.",
                       source_path)
        return False
    if not os.path.exists(abs_path):
        logger.warning("[TAG LIBRARIAN] Backfill target no longer exists: %s", source_path)
        return False

    try:
        with open(abs_path, encoding="utf-8") as fh:
            content = fh.read()
    except OSError as exc:
        logger.warning("[TAG LIBRARIAN] Backfill could not read '%s': %s", source_path, exc)
        return False

    current, _body = parse_frontmatter_tags(content)
    if clean in current:
        return False

    updated_tags = [*current, clean]
    try:
        write_file_with_frontmatter(
            abs_path, update_frontmatter_tags(content, updated_tags), preserve_mtime=True
        )
    except OSError as exc:
        logger.warning("[TAG LIBRARIAN] Backfill could not write '%s': %s", source_path, exc)
        return False

    # The write preserves mtime, so the vault watcher will not notice it — the index has to
    # be told directly, exactly as the audit tells it at the end of a pass.
    tags_str = ", ".join(updated_tags)
    with contextlib.suppress(sqlite3.Error, OSError):
        vault_db.update_document_semantic_tag_audit(source_path, tags=tags_str)
    with contextlib.suppress(Exception):
        chroma_rag.ingest_markdown_file(
            file_path=abs_path,
            content=update_frontmatter_tags(content, updated_tags),
            collection_name=getattr(cfg, "CHROMA_MEMORY_COLLECTION", "evelyn_memory"),
            extra_metadata={"tags": tags_str},
        )

    logger.info("[TAG LIBRARIAN] Backfilled '%s' onto note %s.", clean, source_path)
    return True


def propose_tag_retirement(master_tags: list[dict[str, Any]], unused: list[str]) -> list[str]:
    """Propose long-unused terms for retirement instead of deleting them.

    Retirement is a human decision and, in a controlled vocabulary, normally means
    deprecating a term *with a pointer* to its preferred form rather than erasing the
    record (taxonomy §6.2) — the alias is what stops the variant being re-minted later.

    A grace period applies, because a reserved term legitimately has no uses yet: the DCMI
    media sub-types and the reserved `event/` values were registered before any document
    needed them. Curated terms (``protected``) are never proposed at all; they arrived from
    the reviewed vocabulary and their absence from the index says nothing about their worth.

    Args:
        master_tags: Registry rows, as returned by ``taxonomy_db.get_master_tags()``.
        unused: Terms with zero current usage.

    Returns:
        list[str]: Terms newly proposed for retirement.
    """
    from Evelyn.tools import memory_db

    grace_days = getattr(cfg, "TAG_RETIREMENT_GRACE_DAYS", 90)
    cutoff = time.time() - (grace_days * 86400)
    by_tag = {m["tag"]: m for m in master_tags}

    candidates = []
    for term in unused:
        row = by_tag.get(term, {})
        if row.get("protected"):
            continue  # curated vocabulary: reserved on purpose, not stale
        registered_at = row.get("created_at") or 0
        if registered_at > cutoff:
            continue  # still inside its grace period
        candidates.append(term)

    if not candidates:
        return []

    try:
        pending = memory_db.get_pending_proposals(TAG_RETIREMENT_PROPOSAL)
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[TAG LIBRARIAN] Could not read pending retirement proposals: %s", exc)
        return []

    already = {(p.get("topic") or "").strip() for p in pending}
    proposed: list[str] = []
    for term in candidates:
        if term in already:
            continue
        try:
            memory_db.insert_proposal(
                type=TAG_RETIREMENT_PROPOSAL,
                source_ids=[],
                topic=term,
                suggested_category=by_tag.get(term, {}).get("category", ""),
                reason=(
                    f"Nothing in the vault or memory has used this term for at least "
                    f"{grace_days} days. "
                    f"Approve to retire it; supply a preferred term to record an equivalence "
                    f"instead of removing it outright."
                ),
                confidence="low",
            )
        except (sqlite3.Error, OSError) as exc:
            logger.warning("[TAG LIBRARIAN] Could not propose retirement of '%s': %s", term, exc)
            continue
        proposed.append(term)

    if proposed:
        logger.info("[TAG LIBRARIAN] Proposed %d term(s) for retirement.", len(proposed))
    return proposed


def retire_term(term: str, replacement: str = "") -> bool:
    """Retire an approved term, recording an equivalence when a replacement is given.

    With a replacement the term becomes a `UF` pointer to it, so documents and queries
    phrased the retired way still resolve — which is what makes the collapse permanent
    rather than something the next extraction undoes. Without one the registry row is
    removed; the proposal itself preserves the record that the term existed.

    The relations layer is settled in the same breath. Retirement used to remove the registry
    row and leave `master_tag_related` alone, so a curated relation survived pointing at a term
    the vocabulary no longer held — expansion would follow it to nothing. With a replacement the
    relations move to it, since a rename does not change what a term is related to; without one
    they go, because a relation with a missing endpoint is broken rather than merely weaker.

    Args:
        term: The term being retired, in canonical §5 format.
        replacement: Optional preferred term to redirect to.

    Returns:
        bool: True when the registry was changed.
    """
    clean = normalize_tag_format(term)
    if not clean:
        return False
    preferred = normalize_tag_format(replacement) if replacement else ""
    if preferred and preferred != clean:
        taxonomy_db.record_alias(clean, preferred, tier="reviewed")
        moves = taxonomy_db.repoint_relations(clean, preferred)
        if any(moves.values()):
            logger.info(
                "[TAG LIBRARIAN] Relations on '%s' re-pointed to '%s': %s", clean, preferred, moves
            )
    else:
        gone = taxonomy_db.delete_relations(clean)
        if gone:
            logger.info("[TAG LIBRARIAN] Removed %d relation(s) on retired term '%s'.", gone, clean)
    taxonomy_db.delete_master_tag(clean)
    delete_tag_from_chroma(clean)
    return True


_last_census_ts = 0.0


def run_taxonomy_census_if_due(force: bool = False) -> dict[str, Any] | None:
    """Run the usage census when it is due, and otherwise cheaply do nothing.

    **`maintain_master_taxonomy()` had no reachable caller.** It is invoked from
    `scripts/master_librarian.py` behind `--rebalance-taxonomy`, and the only scheduled path to
    that script calls `run_master_librarian_task()` with no arguments — so the flag defaults to
    `False` and the census would be skipped *even with the master librarian enabled*. The other
    route is a manual endpoint nothing calls. Measured 2026-09-25: **585 of 702 registry counts
    disagreed with the live corpus.**

    Two things read those counts, so both were wrong. The vocabulary view (F5) would render
    them, and `propose_tag_retirement` runs *inside* the census — meaning the retirement path
    had never executed either, and no term has ever been proposed for retirement.

    Throttled in memory rather than persisted: the census is idempotent, and running once more
    after a restart is cheaper than a table to remember that it did.

    Args:
        force: Run regardless of when it last ran.

    Returns:
        dict[str, Any] | None: The census result, or None when it was not due.
    """
    global _last_census_ts

    hours = getattr(cfg, "TAXONOMY_CENSUS_INTERVAL_HOURS", 24)
    if not hours and not force:
        return None
    if not force and (time.time() - _last_census_ts) < (hours * 3600):
        return None

    _last_census_ts = time.time()
    result = maintain_master_taxonomy()
    logger.info(
        "[TAG LIBRARIAN] Taxonomy census: %s term count(s) corrected, %s unused, "
        "%s proposed for retirement.",
        result.get("updated_master_tags"), result.get("unused_terms"),
        result.get("retirement_proposed"),
    )
    return result


def maintain_master_taxonomy() -> dict[str, Any]:
    """Perform periodic maintenance on the master tag taxonomy table and sync to Chroma.

    Updates tag usage counts across the whole corpus — vault notes, memory facts and
    procedures alike — and proposes long-unused terms for retirement. Includes safety
    circuit breakers to prevent accidental taxonomy wipes.

    Returns:
        Dict[str, Any]: Summary of maintenance pass.
    """
    # A partial census is worse than none: every term the unread substrate holds reports
    # zero, and zero is what starts the retirement clock.
    try:
        current_counts, substrates = census_tag_usage()
    except (sqlite3.Error, OSError) as exc:
        print(f"[TAG LIBRARIAN] [SAFETY CIRCUIT BREAKER] Aborting taxonomy maintenance: census read failed: {exc}")
        return {
            "status": "aborted",
            "reason": "census_read_failed",
            "updated_master_tags": 0,
            "removed_master_tags": 0,
        }

    if not substrates["vault"]:
        print("[TAG LIBRARIAN] [SAFETY CIRCUIT BREAKER] Aborting taxonomy maintenance: vault_documents table is empty.")
        return {
            "status": "aborted",
            "reason": "empty_vault_documents",
            "updated_master_tags": 0,
            "removed_master_tags": 0,
        }

    if not current_counts:
        print("[TAG LIBRARIAN] [SAFETY CIRCUIT BREAKER] Aborting taxonomy maintenance: 0 active tags found across the corpus.")
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

    unused: list[str] = []
    tags_to_update: list[tuple[str, str, str, int]] = []

    for m in master_tags:
        t = m["tag"]
        count = current_counts.get(t, 0)
        if count == 0:
            # Maintenance no longer deletes anything. A controlled vocabulary is an
            # authority file — the set of terms judged legitimate — while usage counts
            # describe the index, which is merely what happens to be tagged right now.
            # Deleting an authority record because the index does not reference it lets
            # content churn drive vocabulary churn: remove a note and its terms vanish, so
            # restoring the note days later either re-mints them in some other surface form
            # or forces them back through review. In post-coordinate classification a term
            # with no current uses is a perfectly good axis value awaiting its first
            # document. Long-unused terms are proposed for retirement instead, and the
            # decision is a human one (see propose_tag_retirement).
            unused.append(t)
            # Not deleting is the rule above; *lying* is not part of it. The count was left
            # at whatever it last was, so a term that fell out of use kept its old number
            # forever — four such terms read `usage_count = 1` against a true zero on
            # 2026-09-25, which is precisely the set a reviewer would want to see. The row
            # stays; the number tells the truth.
            if m.get("usage_count", 0) != 0:
                tags_to_update.append(
                    (t, m.get("category", "general"), m.get("description", ""), 0)
                )
        elif count != m.get("usage_count", 0):
            tags_to_update.append((t, m.get("category", "general"), m.get("description", ""), count))

    retirement_candidates = propose_tag_retirement(master_tags, unused)

    for t, cat, desc, count in tags_to_update:
        taxonomy_db.upsert_master_tag(t, category=cat, description=desc, usage_count=count)
        index_master_tag_in_chroma(t, category=cat, description=desc, usage_count=count)

    return {
        "status": "success",
        "updated_master_tags": len(tags_to_update),
        "removed_master_tags": 0,  # maintenance never deletes; see propose_tag_retirement
        "unused_terms": len(unused),
        "retirement_proposed": len(retirement_candidates),
        "records_counted": substrates,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Obsidian Tag Librarian CLI")
    parser.add_argument("--audit-one", action="store_true", help="Audit next eligible vault document")
    parser.add_argument("--audit-batch", type=int, default=0, help="Audit N eligible vault documents")
    parser.add_argument("--seed-taxonomy", action="store_true", help="Bootstrap an empty master taxonomy from the vault index")
    parser.add_argument("--allow-populated", action="store_true",
                        help="Let --seed-taxonomy run against a registry that already holds terms (restore only)")
    parser.add_argument("--maintain-taxonomy", action="store_true", help="Perform taxonomy maintenance pass")
    parser.add_argument("--sync-vector-tags", action="store_true", help="Sync SQLite master tags to Chroma vector store")

    args = parser.parse_args()

    if args.seed_taxonomy:
        res = seed_master_taxonomy_from_vault(allow_populated=args.allow_populated)
        if res["status"] == "seeded":
            print(f"[TAG LIBRARIAN] Registered {res['registered']} new term(s) into "
                  f"master_tag_taxonomy and enqueued them for the tag vector store.")
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
