#!/usr/bin/env python3
"""Migrate remaining vault PDFs to Attachments/Source Material and create Sidecar cards."""

import os
import re
import shutil
import time
from pathlib import Path

VAULT_ROOT = Path("/home/rathius/obsidian_vault")
ATTACHMENTS_ROOT = VAULT_ROOT / "Attachments" / "Source Material"


def get_domain_and_tag(rel_path_str: str) -> tuple[str, str]:
    """Map relative path to domain subfolder in Attachments and taxonomy tag."""
    p = Path(rel_path_str)
    parts = p.parts

    if "Medical" in parts:
        sub = parts[parts.index("Medical") + 1] if len(parts) > parts.index("Medical") + 1 and not parts[parts.index("Medical") + 1].endswith(".pdf") else ""
        domain = f"Medical/{sub}" if sub else "Medical"
        tag = f"personal/medical/{sub.lower()}" if sub else "personal/medical"
    elif "Financial" in parts:
        domain = "Financial"
        tag = "personal/financial"
    elif "Professional" in parts:
        domain = "Professional"
        tag = "personal/professional"
    elif "Schyler" in parts:
        domain = "Family/Schyler"
        tag = "family/schyler"
    elif "Genealogy" in parts:
        domain = "Genealogy"
        tag = "personal/genealogy"
    elif "Pets" in parts:
        domain = "Pets"
        tag = "personal/pets"
    elif "Recipes" in parts:
        domain = "Recipes"
        tag = "lifestyle/recipes"
    elif "Talonesti" in parts:
        domain = "Creative/Talonesti"
        tag = "creative/talonesti"
    elif "Dungeons & Dragons" in parts:
        domain = "DnD"
        tag = "creative/dnd"
    else:
        domain = "General"
        tag = "document/pdf"

    return domain, tag


def clean_title_from_filename(filename: str) -> str:
    name = re.sub(r'\.pdf$', '', filename, flags=re.IGNORECASE)
    # Replace underscores with spaces unless it looks like a code
    if "_" in name and not re.match(r'^[A-Z0-9_-]+$', name):
        name = name.replace("_", " ")
    return name.strip()


def migrate_all_remaining_pdfs(dry_run: bool = False):
    pdf_files = []
    for root, _, files in os.walk(VAULT_ROOT):
        # Skip existing Attachments/
        if "/Attachments" in root:
            continue
        for f in files:
            if f.lower().endswith(".pdf"):
                pdf_files.append(Path(root) / f)

    print(f"Found {len(pdf_files)} remaining PDF(s) to process.")

    for pdf_path in sorted(pdf_files):
        rel_path = pdf_path.relative_to(VAULT_ROOT)
        domain, tag = get_domain_and_tag(str(rel_path))
        dest_dir = ATTACHMENTS_ROOT / domain
        dest_pdf = dest_dir / pdf_path.name
        rel_dest_pdf = dest_pdf.relative_to(VAULT_ROOT)

        title = clean_title_from_filename(pdf_path.name)
        sidecar_path = pdf_path.parent / f"{title}.md"

        print(f"\nProcessing: {rel_path}")
        print(f"  -> Domain: {domain} (tag: #{tag})")
        print(f"  -> Relocated PDF: {rel_dest_pdf}")
        print(f"  -> Sidecar Card: {sidecar_path.relative_to(VAULT_ROOT)}")

        if not dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)
            # Copy PDF to attachments
            shutil.copy2(pdf_path, dest_pdf)

            # Generate Sidecar Note content
            sidecar_content = f"""---
title: "{title}"
type: document/card
source: "[[{rel_dest_pdf}]]"
tags:
  - {tag}
  - source/pdf
created: {time.strftime('%Y-%m-%d')}
---

# {title}

## Document Viewer
![[{rel_dest_pdf}]]
"""
            # Write sidecar note if not already existing with custom content
            if not sidecar_path.exists():
                with open(sidecar_path, "w", encoding="utf-8") as f:
                    f.write(sidecar_content)
                print(f"  Wrote Sidecar Note: {sidecar_path.name}")
            else:
                print(f"  Sidecar note already exists: {sidecar_path.name}")

            # Remove original PDF from non-attachment folder
            if os.path.exists(pdf_path) and os.path.abspath(pdf_path) != os.path.abspath(dest_pdf):
                os.remove(pdf_path)
                print(f"  Removed source: {pdf_path.name}")

    print("\n[OK] Migration completed successfully.")


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    migrate_all_remaining_pdfs(dry_run=dry)
