#!/usr/bin/env python3
# pdf_staging_worker.py
# date created: 2026-08-28 11:24:49
# date modified: 2026-08-28 11:52:11
# tags:

"""
Evelyn Engine — Automated PDF Staging Worker.

Monitors and processes documents placed in vault staging directories:
- Attachments/Staging/Full_Extraction/  -> Runs extract_pdf_library.py into target domain folder
- Attachments/Staging/Sidecar_Only/    -> Runs sidecar creation and relocates source to Attachments/Source Material/

Coordinates with task_manager.py for mutual exclusion against other heavy tasks.
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path

# Anchor workspace roots for imports
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent.parent
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import extract_pdf_library

import evelyn_config as cfg
from Evelyn.tools import task_manager
from Evelyn.tools.frontmatter_utils import format_yaml_array

VAULT_ROOT = Path(getattr(cfg, "VAULT_BASE_DIR", "/home/rathius/obsidian_vault"))
STAGING_DIR = VAULT_ROOT / "Attachments" / "Staging"
FULL_EXTRACTION_STAGING = STAGING_DIR / "Full_Extraction"
SIDECAR_ONLY_STAGING = STAGING_DIR / "Sidecar_Only"
ATTACHMENTS_SOURCE_ROOT = VAULT_ROOT / "Attachments" / "Source Material"

DEFAULT_DOMAINS = [
    {"label": "Notes (Inbox)", "path": "Notes", "domain": "General"},
    {"label": "Reference Library (AI & Tech)", "path": "Reference Library", "domain": "AI"},
    {"label": "Owner's Manuals & Hardware Specs", "path": "Reference Library/Owner's Manuals", "domain": "Hardware"},
    {"label": "Medical Records (Ricky)", "path": "Ricky/Medical", "domain": "Medical"},
    {"label": "Financial & Taxes (Ricky)", "path": "Ricky/Financial", "domain": "Financial"},
    {"label": "Professional & Career (Ricky)", "path": "Ricky/Professional", "domain": "Professional"},
    {"label": "Family & Legal (Schyler)", "path": "Schyler", "domain": "Family/Schyler"},
    {"label": "Genealogy & Ancestry", "path": "Genealogy", "domain": "Genealogy"},
    {"label": "Creative & Projects", "path": "Projects", "domain": "Creative"},
    {"label": "D&D Campaigns", "path": "Dungeons & Dragons", "domain": "DnD"},
]


def get_available_domains() -> list[dict[str, str]]:
    """Return list of supported destination domains and relative vault paths."""
    return DEFAULT_DOMAINS


def _read_sidecar_metadata(meta_path: Path) -> dict:
    """Read metadata JSON if present for uploaded staged files."""
    if meta_path.exists():
        try:
            with open(meta_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"[PDF_STAGING_WORKER] Metadata read warning for {meta_path}: {e}", flush=True)
    return {}


def process_staging_item(pdf_file: Path, mode: str) -> dict:
    """Process a single PDF item from staging."""
    meta_file = pdf_file.with_name(f"{pdf_file.name}.meta.json")
    metadata = _read_sidecar_metadata(meta_file)

    target_rel_path = metadata.get("target_path") or metadata.get("domain_path") or ""
    domain_name = metadata.get("domain") or ""

    # Infer domain name and folder if not explicitly provided
    if not target_rel_path:
        target_rel_path = "Notes"
        domain_name = domain_name or "General"

    target_folder = VAULT_ROOT / target_rel_path
    target_folder.mkdir(parents=True, exist_ok=True)

    result = {
        "filename": pdf_file.name,
        "mode": mode,
        "target_path": str(target_rel_path),
        "status": "success",
        "error": None,
    }

    try:
        if mode == "full":
            # Perform full book/document extraction
            extract_res = extract_pdf_library.extract_pdf(
                str(pdf_file),
                output_dir=str(target_folder),
                domain=domain_name or "General",
                create_sidecar=True,
                move_source=True,
                attachments_dir=str(ATTACHMENTS_SOURCE_ROOT),
                skip_gists=False,
            )
            result["chapters"] = extract_res.get("chapters", 0)
            result["title"] = extract_res.get("title", "")
        else:
            # Sidecar only
            title = extract_pdf_library.sanitize_filename(
                extract_pdf_library.normalize_book_title(str(pdf_file))[0]
            )
            dest_dir = ATTACHMENTS_SOURCE_ROOT / (domain_name or "General")
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_pdf = dest_dir / pdf_file.name
            rel_dest_pdf = dest_pdf.relative_to(VAULT_ROOT)

            # Move PDF to attachments
            shutil.copy2(pdf_file, dest_pdf)

            # Generate sidecar note
            sidecar_dir = target_folder / title
            sidecar_dir.mkdir(parents=True, exist_ok=True)
            sidecar_file = sidecar_dir / f"{title}_index.md"

            tags_str = format_yaml_array([domain_name.lower().replace(' ', '/'), "source/pdf"])
            content = f"""---
title: "{title}"
type: document/card
source: "[[{rel_dest_pdf}]]"
tags: {tags_str}
created: {time.strftime('%Y-%m-%d')}
---

# {title}

## Document Viewer
![[{rel_dest_pdf}]]
"""
            with open(sidecar_file, "w", encoding="utf-8") as f:
                f.write(content)

            # Remove original from staging
            if pdf_file.exists():
                os.remove(pdf_file)

            result["title"] = title
            result["sidecar_path"] = str(sidecar_file.relative_to(VAULT_ROOT))

        # Cleanup meta file
        if meta_file.exists():
            os.remove(meta_file)

    except (OSError, ValueError, RuntimeError, KeyError) as e:
        result["status"] = "error"
        result["error"] = str(e)

    return result


def process_staging_queue(max_items: int = 10) -> list[dict]:
    """Scan staging folders and process queued PDFs respecting Task Manager mutual exclusion."""
    FULL_EXTRACTION_STAGING.mkdir(parents=True, exist_ok=True)
    SIDECAR_ONLY_STAGING.mkdir(parents=True, exist_ok=True)

    full_files = [f for f in FULL_EXTRACTION_STAGING.iterdir() if f.is_file() and f.suffix.lower() == ".pdf" and not f.name.endswith(".tmp")]
    sidecar_files = [f for f in SIDECAR_ONLY_STAGING.iterdir() if f.is_file() and f.suffix.lower() == ".pdf" and not f.name.endswith(".tmp")]

    total_pending = len(full_files) + len(sidecar_files)
    if total_pending == 0:
        return []

    # Check mutual exclusion
    if task_manager.is_any_running(exclude="pdf_staging_ingestion"):
        print("[STAGING_WORKER] Other heavy tasks currently active. Deferring staging ingestion.", flush=True)
        return []

    task_manager.set_running("pdf_staging_ingestion")
    processed = []

    try:
        # Process Full Extraction queue
        for f in full_files[:max_items]:
            res = process_staging_item(f, mode="full")
            processed.append(res)

        # Process Sidecar Only queue
        for f in sidecar_files[:max_items]:
            res = process_staging_item(f, mode="card")
            processed.append(res)

    finally:
        task_manager.clear_running("pdf_staging_ingestion")

    return processed


if __name__ == "__main__":
    results = process_staging_queue()
    print(f"Processed {len(results)} staging items: {results}")
