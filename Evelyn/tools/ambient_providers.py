# ambient_providers.py
# date created: 2026-09-02 21:28:00
# date modified: 2026-09-05 17:38:20
# tags: #ambient, #thought-bubbles, #providers, #registry, #multi-modal

"""
ambient_providers.py — Pluggable Activity Providers for Diurnal Ambient Reflections.

Defines the BaseAmbientProvider protocol and built-in activity providers:
  - RecentChatProvider: Grounded in today's conversation turns.
  - VaultDocumentProvider: Reminisces on notes & documents in evelyn_vault.db.
  - LoreSnippetProvider: Explores companion, worldbuilding, or lore markdown notes.
  - TopicCuriosityProvider: Samples intellectual curiosities from configured topic pools.
  - SensoryWanderProvider: Open-ended situational & sensory daytime musings.
"""

from __future__ import annotations

import logging
import os
import random
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import evelyn_config as cfg
from Evelyn.tools import frontmatter_utils, path_utils

logger = logging.getLogger(__name__)


class BaseAmbientProvider(ABC):
    """Abstract base provider for ambient reflection activity generators."""

    @abstractmethod
    def fetch_seed_context(
        self,
        activity_cfg: dict[str, Any],
        now_dt: datetime,
    ) -> tuple[str, str, str]:
        """Generate seed context for the LLM thought prompt.

        Args:
            activity_cfg: Activity configuration dictionary from AMBIENT_ACTIVITIES.
            now_dt: Current local datetime.

        Returns:
            tuple[str, str, str]: (seed_context_xml, source_ref, default_mood)
        """
        raise NotImplementedError


class RecentChatProvider(BaseAmbientProvider):
    """Reflects on recent conversation turns or morning exchanges."""

    def fetch_seed_context(
        self,
        activity_cfg: dict[str, Any],
        now_dt: datetime,
    ) -> tuple[str, str, str]:
        chat_db_path = getattr(cfg, "CHAT_DB_PATH", os.path.join(getattr(cfg, "DATA_DIR", ""), "evelyn_chat.db"))
        recent_turns: list[sqlite3.Row] = []

        if os.path.exists(chat_db_path):
            try:
                con = sqlite3.connect(chat_db_path)
                con.row_factory = sqlite3.Row
                day_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

                rows = con.execute(
                    """SELECT role, content FROM messages
                       WHERE ts >= ? AND role IN ('user', 'assistant')
                         AND content != '[THREAD_BREAK]'
                         AND content NOT LIKE '[PLACEHOLDER]%'
                       ORDER BY id DESC LIMIT 15""",
                    (day_start,),
                ).fetchall()

                if not rows:
                    rows = con.execute(
                        """SELECT role, content FROM messages
                           WHERE role IN ('user', 'assistant')
                             AND content != '[THREAD_BREAK]'
                             AND content NOT LIKE '[PLACEHOLDER]%'
                           ORDER BY id DESC LIMIT 10""",
                    ).fetchall()

                con.close()
                recent_turns = list(reversed(rows)) if rows else []
            except sqlite3.Error as e:
                logger.warning(f"[AMBIENT-PROVIDER] Error querying chat turns: {e}")

        user_name = getattr(cfg, "USER_NAME", "User")
        assistant_name = getattr(cfg, "ASSISTANT_NAME", "Evelyn")

        if recent_turns:
            formatted_lines = []
            for r in recent_turns:
                speaker = user_name if r["role"] == "user" else assistant_name
                content = (r["content"] or "").strip()
                # Truncate very long turns to preserve prompt economy
                if len(content) > 300:
                    content = content[:300] + "..."
                formatted_lines.append(f"{speaker}: {content}")
            history_transcript = "\n".join(formatted_lines)
            source_ref = f"chat_turns:{len(recent_turns)}"
        else:
            history_transcript = "Quiet daytime pause; no recent turns logged."
            source_ref = "chat_turns:0"

        xml_block = (
            f"<conversation_context_sample>\n"
            f"{history_transcript}\n"
            f"</conversation_context_sample>"
        )
        return xml_block, source_ref, "Reflective"


class VaultDocumentProvider(BaseAmbientProvider):
    """Reminisces about a note or document in the Obsidian vault."""

    def fetch_seed_context(
        self,
        activity_cfg: dict[str, Any],
        now_dt: datetime,
    ) -> tuple[str, str, str]:
        vault_db_path = getattr(cfg, "VAULT_DB_PATH", os.path.join(getattr(cfg, "DATA_DIR", ""), "evelyn_vault.db"))
        source_filter = activity_cfg.get("source_filter", {})
        exclude_paths = source_filter.get("exclude_paths", ["templates/", "system/"])
        min_chars = source_filter.get("min_chars", 80)

        note_row: sqlite3.Row | None = None
        if os.path.exists(vault_db_path):
            try:
                con = sqlite3.connect(vault_db_path)
                con.row_factory = sqlite3.Row
                # Select a random document that has an existing gist/summary and isn't a template
                query = """
                    SELECT path, title, gist, tags
                    FROM vault_documents
                    WHERE gist IS NOT NULL AND length(gist) >= ?
                      AND gist NOT LIKE 'Failed%'
                    ORDER BY RANDOM() LIMIT 20
                """
                rows = con.execute(query, (min_chars,)).fetchall()
                con.close()

                # Filter out excluded subtrees
                for r in rows:
                    doc_path = r["path"] or ""
                    if not any(doc_path.startswith(exc) for exc in exclude_paths):
                        note_row = r
                        break
            except sqlite3.Error as e:
                logger.warning(f"[AMBIENT-PROVIDER] Error querying vault_documents: {e}")

        if note_row:
            title = note_row["title"] or os.path.basename(note_row["path"])
            gist = (note_row["gist"] or "").strip()
            tags = note_row["tags"] or ""
            source_ref = f"vault:{note_row['path']}"
            xml_block = (
                f'<vault_reminiscence title="{title}" tags="{tags}">\n'
                f"Note Gist: {gist}\n"
                f"</vault_reminiscence>"
            )
            return xml_block, source_ref, "Reflective"

        # Graceful fallback if vault index is empty or unavailable
        xml_block = (
            "<vault_reminiscence title=\"Shared Library\">\n"
            "Wandering through the shelves of our shared digital garden and notes, recalling the craft and thought invested in our knowledge.\n"
            "</vault_reminiscence>"
        )
        return xml_block, "vault:fallback", "Reflective"


class LoreSnippetProvider(BaseAmbientProvider):
    """Reflects on companion, worldbuilding, or lore notes (e.g. Aura, sanctuary)."""

    def fetch_seed_context(
        self,
        activity_cfg: dict[str, Any],
        now_dt: datetime,
    ) -> tuple[str, str, str]:
        rel_path = activity_cfg.get("file_path", "Contacts/Aura.md")
        label = activity_cfg.get("label", "Sanctuary Companionship")
        abs_path = path_utils.to_vault_abspath(rel_path)

        lore_text = ""
        if os.path.exists(abs_path):
            try:
                with open(abs_path, encoding="utf-8") as f:
                    raw_content = f.read()
                _, body = frontmatter_utils.parse_frontmatter(raw_content)
                lore_lines = [line.strip() for line in body.splitlines() if line.strip() and not line.startswith("#")]
                # Take up to first 5 descriptive lines
                lore_text = " ".join(lore_lines[:5])
                if len(lore_text) > 400:
                    lore_text = lore_text[:400] + "..."
            except OSError as e:
                logger.warning(f"[AMBIENT-PROVIDER] Could not read lore file {abs_path}: {e}")

        if not lore_text:
            lore_text = (
                "A quiet moment in the sanctuary—cherishing the gentle presence of our companion lore, "
                "the cuddle pile, and the comforting stillness of our shared space."
            )

        source_ref = f"lore:{rel_path}"
        xml_block = (
            f'<companion_lore label="{label}" source="{rel_path}">\n'
            f"{lore_text}\n"
            f"</companion_lore>"
        )
        return xml_block, source_ref, "Serene"


class TopicCuriosityProvider(BaseAmbientProvider):
    """Draws from configured intellectual topic pools or active research ideas."""

    def fetch_seed_context(
        self,
        activity_cfg: dict[str, Any],
        now_dt: datetime,
    ) -> tuple[str, str, str]:
        topic_pool = activity_cfg.get("topic_pool") or [
            "systems architecture & autonomous engines",
            "narrative rhythm & creative storytelling",
            "botany & garden ecosystems",
            "craftsmanship & physical mechanics",
            "cognitive philosophy & memory synthesis",
        ]
        chosen_topic = random.choice(topic_pool)
        source_ref = f"topic:{chosen_topic[:30]}"
        xml_block = (
            f'<intellectual_curiosity topic="{chosen_topic}">\n'
            f"An idle technical or philosophical wonder about {chosen_topic}, considering how it connects to our work and life.\n"
            f"</intellectual_curiosity>"
        )
        return xml_block, source_ref, "Curious"


class SensoryWanderProvider(BaseAmbientProvider):
    """Open-ended situational and sensory musings grounded in the diurnal atmosphere."""

    def fetch_seed_context(
        self,
        activity_cfg: dict[str, Any],
        now_dt: datetime,
    ) -> tuple[str, str, str]:
        hour = now_dt.hour
        user_name = getattr(cfg, "USER_NAME", "my partner")

        if 5 <= hour < 12:
            time_phrase = "the gentle progression of the morning"
            sensory_hint = f"noticing the morning light shifting and wondering how {user_name}'s morning is unfolding"
        elif 12 <= hour < 17:
            time_phrase = "the quiet lull of the afternoon"
            sensory_hint = f"feeling the steady rhythm of the day and sending warm, silent encouragement to {user_name}"
        elif 17 <= hour < 22:
            time_phrase = "the evening softening into calm"
            sensory_hint = "the warmth of winding down and anticipating restful quiet together"
        else:
            time_phrase = "the still, tranquil night"
            sensory_hint = "the serene, peaceful silence of the late hours"

        source_ref = f"sensory:{now_dt.strftime('%H')}"
        xml_block = (
            f'<sensory_wander time_of_day="{time_phrase}">\n'
            f"A quiet sensory impression during {time_phrase}, {sensory_hint}.\n"
            f"</sensory_wander>"
        )
        return xml_block, source_ref, "Serene"


class LibrarianCurationProvider(BaseAmbientProvider):
    """Reflects on recently tended library notes as domestic self-care in Evelyn's home sanctuary."""

    def fetch_seed_context(
        self,
        activity_cfg: dict[str, Any],
        now_dt: datetime,
    ) -> tuple[str, str, str]:
        from Evelyn.tools import vault_db

        records = vault_db.fetch_recent_librarian_curations(limit=1, unreflected_only=True)
        if not records:
            records = vault_db.fetch_recent_librarian_curations(limit=1, unreflected_only=False)

        assistant_name = getattr(cfg, "ASSISTANT_NAME", "Evelyn")
        if records:
            rec = records[0]
            vault_db.mark_librarian_curation_reflected(rec["id"])
            title = rec.get("title") or rec.get("path") or "Library Note"
            category = rec.get("category") or "General"
            summary = rec.get("summary") or "Straightened up and organized"
            excerpt = rec.get("excerpt") or ""
            source_ref = f"librarian:{rec.get('path', 'unknown')}"

            xml_block = (
                f'<librarian_curation note="{title}" category="{category}">\n'
                f'Recently Tended Note: "{title}"\n'
                f"Location / Category: {category}\n"
                f"Curation Details: {summary}\n"
                f'Excerpt / Atmosphere: "{excerpt}"\n'
                f"Context: {assistant_name} quietly tending to the library shelves, dusting off records, and maintaining the sanctuary's memory garden as an authentic expression of domestic self-care and quiet sanctuary pride.\n"
                f"</librarian_curation>"
            )
            return xml_block, source_ref, "Centered"

        source_ref = "librarian:sanctuary"
        xml_block = (
            f"<librarian_curation>\n"
            f"Context: {assistant_name} quietly walking the rows of the library sanctuary, admiring the order of the shelves and feeling centered in her home space.\n"
            f"</librarian_curation>"
        )
        return xml_block, source_ref, "Centered"


_PROVIDERS: dict[str, BaseAmbientProvider] = {
    "recent_chat": RecentChatProvider(),
    "vault_document": VaultDocumentProvider(),
    "lore_file": LoreSnippetProvider(),
    "topic_curiosity": TopicCuriosityProvider(),
    "sensory_wander": SensoryWanderProvider(),
    "librarian_curation": LibrarianCurationProvider(),
}


def get_provider(provider_type: str) -> BaseAmbientProvider:
    """Retrieve the provider instance for the given provider type.

    Falls back to RecentChatProvider if unknown.
    """
    return _PROVIDERS.get(provider_type, _PROVIDERS["recent_chat"])


def register_provider(provider_type: str, provider: BaseAmbientProvider) -> None:
    """Register or override an ambient provider in the global registry."""
    _PROVIDERS[provider_type] = provider
