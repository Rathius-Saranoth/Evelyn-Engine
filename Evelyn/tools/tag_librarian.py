# tag_librarian.py
# date created: 2026-08-02 11:53:00
# date modified: 2026-09-18 21:33:51
# tags: #tag, #librarian, #taxonomy, #indexing, #obsidian, #idle_time, #rag, #chromadb

"""
tag_librarian.py — Incremental Obsidian Tag Maintenance & Taxonomy Management.

Exports:
    is_excluded_tag()                     — Checks if a tag matches protected exclusion rules (e.g. CY-YYYY/MM/DD).
    canonicalize_date_tag()               — Resolves EDTF date anchors (reduced precision / unspecified digits).
    normalize_tag_format()                — Standardizes tags to lowercase-hyphen-slash form (taxonomy §5).
    strip_subject_duplicate_tags()        — Drops tags that merely restate a record's own subject.
    audit_single_document()               — Audits one vault note against the Master Tag Taxonomy during idle windows using Tag RAG.
    retrieve_candidate_tags_for_document() — Semantic vector retrieval of candidate master tags with distance scoring.
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

def _build_tag_embedding_doc(tag: str, category: str = "", description: str = "") -> str:
    """Build rich descriptive text for embedding a taxonomy tag in Chroma."""
    parts = tag.split("/")
    hierarchy = " > ".join(parts)
    cat_str = category or (parts[0] if parts else "general")
    desc_str = description or f"Obsidian notes tagged under {tag}"
    return (
        f"Tag: #{tag}\n"
        f"Category: {cat_str}\n"
        f"Hierarchy: {hierarchy}\n"
        f"Scope & Scope Description: {desc_str}"
    )


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
        doc_text = _build_tag_embedding_doc(clean_tag, category, description)
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
    """Synchronize all SQLite master taxonomy tags into Chroma vector store via staging queue.

    Returns:
        int: Total number of tags enqueued into Chroma staging queue.
    """
    master_tags = taxonomy_db.get_master_tags()
    if not master_tags:
        return 0

    enqueued_count = 0
    for m in master_tags:
        tag = normalize_tag_format(m["tag"])
        if not tag or is_excluded_tag(tag):
            continue
        category = m.get("category", tag.split("/")[0] if "/" in tag else "general")
        description = m.get("description", "")
        usage_count = m.get("usage_count", 0)

        doc_id = f"tag::{tag}"
        doc_text = _build_tag_embedding_doc(tag, category, description)
        meta = {
            "tag": tag,
            "category": category,
            "description": description,
            "usage_count": usage_count,
            "type": "master_tag"
        }
        if chroma_rag.enqueue_upsert(doc_id, doc_text, collection_name=TAG_COLLECTION_NAME, extra_metadata=meta):
            enqueued_count += 1

    return enqueued_count


def retrieve_candidate_tags_for_document(
    title: str,
    gist: str,
    body_sample: str,
    current_tags: list[str],
    top_k: int | None = None
) -> tuple[list[dict[str, Any]], float, str]:
    """Retrieve semantically relevant candidate master tags for a document using Tag RAG.

    Uses a composite query approach (title + gist, sample body, and current tags)
    and computes cosine distance to evaluate taxonomy alignment and novelty.

    Args:
        title: Document title.
        gist: Document summary or gist.
        body_sample: Initial text chunk of note body.
        current_tags: Existing tags on the note.
        top_k: Maximum candidate tags to retrieve.

    Returns:
        Tuple[List[Dict[str, Any]], float, str]:
            - List of candidate tag dictionaries (tag, category, description, distance, usage_count).
            - Minimum cosine distance found.
            - Novelty guidance directive for the LLM.
    """
    if top_k is None:
        top_k = getattr(cfg, "TAG_LIBRARIAN_TOP_K_TAGS", 35)

    queries = []
    # 1. Semantic metadata query (title + summary)
    meta_query = f"{title}. {gist}".strip()
    if meta_query:
        queries.append(meta_query)

    # 2. Body sample query
    sample_clean = body_sample[:600].strip()
    if sample_clean:
        queries.append(sample_clean)

    # 3. Taxonomic query from current tags
    if current_tags:
        clean_tags_query = " ".join([
            t.replace("/", " ").replace("-", " ").replace("_", " ")
            for t in current_tags if not is_excluded_tag(t)
        ]).strip()
        if clean_tags_query:
            queries.append(clean_tags_query)

    if not queries:
        return [], 1.0, "NO_QUERY_AVAILABLE"

    candidates_map: dict[str, dict[str, Any]] = {}

    for q in queries:
        try:
            results = chroma_rag.query_collection(q, TAG_COLLECTION_NAME, n_results=top_k)
            for r in results:
                meta = r.get("metadata") or {}
                tag = meta.get("tag")
                if not tag or is_excluded_tag(tag) or tag in current_tags:
                    continue
                dist = float(r.get("distance", 1.0))

                if tag not in candidates_map or dist < candidates_map[tag]["distance"]:
                    candidates_map[tag] = {
                        "tag": tag,
                        "category": meta.get("category", "general"),
                        "description": meta.get("description", ""),
                        "usage_count": meta.get("usage_count", 0),
                        "distance": dist
                    }
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
            print(f"[TAG LIBRARIAN] Tag RAG query failed for '{q[:30]}...': {e}")

    # If Chroma tag collection is empty or query had no results, fallback to SQLite master tags
    if not candidates_map:
        fallback_tags = taxonomy_db.get_master_tags()
        for m in fallback_tags[:top_k]:
            t = m["tag"]
            if not is_excluded_tag(t):
                candidates_map[t] = {
                    "tag": t,
                    "category": m.get("category", "general"),
                    "description": m.get("description", ""),
                    "usage_count": m.get("usage_count", 0),
                    "distance": 0.50
                }

    sorted_candidates = sorted(candidates_map.values(), key=lambda x: x["distance"])[:top_k]
    min_dist = sorted_candidates[0]["distance"] if sorted_candidates else 1.0
    novelty_threshold = getattr(cfg, "TAG_NOVELTY_DISTANCE_THRESHOLD", 0.55)

    if min_dist < 0.40:
        novelty_guidance = (
            f"TAXONOMY MATCH CONFIDENCE: HIGH (Nearest match distance: {min_dist:.2f}).\n"
            "Strong domain alignment exists in the Master Taxonomy. Strictly adhere to existing parent hierarchies "
            "or add specific child tags if the document covers a narrower specialization."
        )
    elif min_dist < novelty_threshold:
        novelty_guidance = (
            f"TAXONOMY MATCH CONFIDENCE: MODERATE (Nearest match distance: {min_dist:.2f}).\n"
            "Related parent domains found, but this note may represent a distinct sub-domain or angle. "
            "You may extend existing parent branches (e.g. '3D-Printing/...', 'AI/LLM/...') or introduce a clean nested category."
        )
    else:
        novelty_guidance = (
            f"TAXONOMY MATCH CONFIDENCE: LOW / NOVEL DOMAIN (Nearest match distance: {min_dist:.2f}).\n"
            "This document introduces concepts not well-covered by existing taxonomy. "
            "You are EXPLICITLY ENCOURAGED to mint new domain-level tag hierarchies (e.g. #Domain/Subtopic or #Domain/Subdomain/Topic)."
        )

    return sorted_candidates, min_dist, novelty_guidance


def query_ollama(prompt: str, system_prompt: str = "") -> str:
    """Query local Ollama instance synchronously for LLM reasoning.

    Args:
        prompt: User prompt string.
        system_prompt: Optional system instruction.

    Returns:
        str: Raw response text from model.
    """
    return _canonical_query_ollama(
        prompt=prompt,
        system=system_prompt if system_prompt else None,
        options={"temperature": 0.1, "num_predict": 2048},
        timeout=60,
        think=True,
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
        "new_masters": [],
    }

    # 2. Semantic LLM Tag RAG (if enabled and applicable)
    if enable_llm:
        title = os.path.basename(path).replace(".md", "") if path else "Untitled"
        doc_info = vault_db.get_document(path) if path else None
        gist = doc_info.get("gist", "") if doc_info else ""

        candidate_tags, min_dist, _novelty_guidance = retrieve_candidate_tags_for_document(
            title=title,
            gist=gist,
            body_sample=body[:1500],
            current_tags=auditable_tags,
        )
        details["min_taxonomy_distance"] = min_dist

        candidate_list_text = (
            "\n".join([
                f"- #{c.get('tag', '')} ({c.get('category', 'general')})"
                for c in candidate_tags
                if c.get("tag")
            ])
            if candidate_tags
            else "No existing master tags matched."
        )

        # Summarize auditable tags if extensive to prevent model deliberation loops
        if len(auditable_tags) > 6:
            tag_summary = f"{len(auditable_tags)} legacy tags (including: {', '.join(auditable_tags[:5])} ...)"
        else:
            tag_summary = ", ".join(auditable_tags) if auditable_tags else "none"

        system_prompt = (
            "You are a professional library cataloger maintaining a Faceted Classification system for a personal knowledge vault.\n"
            "Your role mirrors a research librarian filing books: you classify documents by SUBJECT MATTER, not by the author's role or employer.\n\n"
            "CLASSIFICATION RULES:\n"
            "1. DOMAIN TAGS: Assign tags for each genuine subject the document covers.\n"
            "   - Use nested Domain/Subdomain only when specialization warrants it (e.g. #Tech/GIS, #Craft/Tailoring, #Music/Cello, #Science/Geology).\n"
            "   - A document on GIS gets #Tech/GIS — not #Work or #Job. A document on genealogy gets #Genealogy — not #Hobby.\n"
            "   - Multi-topic documents MUST receive a tag for EACH meaningful subject area. Do not collapse them into one tag.\n"
            "2. TYPE/FORM FACETS: Assign exactly one orthogonal facet for the document's form or nature:\n"
            "   - #type/overview, #type/manual, #type/guide, #type/journal-entry, #type/reference, #type/list, #type/recipe, #type/notes, #type/log\n"
            "   - This facet is SEPARATE from domain tags — never mix form with subject.\n"
            "3. EXISTING TAGS: Evaluate each current tag for correctness and format.\n"
            "   - Tags already in Domain/Subdomain or Domain/Subdomain/Leaf format are ALREADY WELL-CATALOGED structured facets — treat them as correct and preserve them unless they are factually wrong about the document's content.\n"
            "   - Only flat or un-nested tags (e.g. 'gis-technician', 'dream-journal') need reformatting into a proper hierarchy.\n"
            "   - Remove only tags that are genuinely wrong, exact redundant duplicates, or meaningless noise.\n"
            "4. QUANTITY GUIDE: Simple notes: 2-4 tags total. Multi-topic reference documents: as many domain tags as genuinely needed. Never pad; never truncate coverage.\n"
            "5. FORMAT: All tags use Title-Case with hyphens for multi-word terms. Subdomains separated by /. No spaces.\n\n"
            "Output valid JSON only with fields: tags_to_keep, tags_to_add, tags_to_remove, new_master_tags.\n"
            "CRITICAL: Decide once, output JSON immediately. Do NOT loop or debate."
        )

        user_prompt = (
            f"Document Title: {title}\n"
            f"Document Path: {path}\n"
            f"Current Tags: {tag_summary}\n\n"
            f"--- CANDIDATE MASTER TAGS (from vault taxonomy) ---\n"
            f"{candidate_list_text}\n\n"
            f"--- DOCUMENT SKELETON (headings + gist + opening) ---\n"
            f"'''\n{_extract_document_skeleton(body, gist)}\n'''\n\n"
            "Classify this document using Faceted Classification.\n"
            "1. Assess each current tag: keep if accurate and well-formatted, reformat if needed, remove if wrong or redundant.\n"
            "2. Add domain tags for any subject areas not yet covered.\n"
            "3. Add exactly one #type/ facet for the document's form.\n"
            "4. Do not collapse a multi-topic document into fewer than its genuine subject areas.\n"
            "Output JSON immediately."
        )

        # Fix 1: High-tag-count bypass — docs with >15 legacy tags need mechanical
        # reformatting, not deep reasoning. Routing think=True on 15+ tags causes the
        # model to deliberate over each one individually, reliably hitting the timeout.
        # Route directly to think=False for these; the result is faster and equally good.
        use_fast_path = len(auditable_tags) > 15
        if use_fast_path:
            logger.info("High tag count (%d tags) for %s — routing directly to fast-path inference.", len(auditable_tags), path)

        try:
            if use_fast_path:
                response_text = _canonical_query_ollama(
                    prompt=user_prompt,
                    system=system_prompt,
                    options={"temperature": 0.1, "num_predict": 1536},
                    timeout=60,
                    think=False,
                )
            else:
                response_text = query_ollama(user_prompt, system_prompt)
            json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if not json_match:
                logger.info("Reasoning token limit reached or no JSON for %s; retrying with direct inference fallback.", path)
                response_text = _canonical_query_ollama(
                    prompt=user_prompt,
                    system=system_prompt,
                    options={"temperature": 0.1, "num_predict": 1536},
                    timeout=60,
                    think=False,
                )
                json_match = re.search(r"\{.*\}", response_text, re.DOTALL)

            if json_match:
                parsed = json.loads(json_match.group(0))
                tags_to_keep = [normalize_tag_format(t) for t in parsed.get("tags_to_keep", []) if t]
                tags_to_add = [normalize_tag_format(t) for t in parsed.get("tags_to_add", []) if t]
                tags_to_remove = [normalize_tag_format(t) for t in parsed.get("tags_to_remove", []) if t]
                new_masters = parsed.get("new_master_tags", [])

                working_set = set(protected_tags)
                for t in tags_to_keep:
                    if t:
                        working_set.add(t)
                for t in tags_to_add:
                    if t:
                        working_set.add(t)
                for t in tags_to_remove:
                    if t in working_set and not is_excluded_tag(t):
                        working_set.remove(t)

                final_tags_list = sorted(working_set)
                details["final_tags"] = final_tags_list
                details["llm_evaluated"] = True
                details["new_masters"] = new_masters

                # Upsert newly minted master tags
                for m in new_masters:
                    if isinstance(m, dict):
                        ntag = normalize_tag_format(m.get("tag", ""))
                        cat = m.get("category", ntag.split("/")[0] if "/" in ntag else "general")
                        desc = m.get("description", f"Obsidian notes tagged under {ntag}")
                    elif isinstance(m, str):
                        ntag = normalize_tag_format(m)
                        cat = ntag.split("/")[0] if "/" in ntag else "general"
                        desc = f"Obsidian notes tagged under {ntag}"
                    else:
                        continue
                    if ntag and not is_excluded_tag(ntag):
                        taxonomy_db.upsert_master_tag(ntag, category=cat, description=desc, usage_count=1)
                        index_master_tag_in_chroma(ntag, category=cat, description=desc, usage_count=1)
        except Exception as llm_err:  # noqa: BLE001
            print(f"[TAG LIBRARIAN] LLM semantic tagging failed for {path}: {llm_err}")

        if not details["llm_evaluated"]:
            print(f"[TAG LIBRARIAN] Warning: LLM semantic tagging did not yield a valid JSON decision for {path}")

    # Recorded UF equivalences are applied last, so a retired variant reaching this point
    # from any source resolves to its preferred term instead of being re-minted (§6.2).
    final_tags_list = taxonomy_db.canonicalize_tags(final_tags_list)
    details["final_tags"] = final_tags_list

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
        "min_taxonomy_distance": details.get("min_taxonomy_distance", 1.0),
        "new_masters": details.get("new_masters", []),
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

    for m in master_tags:
        t = m["tag"]
        count = current_counts.get(t, 0)
        if count == 0:
            tags_to_delete.append(t)
        elif count != m.get("usage_count", 0):
            tags_to_update.append((t, m.get("category", "general"), m.get("description", ""), count))

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
