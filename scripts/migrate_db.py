#!/usr/bin/env python3
"""
Evelyn Engine Database Migration CLI.

Usage:
  python scripts/migrate_db.py --status
  python scripts/migrate_db.py --execute
  python scripts/migrate_db.py --execute --tag
  python scripts/migrate_db.py --dry-run
  python scripts/migrate_db.py --db memory --execute
"""

import argparse
import os
import subprocess
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Evelyn.version import __version__, VERSION_NAME
from Evelyn.tools.db_migrator import (
    check_all_dbs_status,
    apply_pending_migrations,
    get_db_version,
    DB_MAP,
)


def print_status_table(status_data: dict) -> None:
    print("\n" + "=" * 78)
    print(f"🌌 Evelyn Engine Database Schema Status (App: v{__version__} - {VERSION_NAME})")
    print("=" * 78)
    print(f"{'Database':<10} | {'Current Version':<18} | {'Target Version':<14} | {'Status':<12} | {'Pending'}")
    print("-" * 78)
    
    all_clean = True
    for db_name, info in status_data.items():
        status_label = "✅ UP TO DATE" if info["is_up_to_date"] else "⚠️  PENDING"
        if not info["is_up_to_date"]:
            all_clean = False
        pending_str = ", ".join(info["pending_migrations"]) if info["pending_migrations"] else "None"
        print(f"{db_name:<10} | {info['current_version']:<18} | {info['target_version']:<14} | {status_label:<12} | {pending_str}")
    
    print("-" * 78)
    if all_clean:
        print("All database schemas match application target version.")
    else:
        print("Pending migrations detected. Run with '--execute' to apply.")
    print("=" * 78 + "\n")


def create_git_tag(version_tag: str) -> None:
    tag_name = f"v{version_tag}"
    print(f"[CLI] Attempting to create Git release tag: {tag_name}...")
    try:
        # Check if tag already exists
        check_proc = subprocess.run(
            ["git", "tag", "-l", tag_name],
            capture_output=True,
            text=True,
            check=True
        )
        if tag_name in check_proc.stdout.split():
            print(f"[CLI] [WARNING] Git tag {tag_name} already exists. Skipping creation.")
            return

        subprocess.run(
            ["git", "tag", "-a", tag_name, "-m", f"Release {tag_name}: {VERSION_NAME}"],
            check=True
        )
        print(f"[CLI] ✅ Successfully created Git release tag '{tag_name}'.")
    except Exception as e:
        print(f"[CLI] [WARNING] Failed to create Git tag: {e}")


def main():
    parser = argparse.ArgumentParser(description="Evelyn Engine Database Migration Runner")
    parser.add_argument("--status", action="store_true", help="Print schema version status for all databases")
    parser.add_argument("--execute", action="store_true", help="Apply pending database migrations")
    parser.add_argument("--dry-run", action="store_true", help="Simulate migration execution without making changes")
    parser.add_argument("--tag", action="store_true", help="Create a Git release tag after successful execution")
    parser.add_argument("--db", type=str, default=None, choices=list(DB_MAP.keys()), help="Target a specific database only")
    parser.add_argument("--target", type=str, default=None, help="Target a specific schema version (default: current app version)")

    args = parser.parse_args()

    # Default action if no flags provided is --status
    if not args.execute and not args.dry_run and not args.status:
        args.status = True

    if args.status:
        status_data = check_all_dbs_status(args.target)
        print_status_table(status_data)
        return

    if args.dry_run or args.execute:
        mode_str = "DRY RUN" if args.dry_run else "EXECUTION"
        print(f"\n[CLI] Starting database migration in {mode_str} mode (Target Version: {args.target or __version__})...\n")
        
        results = apply_pending_migrations(
            target_db=args.db,
            target_version=args.target,
            dry_run=args.dry_run,
            create_snapshots=True
        )
        
        if not results:
            print("[CLI] No pending migrations to apply. All databases are up to date.")
        else:
            print(f"\n[CLI] Processed {len(results)} migration step(s) successfully.")
            for r in results:
                status_icon = "🧪" if r["status"] == "dry_run" else "✅"
                print(f" {status_icon} [{r['target_db']}] v{r['version']} ({r['name']}) -> {r['status']}")

        if args.execute and args.tag and results:
            create_git_tag(args.target or __version__)

        print("\n[CLI] Final Status:")
        print_status_table(check_all_dbs_status(args.target))


if __name__ == "__main__":
    main()
