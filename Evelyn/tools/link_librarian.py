# link_librarian.py
# date created: 2026-09-05 17:42:00
# date modified: 2026-10-01 17:37:00
# tags: #librarian, #links, #wikilinks, #ghost_links, #alias_hygiene, #attachments, #breadcrumbs

"""
link_librarian.py — Vault Link Integrity, Ghost Link Auditing & Alias Hygiene.

Exports:
    audit_document_links()          — Complete audit pass over markdown note links and frontmatter aliases.
    wrap_spurious_code_arrays()     — Wraps un-fenced NumPy arrays, tensors, and float lists in backticks.
    resolve_bare_attachments()      — Expands bare filename attachment links to full relative vault paths.
    prune_redundant_aliases()       — Cleans possessive ('s) and plural (s) aliases; converts doc types to tags.
    inject_parent_breadcrumbs()     — Injects upstream parent index callout into isolated chapter notes.
    tokenize_wikilink()             — Tokenizes [[Target#Heading|Display]] into stem, subpath, and display.
    resolve_canonical_link_target() — Resolves wikilink target to canonical note stem with alias and disambiguation priority.
    canonicalize_document_wikilinks() — Rewrites alias and disambiguation targets to [[CanonicalTarget|OriginalText]].
    condense_redundant_aliases()    — Collapses self-referential aliases [[X|X]] to [[X]].
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import evelyn_config as cfg
from Evelyn.tools import frontmatter_utils, string_utils

logger = logging.getLogger("evelyn.link_librarian")

DOC_TYPE_ALIAS_MAP = {
    "user manual": "user-manual",
    "specification sheet": "spec-sheet",
    "user guide": "user-guide",
    "datasheet": "datasheet",
    "spec sheet": "spec-sheet",
    "user manutial": "user-manual",
}

EXCLUDED_TARGET_STEMS = {
    "introduction",
    "preface",
    "foreword",
    "index",
    "_index",
    "table of contents",
    "features",
    "safety",
    "other",
    "summary",
    "appendix",
    "glossary",
    "faq",
    "troubleshooting",
    "maintenance",
    "specifications",
    "specification sheet",
    "user manual",
    "manual",
    "parts list",
    "accessories list",
    "readme",
    "roadmap",
    "support",
    "agents",
}

# Characters that never occur in legitimate vault note stems but are ubiquitous in
# source code fragments (pandas/NumPy double-subscripts, JS template literals, HTML).
# Verified against every wikilink target in the vault: zero legitimate hits.
CODE_FRAGMENT_CHARS = frozenset('"*<>{}=;`')

# Unfilled template scaffolding, e.g. "- **NPCs Met:** [[NPC Name]]" in session log
# templates. Only NPC/Location/Item Name occur in the vault and no note ends in " Name",
# so the suffix rule also covers future placeholders like [[Character Name]].
PLACEHOLDER_TARGET_RE = re.compile(r"^(?:\w[\w'-]*\s+)?name$")

# Retired documents carry an explicit status marker. Matched as a whole word so
# ordinary disambiguation such as "Oberon (warframe)" is unaffected.
ARCHIVED_TARGET_RE = re.compile(r"\((?:archived|deprecated|obsolete|retired)\)")

# A "[[" preceded by an identifier character, ")" or "]" is a code subscript
# (e.g. iris.data[["petal length (cm)"]]), not a wikilink. Markdown italics use
# "_[[Target]]", so "_" is deliberately excluded from the guard.
WIKILINK_OPEN_GUARD = r"(?<![A-Za-z0-9)\]])"


@dataclass
class StubPayload:
    """Structured container for entity stub synthesis."""

    target_name: str
    source_path: str = ""
    context_excerpt: str = ""
    domain: str = ""
    # A stub carries its class in the `type: [stub]` frontmatter property (taxonomy §3.4).
    # Its tags are empty until a human or librarian indexes it with atomic subject terms.
    type: list[str] = field(default_factory=lambda: ["stub"])
    tags: list[str] = field(default_factory=list)
    min_refs: int = 2
    ref_count: int = 0
    sources: list[str] = field(default_factory=list)
    references: list[dict[str, str]] = field(default_factory=list)
    synthesized_abstract: str = ""
    total_context_chars: int = 0
    synthesis_mode: str = "fallback"


def _is_stub_note(frontmatter: dict[str, Any] | None, path: str | None = None) -> bool:
    """Report whether a parsed note or path represents an entity stub.

    A stub note is identified either by its location in the vault (`Stubs/` directory)
    or by its frontmatter metadata (`type: [stub]` property or legacy `type/stub` tag).

    Args:
        frontmatter: Parsed frontmatter mapping, or None.
        path: Optional relative or absolute filepath.

    Returns:
        bool: True when the note resides in Stubs/ or carries type 'stub'/'type/stub'.
    """
    if path:
        norm = path.replace("\\", "/").strip().lower()
        parts = [p for p in norm.split("/") if p]
        if any(part == "stubs" for part in parts[:-1]) or norm.startswith("stubs/"):
            return True

    if not frontmatter:
        return False
    # 1. Canonical: check type property
    raw_type = frontmatter.get("type") or []
    if isinstance(raw_type, str):
        type_vals = [raw_type.strip().lower()]
    elif isinstance(raw_type, (list, tuple, set)):
        type_vals = [str(x).strip().lower() for x in raw_type]
    else:
        type_vals = []
    if "stub" in type_vals or "type/stub" in type_vals:
        return True

    # 2. Legacy fallback: check tags (only type/stub; bare 'stub' in tags is a subject)
    tags = frontmatter.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in re.split(r"[,\n]", tags)]
    return any(str(t).strip().strip("'\"[]").lower() == "type/stub" for t in tags)


def harvest_entity_references(
    target_name: str,
    vault_root: str | None = None,
    max_refs: int = 12,
) -> list[dict[str, str]]:
    """Scan vault for all notes referencing target_name via wikilink, extracting context.

    Uses ripgrep (case-insensitive for target and aliases) with fallback to disk walk.
    Extracts sentence-bounded excerpts using string_utils.extract_link_context.

    Args:
        target_name: Entity target name.
        vault_root: Optional vault root directory.
        max_refs: Maximum number of referencing documents to harvest.

    Returns:
        list[dict[str, str]]: List of dicts with 'source' (vault relpath) and 'context' (clean excerpt).
    """
    root = vault_root or getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")
    if not os.path.isdir(root):
        return []

    # Regex pattern: [[target]] or [[target|...]] with case insensitivity
    pattern = rf"\[\[{re.escape(target_name)}(\|[^\]\n]*)?\]\]"
    matching_files: list[str] = []

    # 1. Primary: ripgrep
    try:
        cmd = ["rg", "-i", "-l", pattern, root]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode == 0 and res.stdout.strip():
            matching_files = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug("ripgrep search failed, falling back to disk walk: %s", e)
        matching_files = []

    # 2. Fallback: disk walk if rg failed or returned nothing
    if not matching_files:
        p_re = re.compile(rf"\[\[\s*{re.escape(target_name)}(?:\|[^\]\n]*)?\s*\]\]", re.IGNORECASE)
        for dirpath, _, filenames in os.walk(root):
            for fn in filenames:
                if fn.endswith(".md"):
                    fpath = os.path.join(dirpath, fn)
                    try:
                        with open(fpath, encoding="utf-8", errors="ignore") as f:
                            content = f.read(200000)
                        if p_re.search(content):
                            matching_files.append(fpath)
                            if len(matching_files) >= max_refs * 2:
                                break
                    except (OSError, UnicodeDecodeError):
                        continue
            if len(matching_files) >= max_refs * 2:
                break

    from Evelyn.tools.path_utils import to_vault_relpath

    references: list[dict[str, str]] = []
    seen_sources = set()

    for fpath in matching_files:
        try:
            rel_path = Path(fpath).resolve().relative_to(Path(root).resolve()).as_posix()
        except (ValueError, OSError):
            rel_path = to_vault_relpath(fpath)
        if rel_path in seen_sources:
            continue
        # Avoid harvesting a self-referencing stub if it already exists
        if os.path.basename(rel_path).lower() == f"{target_name.lower()}.md":
            continue

        # Fast path check: if path is inside Stubs/, skip reading and parsing
        if _is_stub_note(None, path=rel_path):
            continue

        try:
            with open(fpath, encoding="utf-8", errors="ignore") as f:
                content = f.read()
            fm, body = frontmatter_utils.parse_frontmatter(content)

            # A stub is not a witness. Its own Context & Mentions section is made of
            # excerpts harvested from elsewhere, so quoting it counts one original source
            # twice — and quoting a stub that itself quoted a stub compounds, which is how
            # 307 truncation artefacts propagated through 134 notes (`.257`). Every stub
            # got its evidence from outside the stub tree; that is where to read it.
            if _is_stub_note(fm, path=rel_path):
                continue

            excerpt = string_utils.extract_link_context(body, target_name, window_chars=180)
            if excerpt:
                seen_sources.add(rel_path)
                references.append({"source": rel_path, "context": excerpt, "snippet": excerpt})
                if len(references) >= max_refs:
                    break
        except (OSError, UnicodeDecodeError):
            continue

    return references


def synthesize_entity_abstract(
    target_name: str,
    references: list[dict[str, str]],
    domain: str = "",
    use_llm: bool | None = None,
) -> tuple[str, str]:
    """Synthesize a cohesive multi-reference executive abstract for an entity using local Ollama.

    Args:
        target_name: Entity target name.
        references: List of harvested reference dictionaries.
        domain: Optional domain classification (e.g. 'dnd', 'hardware', 'code').
        use_llm: Optional override for local Ollama LLM synthesis.

    Returns:
        tuple[str, str]: (abstract, synthesis_mode) where mode is 'llm' when Ollama
        produced the text and 'fallback' when it was compiled deterministically.
    """
    if not references:
        return f"Conceptual entity stub for [[{target_name}]].", "fallback"

    # Build reference context block
    ref_lines = []
    for r in references[:8]:
        src_stem = os.path.splitext(os.path.basename(r.get("source", "")))[0]
        ctx_val = r.get("context", "") or r.get("snippet", "")
        ref_lines.append(f"- From [[{src_stem}]]: \"{ctx_val}\"")
    ref_block = "\n".join(ref_lines)

    should_use_llm = use_llm if use_llm is not None else getattr(cfg, "LIBRARIAN_STUB_LLM_SYNTHESIS", True)
    if should_use_llm:
        from Evelyn.tools import ollama_client

        domain_hint = f"Domain / Topic Area: {domain}\n" if domain else ""
        prompt = (
            f"You are an expert knowledge base curator and PKM librarian writing an executive abstract for an Obsidian vault entry titled \"{target_name}\".\n\n"
            f"{domain_hint}"
            f"Referencing notes from the vault citing this entity:\n"
            f"{ref_block}\n\n"
            f"Instructions:\n"
            f"1. Identify who or what \"{target_name}\" actually is. Use your general world knowledge if \"{target_name}\" is an established real-world entity (e.g. musical artist/band, software/service, hardware tool, video game, company, creative work, mythological deity, public figure, or scientific concept).\n"
            f"2. If \"{target_name}\" is a personal, private, or campaign entity (e.g. a tabletop RPG character/location/item, custom AI persona, personal project, or private contact), deduce its true nature and role accurately from the referencing notes.\n"
            f"3. Write a concise, high-density 2-3 sentence executive abstract that:\n"
            f"   - Directly defines the entity's core identity, medium/domain, origin or creator, and primary nature.\n"
            f"   - Contextualizes how it connects to the vault or user's records (e.g. personal gaming preference, project dependency, listening rotation, campaign element, or gift preference).\n"
            f"4. Critical guardrails (DO NOT violate):\n"
            f"   - DO NOT mistake list items (like gift ideas, hobbies, or music tracks) for colleagues, employers, or people.\n"
            f"   - DO NOT claim two distinct entities or titles are synonyms simply because they were listed together with a slash, comma, or conjunction.\n"
            f"   - DO NOT define a major real-world entity solely through a single personal anecdote (e.g. do not say 'Star Wars is a gift idea for X'; state what Star Wars is first, then its vault context).\n"
            f"5. Output format: Output ONLY the 2-3 sentence abstract text with relevant [[wikilinks]]. Do NOT include markdown headings, bullet points, introductory phrases, or markdown callout blocks (> [!ABSTRACT]). State facts directly."
        )
        try:
            # think=False is load-bearing: with reasoning enabled this call measured 26.9s
            # against the former 18s ceiling and could never return, so every stub silently
            # fell back to the deterministic compiler. Disabled it runs in ~2.2s.
            res = ollama_client.query_ollama(
                prompt=prompt,
                system="You are an expert knowledge base curator and PKM librarian writing concise, objective entity abstracts.",
                timeout=getattr(cfg, "LIBRARIAN_STUB_SYNTHESIS_TIMEOUT", 45),
                think=False,
            )
            clean_res = string_utils.clean_llm_gist(res)
            clean_res = string_utils.strip_thinking_tags(clean_res).strip()
            # Strip accidental callout syntax, markdown quotes, or double-prefixing
            clean_res = re.sub(r"^>\s*\[!ABSTRACT\][^\n]*\n?", "", clean_res, flags=re.IGNORECASE)
            clean_res = "\n".join(l.lstrip("> ").strip() for l in clean_res.splitlines() if l.lstrip("> ").strip())
            if clean_res and len(clean_res) >= 30:
                return clean_res, "llm"
            logger.warning(
                "Ollama abstract synthesis returned %d usable chars for %r; using deterministic fallback",
                len(clean_res),
                target_name,
            )
        except (OSError, ValueError, KeyError, TimeoutError) as e:
            logger.warning("Ollama abstract synthesis failed for %r, using fallback: %s", target_name, e)

    # Fallback compilation if Ollama is disabled, offline, or returns empty
    if len(references) == 1:
        src_stem = os.path.splitext(os.path.basename(references[0].get("source", "")))[0]
        return (
            f"Conceptual entity stub for [[{target_name}]], referenced from [[{src_stem}]]. "
            f"Context: \"{references[0].get('context', '')}\"",
            "fallback",
        )
    else:
        src_stems = [os.path.splitext(os.path.basename(r.get("source", "")))[0] for r in references]
        src_list = ", ".join(f"[[{s}]]" for s in src_stems[:4])
        if len(src_stems) > 4:
            src_list += f", and {len(src_stems) - 4} other notes"
        return (
            f"Conceptual entity stub for [[{target_name}]], cited across {len(references)} notes in the vault "
            f"(including {src_list}).",
            "fallback",
        )


def render_stub_xml(payload: StubPayload) -> str:
    """Serialize stub payload into standardized semantic XML envelope.

    Args:
        payload: StubPayload instance.

    Returns:
        str: XML string payload.
    """
    elem = ET.Element(
        "entity_stub",
        {
            "target": payload.target_name,
            "ref_count": str(payload.ref_count),
            "min_refs": str(payload.min_refs),
            "context_chars": str(payload.total_context_chars),
            "synthesis_mode": payload.synthesis_mode or "fallback",
        },
    )

    if payload.synthesized_abstract:
        clean_abstract = " ".join(payload.synthesized_abstract.split()).replace("---", "").strip()
        if clean_abstract:
            abstract_el = ET.SubElement(elem, "abstract")
            abstract_el.text = clean_abstract

    if payload.source_path:
        source_el = ET.SubElement(elem, "source_path")
        source_el.text = payload.source_path

    if payload.context_excerpt:
        ctx_el = ET.SubElement(elem, "context")
        ctx_el.text = " ".join(payload.context_excerpt.split()).replace("---", "").strip()

    if payload.domain:
        dom_el = ET.SubElement(elem, "domain")
        dom_el.text = payload.domain

    tags_el = ET.SubElement(elem, "tags")
    tags_el.text = ", ".join(payload.tags)

    if payload.references:
        sources_el = ET.SubElement(elem, "sources")
        for r in payload.references:
            ref_el = ET.SubElement(sources_el, "source", {"path": r.get("source", "")})
            ref_el.text = r.get("context", "")

    return ET.tostring(elem, encoding="unicode")


def parse_stub_xml(xml_str: str) -> StubPayload:
    """Deserialize semantic XML envelope back into a StubPayload.

    Args:
        xml_str: XML string.

    Returns:
        StubPayload instance.
    """
    root = ET.fromstring(xml_str)
    target = root.attrib.get("target", "")
    ref_count = int(root.attrib.get("ref_count", "0"))
    min_refs = int(root.attrib.get("min_refs", "2"))
    ctx_chars = int(root.attrib.get("context_chars", "0"))
    synthesis_mode = root.attrib.get("synthesis_mode", "") or "fallback"

    abstract = root.findtext("abstract") or ""
    source_path = root.findtext("source_path") or ""
    context = root.findtext("context") or ""
    domain = root.findtext("domain") or ""
    tags_str = root.findtext("tags") or ""
    tags = [
        t.strip() for t in tags_str.split(",")
        if t.strip() and t.strip() not in ("type/stub", "stub") and not t.strip().startswith("type/")
    ]

    sources = []
    references = []
    sources_el = root.find("sources")
    if sources_el is not None:
        for s_el in sources_el.findall("source"):
            p = s_el.attrib.get("path") or ""
            c = s_el.text or ""
            if p:
                sources.append(p)
                references.append({"source": p, "context": c, "snippet": c})

    if not sources and source_path:
        sources = [source_path]
        if context:
            references = [{"source": source_path, "context": context, "snippet": context}]

    if not ctx_chars:
        ctx_chars = sum(len(r.get("context", "")) for r in references) or len(context)

    return StubPayload(
        target_name=target,
        source_path=source_path,
        context_excerpt=context,
        domain=domain,
        tags=tags,
        min_refs=min_refs,
        ref_count=ref_count,
        sources=sources,
        references=references,
        synthesized_abstract=abstract,
        total_context_chars=ctx_chars,
        synthesis_mode=synthesis_mode,
    )


def render_stub_markdown(payload: StubPayload, now_str: str | None = None) -> str:
    """Render a clean Visual PKM stub markdown note from a StubPayload.

    Guarantees:
      - Valid YAML frontmatter rendered via canonical frontmatter_utils.
      - Abstract callout prefixes every single line (including blank lines) with '> ', preventing YAML/body bleed.
      - Includes ## 🧭 Context & Mentions citing all harvested quotes.
      - Includes ## 🔗 References linking to all referencing notes across the vault.

    Args:
        payload: StubPayload instance.
        now_str: Optional timestamp string. Defaults to current local time.

    Returns:
        str: Fully rendered markdown file content.
    """
    from Evelyn.tools.format_librarian import normalize_flow_array

    ts = now_str or time.strftime("%Y-%m-%d %H:%M:%S")

    # When sanitising changed the stem, the original is the name every existing `[[wikilink]]`
    # uses. The alias records that name so the note is still findable by it in search and the
    # quick switcher — it does NOT make those links resolve. Obsidian resolves a wikilink
    # against filenames only, so `[[Nier: Automata]]` stays unresolved against
    # `Nier Automata.md` however the alias reads. Retargeting the links is what fixes them,
    # and that is `retarget_inbound_links`, called by the stub writers.
    from Evelyn.tools.string_utils import is_filename_safe

    aliases = [] if is_filename_safe(payload.target_name) else [payload.target_name]

    fm_dict = {
        "title": payload.target_name,
        "aliases": aliases,
        "type": ["stub"],
        "tags": [
            t for t in payload.tags
            if t not in ("type/stub", "stub") and not t.startswith("type/")
        ],
        "date created": ts,
        "date modified": ts,
    }

    # Use synthesized abstract if present, else fallback
    abstract_text = payload.synthesized_abstract
    if not abstract_text:
        source_stem = (
            os.path.splitext(os.path.basename(payload.source_path))[0]
            if payload.source_path
            else "Vault"
        )
        abstract_text = f"Conceptual entity stub for [[{payload.target_name}]]."
        if payload.source_path:
            abstract_text += f" Linked from [[{source_stem}]]."
        if payload.context_excerpt:
            abstract_text += f'\nContext: "{payload.context_excerpt}"'

    # Strict multi-line prefixing with '> ' on every line
    abstract_lines = ["> [!ABSTRACT]"]
    for line in abstract_text.splitlines():
        clean_l = line.strip()
        if clean_l:
            abstract_lines.append(f"> {clean_l}")
        else:
            abstract_lines.append(">")
    abstract_block = "\n".join(abstract_lines)

    # Compile Context & Mentions section if references available
    mentions_section = ""
    if payload.references:
        m_lines = ["## 🧭 Context & Mentions"]
        for r in payload.references:
            s_stem = os.path.splitext(os.path.basename(r.get("source", "")))[0]
            ctx = r.get("context", "").strip()
            if ctx:
                if not ctx.startswith(("…", "...")):
                    ctx = f"… {ctx}"
                if not ctx.endswith(("…", "...")):
                    ctx = f"{ctx} …"
                m_lines.append(f"- **[[{s_stem}]]**: \"{ctx}\"")
        if len(m_lines) > 1:
            mentions_section = "\n".join(m_lines) + "\n\n"

    # Compile References list (deduplicated stems)
    ref_stems = []
    seen_stems = set()
    all_sources = payload.sources or ([payload.source_path] if payload.source_path else [])
    for src in all_sources:
        stem = os.path.splitext(os.path.basename(src))[0]
        if stem and stem not in seen_stems:
            seen_stems.add(stem)
            ref_stems.append(stem)

    if not ref_stems and payload.source_path:
        ref_stems = [os.path.splitext(os.path.basename(payload.source_path))[0]]

    ref_lines = ["## 🔗 References"]
    ref_lines.extend(f"- [[{stem}]]" for stem in sorted(ref_stems))
    references_block = "\n".join(ref_lines)

    body = (
        f"# 🏛️ {payload.target_name}\n\n"
        f"{abstract_block}\n\n"
        f"{mentions_section}"
        f"{references_block}\n"
    )

    rendered_fm = frontmatter_utils.render_frontmatter(fm_dict)
    fm_lines = []
    for line in rendered_fm.splitlines():
        if line.startswith("tags:"):
            fm_lines.append(f"tags: {normalize_flow_array(fm_dict.get('tags', []))}")
        elif line.startswith("aliases:"):
            fm_lines.append(f"aliases: {normalize_flow_array(fm_dict.get('aliases', []))}")
        elif line.startswith("type:"):
            fm_lines.append(f"type: {normalize_flow_array(fm_dict.get('type', []))}")
        else:
            fm_lines.append(line)

    return f"{'\n'.join(fm_lines)}\n\n{body}"


def is_valid_entity_target(target: str) -> tuple[bool, str]:
    """Validate and sanitize a candidate link target for entity/stub synthesis.

    Strips trailing .md extensions, rejects chapter prefixes (e.g. '01 - '),
    generic document headings, non-printable OCR glyphs, and too short/long strings.

    Args:
        target: Raw target string extracted from [[target]].

    Returns:
        tuple[bool, str]: (is_valid, cleaned_target)
    """
    clean = re.sub(r"\.md$", "", target.strip(), flags=re.IGNORECASE).strip()
    if not clean or len(clean) < 2 or len(clean) > 120:
        return False, ""

    # Reject private use unicode characters (common in PDF OCR icon glitches)
    if any(0xE000 <= ord(c) <= 0xF8FF for c in clean):
        return False, ""

    # Reject chapter/section number prefixes (e.g. "01 - ", "002 - ", "1. ")
    if re.match(r"^\d{1,3}\s*[-–—.]\s*", clean):
        return False, ""

    lower_clean = clean.lower()

    # Reject folder index files, tables of contents, and MOCs (e.g. "_index", "Book_index", "Topic_moc")
    if lower_clean.endswith(("_index", "_moc")) or lower_clean.startswith("_index"):
        return False, ""

    # Reject generic document sections and filenames
    if lower_clean in EXCLUDED_TARGET_STEMS:
        return False, ""

    # Reject purely punctuation or symbol strings
    if not re.search(r"[a-zA-Z0-9]", clean):
        return False, ""

    # Reject embedded source code fragments captured by double-bracket syntax collisions
    # (pandas/NumPy subscripts, JS template literals) rather than authentic wikilinks
    if CODE_FRAGMENT_CHARS & set(clean):
        return False, ""

    # Reject unfilled template scaffolding: "[[NPC Name]]" under "**NPCs Met:**" is a
    # format placeholder, not an entity. No note in the vault ends in " Name".
    if PLACEHOLDER_TARGET_RE.match(lower_clean):
        return False, ""

    # Reject links to retired documents; the note was archived deliberately, so
    # synthesizing a fresh stub for it resurrects what was meant to go away.
    if ARCHIVED_TARGET_RE.search(lower_clean):
        return False, ""

    return True, clean


def stub_dedupe_key(target: str) -> str:
    """Build a comparison key that collapses the duplicate shapes seen in practice.

    A single librarian sweep proposes every ghost target before any stub note exists,
    so intra-batch variants cannot be caught by an existence check. Two shapes slipped
    through and created redundant notes: a case-only difference
    ("Sekulich Coat Of Arms" / "Sekulich Coat of Arms"), which on a case-insensitive
    sync peer becomes a file conflict, and a leading article
    ("Queen's Palace" / "The Queen's Palace").

    Args:
        target: Cleaned link target.

    Returns:
        str: Normalized key for duplicate comparison.
    """
    key = " ".join(target.lower().split())
    key = re.sub(r"^(?:the|a|an)\s+", "", key)
    return key.strip()


def resolves_as_possessive(target: str, vault_root: str | None = None) -> bool:
    """Check whether target is the possessive form of a note that already exists.

    "[[JT's]]" refers to the existing [[JT Delgado]], so it is not a missing entity.
    The check is deliberately narrow: it fires only when the target itself ends in an
    apostrophe-s AND the base resolves. Entity names that merely contain a possessive
    ("The Dragon's Fangs", "Euraylia's Heart", "Brindle's Staff") do not end that way
    and are untouched — all three were approved as legitimate stubs.

    Args:
        target: Cleaned link target.
        vault_root: Optional vault root directory.

    Returns:
        bool: True when this is a possessive reference to an existing note.
    """
    stripped = target.rstrip()
    for suffix in ("'s", "\u2019s"):
        if stripped.lower().endswith(suffix):
            base = stripped[: -len(suffix)].strip()
            if len(base) < 2:
                return False
            resolved, _ = resolve_canonical_link_target(base, vault_root=vault_root)
            return bool(resolved) or target_note_exists(base, vault_root=vault_root)
    return False


def target_note_exists(
    target_stem: str,
    source_path: str = "",
    vault_root: str | None = None,
) -> bool:
    """Verify whether a target note exists anywhere in the vault.

    Checks:
      1. Sibling directory relative to source note.
      2. Vault root.
      3. Vault database query across all document paths and aliases.

    Args:
        target_stem: Clean stem name of the note (without .md).
        source_path: Optional relative path of referencing note.
        vault_root: Optional vault root directory.

    Returns:
        bool: True if note exists in vault, False otherwise.
    """
    root = vault_root or getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")

    # 1. Sibling check
    if source_path and "/" in source_path:
        source_dir = os.path.dirname(source_path)
        if os.path.exists(os.path.join(root, source_dir, f"{target_stem}.md")):
            return True

    # 2. Direct root check
    if os.path.exists(os.path.join(root, f"{target_stem}.md")):
        return True

    # 3. Vault database check across subdirectories
    try:
        from Evelyn.tools import vault_db

        con = vault_db.get_db()
        cursor = con.cursor()
        query = """
            SELECT 1 FROM vault_documents
            WHERE path = ? OR path LIKE ? OR aliases LIKE ?
            LIMIT 1
        """
        exact_rel = f"{target_stem}.md"
        suffix_pat = f"%/{target_stem}.md"
        alias_pat = f"%{target_stem}%"
        row = cursor.execute(query, (exact_rel, suffix_pat, alias_pat)).fetchone()
        con.close()
        if row is not None:
            return True
    except (sqlite3.Error, OSError) as e:
        logger.debug(f"target_note_exists DB lookup fallback: {e}")

    return False


def tokenize_wikilink(inner: str) -> tuple[str, str, str]:
    """Tokenize the inner contents of a wikilink [[inner]] into (stem, subpath, display).

    Delimiters handled:
      'Target' -> ('Target', '', '')
      'Target|Display' -> ('Target', '', 'Display')
      'Target#Heading' -> ('Target', '#Heading', '')
      'Target#Heading|Display' -> ('Target', '#Heading', 'Display')
      'Target#^blockid|Display' -> ('Target', '#^blockid', 'Display')

    Args:
        inner: Content between [[ and ]].

    Returns:
        tuple[str, str, str]: (stem, subpath, display)
    """
    display = ""
    if "|" in inner:
        target_part, display = inner.split("|", 1)
    else:
        target_part = inner

    subpath = ""
    if "#" in target_part:
        stem, sub = target_part.split("#", 1)
        subpath = f"#{sub.strip()}"
    else:
        stem = target_part

    return stem.strip(), subpath, display.strip()


LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+")


def _strip_leading_article(normalized: str) -> str:
    """Drop a leading English article from an already-lowercased, space-normalized stem.

    Args:
        normalized: Lowercased stem with whitespace collapsed.

    Returns:
        str: The stem without its leading article, or the stem unchanged.
    """
    return LEADING_ARTICLE_RE.sub("", normalized).strip()


def resolve_canonical_link_target(
    target_stem: str,
    vault_root: str | None = None,
) -> tuple[bool, str]:
    """Resolve a wikilink target stem to its canonical note stem in the vault.

    Strict Resolution Hierarchy (Anti-Collision & Disambiguation Aware):
      1. Direct Stem Match: target.md exists on disk or DB -> (True, target)
      2. Exact Alias Match: target matches an alias in vault_documents -> (True, canonical_stem)
      3. Unambiguous Disambiguation Stem: Exactly ONE document matches 'target (*)' -> (True, matched_stem)
         (If > 1 matches, bails out and returns (False, target) to prevent namespace collisions)
      4. Hyphen/Underscore Normalization: Matches a note with hyphens/underscores substituted -> (True, matched_stem)
      5. Leading Article Normalization: "[[The X]]" resolves to note "X" and "[[X]]" to note
         "The X". Writers are inconsistent about the article, and treating the two as
         separate entities produced duplicate stub notes. Requires exactly ONE match, so
         if both "X" and "The X" exist as real notes they stay distinct.
      6. Fallback: No resolution -> (False, target)

    Args:
        target_stem: Clean stem name of the note.
        vault_root: Optional vault root directory.

    Returns:
        tuple[bool, str]: (resolved_bool, canonical_stem)
    """
    clean_stem = target_stem.strip()
    if not clean_stem:
        return False, clean_stem

    stem_lower = clean_stem.lower()
    root = vault_root or getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")

    # 1. Direct Stem Match (Disk check)
    if os.path.exists(os.path.join(root, f"{clean_stem}.md")):
        return True, clean_stem

    try:
        from Evelyn.tools import vault_db

        con = vault_db.get_db()
        cursor = con.cursor()

        # Direct DB Match
        query_direct = """
            SELECT path FROM vault_documents
            WHERE path = ? OR path LIKE ?
            LIMIT 1
        """
        row = cursor.execute(query_direct, (f"{clean_stem}.md", f"%/{clean_stem}.md")).fetchone()
        if row is not None:
            actual_stem = Path(row[0]).stem
            con.close()
            return True, actual_stem

        # 2. Exact Alias Match
        query_alias = """
            SELECT path, aliases FROM vault_documents
            WHERE aliases LIKE ?
        """
        alias_rows = cursor.execute(query_alias, (f"%{clean_stem}%",)).fetchall()
        alias_candidates = []
        for r_path, r_aliases in alias_rows:
            if r_aliases:
                alias_list = [a.strip().lower() for a in r_aliases.split(",") if a.strip()]
                if stem_lower in alias_list:
                    cand_stem = Path(r_path).stem
                    if cand_stem not in alias_candidates:
                        alias_candidates.append(cand_stem)

        if len(alias_candidates) == 1:
            con.close()
            return True, alias_candidates[0]
        elif len(alias_candidates) > 1:
            logger.warning(
                f"resolve_canonical_link_target: alias collision for '{clean_stem}' "
                f"across multiple notes: {alias_candidates}. Bailing out."
            )
            con.close()
            return False, clean_stem

        # 3. Unambiguous Disambiguation Stem: target matches 'target (*)'
        query_disambig = """
            SELECT path FROM vault_documents
            WHERE path LIKE ? OR path LIKE ?
        """
        disambig_rows = cursor.execute(query_disambig, (f"{clean_stem} (%).md", f"%/{clean_stem} (%).md")).fetchall()
        disambig_matches = []
        for (d_path,) in disambig_rows:
            d_stem = Path(d_path).stem
            m = re.match(r"^(.+?)\s*\([^)]+\)$", d_stem)
            if m and m.group(1).strip().lower() == stem_lower and d_stem not in disambig_matches:
                disambig_matches.append(d_stem)

        if len(disambig_matches) == 1:
            con.close()
            return True, disambig_matches[0]
        elif len(disambig_matches) > 1:
            logger.warning(
                f"resolve_canonical_link_target: ambiguous parenthetical match for '{clean_stem}' "
                f"found multiple candidates: {disambig_matches}. Bailing out."
            )
            con.close()
            return False, clean_stem

        # 4. Hyphen/Underscore Normalization
        normalized_stem = re.sub(r"[-_\s]+", " ", stem_lower).strip()
        query_all = "SELECT path FROM vault_documents"
        all_rows = cursor.execute(query_all).fetchall()
        con.close()

        norm_matches = []
        for (a_path,) in all_rows:
            a_stem = Path(a_path).stem
            a_norm = re.sub(r"[-_\s]+", " ", a_stem.lower()).strip()
            if a_norm == normalized_stem and a_stem.lower() != stem_lower and a_stem not in norm_matches:
                norm_matches.append(a_stem)

        if len(norm_matches) == 1:
            return True, norm_matches[0]

        # 5. Leading Article Normalization (reuses all_rows; no extra query)
        article_stem = _strip_leading_article(normalized_stem)
        if article_stem:
            article_matches = []
            for (a_path,) in all_rows:
                a_stem = Path(a_path).stem
                a_norm = re.sub(r"[-_\s]+", " ", a_stem.lower()).strip()
                if a_norm == normalized_stem:
                    continue
                if _strip_leading_article(a_norm) == article_stem and a_stem not in article_matches:
                    article_matches.append(a_stem)

            if len(article_matches) == 1:
                return True, article_matches[0]
            if len(article_matches) > 1:
                logger.warning(
                    f"resolve_canonical_link_target: article-variant collision for "
                    f"'{clean_stem}': {article_matches}. Bailing out."
                )
                return False, clean_stem

    except (sqlite3.Error, OSError) as e:
        logger.debug(f"resolve_canonical_link_target DB lookup fallback: {e}")

    return False, clean_stem


def condense_redundant_aliases(body: str) -> tuple[bool, int, str]:
    """Collapse wikilinks whose alias repeats the target exactly: [[X|X]] -> [[X]].

    An alias that matches its own target carries no information and renders identically,
    so the pipe is pure noise. Handles the table-escaped form ([[X\\|X]]) as well, which
    condenses safely because the result contains no pipe to escape.

    Deliberately left alone:
      - Case-only differences ([[Music|music]]), where the display casing is intentional.
      - Subpath or block targets ([[Note#Section|Note]]), where the display genuinely
        differs from the full target.

    Embeds are condensed on the same terms, keeping their "!" prefix: an image spec such
    as ![[photo.png|300]] has a differing display and is untouched, while a self-referential
    ![[Note|Note]] is just as redundant as the inline form.

    Args:
        body: Text (already masked by protect_code_blocks).

    Returns:
        tuple[bool, int, str]: (changed, count_condensed, updated_body)
    """
    pattern = re.compile(
        WIKILINK_OPEN_GUARD + r"\[\[([^\[\]\n|#^]+?)\\?\|([^\[\]\n|]*?)\]\]"
    )
    count = 0

    def _replace(match: re.Match) -> str:
        nonlocal count
        target = match.group(1).strip()
        display = match.group(2).strip()
        if not target or target != display:
            return match.group(0)
        count += 1
        return f"[[{target}]]"

    updated = pattern.sub(_replace, body)
    return count > 0, count, updated


def canonicalize_document_wikilinks(
    body: str,
    vault_root: str | None = None,
) -> tuple[bool, str, list[str]]:
    """Scan and canonicalize wikilinks in markdown text where alias or disambiguation stems are used as targets.

    Preserves exact reading-mode text:
      [[Target]] -> [[CanonicalTarget|Target]]
      [[Target|Display]] -> [[CanonicalTarget|Display]]
      [[Target#Heading|Display]] -> [[CanonicalTarget#Heading|Display]]
      [[Target#Heading]] -> [[CanonicalTarget#Heading|Target]]

    Args:
        body: Text (already masked by protect_code_blocks).
        vault_root: Optional vault root directory.

    Returns:
        tuple[bool, str, list[str]]: (changed, updated_body, actions)
    """
    changed = False
    actions = []

    pattern = re.compile(WIKILINK_OPEN_GUARD + r"\[\[([^|\]\n#^]+)(#[^|\]\n]+)?(\|[^\]\n]+)?\]\]")

    def replacer(match: re.Match) -> str:
        nonlocal changed
        raw_full = match.group(0)
        target_stem = match.group(1).strip()
        subpath = match.group(2) or ""
        display = match.group(3) or ""

        is_valid, clean = is_valid_entity_target(target_stem)
        if not is_valid or "/" in clean:
            return raw_full

        resolved, canonical_stem = resolve_canonical_link_target(clean, vault_root=vault_root)
        if resolved and canonical_stem != clean:
            changed = True
            action_label = f"canonicalized_link:{clean}->{canonical_stem}"
            if action_label not in actions:
                actions.append(action_label)

            if display:
                return f"[[{canonical_stem}{subpath}{display}]]"
            else:
                return f"[[{canonical_stem}{subpath}|{clean}]]"

        return raw_full

    updated_body = pattern.sub(replacer, body)
    return changed, updated_body, actions


def retarget_inbound_links(
    written_target: str,
    canonical_stem: str,
    vault_root: str | None = None,
    sources: list[str] | None = None,
) -> tuple[int, list[str]]:
    """Point every `[[written_target]]` in the vault at `canonical_stem` instead.

    Called when a note is filed under a stem that differs from the name the links use,
    which happens whenever the target is not filename-safe: `[[Nier: Automata]]` has to
    be stored as `Nier Automata.md`. Obsidian resolves links by filename, so until the
    links themselves are rewritten they stay unresolved and the new note is an orphan
    nothing points at. A frontmatter alias does not cover this (see
    :func:`string_utils.is_filename_safe`).

    The reader's words are preserved in every case:
        [[Nier: Automata]]            -> [[Nier Automata|Nier: Automata]]
        [[Nier: Automata|the game]]   -> [[Nier Automata|the game]]
        [[Nier: Automata#Combat]]     -> [[Nier Automata#Combat|Nier: Automata]]

    Matching is case-insensitive on the target, so `[[NieR: Automata]]` is retargeted
    too and keeps its own spelling as the display text.

    Args:
        written_target: Target exactly as the links spell it.
        canonical_stem: Stem of the note that now exists on disk.
        vault_root: Optional vault root directory.
        sources: Optional vault-relative paths to limit the rewrite to. When omitted,
            the referencing notes are harvested from the vault.

    Returns:
        tuple[int, list[str]]: (files_rewritten, action labels).
    """
    root = vault_root or getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")
    actions: list[str] = []
    if not written_target or not canonical_stem or written_target == canonical_stem:
        return 0, actions

    if sources is None:
        sources = [
            r["source"]
            for r in harvest_entity_references(
                written_target,
                vault_root=root,
                max_refs=getattr(cfg, "LIBRARIAN_STUB_MAX_HARVEST_REFS", 12),
            )
        ]

    pattern = re.compile(
        WIKILINK_OPEN_GUARD
        + r"\[\["
        + re.escape(written_target)
        + r"(#[^|\]\n]+)?(\|[^\]\n]+)?\]\]",
        re.IGNORECASE,
    )

    def replacer(match: re.Match) -> str:
        subpath = match.group(1) or ""
        display = match.group(2) or ""
        if display:
            return f"[[{canonical_stem}{subpath}{display}]]"
        # Recover the spelling this document actually used, so casing survives.
        as_written = match.group(0).split("[[", 1)[1].split("]]")[0].split("#")[0]
        return f"[[{canonical_stem}{subpath}|{as_written}]]"

    rewritten = 0
    for rel in sources:
        abspath = os.path.join(root, rel)
        if not os.path.isfile(abspath):
            continue
        try:
            with open(abspath, encoding="utf-8") as f:
                raw = f.read()
        except OSError as e:
            logger.debug(f"retarget_inbound_links could not read {rel}: {e}")
            continue

        masked, placeholders = string_utils.protect_code_blocks(raw)
        updated, hits = pattern.subn(replacer, masked)
        if not hits:
            continue
        restored = string_utils.restore_code_blocks(updated, placeholders)
        if restored == raw:
            continue

        tmp_path = f"{abspath}.tmp_{os.getpid()}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(restored)
            os.replace(tmp_path, abspath)
        except OSError as e:
            logger.warning(f"[LINK LIBRARIAN] Could not retarget links in {rel}: {e}")
            continue
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        rewritten += 1
        actions.append(f"retargeted_links:{rel}:{hits}")

    if rewritten:
        logger.info(
            "[LINK LIBRARIAN] '%s' is not filename-safe; retargeted %d link(s) across "
            "%d note(s) to [[%s]].",
            written_target, sum(int(a.rsplit(":", 1)[1]) for a in actions), rewritten, canonical_stem,
        )
    return rewritten, actions


def find_canonical_note_path(
    target_name: str,
    vault_root: str | None = None,
) -> tuple[str | None, str | None]:
    """Locate the vault-relative path and canonical stem of an existing note.

    Handles direct filenames, note titles, stems, and aliases across both
    the database index (vault_documents) and the filesystem.

    Args:
        target_name: Note title, stem, or relative path (e.g. 'Evelyn Engine').
        vault_root: Optional vault root directory.

    Returns:
        tuple[str | None, str | None]: (vault_relpath, canonical_stem), or (None, None).
    """
    root = vault_root or getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")
    clean = target_name.strip()
    if clean.endswith(".md"):
        clean = clean[:-3]
    clean_base = os.path.basename(clean)

    # 1. Direct path check on disk
    direct_rel = f"{clean}.md"
    if os.path.isfile(os.path.join(root, direct_rel)):
        return direct_rel, clean_base

    # 2. Database lookup (vault_documents)
    try:
        from Evelyn.tools import vault_db

        con = vault_db.get_db()
        cursor = con.cursor()
        query = """
            SELECT path, title FROM vault_documents
            WHERE LOWER(title) = LOWER(?)
               OR LOWER(path) = LOWER(?)
               OR LOWER(path) = LOWER(?)
               OR LOWER(path) LIKE LOWER(?)
            ORDER BY (LOWER(title) = LOWER(?)) DESC, length(path) ASC
            LIMIT 1
        """
        row = cursor.execute(
            query,
            (clean_base, f"{clean_base}.md", f"{clean}.md", f"%/{clean_base}.md", clean_base),
        ).fetchone()
        con.close()
        if row and row[0]:
            rel = row[0]
            if os.path.isfile(os.path.join(root, rel)):
                stem = os.path.splitext(os.path.basename(rel))[0]
                return rel, stem
    except (sqlite3.Error, OSError, ValueError, KeyError, AttributeError) as e:
        logger.debug(f"find_canonical_note_path vault_db lookup skipped: {e}")

    # 3. Direct disk search under root (case-insensitive filename walk)
    if os.path.isdir(root):
        target_fn = f"{clean_base.lower()}.md"
        for dirpath, _, filenames in os.walk(root):
            for fn in filenames:
                if fn.lower() == target_fn:
                    full = os.path.join(dirpath, fn)
                    try:
                        rel = Path(full).resolve().relative_to(Path(root).resolve()).as_posix()
                    except (ValueError, OSError):
                        rel = os.path.relpath(full, root).replace("\\", "/")
                    stem = os.path.splitext(fn)[0]
                    return rel, stem

    return None, None


def redirect_ghost_link_to_canonical(
    ghost_target: str,
    canonical_target: str,
    vault_root: str | None = None,
    sources: list[str] | None = None,
    add_frontmatter_alias: bool = True,
) -> tuple[int, list[str]]:
    """Vault-wide redirect of a ghost link target to an existing canonical note.

    1. Resolves and validates that the canonical note exists in the vault.
    2. Rewrites all occurrences of [[ghost_target]] across the vault to point
       to the canonical stem while retaining the author's wording as the display alias:
       [[ghost_target]] -> [[canonical_stem|ghost_target]]
    3. If add_frontmatter_alias is True, ensures ghost_target is recorded in the
       canonical note's frontmatter `aliases:` list.
    4. Updates vault_db document audit and mtime metadata for affected notes.

    Args:
        ghost_target: The ghost link concept being redirected (e.g. 'Local AI').
        canonical_target: Title, stem, or path of the target note (e.g. 'Evelyn Engine').
        vault_root: Optional vault root directory.
        sources: Optional list of referencing files to retarget. If None, harvests
            all referencing notes vault-wide.
        add_frontmatter_alias: Whether to append ghost_target to canonical note aliases.

    Returns:
        tuple[int, list[str]]: (files_rewritten, action_logs).

    Raises:
        ValueError: If ghost_target is empty, or canonical note cannot be found.
    """
    root = vault_root or getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")
    actions: list[str] = []

    clean_ghost = ghost_target.strip()
    if not clean_ghost:
        raise ValueError("ghost_target must not be empty.")

    canon_rel, canon_stem = find_canonical_note_path(canonical_target, vault_root=root)
    if not canon_rel or not canon_stem:
        raise ValueError(f"Target note '{canonical_target}' does not exist in vault.")

    # 1. Retarget inbound links across referencing notes
    if sources is None:
        harvested = harvest_entity_references(
            clean_ghost,
            vault_root=root,
            max_refs=getattr(cfg, "LIBRARIAN_STUB_MAX_HARVEST_REFS", 12) * 50,
        )
        sources = [r["source"] for r in harvested]

    rewritten, retarget_actions = retarget_inbound_links(
        written_target=clean_ghost,
        canonical_stem=canon_stem,
        vault_root=root,
        sources=sources,
    )
    actions.extend(retarget_actions)

    # 2. Add alias to canonical note frontmatter if requested
    if add_frontmatter_alias and canon_rel:
        canon_abs = os.path.join(root, canon_rel)
        if os.path.isfile(canon_abs):
            try:
                with open(canon_abs, encoding="utf-8") as f:
                    content = f.read()

                fm, _ = frontmatter_utils.parse_frontmatter(content)
                existing_aliases_raw = fm.get("aliases")
                existing_aliases: list[str] = []
                if isinstance(existing_aliases_raw, list):
                    existing_aliases = [str(a).strip() for a in existing_aliases_raw if str(a).strip()]
                elif isinstance(existing_aliases_raw, str) and existing_aliases_raw.strip():
                    raw = existing_aliases_raw.strip()
                    if raw.startswith("[") and raw.endswith("]"):
                        raw = raw[1:-1]
                    existing_aliases = [a.strip().strip("'\"#") for a in raw.split(",") if a.strip().strip("'\"#")]

                alias_lower_set = {a.lower() for a in existing_aliases}
                if clean_ghost.lower() not in alias_lower_set and clean_ghost.lower() != canon_stem.lower():
                    updated_aliases = [*existing_aliases, clean_ghost]
                    new_content = frontmatter_utils.update_frontmatter_field(content, "aliases", updated_aliases)
                    tmp_canon = f"{canon_abs}.tmp_{os.getpid()}"
                    with open(tmp_canon, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    os.replace(tmp_canon, canon_abs)
                    actions.append(f"added_alias:{canon_rel}:{clean_ghost}")

                    try:
                        from Evelyn.tools import vault_db

                        vault_db.update_document_librarian_audit(
                            canon_rel,
                            aliases=", ".join(updated_aliases),
                            mtime=os.path.getmtime(canon_abs),
                        )
                    except (sqlite3.Error, OSError, ValueError, KeyError, AttributeError) as e_vdb:
                        logger.debug(f"Could not update vault_db for canonical note {canon_rel}: {e_vdb}")
            except OSError as e_canon:
                logger.warning(f"Could not update frontmatter aliases in {canon_rel}: {e_canon}")

    # 3. Synchronize vault_db audit stats for rewritten files
    for act in retarget_actions:
        parts = act.split(":")
        if len(parts) >= 3 and parts[0] == "retargeted_links":
            rel_source = parts[1]
            src_abs = os.path.join(root, rel_source)
            if os.path.isfile(src_abs):
                try:
                    from Evelyn.tools import vault_db

                    vault_db.update_document_librarian_audit(
                        rel_source,
                        mtime=os.path.getmtime(src_abs),
                    )
                except (sqlite3.Error, OSError, ValueError, KeyError, AttributeError) as e_vdb:
                    logger.debug(f"Could not update vault_db for {rel_source}: {e_vdb}")

    return rewritten, actions


def reattach_split_array_qualifiers(text: str) -> tuple[bool, str]:
    """Re-join a module qualifier that an earlier fence split off from its call.

    A previous wrapping pattern anchored on a bare ``array``/``tensor`` name, so a dotted
    call was fenced from the function name onward and the qualifier was left outside:
    ``torch.`tensor([[1, 2]])``` instead of ```torch.tensor([[1, 2]])```.

    Also strips doubled backticks an earlier pass left around a literal or call, e.g.
    ``torch.`tensor(``[[10.0]]``, dtype=torch.float32)``` .

    This must run on the raw body, before protect_code_blocks masks inline spans — once
    the damaged span is a placeholder the qualifier is no longer adjacent to it.

    Args:
        text: Raw markdown body (NOT masked).

    Returns:
        tuple[bool, str]: (changed, updated_text)
    """
    # Strip doubled backticks fenced around a literal or call by an earlier pass. The
    # literal is left bare so the normal wrapper re-fences it as a single clean span;
    # prose spans such as ``bash pip install ...`` do not match and are untouched.
    text, c_double = re.subn(
        r"``(\s*(?:\[\[[^`\n]*?\]\]|(?:[A-Za-z_]\w*\.)*(?:array|tensor)\s*\([^`\n]*?\))\s*)``",
        r"\1",
        text,
    )
    repaired, count = re.subn(
        r"((?:[A-Za-z_]\w*\.)+)`((?:array|tensor)\s*\([^`\n]*?\))`",
        r"`\1\2`",
        text,
    )
    return (count + c_double) > 0, repaired


def _is_numeric_literal_body(inner: str) -> bool:
    """Check whether the inside of a [[...]] span is a pure numeric list literal.

    Accepts flat and nested numeric sequences, including Python's ``None`` holes and
    underscore digit separators: "0.5, 1.2", "2, 0.5], [3, 1", "None, [0.8, 0.1]".
    Rejects anything containing letters beyond ``None`` — i.e. every authentic wikilink.

    Args:
        inner: Text between the opening and closing double brackets.

    Returns:
        bool: True when every element is numeric, ``None``, or a nested numeric group.
    """
    stripped = inner.strip()
    if not stripped:
        return False
    # Flatten nested groups; structure was already validated by the depth scanner
    flat = stripped.replace("[", ",").replace("]", ",")
    items = [x.strip() for x in flat.split(",") if x.strip()]
    if not items:
        return False
    for it in items:
        if it == "None":
            continue
        try:
            float(it.replace("_", ""))
        except ValueError:
            return False
    return True


def _wrap_numeric_bracket_literals(text: str, max_span: int = 2000) -> tuple[bool, str]:
    """Wrap un-fenced numeric ``[[...]]`` literals in backticks via a linear depth scan.

    Walks each candidate opening ``[[`` forward with a bracket-depth counter instead of
    using a nested regex quantifier, so runtime stays linear in the length of the text
    regardless of how many brackets a PDF-extracted code line contains.

    Args:
        text: Markdown text (already masked by protect_code_blocks).
        max_span: Maximum literal length to consider, guarding against runaway scans.

    Returns:
        tuple[bool, str]: (changed, updated_text)
    """
    out: list[str] = []
    i = 0
    n = len(text)
    changed = False

    while i < n:
        if text[i] == "[" and i + 1 < n and text[i + 1] == "[":
            # Never re-wrap an already fenced literal, and never split an identifier
            prev = text[i - 1] if i > 0 else ""
            if prev == "`" or prev.isalnum() or prev == "_":
                out.append(text[i])
                i += 1
                continue

            depth = 0
            j = i
            end = -1
            limit = min(n, i + max_span)
            while j < limit:
                ch = text[j]
                if ch == "\n" or ch == "`":
                    break
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        end = j + 1
                        break
                j += 1

            # A closing "]]" requires the span to end on two consecutive brackets
            if end > 0 and end - i >= 4 and text[end - 2] == "]":
                after = text[end] if end < n else ""
                if not (after == "`" or after.isalnum() or after == "_"):
                    span = text[i:end]
                    if _is_numeric_literal_body(span[2:-2]):
                        out.append(f"`{span}`")
                        changed = True
                        i = end
                        continue

            out.append(text[i])
            i += 1
        else:
            out.append(text[i])
            i += 1

    return changed, "".join(out)


def wrap_spurious_code_arrays(text: str) -> tuple[bool, str]:
    """Wrap un-fenced floating-point and numeric arrays in code backticks.

    Matches numpy array outputs, torch tensors, and float lists:
        array([[0.33149648]], dtype=float32) -> `array([[0.33149648]], dtype=float32)`
        tensor([[-31893., ...]]) -> `tensor([[-31893., ...]])`
        [[0., 0., 0., 1., 0.]] -> `[[0., 0., 0., 1., 0.]]`

    Args:
        text: Markdown text (already masked by protect_code_blocks).

    Returns:
        tuple[bool, str]: (changed, updated_text)
    """
    changed = False

    # 0. Clean damaged backtick boundaries from prior ad-hoc runs: `array(`[[...]]`)` -> array([[...]])
    repaired_text, c0 = re.subn(
        r"`(array|tensor)\(`(\[\[[^`\n]*?\]\])`?\)`",
        r"\1(\2)",
        text,
    )
    if c0 > 0:
        changed = True
        text = repaired_text

    # 1. Matches array([[...]]) or tensor([[...]]), including any dotted qualifier so the
    #    fence wraps the whole call rather than splitting np.array into np. + `array(...)`
    arr_pattern = re.compile(
        r"(?<![`\w.])((?:[A-Za-z_]\w*\.)*(?:array|tensor)\s*\(\s*\[\[[^`\n]*?\]\](?:,\s*dtype=[\w\d]+)?\s*\))(?![`\w])",
        re.MULTILINE,
    )
    new_text, c1 = arr_pattern.subn(r"`\1`", text)
    if c1 > 0:
        changed = True
        text = new_text

    # 2. Protect newly created code backticks so float_pattern does not match inside them
    masked_text, local_placeholders = string_utils.protect_code_blocks(text)

    # 3. Wrap bare numeric literals, flat or nested: [[0.907, 0.093]], [[2, 0.5], [3, 1]],
    #    [[0.7, 0.0], [1.0, 0.0]]. Scanned linearly rather than matched with a nested
    #    quantifier, which is what caused the prior catastrophic-backtracking freeze.
    c3, masked_text = _wrap_numeric_bracket_literals(masked_text)
    if c3:
        changed = True

    text = string_utils.restore_code_blocks(masked_text, local_placeholders)
    return changed, text


def resolve_bare_attachments(text: str, vault_root: str | None = None) -> tuple[bool, str, int]:
    """Expand bare filename attachment links to full relative vault paths.

    Example: `[[Federal Tax 2024.pdf]]` -> `[[Attachments/Source Material/Financial/Federal Tax 2024.pdf]]`

    Args:
        text: Markdown text (already masked by protect_code_blocks).
        vault_root: Optional vault root directory.

    Returns:
        tuple[bool, str, int]: (changed, updated_text, resolved_count)
    """
    root = vault_root or getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")
    attachments_dir = os.path.join(root, "Attachments")
    if not os.path.exists(attachments_dir):
        return False, text, 0

    # Build lookup map of bare attachment filenames -> relative vault paths
    attachment_exts = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp3", ".wav"}
    file_map: dict[str, str] = {}
    for dirpath, _, filenames in os.walk(attachments_dir):
        for f in filenames:
            ext = os.path.splitext(f)[1].lower()
            if ext in attachment_exts:
                abs_f = os.path.join(dirpath, f)
                rel_f = os.path.relpath(abs_f, root).replace("\\", "/")
                file_map[f.lower()] = rel_f

    if not file_map:
        return False, text, 0

    resolved_count = 0

    def _replace_att(m: re.Match) -> str:
        nonlocal resolved_count
        target = m.group(1).strip()
        alias_part = m.group(2) or ""

        # Skip if already path-qualified
        if "/" in target:
            return m.group(0)

        target_lower = target.lower()
        if target_lower in file_map:
            resolved_count += 1
            return f"[[{file_map[target_lower]}{alias_part}]]"

        return m.group(0)

    pattern = re.compile(r"\[\[([^|\]\n]+\.(?:pdf|png|jpg|jpeg|gif|webp|mp3|wav))(\|.*?)?\]\]", re.IGNORECASE)
    new_text = pattern.sub(_replace_att, text)
    return resolved_count > 0, new_text, resolved_count


def prune_redundant_aliases(
    aliases: list[str] | str,
    tags: list[str] | str,
    title: str = "",
) -> tuple[bool, list[str], list[str], list[str]]:
    """Prune redundant possessive ('s) and plural (s) aliases; migrate doc types to tags.

    Args:
        aliases: List of aliases or comma-separated string.
        tags: List of tags or comma-separated string.
        title: Document title.

    Returns:
        tuple[bool, list[str], list[str], list[str]]: (changed, clean_aliases, clean_tags, actions)
    """
    if isinstance(aliases, str):
        alias_list = [a.strip() for a in aliases.split(",") if a.strip()]
    else:
        alias_list = list(aliases or [])

    if isinstance(tags, str):
        tag_list = [t.strip().lstrip("#") for t in tags.split(",") if t.strip()]
    else:
        tag_list = list(tags or [])

    changed = False
    actions = []

    # 1. Base names that can be referenced natively by Obsidian link suffixes
    base_names = {title.strip().lower()} if title.strip() else set()
    for a in alias_list:
        base_names.add(a.strip().lower())

    final_aliases: list[str] = []
    for a in alias_list:
        clean_a = a.strip()
        lower_a = clean_a.lower()

        # Check doc-type migration
        if lower_a in DOC_TYPE_ALIAS_MAP:
            target_tag = DOC_TYPE_ALIAS_MAP[lower_a]
            if target_tag not in tag_list:
                tag_list.append(target_tag)
            changed = True
            actions.append(f"migrated_doc_type_alias:{clean_a}->#{target_tag}")
            continue

        # Check possessive 's or ’s pruning
        is_possessive = False
        for sfx in ("'s", "’s"):
            if lower_a.endswith(sfx):
                stem = lower_a[: -len(sfx)].strip()
                if stem in base_names:
                    is_possessive = True
                    break

        if is_possessive:
            changed = True
            actions.append(f"pruned_possessive_alias:{clean_a}")
            continue

        # Check redundant plural s pruning if stem is in base names
        if (
            len(lower_a) > 3
            and lower_a.endswith("s")
            and not lower_a.endswith("ss")
            and lower_a[:-1] in base_names
        ):
            changed = True
            actions.append(f"pruned_plural_alias:{clean_a}")
            continue

        final_aliases.append(clean_a)

    return changed, final_aliases, tag_list, actions


def inject_parent_breadcrumbs(
    body: str,
    path: str,
    vault_root: str | None = None,
) -> tuple[bool, str]:
    """Inject upstream parent index callouts into isolated chapter notes.

    Example: `> [!abstract] [[Book_index|📖 Book]]\n\n`

    Args:
        body: Markdown body of the note.
        path: Relative path of the document.
        vault_root: Vault root directory.

    Returns:
        tuple[bool, str]: (changed, updated_body)
    """
    if not path or "_index" in path.lower():
        return False, body

    dirpath = os.path.dirname(path)
    if not dirpath:
        return False, body

    folder_name = os.path.basename(dirpath)
    index_candidate_stem = f"{folder_name}_index"

    # Check if index candidate already referenced
    if index_candidate_stem.lower() in body.lower():
        return False, body

    # Check if index file exists on disk
    root = vault_root or getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")
    possible_index_path = os.path.join(root, dirpath, f"{index_candidate_stem}.md")
    if not os.path.exists(possible_index_path):
        # Also check generic _index.md
        possible_index_path = os.path.join(root, dirpath, "_index.md")
        if not os.path.exists(possible_index_path):
            return False, body
        index_candidate_stem = "_index"

    breadcrumb = f"> [!abstract] [[{index_candidate_stem}|📖 {folder_name}]]\n\n"
    new_body = breadcrumb + body.lstrip()
    return True, new_body


def audit_document_links(
    content: str,
    path: str = "",
    vault_root: str | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Perform a complete link audit and normalization on a note.

    Args:
        content: Raw markdown text of the note.
        path: Relative path of the note.
        vault_root: Optional vault root directory.

    Returns:
        tuple[bool, str, dict[str, Any]]: (changed, updated_content, details)
    """
    if not content:
        return False, content, {"status": "empty"}

    fm_dict, body = frontmatter_utils.parse_frontmatter(content)
    changed = False
    details: dict[str, Any] = {
        "actions": [],
        "ghost_links_count": 0,
        "resolved_attachments": 0,
    }

    # 1. Alias & Doc-type hygiene
    title = str(fm_dict.get("title", ""))
    aliases = fm_dict.get("aliases", [])
    tags = fm_dict.get("tags", [])
    alias_changed, clean_aliases, clean_tags, alias_actions = prune_redundant_aliases(
        aliases=aliases,
        tags=tags,
        title=title,
    )
    if alias_changed:
        fm_dict["aliases"] = clean_aliases
        fm_dict["tags"] = clean_tags
        changed = True
        details["actions"].extend(alias_actions)

    # 2. Body processing: first repair fractured array backticks from prior unmasked scripts
    body, c_fix = re.subn(
        r"`(array|tensor)\(`\s*(\[\[[\s\S]*?\]\])\s*`\)`",
        r"`\1(\2)`",
        body,
    )
    if c_fix > 0:
        changed = True
        details["actions"].append("repaired_fractured_array_backticks")

    # Still pre-mask: a split qualifier is only adjacent to its span before masking
    c_qual, body = reattach_split_array_qualifiers(body)
    if c_qual:
        changed = True
        details["actions"].append("reattached_split_array_qualifiers")

    # Mask code blocks to protect authentic code
    masked_body, placeholders = string_utils.protect_code_blocks(body)
    try:
        # 2a. Wrap spurious code arrays (NumPy, tensor, float lists)
        c_arr, masked_body = wrap_spurious_code_arrays(masked_body)
        if c_arr:
            changed = True
            details["actions"].append("wrapped_spurious_arrays")

        # 2b. Resolve bare attachments
        c_att, masked_body, att_count = resolve_bare_attachments(masked_body, vault_root=vault_root)
        if c_att:
            changed = True
            details["resolved_attachments"] = att_count
            details["actions"].append(f"resolved_{att_count}_attachments")

        # 2c. Parent breadcrumbs for chapter notes
        c_crumb, masked_body = inject_parent_breadcrumbs(masked_body, path, vault_root=vault_root)
        if c_crumb:
            changed = True
            details["actions"].append("injected_parent_breadcrumb")

        # 2c-bis. Collapse self-referential aliases ([[X|X]]) before canonicalization
        c_cond, n_cond, masked_body = condense_redundant_aliases(masked_body)
        if c_cond:
            changed = True
            details["condensed_aliases"] = n_cond
            details["actions"].append(f"condensed_{n_cond}_redundant_aliases")

        # 2d. Canonicalize wikilinks targeting aliases or disambiguation stems
        c_can, masked_body, can_actions = canonicalize_document_wikilinks(masked_body, vault_root=vault_root)
        if c_can:
            changed = True
            details["actions"].extend(can_actions)

        # 2e. Count and track ghost links (with target validation and vault-wide resolution)
        link_matches = re.findall(
            WIKILINK_OPEN_GUARD + r"\[\[([^|\]\n#]+)(?:[|#][^\]\n]*)?\]\]",
            masked_body,
        )
        root = vault_root or getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")
        ghost_count = 0
        ghost_targets = []
        for target in link_matches:
            is_valid, target_clean = is_valid_entity_target(target)
            if not is_valid or "/" in target_clean:
                continue
            # Vault-wide resolution check: verify sibling dir, vault root, DB, and canonical resolution
            if resolves_as_possessive(target_clean, vault_root=root):
                continue
            res_can, _ = resolve_canonical_link_target(target_clean, vault_root=root)
            if not res_can and not target_note_exists(target_clean, source_path=path, vault_root=root):
                ghost_count += 1
                if target_clean not in ghost_targets:
                    ghost_targets.append(target_clean)
        details["ghost_links_count"] = ghost_count
        details["ghost_targets"] = ghost_targets

    finally:
        restored_body = string_utils.restore_code_blocks(masked_body, placeholders)

    # Re-render frontmatter if changed
    if changed:
        from Evelyn.tools.format_librarian import normalize_flow_array

        rendered_fm = frontmatter_utils.render_frontmatter(fm_dict)
        # Enforce flow array rendering
        lines = rendered_fm.splitlines()
        final_lines = []
        for line in lines:
            if line.startswith("tags:"):
                final_lines.append(f"tags: {normalize_flow_array(fm_dict.get('tags', []))}")
            elif line.startswith("aliases:"):
                final_lines.append(f"aliases: {normalize_flow_array(fm_dict.get('aliases', []))}")
            else:
                final_lines.append(line)
        updated_fm = "\n".join(final_lines)
        updated_content = f"{updated_fm}\n{restored_body}" if restored_body else f"{updated_fm}\n"
    else:
        updated_content = content

    return changed, updated_content, details


def infer_stub_domain(references: list[dict[str, str]]) -> str:
    """Retired. A stub is filed flat, and the reviewer files it.

    Kept as a no-op because the *reason* is worth carrying: this function has now produced
    the same defect twice under two different heuristics. The first voted on co-linked notes
    and filed a country under Contacts at 89% confidence. Its replacement voted on the
    top-level folder of the referencing notes and filed a **holiday** under Contacts, because
    contact notes are where it happened to be mentioned.

    Both were measuring *where a thing is talked about* and reporting it as *what the thing
    is*, which is not a weaker version of the same signal — it is a different question. The
    lesson was already written into this module's docstring after the first attempt: a
    confidently wrong folder costs more to undo than an unsorted stub. It was then re-learned
    against the second.

    Args:
        references: Ignored.

    Returns:
        str: Always "", so stubs land in `Stubs/` for the reviewer to file.
    """
    return ""


def stub_relpath(target_stem: str, domain: str = "") -> str:
    """Build the vault-relative path a stub note should occupy.

    Args:
        target_stem: Clean note stem, without extension.
        domain: Optional domain folder from infer_stub_domain().

    Returns:
        str: Path relative to the vault root, using forward slashes.
    """
    from Evelyn.tools.string_utils import sanitize_filename

    stub_dir = getattr(cfg, "LIBRARIAN_STUB_DIR", "Stubs").strip("/")
    parts = [p for p in (stub_dir, domain.strip("/")) if p]
    # A wikilink target may legally contain characters a filename may not: `[[Nier: Automata]]`
    # produced `Nier: Automata.md`, which Windows cannot represent and Syncthing refuses to
    # sync. The note keeps its real name in `title` and an alias, but that does not make the
    # inbound links resolve — callers must retarget them with `retarget_inbound_links`.
    parts.append(f"{sanitize_filename(target_stem, default='Untitled')}.md")
    return "/".join(parts)


def create_ghost_link_stub(
    target_name: str,
    source_path: str = "",
    context_excerpt: str = "",
    vault_root: str | None = None,
    min_refs: int | None = None,
    min_context_chars: int | None = None,
    min_snippet_chars: int | None = None,
    domain: str = "",
) -> dict[str, Any]:
    """Evaluate and synthesize a Visual PKM stub note for a recurring ghost link.

    Strict Quality & Peer Review Guardrails:
    - Target Sanitization: Rejects invalid, chapter, generic, or OCR noise targets.
    - Vault-Wide Existence: Aborts if note already exists anywhere in vault.
    - Multi-Reference Harvesting: Scans vault for all notes citing target via wikilinks.
    - Dual Quality Threshold:
        1. Must appear in >= min_refs distinct notes (default: 2).
        2. Must have >= min_context_chars combined context (~2-3 substantive sentences, default: 200).
        3. Must have at least one substantive excerpt (>= 60 chars).
      If below threshold, stays an unpromoted ghost link.
    - Context Synthesis: Prompts local Ollama to synthesize a 2-3 sentence executive abstract.
    - Tier 1 vs Tier 2:
        - If MASTER_LIBRARIAN_AUTO_STUBS is True, synthesizes note directly.
        - Otherwise, registers a Tier 2 proposal in the review queue.

    Args:
        target_name: Stem name of the missing link (e.g. 'Voron StealthBurner').
        source_path: Path of the document where the link was found.
        context_excerpt: Surrounding text from the referencing note (without YAML).
        vault_root: Optional vault root directory.
        min_refs: Minimum independent notes citing this link.
        min_context_chars: Minimum combined context character threshold.
        domain: Optional domain folder for filing under Stubs/<domain>/. When
            omitted it is inferred from where the referencing notes live.

    Returns:
        dict[str, Any]: Execution status and action summary.
    """
    is_valid, clean_target = is_valid_entity_target(target_name)
    if not is_valid:
        return {"status": "skipped", "reason": "invalid_or_excluded_target"}

    root = vault_root or getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")

    # Vault-wide existence check
    if resolves_as_possessive(clean_target, vault_root=root):
        return {"status": "skipped", "reason": "possessive_of_existing_note", "target": clean_target}

    res_can, canonical_name = resolve_canonical_link_target(clean_target, vault_root=root)
    if res_can or target_note_exists(clean_target, source_path=source_path, vault_root=root):
        return {"status": "already_exists", "target": canonical_name or clean_target}

    # Placeholder path; the domain is only known after references are harvested below.
    target_relpath = stub_relpath(clean_target)
    target_abspath = os.path.join(root, target_relpath)

    # 1. Harvest all references across the vault
    max_harvest = getattr(cfg, "LIBRARIAN_STUB_MAX_HARVEST_REFS", 12)
    harvested_refs = harvest_entity_references(clean_target, vault_root=root, max_refs=max_harvest)

    # If caller provided a specific source and excerpt, ensure it is included
    caller_is_stub = False
    if source_path:
        caller_rel = source_path.replace("\\", "/")
        caller_is_stub = _is_stub_note(None, path=caller_rel)
        if not caller_is_stub:
            caller_abs = os.path.join(root, caller_rel) if not os.path.isabs(caller_rel) else caller_rel
            if os.path.exists(caller_abs):
                try:
                    with open(caller_abs, encoding="utf-8", errors="ignore") as cf:
                        cfm, _ = frontmatter_utils.parse_frontmatter(cf.read())
                    caller_is_stub = _is_stub_note(cfm, path=caller_rel)
                except OSError:
                    pass

        # A stub is not a witness. Never include a stub note in harvested evidence.
        if not caller_is_stub and not any(r["source"] == caller_rel for r in harvested_refs):
            clean_caller_ctx = context_excerpt.strip()
            if clean_caller_ctx:
                harvested_refs.insert(0, {"source": caller_rel, "context": clean_caller_ctx})

    ref_count = len(harvested_refs)
    total_context_chars = sum(len(r.get("context", "")) for r in harvested_refs)
    min_refs_val = min_refs if min_refs is not None else getattr(cfg, "LIBRARIAN_GHOST_STUB_MIN_REFS", 2)
    min_ctx_val = min_context_chars if min_context_chars is not None else getattr(cfg, "LIBRARIAN_GHOST_STUB_MIN_CONTEXT_CHARS", 200)
    min_snippet_val = min_snippet_chars if min_snippet_chars is not None else getattr(cfg, "LIBRARIAN_GHOST_STUB_MIN_SNIPPET_CHARS", 60)

    has_substantive_snippet = any(len(r.get("context", "")) >= min_snippet_val for r in harvested_refs)

    # 2. Informational Quality Gate: Must have >= min_refs and >= min_context_chars
    if ref_count < min_refs_val or total_context_chars < min_ctx_val or not has_substantive_snippet:
        return {
            "status": "below_threshold",
            "target": clean_target,
            "source": source_path,
            "ref_count": ref_count,
            "min_refs": min_refs_val,
            "total_context_chars": total_context_chars,
            "min_context_chars": min_ctx_val,
            "reason": (
                f"Context threshold not met ({ref_count}/{min_refs_val} refs, "
                f"{total_context_chars}/{min_ctx_val} context chars). Stays ghost link."
            ),
        }

    # 3. Multi-Reference Abstract Synthesis
    # Route by where the referencing notes live, unless the caller named a domain
    if not domain:
        domain = infer_stub_domain(harvested_refs)
    target_relpath = stub_relpath(clean_target, domain)
    target_abspath = os.path.join(root, target_relpath)

    synthesized_abstract, synthesis_mode = synthesize_entity_abstract(
        clean_target, harvested_refs, domain=domain
    )
    sources = [r["source"] for r in harvested_refs]
    if caller_is_stub or not source_path:
        primary_source = sources[0] if sources else "Vault"
        primary_context = harvested_refs[0]["context"] if harvested_refs else ""
    else:
        primary_source = source_path
        primary_context = context_excerpt or (harvested_refs[0]["context"] if harvested_refs else "")

    payload = StubPayload(
        target_name=clean_target,
        source_path=primary_source,
        context_excerpt=primary_context,
        domain=domain,
        tags=[],
        min_refs=min_refs_val,
        ref_count=ref_count,
        sources=sources,
        references=harvested_refs,
        synthesized_abstract=synthesized_abstract,
        total_context_chars=total_context_chars,
        synthesis_mode=synthesis_mode,
    )
    xml_payload = render_stub_xml(payload)

    auto_create = getattr(cfg, "MASTER_LIBRARIAN_AUTO_STUBS", True)
    if not auto_create:
        from Evelyn.tools import memory_db

        existing_proposals = memory_db.get_pending_proposals(type="ghost_link_stub")
        target_key = stub_dedupe_key(clean_target)
        existing = next(
            (p for p in existing_proposals if stub_dedupe_key(str(p.get("topic") or "")) == target_key),
            None,
        )

        # A rejected stub stays rejected until the evidence materially changes (G1, user's call
        # 2026-09-25). Ghost links persist in the vault, so without this the same target returns
        # on the next sweep and the only available action is to reject it again.
        #
        # Deliberately *not* permanent, unlike a rejected vocabulary term. "Not worth a note"
        # is a judgement about how much the vault leans on this target, and that is exactly
        # what changes: `[[Foo]]` cited twice may well deserve a stub at twenty. Re-asking only
        # once the citation count has multiplied means the reviewer is answering a genuinely
        # different question, not the same one again.
        if not existing:
            factor = getattr(cfg, "GHOST_STUB_REEVIDENCE_FACTOR", 2.0)
            for prop in memory_db.get_rejected_proposals("ghost_link_stub"):
                if stub_dedupe_key(str(prop.get("topic") or "")) != target_key:
                    continue
                # No recorded evidence means an old row the backfill could not read; treat it
                # as binding rather than guess a threshold that might be zero.
                was = prop.get("evidence")
                if was is None or ref_count < int(was) * factor:
                    memory_db.record_rejected_request("ghost_link_stub", str(prop.get("topic") or ""))
                    logger.info(
                        "[LINK LIBRARIAN] Stub for '%s' was rejected at %s citation(s); "
                        "now %d, below the %.1f× threshold — not re-proposing.",
                        clean_target, was, ref_count, factor,
                    )
                    return {
                        "status": "skipped",
                        "reason": "rejected_threshold_not_met",
                        "target": clean_target,
                        "ref_count": ref_count,
                    }
                logger.info(
                    "[LINK LIBRARIAN] Stub for '%s' was rejected at %s citation(s) but is now "
                    "cited %d times — re-proposing on the new evidence.",
                    clean_target, was, ref_count,
                )
                break

        if existing:
            proposal_id = existing["id"]
        else:
            proposal_id = memory_db.insert_proposal(
                type="ghost_link_stub",
                source_ids=[],
                topic=clean_target,
                suggested_category=primary_source,
                reason=f"Ghost link [[{clean_target}]] cited in {ref_count} notes ({total_context_chars} context chars).",
                merged_observation=xml_payload,
                confidence="high",
                # The number a later rejection is measured against, stored rather than left to
                # be re-parsed out of `reason`.
                evidence=ref_count,
            )

        return {
            "status": "tier_2_proposal",
            "target": clean_target,
            "source": primary_source,
            "ref_count": ref_count,
            "min_refs": min_refs_val,
            "total_context_chars": total_context_chars,
            "proposal_id": proposal_id,
            "xml_payload": xml_payload,
            "proposal": {
                "id": proposal_id,
                "type": "ghost_link_stub",
                "target": clean_target,
                "source": primary_source,
                "context": synthesized_abstract,
                "xml_payload": xml_payload,
            },
        }

    # Tier 1 Auto-Synthesis via structured payload
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    content = render_stub_markdown(payload, now_str=now_str)

    os.makedirs(os.path.dirname(target_abspath), exist_ok=True)
    tmp_path = f"{target_abspath}.tmp_{os.getpid()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, target_abspath)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    from Evelyn.tools import vault_db

    source_stem = os.path.splitext(os.path.basename(primary_source))[0] if primary_source else "Vault"
    gist_text = f"Entity stub for [[{clean_target}]], cited across {ref_count} notes including [[{source_stem}]]."
    new_mtime = os.path.getmtime(target_abspath)
    vault_db.upsert_document(
        path=target_relpath,
        title=clean_target,
        mtime=new_mtime,
        gist=gist_text,
        rag_priority="normal",
        rag_pinned=False,
        tags="",
        aliases="",
    )
    vault_db.update_document_librarian_audit(target_relpath, ghost_count=0, mtime=new_mtime)

    # The note is filed under a sanitised stem, so every link that named the original spelling
    # now points at a file that does not exist. Rewrite them, or the stub we just wrote is an
    # orphan and the links stay ghosts forever — a note can never be created under that name.
    written_stem = os.path.splitext(os.path.basename(target_relpath))[0]
    links_retargeted, retarget_actions = retarget_inbound_links(
        clean_target, written_stem, vault_root=root, sources=sources,
    )

    import sys
    subprocess.run(
        [sys.executable, "scripts/update_frontmatter.py", target_abspath],
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        capture_output=True,
    )

    return {
        "status": "created_stub",
        "path": target_relpath,
        "ref_count": ref_count,
        "total_context_chars": total_context_chars,
        "tier": 1,
        "xml_payload": xml_payload,
        "links_retargeted": links_retargeted,
        "retarget_actions": retarget_actions,
    }
