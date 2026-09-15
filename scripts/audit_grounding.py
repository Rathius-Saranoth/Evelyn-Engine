#!/usr/bin/env python3
# audit_grounding.py
# date created: 2026-09-14
# date modified: 2026-09-14 20:23:52
# tags: #cli, #grounding, #auditor, #proposals

"""
CLI utility to scan evelyn_memory.db for ungrounded pronouns, bare verbs, and
subject contradictions, and stage non-destructive review proposals into SQLite.

Usage:
  python scripts/audit_grounding.py --dry-run
  python scripts/audit_grounding.py --limit 10 --show-chat
  python scripts/audit_grounding.py --category Cat05-U --limit 20 --execute
"""

import argparse
import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Evelyn.tools.grounding_auditor import (
    detect_grounding_issues,
    find_surrounding_chat_context,
    stage_grounding_proposals,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit and ground memory facts.")
    parser.add_argument("--limit", type=int, default=25, help="Maximum entries to audit (default: 25)")
    parser.add_argument("--category", type=str, default=None, help="Filter by category (e.g. Cat05-U)")
    parser.add_argument("--subject", type=str, default=None, help="Filter by subject")
    parser.add_argument("--execute", action="store_true", help="Stage proposals in SQLite (default: dry run)")
    parser.add_argument("--dry-run", action="store_true", help="Preview proposed fixes without modifying DB")
    parser.add_argument("--show-chat", action="store_true", help="Fetch and display surrounding chat context")

    args = parser.parse_args()
    is_execute = args.execute and not args.dry_run

    print("\n" + "=" * 78)
    print("🔍 Evelyn Engine — Subject & Pronoun Grounding Auditor")
    print("=" * 78)
    mode_label = "⚡ EXECUTE (Staging Proposals)" if is_execute else "🛡️  DRY RUN (Read Only)"
    print(f"Mode: {mode_label} | Limit: {args.limit} | Category: {args.category or 'ALL'}")
    print("-" * 78)

    issues = detect_grounding_issues(
        limit=args.limit,
        category=args.category,
        subject=args.subject,
    )

    if not issues:
        print("✅ No ungrounded entries found matching the criteria.")
        print("=" * 78 + "\n")
        return 0

    print(f"Found {len(issues)} ungrounded entries:\n")

    for idx, item in enumerate(issues, 1):
        print(f"[{idx}/{len(issues)}] Entry #{item['entry_id']} ({item['current_category']} | Subj: {item['current_subject']})")
        print(f"  Issue:       {item['issue_type']}")
        print(f"  Reason:      {item['reason']}")
        print(f"  Original:    \"{item['original_observation']}\"")
        print(f"  Proposed:    \"{item['grounded_observation']}\"")

        if args.show_chat:
            chat_info = find_surrounding_chat_context(item["entry_id"], window=2)
            if chat_info and chat_info.get("messages"):
                print("  Chat Context:")
                for m in chat_info["messages"]:
                    prefix = "  👉" if m["is_match"] else "    "
                    content_snip = m["content"].replace("\n", " ")[:90]
                    print(f"{prefix} [{m['role']}] {content_snip}...")
            else:
                print("  Chat Context: (no FTS match found)")

        print("-" * 78)

    if is_execute:
        staged_ids = stage_grounding_proposals(issues)
        print(f"\n🎉 Successfully staged {len(staged_ids)} review proposals in proposals table:")
        print(f"   Proposal IDs: {staged_ids}")
        print("   Review them in evelyn_ui/dev.html under the Unified Review tab.\n")
    else:
        print(f"\n💡 Dry run complete. To stage these {len(issues)} items as review proposals in dev.html, run:")
        cat_arg = f" --category {args.category}" if args.category else ""
        print(f"   python scripts/audit_grounding.py --limit {args.limit}{cat_arg} --execute\n")

    print("=" * 78 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
