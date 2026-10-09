#!/usr/bin/env python3
# generate_hyphen_decomposition.py
# tags: #taxonomy, #decomposition, #migration, #review

"""Compute which hyphenated compounds decompose into atoms, and freeze the decision.

Decomposition to atoms split hierarchical paths (`a/b/c`) and deliberately stopped at the
slash, because §5 gives the hyphen a real job: joining words inside a single term. The
measurement afterwards showed the other half of the problem — hyphenated compounds
outnumbered atoms three to one, and most of them were never written as a phrase anywhere
in the vault. Those are pre-coordinate guesses with a different separator.

Literary warrant decides (§6.3.3). Unlike a hand-curated merge review, this decision is
derived from the corpus rather than judged, so it is reproducible: rerunning this script
against the same vault yields the same plan. It is frozen to disk anyway, because a
migration must apply the plan that was reviewed, not whatever a later corpus scan produces.

The output lands in `scratch/` (gitignored) for the same reason the merge decisions do:
tag names include personal terms that must not reach a tracked file (AGENTS.md §4).

Usage:
    PYTHONPATH=. python scripts/generate_hyphen_decomposition.py [--apply-to-plan]
"""

import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import evelyn_config as cfg
from Evelyn.tools import tag_synonym, taxonomy_db

PLAN_PATH = os.path.join(cfg.BASE_DIR, "scratch", "hyphen_decomposition_plan.json")


def _canonical(atom: str) -> str:
    """Resolve one atom through the alias table, so decomposition lands on preferred terms."""
    resolved = taxonomy_db.canonicalize_tags([atom])
    return resolved[0] if resolved else atom


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=25, help="Sample rows to display.")
    args = parser.parse_args()

    conn = sqlite3.connect(cfg.VAULT_DB_PATH, timeout=30.0)
    try:
        tax = dict(conn.execute("SELECT tag, usage_count FROM master_tag_taxonomy"))
        aliases = conn.execute("SELECT alias, canonical FROM master_tag_aliases").fetchall()
    finally:
        conn.close()

    plan = tag_synonym.flat_compound_decomposition(
        tax.keys(), cfg.VAULT_BASE_DIR, canonicalize=_canonical
    )

    # An alias is a one-to-one record. When its target decomposes into several atoms there
    # is no single term left to point at, so the alias is retired with it (§6.2); the same
    # warrant rule will decompose the alias's own form when it is next encountered.
    orphaned = sorted(alias for alias, canonical in aliases if canonical in plan)

    flat = [t for t in tax if "/" not in t and "-" in t]
    atoms_before = {t for t in tax if "/" not in t and "-" not in t}
    produced: dict[str, int] = {}
    for term, atoms in plan.items():
        for atom in atoms:
            produced[atom] = produced.get(atom, 0) + tax.get(term, 0)
    new_atoms = {a: c for a, c in produced.items() if a not in atoms_before}

    os.makedirs(os.path.dirname(PLAN_PATH), exist_ok=True)
    with open(PLAN_PATH, "w", encoding="utf-8") as fh:
        json.dump({"plan": plan, "orphaned_aliases": orphaned}, fh, indent=1, sort_keys=True)

    print(f"flat hyphenated terms      : {len(flat)}")
    print(f"  kept whole (warranted)   : {len(flat) - len(plan)}")
    print(f"  decomposing              : {len(plan)}")
    print(f"atoms produced             : {len(produced)}  ({len(new_atoms)} new to the registry)")
    print(f"orphaned aliases to retire : {len(orphaned)}")
    print(f"\nplan written: {PLAN_PATH}")
    print("\nsample:")
    for term in sorted(plan, key=lambda t: -tax.get(t, 0))[: args.top]:
        print(f"   {term:34} use={tax.get(term, 0):4} -> {', '.join(plan[term])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
