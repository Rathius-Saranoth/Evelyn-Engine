#!/usr/bin/env python3
# generate_tag_merge_review.py
# date created: 2026-09-19 00:00:00
# date modified: 2026-09-19 00:00:00
# tags: #taxonomy, #review, #synonyms, #uf

"""Generate the batch review document for UF merges (taxonomy §9 step 5).

Proposals are grouped **by term family**, not by usage impact. Deciding
`health/physical-condition` in isolation and only later meeting `health/physical-symptoms`
produces inconsistent answers, so every decision touching a family — structural and
semantic alike — is presented together.

Embeddings are cached to disk; re-ordering the document costs seconds rather than a full
re-embedding pass over the corpus.

Usage:
    PYTHONPATH=. python scripts/generate_tag_merge_review.py [--threshold 0.92] [--out PATH]
    PYTHONPATH=. python scripts/generate_tag_merge_review.py --refresh-embeddings
"""

import argparse
import os
import sys

import numpy as np

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from Evelyn.tools import chroma_rag, taxonomy_db
from Evelyn.tools.tag_synonym import (
    build_corpus,
    lexical_equivalences,
    resolve_structural_nesting,
)


def _embed_corpus(terms: list[str], cache_path: str, refresh: bool = False) -> np.ndarray:
    """Embed the corpus, reusing a disk cache keyed by term.

    Embedding is the slow part by an order of magnitude, and re-ordering the review
    document should not require repeating it.

    Args:
        terms: Corpus terms, in output order.
        cache_path: Where the cache lives.
        refresh: Ignore any existing cache.

    Returns:
        np.ndarray: L2-normalized embedding matrix aligned to `terms`.
    """
    path = cache_path if os.path.isabs(cache_path) else os.path.join(_ROOT, cache_path)
    cached: dict[str, np.ndarray] = {}
    if not refresh and os.path.exists(path):
        try:
            blob = np.load(path, allow_pickle=True)
            cached = dict(zip(blob["terms"].tolist(), blob["vectors"], strict=False))
            print(f"[REVIEW] embedding cache: {len(cached)} terms", flush=True)
        except (OSError, ValueError, KeyError):
            cached = {}

    missing = [t for t in terms if t not in cached]
    if missing:
        print(f"[REVIEW] embedding {len(missing)} new terms...", flush=True)
        fn = chroma_rag._get_embedding_fn()
        for i in range(0, len(missing), 512):
            chunk = missing[i:i + 512]
            vecs = np.asarray(
                fn([t.replace("/", " ").replace("-", " ") for t in chunk]), dtype=np.float32
            )
            for term, vec in zip(chunk, vecs, strict=False):
                cached[term] = vec
        os.makedirs(os.path.dirname(path), exist_ok=True)
        keys = list(cached)
        np.savez_compressed(path, terms=np.array(keys, dtype=object),
                            vectors=np.vstack([cached[k] for k in keys]))

    emb = np.vstack([cached[t] for t in terms])
    return emb / (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9)


def family_of(tag: str) -> str:
    """Return the family key a term belongs to for review grouping.

    The family is the leading segment before the first '/' or '-'. It deliberately
    ignores hierarchy depth so that 'work/stress' and 'work-stress' — the very pair whose
    structure is under review — land in the same family rather than being separated by
    the thing being decided.

    Args:
        tag: Any term.

    Returns:
        str: Family key.
    """
    head = tag.split("/", 1)[0]
    return head.split("-", 1)[0] or tag


def structural_shape(members: list[str]) -> tuple[str, str, str]:
    """Describe a flat-vs-nested pair as a reusable shape.

    `health/physical-condition` vs `health/physical/condition` differ only in whether the
    leaf is a compound term or another level. The same question recurs across many pairs
    under the same parent, so it is worth answering once.

    Args:
        members: The two (or more) competing forms.

    Returns:
        tuple[str, str, str]: (shape key, compound form, nested form).
    """
    flat = min(members, key=lambda t: t.count("/"))
    nested = max(members, key=lambda t: t.count("/"))
    parent = "/".join(flat.split("/")[:-1])
    leaf_head = flat.split("/")[-1].split("-", 1)[0]
    key = f"{parent}/{leaf_head}" if parent else leaf_head
    return key, flat, nested


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the tag-merge review document")
    ap.add_argument("--threshold", type=float, default=0.92, help="Cosine similarity cut (default 0.92)")
    ap.add_argument("--out", default="scratch/tag_merge_review.md", help="Output path")
    ap.add_argument("--min-canonical-uses", type=int, default=2,
                    help="A term must have at least this many uses to be a preferred term")
    ap.add_argument("--cache", default="scratch/.tag_embeddings.npz",
                    help="Embedding cache path")
    ap.add_argument("--refresh-embeddings", action="store_true",
                    help="Ignore the cache and re-embed the corpus")
    ap.add_argument("--export-decisions", metavar="PATH",
                    help="Also write the resolved alias map as JSON for the migration to apply")
    args = ap.parse_args()

    counts = build_corpus()
    aliases = taxonomy_db.get_aliases()
    lexical = lexical_equivalences(counts)

    # Terms already owned by section 1 are excluded here — listing a pair in both sections
    # invites two different answers to the same question.
    deferred_members = {m for group in lexical["deferred"] for m in group}
    terms = sorted(
        (t for t in counts if t not in aliases and t not in deferred_members),
        key=lambda t: (-counts[t], t),
    )
    emb = _embed_corpus(terms, args.cache, refresh=args.refresh_embeddings)

    # Star-shaped assignment: high-usage terms become canonicals; each variant attaches to
    # exactly one. Never transitive — that chains unrelated terms through intermediates.
    assigned = np.zeros(len(terms), dtype=bool)
    groups: list[tuple[str, list[str]]] = []
    for i, term in enumerate(terms):
        if assigned[i] or counts[term] < args.min_canonical_uses:
            continue
        sims = emb[i] @ emb.T
        members = []
        for j in np.nonzero(sims >= args.threshold)[0]:
            j = int(j)
            if j == i or assigned[j] or counts[terms[j]] > counts[term]:
                continue
            members.append(terms[j])
            assigned[j] = True
        if members:
            assigned[i] = True
            groups.append((term, sorted(members, key=lambda t: -counts[t])))

    groups.sort(key=lambda g: -(counts[g[0]] + sum(counts[m] for m in g[1])))

    # Every decision touching a family is presented together, regardless of type.
    decisions: list[dict] = [
        {
            "kind": "structural",
            "family": family_of(members[0]),
            "key": min(members),
            "members": members,
            "uses": sum(counts[m] for m in members),
        }
        for members in lexical["deferred"]
    ]
    decisions += [
        {
            "kind": "merge",
            "family": family_of(canon),
            "key": canon,
            "canonical": canon,
            "members": members,
            "uses": counts[canon] + sum(counts[m] for m in members),
        }
        for canon, members in groups
    ]

    families: dict[str, list[dict]] = {}
    for d in decisions:
        families.setdefault(d["family"], []).append(d)
    # Families by impact; decisions alphabetical within a family so near-identical
    # terms ('health/physical-condition', 'health/physical-symptoms') sit adjacent.
    ordered = sorted(families.items(), key=lambda kv: -sum(d["uses"] for d in kv[1]))
    for _fam, ds in ordered:
        ds.sort(key=lambda d: d["key"])

    total_merges = sum(len(d["members"]) for d in decisions if d["kind"] == "merge")
    n_struct = sum(1 for d in decisions if d["kind"] == "structural")

    lines = [
        "# Tag Merge Review",
        "",
        "> **How to review:** keep the lines you agree with, delete the ones you do not.",
        "> To choose a different preferred term, edit the `->` target. Deleting a whole",
        "> block rejects every merge in it. Nothing is applied until you say so.",
        "",
        "> **Grouped by family, not by size.** Every decision touching a family appears",
        "> together — including both kinds below — so related terms are decided with each",
        "> other in view rather than hundreds of lines apart.",
        "",
        "Two kinds of block appear:",
        "",
        "- **`⚖ POLICY`** — the same flat-vs-nested question recurring under one parent.",
        "  Tick ONE line to settle every decision it covers at once.",
        "- **`[structural]`** — the same term written flat or nested. Tick exactly ONE line:",
        "  the form you want as canonical. Delete the block to leave both terms alone.",
        "  A block marked *covered by policy* needs no action unless you want an exception.",
        "- **`[merge]`** — variants to retire into a preferred term. Pre-ticked; untick or",
        "  delete any you disagree with.",
        "",
        f"- Similarity threshold: `{args.threshold}`",
        f"- Families: **{len(ordered)}**",
        f"- Structural decisions: **{n_struct}**  |  Proposed merges: **{total_merges}**",
        "",
        "---",
        "",
    ]

    for fam, ds in ordered:
        fam_uses = sum(d["uses"] for d in ds)
        lines.append(f"## `{fam}`  —  {len(ds)} decisions, {fam_uses} uses")
        lines.append("")

        # Where the same flat-vs-nested question recurs under one parent, offer it once.
        shapes: dict[str, list[dict]] = {}
        for d in ds:
            if d["kind"] != "structural":
                continue
            key, flat, nested = structural_shape(d["members"])
            d["_shape"] = key
            shapes.setdefault(key, []).append({"flat": flat, "nested": nested})
        recurring = {k: v for k, v in shapes.items() if len(v) > 1}

        for key, items in sorted(recurring.items(), key=lambda kv: -len(kv[1])):
            lines += [
                f"#### ⚖ POLICY — `{key}/*`  ({len(items)} decisions share this shape)",
                "",
                f"Every pair under `{key}` differs only in whether the leaf is compound or",
                "nested. Tick ONE line to settle all of them; any individual block below that",
                "you tick explicitly overrides this.",
                "",
                f"- [ ] POLICY `{key}/*` — prefer COMPOUND leaf  "
                f"(e.g. `{items[0]['flat']}`)",
                f"- [ ] POLICY `{key}/*` — prefer NESTED level   "
                f"(e.g. `{items[0]['nested']}`)",
                "",
            ]
        for d in ds:
            if d["kind"] == "structural":
                shown = " vs ".join(f"`{m}` ({counts[m]})" for m in d["members"])
                covered = d.get("_shape") in recurring
                suffix = f"   *(covered by policy `{d['_shape']}/*`)*" if covered else ""
                lines.append(f"### [structural] {shown}{suffix}")
                for keep in d["members"]:
                    others = ", ".join(f"`{o}`" for o in d["members"] if o != keep)
                    lines.append(f"- [ ] canonical `{keep}`  <-  {others}")
            else:
                canon = d["canonical"]
                n = len(d["members"])
                lines.append(
                    f"### [merge] `{canon}` ({counts[canon]})  —  "
                    f"{n} variant{'s' if n != 1 else ''}, {d['uses']} total uses"
                )
                lines.extend(
                    f"- [x] `{m}` ({counts[m]})  ->  `{canon}`" for m in d["members"]
                )
            lines.append("")
        lines.append("---")
        lines.append("")

    out_path = args.out if os.path.isabs(args.out) else os.path.join(_ROOT, args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    print(f"[REVIEW] wrote {out_path}")

    if args.export_decisions:
        import json

        # Structural pairs are settled by the sibling test (§6.3.1); semantic pairs are the
        # reviewed merge blocks. Both become UF aliases.
        alias_map: dict[str, str] = {
            variant: canonical
            for canonical, variant in resolve_structural_nesting(lexical["deferred"], counts)
        }
        for canon, members in groups:
            alias_map.update(dict.fromkeys(members, canon))

        # An alias must not point at something that is itself retired.
        for variant, canonical in list(alias_map.items()):
            seen = {variant}
            while canonical in alias_map and canonical not in seen:
                seen.add(canonical)
                canonical = alias_map[canonical]
            alias_map[variant] = canonical
        alias_map = {v: c for v, c in alias_map.items() if v != c}

        dpath = (args.export_decisions if os.path.isabs(args.export_decisions)
                 else os.path.join(_ROOT, args.export_decisions))
        os.makedirs(os.path.dirname(dpath), exist_ok=True)
        with open(dpath, "w", encoding="utf-8") as fh:
            json.dump({"threshold": args.threshold, "alias_map": alias_map}, fh, indent=2)
        print(f"[REVIEW] exported {len(alias_map)} decisions -> {dpath}")
    print(f"[REVIEW] {len(groups)} semantic groups, {sum(len(m) for _c, m in groups)} proposed merges")
    print(f"[REVIEW] {len(lexical['deferred'])} structural decisions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
