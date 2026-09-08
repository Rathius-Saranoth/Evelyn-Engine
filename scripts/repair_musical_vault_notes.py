# repair_musical_vault_notes.py
# date created: 2026-09-08 18:15:00
# date modified: 2026-09-08 18:26:47
# tags: #migration, #vault, #music, #normalization, #refactoring, #links

"""
repair_musical_vault_notes.py — Normalize and Refactor Leaked Notation Notes in the Vault.

Scans extracted sheet music notes (e.g. Cello Method, Cello First Lessons), recovers genuine
song/exercise titles from subheadings, atomically renames files on disk, updates frontmatter
titles and aliases, rewrites parent table-of-contents indices, and performs global wikilink
refactoring across the entire vault.

Usage:
    python scripts/repair_musical_vault_notes.py --dry-run
    python scripts/repair_musical_vault_notes.py
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys

# Ensure project root is on sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import evelyn_config as cfg
from Evelyn.tools import format_librarian, frontmatter_utils, vault_db
from Evelyn.tools.string_utils import is_notation_leak, sanitize_filename

NOISE_HEADINGS = {
    "cello", "a string", "d string", "g string", "c string", "slowly", "fast",
    "exercise for bow control and cello sound", "bowing exercise with variations",
    "say and play write the notes", "using your left arm to play the notes",
    "hold your bow firmly and move your arm carefully", "up and down until you find the right spot",
    "is the top line", "is the middle line", "is the bottom line", "is two lines below",
    "second space on top of the staff is d", "top space is g", "second space up from the bottom is c",
    "what note? what finger? what string?", "two spaces below the staff is d play d with first finger",
    "three spaces below the staff is c play c with third finger", "four spaces below the staff is b play b with fourth finger",
    "reminder: fs = 3rd finger fn = 2nd finger", "reminder", "dynamics",
}


KNOWN_RECOVERIES: dict[tuple[str, int], str] = {
    # Cello Method specific chapters
    ("Cello Method", 2): "The Staff & Musical Alphabet",
    ("Cello Method", 3): "Bass Clef & Time Signatures",
    ("Cello Method", 6): "Whole Notes & Half Notes",
    ("Cello Method", 7): "Quarter Notes & Rests",
    ("Cello Method", 8): "Changing Strings",
    ("Cello Method", 9): "Using Left Hand To Play Notes",
    ("Cello Method", 16): "Notes on C and G Strings",
    ("Cello Method", 17): "Pony Ride",
    ("Cello Method", 18): "Pony Ride (Part 2)",
    ("Cello Method", 19): "Twinkle Twinkle Little Star",
    ("Cello Method", 20): "Twinkle Twinkle Little Star (Part 2)",
    ("Cello Method", 53): "Triplets & Irish Washerwoman",
    # Cello First Lessons
    ("Cello First Lessons", 3): "Playing the Open Strings",
    ("Cello First Lessons", 21): "Bourrée from Water Music",
    ("Cello First Lessons", 24): "Soldier's March",
    ("Cello First Lessons", 25): "Glockenspiel",
}


def clean_song_name(name: str) -> str:
    """Sanitize song name, removing leading noise, quotes, and trailing annotations."""
    s = name.strip()
    s = re.sub(r'^[#\s"\'“]+|[#\s"\'”]+$', '', s)
    s = re.sub(r"\s+Fn\s*=\s*\d.*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+Fs\s*=\s*\d.*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*T\.\s*Bayly.*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*Greig\s*", "Grieg ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*Pachalbel\s*", "Pachelbel ", s, flags=re.IGNORECASE)
    if s.isupper():
        s = s.title()
    return s.strip()


def find_best_recovered_title(content: str, fallback_num: int, parent_folder: str, existing_names: set[str]) -> str:
    """Extract the most descriptive song or lesson title from note subheadings."""
    # Check known canonical chapter overrides first
    if (parent_folder, fallback_num) in KNOWN_RECOVERIES:
        override = KNOWN_RECOVERIES[(parent_folder, fallback_num)]
        existing_names.add(override)
        return override

    subheadings = re.findall(r"(?m)^(?:#{2,4})\s+(.*)$", content)
    valid_candidates = []

    for sh in subheadings:
        clean = clean_song_name(sh)
        clean_lower = clean.lower()

        if len(clean) <= 2:
            continue
        if is_notation_leak(clean):
            continue
        if clean_lower in NOISE_HEADINGS:
            continue
        # Avoid note letter lists like "A B C D" or "0 B"
        if re.match(r"^(?:\d+\s+[A-G]|\b[A-G][b#s]?\b\s*)+$", clean):
            continue
        if re.match(r"^\d+\s*[-–—]\s*\d+$", clean):
            continue

        valid_candidates.append(clean)

    title = ""
    if valid_candidates:
        if len(valid_candidates) >= 2 and len(valid_candidates[0]) + len(valid_candidates[1]) < 40:
            title = f"{valid_candidates[0]} & {valid_candidates[1]}"
        else:
            title = valid_candidates[0]
    else:
        title = f"Exercise {fallback_num}"

    # Handle duplicates across same book (e.g. Old Mac Donald part 1 vs part 2)
    if title in existing_names:
        title = f"{title} (Part 2)"

    existing_names.add(title)
    return title


def repair_musical_notes(vault_root: str | None = None, dry_run: bool = True) -> dict[str, int]:
    """Execute migration across Cello Method and Cello First Lessons collections."""
    root = vault_root or getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")
    target_dirs = [
        os.path.join(root, "Reference Library", "Learning Cello", "Cello Method"),
        os.path.join(root, "Reference Library", "Learning Cello", "Cello First Lessons"),
    ]

    total_renamed = 0
    total_links_rewritten = 0
    renamed_map: dict[str, str] = {}  # old_stem -> new_stem
    recovered_title_map: dict[str, str] = {}  # old_stem -> clean_title

    for target_dir in target_dirs:
        if not os.path.exists(target_dir):
            continue

        folder_name = os.path.basename(target_dir)
        print(f"\nScanning collection: {folder_name} (dry_run={dry_run})...")
        files = sorted(glob.glob(os.path.join(target_dir, "*.md")))
        assigned_titles: set[str] = set()

        for filepath in files:
            filename = os.path.basename(filepath)
            if filename.endswith("_index.md") or filename == "_index.md":
                continue

            old_stem = os.path.splitext(filename)[0]
            with open(filepath, encoding="utf-8") as f:
                content = f.read()

            fm_dict, body = frontmatter_utils.parse_frontmatter(content)
            current_title = str(fm_dict.get("title", "")).strip()

            # Check if file stem or frontmatter title genuinely contains a notation leak
            has_notation_stem = is_notation_leak(old_stem)
            has_notation_title = is_notation_leak(current_title) or not current_title or current_title.endswith(".md")

            if not has_notation_stem and not has_notation_title:
                continue

            num_match = re.match(r"^(\d+)", filename)
            num_val = int(num_match.group(1)) if num_match else 0
            pad_width = len(num_match.group(1)) if num_match else 2

            recovered = find_best_recovered_title(content, num_val, folder_name, assigned_titles)
            clean_safe_name = sanitize_filename(f"{num_val:0{pad_width}d} - {recovered}")
            new_filename = f"{clean_safe_name}.md"
            new_stem = clean_safe_name
            new_filepath = os.path.join(target_dir, new_filename)

            # Update frontmatter
            fm_dict["title"] = recovered
            aliases = fm_dict.get("aliases", [])
            if isinstance(aliases, str):
                aliases = [a.strip() for a in aliases.split(",") if a.strip()]
            elif not isinstance(aliases, list):
                aliases = []

            # Preserve old stem in aliases if different and not a notation leak
            if old_stem != new_stem and not is_notation_leak(old_stem) and old_stem not in aliases:
                aliases.append(old_stem)
            fm_dict["aliases"] = [a for a in aliases if not is_notation_leak(a)]

            # Format frontmatter through format_librarian
            new_raw_fm = frontmatter_utils.render_frontmatter(fm_dict)
            _, formatted_doc, _ = format_librarian.audit_document_format(
                f"{new_raw_fm}\n{body}", path=os.path.relpath(filepath, root)
            )

            print(f"  [RENAME] '{filename}'\n      --> '{new_filename}' (Title: {recovered})")

            if not dry_run:
                if new_filepath != filepath and os.path.exists(new_filepath):
                    raise FileExistsError(f"Destination path already exists: {new_filepath}")

                # Atomic write to new path
                tmp_path = f"{new_filepath}.tmp_{os.getpid()}"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(formatted_doc)
                os.replace(tmp_path, new_filepath)

                if new_filepath != filepath:
                    if os.path.exists(filepath):
                        os.remove(filepath)
                    # Sync vault_db
                    old_rel = os.path.relpath(filepath, root).replace("\\", "/")
                    new_rel = os.path.relpath(new_filepath, root).replace("\\", "/")
                    vault_db.move_document(old_rel, new_rel)
                    vault_db.update_document_librarian_audit(new_rel, title=recovered)

            total_renamed += 1
            renamed_map[old_stem] = new_stem
            recovered_title_map[old_stem] = recovered

    # Phase 2: Index Table-of-Contents & Global Wikilink Refactoring
    if renamed_map:
        print(f"\nRefactoring wikilinks across vault ({len(renamed_map)} notes renamed)...")
        all_md_files = glob.glob(os.path.join(root, "**", "*.md"), recursive=True)

        for md_path in all_md_files:
            try:
                with open(md_path, encoding="utf-8") as f:
                    note_text = f.read()
            except OSError:
                continue

            changed = False
            for old_s, new_s in renamed_map.items():
                if old_s == new_s:
                    continue
                rec_title = recovered_title_map.get(old_s, new_s)
                # Match [[old_s]] or [[old_s|alias]] or [[old_s\|alias]] (table escaped)
                pattern = re.compile(rf"\[\[{re.escape(old_s)}((\\?\|).*?)?\]\]")
                if pattern.search(note_text):
                    def _sub(m: re.Match, target: str = new_s, title: str = rec_title) -> str:
                        full_alias = m.group(1) or ""
                        pipe_token = m.group(2) or "|"
                        if full_alias:
                            inner = full_alias[len(pipe_token):].strip()
                            if is_notation_leak(inner) or not inner:
                                return f"[[{target}{pipe_token}{title}]]"
                        return f"[[{target}{full_alias}]]"

                    note_text = pattern.sub(_sub, note_text)
                    changed = True
                    total_links_rewritten += 1

            if changed and not dry_run:
                tmp_path = f"{md_path}.tmp_{os.getpid()}"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(note_text)
                os.replace(tmp_path, md_path)
                print(f"  [WIKILINK REWRITE] Updated links in: {os.path.relpath(md_path, root)}")

    # Phase 3: Table-of-Contents synchronization for _index.md files
    for target_dir in target_dirs:
        folder_name = os.path.basename(target_dir)
        idx_file = os.path.join(target_dir, f"{folder_name}_index.md")
        sync_count = sync_index_table(idx_file, target_dir, dry_run=dry_run)
        if sync_count:
            print(f"  [INDEX SYNC] Synchronized {sync_count} TOC rows in {os.path.basename(idx_file)}")

    print(f"\nDone! Total notes renamed: {total_renamed}, Total link references updated: {total_links_rewritten}")
    return {"renamed": total_renamed, "links_rewritten": total_links_rewritten}


def sync_index_table(index_path: str, folder_path: str, dry_run: bool = True) -> int:
    """Ensure TOC table rows in _index.md match actual note stems and titles on disk."""
    if not os.path.exists(index_path) or not os.path.exists(folder_path):
        return 0
    with open(index_path, encoding="utf-8") as f:
        content = f.read()

    files = {f.split(" - ")[0]: f for f in os.listdir(folder_path) if not f.endswith("_index.md") and " - " in f}

    def repl_row(m: re.Match) -> str:
        num_str = m.group(1)
        page = m.group(4)
        summary = m.group(5)

        matched_file = files.get(num_str) or files.get(f"{int(num_str):02d}")
        if matched_file:
            stem = os.path.splitext(matched_file)[0]
            with open(os.path.join(folder_path, matched_file), encoding="utf-8") as fp:
                fm, _ = frontmatter_utils.parse_frontmatter(fp.read())
            title = fm.get("title", stem.split(" - ", 1)[-1])
            return f"| {num_str} | [[{stem}\\|{title}]] | {page} | {summary} |"
        return m.group(0)

    pattern = re.compile(
        r"\|\s*(\d+)\s*\|\s*\[\[([^\|\]]+)(?:\\\|([^\]]*))?\]\]\s*\|\s*(p\.\s*\d+)\s*\|\s*([^\|]+)\s*\|"
    )
    new_content, count = pattern.subn(repl_row, content)
    if count and not dry_run and new_content != content:
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(new_content)
    return count


def main():
    parser = argparse.ArgumentParser(description="Repair musical notation titles in Obsidian vault.")
    parser.add_argument("--dry-run", action="store_true", help="Simulate transformations without writing to disk.")
    parser.add_argument("--execute", action="store_true", help="Execute file renaming and link refactoring.")
    args = parser.parse_args()

    is_dry = not args.execute
    repair_musical_notes(dry_run=is_dry)


if __name__ == "__main__":
    main()
