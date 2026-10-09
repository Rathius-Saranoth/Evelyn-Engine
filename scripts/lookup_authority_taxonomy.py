#!/usr/bin/env python3
# lookup_authority_taxonomy.py
# date created: 2026-09-27 12:45:00
# date modified: 2026-09-29 19:15:09
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
import concurrent.futures
import contextlib
import json
import logging
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Any

# Ensure project root in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Evelyn.tools import string_utils, taxonomy_db, vault_db

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("lookup_authority_taxonomy")

LOC_SEARCH_URL = "https://id.loc.gov/search/?q={query}&q=cs%3Ahttp%3A%2F%2Fid.loc.gov%2Fauthorities%2Fsubjects&count=25&format=json"
LOC_SUGGEST_URL = "https://id.loc.gov/authorities/subjects/suggest/?q={query}"
FAST_SUGGEST_URL = "https://fast.oclc.org/searchfast/fastsuggest?query={query}&queryIndex=suggestall&queryReturn=suggestall%2Cidroot%2Cauth%2Ctag%2Ctype&rows=30&suggest=autoSubject"

# In-memory query cache: clean_term:max_results -> (cached_at_ts, diagnostic_dict)
_QUERY_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 1800  # 30 minutes


def normalize_atom(term: str) -> str:
    """Normalize term to a controlled-vocabulary kebab-case atom (§5)."""
    return string_utils.slugify(term, delimiter="-").lower().strip("-")


def query_fast(clean_term: str, max_results: int = 30) -> tuple[list[dict[str, Any]], str | None]:
    """Query OCLC FAST Suggest API for authoritative headings and variants."""
    q = urllib.parse.quote(clean_term)
    url = FAST_SUGGEST_URL.format(query=q)
    req = urllib.request.Request(url, headers={"User-Agent": "Evelyn-Engine/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        err = f"OCLC FAST: {exc}"
        logger.debug("FAST search failed for '%s': %s", clean_term, exc)
        return [], err

    docs = data.get("response", {}).get("docs", [])
    grouped: dict[str, dict[str, Any]] = {}

    for d in docs:
        auth = str(d.get("auth", "")).strip()
        if not auth:
            continue
        canonical_tag = normalize_atom(auth.split("--")[0])
        if not canonical_tag:
            continue

        raw_idroot = d.get("idroot", [""])[0] if isinstance(d.get("idroot"), list) else str(d.get("idroot") or "")
        url_fast = f"https://id.worldcat.org/fast/{raw_idroot}" if raw_idroot else ""
        raw_tag = str(d.get("tag", "150")).strip()
        match_type = str(d.get("type", "auth")).strip()

        # Determine if match occurred via acronym or see-from variant
        matched_via = None
        auth_lower = auth.lower()
        if clean_term not in auth_lower:
            for s in d.get("suggestall", []):
                if isinstance(s, str) and (s.lower() == clean_term or clean_term in s.lower()):
                    matched_via = s.strip()
                    break

        if canonical_tag not in grouped:
            grouped[canonical_tag] = {
                "title": auth,
                "canonical_tag": canonical_tag,
                "url": url_fast,
                "source": "FAST",
                "marc_tag": raw_tag,
                "match_type": match_type,
                "matched_via": matched_via,
                "variants": set(),
            }

        # Collect see-from variants from suggestall
        for v in d.get("suggestall", []):
            if not isinstance(v, str):
                continue
            base_v = re.sub(r"\(.*?\)", "", v).strip()
            atom_v = normalize_atom(base_v)
            if atom_v and atom_v != canonical_tag:
                grouped[canonical_tag]["variants"].add(atom_v)

    results = []
    for g in grouped.values():
        results.append({
            "title": g["title"],
            "canonical_tag": g["canonical_tag"],
            "url": g["url"],
            "source": g["source"],
            "marc_tag": g["marc_tag"],
            "match_type": g["match_type"],
            "matched_via": g["matched_via"],
            "variants": g["variants"],
        })
        if len(results) >= max_results:
            break

    return results, None


def fetch_loc_concept_facets(concept_href: str, canon_tag: str) -> dict[str, Any]:
    """Fetch 4 thesaurus facets (UF, BT, NT, RT) from an authoritative Library of Congress concept record."""
    empty_facets: dict[str, Any] = {"uf": [], "bt": [], "nt": [], "rt": []}
    if not concept_href:
        return empty_facets

    url = concept_href
    if url.startswith("http://"):
        url = "https://" + url[7:]
    if not url.endswith(".json"):
        url = url.rstrip("/") + ".json"

    try:
        creq = urllib.request.Request(url, headers={"User-Agent": "Evelyn-Engine/1.0"})
        with urllib.request.urlopen(creq, timeout=5) as cresp:
            cdata = json.load(cresp)
    except Exception:  # noqa: BLE001
        return empty_facets

    nodes_by_id = {node.get("@id"): node for node in cdata if isinstance(node, dict) and "@id" in node}

    main_node = None
    for node in nodes_by_id.values():
        types = node.get("@type", [])
        if any("Topic" in t or "Concept" in t or "Authority" in t for t in types) and any(
            "broader" in k or "narrower" in k or "related" in k or "altLabel" in k for k in node
        ):
            main_node = node
            break
    if not main_node:
        for node in nodes_by_id.values():
            if any("Topic" in t or "Concept" in t for t in node.get("@type", [])):
                main_node = node
                break

    def _extract_label(n: dict[str, Any] | None) -> str | None:
        if not n:
            return None
        for k in (
            "http://www.loc.gov/mads/rdf/v1#authoritativeLabel",
            "http://www.w3.org/2004/02/skos/core#prefLabel",
        ):
            if k in n and isinstance(n[k], list):
                for el in n[k]:
                    if isinstance(el, dict) and "@value" in el:
                        return str(el["@value"]).strip()
        return None

    def _extract_related(pred_suffix: str) -> list[dict[str, str]]:
        res: dict[str, dict[str, str]] = {}
        if not main_node:
            return []
        for k, v in main_node.items():
            if k.endswith((pred_suffix, pred_suffix.capitalize() + "Authority")) and isinstance(v, list):
                for lk in v:
                    if isinstance(lk, dict) and "@id" in lk:
                        target_id = str(lk["@id"])
                        lbl = _extract_label(nodes_by_id.get(target_id))
                        if lbl:
                            clean_lbl = re.sub(r"\(.*?\)", "", lbl).strip()
                            canon = normalize_atom(clean_lbl.split("--")[0])
                            if canon and canon != canon_tag and canon not in res:
                                res[canon] = {
                                    "title": lbl,
                                    "canonical_tag": canon,
                                    "url": target_id if target_id.startswith("http") else "",
                                }
        return list(res.values())

    uf_set: set[str] = set()
    if main_node:
        for k, v in main_node.items():
            if k.endswith(("altLabel", "variantLabel")) and isinstance(v, list):
                for el in v:
                    if isinstance(el, dict) and "@value" in el:
                        raw_v = str(el["@value"])
                        clean_v = re.sub(r"\(.*?\)", "", raw_v).strip()
                        atom_v = normalize_atom(clean_v)
                        if atom_v and atom_v != canon_tag:
                            uf_set.add(atom_v)

    return {
        "uf": sorted(uf_set),
        "bt": _extract_related("broader"),
        "nt": _extract_related("narrower"),
        "rt": _extract_related("related"),
    }


def fetch_loc_variants(concept_href: str, canon_tag: str) -> list[str]:
    """Fetch see-from (UF) variant labels for an authoritative Library of Congress concept record."""
    return fetch_loc_concept_facets(concept_href, canon_tag)["uf"]


def query_loc(clean_term: str, max_results: int = 25) -> tuple[list[dict[str, Any]], str | None]:
    """Query Library of Congress linked data subject service for authoritative headings."""
    q = urllib.parse.quote(clean_term)
    url = LOC_SEARCH_URL.format(query=q)
    req = urllib.request.Request(url, headers={"User-Agent": "Evelyn-Engine/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        err = f"Library of Congress: {exc}"
        logger.debug("LOC search failed for '%s': %s", clean_term, exc)
        return [], err

    results: list[dict[str, Any]] = []
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
            # Normalize URL to HTTPS to prevent redirect latency
            if concept_url.startswith("http://"):
                concept_url = "https://" + concept_url[7:]
            results.append({
                "title": title,
                "canonical_tag": normalize_atom(title.split("--")[0]),
                "url": concept_url,
                "source": "LOC",
                "marc_tag": "150",
                "match_type": "auth" if clean_term in title.lower() else "alt",
                "matched_via": None,
                "variants": set(),
            })
            if len(results) >= max_results:
                break

    return results, None


def score_authority_result(item: dict[str, Any], query: str, query_atom: str) -> float:
    """Calculate multi-factor authority relevance score prioritizing LCSH, topical subjects, and exact hits."""
    score = 0.0
    title_lower = item["title"].lower()
    tag = item["canonical_tag"]
    source = item.get("source", "")
    marc_tag = str(item.get("marc_tag") or "150")
    matched_via = item.get("matched_via")

    # 1. Authority source weighting (LOC is the gold standard authority for subject headings)
    if "FAST + LOC" in source:
        score += 350.0  # Dual authority confirmation
    elif "LOC" in source:
        score += 250.0  # Authoritative Library of Congress Subject Heading (LCSH)
    else:
        score += 50.0   # OCLC FAST single-source

    # 2. MARC Classification weighting
    if marc_tag == "150":
        score += 150.0  # Topical subject term
    elif marc_tag in ("110", "111"):
        score -= 400.0  # Corporate entity / organization / meeting penalty
    elif marc_tag == "100":
        score -= 250.0  # Personal name penalty

    # 3. Query matching precision
    if tag == query_atom:
        score += 1000.0  # Exact controlled-vocabulary atom hit (#craft)
    elif title_lower == query:
        score += 900.0   # Exact heading title hit ("Craft")
    elif any(v == query_atom for v in item.get("variants", [])):
        score += 850.0   # Exact see-from alias hit (#3d-printing -> Three-dimensional printing)
    elif tag.startswith(f"{query_atom}-") or title_lower.startswith(f"{query} "):
        score += 500.0   # Exact prefix / head match ("Craft festivals", "Craft malls")
    elif re.search(r"\b" + re.escape(query) + r"\b", title_lower):
        score += 300.0   # Word boundary hit inside title ("Art and craft debate")
    elif query in title_lower:
        score += 150.0   # Substring match in title
    else:
        # Title does not contain query - matched via cross-reference or acronym
        if matched_via:
            mv_lower = matched_via.lower()
            if mv_lower == query or normalize_atom(mv_lower) == query_atom:
                score += 80.0
            else:
                score += 20.0
        else:
            score += 10.0

    # 4. Conciseness length penalty (prefer focused canonical terms over sprawling compounds)
    score -= min(100.0, len(item["title"]) * 0.5)

    return score


def query_authority_diagnostic(term: str, max_results: int = 8) -> dict[str, Any]:
    """Search Library of Congress & OCLC FAST with relevance ranking, caching, and diagnostics."""
    clean_term = term.strip().lower()
    if not clean_term:
        return {"results": [], "total": 0, "sources": [], "errors": []}

    query_atom = normalize_atom(clean_term)

    now = time.time()
    cache_key = f"{clean_term}:{max_results}"
    if cache_key in _QUERY_CACHE:
        cached_ts, cached_payload = _QUERY_CACHE[cache_key]
        if now - cached_ts < _CACHE_TTL_SECONDS:
            return cached_payload

    # Run FAST and LOC candidate lookups in parallel with deep candidate pools
    fast_results: list[dict[str, Any]] = []
    loc_results: list[dict[str, Any]] = []
    errors: list[str] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        fast_fut = executor.submit(query_fast, clean_term, max_results=30)
        loc_fut = executor.submit(query_loc, clean_term, max_results=25)

        f_res, f_err = fast_fut.result()
        l_res, l_err = loc_fut.result()

        fast_results = f_res
        if f_err:
            errors.append(f_err)

        loc_results = l_res
        if l_err:
            errors.append(l_err)

    # Merge and deduplicate by canonical_tag
    merged_map: dict[str, dict[str, Any]] = {}
    sources_used = set()

    for r in fast_results + loc_results:
        tag = r["canonical_tag"]
        sources_used.add(r.get("source", "Authority"))
        if tag not in merged_map:
            merged_map[tag] = {
                "title": r["title"],
                "canonical_tag": tag,
                "url": r["url"],
                "source": r.get("source", "Authority"),
                "marc_tag": r.get("marc_tag", "150"),
                "match_type": r.get("match_type", "auth"),
                "matched_via": r.get("matched_via"),
                "variants": set(r.get("variants") or []),
            }
        else:
            existing = merged_map[tag]
            existing["variants"].update(r.get("variants") or [])
            if existing["source"] != r.get("source"):
                existing["source"] = "FAST + LOC"
            if r.get("source") == "LOC" and (not existing.get("url") or "worldcat" in existing.get("url", "")):
                # If LOC concept URL available, prefer LOC authority record or keep dual reference
                existing["url"] = r["url"]
            # If incoming record has a direct title match, prefer direct title and clear matched_via
            if r["title"].lower().startswith(clean_term) or clean_term in r["title"].lower():
                existing["matched_via"] = None
                existing["title"] = r["title"]
            if r.get("marc_tag") == "150":
                existing["marc_tag"] = "150"

    # Sort merged candidates by multi-factor relevance score descending
    ranked_candidates = sorted(
        merged_map.values(),
        key=lambda it: score_authority_result(it, clean_term, query_atom),
        reverse=True,
    )

    top_candidates = ranked_candidates[:max_results]

    # Concurrently fetch 4 thesaurus facets (UF, BT, NT, RT) for top-ranked LOC results
    loc_facet_targets = [
        item
        for item in top_candidates
        if item.get("url", "").startswith(("https://id.loc.gov", "http://id.loc.gov"))
    ]
    if loc_facet_targets:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(loc_facet_targets))) as executor:
            fut_to_item = {
                executor.submit(fetch_loc_concept_facets, item["url"], item["canonical_tag"]): item
                for item in loc_facet_targets
            }
            for fut in concurrent.futures.as_completed(fut_to_item):
                item = fut_to_item[fut]
                with contextlib.suppress(Exception):
                    facets = fut.result()
                    item["variants"].update(facets.get("uf") or [])
                    item["bt"] = facets.get("bt") or []
                    item["nt"] = facets.get("nt") or []
                    item["rt"] = facets.get("rt") or []

    final_results = [
        {
            "title": m["title"],
            "canonical_tag": m["canonical_tag"],
            "url": m["url"],
            "source": m["source"],
            "matched_via": m.get("matched_via"),
            "variants": sorted(m["variants"]),
            "uf": sorted(m["variants"]),
            "bt": m.get("bt") or [],
            "nt": m.get("nt") or [],
            "rt": m.get("rt") or [],
        }
        for m in top_candidates
    ]

    payload = {
        "results": final_results,
        "total": len(final_results),
        "sources": sorted(sources_used),
        "errors": errors,
    }

    # Cache successful or clean-attempt queries
    if len(_QUERY_CACHE) > 256:
        oldest_key = min(_QUERY_CACHE.keys(), key=lambda k: _QUERY_CACHE[k][0])
        del _QUERY_CACHE[oldest_key]
    _QUERY_CACHE[cache_key] = (now, payload)

    return payload


def query_authority(term: str, max_results: int = 8) -> list[dict[str, Any]]:
    """Search institutional authorities (FAST & LOC) for a subject term."""
    return query_authority_diagnostic(term, max_results=max_results)["results"]


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
