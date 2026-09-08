# repair_owner_manuals.py
# date created: 2026-09-08 18:40:00
# date modified: 2026-09-08 18:40:59
# tags: #migration, #vault, #manuals, #normalization, #pruning

"""
repair_owner_manuals.py — Repair Mojibake Manual Notes & Prune Non-English Sections.

1. Repairs 15 owner's manual notes containing Unicode replacement characters (\ufffd / mojibake).
2. Prunes non-English duplicate translation chapters (Russian, Japanese, Korean, Chinese, French, Spanish).
3. Synchronizes evelyn_vault.db (moves, updates, deletions).
4. Rebuilds table-of-contents in manual _index.md files.

Usage:
    python scripts/repair_owner_manuals.py --dry-run
    python scripts/repair_owner_manuals.py
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

# ---------------------------------------------------------------------------
# 1. MOJIBAKE REPAIR DEFINITIONS (15 files)
# Mapping: (folder_name, match_pattern_or_exact) -> (new_filename, clean_title)
# ---------------------------------------------------------------------------

MOJIBAKE_REPAIRS: list[dict[str, str]] = [
    # GE GNE27JYMFS
    {
        "folder": "GE GNE27JYMFS",
        "file_prefix": "019 - USING THE REFRIGERATOR",
        "new_filename": "019 - Water Filter Safety & Guidelines.md",
        "title": "Water Filter Safety & Guidelines",
    },
    # Samsung NE59J7630SS
    {
        "folder": "Samsung NE59J7630SS",
        "file_prefix": "06 - ",
        "new_filename": "06 - Steam Cleaning.md",
        "title": "Steam Cleaning",
    },
    {
        "folder": "Samsung NE59J7630SS",
        "file_prefix": "07 - ",
        "new_filename": "07 - Model Comparison & Specifications.md",
        "title": "Model Comparison & Specifications",
    },
    # AOSmith G9-T4040NVR 400
    {
        "folder": "AOSmith G9-T4040NVR 400",
        "file_prefix": "02 - Installa",
        "new_filename": "02 - Installation Instructions and Use & Care Guide.md",
        "title": "Installation Instructions and Use & Care Guide",
    },
    {
        "folder": "AOSmith G9-T4040NVR 400",
        "file_prefix": "03 - COMPLETED INSTALLATION",
        "new_filename": "03 - Completed Installation (Typical).md",
        "title": "Completed Installation (Typical)",
    },
    {
        "folder": "AOSmith G9-T4040NVR 400",
        "file_prefix": "14 - Ven",
        "new_filename": "14 - Venting.md",
        "title": "Venting",
    },
    {
        "folder": "AOSmith G9-T4040NVR 400",
        "file_prefix": "17 - home is equipped",
        "new_filename": "17 - Proper Operation Requirements.md",
        "title": "Proper Operation Requirements",
    },
    {
        "folder": "AOSmith G9-T4040NVR 400",
        "file_prefix": "22 - ture Regula",
        "new_filename": "22 - Temperature Regulation.md",
        "title": "Temperature Regulation",
    },
    {
        "folder": "AOSmith G9-T4040NVR 400",
        "file_prefix": "23 - Step 2 Verify that the loca",
        "new_filename": "23 - Step 2 Verify that the Location is Appropriate.md",
        "title": "Step 2 Verify that the Location is Appropriate",
    },
    {
        "folder": "AOSmith G9-T4040NVR 400",
        "file_prefix": "30 - Install Shutoff and Ther- mosta",
        "new_filename": "30 - Install Shutoff and Thermostatic Mixing Valves.md",
        "title": "Install Shutoff and Thermostatic Mixing Valves",
    },
    {
        "folder": "AOSmith G9-T4040NVR 400",
        "file_prefix": "34 - Verify Connec",
        "new_filename": "34 - Verify Connections and Completely Fill Tank.md",
        "title": "Verify Connections and Completely Fill Tank",
    },
    {
        "folder": "AOSmith G9-T4040NVR 400",
        "file_prefix": "35 - Install Dra",
        "new_filename": "35 - Install Draft Hood.md",
        "title": "Install Draft Hood",
    },
    {
        "folder": "AOSmith G9-T4040NVR 400",
        "file_prefix": "36 - Make Gas Connec",
        "new_filename": "36 - Make Gas Connections.md",
        "title": "Make Gas Connections",
    },
    {
        "folder": "AOSmith G9-T4040NVR 400",
        "file_prefix": "37 - Ligh",
        "new_filename": "37 - Lighting Instructions.md",
        "title": "Lighting Instructions",
    },
    {
        "folder": "AOSmith G9-T4040NVR 400",
        "file_prefix": "45 - Rou",
        "new_filename": "45 - Routine Maintenance.md",
        "title": "Routine Maintenance",
    },
]


def repair_mojibake_files(vault_root: str, dry_run: bool = True) -> int:
    """Rename and heal the 15 mojibake notes across owner's manuals."""
    print(f"\n--- Phase 1: Repairing Mojibake Notes (dry_run={dry_run}) ---")
    manuals_base = os.path.join(vault_root, "Reference Library", "Owner's Manuals")
    repaired_count = 0

    for rep in MOJIBAKE_REPAIRS:
        folder_path = os.path.join(manuals_base, rep["folder"])
        if not os.path.exists(folder_path):
            continue

        matched_file = None
        for fn in os.listdir(folder_path):
            if fn.startswith(rep["file_prefix"]):
                matched_file = fn
                break

        if not matched_file:
            # Check if destination already exists (already repaired)
            dest_path = os.path.join(folder_path, rep["new_filename"])
            if os.path.exists(dest_path):
                print(f"  [OK] Already repaired: {rep['new_filename']}")
            else:
                print(f"  [WARN] Could not find match for prefix '{rep['file_prefix']}' in {rep['folder']}")
            continue

        src_path = os.path.join(folder_path, matched_file)
        dest_path = os.path.join(folder_path, rep["new_filename"])

        with open(src_path, encoding="utf-8", errors="replace") as f:
            content = f.read()

        fm_dict, body = frontmatter_utils.parse_frontmatter(content)
        clean_title = rep["title"]
        source_name = fm_dict.get("source", rep["folder"])

        # Update frontmatter
        fm_dict["title"] = f"{source_name} — {clean_title}"
        fm_dict["aliases"] = []

        # Replace top heading
        body_lines = body.split("\n")
        new_body_lines = []
        replaced_heading = False
        for line in body_lines:
            if not replaced_heading and line.startswith("# "):
                new_body_lines.append(f"# {source_name} — {clean_title}")
                replaced_heading = True
            else:
                new_body_lines.append(line)
        if not replaced_heading:
            new_body_lines.insert(0, f"# {source_name} — {clean_title}\n")

        new_body = "\n".join(new_body_lines)
        new_raw_fm = frontmatter_utils.render_frontmatter(fm_dict)
        formatted_doc = f"{new_raw_fm}\n{new_body}"

        # Audit formatting through format_librarian
        _, audited_doc, _ = format_librarian.audit_document_format(
            formatted_doc, path=os.path.relpath(dest_path, vault_root)
        )

        print(f"  [REPAIR] '{matched_file}'\n      --> '{rep['new_filename']}' (Title: {clean_title})")

        if not dry_run:
            tmp_path = f"{dest_path}.tmp_{os.getpid()}"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(audited_doc)
            os.replace(tmp_path, dest_path)

            if src_path != dest_path and os.path.exists(src_path):
                os.remove(src_path)

            # Synchronize vault_db
            old_rel = os.path.relpath(src_path, vault_root).replace("\\", "/")
            new_rel = os.path.relpath(dest_path, vault_root).replace("\\", "/")
            vault_db.move_document(old_rel, new_rel)
            vault_db.update_document_librarian_audit(new_rel, title=f"{source_name} — {clean_title}")

        repaired_count += 1

    print(f"Repaired {repaired_count} mojibake notes.")
    return repaired_count


# ---------------------------------------------------------------------------
# 2. PRUNING NON-ENGLISH SECTIONS
# ---------------------------------------------------------------------------

def is_non_english_note(filename: str, content: str) -> bool:
    """Determine if an owner's manual note is a non-English chapter."""
    fname = os.path.basename(filename)

    # 1. Non-Latin scripts in filename
    if re.search(r"[\u0400-\u04ff\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", fname):
        return True

    # 2. Specific French/Spanish file prefixes in GE
    if fname.startswith(("072 - à congélateur", "129 - INSTALLATION DU RÉFRIGÉRATEUR", "180 - DIMENSIONES DEL ELECTRODOMÉSTICO")):
        return True

    # 3. Sound Blaster non-English
    if fname in ("03 - PC Mac.md", "04 - KR RU.md"):
        return True

    # 4. MSI PROB650-PWIFI duplicate translation chapters (094 to 226, plus regional non-English 242 to 245)
    num_match = re.match(r"^(\d+)", fname)
    if num_match:
        sec_num = int(num_match.group(1))
        if 94 <= sec_num <= 226 or 242 <= sec_num <= 245:
            return True

    return False


def prune_non_english_sections(vault_root: str, dry_run: bool = True) -> int:
    """Remove redundant non-English duplicate chapters across owner's manuals."""
    print(f"\n--- Phase 2: Pruning Non-English Sections (dry_run={dry_run}) ---")
    manuals_base = os.path.join(vault_root, "Reference Library", "Owner's Manuals")
    target_manuals = ["MSI PROB650-PWIFI", "GE GNE27JYMFS", "Sound Blaster G3"]
    pruned_count = 0

    for manual_name in target_manuals:
        manual_dir = os.path.join(manuals_base, manual_name)
        if not os.path.exists(manual_dir):
            continue

        files = sorted(glob.glob(os.path.join(manual_dir, "*.md")))
        for filepath in files:
            fname = os.path.basename(filepath)
            if fname.endswith("_index.md") or fname == "_index.md":
                continue

            with open(filepath, encoding="utf-8", errors="ignore") as f:
                content = f.read()

            if is_non_english_note(fname, content):
                print(f"  [PRUNE] {manual_name}/{fname}")
                if not dry_run:
                    # Remove file
                    os.remove(filepath)
                    # Delete from vault_db
                    rel_path = os.path.relpath(filepath, vault_root).replace("\\", "/")
                    vault_db.delete_document(rel_path)

                pruned_count += 1

    print(f"Pruned {pruned_count} non-English notes.")
    return pruned_count


# ---------------------------------------------------------------------------
# 3. INDEX TOC TABLE SYNCHRONIZATION
# ---------------------------------------------------------------------------

def sync_manual_index(index_path: str, manual_dir: str, dry_run: bool = True) -> int:
    """Rebuild the TOC markdown table in a manual's _index.md to match remaining files."""
    if not os.path.exists(index_path):
        return 0

    with open(index_path, encoding="utf-8") as f:
        idx_content = f.read()

    files = sorted(glob.glob(os.path.join(manual_dir, "*.md")))
    valid_notes = []
    for fp in files:
        fn = os.path.basename(fp)
        if fn.endswith("_index.md") or fn == "_index.md":
            continue
        valid_notes.append(fp)

    rows = []
    rows.append("| Section | Title | Page | Summary |")
    rows.append("|:---:|:---|:---:|:---|")

    for fp in valid_notes:
        fn = os.path.basename(fp)
        stem = os.path.splitext(fn)[0]
        num_match = re.match(r"^(\d+)", fn)
        sec_num = num_match.group(1) if num_match else "--"

        with open(fp, encoding="utf-8", errors="ignore") as f:
            c = f.read()
        if is_non_english_note(fn, c):
            continue

        fm_dict, _ = frontmatter_utils.parse_frontmatter(c)
        title = fm_dict.get("title", stem)
        if " — " in title:
            title = title.split(" — ", 1)[1].strip()

        rows.append(f"| {sec_num} | [[{stem}\\|{title}]] | — | _Summary pending_ |")

    new_table = "\n".join(rows)

    # Replace existing table in _index.md
    table_pattern = re.compile(r"\| Section \| Title \| Page \| Summary \|\n\|:?---.*?\n(?:\|.*?\n)+", re.MULTILINE)
    if table_pattern.search(idx_content):
        updated_idx = table_pattern.sub(f"{new_table}\n", idx_content)
    else:
        # Append table before Related Notes if present
        if "## 🔗 Related Notes" in idx_content:
            updated_idx = idx_content.replace("## 🔗 Related Notes", f"## 📑 Table of Contents\n\n{new_table}\n\n## 🔗 Related Notes")
        else:
            updated_idx = f"{idx_content}\n\n## 📑 Table of Contents\n\n{new_table}\n"

    if updated_idx != idx_content:
        if not dry_run:
            tmp_path = f"{index_path}.tmp_{os.getpid()}"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(updated_idx)
            os.replace(tmp_path, index_path)
        return len(valid_notes)
    return 0


def sync_all_indices(vault_root: str, dry_run: bool = True) -> None:
    """Synchronize TOC tables in index files for affected manuals."""
    print(f"\n--- Phase 3: Synchronizing Index TOC Tables (dry_run={dry_run}) ---")
    manuals_base = os.path.join(vault_root, "Reference Library", "Owner's Manuals")
    affected_manuals = [
        "GE GNE27JYMFS",
        "Samsung NE59J7630SS",
        "AOSmith G9-T4040NVR 400",
        "MSI PROB650-PWIFI",
        "Sound Blaster G3",
    ]

    for m in affected_manuals:
        mdir = os.path.join(manuals_base, m)
        idx_file = os.path.join(mdir, f"{m}_index.md")
        if os.path.exists(idx_file):
            cnt = sync_manual_index(idx_file, mdir, dry_run=dry_run)
            print(f"  [INDEX SYNC] {m}_index.md ({cnt} rows synchronized)")


# ---------------------------------------------------------------------------
# MAIN CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Repair mojibake notes and prune non-English chapters.")
    parser.add_argument("--dry-run", action="store_true", help="Preview modifications without writing to disk or database.")
    parser.add_argument("--vault-root", type=str, default=None, help="Custom vault root path.")
    args = parser.parse_args()

    vault_root = args.vault_root or getattr(cfg, "VAULT_BASE_DIR", os.path.expanduser("~/obsidian_vault"))
    print(f"Running manual repair and pruning on: {vault_root}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE EXECUTION'}")

    repaired = repair_mojibake_files(vault_root, dry_run=args.dry_run)
    pruned = prune_non_english_sections(vault_root, dry_run=args.dry_run)
    sync_all_indices(vault_root, dry_run=args.dry_run)

    print("\n==========================================")
    print(f"Summary: {repaired} notes repaired, {pruned} notes pruned.")
    print("==========================================")


if __name__ == "__main__":
    main()
