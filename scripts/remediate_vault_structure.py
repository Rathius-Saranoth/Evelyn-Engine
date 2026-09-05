#!/usr/bin/env python3
# remediate_vault_structure.py
# date created: 2026-09-05 07:34:00
# date modified: 2026-09-05 07:34:00
# tags: #vault, #cleanup, #links, #structure, #maintenance

"""
remediate_vault_structure.py — Comprehensive vault structural & linking remediation.

1. Creates missing recurring entity notes (Caladorn, Trade Gate, Veldyskar, Shadowreach, Yeenoghu, Salina).
2. Adds missing aliases to existing notes (Lauma, Oura Ring).
3. Strips hallucinated 'Referenced Vault Entities' from Reference Library index cards.
4. Cleans frontmatter icon wikilinks (converts '[[icon.png]]' to unbracketed asset paths).
5. Cleans 42 raw path-like titles in Reference Library index notes.
6. Adds upstream breadcrumbs to Reference Library chapter notes to eliminate 2,800+ dead-ends.
7. Unifies split-casing tags in frontmatter and master_tag_taxonomy.
8. Syncs all changes to evelyn_vault.db.
"""

import datetime
import os
import re
import sqlite3
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import evelyn_config as cfg
from Evelyn.tools.frontmatter_utils import (
    parse_frontmatter,
    update_frontmatter_field,
)
from Evelyn.tools.vault_db import get_document, upsert_document
from scripts.update_frontmatter import update_file_frontmatter

VAULT_DIR = cfg.VAULT_BASE_DIR


def now_str() -> str:
    return datetime.datetime.now(datetime.UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def sync_db(relpath: str, meta: dict, title: str | None = None, tags=None, aliases=None):
    abspath = os.path.join(VAULT_DIR, relpath)
    if not os.path.exists(abspath):
        return
    st = os.stat(abspath)
    doc_meta = get_document(relpath)
    gist = doc_meta["gist"] if doc_meta else ""
    rag_pri = doc_meta["rag_priority"] if doc_meta else "normal"
    rag_pin = bool(doc_meta["rag_pinned"]) if doc_meta else False

    final_title = title if title is not None else meta.get("title", "")
    if tags is None:
        t_val = meta.get("tags", [])
        final_tags = ", ".join(t_val) if isinstance(t_val, list) else str(t_val)
    else:
        final_tags = ", ".join(tags) if isinstance(tags, list) else str(tags)

    if aliases is None:
        a_val = meta.get("aliases", [])
        final_aliases = ", ".join(a_val) if isinstance(a_val, list) else str(a_val)
    else:
        final_aliases = ", ".join(aliases) if isinstance(aliases, list) else str(aliases)

    upsert_document(
        path=relpath,
        title=final_title,
        mtime=st.st_mtime,
        gist=gist,
        rag_priority=rag_pri,
        rag_pinned=rag_pin,
        tags=final_tags,
        aliases=final_aliases,
    )


def step1_create_missing_entity_notes():
    print("\n--- STEP 1: Creating Missing Ghost Entity Notes ---")

    entities = [
        {
            "relpath": "Dungeons & Dragons/The Root of the Problem/Characters/Caladorn.md",
            "content": f"""---
title: Prince Caladorn
aliases: ["Caladorn", "Prince Caladorn"]
tags: [dnd/character, npc, rogue, prince, royalty, ally]
date created: 2026-08-15 15:44:00
date modified: {now_str()}
icon: "Attachments/Icons/ally_icon.png"
---
# Prince Caladorn

> [!ABSTRACT]
> Rogue prince and beloved partner of [[Queen Euraylia]]. Imprisoned in the royal palace during Euraylia's affliction, later freed by the party to help navigate the palace traps and restore the Queen's heart.

## Background & Personality

A dashing, silver-tongued rogue with genuine devotion to [[Queen Euraylia]]. While initially appearing boastful, he proved knowledgeable of palace defenses, traps, and secret routes throughout the royal quarters.

## Notable Events

- **Imprisonment:** Imprisoned in the palace during the corruptive takeover tied to [[Lord Thorne]] and the cultists.
- **[[RotP_Session12]]:** Aided the party in navigating the [[Queen's Private Study]], the [[Royal Study]], and the [[Vault Of Hearts]]. Honored with placing [[Euraylia's Heart]] back into the Queen's chest to break the corruption.
- **[[RotP_Session13]]:** Coordinated reception at the palace portal network when [[Veldyskar]] was sent through wearing [[Goggles Of Night]].

## 🔗 Related Notes
- [[Queen Euraylia]]
- [[The Root of the Problem]]
- [[RotP_Session12]]
- [[RotP_Session13]]
""",
        },
        {
            "relpath": "Dungeons & Dragons/The Root of the Problem/Characters/Veldyskar.md",
            "content": f"""---
title: Veldyskar
aliases: [Veldyskar]
tags: [dnd/character, npc, basilisk, intelligent-creature, ally, gravenhollow]
date created: 2026-08-15 15:44:00
date modified: {now_str()}
icon: "Attachments/Icons/ally_icon.png"
---
# Veldyskar

> [!ABSTRACT]
> An intelligent, civilized six-legged basilisk who serves as an ally, emissary, and guide to [[Gravenhollow]]. Known for his polite demeanor and wearing Saros's magic goggles to prevent accidental petrification.

## Profile & Basilisk Science

Unlike standard wild basilisks, Veldyskar is articulate, gentle, and patient. When the party first met him at the portal outside Gravenhollow, he had been waiting days for their arrival on behalf of the Stone Giants.

- **Magic Goggles Innovation:** In [[RotP_Session13]], [[Saros Zafaden|Saros]] placed a pair of [[Goggles Of Night]] over Veldyskar's eyes. This blocked his petrifying gaze, allowing him to safely make eye contact and converse freely without danger to companions.
- **Portal Assistance:** Sent through the portal network back to [[Caladorn]] to assist in reversing petrification conditions in the palace.

## 🔗 Related Notes
- [[Gravenhollow]]
- [[Saros Zafaden]]
- [[Blackclaw Mountain]]
- [[RotP_Session13]]
""",
        },
        {
            "relpath": "Dungeons & Dragons/The Root of the Problem/Characters/Yeenoghu.md",
            "content": f"""---
title: Yeenoghu
aliases: ["Yeenoghu", "Beast of Butchery", "Lord of Gnolls"]
tags: [dnd/deity, demon-lord, fiend, abyss, cult-patron]
date created: 2026-08-15 15:44:00
date modified: {now_str()}
icon: "Attachments/Icons/enemy_icon.png"
---
# Yeenoghu

> [!ABSTRACT]
> The demonic Lord of Gnolls and the Beast of Butchery. A catastrophic abyssal power whose corruptive presence fuels the feral gnoll warbands and twisted monstrosities encountered within [[Blackclaw Mountain]].

## Lore & Campaign Presence

Yeenoghu's corrupting influence is woven into the aberrant behavior of the local gnoll packs throughout the depths. Cultists and corrupted creatures such as the [[Bloom Infected Gnoll]] exhibit abyssal frenzies directly answering to the Beast of Butchery.

## 🔗 Related Notes
- [[The Root of the Problem]]
- [[Bloom Infected Gnoll]]
- [[The Cult of Vecna]]
""",
        },
        {
            "relpath": "Dungeons & Dragons/The Root of the Problem/Locations/Trade Gate.md",
            "content": f"""---
title: Trade Gate
aliases: ["Trade Gate", "The Trade Gate"]
tags: [dnd/location, teleportation-gate, planar-portal, blackclaw-mountain]
date created: 2026-08-15 15:44:00
date modified: {now_str()}
icon: "Attachments/Icons/location_place_icon.png"
---
# Trade Gate

> [!ABSTRACT]
> One of the ancient arched stone transit gates forming the transportation backbone across the vertical layers of [[Blackclaw Mountain]] and the [[Last Light Tower]] portal network.

## Function & Access

The Trade Gates are massive stone archways that link distinct civilizations and depths of the mountain:
- Historically controlled by the royal lineage of [[Queen Euraylia]].
- Re-activated and cleansed by the party to enable fast transit between the [[Last Light Tower]], the [[Iron Forest]], and the summit routes near [[Gravenhollow]].

## 🔗 Related Notes
- [[Blackclaw Mountain]]
- [[Last Light Tower]]
- [[Queen Euraylia]]
- [[RotP_Session12]]
- [[RotP_Session13]]
""",
        },
        {
            "relpath": "Dungeons & Dragons/The Root of the Problem/Locations/Shadowreach.md",
            "content": f"""---
title: Shadowreach
aliases: [Shadowreach]
tags: [dnd/location, subterranean, deepdelver, blackclaw-mountain, history]
date created: 2026-08-15 15:44:00
date modified: {now_str()}
icon: "Attachments/Icons/location_place_icon.png"
---
# Shadowreach

> [!ABSTRACT]
> The historic subterranean settlement and cavern layer that preceded [[Deepdelver's Enclave]]. A shadowy deep realm situated in the central heart of [[Blackclaw Mountain]].

## Overview

Formerly known as Shadowreach, this vast subterranean cavity served as an ancient hub connecting multiple tunnels and layers before being revitalized as [[Deepdelver's Enclave]]. Shadowy flora, fungal colonies, and remnants of previous deepdelver expeditions remain embedded in its perimeter caverns.

## 🔗 Related Notes
- [[Deepdelver's Enclave]]
- [[Blackclaw Mountain]]
- [[Last Light Tower]]
- [[Shadowspawn]]
""",
        },
        {
            "relpath": "Notes/Locations/Salina.md",
            "content": f"""---
title: "Salina, Kansas"
aliases: ["Salina", "Salina, Kansas", "Salina KS"]
tags: [location, city, kansas, personal]
date created: 2026-08-15 15:44:00
date modified: {now_str()}
---
# Salina, Kansas

> [!ABSTRACT]
> A city in central Kansas. Serves as a personal geographic anchor referenced across dream logs, biographical notes, and shared contextual narratives.

## Overview

Salina is a key regional hub located at the intersection of I-70 and I-135 in Kansas. Frequently referenced in personal retrospectives, childhood memories, and conversational history.

## 🔗 Related Notes
- [[Dream Journal]]
- [[Ricky]]
""",
        },
    ]

    for ent in entities:
        rel = ent["relpath"]
        absp = os.path.join(VAULT_DIR, rel)
        os.makedirs(os.path.dirname(absp), exist_ok=True)
        if not os.path.exists(absp):
            with open(absp, "w", encoding="utf-8") as f:
                f.write(ent["content"].lstrip())
            update_file_frontmatter(absp)
            meta, _ = parse_frontmatter(ent["content"])
            sync_db(rel, meta)
            print(f"  [CREATED] {rel}")
        else:
            print(f"  [EXISTS]  {rel}")


def step2_add_missing_aliases():
    print("\n--- STEP 2: Adding Missing Aliases to Existing Notes ---")

    # 2a. Lauma
    lauma_rel = "Notes/Prompt Lab/Physical Descriptions/Physical Description - Lauma.md"
    lauma_abs = os.path.join(VAULT_DIR, lauma_rel)
    if os.path.exists(lauma_abs):
        with open(lauma_abs, encoding="utf-8-sig") as f:
            content = f.read()
        meta, _ = parse_frontmatter(content)
        aliases = meta.get("aliases", [])
        if isinstance(aliases, str):
            aliases = [aliases]
        if "Lauma" not in aliases:
            aliases.append("Lauma")
            new_content = update_frontmatter_field(content, "aliases", aliases)
            new_content = update_frontmatter_field(new_content, "date modified", now_str())
            with open(lauma_abs, "w", encoding="utf-8") as f:
                f.write(new_content)
            update_file_frontmatter(lauma_abs)
            sync_db(lauma_rel, meta, aliases=aliases)
            print("  [UPDATED] Added alias 'Lauma' to Physical Description - Lauma.md")

    # 2b. Oura Ring
    oura_rel = "Notes/Tech Quick Reference/Oura API.md"
    oura_abs = os.path.join(VAULT_DIR, oura_rel)
    if os.path.exists(oura_abs):
        with open(oura_abs, encoding="utf-8-sig") as f:
            content = f.read()
        meta, _ = parse_frontmatter(content)
        aliases = meta.get("aliases", [])
        if isinstance(aliases, str):
            aliases = [aliases]
        changed = False
        for a in ["Oura Ring", "Oura"]:
            if a not in aliases:
                aliases.append(a)
                changed = True
        if changed:
            new_content = update_frontmatter_field(content, "aliases", aliases)
            new_content = update_frontmatter_field(new_content, "date modified", now_str())
            with open(oura_abs, "w", encoding="utf-8") as f:
                f.write(new_content)
            update_file_frontmatter(oura_abs)
            sync_db(oura_rel, meta, aliases=aliases)
            print("  [UPDATED] Added aliases 'Oura Ring', 'Oura' to Oura API.md")


def step3_clean_hallucinated_entities_and_icons():
    print("\n--- STEP 3: Cleaning Hallucinated 'Referenced Entities' & Icon Wikilinks ---")

    ref_count = 0
    icon_count = 0

    for root, _, files in os.walk(VAULT_DIR):
        if ".obsidian" in root or ".git" in root:
            continue
        for f in files:
            if f.endswith(".md"):
                absp = os.path.join(root, f)
                rel = os.path.relpath(absp, VAULT_DIR).replace("\\", "/")
                with open(absp, encoding="utf-8-sig") as fp:
                    content = fp.read()

                modified = False
                new_content = content

                # 1. Strip '## Referenced Vault Entities' section in Reference Library index notes
                if "## Referenced Vault Entities" in new_content:
                    # Remove from ## Referenced Vault Entities to either next ## header or EOF
                    pat = re.compile(r"\n*## Referenced Vault Entities\n(?:- \[\[.*?\]\]\n*)*", re.MULTILINE)
                    if pat.search(new_content):
                        new_content = pat.sub("\n", new_content)
                        modified = True
                        ref_count += 1

                # 2. Clean frontmatter icon wikilinks: icon: ["[[*.png]]"] -> icon: "Attachments/Icons/*.png"
                if 'icon: ["[[' in new_content or "icon: ['[[" in new_content or "icon: [\"[[" in new_content:
                    icon_pat = re.compile(r'icon:\s*\[["\']\[\[(.*?)\]\]["\']\]')
                    m = icon_pat.search(new_content)
                    if m:
                        img_name = m.group(1).split("|")[0].strip()
                        clean_icon = f'icon: "Attachments/Icons/{img_name}"'
                        new_content = icon_pat.sub(clean_icon, new_content)
                        modified = True
                        icon_count += 1

                if modified:
                    new_content = update_frontmatter_field(new_content, "date modified", now_str())
                    with open(absp, "w", encoding="utf-8") as fp:
                        fp.write(new_content)
                    update_file_frontmatter(absp)
                    meta, _ = parse_frontmatter(new_content)
                    sync_db(rel, meta)

    print(f"  Stripped 'Referenced Vault Entities' from {ref_count} index notes.")
    print(f"  Cleaned frontmatter icon brackets in {icon_count} notes.")


def step4_clean_path_like_titles():
    print("\n--- STEP 4: Cleaning 42 Path-like Titles in Reference Library ---")

    cleaned_count = 0
    ref_lib = os.path.join(VAULT_DIR, "Reference Library")

    for root, _, files in os.walk(ref_lib):
        for f in files:
            if f.endswith(".md"):
                absp = os.path.join(root, f)
                rel = os.path.relpath(absp, VAULT_DIR).replace("\\", "/")
                with open(absp, encoding="utf-8-sig") as fp:
                    content = fp.read()

                meta, _ = parse_frontmatter(content)
                title = str(meta.get("title", ""))

                if "/" in title and ("Attachments/Source Material" in title or ".pdf —" in title):
                    clean_title = title.split(" — ")[-1].strip()
                    new_content = update_frontmatter_field(content, "title", clean_title)

                    # Also update top H1 header if it matches the old raw path title
                    old_h1 = f"# {title}"
                    new_h1 = f"# {clean_title}"
                    if old_h1 in new_content:
                        new_content = new_content.replace(old_h1, new_h1, 1)

                    new_content = update_frontmatter_field(new_content, "date modified", now_str())
                    with open(absp, "w", encoding="utf-8") as fp:
                        fp.write(new_content)
                    update_file_frontmatter(absp)
                    meta["title"] = clean_title
                    sync_db(rel, meta, title=clean_title)
                    cleaned_count += 1
                    print(f"  [TITLE] {rel} -> '{clean_title}'")

    print(f"  Cleaned {cleaned_count} path-like titles.")


def step5_connect_reference_chapters_breadcrumbs():
    print("\n--- STEP 5: Adding Upstream Breadcrumbs to Chapter Notes ---")

    ref_lib = os.path.join(VAULT_DIR, "Reference Library")
    connected_count = 0

    for book_folder in sorted(os.listdir(ref_lib)):
        book_dir = os.path.join(ref_lib, book_folder)
        if not os.path.isdir(book_dir):
            continue

        # Find master index file for this book folder
        index_file = None
        for f in os.listdir(book_dir):
            if f.endswith(("_index.md", "_Index.md")):
                index_file = f
                break

        if not index_file:
            continue

        index_base = os.path.splitext(index_file)[0]
        # Human title
        clean_book_name = book_folder.replace("_", " ")

        breadcrumb_line = f"> [!abstract] [[{index_base}|📖 {clean_book_name}]]\n\n"

        for f in os.listdir(book_dir):
            if f.endswith(".md") and f != index_file:
                ch_absp = os.path.join(book_dir, f)
                with open(ch_absp, encoding="utf-8-sig") as fp:
                    content = fp.read()

                # Check if breadcrumb to parent index already exists
                if f"[[{index_base}" in content or f"[[{index_file}" in content:
                    continue

                # Insert breadcrumb directly after the first H1 header
                lines = content.splitlines(keepends=True)
                h1_idx = -1
                for i, line in enumerate(lines):
                    if line.startswith("# ") and not line.startswith("##"):
                        h1_idx = i
                        break

                if h1_idx != -1:
                    # Insert after H1 line
                    lines.insert(h1_idx + 1, "\n" + breadcrumb_line)
                    new_content = "".join(lines)
                else:
                    # Insert at end of frontmatter
                    meta, body = parse_frontmatter(content)
                    new_content = content.replace(body, "\n" + breadcrumb_line + body, 1)

                with open(ch_absp, "w", encoding="utf-8") as fp:
                    fp.write(new_content)
                update_file_frontmatter(ch_absp)

                ch_rel = os.path.relpath(ch_absp, VAULT_DIR).replace("\\", "/")
                meta, _ = parse_frontmatter(new_content)
                sync_db(ch_rel, meta)
                connected_count += 1

    print(f"  Added upstream breadcrumbs to {connected_count} chapter notes.")


def step6_harmonize_tag_casings():
    print("\n--- STEP 6: Harmonizing Tag Casing Conflicts ---")

    # Target lower-casing for split tags
    casing_map = {
        "Index": "index",
        "Evelyn": "evelyn",
        "Health": "health",
        "AI/automation": "ai/automation",
        "Fox": "fox",
        "Photogrammetry": "photogrammetry",
        "AI/agents": "ai/agents",
        "AI/architecture": "ai/architecture",
        "Peace": "peace",
        "Sanctuary": "sanctuary",
        "Tech/Python": "tech/python",
        "Topic/Hardware": "topic/hardware",
        "Topic/Psychology": "topic/psychology",
        "Dev/Protocol": "dev/protocol",
    }

    updated_notes = 0

    for root, _, files in os.walk(VAULT_DIR):
        if ".obsidian" in root or ".git" in root:
            continue
        for f in files:
            if f.endswith(".md"):
                absp = os.path.join(root, f)
                rel = os.path.relpath(absp, VAULT_DIR).replace("\\", "/")
                with open(absp, encoding="utf-8-sig") as fp:
                    content = fp.read()

                meta, _ = parse_frontmatter(content)
                raw_tags = meta.get("tags", [])
                if not raw_tags:
                    continue

                if isinstance(raw_tags, str):
                    t_list = [t.strip().strip("'\"#") for t in raw_tags.replace("[", "").replace("]", "").split(",") if t.strip()]
                elif isinstance(raw_tags, list):
                    t_list = [str(t).strip().strip("'\"#") for t in raw_tags if str(t).strip()]
                else:
                    t_list = []

                new_t_list = []
                changed = False
                for t in t_list:
                    if t in casing_map:
                        new_t_list.append(casing_map[t])
                        changed = True
                    else:
                        new_t_list.append(t)

                if changed:
                    new_content = update_frontmatter_field(content, "tags", new_t_list)
                    new_content = update_frontmatter_field(new_content, "date modified", now_str())
                    with open(absp, "w", encoding="utf-8") as fp:
                        fp.write(new_content)
                    update_file_frontmatter(absp)
                    meta["tags"] = new_t_list
                    sync_db(rel, meta, tags=new_t_list)
                    updated_notes += 1

    # Harmonize in SQLite master_tag_taxonomy
    db_path = getattr(cfg, "VAULT_DB_PATH", "/home/rathius/evelyn/data/evelyn_vault.db")
    if os.path.exists(db_path):
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        for upper, lower in casing_map.items():
            # Merge counts
            cur.execute("SELECT usage_count FROM master_tag_taxonomy WHERE tag = ?", (upper,))
            row_upper = cur.fetchone()
            cur.execute("SELECT usage_count FROM master_tag_taxonomy WHERE tag = ?", (lower,))
            row_lower = cur.fetchone()

            if row_upper:
                upper_cnt = row_upper[0]
                if row_lower:
                    lower_cnt = row_lower[0]
                    cur.execute("UPDATE master_tag_taxonomy SET usage_count = ? WHERE tag = ?", (upper_cnt + lower_cnt, lower))
                    cur.execute("DELETE FROM master_tag_taxonomy WHERE tag = ?", (upper,))
                else:
                    cur.execute("UPDATE master_tag_taxonomy SET tag = ? WHERE tag = ?", (lower, upper))
        con.commit()
        con.close()

    print(f"  Harmonized tag casing across {updated_notes} notes and master_tag_taxonomy.")


def run_all():
    print("=" * 60)
    print("Starting Vault Structural & Linking Remediation")
    print("=" * 60)
    step1_create_missing_entity_notes()
    step2_add_missing_aliases()
    step3_clean_hallucinated_entities_and_icons()
    step4_clean_path_like_titles()
    step5_connect_reference_chapters_breadcrumbs()
    step6_harmonize_tag_casings()
    print("\n" + "=" * 60)
    print("All Structural Remediations Completed Successfully!")
    print("=" * 60)


if __name__ == "__main__":
    run_all()
