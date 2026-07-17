#!/usr/bin/env python
# migrate_research_headers.py
# date created: 2026-07-03 11:00:00
# date modified: 2026-07-03 11:15:37
# tags: #migration, #metadata, #research, #obsidian, #utility

"""migrate_research_headers.py — Batch updates previous research files with aliases and topic tags.

Analyzes the report titles and bodies via Ollama to generate descriptive short titles
and tags, updating the YAML frontmatter.

Usage:
  python scripts/migrate_research_headers.py --dir "C:\Temp"
  python scripts/migrate_research_headers.py --dir "G:\My Drive\Obsidian_Vault\Evelyn\Research"
"""

import os
import re
import sys
import argparse
import asyncio
import datetime
import importlib
import json
from typing import Tuple, Dict, Any, List

import httpx
import yaml

# Ensure project directories are in system path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import evelyn_config as cfg

def parse_json_response(raw_response: str) -> Any:
    """Parse JSON from LLM response, stripping markdown fences if present."""
    cleaned = raw_response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("\n", 1)[0]
    cleaned = cleaned.strip()
    return json.loads(cleaned)

def parse_existing_file(filepath: str) -> Tuple[Dict[str, Any], str]:
    """Parse frontmatter and return metadata dict and body content."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Match standard YAML frontmatter
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if fm_match:
        frontmatter_text = fm_match.group(1)
        body = content[fm_match.end():]
        try:
            metadata = yaml.safe_load(frontmatter_text)
            if isinstance(metadata, dict):
                return metadata, body
        except Exception:
            pass
    return {}, content

async def query_llm_for_metadata(query: str, scope: str, report_body: str) -> Tuple[str, List[str]]:
    """Query the local Ollama LLM to extract short_title and topic_tags."""
    # Determine tags count instruction
    if scope == "quick":
        tag_range = "1 to 3"
    elif scope == "deep":
        tag_range = "6 to 9"
    else:
        tag_range = "3 to 6"

    # Take a snippet of the report body for context
    preview_limit = 2000
    report_preview = report_body[:preview_limit] + "..." if len(report_body) > preview_limit else report_body

    prompt = (
        "You are a research metadata helper. Analyze the following research query and the report content. "
        "Generate a suitable short title and specific topic tags.\n\n"
        f"Original Query: \"{query}\"\n"
        f"Research Scope: {scope}\n"
        f"Report Content:\n{report_preview}\n\n"
        "Requirements:\n"
        "1. The short_title should be a highly descriptive, concise alternative title/alias (2-5 words max, e.g. '3D Printer Bed Leveling').\n"
        f"2. Generate topic tags based on the scope: '{scope}'. For scope '{scope}', generate EXACTLY {tag_range} specific, lowercase, hyphenated topic tags (no spaces, e.g. '3d-printing', 'calibration'). Do NOT include metadata status tags like 'research/done'.\n"
        "3. Output ONLY a valid JSON object matching the format below. Do not output markdown code fences, do not output any conversational preamble, thinking text, or explanations. Respond with ONLY the JSON object:\n"
        "{\n"
        "  \"short_title\": \"Concise Short Title\",\n"
        "  \"topic_tags\": [\"tag1\", \"tag2\"]\n"
        "}"
    )

    override = getattr(cfg, "RESEARCH_MODEL_OVERRIDE", "default")
    model = cfg.MODEL_NAME if override == "default" else override

    options = {
        "num_ctx": cfg.NUM_CTX,
        "num_predict": 1024,
        "temperature": 0.3,
        "min_p": cfg.MIN_P,
        "top_k": cfg.TOP_K,
        "top_p": cfg.TOP_P,
    }

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": options,
        "think": False
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{cfg.OLLAMA_URL}/api/chat", json=payload)
            resp.raise_for_status()
            res_json = resp.json()
            raw_response = res_json.get("message", {}).get("content", "")
            
            # Parse response
            data = parse_json_response(raw_response)
            short_title = data.get("short_title", "").strip()
            topic_tags = data.get("topic_tags", [])
            if not isinstance(topic_tags, list):
                topic_tags = [t.strip() for t in str(topic_tags).split(",") if t.strip()]
            return short_title, topic_tags
    except Exception as e:
        print(f"  [LLM ERROR] Failed to query LLM: {e}")
        return "", []

async def migrate_file(filepath: str) -> bool:
    """Migrate frontmatter in a single file, replacing tags/aliases."""
    print(f"Processing: {os.path.basename(filepath)}")
    metadata, body = parse_existing_file(filepath)
    if not metadata:
        print(f"  [SKIPPED] No YAML frontmatter found in {os.path.basename(filepath)}")
        return False

    query = metadata.get("title", "")
    # Recover original human-readable title from markdown body heading if title is a filename
    if not query or query.endswith(".md"):
        heading_match = re.search(r"^#\s+(?:Research Report:\s*)?(.*?)$", body, re.MULTILINE)
        if heading_match:
            query = heading_match.group(1).strip()
        else:
            query = os.path.basename(filepath).replace(".md", "").replace("-", " ").title()

    scope = metadata.get("scope", "standard")
    confidence = 70
    conf_raw = metadata.get("confidence", "70%")
    try:
        confidence = int(str(conf_raw).replace("%", "").strip())
    except Exception:
        pass

    print(f"  Query: \"{query}\" (Scope: {scope}, Confidence: {confidence}%)")
    
    # Query LLM to get tags & short title
    short_title, topic_tags = await query_llm_for_metadata(query, scope, body)

    if not short_title:
        # Fallback short title
        words = query.split()
        short_title = " ".join(words[:5]) + "..." if len(words) > 5 else query

    # Clean short title
    clean_short_title = short_title.replace('"', '\\"')

    # Build tags
    tags_list = ["research/done"]
    if confidence >= 80:
        tags_list.append("research/high-quality")
    else:
        tags_list.append("research/partial")

    for tag in topic_tags:
        cleaned_tag = re.sub(r"[^\w\s-]", "", tag.lower())
        cleaned_tag = re.sub(r"[-\s]+", "-", cleaned_tag).strip("-_")
        if cleaned_tag and cleaned_tag not in tags_list:
            tags_list.append(cleaned_tag)

    tags_str = ", ".join(tags_list)

    # Rebuild frontmatter, keeping original fields but rewriting aliases and tags
    metadata["aliases"] = [clean_short_title]
    metadata["tags"] = tags_list

    # Ensure format date created
    date_created = metadata.get("date created")
    if not date_created:
        date_created = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    triggered_by_val = metadata.get('triggered_by', 'user')
    if isinstance(triggered_by_val, str) and triggered_by_val.lower() == "evelyn":
        triggered_by_val = "Evelyn"

    frontmatter = (
        "---\n"
        f"title: \"{query}\"\n"
        f"aliases: [\"{clean_short_title}\"]\n"
        f"date created: {date_created}\n"
        f"research_task_id: {metadata.get('research_task_id', 'legacy_migration')}\n"
        f"scope: {scope}\n"
        f"source_count: {metadata.get('source_count', 0)}\n"
        f"confidence: {confidence}%\n"
        f"triggered_by: {triggered_by_val}\n"
        f"tags: [{tags_str}]\n"
        "---\n\n"
    )

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(frontmatter + body)

    print(f"  [SUCCESS] Wrote updated frontmatter (aliases: {metadata['aliases']}, tags count: {len(tags_list)})")
        
    return True

async def main():
    parser = argparse.ArgumentParser(description="Migrate YAML frontmatter of research reports.")
    parser.add_argument(
        "--dir", 
        default="C:\\Temp", 
        help="Target directory containing research markdown reports. Defaults to C:\\Temp."
    )
    args = parser.parse_args()

    target_dir = args.dir
    if not os.path.exists(target_dir):
        print(f"Target directory {target_dir} does not exist.")
        sys.exit(1)

    print(f"Starting metadata migration on directory: {target_dir}")
    md_files = [os.path.join(target_dir, f) for f in os.listdir(target_dir) if f.endswith(".md")]
    
    if not md_files:
        print("No markdown files found in the directory.")
        sys.exit(0)

    print(f"Found {len(md_files)} markdown files to process.")
    
    success_count = 0
    for idx, filepath in enumerate(md_files, 1):
        print(f"\n[{idx}/{len(md_files)}]")
        success = await migrate_file(filepath)
        if success:
            success_count += 1
        # Brief pause between tasks to allow cooling down
        await asyncio.sleep(0.5)

    print(f"\nMigration complete: {success_count}/{len(md_files)} files successfully updated.")

if __name__ == "__main__":
    asyncio.run(main())
