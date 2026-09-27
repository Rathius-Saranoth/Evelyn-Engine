#!/usr/bin/env python3
# scripts/migrate_facets_to_properties.py
# date created: 2026-09-27 10:55:00
# tags: #migration, #taxonomy, #properties, #yaml

"""
scripts/migrate_facets_to_properties.py — Vault Frontmatter Facet Property Migration.

Migrates legacy prefix tags (type/*, motif/*, setting/*, event/*) from the frontmatter
`tags` array into native YAML frontmatter properties formatted as Visual PKM single-line
flow arrays, enforcing the Zero-Slash Invariant on subject tags.

Usage:
    PYTHONPATH=. /home/rathius/evelyn/venv/bin/python scripts/migrate_facets_to_properties.py --dry-run
    PYTHONPATH=. /home/rathius/evelyn/venv/bin/python scripts/migrate_facets_to_properties.py --apply
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from typing import Any

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import evelyn_config as cfg
from Evelyn.tools.frontmatter_utils import (
    parse_frontmatter,
    update_frontmatter_field,
    write_file_with_frontmatter,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate_facets_to_properties")


def parse_items(raw: Any) -> list[str]:
    """Parse string, list, or sequence into unique ordered tokens."""
    if not raw:
        return []
    if isinstance(raw, str):
        raw = raw.strip()
        if raw.startswith("[") and raw.endswith("]"):
            raw = raw[1:-1]
        tokens = [t.strip().strip("'\"#") for t in raw.split(",") if t.strip().strip("'\"#")]
    elif isinstance(raw, (list, set, tuple)):
        tokens = [str(t).strip().strip("'\"#") for t in raw if str(t).strip().strip("'\"#")]
    else:
        tokens = [str(raw).strip().strip("'\"#")]

    seen = set()
    ordered = []
    for t in tokens:
        if t and t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered


def migrate_vault_facets(
    vault_dir: str,
    dry_run: bool = True,
    limit: int | None = None,
    create_backup: bool = True,
) -> dict[str, Any]:
    """Inspect and migrate facet prefix tags to YAML properties."""
    vault_path = os.path.abspath(os.path.expanduser(vault_dir))
    if not os.path.exists(vault_path):
        raise FileNotFoundError(f"Vault directory not found: {vault_path}")

    all_files = [
        f for f in glob.glob(os.path.join(vault_path, "**", "*.md"), recursive=True)
        if not os.path.basename(f).startswith(".")
    ]
    all_files.sort()

    if limit:
        all_files = all_files[:limit]

    logger.info("Found %d markdown files in vault (%s). Dry-run: %s", len(all_files), vault_path, dry_run)

    stats = Counter()
    manifest: dict[str, Any] = {}

    for file_path in all_files:
        stats["total_scanned"] += 1
        rel_path = os.path.relpath(file_path, vault_path)

        try:
            with open(file_path, encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
        except OSError as e:
            logger.warning("Could not read file %s: %s", rel_path, e)
            stats["read_errors"] += 1
            continue

        meta, _body = parse_frontmatter(content)
        if not meta:
            stats["no_frontmatter"] += 1
            continue

        raw_tags = meta.get("tags")
        tag_tokens = parse_items(raw_tags)
        if not tag_tokens:
            stats["no_tags"] += 1
            continue

        type_tokens = []
        motif_tokens = []
        setting_tokens = []
        event_tokens = []
        remaining_tags = []

        # Existing properties (if any)
        existing_type = parse_items(meta.get("type"))
        existing_motif = parse_items(meta.get("motif"))
        existing_setting = parse_items(meta.get("setting"))
        existing_event = parse_items(meta.get("event"))

        has_facet_in_tags = False

        for tag in tag_tokens:
            tag_clean = tag.strip().lower()
            if tag_clean.startswith("type/"):
                has_facet_in_tags = True
                type_val = tag[5:].strip()
                if type_val and type_val not in type_tokens:
                    type_tokens.append(type_val)
            elif tag_clean.startswith("motif/"):
                has_facet_in_tags = True
                motif_val = tag[6:].strip()
                if motif_val and motif_val not in motif_tokens:
                    motif_tokens.append(motif_val)
            elif tag_clean.startswith("setting/"):
                has_facet_in_tags = True
                setting_val = tag[8:].strip()
                if setting_val and setting_val not in setting_tokens:
                    setting_tokens.append(setting_val)
            elif tag_clean.startswith("event/"):
                has_facet_in_tags = True
                event_val = tag[6:].strip()
                if event_val and event_val not in event_tokens:
                    event_tokens.append(event_val)
            else:
                remaining_tags.append(tag)

        if not has_facet_in_tags:
            stats["already_clean"] += 1
            continue

        # Combine with any existing properties
        combined_type = parse_items([*existing_type, *type_tokens])
        combined_motif = parse_items([*existing_motif, *motif_tokens])
        combined_setting = parse_items([*existing_setting, *setting_tokens])
        combined_event = parse_items([*existing_event, *event_tokens])

        updated_content = content

        # 1. Update tags
        updated_content = update_frontmatter_field(updated_content, "tags", remaining_tags)

        # 2. Update type
        if combined_type:
            updated_content = update_frontmatter_field(updated_content, "type", combined_type)
            stats["type_properties_set"] += 1

        # 3. Update motif
        if combined_motif:
            updated_content = update_frontmatter_field(updated_content, "motif", combined_motif)
            stats["motif_properties_set"] += 1

        # 4. Update setting
        if combined_setting:
            updated_content = update_frontmatter_field(updated_content, "setting", combined_setting)
            stats["setting_properties_set"] += 1

        # 5. Update event
        if combined_event:
            updated_content = update_frontmatter_field(updated_content, "event", combined_event)
            stats["event_properties_set"] += 1

        stats["files_migrated"] += 1

        record = {
            "path": rel_path,
            "before": {
                "tags": tag_tokens,
                "type": existing_type,
                "motif": existing_motif,
                "setting": existing_setting,
                "event": existing_event,
            },
            "after": {
                "tags": remaining_tags,
                "type": combined_type,
                "motif": combined_motif,
                "setting": combined_setting,
                "event": combined_event,
            },
        }
        manifest[rel_path] = record

        if not dry_run:
            try:
                write_file_with_frontmatter(file_path, updated_content, preserve_mtime=True)
            except OSError as e:
                logger.error("Failed to write %s: %s", rel_path, e)
                stats["write_errors"] += 1

    # Save manifest backup
    if create_backup and manifest:
        backup_dir = os.path.join(cfg.DATA_DIR, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        manifest_path = os.path.join(
            backup_dir, f"facets_migration_manifest_{timestamp}{'_dryrun' if dry_run else ''}.json"
        )
        try:
            with open(manifest_path, "w", encoding="utf-8") as fh:
                json.dump({"timestamp": timestamp, "stats": dict(stats), "manifest": manifest}, fh, indent=2)
            logger.info("Saved migration manifest to: %s", manifest_path)
        except OSError as e:
            logger.warning("Could not save manifest: %s", e)

    logger.info("=== Migration Summary ===")
    for k, v in sorted(stats.items()):
        logger.info("  • %s: %d", k, v)

    return {"stats": dict(stats), "manifest_count": len(manifest)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate vault prefix facet tags to YAML frontmatter properties.")
    parser.add_argument("--vault-dir", default=getattr(cfg, "VAULT_BASE_DIR", "~/obsidian_vault"))
    parser.add_argument("--dry-run", action="store_true", default=True, help="Preview changes without writing (default)")
    parser.add_argument("--apply", action="store_true", help="Apply changes directly to files")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of notes processed")
    parser.add_argument("--no-backup", action="store_true", help="Skip writing backup manifest JSON")

    args = parser.parse_args()
    dry_run = not args.apply

    logger.info("Starting Facet Properties Migration (dry_run=%s)", dry_run)
    migrate_vault_facets(
        vault_dir=args.vault_dir,
        dry_run=dry_run,
        limit=args.limit,
        create_backup=not args.no_backup,
    )


if __name__ == "__main__":
    main()
