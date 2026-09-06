# link_librarian.py
# date created: 2026-09-05 17:42:00
# date modified: 2026-09-06 08:34:50
# tags: #librarian, #links, #wikilinks, #ghost_links, #alias_hygiene, #attachments, #breadcrumbs

"""
link_librarian.py — Vault Link Integrity, Ghost Link Auditing & Alias Hygiene.

Exports:
    audit_document_links()          — Complete audit pass over markdown note links and frontmatter aliases.
    wrap_spurious_code_arrays()     — Wraps un-fenced NumPy arrays, tensors, and float lists in backticks.
    resolve_bare_attachments()      — Expands bare filename attachment links to full relative vault paths.
    prune_redundant_aliases()       — Cleans possessive ('s) and plural (s) aliases; converts doc types to tags.
    inject_parent_breadcrumbs()     — Injects upstream parent index callout into isolated chapter notes.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
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


@dataclass
class StubPayload:
    """Structured container for entity stub synthesis."""

    target_name: str
    source_path: str = ""
    context_excerpt: str = ""
    domain: str = ""
    tags: list[str] = field(default_factory=lambda: ["stub", "concept"])
    min_refs: int = 2
    ref_count: int = 0
    sources: list[str] = field(default_factory=list)
    references: list[dict[str, str]] = field(default_factory=list)
    synthesized_abstract: str = ""
    total_context_chars: int = 0


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

        try:
            with open(fpath, encoding="utf-8", errors="ignore") as f:
                content = f.read()
            _, body = frontmatter_utils.parse_frontmatter(content)
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
) -> str:
    """Synthesize a cohesive multi-reference executive abstract for an entity using local Ollama.

    Args:
        target_name: Entity target name.
        references: List of harvested reference dictionaries.
        domain: Optional domain classification (e.g. 'dnd', 'hardware', 'code').
        use_llm: Optional override for local Ollama LLM synthesis.

    Returns:
        str: Concise 2-3 sentence synthesized abstract.
    """
    if not references:
        return f"Conceptual entity stub for [[{target_name}]]."

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

        prompt = (
            f"The entity \"{target_name}\" is cited across multiple notes in an Obsidian knowledge base:\n\n"
            f"{ref_block}\n\n"
            f"Write a concise, high-density 2-3 sentence executive abstract describing who or what "
            f"\"{target_name}\" is based strictly on the provided context. "
            f"Do NOT include headings, bullet points, introductory phrases, or markdown formatting other than wikilinks. "
            f"State the facts directly."
        )
        try:
            res = ollama_client.query_ollama(
                prompt=prompt,
                system="You are an expert archivist and PKM librarian writing concise, objective entity abstracts.",
                timeout=18,
            )
            clean_res = string_utils.clean_llm_gist(res)
            clean_res = string_utils.strip_thinking_tags(clean_res).strip()
            if clean_res and len(clean_res) >= 30:
                return clean_res
        except (OSError, ValueError, KeyError, TimeoutError) as e:
            logger.debug("Ollama abstract synthesis fallback triggered: %s", e)

    # Fallback compilation if Ollama is disabled, offline, or returns empty
    if len(references) == 1:
        src_stem = os.path.splitext(os.path.basename(references[0].get("source", "")))[0]
        return (
            f"Conceptual entity stub for [[{target_name}]], referenced from [[{src_stem}]]. "
            f"Context: \"{references[0].get('context', '')}\""
        )
    else:
        src_stems = [os.path.splitext(os.path.basename(r.get("source", "")))[0] for r in references]
        src_list = ", ".join(f"[[{s}]]" for s in src_stems[:4])
        if len(src_stems) > 4:
            src_list += f", and {len(src_stems) - 4} other notes"
        return (
            f"Conceptual entity stub for [[{target_name}]], cited across {len(references)} notes in the vault "
            f"(including {src_list})."
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

    abstract = root.findtext("abstract") or ""
    source_path = root.findtext("source_path") or ""
    context = root.findtext("context") or ""
    domain = root.findtext("domain") or ""
    tags_str = root.findtext("tags") or "stub, concept"
    tags = [t.strip() for t in tags_str.split(",") if t.strip()]

    sources = []
    references = []
    sources_el = root.find("sources")
    if sources_el is not None:
        for s_el in sources_el.findall("source"):
            p = s_el.attrib.get("path") or ""
            c = s_el.text or ""
            if p:
                sources.append(p)
                references.append({"source": p, "context": c})

    if not sources and source_path:
        sources = [source_path]
        if context:
            references = [{"source": source_path, "context": context}]

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

    fm_dict = {
        "title": payload.target_name,
        "aliases": [],
        "tags": payload.tags,
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
    for stem in sorted(ref_stems):
        ref_lines.append(f"- [[{stem}]]")
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

    # Reject generic document sections and filenames
    if clean.lower() in EXCLUDED_TARGET_STEMS:
        return False, ""

    # Reject purely punctuation or symbol strings
    if not re.search(r"[a-zA-Z0-9]", clean):
        return False, ""

    return True, clean


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
        r"`(array|tensor)\(`(\[\[[\s\S]*?\]\])`?\)`",
        r"\1(\2)",
        text,
    )
    if c0 > 0:
        changed = True
        text = repaired_text

    # 1. Matches array([[...]]) or tensor([[...]])
    arr_pattern = re.compile(
        r"(?<![`\w])((?:array|tensor)\s*\(\s*\[\[[\s\S]*?\]\](?:,\s*dtype=[\w\d]+)?\s*\))(?![`\w])",
        re.MULTILINE,
    )
    new_text, c1 = arr_pattern.subn(r"`\1`", text)
    if c1 > 0:
        changed = True
        text = new_text

    # 2. Protect newly created code backticks so float_pattern does not match inside them
    masked_text, local_placeholders = string_utils.protect_code_blocks(text)

    # 3. Matches bare numeric/float 2D lists: [[0. , 0.907, 0.093]] or [[-0.5, 1.2]] or [[1.5]]
    float_pattern = re.compile(
        r"(?<![`\w])(\[\[\s*[-+]?\d*\.?\d+(?:_?\d+)*(?:\s*,\s*[-+]?\d*\.?\d+(?:_?\d+)*)*\s*\]\])(?![`\w])",
        re.MULTILINE,
    )
    new_text, c2 = float_pattern.subn(r"`\1`", masked_text)
    if c2 > 0:
        changed = True
        masked_text = new_text

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

        # 2d. Count and track ghost links (with target validation and vault-wide resolution)
        link_matches = re.findall(r"\[\[([^|\]\n#]+)(?:[|#][^\]\n]*)?\]\]", masked_body)
        root = vault_root or getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")
        ghost_count = 0
        ghost_targets = []
        for target in link_matches:
            is_valid, target_clean = is_valid_entity_target(target)
            if not is_valid or "/" in target_clean:
                continue
            # Vault-wide resolution check: verify sibling dir, vault root, and vault_documents DB
            if not target_note_exists(target_clean, source_path=path, vault_root=root):
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
        domain: Optional domain classification for entity.

    Returns:
        dict[str, Any]: Execution status and action summary.
    """
    is_valid, clean_target = is_valid_entity_target(target_name)
    if not is_valid:
        return {"status": "skipped", "reason": "invalid_or_excluded_target"}

    root = vault_root or getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")

    # Vault-wide existence check
    if target_note_exists(clean_target, source_path=source_path, vault_root=root):
        return {"status": "already_exists", "target": clean_target}

    target_relpath = f"{clean_target}.md"
    target_abspath = os.path.join(root, target_relpath)

    # 1. Harvest all references across the vault
    max_harvest = getattr(cfg, "LIBRARIAN_STUB_MAX_HARVEST_REFS", 12)
    harvested_refs = harvest_entity_references(clean_target, vault_root=root, max_refs=max_harvest)

    # If caller provided a specific source and excerpt, ensure it is included
    if source_path:
        caller_rel = source_path.replace("\\", "/")
        if not any(r["source"] == caller_rel for r in harvested_refs):
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
    synthesized_abstract = synthesize_entity_abstract(clean_target, harvested_refs, domain=domain)
    sources = [r["source"] for r in harvested_refs]
    primary_source = source_path or (sources[0] if sources else "Vault")
    primary_context = context_excerpt or (harvested_refs[0]["context"] if harvested_refs else "")

    payload = StubPayload(
        target_name=clean_target,
        source_path=primary_source,
        context_excerpt=primary_context,
        domain=domain,
        tags=["stub", "concept"],
        min_refs=min_refs_val,
        ref_count=ref_count,
        sources=sources,
        references=harvested_refs,
        synthesized_abstract=synthesized_abstract,
        total_context_chars=total_context_chars,
    )
    xml_payload = render_stub_xml(payload)

    auto_create = getattr(cfg, "MASTER_LIBRARIAN_AUTO_STUBS", True)
    if not auto_create:
        from Evelyn.tools import memory_db

        existing_proposals = memory_db.get_pending_proposals(type="ghost_link_stub")
        existing = next((p for p in existing_proposals if p.get("topic") == clean_target), None)
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
        tags="stub,concept",
        aliases="",
    )
    vault_db.update_document_librarian_audit(target_relpath, ghost_count=0, mtime=new_mtime)

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
    }
