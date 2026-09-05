#!/usr/bin/env python3
# cleanup_vault_aliases.py
# date created: 2026-09-05 07:16:00
# date modified: 2026-09-05 07:16:00
# tags: #vault, #cleanup, #aliases, #tags, #maintenance

"""
cleanup_vault_aliases.py — Safe, targeted audit and remediation of vault note aliases and tags.

Removes high-collision generic aliases (User Manual, Specification Sheet, Vs, Cc,
Syntax, Linting, the mountain, Enclave, Gnoll, Naga, Cookies, Recovery),
converts documentation types to tags, cleans up OCR typos and malformed parsing artifacts,
and eliminates redundant character possessive ('s) aliases.
"""

import datetime
import os
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


def to_list(val) -> list[str]:
    if not val:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        v = val.strip()
        if v.startswith("[") and v.endswith("]"):
            v = v[1:-1]
        return [x.strip().strip("'\"#") for x in v.split(",") if x.strip().strip("'\"#")]
    return [str(val).strip()]


def cleanup_file(relpath: str, modify_fn) -> bool:
    """Read file, apply modify_fn(meta), update frontmatter, write back, and update vault_db."""
    abspath = os.path.join(cfg.VAULT_BASE_DIR, relpath)
    if not os.path.exists(abspath):
        print(f"[SKIP] Not found: {relpath}")
        return False

    with open(abspath, encoding="utf-8-sig") as f:
        content = f.read()

    meta, _body = parse_frontmatter(content)
    changed, new_title, new_aliases, new_tags = modify_fn(meta)

    if not changed:
        print(f"[NO-CHANGE] {relpath}")
        return False

    updated_content = content
    if new_title is not None and new_title != meta.get("title"):
        updated_content = update_frontmatter_field(updated_content, "title", new_title)
    if new_aliases is not None:
        updated_content = update_frontmatter_field(updated_content, "aliases", new_aliases)
    if new_tags is not None:
        updated_content = update_frontmatter_field(updated_content, "tags", new_tags)

    now_str = datetime.datetime.now(datetime.UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    updated_content = update_frontmatter_field(updated_content, "date modified", now_str)

    with open(abspath, "w", encoding="utf-8") as f:
        f.write(updated_content)

    update_file_frontmatter(abspath)

    # Sync to vault_documents in evelyn_vault.db
    st = os.stat(abspath)
    doc_meta = get_document(relpath)
    gist = doc_meta["gist"] if doc_meta else ""
    rag_pri = doc_meta["rag_priority"] if doc_meta else "normal"
    rag_pin = bool(doc_meta["rag_pinned"]) if doc_meta else False

    final_title = new_title if new_title is not None else meta.get("title", "")
    final_tags = ", ".join(new_tags) if new_tags is not None else ", ".join(to_list(meta.get("tags", [])))
    final_aliases = ", ".join(new_aliases) if new_aliases is not None else ", ".join(to_list(meta.get("aliases", [])))

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

    print(f"[UPDATED] {relpath}")
    if new_title and new_title != meta.get("title"):
        print(f"  Title:   {meta.get('title')} -> {new_title}")
    if new_aliases is not None:
        print(f"  Aliases: {new_aliases}")
    if new_tags is not None:
        print(f"  Tags:    {new_tags}")
    return True


def run_cleanup():
    print("=" * 60)
    print("Starting Vault Alias & Tag Remediation")
    print("=" * 60)

    # 1. Reference Library / Owner's Manuals
    manuals_dir = os.path.join(cfg.VAULT_BASE_DIR, "Reference Library", "Owner's Manuals")
    if os.path.exists(manuals_dir):
        for root, _, files in os.walk(manuals_dir):
            for file in files:
                if file.endswith(".md"):
                    full_p = os.path.join(root, file)
                    relpath = os.path.relpath(full_p, cfg.VAULT_BASE_DIR).replace("\\", "/")

                    def clean_manual(meta):
                        aliases = to_list(meta.get("aliases", []))
                        tags = to_list(meta.get("tags", []))
                        orig_aliases = list(aliases)
                        orig_tags = list(tags)

                        has_manual = False
                        has_spec = False
                        new_aliases = []

                        for a in aliases:
                            if a in ("User Manual", "User Manutial"):
                                has_manual = True
                                continue
                            if a in ("Specification Sheet", "Spec Sheet"):
                                has_spec = True
                                continue
                            # Clean up OCR typos in combined aliases
                            cleaned_a = a
                            if "Samsuting" in cleaned_a:
                                cleaned_a = cleaned_a.replace("Samsuting", "Samsung")
                            if "User Manutial" in cleaned_a:
                                cleaned_a = cleaned_a.replace("User Manutial", "User Manual")
                            new_aliases.append(cleaned_a)

                        if has_manual and "user-manual" not in tags:
                            tags.append("user-manual")
                        if has_spec and "spec-sheet" not in tags:
                            tags.append("spec-sheet")

                        changed = (new_aliases != orig_aliases) or (tags != orig_tags)
                        return changed, None, new_aliases, tags

                    cleanup_file(relpath, clean_manual)

    # 2. Parsing Glitches
    # 2a. VS Code Intro to Python
    def clean_vscode(meta):
        new_title = "VS Code EDU - Intro to Python"
        aliases = [
            "VS Code EDU - Intro to Python",
            "VS Code Intro to Python",
            "Code Edu-Intro to Python",
            "VS Code EDU-Intro to Python_index",
        ]
        return True, new_title, aliases, None

    cleanup_file("Notes/Python/VS Code Intro to Python/VS Code Intro to Python_index.md", clean_vscode)

    # 2b. Color Code Basic Report
    def clean_cc(meta):
        new_title = "Color Code Basic Report"
        aliases = [
            "Color Code Basic Report",
            "Color Code Report Basic",
        ]
        return True, new_title, aliases, None

    cleanup_file("Ricky/Medical/Psychology/Color Code Basic Report/Color Code Basic Report_index.md", clean_cc)

    # 2c. Comprehensive Analysis - The Color Code
    def clean_cc_analysis(meta):
        aliases = to_list(meta.get("aliases", []))
        new_aliases = []
        for a in aliases:
            cleaned_a = a.replace(": :", ":").strip()
            if cleaned_a.startswith(":"):
                cleaned_a = cleaned_a.lstrip(":").strip()
            new_aliases.append(cleaned_a)
        return True, None, new_aliases, None

    cleanup_file("Ricky/Medical/Psychology/Comprehensive Analysis - The Color Code/Comprehensive Analysis - The Color Code_index.md", clean_cc_analysis)

    # 3. Coding Standards & Recovery
    # 3a. Coding Standards
    def clean_coding_standards(meta):
        aliases = to_list(meta.get("aliases", []))
        tags = to_list(meta.get("tags", []))
        new_aliases = [a for a in aliases if a not in ("Syntax", "Linting")]
        for t in ("syntax", "linting", "code-style"):
            if t not in tags:
                tags.append(t)
        return True, None, new_aliases, tags

    cleanup_file("Notes/Tech Quick Reference/Coding Standards.md", clean_coding_standards)

    # 3b. Recovery
    def clean_recovery(meta):
        return True, None, [], None

    cleanup_file("Notes/Recovery.md", clean_recovery)

    # 4. Project Tristram typo
    def clean_tristram(meta):
        aliases = to_list(meta.get("aliases", []))
        new_aliases = ["Sanctuary Remnants" if a == "Sancturary Remnants" else a for a in aliases]
        return True, None, new_aliases, None

    cleanup_file("Projects/Project Tristram.md", clean_tristram)

    # 5. D&D Campaign Specifics
    # 5a. Blackclaw Mountain
    def clean_mountain(meta):
        aliases = [a for a in to_list(meta.get("aliases", [])) if a.lower() != "the mountain"]
        return True, None, aliases, None

    cleanup_file("Dungeons & Dragons/The Root of the Problem/Locations/Blackclaw Mountain.md", clean_mountain)

    # 5b. The Evershady Bazaar
    def clean_bazaar(meta):
        aliases = [a for a in to_list(meta.get("aliases", [])) if a.lower() != "the bazaar"]
        return True, None, aliases, None

    cleanup_file("Dungeons & Dragons/The Root of the Problem/Locations/Deepdelver's Enclave/The Evershady Bazaar.md", clean_bazaar)

    # 5c. Deepdelver's Enclave
    def clean_enclave(meta):
        aliases = [a for a in to_list(meta.get("aliases", [])) if a.lower() not in ("enclave", "the enclave")]
        return True, None, aliases, None

    cleanup_file("Dungeons & Dragons/The Root of the Problem/Locations/Deepdelver's Enclave/Deepdelver's Enclave.md", clean_enclave)

    # 5d. Bloom Infected Gnoll
    def clean_gnoll(meta):
        aliases = [a for a in to_list(meta.get("aliases", [])) if a.lower() not in ("gnoll", "gnolls")]
        return True, None, aliases, None

    cleanup_file("Dungeons & Dragons/The Root of the Problem/Creatures/Bloom Infected Gnoll.md", clean_gnoll)

    # 5e. Bone Naga
    def clean_naga(meta):
        aliases = [a for a in to_list(meta.get("aliases", [])) if a.lower() != "naga"]
        return True, None, aliases, None

    cleanup_file("Dungeons & Dragons/The Root of the Problem/Creatures/Bone Naga.md", clean_naga)

    # 5f. Cookie
    def clean_cookie(meta):
        aliases = [
            a for a in to_list(meta.get("aliases", []))
            if a.lower() not in ("cookies", "cookie's", "cookie’s")
        ]
        return True, None, aliases, None

    cleanup_file("Dungeons & Dragons/The Root of the Problem/Characters/Cookie.md", clean_cookie)

    # 5g. Gelatinous Cube
    def clean_cube(meta):
        aliases = to_list(meta.get("aliases", []))
        new_aliases = ["Gelatinous Cubes" if a == "Gelantinous Cubes" else a for a in aliases]
        return True, None, new_aliases, None

    cleanup_file("Dungeons & Dragons/The Root of the Problem/Creatures/Gelatinous Cube.md", clean_cube)

    # 6. Redundant Character Possessives ('s)
    character_files = [
        "Dungeons & Dragons/The Root of the Problem/Characters/Aeris Stormlight.md",
        "Dungeons & Dragons/The Root of the Problem/Characters/Bailon the Beardless.md",
        "Dungeons & Dragons/The Root of the Problem/Characters/Breach the Blacksmith.md",
        "Dungeons & Dragons/The Root of the Problem/Characters/Brindle.md",
        "Dungeons & Dragons/The Root of the Problem/Characters/Eloran.md",
        "Dungeons & Dragons/The Root of the Problem/Characters/Felix Humblehand.md",
        "Dungeons & Dragons/The Root of the Problem/Characters/Hagatha Crane.md",
        "Dungeons & Dragons/The Root of the Problem/Characters/Kyorlin Zauvyth.md",
        "Dungeons & Dragons/The Root of the Problem/Characters/Orritha.md",
        "Dungeons & Dragons/The Root of the Problem/Characters/Players/Benny.md",
        "Dungeons & Dragons/The Root of the Problem/Characters/Players/Eldara.md",
        "Dungeons & Dragons/The Root of the Problem/Characters/Players/Saros Zafaden/Saros Zafaden.md",
        "Dungeons & Dragons/The Root of the Problem/Characters/Players/Sedna Finch.md",
        "Dungeons & Dragons/The Root of the Problem/Characters/Players/Vorrak Stoneborn.md",
        "Dungeons & Dragons/The Root of the Problem/Characters/Poxificus.md",
        "Dungeons & Dragons/The Root of the Problem/Characters/Szazz.md",
        "Dungeons & Dragons/The Root of the Problem/Characters/Vecna.md",
    ]

    for char_rel in character_files:
        def clean_possessives(meta):
            aliases = to_list(meta.get("aliases", []))
            orig_aliases = list(aliases)
            # Filter out aliases ending with 's or s' (possessive) or "Vorraks"
            new_aliases = []
            for a in aliases:
                if a.endswith(("'s", "’s", "s'")):
                    continue
                if a == "Vorraks":
                    continue
                new_aliases.append(a)
            changed = new_aliases != orig_aliases
            return changed, None, new_aliases, None

        cleanup_file(char_rel, clean_possessives)

    print("=" * 60)
    print("Remediation Completed Successfully!")
    print("=" * 60)


if __name__ == "__main__":
    run_cleanup()
