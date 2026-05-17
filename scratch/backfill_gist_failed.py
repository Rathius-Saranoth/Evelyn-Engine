"""
scratch/backfill_gist_failed.py

One-time backfill: scans vault_map_data.json for entries that look like
text-slice fallbacks (same heuristics as find_bad_gists.py) and sets
gist_failed=True on them so the next vault map run picks them up for retry.
"""

# backfill_gist_failed.py

import json

STATE_FILE = r"C:\Projects\LocalAI\Vault_Map\vault_map_data.json"


def looks_like_fallback(gist: str) -> tuple[bool, str]:
    if not gist:
        return True, "empty"
    if gist.endswith("..."):
        return True, "truncated"
    if gist.startswith("#"):
        return True, "raw markdown header"
    if "[[" in gist:
        return True, "raw wiki-links"
    if gist.startswith("---"):
        return True, "frontmatter fragment"
    return False, ""


def main():
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)

    flagged = []
    for rel_path, entry in state.items():
        data = entry.get("data", {})
        gist = data.get("gist", "")
        is_bad, reason = looks_like_fallback(gist)
        if is_bad and not data.get("gist_failed"):
            data["gist_failed"] = True
            flagged.append((rel_path, reason))

    if not flagged:
        print("Nothing to backfill — all entries already flagged or clean.")
        return

    print(f"Backfilling {len(flagged)} entries:")
    for rel_path, reason in flagged:
        print(f"  [{reason}] {rel_path}")

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

    print(f"\nSaved. Next vault map run will retry these {len(flagged)} files.")


if __name__ == "__main__":
    main()
