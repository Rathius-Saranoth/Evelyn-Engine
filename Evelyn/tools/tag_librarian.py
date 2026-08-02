# tag_librarian.py
# date created: 2026-08-02 11:53:00
# date modified: 2026-08-02 12:15:34
# tags: #tag, #librarian, #taxonomy, #indexing, #obsidian, #idle_time

"""
tag_librarian.py — Incremental Obsidian Tag Maintenance & Taxonomy Management.

Exports:
    is_excluded_tag()          — Checks if a tag matches protected exclusion rules (e.g. CY-YYYY/MM/DD).
    normalize_tag_format()     — Standardizes multi-word tags (hyphens), entities (underscores), and paths.
    audit_single_document()    — Audits one vault note against the Master Tag Taxonomy during idle windows.
    maintain_master_taxonomy() — Cleans up stale/unused master tags and keeps tag counts balanced.
    seed_master_taxonomy_from_vault() — Seeds initial master tags from current vault index.

Key config: evelyn_config.py (TAG_LIBRARIAN_EXCLUSIONS, TAG_LIBRARIAN_FORMAT_RULES)
See also: reference/engine_architecture.md
"""

import os
import re
import sys
import json
import time
import urllib.request
import urllib.parse
from typing import Dict, Any, List, Optional, Tuple

# Ensure parent path for imports
sys.path.insert(0, r"C:\Projects\LocalAI")
import evelyn_config as cfg
from Evelyn.tools import vault_db

VAULT_ROOT = r"G:\My Drive\Obsidian_Vault"


def is_excluded_tag(tag: str) -> bool:
    """Check if a tag matches any protected exclusion pattern.

    Args:
        tag: Tag string to evaluate (e.g. 'CY-2026/08/02' or 'tech/python').

    Returns:
        bool: True if the tag is protected from modification/removal, False otherwise.
    """
    clean_tag = tag.strip().lstrip("#")
    exclusions = getattr(cfg, "TAG_LIBRARIAN_EXCLUSIONS", [r"^CY-\d{4}/\d{2}/\d{2}$"])
    
    for pattern in exclusions:
        if re.search(pattern, clean_tag, re.IGNORECASE):
            return True
    return False


def normalize_tag_format(tag: str, is_entity: Optional[bool] = None) -> str:
    """Normalize a tag string according to project formatting standards.

    Rules:
    - Protected tags (e.g. CY-YYYY/MM/DD) are preserved.
    - Proper Nouns / Entities (Person, Place, Thing, Title - detected by Capitalized/CamelCase words
      or explicit is_entity flag) use TitleCase with underscores (e.g. 'Dungeon_Crawler_Carl', 'Ricky_Sekulich').
    - General concepts (lowercase) use hyphens for multi-word phrases (e.g. 'home-improvement', 'system-update').
    - Hierarchy slashes (e.g. 'tech/python/fastapi') are preserved.

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
        # If is_entity is explicitly passed, use it.
        # Otherwise, check if string contains any uppercase characters (CamelCase or TitleCase).
        part_is_entity = is_entity if is_entity is not None else any(c.isupper() for c in p)

        if part_is_entity:
            # Handle CamelCase insertion before splitting on spaces/hyphens/underscores
            # e.g., 'DungeonCrawlerCarl' -> 'Dungeon Crawler Carl'
            s1 = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', p)
            words = [w.capitalize() for w in re.split(r"[\s_-]+", s1) if w]
            norm_parts.append("_".join(words))
        else:
            # Standard multi-word concept -> lowercase hyphens
            words = [w.lower() for w in re.split(r"[\s_-]+", p) if w]
            norm_parts.append("-".join(words))

    return "/".join(norm_parts)



def parse_frontmatter_tags(content: str) -> Tuple[List[str], str]:
    """Extract frontmatter tags and return (tags_list, body_content).

    Args:
        content: Raw markdown note text.

    Returns:
        Tuple[List[str], str]: List of current tags and remaining document text.
    """
    tags = []
    body = content
    
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            fm_text = parts[1]
            body = parts[2]
            
            fm_lines = fm_text.split("\n")
            in_tags_block = False
            
            for line in fm_lines:
                stripped = line.strip()
                if line.startswith("tags:"):
                    raw = line[5:].strip()
                    if raw:
                        if raw.startswith("[") and raw.endswith("]"):
                            items = raw[1:-1].split(",")
                        else:
                            items = raw.split(",")
                        tags.extend([t.strip().strip("'\"#") for t in items if t.strip()])
                    else:
                        in_tags_block = True
                elif in_tags_block:
                    if stripped.startswith("- "):
                        tag_val = stripped[2:].strip().strip("'\"#")
                        if tag_val:
                            tags.append(tag_val)
                    elif ":" in stripped or stripped == "":
                        in_tags_block = False
                        
    return tags, body


def update_frontmatter_tags(content: str, updated_tags: List[str]) -> str:
    """Update or inject YAML frontmatter tags in markdown content cleanly.

    Args:
        content: Original markdown file content.
        updated_tags: Deduplicated list of normalized tag strings.

    Returns:
        str: Updated markdown content with formatted frontmatter.
    """
    clean_tags = sorted(list(dict.fromkeys([t.strip().lstrip("#") for t in updated_tags if t.strip()])))
    tags_str = f"[{', '.join(clean_tags)}]" if clean_tags else "[]"
    
    lines = content.split("\n")
    if content.startswith("---"):
        # Existing frontmatter block
        out_lines = []
        in_fm = True
        tags_updated = False
        skipping_multiline_tags = False
        
        out_lines.append(lines[0])  # '---'
        for line in lines[1:]:
            if in_fm and line.strip() == "---":
                if not tags_updated:
                    out_lines.append(f"tags: {tags_str}")
                in_fm = False
                out_lines.append(line)
                continue
                
            if in_fm:
                if line.startswith("tags:"):
                    out_lines.append(f"tags: {tags_str}")
                    tags_updated = True
                    skipping_multiline_tags = True
                elif skipping_multiline_tags:
                    if line.strip().startswith("- "):
                        continue  # Skip multiline tag items
                    else:
                        skipping_multiline_tags = False
                        out_lines.append(line)
                else:
                    out_lines.append(line)
            else:
                out_lines.append(line)
                
        return "\n".join(out_lines)
    else:
        # Prepend new frontmatter
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        fm = [
            "---",
            f"date created: {now_str}",
            f"date modified: {now_str}",
            f"tags: {tags_str}",
            "---",
            ""
        ]
        return "\n".join(fm) + content.lstrip("\n")



def query_ollama(prompt: str, system_prompt: str = "") -> str:
    """Query local Ollama instance synchronously for LLM reasoning.

    Args:
        prompt: User prompt string.
        system_prompt: Optional system instruction.

    Returns:
        str: Raw response text from model.
    """
    url = f"{cfg.OLLAMA_URL}/api/generate"
    payload = {
        "model": cfg.MODEL_NAME,
        "prompt": prompt,
        "system": system_prompt,
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_predict": 2048,
        }
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            res_text = data.get("response", "")
            # Strip <think> tags if present
            res_text = re.sub(r"<think>.*?</think>", "", res_text, flags=re.DOTALL).strip()
            return res_text
    except Exception as e:
        print(f"[TAG LIBRARIAN] Ollama query failed: {e}")
        return ""


def audit_single_document(doc_path: Optional[str] = None) -> Dict[str, Any]:
    """Audit a single vault document against the Master Tag Taxonomy.

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

    # Resolve absolute file path
    abs_path = doc_path if os.path.isabs(doc_path) else os.path.join(VAULT_ROOT, doc_path)
    if not os.path.exists(abs_path):
        # Update DB to record attempt
        vault_db.update_document_tag_audit(doc_path)
        return {"status": "error", "path": doc_path, "message": "File not found on disk."}

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        vault_db.update_document_tag_audit(doc_path)
        return {"status": "error", "path": doc_path, "message": f"Read error: {e}"}

    current_tags, body = parse_frontmatter_tags(content)
    
    # Identify protected tags (e.g. CY-YYYY/MM/DD) and normalize auditable tags up front
    protected_tags = [t for t in current_tags if is_excluded_tag(t)]
    auditable_tags = [normalize_tag_format(t) for t in current_tags if not is_excluded_tag(t)]

    # Fetch active master taxonomy
    master_tags = vault_db.get_master_tags()
    master_list_text = "\n".join([
        f"- #{m['tag']} ({m['category']}): {m['description'] or 'No description'}"
        for m in master_tags[:150]  # Cap to prevent prompt overflow
    ]) if master_tags else "No master tags indexed yet."

    system_prompt = (
        "You are an expert librarian and bookseller maintaining a precise tag taxonomy for a personal knowledge vault.\n"
        "Your goal is to ensure notes have clear, relevant, nested tags that are neither too vague nor overly specific.\n"
        "Formatting rules:\n"
        "- General semantic concepts MUST use lowercase hyphens for multi-word phrases (e.g. 'home-improvement', 'system-update', 'recovery-journey', 'peace-of-mind').\n"
        "- Proper Nouns / Entities (Person, Place, Thing, Title, Media) MUST use TitleCase with underscores (e.g. 'Ricky_Sekulich', 'Dungeon_Crawler_Carl', 'Evelyn_Engine').\n"
        "- Sub-hierarchies use slashes (e.g. 'tech/python/fastapi', 'journal/reflections').\n"
        "Return ONLY a valid JSON object with the following fields:\n"
        "{\n"
        '  "tags_to_keep": ["tag1", "tag2"],\n'
        '  "tags_to_add": ["tag3"],\n'
        '  "tags_to_remove": ["tag4"],\n'
        '  "new_master_tags": [{"tag": "cat/name", "category": "cat", "description": "1-sentence scope"}]\n'
        "}"
    )


    user_prompt = (
        f"Document Title: {title}\n"
        f"Document Path: {doc_path}\n"
        f"Document Summary/Gist: {gist}\n"
        f"Current Auditable Tags: {auditable_tags}\n\n"
        f"Active Master Tag Taxonomy:\n{master_list_text}\n\n"
        "Note Content Sample (first 1000 chars):\n"
        f"'''\n{body[:1000]}\n'''\n\n"
        "Evaluate tag suitability for this document. Select 2-5 highly relevant tags from the Master Taxonomy or suggest new nested tags if appropriate."
    )

    response_text = query_ollama(user_prompt, system_prompt)
    
    # Try parsing JSON output
    tags_to_keep = auditable_tags
    tags_to_add = []
    tags_to_remove = []
    new_masters = []

    try:
        # Match JSON block in response
        json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group(0))
            tags_to_keep = [normalize_tag_format(t) for t in parsed.get("tags_to_keep", []) if t]
            tags_to_add = [normalize_tag_format(t) for t in parsed.get("tags_to_add", []) if t]
            tags_to_remove = [normalize_tag_format(t) for t in parsed.get("tags_to_remove", []) if t]
            new_masters = parsed.get("new_master_tags", [])
    except Exception as e:
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

    final_tags_list = sorted(list(final_tags_set))
    tags_str = ", ".join(final_tags_list)

    # Save changes if tags modified
    modified = (set(final_tags_list) != set(current_tags))
    if modified:
        new_content = update_frontmatter_tags(content, final_tags_list)
        try:
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            # Instantly re-index modified note in Chroma DB vector store
            try:
                from Evelyn.tools import chroma_rag
                chroma_rag.ingest_markdown_file(
                    file_path=abs_path,
                    content=new_content,
                    collection_name="obsidian_vault",
                    extra_metadata={"tags": tags_str}
                )
            except Exception as ve:
                print(f"[TAG LIBRARIAN] Single-file vector update skipped: {ve}")
        except Exception as e:
            vault_db.update_document_tag_audit(doc_path)
            return {"status": "error", "path": doc_path, "message": f"Write error: {e}"}



    # Record new master tags in database
    for m in new_masters:
        ntag = normalize_tag_format(m.get("tag", ""))
        if ntag:
            category = m.get("category", ntag.split("/")[0] if "/" in ntag else "general")
            desc = m.get("description", f"Tags related to {ntag}")
            vault_db.upsert_master_tag(ntag, category=category, description=desc, usage_count=1)

    # Update database audit timestamp & tags
    vault_db.update_document_tag_audit(doc_path, tags=tags_str)

    return {
        "status": "success",
        "path": doc_path,
        "modified": modified,
        "previous_tags": current_tags,
        "final_tags": final_tags_list,
        "protected_tags": protected_tags
    }


def seed_master_taxonomy_from_vault() -> int:
    """Seed initial master tag taxonomy from all existing vault notes in database.

    Returns:
        int: Number of unique tags seeded into master_tag_taxonomy.
    """
    docs = vault_db.get_all_documents()
    tag_counts: Dict[str, int] = {}
    
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

    return len(tag_counts)


def maintain_master_taxonomy() -> Dict[str, Any]:
    """Perform periodic maintenance on the master tag taxonomy table.

    Updates tag usage counts across the vault and removes zero-usage tags.

    Returns:
        Dict[str, Any]: Summary of maintenance pass.
    """
    docs = vault_db.get_all_documents()
    current_counts: Dict[str, int] = {}
    
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
            removed_count += 1
        elif count != m["usage_count"]:
            vault_db.upsert_master_tag(t, category=m["category"], description=m["description"], usage_count=count)
            updated_count += 1

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
    
    args = parser.parse_args()
    
    if args.seed_taxonomy:
        count = seed_master_taxonomy_from_vault()
        print(f"[TAG LIBRARIAN] Seeded {count} tags into master_tag_taxonomy.")
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
