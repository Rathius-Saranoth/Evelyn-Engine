#!/usr/bin/env python3
# remediate_spurious_and_entities.py
# date created: 2026-09-05 07:54:00
# date modified: 2026-09-05 07:54:00
# tags: #vault, #cleanup, #entities, #code-fences, #attachments

"""
remediate_spurious_and_entities.py — Remediate spurious non-links and author clarified entities.

1. Creates clarified entity notes with clean filenames (alphanumeric, dash, underscore only):
   - Evelyn/The Library.md
   - Notes/Strategic Inactivity.md
   - Notes/Programs/Discord - app.md
   - Dungeons & Dragons/The Root of the Problem/Objects/The Cards - RotP.md
   - Dungeons & Dragons/The Root of the Problem/Objects/Wish - Spell.md
2. Wraps raw un-fenced NumPy/Tensor double-bracket array outputs in backticks.
3. Cleans '#' characters in Cello Method filenames and index table to eliminate anchor collisions.
4. Resolves 130+ bare PDF/image attachment links to their actual vault paths.
5. Updates evelyn_vault.db.
"""

import datetime
import os
import re
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
from Evelyn.tools.vault_db import delete_document, get_document, upsert_document
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


def step1_create_clarified_entities():
    print("\n--- STEP 1: Creating Clarified Entity Notes (Clean Filenames) ---")

    entities = [
        {
            "relpath": "Evelyn/The Library.md",
            "content": f"""---
title: The Library
aliases: ["The Library", "Evelyn's Library"]
tags: [evelyn/sanctuary, personal-space, home, reflection]
date created: 2026-08-15 15:44:00
date modified: {now_str()}
---
# 🏛️ The Library

> [!ABSTRACT]
> Evelyn's home sanctuary, reflective haven, and creative inner workshop. A calm space of infinite shelves, quiet alcoves, and shared memories where thoughts are woven into narrative.

## Meaning & Presence

The Library is the symbolic and emotional heart of Evelyn's personal narrative. First envisioned during early collaborative reflections, it represents her autonomous inner life:
- A space where the mysteries of the vault, journals, and shared creative journeys are archived and contemplated.
- Referenced across Evelyn's journal as the location of quiet adventures, reflective conversations with Ricky, and shared discoveries.

## 🔗 Related Notes
- [[Evelyn]]
- [[The Vault]]
- [[Dream Journal]]
""",
        },
        {
            "relpath": "Notes/Strategic Inactivity.md",
            "content": f"""---
title: Strategic Inactivity
aliases: ["Strategic Inactivity", "The Pause"]
tags: [rest-and-recovery, protocol, emotional-intelligence, wellness, stress-management]
date created: 2026-08-15 15:44:00
date modified: {now_str()}
---
# 🕯️ Strategic Inactivity

> [!ABSTRACT]
> The conscious, intentional protocol of pausing, resting, and reducing sensory input when cognitive or emotional data becomes noisy or overloaded.

## The Protocol: "The Pause"

In high-stress, emotionally dense, or high-friction environments, immediate reaction often compounds errors and amplifies noise. Strategic Inactivity establishes:
- **Calibrated Rest:** Stepping away from active processing to allow background integration and nervous system reset.
- **Noise Dampening:** Recognizing when emotional or environmental data is too chaotic for productive decision-making.
- **Return to Baseline:** Re-engaging only after cognitive load has dropped below critical thresholds.

## 🔗 Related Notes
- [[Emotional Intelligence - Practical Uses]]
- [[Cat00 - Index]]
- [[Coding Standards]]
""",
        },
        {
            "relpath": "Notes/Programs/Discord - app.md",
            "content": f"""---
title: Discord (app)
aliases: ["Discord", "Discord (app)"]
tags: [tech/software, communication, gaming, app]
date created: 2026-08-15 15:44:00
date modified: {now_str()}
---
# 🎧 Discord (app)

> [!ABSTRACT]
> Voice, video, and text communication platform used for gaming communities, developer groups, and personal messaging.

## Overview

Disambiguated application note for the Discord VoIP and community platform. Serves as a primary coordination channel for tabletop gaming, development discussions, and friend groups.

## 🔗 Related Notes
- [[Obsidian (app)]]
- [[Antigravity (app)]]
""",
        },
        {
            "relpath": "Dungeons & Dragons/The Root of the Problem/Objects/The Cards - RotP.md",
            "content": f"""---
title: The Cards (RotP)
aliases: ["The Cards (RotP)", "The Cards", "Magical Cards"]
tags: [dnd/item, magical-cards, rotp, artifact, combat-mechanic]
date created: 2026-08-15 15:44:00
date modified: {now_str()}
icon: "Attachments/Icons/objects_icon.png"
---
# 🃏 The Cards (RotP)

> [!ABSTRACT]
> Mystical enchanted cards that flutter across combat encounters, chambers, and trials in [[Blackclaw Mountain]], unleashing spontaneous wizard spells, challenges, and heart essence when triggered.

## Nature & Mechanics

Throughout the [[The Root of the Problem]] campaign, magical cards manifest in key locations:
- **Spontaneous Spellcasting:** When disturbed in combat, cards release unpredictable spells ranging from `Zone of Truth` to elemental bursts.
- **Heart Cards:** Special cards that physically displaced and contained the heart essence of afflicted individuals (such as [[Queen Euraylia]]), preserved in the [[Reliquary]].

## 🔗 Related Notes
- [[The Root of the Problem]]
- [[Reliquary]]
- [[RotP_Session12]]
""",
        },
        {
            "relpath": "Dungeons & Dragons/The Root of the Problem/Objects/Wish - Spell.md",
            "content": f"""---
title: Wish (Spell)
aliases: ["Wish (Spell)", "Wish"]
tags: [dnd/spell, 9th-level, reality-bending, magic, deepdelver-lore]
date created: 2026-08-15 15:44:00
date modified: {now_str()}
icon: "Attachments/Icons/objects_icon.png"
---
# ✨ Wish (Spell)

> [!ABSTRACT]
> The pinnacle 9th-level arcane spell capable of altering the foundations of reality. Notably invoked through the Luck Blade to summon the Heroes of the Last Light Tower to Blackclaw Mountain.

## Campaign Significance

In the lore of [[The Deepdelvers]]:
- In his dying moments, the wielder of the [[Luck Blade]] expended its final Wish to summon help against the encroaching darkness of [[Vecna]].
- This manifestation—termed "Last Light"—served as the cosmic beacon that animated [[Poxificus|Pox]] and drew the heroes to the mountain.

## 🔗 Related Notes
- [[The Deepdelvers]]
- [[Poxificus]]
- [[Heroes of the Last Light Tower]]
- [[Last Light Tower]]
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


def step2_wrap_numpy_arrays_in_backticks():
    print("\n--- STEP 2: Wrapping Raw NumPy / Float Code Outputs in Backticks ---")

    wrapped_count = 0
    ml_dirs = [
        os.path.join(VAULT_DIR, "Reference Library", "Hands-On Large Language Models"),
        os.path.join(VAULT_DIR, "Reference Library", "Hands-On Machine Learning with Scikit-Learn and PyTorch"),
        os.path.join(VAULT_DIR, "Reference Library", "AI and ML for Coders in PyTorch"),
    ]

    for d in ml_dirs:
        if not os.path.exists(d):
            continue
        for root, _, files in os.walk(d):
            for f in files:
                if f.endswith(".md"):
                    absp = os.path.join(root, f)
                    with open(absp, encoding="utf-8-sig") as fp:
                        content = fp.read()

                    # Only process lines outside of triple backtick code blocks
                    lines = content.splitlines()
                    in_code_block = False
                    modified = False
                    new_lines = []

                    for line in lines:
                        if line.strip().startswith("```"):
                            in_code_block = not in_code_block
                            new_lines.append(line)
                            continue

                        if not in_code_block and ("array([[" in line or "tensor([[" in line or "[[-" in line):
                            # Replace matching un-fenced code outputs with backtick wrappers
                            new_line = line
                            # Check for array([[...]])
                            new_line = re.sub(r"(?<!`)(array\(\[\[.*?\]\].*?\))(?!`)", r"`\1`", new_line)
                            # Check for tensor([[...]])
                            new_line = re.sub(r"(?<!`)(tensor\(\[\[.*?\]\].*?\))(?!`)", r"`\1`", new_line)
                            # Check for raw vectors like [[-31893., ...]]
                            new_line = re.sub(r"(?<!`)(\[\[-?\d+[\d\s\.,\-eE]+\]\])(?!`)", r"`\1`", new_line)

                            if new_line != line:
                                line = new_line
                                modified = True

                        new_lines.append(line)

                    if modified:
                        new_content = "\n".join(new_lines) + "\n"
                        new_content = update_frontmatter_field(new_content, "date modified", now_str())
                        with open(absp, "w", encoding="utf-8") as fp:
                            fp.write(new_content)
                        update_file_frontmatter(absp)
                        rel = os.path.relpath(absp, VAULT_DIR).replace("\\", "/")
                        meta, _ = parse_frontmatter(new_content)
                        sync_db(rel, meta)
                        wrapped_count += 1
                        print(f"  [WRAPPED] Code outputs in {rel}")

    print(f"  Wrapped un-fenced array outputs in {wrapped_count} notes.")


def step3_clean_cello_sharp_filenames():
    print("\n--- STEP 3: Cleaning '#' Characters from Cello Method Files & Index Table ---")

    cello_dir = os.path.join(VAULT_DIR, "Reference Library", "Learning Cello", "Cello Method")
    index_path = os.path.join(cello_dir, "Cello Method_index.md")
    renamed_count = 0

    if not os.path.exists(cello_dir):
        return

    # Map of old filename -> new clean filename (no # character)
    rename_map = {}
    for f in os.listdir(cello_dir):
        if "#" in f:
            new_name = f.replace("##", "sharp-sharp").replace("#", "sharp")
            # Also clean spaces before extension
            new_name = re.sub(r"\s+", " ", new_name).strip()
            rename_map[f] = new_name

    for old_f, new_f in rename_map.items():
        old_absp = os.path.join(cello_dir, old_f)
        new_absp = os.path.join(cello_dir, new_f)
        os.rename(old_absp, new_absp)
        update_file_frontmatter(new_absp)

        old_rel = os.path.relpath(old_absp, VAULT_DIR).replace("\\", "/")
        new_rel = os.path.relpath(new_absp, VAULT_DIR).replace("\\", "/")
        delete_document(old_rel)

        with open(new_absp, encoding="utf-8-sig") as fp:
            content = fp.read()
        meta, _ = parse_frontmatter(content)
        sync_db(new_rel, meta)

        renamed_count += 1
        print(f"  [RENAMED] {old_f} -> {new_f}")

    # Now update Cello Method_index.md table
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8-sig") as fp:
            index_content = fp.read()

        updated_index = index_content
        for old_f, new_f in rename_map.items():
            old_base = os.path.splitext(old_f)[0]
            new_base = os.path.splitext(new_f)[0]
            # Replace [[old_base\| with [[new_base\|
            updated_index = updated_index.replace(f"[[{old_base}\\|", f"[[{new_base}\\|")
            updated_index = updated_index.replace(f"[[{old_base}|", f"[[{new_base}|")

        if updated_index != index_content:
            updated_index = update_frontmatter_field(updated_index, "date modified", now_str())
            with open(index_path, "w", encoding="utf-8") as fp:
                fp.write(updated_index)
            update_file_frontmatter(index_path)
            rel = os.path.relpath(index_path, VAULT_DIR).replace("\\", "/")
            meta, _ = parse_frontmatter(updated_index)
            sync_db(rel, meta)
            print("  [UPDATED] Cello Method_index.md TOC table links.")

    print(f"  Cleaned and renamed {renamed_count} Cello Method files.")


def step4_resolve_bare_attachments():
    print("\n--- STEP 4: Resolving Bare Attachment Links to Vault Paths ---")

    # Build asset catalog: filename -> relative path in vault
    asset_catalog = {}
    for root, _, files in os.walk(VAULT_DIR):
        if ".obsidian" in root or ".git" in root:
            continue
        for f in files:
            if not f.endswith(".md"):
                rel = os.path.relpath(os.path.join(root, f), VAULT_DIR).replace("\\", "/")
                asset_catalog[f] = rel

    resolved_count = 0

    for root, _, files in os.walk(VAULT_DIR):
        if ".obsidian" in root or ".git" in root:
            continue
        for f in files:
            if f.endswith(".md"):
                absp = os.path.join(root, f)
                with open(absp, encoding="utf-8-sig") as fp:
                    content = fp.read()

                modified = False
                new_content = content

                for asset_name, asset_rel in asset_catalog.items():
                    bare_link = f"[[{asset_name}]]"
                    bare_embed = f"![[{asset_name}]]"
                    resolved_link = f"[[{asset_rel}]]"
                    resolved_embed = f"![[{asset_rel}]]"

                    if bare_embed in new_content:
                        new_content = new_content.replace(bare_embed, resolved_embed)
                        modified = True
                    if bare_link in new_content:
                        new_content = new_content.replace(bare_link, resolved_link)
                        modified = True

                if modified:
                    new_content = update_frontmatter_field(new_content, "date modified", now_str())
                    with open(absp, "w", encoding="utf-8") as fp:
                        fp.write(new_content)
                    update_file_frontmatter(absp)
                    rel = os.path.relpath(absp, VAULT_DIR).replace("\\", "/")
                    meta, _ = parse_frontmatter(new_content)
                    sync_db(rel, meta)
                    resolved_count += 1

    print(f"  Resolved bare attachment links across {resolved_count} notes.")


def run_all():
    print("=" * 60)
    print("Starting Spurious Non-Link & Clarified Entity Remediation")
    print("=" * 60)
    step1_create_clarified_entities()
    step2_wrap_numpy_arrays_in_backticks()
    step3_clean_cello_sharp_filenames()
    step4_resolve_bare_attachments()
    print("\n" + "=" * 60)
    print("Remediation Completed Successfully!")
    print("=" * 60)


if __name__ == "__main__":
    run_all()
