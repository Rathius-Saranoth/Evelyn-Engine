# tag_librarian.py
# date created: 2026-08-02 11:53:00
# date modified: 2026-08-28 12:27:17
# tags: #tag, #librarian, #taxonomy, #indexing, #obsidian, #idle_time, #rag, #chromadb

"""
tag_librarian.py — Incremental Obsidian Tag Maintenance & Taxonomy Management.

Exports:
    is_excluded_tag()                     — Checks if a tag matches protected exclusion rules (e.g. CY-YYYY/MM/DD).
    normalize_tag_format()                — Standardizes multi-word tags (hyphens), entities (underscores), and paths.
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
from Evelyn.tools import chroma_rag, vault_db
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
    excluded_paths = getattr(cfg, "TAG_LIBRARIAN_EXCLUDED_DOCUMENTS", [])
    for ex in excluded_paths:
        clean_ex = ex.replace("\\", "/").strip()
        if clean_path == clean_ex or clean_path.endswith("/" + clean_ex):
            return True
    return False


def normalize_tag_format(tag: str, is_entity: bool | None = None) -> str:
    """Normalize a tag string according to project formatting standards.

    Rules:
    - Protected tags (e.g. CY-YYYY/MM/DD) are preserved.
    - Proper Nouns / Entities (Person, Place, Thing, Title - detected by Capitalized/CamelCase words
      or explicit is_entity flag) use TitleCase with underscores (e.g. 'Dungeon_Crawler_Carl', 'Ricky_Sekulich').
    - General concepts (lowercase) use hyphens for multi-word phrases (e.g. 'home-improvement', 'system-update').
    - Hierarchy slashes (e.g. '3D-Printing/Slicing', 'Tech/Python/FastAPI') are preserved.

    Args:
        tag: Raw tag string (e.g. 'home_improvement' or 'DungeonCrawlerCarl').
        is_entity: Optional explicit boolean override for entity classification.

    Returns:
        str: Normalized tag string.
    """
    clean = tag.strip().lstrip("#").strip()
    if not clean or is_excluded_tag(clean):
        return clean

    # Strip redundant legacy noise prefixes (kw/, ctx/) to prevent bloated kw/ folders
    if clean.lower().startswith("kw/"):
        clean = clean[3:].strip()
    elif clean.lower().startswith("ctx/"):
        clean = clean[4:].strip()

    if not clean or is_excluded_tag(clean):
        return clean

    parts = clean.split("/")
    norm_parts = []

    for part in parts:
        p = part.strip()
        if not p:
            continue

        # Determine if part is an entity (Proper Noun: Person, Place, Thing, Title)
        part_is_entity = is_entity if is_entity is not None else any(c.isupper() for c in p)

        if part_is_entity:
            # Handle CamelCase insertion before splitting on spaces/hyphens/underscores
            s1 = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', p)
            words = [w.capitalize() for w in re.split(r"[\s_-]+", s1) if w]
            norm_parts.append("_".join(words))
        else:
            # Standard multi-word concept -> lowercase hyphens
            words = [w.lower() for w in re.split(r"[\s_-]+", p) if w]
            norm_parts.append("-".join(words))

    return "/".join(norm_parts)


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
    master_tags = vault_db.get_master_tags()
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
                if not tag or is_excluded_tag(tag):
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
        fallback_tags = vault_db.get_master_tags()
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
        options={"temperature": 0.2, "num_predict": 1024},
        timeout=120,
    )


def audit_single_document(doc_path: str | None = None) -> dict[str, Any]:
    """Audit a single vault document against the Master Tag Taxonomy using Tag RAG.

    Args:
        doc_path: Optional relative path of document to audit. If None, fetches next.

    Returns:
        Dict[str, Any]: Result summary dict with status, path, tags_added, tags_removed.
    """
    if not doc_path:
        doc_info = vault_db.fetch_next_document_for_tag_audit()
        if not doc_info:
            return {"status": "empty", "message": "No documents found in vault DB."}
        doc_path = doc_info["path"]
        gist = doc_info.get("gist", "")
        title = doc_info.get("title", "")
    else:
        doc_info = vault_db.get_document(doc_path)
        gist = doc_info.get("gist", "") if doc_info else ""
        title = os.path.basename(doc_path)

    # Check document path exclusions
    if is_excluded_document(doc_path):
        vault_db.update_document_tag_audit(doc_path)
        return {"status": "skipped", "path": doc_path, "message": "Document path is excluded from tag auditing."}

    # Resolve absolute file path
    try:
        abs_path = str(to_vault_abspath(doc_path))
    except (ValueError, TypeError):
        abs_path = doc_path if os.path.isabs(doc_path) else os.path.join(VAULT_ROOT, doc_path)
    if not os.path.exists(abs_path):
        vault_db.update_document_tag_audit(doc_path)
        return {"status": "error", "path": doc_path, "message": "File not found on disk."}

    try:
        with open(abs_path, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        vault_db.update_document_tag_audit(doc_path)
        return {"status": "error", "path": doc_path, "message": f"Read error: {e}"}

    current_tags, body = parse_frontmatter_tags(content)

    # Identify protected tags (e.g. CY-YYYY/MM/DD) and normalize auditable tags up front
    protected_tags = [t for t in current_tags if is_excluded_tag(t)]
    auditable_tags = [normalize_tag_format(t) for t in current_tags if not is_excluded_tag(t)]

    # Semantic Tag RAG: Retrieve candidate master tags and novelty guidance based on document content
    candidate_tags, min_dist, novelty_guidance = retrieve_candidate_tags_for_document(
        title=title,
        gist=gist,
        body_sample=body[:1500],
        current_tags=auditable_tags
    )

    candidate_list_text = "\n".join([
        f"- #{c['tag']} (category: {c['category']}, match distance: {c['distance']:.2f}): {c['description'] or 'No description'}"
        for c in candidate_tags
    ]) if candidate_tags else "No existing master tags matched."

    system_prompt = (
        "You are an expert taxonomy librarian maintaining a structured, nested tag hierarchy for a personal Obsidian knowledge vault.\n"
        "Your goal is to organize notes under clear, domain-level nested tags that reduce clutter, group related concepts, and resolve ambiguous terms using note context.\n\n"
        "Taxonomy & Nesting Principles:\n"
        "1. Domain-Level Hierarchies: Group flat concepts into logical multi-tier domains using forward slashes (e.g. #3D-Printing/Slicing, #3D-Modeling/Topology, #AI/LLM/Inference, #AI/RAG/Evaluation, #Mood/Peace, #Lore/Worldbuilding, #Contact/Friend, #Media/Game).\n"
        "2. Semantic & Contextual Disambiguation: Use the full context of the note to disambiguate polysemous or broad words:\n"
        "   - 'corruption' -> #Lore/Corruption (fantasy/magic), #Politics/Corruption, #Psychology/Corruption\n"
        "   - 'mesh' -> #3D-Modeling/Mesh, #Networking/Mesh-Topology\n"
        "   - 'memory' -> #AI/LLM/Memory, #Psychology/Memory, #Hardware/RAM\n"
        "   - 'peace' / 'anxiety' / 'reflection' -> #Mood/Peace, #Mood/Anxiety, #Mood/Reflection\n"
        "3. Tag Formatting Rules:\n"
        "   - General semantic concepts MUST use lowercase hyphens for multi-word segments (e.g. 'home-improvement', 'system-update', 'peace-of-mind').\n"
        "   - Proper Nouns / Entities (Person, Place, Thing, Title, Media) MUST use TitleCase with underscores (e.g. 'Ricky_Sekulich', 'Dungeon_Crawler_Carl', 'Evelyn_Engine').\n"
        "   - Sub-hierarchies use forward slashes (e.g. 'Tech/Python/FastAPI', 'Journal/Reflections').\n"
        "4. Clean Replacement: Replace overly flat, vague, or cluttered tags with clean nested equivalents (put old flat tags in 'tags_to_remove' and new nested tags in 'tags_to_add').\n"
        "5. Output Format: Return ONLY a valid JSON object with the following fields:\n"
        "{\n"
        '  "tags_to_keep": ["tag1", "tag2"],\n'
        '  "tags_to_add": ["domain/subdomain/tag3"],\n'
        '  "tags_to_remove": ["flat-tag-being-replaced"],\n'
        '  "new_master_tags": [{"tag": "domain/subdomain/tag3", "category": "domain", "description": "1-sentence scope"}]\n'
        "}"
    )

    user_prompt = (
        f"Document Title: {title}\n"
        f"Document Path: {doc_path}\n"
        f"Document Summary/Gist: {gist}\n"
        f"Current Auditable Tags: {auditable_tags}\n\n"
        f"--- SEMANTICALLY MATCHED MASTER TAGS (TAG RAG) ---\n"
        f"{candidate_list_text}\n\n"
        f"--- NOVELTY & ALIGNMENT GUIDANCE ---\n"
        f"{novelty_guidance}\n\n"
        f"--- NOTE CONTENT SAMPLE ---\n"
        f"'''\n{body[:1500]}\n'''\n\n"
        "Evaluate tag suitability for this document. Select 2-5 highly relevant tags from the Master Taxonomy or suggest new nested tags if appropriate."
    )

    response_text = query_ollama(user_prompt, system_prompt)

    tags_to_keep = auditable_tags
    tags_to_add = []
    tags_to_remove = []
    new_masters = []

    try:
        json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group(0))
            tags_to_keep = [normalize_tag_format(t) for t in parsed.get("tags_to_keep", []) if t]
            tags_to_add = [normalize_tag_format(t) for t in parsed.get("tags_to_add", []) if t]
            tags_to_remove = [normalize_tag_format(t) for t in parsed.get("tags_to_remove", []) if t]
            new_masters = parsed.get("new_master_tags", [])
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        print(f"[TAG LIBRARIAN] JSON parse fallback for {doc_path}: {e}")

    # Build final tag set: protected date tags + kept + added - removed
    final_tags_set = set(protected_tags)
    for t in tags_to_keep:
        if t: final_tags_set.add(t)
    for t in tags_to_add:
        if t: final_tags_set.add(t)
    for t in tags_to_remove:
        if t in final_tags_set and not is_excluded_tag(t):
            final_tags_set.remove(t)

    final_tags_list = sorted(final_tags_set)
    tags_str = ", ".join(final_tags_list)

    # Save changes if tags modified
    modified = (set(final_tags_list) != set(current_tags))
    if modified:
        new_content = update_frontmatter_tags(content, final_tags_list)
        try:
            write_file_with_frontmatter(abs_path, new_content, preserve_mtime=True)
            # Re-index modified note in Chroma DB memory collection
            try:
                target_col = getattr(cfg, "CHROMA_MEMORY_COLLECTION", "evelyn_memory")
                chroma_rag.ingest_markdown_file(
                    file_path=abs_path,
                    content=new_content,
                    collection_name=target_col,
                    extra_metadata={"tags": tags_str}
                )
            except (sqlite3.Error, OSError, RuntimeError, ValueError) as ve:
                print(f"[TAG LIBRARIAN] Single-file vector update skipped: {ve}")
        except OSError as e:
            vault_db.update_document_tag_audit(doc_path)
            return {"status": "error", "path": doc_path, "message": f"Write error: {e}"}

    # Record new master tags in SQLite and index into Chroma Tag Taxonomy
    for m in new_masters:
        ntag = normalize_tag_format(m.get("tag", ""))
        if ntag and not is_excluded_tag(ntag):
            category = m.get("category", ntag.split("/")[0] if "/" in ntag else "general")
            desc = m.get("description", f"Obsidian notes tagged under {ntag}")
            vault_db.upsert_master_tag(ntag, category=category, description=desc, usage_count=1)
            index_master_tag_in_chroma(ntag, category=category, description=desc, usage_count=1)

    # Update database audit timestamp & tags
    vault_db.update_document_tag_audit(doc_path, tags=tags_str)

    return {
        "status": "success",
        "path": doc_path,
        "modified": modified,
        "previous_tags": current_tags,
        "final_tags": final_tags_list,
        "protected_tags": protected_tags,
        "min_taxonomy_distance": min_dist
    }


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
        vault_db.upsert_master_tag(tag, category=category, description=desc, usage_count=count)

    # Sync all seeded tags into Chroma vector store
    sync_master_tags_to_vector_db()

    return len(tag_counts)


def maintain_master_taxonomy() -> dict[str, Any]:
    """Perform periodic maintenance on the master tag taxonomy table and sync to Chroma.

    Updates tag usage counts across the vault and removes zero-usage tags.

    Returns:
        Dict[str, Any]: Summary of maintenance pass.
    """
    docs = vault_db.get_all_documents()
    current_counts: dict[str, int] = {}

    for doc in docs:
        raw_tags = doc.get("tags") or ""
        if not raw_tags:
            continue
        for t in raw_tags.split(","):
            clean = normalize_tag_format(t)
            if clean and not is_excluded_tag(clean):
                current_counts[clean] = current_counts.get(clean, 0) + 1

    master_tags = vault_db.get_master_tags()
    updated_count = 0
    removed_count = 0

    for m in master_tags:
        t = m["tag"]
        count = current_counts.get(t, 0)
        if count == 0:
            vault_db.delete_master_tag(t)
            delete_tag_from_chroma(t)
            removed_count += 1
        elif count != m["usage_count"]:
            vault_db.upsert_master_tag(t, category=m["category"], description=m["description"], usage_count=count)
            updated_count += 1

    # Sync any updated counts to Chroma
    sync_master_tags_to_vector_db()

    return {
        "status": "success",
        "updated_master_tags": updated_count,
        "removed_master_tags": removed_count
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
