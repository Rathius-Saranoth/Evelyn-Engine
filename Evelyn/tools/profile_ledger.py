# profile_ledger.py
# date created: 2026-09-12 09:40:00
# date modified: 2026-09-12 09:44:29
# tags: #profile, #ledger, #facts, #persona, #evolution

"""
profile_ledger.py — Authoritative Profile Factoid Ledger Module.

Manages the two-layer persona state architecture:
1. Authoritative Fact Ledgers (*_facts.md): Discrete, bulleted items categorized
   by canonical section with explicit priority tiers ([Tier 1], [Tier 2], [Tier 3]).
2. Compiled/Synthesized Presentations (*.md): Living persona documents loaded by
   the engine into system prompts, with tier markers completely stripped.

Exports:
    LedgerItem                  — Data structure representing an atomic factoid.
    parse_ledger()              — Parses markdown ledger into frontmatter and sections.
    render_ledger()             — Serializes sections back to *_facts.md ledger format.
    compile_clean_markdown()    — Serializes sections to clean *.md format (tier tags stripped).
    apply_ledger_delta()        — Applies structured added/modified/removed operations.
    prune_ledger_to_budget()    — Deterministically prunes Tier 3/Tier 2 items to fit word budget.
    get_ledger_filename()       — Maps 'User_Profile.md' -> 'User_Profile_facts.md'.
    get_profile_filename()      — Maps 'User_Profile_facts.md' -> 'User_Profile.md'.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class LedgerItem:
    """Represents a single atomic factoid in a profile ledger."""

    section: str
    tier: int  # 1 (Core Invariant), 2 (Active Habit/Context), 3 (Ephemeral)
    label: str
    fact: str

    @property
    def raw_bullet(self) -> str:
        """Returns the formatted ledger bullet with explicit tier marker."""
        tier_tag = f"[Tier {self.tier}]"
        if self.label:
            return f"* {tier_tag} **{self.label}**: {self.fact}"
        return f"* {tier_tag} {self.fact}"

    @property
    def clean_bullet(self) -> str:
        """Returns the clean bullet stripped of all tier markers for active prompts."""
        if self.label:
            return f"* **{self.label}**: {self.fact}"
        return f"* {self.fact}"

    @property
    def word_count(self) -> int:
        """Calculates word count of the fact content and label."""
        return len(f"{self.label} {self.fact}".split())


# Regex to parse ledger bullets with optional [Tier X] and optional **Label**:
_LEDGER_BULLET_REGEX = re.compile(
    r"^\s*[*+-]\s+"
    r"(?:\[Tier\s*([123])\]\s+)?"
    r"(?:\*\*([^*]+)\*\*:\s*)?"
    r"(.*)$",
    re.DOTALL,
)


def get_ledger_filename(profile_filename: str) -> str:
    """Map a profile document filename to its corresponding ledger filename.

    Example:
        'Assistant_Profile.md' -> 'Assistant_Profile_facts.md'
        'User_Profile.md' -> 'User_Profile_facts.md'
        'System_Directives.md' -> 'System_Directives_facts.md'
    """
    if profile_filename.endswith("_facts.md"):
        return profile_filename
    if profile_filename.endswith(".md"):
        base = profile_filename[:-3]
        return f"{base}_facts.md"
    return f"{profile_filename}_facts.md"


def get_profile_filename(ledger_filename: str) -> str:
    """Map a ledger filename to its corresponding profile document filename.

    Example:
        'Assistant_Profile_facts.md' -> 'Assistant_Profile.md'
    """
    if ledger_filename.endswith("_facts.md"):
        base = ledger_filename[:-9]
        return f"{base}.md"
    return ledger_filename


def parse_ledger(content: str) -> tuple[str, dict[str, list[LedgerItem]]]:
    """Parse a markdown ledger file into frontmatter string and section-grouped LedgerItems.

    Args:
        content: Raw markdown text of the ledger file.

    Returns:
        tuple[str, dict[str, list[LedgerItem]]]:
            - frontmatter: Raw frontmatter header block (including '---' delimiters) if present, else ''.
            - sections: Dictionary mapping section header (e.g. '## Identity & Presence')
              to a list of LedgerItem instances.
    """
    frontmatter = ""
    body = content or ""

    if body.startswith("---"):
        end_idx = body.find("\n---", 3)
        if end_idx != -1:
            full_end = body.find("\n", end_idx + 1)
            if full_end != -1:
                frontmatter = body[:full_end].strip()
                body = body[full_end:].strip()
            else:
                frontmatter = body.strip()
                body = ""

    sections: dict[str, list[LedgerItem]] = {}
    current_header = ""
    lines = body.splitlines()

    i = 0
    while i < len(lines):
        line = lines[i]
        header_match = re.match(r"^(#{1,6}\s+.*)", line.strip())
        if header_match:
            current_header = header_match.group(1).strip()
            if current_header not in sections:
                sections[current_header] = []
            i += 1
            continue

        stripped = line.strip()
        if not stripped or not current_header:
            i += 1
            continue

        if stripped.startswith(("-", "*")):
            # Gather multi-line continuation lines for this bullet
            bullet_lines = [stripped]
            i += 1
            while i < len(lines) and lines[i].startswith(("  ", "\t")) and not lines[i].strip().startswith(("-", "*", "#")):
                bullet_lines.append(lines[i].strip())
                i += 1
            full_bullet = " ".join(bullet_lines)

            m = _LEDGER_BULLET_REGEX.match(full_bullet)
            if m:
                tier_str, label, fact = m.groups()
                tier = int(tier_str) if tier_str else 2
                label_clean = label.strip() if label else ""
                fact_clean = fact.strip() if fact else ""
                sections[current_header].append(
                    LedgerItem(
                        section=current_header,
                        tier=tier,
                        label=label_clean,
                        fact=fact_clean,
                    )
                )
            else:
                sections[current_header].append(
                    LedgerItem(
                        section=current_header,
                        tier=2,
                        label="",
                        fact=full_bullet,
                    )
                )
        else:
            i += 1

    return frontmatter, sections


def render_ledger(frontmatter: str, sections: dict[str, list[LedgerItem]]) -> str:
    """Serialize sections and frontmatter back to the authoritative *_facts.md format.

    Args:
        frontmatter: Frontmatter string block.
        sections: Dictionary mapping section headers to lists of LedgerItems.

    Returns:
        str: Formatted markdown ledger content.
    """
    blocks = []
    if frontmatter:
        blocks.append(frontmatter.strip())

    for header, items in sections.items():
        if not header:
            continue
        sec_lines = [header]
        sec_lines.extend(item.raw_bullet for item in items)
        blocks.append("\n".join(sec_lines))

    return "\n\n".join(blocks).strip() + "\n"


def compile_clean_markdown(frontmatter: str, sections: dict[str, list[LedgerItem]]) -> str:
    """Compile sections to clean standard markdown (stripping all [Tier X] tags).

    Used for generating User_Profile.md and System_Directives.md for ingestion into
    system prompts.

    Args:
        frontmatter: Frontmatter string block.
        sections: Dictionary mapping section headers to lists of LedgerItems.

    Returns:
        str: Clean markdown content without tier markers.
    """
    blocks = []
    if frontmatter:
        blocks.append(frontmatter.strip())

    for header, items in sections.items():
        if not header:
            continue
        sec_lines = [header]
        sec_lines.extend(item.clean_bullet for item in items)
        blocks.append("\n".join(sec_lines))

    return "\n\n".join(blocks).strip() + "\n"


def apply_ledger_delta(
    sections: dict[str, list[LedgerItem]],
    delta: dict[str, Any],
) -> tuple[dict[str, list[LedgerItem]], dict[str, list[str]]]:
    """Deterministically apply added, modified, and removed deltas to a ledger.

    Args:
        sections: Current section dictionary mapping header -> list[LedgerItem].
        delta: Dictionary containing:
            - 'added': list[dict] with 'section', 'tier', 'label', 'fact'
            - 'modified': list[dict] with 'section', 'label', 'new_fact', (optional 'tier')
            - 'removed': list[dict] with 'section', 'label'

    Returns:
        tuple[dict[str, list[LedgerItem]], dict[str, list[str]]]:
            - Updated copy of sections dictionary.
            - Changelog dictionary: {'added': [...], 'modified': [...], 'removed': [...]}.
    """
    # Create a deep copy of the sections dictionary
    updated: dict[str, list[LedgerItem]] = {
        h: [LedgerItem(item.section, item.tier, item.label, item.fact) for item in items]
        for h, items in sections.items()
    }

    changelog: dict[str, list[str]] = {"added": [], "modified": [], "removed": []}

    # Normalize section headers for lookup
    def _find_matching_section(sec_query: str) -> str | None:
        q_norm = sec_query.strip().lower()
        for h in updated:
            if h.strip().lower() == q_norm or h.strip().lower().endswith(q_norm.replace("#", "").strip()):
                return h
        return None

    # 1. Apply removals
    for rem in delta.get("removed", []):
        sec_name = rem.get("section", "")
        label = rem.get("label", "").strip()
        target_sec = _find_matching_section(sec_name)
        if not target_sec:
            continue

        before_count = len(updated[target_sec])
        updated[target_sec] = [
            item for item in updated[target_sec]
            if item.label.lower() != label.lower()
        ]
        if len(updated[target_sec]) < before_count:
            changelog["removed"].append(f"[{target_sec}] {label}")

    # 2. Apply modifications
    for mod in delta.get("modified", []):
        sec_name = mod.get("section", "")
        label = mod.get("label", "").strip()
        new_fact = mod.get("new_fact", "").strip()
        new_tier = mod.get("tier")
        target_sec = _find_matching_section(sec_name)
        if not target_sec or not new_fact:
            continue

        for item in updated[target_sec]:
            if item.label.lower() == label.lower():
                item.fact = new_fact
                if new_tier in (1, 2, 3):
                    item.tier = new_tier
                changelog["modified"].append(f"[{target_sec}] {label}")
                break

    # 3. Apply additions
    for add in delta.get("added", []):
        sec_name = add.get("section", "")
        label = add.get("label", "").strip()
        fact = add.get("fact", "").strip()
        tier = add.get("tier", 2)
        if tier not in (1, 2, 3):
            tier = 2
        if not fact:
            continue

        target_sec = _find_matching_section(sec_name)
        if not target_sec:
            target_sec = sec_name if sec_name.startswith("#") else f"## {sec_name}"
            updated[target_sec] = []

        dup = next((it for it in updated[target_sec] if it.label and it.label.lower() == label.lower()), None)
        if dup:
            dup.fact = fact
            dup.tier = tier
            changelog["modified"].append(f"[{target_sec}] {label}")
        else:
            new_item = LedgerItem(section=target_sec, tier=tier, label=label, fact=fact)
            updated[target_sec].append(new_item)
            changelog["added"].append(f"[{target_sec}] {label or fact[:40]}")

    return updated, changelog


def count_ledger_words(sections: dict[str, list[LedgerItem]]) -> int:
    """Calculate total words across all items in a ledger."""
    return sum(item.word_count for items in sections.values() for item in items)


def prune_ledger_to_budget(
    sections: dict[str, list[LedgerItem]],
    target_words: int,
    min_bullets_per_sec: int = 2,
) -> tuple[dict[str, list[LedgerItem]], int]:
    """Deterministically prune lower-priority items to guarantee budget adherence.

    Adheres strictly to the 3-Tier Priority Framework:
      - Tier 3 (Ephemeral Details): Pruned first.
      - Tier 2 (Active Habits/Context): Pruned second if still over budget.
      - Tier 1 (Core Invariants): 100% PROTECTED. Never pruned unless a section
        exceeds minimum constraints.

    Args:
        sections: Section dictionary.
        target_words: Maximum word budget (e.g. 600).
        min_bullets_per_sec: Minimum bullets to preserve per section.

    Returns:
        tuple[dict[str, list[LedgerItem]], int]:
            - Pruned sections dictionary.
            - Total number of pruned items.
    """
    updated: dict[str, list[LedgerItem]] = {
        h: [LedgerItem(item.section, item.tier, item.label, item.fact) for item in items]
        for h, items in sections.items()
    }

    current_words = count_ledger_words(updated)
    if current_words <= target_words:
        return updated, 0

    total_pruned = 0

    for tier_to_prune in (3, 2):
        while current_words > target_words:
            candidates: list[tuple[str, LedgerItem]] = []
            for h, items in updated.items():
                if len(items) > min_bullets_per_sec:
                    candidates.extend((h, item) for item in items if item.tier == tier_to_prune)

            if not candidates:
                break

            chosen_sec, chosen_item = max(
                candidates,
                key=lambda pair: (len(updated[pair[0]]), pair[1].word_count),
            )

            updated[chosen_sec].remove(chosen_item)
            total_pruned += 1
            current_words = count_ledger_words(updated)

    return updated, total_pruned
