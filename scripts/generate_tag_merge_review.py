#!/usr/bin/env python3
# generate_tag_merge_review.py
# date created: 2026-09-19 00:00:00
# date modified: 2026-09-19 00:00:00
# tags: #taxonomy, #review, #synonyms, #uf

"""Generate the batch review document for semantic UF merges (taxonomy §9 step 5).

Produces a markdown file of proposed equivalences for human review. Keep the lines you
agree with, delete the ones you do not, edit a canonical if you prefer a different
preferred term. The companion migration applies whatever survives.

Usage:
    PYTHONPATH=. python scripts/generate_tag_merge_review.py [--threshold 0.92] [--out PATH]
"""

import argparse
import os
import sys

import numpy as np

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from Evelyn.tools import chroma_rag, taxonomy_db
from Evelyn.tools.tag_synonym import build_corpus, lexical_equivalences


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the tag-merge review document")
    ap.add_argument("--threshold", type=float, default=0.92, help="Cosine similarity cut (default 0.92)")
    ap.add_argument("--out", default="scratch/tag_merge_review.md", help="Output path")
    ap.add_argument("--min-canonical-uses", type=int, default=2,
                    help="A term must have at least this many uses to be a preferred term")
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
    print(f"[REVIEW] corpus {len(terms)} terms; embedding...", flush=True)

    fn = chroma_rag._get_embedding_fn()
    vecs = []
    for i in range(0, len(terms), 512):
        chunk = [t.replace("/", " ").replace("-", " ") for t in terms[i:i + 512]]
        vecs.append(np.asarray(fn(chunk), dtype=np.float32))
    emb = np.vstack(vecs)
    emb /= (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9)

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

    lines = [
        "# Tag Merge Review",
        "",
        "> **How to review:** keep the lines you agree with, delete the lines you do not.",
        "> To choose a different preferred term, edit the `->` target. Deleting a whole",
        "> section rejects every merge in it. Nothing is applied until you say so.",
        "",
        f"- Similarity threshold: `{args.threshold}`",
        f"- Proposed semantic merges: **{sum(len(m) for _c, m in groups)}** "
        f"across **{len(groups)}** preferred terms",
        f"- Structural decisions deferred from the lexical pass: **{len(lexical['deferred'])}**",
        "",
        "---",
        "",
        "## 1. Structural decisions (flat vs hierarchical)",
        "",
        "Same term, written flat or nested. Usage counts are in parentheses. Keep ONE line per",
        "group — the form you want as canonical — and delete the other.",
        "",
    ]
    for members in sorted(lexical["deferred"], key=lambda ms: -max(counts[m] for m in ms)):
        lines.append(f"### {' vs '.join(f'`{m}` ({counts[m]})' for m in members)}")
        for keep in members:
            others = [m for m in members if m != keep]
            lines.append(f"- [ ] canonical `{keep}`  <-  " + ", ".join(f"`{o}`" for o in others))
        lines.append("")

    lines += ["---", "", "## 2. Semantic merges", "",
              "Ordered by total usage impact. Each line retires the variant into the preferred term.", ""]
    for canon, members in groups:
        total = counts[canon] + sum(counts[m] for m in members)
        lines.append(f"### `{canon}` ({counts[canon]})  —  {len(members)} variants, {total} total uses")
        for m in members:
            lines.append(f"- [x] `{m}` ({counts[m]})  ->  `{canon}`")
        lines.append("")

    out_path = args.out if os.path.isabs(args.out) else os.path.join(_ROOT, args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    print(f"[REVIEW] wrote {out_path}")
    print(f"[REVIEW] {len(groups)} semantic groups, {sum(len(m) for _c, m in groups)} proposed merges")
    print(f"[REVIEW] {len(lexical['deferred'])} structural decisions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
