#!/usr/bin/env python3
# generate_structure_review.py
# date created: 2026-09-20 00:00:00
# date modified: 2026-09-20 00:00:00
# tags: #taxonomy, #review, #structure

"""Generate the structural review: near-synonym roots and duplicated subtrees (§9 step 6e).

Two shapes that string similarity alone cannot settle, because the right answer depends on
whether a prefix is classification or noise:

1. **Near-synonym roots** — `tech` and `technology` are one domain wearing two names.
   Merging a root rewrites everything beneath it, so these decide the most and come first.

2. **Duplicated subtrees** — the same path exists at root level and again under another
   root. `routine/morning` (100 uses) against `work/routine/morning` (2) is a generated
   wrapper; `sleep/metrics` (1) against `health/sleep/metrics` (6) is the prefix being
   *correct*. Usage is a hint, not the answer, and 75 of these are tied.

Usage:
    PYTHONPATH=. python scripts/generate_structure_review.py --out scratch/structure_review.md
"""

import argparse
import collections
import os
import sys

import numpy as np

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from Evelyn.tools import chroma_rag
from Evelyn.tools.tag_synonym import build_corpus, root_census


def near_synonym_roots(counts, census, threshold: float, cache_path: str) -> list[tuple]:
    """Find root pairs that name the same domain, by embedding similarity."""
    names = sorted(census, key=lambda r: -census[r]["terms"])
    cached: dict[str, np.ndarray] = {}
    path = cache_path if os.path.isabs(cache_path) else os.path.join(_ROOT, cache_path)
    if os.path.exists(path):
        blob = np.load(path, allow_pickle=True)
        cached = dict(zip(blob["terms"].tolist(), blob["vectors"], strict=False))

    missing = [n for n in names if n not in cached]
    if missing:
        fn = chroma_rag._get_embedding_fn()
        for i in range(0, len(missing), 256):
            chunk = missing[i : i + 256]
            vecs = np.asarray(fn([x.replace("-", " ") for x in chunk]), dtype=np.float32)
            for k, v in zip(chunk, vecs, strict=False):
                cached[k] = v

    emb = np.vstack([cached[n] for n in names])
    emb /= np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9
    sims = emb @ emb.T

    pairs = [
        (names[i], names[j], float(sims[i, j]))
        for i in range(len(names))
        for j in range(i + 1, len(names))
        if sims[i, j] >= threshold
    ]
    return sorted(pairs, key=lambda p: -(census[p[0]]["terms"] + census[p[1]]["terms"]))


def duplicated_subtrees(counts) -> list[tuple[str, str]]:
    """Find paths that exist at root level and again nested under another root."""
    terms = [t for t in counts if not t.startswith("CY-")]
    by_suffix: dict[str, list[str]] = collections.defaultdict(list)
    for term in terms:
        if "/" in term:
            by_suffix[term].append(term)

    found: list[tuple[str, str]] = []
    for shallow in terms:
        if "/" not in shallow:
            continue
        for deep in terms:
            if deep != shallow and deep.endswith("/" + shallow):
                found.append((shallow, deep))
                break
    return sorted(found, key=lambda p: -(counts[p[0]] + counts[p[1]]))


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the structural review document")
    ap.add_argument("--out", default="scratch/structure_review.md")
    ap.add_argument("--threshold", type=float, default=0.85)
    ap.add_argument("--cache", default="scratch/.tag_embeddings.npz")
    args = ap.parse_args()

    counts = build_corpus()
    census = root_census(counts)
    roots = near_synonym_roots(counts, census, args.threshold, args.cache)
    subtrees = duplicated_subtrees(counts)

    lines = [
        "# Structural Review",
        "",
        "> **How to review:** tick exactly ONE line per block. Delete a whole block to leave",
        "> both terms as they are. Nothing is applied until you hand this back.",
        "",
        f"- Root synonyms: **{len(roots)}**  |  Duplicated subtrees: **{len(subtrees)}**",
        "",
        "Roots come first deliberately: merging a root rewrites every term beneath it, so",
        "deciding `tech` vs `technology` may resolve subtree pairs further down for free.",
        "",
        "---",
        "",
        "## 1. Root synonyms",
        "",
        "Two names for one domain. The count is how many terms sit under each.",
        "",
    ]
    for a, b, sim in roots:
        ta, tb = census[a]["terms"], census[b]["terms"]
        lines += [
            f"### `{a}` ({ta} terms) vs `{b}` ({tb} terms)  —  similarity {sim:.2f}",
            f"- [ ] canonical `{a}`  <-  absorb `{b}`",
            f"- [ ] canonical `{b}`  <-  absorb `{a}`",
            "- [ ] keep both — these are genuinely different domains",
            "",
        ]

    lines += [
        "---",
        "",
        "## 2. Duplicated subtrees",
        "",
        "The same path exists twice, once at root level and once nested under another root.",
        "Usage is shown, but it is a hint only — sometimes the longer path is the *correct*",
        "classification (`health/sleep/metrics`), sometimes it is a generated wrapper",
        "(`work/routine/morning`).",
        "",
    ]
    by_family: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
    for shallow, deep in subtrees:
        by_family[shallow.split("/", 1)[0]].append((shallow, deep))

    for family, items in sorted(by_family.items(), key=lambda kv: -sum(
        counts[s] + counts[d] for s, d in kv[1]
    )):
        lines.append(f"### family `{family}`  —  {len(items)} pairs")
        lines.append("")
        for shallow, deep in items:
            lines += [
                f"#### `{shallow}` ({counts[shallow]})  vs  `{deep}` ({counts[deep]})",
                f"- [ ] keep `{shallow}`  <-  retire `{deep}`",
                f"- [ ] keep `{deep}`  <-  retire `{shallow}`",
                "- [ ] keep both",
                "",
            ]
        lines.append("---")
        lines.append("")

    out = args.out if os.path.isabs(args.out) else os.path.join(_ROOT, args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    print(f"[STRUCTURE] root synonyms   : {len(roots)}")
    print(f"[STRUCTURE] subtree pairs   : {len(subtrees)}")
    print(f"[STRUCTURE] families        : {len(by_family)}")
    print(f"[STRUCTURE] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
