"""
scratch/find_bad_gists.py

Scans vault_map_data.json and reports entries that look like text-slice
fallbacks rather than proper LLM summaries.

Detection heuristics (any one is sufficient):
  - Gist ends with '...'  (truncated text slice — strongest signal)
  - Gist starts with '#'  (raw markdown header leaked through)
  - Gist contains '[['    (raw wiki-link not stripped)
  - Gist starts with '---' (frontmatter fragment)
  - Gist is very short AND looks like raw text (< 80 chars)

Run from project root: python scratch/find_bad_gists.py
"""

# find_bad_gists.py

import json
import os
import re

STATE_FILE = r"C:\Projects\LocalAI\Vault_Map\vault_map_data.json"


def looks_like_fallback(gist: str) -> tuple[bool, str]:
    """Returns (is_bad, reason)."""
    if not gist:
        return True, "empty"
    if gist.endswith("..."):
        return True, "truncated (ends with ...)"
    if gist.startswith("#"):
        return True, "raw markdown header"
    if "[[" in gist:
        return True, "contains raw wiki-links"
    if gist.startswith("---"):
        return True, "frontmatter fragment"
    return False, ""


def main():
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)

    bad = []
    for rel_path, entry in state.items():
        data = entry.get("data", {})
        gist = data.get("gist", "")
        is_bad, reason = looks_like_fallback(gist)
        if is_bad:
            bad.append((rel_path, reason, gist))

    print(f"Total entries:   {len(state)}")
    print(f"Suspected failures: {len(bad)}")
    print()

    if not bad:
        print("All gists look good!")
        return

    # Group by reason
    by_reason = {}
    for rel_path, reason, gist in bad:
        by_reason.setdefault(reason, []).append((rel_path, gist))

    for reason, entries in sorted(by_reason.items(), key=lambda x: -len(x[1])):
        print(f"-- {reason} ({len(entries)} files) --")
        for rel_path, gist in entries:
            print(f"  {rel_path}")
            print(f"    Gist preview: {gist[:100].strip()!r}")
        print()


if __name__ == "__main__":
    main()
