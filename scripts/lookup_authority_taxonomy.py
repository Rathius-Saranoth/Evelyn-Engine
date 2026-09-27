#!/usr/bin/env python3
# lookup_authority_taxonomy.py
# date created: 2026-09-27 12:45:00
# date modified: 2026-09-27 12:45:00
# tags: #taxonomy, #authority-control, #loc, #fast, #curation, #local

"""lookup_authority_taxonomy.py — Local Authority Lookup and Promotion Tool.

Queries institutional authorities (Library of Congress Subject Headings & FAST via id.loc.gov)
to discover authoritative subject headings and see-from (UF) variant aliases.
Promotes approved terms and their variants into the local SQLite controlled vocabulary
(`master_tag_taxonomy` and `master_tag_aliases` in `data/evelyn_vault.db`) with zero git leakage.

Usage:
    # Query authority for a term:
    PYTHONPATH=. python scripts/lookup_authority_taxonomy.py "sleep apnea"

    # Query and promote preferred heading + variants to local taxonomy:
    PYTHONPATH=. python scripts/lookup_authority_taxonomy.py "sleep apnea" --promote --category "health"

    # Check all unregistered tags in vault and memory against authority:
    PYTHONPATH=. python scripts/lookup_authority_taxonomy.py --check-unregistered
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import urllib.parse
import urllib.request
from typing import Any

# Ensure project root in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Evelyn.tools import string_utils, taxonomy_db, vault_db

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("lookup_authority_taxonomy")

LOC_SEARCH_URL = "https://id.loc.gov/search/?q={query}&q=cs%3Ahttp%3A%2F%2Fid.loc.gov%2Fauthorities%2Fsubjects&format=json"
LOC_SUGGEST_URL = "https://id.loc.gov/authorities/subjects/suggest/?q={query}"


def normalize_atom(term: str) -> str:
    """Normalize term to a controlled-vocabulary kebab-case atom (§5)."""
    return string_utils.slugify(term, delimiter="-").lower().strip("-")


def query_authority(term: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Search Library of Congress Authorities for a subject term.

    Returns a list of dicts with:
        title: Preferred authoritative heading
        canonical_tag: Kebab-case normalized atom
        url: Concept JSON URL
        variants: List of see-from variant terms (UF)
    """
    clean_term = term.strip()
    if not clean_term:
        return []

    q = urllib.parse.quote(clean_term)
    url = LOC_SEARCH_URL.format(query=q)
    req = urllib.request.Request(url, headers={"User-Agent": "Evelyn-Engine/1.0"})

    results: list[dict[str, Any]] = []
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        logger.debug("LOC search failed for '%s': %s", clean_term, exc)
        return []

    for item in data:
        if not isinstance(item, list) or not item or item[0] != "atom:entry":
            continue
        title = ""
        concept_url = ""
        for sub in item[1:]:
            if not isinstance(sub, list):
                continue
            if sub[0] == "atom:title" and len(sub) > 2:
                title = str(sub[2]).strip()
            elif (
                sub[0] == "atom:link"
                and len(sub) > 1
                and isinstance(sub[1], dict)
                and sub[1].get("type") == "application/json"
            ):
                concept_url = sub[1].get("href", "")

        if title and concept_url:
            results.append({
                "title": title,
                "canonical_tag": normalize_atom(title.split("--")[0]),
                "url": concept_url,
                "variants": [],
            })
            if len(results) >= max_results:
                break

    # Fetch concept details (variants / see-from references) for top results
    for res in results:
        concept_href = res["url"]
        if not concept_href:
            continue
        try:
            creq = urllib.request.Request(concept_href, headers={"User-Agent": "Evelyn-Engine/1.0"})
            with urllib.request.urlopen(creq, timeout=8) as cresp:
                cdata = json.load(cresp)
            raw_variants = []
            for obj in cdata:
                if not isinstance(obj, dict):
                    continue
                for k, v in obj.items():
                    if k.endswith("#variantLabel") and isinstance(v, list):
                        raw_variants.extend(el["@value"] for el in v if isinstance(el, dict) and "@value" in el)
            # Normalize variants
            clean_vars = set()
            for v in raw_variants:
                # Strip parentheticals e.g. "ADHD (Attention-deficit hyperactivity disorder)"
                base_v = re.sub(r"\(.*?\)", "", v).strip()
                atom_v = normalize_atom(base_v)
                if atom_v and atom_v != res["canonical_tag"]:
                    clean_vars.add(atom_v)
            res["variants"] = sorted(clean_vars)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to fetch details for %s: %s", concept_href, exc)

    return results


def get_unregistered_terms() -> dict[str, int]:
    """Find all unique tags across vault notes and memory facts that are not yet in taxonomy or aliases."""
    vault_db.init_db()
    v_con = vault_db.get_db()
    try:
        registered = {r["tag"] for r in v_con.execute("SELECT tag FROM master_tag_taxonomy").fetchall()}
        aliases = {r["alias"] for r in v_con.execute("SELECT alias FROM master_tag_aliases").fetchall()}
        known = registered | aliases

        counts: dict[str, int] = {}

        # Vault notes
        for row in v_con.execute("SELECT tags FROM vault_documents WHERE tags IS NOT NULL").fetchall():
            tags_str = row["tags"] or ""
            for t in tags_str.split(","):
                clean = t.strip()
                if clean and not clean.startswith(("type/", "motif/", "setting/", "event/")) and clean not in known:
                    counts[clean] = counts.get(clean, 0) + 1
    finally:
        v_con.close()

    # Memory facts
    from Evelyn.tools import memory_db
    memory_db.init_db()
    m_con = memory_db.get_db()
    try:
        for row in m_con.execute("SELECT tags FROM context_entries WHERE tags IS NOT NULL").fetchall():
            tags_str = row["tags"] or ""
            for t in tags_str.split(","):
                clean = t.strip()
                if clean and clean not in known:
                    counts[clean] = counts.get(clean, 0) + 1
    finally:
        m_con.close()

    return counts


def promote_term(
    canonical_tag: str,
    category: str = "general",
    description: str = "",
    variants: list[str] | None = None,
    protected: int = 0,
) -> None:
    """Promote an approved term and its variants into the local SQLite taxonomy."""
    taxonomy_db.upsert_master_tag(
        tag=canonical_tag,
        category=category or "general",
        description=description,
        usage_count=0,
    )
    if protected:
        vault_db.init_db()
        con = vault_db.get_db()
        try:
            con.execute("UPDATE master_tag_taxonomy SET protected = 1 WHERE tag = ?", (canonical_tag,))
            con.commit()
        finally:
            con.close()

    if variants:
        for var in variants:
            if var != canonical_tag:
                taxonomy_db.record_alias(alias=var, canonical=canonical_tag, tier="reviewed")
    logger.info("✔ Promoted '%s' [%s] with %d variant alias(es).", canonical_tag, category, len(variants or []))


def main() -> None:
    parser = argparse.ArgumentParser(description="Lookup institutional authority headings and promote to local taxonomy.")
    parser.add_argument("term", nargs="?", default="", help="Term or phrase to lookup in Library of Congress / FAST")
    parser.add_argument("--promote", action="store_true", help="Promote the top match into local master_tag_taxonomy")
    parser.add_argument("--category", default="general", help="Category for promoted term (default: general)")
    parser.add_argument("--description", default="", help="Optional description for promoted term")
    parser.add_argument("--check-unregistered", action="store_true", help="Scan vault and memory for unregistered terms")
    parser.add_argument("--auto-promote-exact", action="store_true", help="Automatically promote exact matches for unregistered terms")

    args = parser.parse_args()

    if args.check_unregistered:
        unreg = get_unregistered_terms()
        if not unreg:
            print("✔ No unregistered terms found across vault notes or memory facts. Controlled vocabulary 100% aligned!")
            return

        print(f"Found {len(unreg)} unregistered term(s) in active use:")
        for term, count in sorted(unreg.items(), key=lambda x: -x[1]):
            print(f"  • {term:<24} ({count} occurrence(s))")

        print("\nQuerying institutional authorities...")
        for term, _count in sorted(unreg.items(), key=lambda x: -x[1]):
            matches = query_authority(term)
            if not matches:
                print(f"\n[?] '{term}' — No authority matches found.")
                continue
            top = matches[0]
            print(f"\n[+] '{term}' -> Authority: '{top['title']}' (Canonical atom: '{top['canonical_tag']}')")
            if top["variants"]:
                print(f"    Variants: {', '.join(top['variants'][:8])}")

            if args.auto_promote_exact and (normalize_atom(term) == top["canonical_tag"] or term in top["variants"]):
                promote_term(
                    canonical_tag=top["canonical_tag"],
                    category=args.category,
                    description=f"Library of Congress Authority: {top['title']}",
                    variants=top["variants"] + ([normalize_atom(term)] if normalize_atom(term) != top["canonical_tag"] else []),
                )
        return

    if not args.term:
        parser.print_help()
        return

    print(f"Searching Library of Congress Authorities for: '{args.term}'...")
    matches = query_authority(args.term)
    if not matches:
        print(f"No authority headings found for '{args.term}'.")
        return

    print(f"\nFound {len(matches)} match(es):")
    for i, m in enumerate(matches, 1):
        print(f"  {i}. {m['title']} -> tag: [{m['canonical_tag']}]")
        print(f"     URI: {m['url']}")
        if m["variants"]:
            print(f"     Variants ({len(m['variants'])}): {', '.join(m['variants'][:10])}")

    if args.promote:
        top = matches[0]
        promote_term(
            canonical_tag=top["canonical_tag"],
            category=args.category,
            description=args.description or f"Library of Congress: {top['title']}",
            variants=top["variants"] + ([normalize_atom(args.term)] if normalize_atom(args.term) != top["canonical_tag"] else []),
        )


if __name__ == "__main__":
    main()
