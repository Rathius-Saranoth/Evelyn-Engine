# migrate_context_to_sqlite.py
# date created: 2026-05-24 09:52:43
# date modified: 2026-05-25 19:50:52
# tags: #migration, #context, #sqlite, #flat_file, #import

"""
migrate_context_to_sqlite.py — One-time migration of flat-file context entries to SQLite.

Walks the Context Entries directory tree and imports all CE_*.md files
into the context_entries table in evelyn_memory.db. Also imports any
remaining EX_*.md files from the Extracted/ staging folder.

Run from the project root:
    python Evelyn\\tools\\migrate_context_to_sqlite.py

Safety:
  - Does NOT delete original .md files. Archive them manually after verification.
  - Skips files that fail to parse (logs a warning).
  - Idempotent: re-running will insert duplicates — check count first.

Parses each file for:
  - Category (from folder name or filename for EX_ files)
  - Subject ('E' or 'R' from category suffix)
  - Observation (from **Summary:** line)
  - Date (from filename: CE_YYYY-MM-DD_*.md)
  - Confidence (from **Confidence:** line or frontmatter, default 'medium')
  - Secondary categories (from **Secondary:** line)
"""

import os
import re
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Path bootstrap
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import evelyn_config as cfg
import Evelyn.tools.memory_db as memory_db

# ---------------------------------------------------------------------------
# Regex patterns (shared with fact_consolidator)
# ---------------------------------------------------------------------------
_SUMMARY_RE = re.compile(r"\*\*Summary:\*\*\s*(.+)", re.IGNORECASE | re.DOTALL)
_CONFIDENCE_RE = re.compile(r"\*\*Confidence:\*\*\s*(\w+)", re.IGNORECASE)
_SECONDARY_RE = re.compile(r"\*\*Secondary:\*\*\s*(.+)", re.IGNORECASE)
_DATE_FROM_FILENAME_RE = re.compile(r"(?:CE|EX)_(\d{4}-\d{2}-\d{2})")
_CAT_CODE_RE = re.compile(r"(Cat\d{2}-[ER])", re.IGNORECASE)


def _parse_ce_file(path: Path, category: str, subject: str) -> dict | None:
    """Parse a single CE_ or EX_ markdown file into a dict for DB insertion.

    Args:
        path:     Absolute path to the .md file.
        category: Category code from the containing folder, e.g. 'Cat05-R'.
        subject:  'E' or 'R'.

    Returns:
        Dict with keys matching memory_db.insert_entry() params, or None on failure.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"  WARNING: Could not read {path.name}: {e}")
        return None

    # Extract observation (was called Summary in flat files)
    summ_match = _SUMMARY_RE.search(text)
    if not summ_match:
        print(f"  WARNING: No **Summary:** found in {path.name}")
        return None

    # The observation may span multiple lines — take everything after **Summary:**
    # up to the next **Field:** or end of file
    observation = summ_match.group(1).strip()
    # Truncate at the next field marker if multi-line
    next_field = re.search(r"\n\*\*\w+:\*\*", observation)
    if next_field:
        observation = observation[:next_field.start()].strip()

    if not observation:
        print(f"  WARNING: Empty observation in {path.name}")
        return None

    # Extract date from filename
    date_match = _DATE_FROM_FILENAME_RE.search(path.name)
    date = date_match.group(1) if date_match else None

    # Extract confidence (from body or frontmatter)
    conf_match = _CONFIDENCE_RE.search(text)
    confidence = conf_match.group(1).lower() if conf_match else "medium"
    if confidence not in ("high", "medium", "low"):
        confidence = "medium"

    # Extract secondary categories
    sec_match = _SECONDARY_RE.search(text)
    secondary_cats = sec_match.group(1).strip() if sec_match else None
    # Clean up — remove category name descriptions in parens
    if secondary_cats:
        # "Cat07-E, Cat02-E, Cat08-E" or "Cat07-E (Core Values)"
        codes = _CAT_CODE_RE.findall(secondary_cats)
        secondary_cats = ", ".join(codes) if codes else None

    # Determine source type
    is_extracted = path.name.startswith("EX_")
    source = "extracted" if is_extracted else "manual"
    status = "extracted" if is_extracted else "live"

    # Expand subject letter to full name
    subject_name = "Evelyn" if subject == "E" else "Ricky"

    return {
        "category": category,
        "subject": subject_name,
        "observation": observation,
        "confidence": confidence,
        "source": source,
        "status": status,
        "date": date,
        "secondary_cats": secondary_cats,
        "original_file": path.name,
    }


def migrate() -> None:
    """Main migration function. Walks Context Entries and imports to SQLite."""
    entries_dir = Path(cfg.CONTEXT_ENTRIES_DIR)
    if not entries_dir.exists():
        print(f"ERROR: Context Entries directory not found: {entries_dir}")
        return

    # Initialize DB schema
    memory_db.init_db()

    # Check if DB already has entries (safety check)
    existing_count = memory_db.count_entries()
    if existing_count > 0:
        print(f"\n  WARNING: evelyn_memory.db already has {existing_count} entries.")
        print("  Re-running will create duplicates. Continue? (y/N)")
        if input("  > ").strip().lower() != "y":
            print("  Aborted.")
            return

    skip_dirs = {"Pending", "Extracted", "_archived"}
    imported = 0
    skipped = 0
    errors = 0
    category_counts: dict[str, int] = {}

    start = time.time()

    # Walk live CE_ files in Cat##/Cat##-{E,R}/ subdirectories
    for cat_dir in sorted(entries_dir.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name in skip_dirs:
            continue
        if not cat_dir.name.startswith("Cat"):
            continue

        for subcat_dir in sorted(cat_dir.iterdir()):
            if not subcat_dir.is_dir():
                continue
            cat_match = _CAT_CODE_RE.match(subcat_dir.name)
            if not cat_match:
                continue

            category = cat_match.group(1)
            subject = category[-1]  # "E" or "R"

            for md_file in sorted(subcat_dir.glob("*.md")):
                if md_file.name.startswith("_"):
                    continue

                parsed = _parse_ce_file(md_file, category, subject)
                if parsed:
                    try:
                        memory_db.insert_entry(**parsed)
                        imported += 1
                        category_counts[category] = category_counts.get(category, 0) + 1
                    except Exception as e:
                        print(f"  ERROR inserting {md_file.name}: {e}")
                        errors += 1
                else:
                    skipped += 1

    # Walk Extracted/ folder for any remaining EX_ files
    extracted_dir = entries_dir / "Extracted"
    if extracted_dir.exists():
        for md_file in sorted(extracted_dir.glob("EX_*.md")):
            if md_file.name.startswith("_"):
                continue

            # Parse category from filename
            cat_match = _CAT_CODE_RE.search(md_file.name)
            if not cat_match:
                print(f"  WARNING: No category in EX_ filename: {md_file.name}")
                skipped += 1
                continue

            category = cat_match.group(1)
            subject = category[-1]

            parsed = _parse_ce_file(md_file, category, subject)
            if parsed:
                try:
                    memory_db.insert_entry(**parsed)
                    imported += 1
                    cat_key = f"{category} (extracted)"
                    category_counts[cat_key] = category_counts.get(cat_key, 0) + 1
                except Exception as e:
                    print(f"  ERROR inserting {md_file.name}: {e}")
                    errors += 1
            else:
                skipped += 1

    elapsed = time.time() - start

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"  Migration Complete — {elapsed:.1f}s")
    print(f"{'=' * 60}")
    print(f"  Imported:  {imported}")
    print(f"  Skipped:   {skipped}")
    print(f"  Errors:    {errors}")
    print(f"\n  By category:")
    for cat, count in sorted(category_counts.items()):
        print(f"    {cat}: {count}")

    # Verify against DB
    db_total = memory_db.count_entries()
    print(f"\n  DB total entries: {db_total}")
    if db_total != imported + existing_count:
        print(f"  WARNING: Expected {imported + existing_count}, got {db_total}")
    else:
        print(f"  OK — Count matches.")
    print()


if __name__ == "__main__":
    migrate()
