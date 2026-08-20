#!/usr/bin/env python3
# obsidian_vault_watcher.py
# date created: 2026-08-15 14:45:36
# date modified: 2026-08-19 19:48:02
# tags: 

# scripts/obsidian_vault_watcher.py
# date created: 2026-08-15
"""
obsidian_vault_watcher.py — Real-time debounced filesystem watcher for Obsidian Vault.

Monitors /home/rathius/obsidian_vault for file additions, modifications, and deletions.
After a quiet debounce cooldown (default 4.0s), triggers incremental ingestion:
  1. Updates SQLite metadata & tags in evelyn_vault.db (via vault_db)
  2. Updates ChromaDB vector embeddings in evelyn_memory (via ingest_obsidian_knowledge)

Designed to run 24/7 as a systemd user service with low CPU/IO priority.
"""

import os
import re
import signal
import sys
import time

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# Anchor workspace roots for imports
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
TOOLS_DIR = os.path.join(ROOT_DIR, "Evelyn", "tools")
for _p in (ROOT_DIR, TOOLS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import evelyn_config as cfg  # noqa: E402
import ingest_obsidian_knowledge  # noqa: E402
import vault_db  # noqa: E402
import task_manager  # noqa: E402
import chroma_rag  # noqa: E402

VAULT_DIR = getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")
DEBOUNCE_SECONDS = 4.0

# Subdirectories and file patterns to ignore
IGNORED_PATTERNS = [
    ".obsidian",
    ".stversions",
    ".stfolder",
    ".trash",
    ".git",
    "syncthing",
]

EXCLUDED_EXTENSIONS = {
    ".tmp",
    ".crswap",
    ".part",
    ".DS_Store",
}


def is_ignored(path_str: str) -> bool:
    """Return True if path is a hidden/temporary/sync artifact."""
    norm = path_str.replace("\\", "/").lower()
    for pattern in IGNORED_PATTERNS:
        if f"/{pattern}/" in norm or norm.endswith(f"/{pattern}") or f"/{pattern}" in norm:
            return True
    _, ext = os.path.splitext(norm)
    if ext in EXCLUDED_EXTENSIONS:
        return True
    return bool(os.path.basename(norm).startswith("."))


def quick_extract_metadata(file_path: str) -> dict | None:
    """Fast extraction of YAML frontmatter, H1 title, and tags without LLM overhead."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError) as e:
        print(f"[WATCHER] Error reading {file_path}: {e}", flush=True)
        return None

    fm_tags = []
    fm_aliases = []
    fm_rag_priority = "normal"
    fm_rag_pinned = False

    fm_match = re.search(r"^---\n(.*?)\n---", content, re.DOTALL)
    if fm_match:
        fm_text = fm_match.group(1)
        tags_line = re.search(r"^tags:\s*(\[.*?\]|.*)$", fm_text, re.MULTILINE)
        if tags_line:
            raw_tags = tags_line.group(1).replace("[", "").replace("]", "").replace('"', "").replace("'", "")
            fm_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]

        aliases_line = re.search(r"^aliases:\s*(\[.*?\]|.*)$", fm_text, re.MULTILINE)
        if aliases_line:
            raw_aliases = aliases_line.group(1).replace("[", "").replace("]", "").replace('"', "").replace("'", "")
            fm_aliases = [a.strip() for a in raw_aliases.split(",") if a.strip()]

        priority_line = re.search(r"^rag_priority:\s*(\S+)", fm_text, re.MULTILINE)
        if priority_line:
            fm_rag_priority = priority_line.group(1).strip().lower()

        pinned_line = re.search(r"^rag_pinned:\s*(\S+)", fm_text, re.MULTILINE)
        if pinned_line:
            fm_rag_pinned = pinned_line.group(1).strip().lower() == "true"

    h1_match = re.search(r"^#\s+(.*)", content, re.MULTILINE)
    title = (
        h1_match.group(1).strip()
        if h1_match
        else os.path.splitext(os.path.basename(file_path))[0]
    )

    inline_tags = re.findall(r"(?:^|\s)#([a-zA-Z0-9_/-]+)(?=\s|$)", content)
    all_tags = sorted(set(fm_tags + inline_tags))

    # Fast text slice gist fallback
    text_body = re.sub(r"^---\n(.*?)\n---", "", content, flags=re.DOTALL)
    text_body = re.sub(r"(?m)^#{1,6}\s+.*$", "", text_body)
    text_body = re.sub(r"\s+", " ", text_body).strip()
    gist = text_body[:400] + ("..." if len(text_body) > 400 else "")

    return {
        "title": title,
        "tags": all_tags,
        "aliases": fm_aliases,
        "rag_priority": fm_rag_priority,
        "rag_pinned": fm_rag_pinned,
        "gist": gist,
    }


def update_sqlite_for_changed_files(changed_files: set[str], deleted_files: set[str]) -> None:
    """Update SQLite database for modified and deleted notes."""
    vault_db.init_db()
    for full_path in changed_files:
        if not os.path.exists(full_path):
            continue
        rel_path = os.path.relpath(full_path, VAULT_DIR)
        meta = quick_extract_metadata(full_path)
        if meta:
            try:
                mtime = os.path.getmtime(full_path)
                vault_db.upsert_document(
                    path=rel_path,
                    title=meta["title"],
                    mtime=mtime,
                    gist=meta["gist"],
                    rag_priority=meta["rag_priority"],
                    rag_pinned=meta["rag_pinned"],
                    tags=",".join(meta["tags"]),
                    aliases=",".join(meta["aliases"]),
                )
            except Exception as e:
                print(f"[WATCHER] SQLite update failed for {rel_path}: {e}", flush=True)

    for full_path in deleted_files:
        rel_path = os.path.relpath(full_path, VAULT_DIR)
        try:
            vault_db.delete_document(rel_path)
        except Exception as e:
            print(f"[WATCHER] SQLite delete failed for {rel_path}: {e}", flush=True)


class DebouncedVaultEventHandler(FileSystemEventHandler):
    """Event handler that aggregates filesystem changes and debounces execution."""

    def __init__(self, debounce_interval: float = DEBOUNCE_SECONDS):
        super().__init__()
        self.debounce_interval = debounce_interval
        self.last_event_time = 0.0
        self.pending = False
        self.changed_files: set[str] = set()
        self.deleted_files: set[str] = set()

    def _record_event(self, src_path: str, is_deletion: bool = False):
        if is_ignored(src_path):
            return
        if not src_path.endswith(".md"):
            return

        self.last_event_time = time.time()
        self.pending = True

        if is_deletion:
            self.deleted_files.add(src_path)
            self.changed_files.discard(src_path)
        else:
            self.changed_files.add(src_path)
            self.deleted_files.discard(src_path)

    def on_created(self, event):
        if not event.is_directory:
            self._record_event(event.src_path, is_deletion=False)

    def on_modified(self, event):
        if not event.is_directory:
            self._record_event(event.src_path, is_deletion=False)

    def on_deleted(self, event):
        if not event.is_directory:
            self._record_event(event.src_path, is_deletion=True)

    def on_moved(self, event):
        if not event.is_directory:
            self._record_event(event.src_path, is_deletion=True)
            self._record_event(event.dest_path, is_deletion=False)

    def check_and_run(self):
        """If debounce window has elapsed, execute ingestion pipeline."""
        if not self.pending:
            return

        elapsed = time.time() - self.last_event_time
        if elapsed >= self.debounce_interval:
            self.pending = False
            files_to_sync = set(self.changed_files)
            files_to_del = set(self.deleted_files)
            self.changed_files.clear()
            self.deleted_files.clear()

            print(
                f"\n[WATCHER] Vault activity stabilized ({len(files_to_sync)} changed, "
                f"{len(files_to_del)} deleted). Starting ingestion pipeline...",
                flush=True,
            )

            start_t = time.time()
            try:
                # 1. Update SQLite fast metadata index
                update_sqlite_for_changed_files(files_to_sync, files_to_del)

                # 2. Incremental ChromaDB Vector Sync via staging queue
                if task_manager.is_any_running():
                    print("[WATCHER] Heavy background task is active; skipping immediate Chroma vector sync.", flush=True)
                else:
                    ingest_obsidian_knowledge.main()

                duration = time.time() - start_t
                print(f"[WATCHER] Ingestion completed successfully in {duration:.2f}s.\n", flush=True)
            except Exception as e:
                print(f"[WATCHER ERROR] Ingestion failed: {e}", file=sys.stderr, flush=True)


def main():
    if not os.path.exists(VAULT_DIR):
        print(f"[WATCHER ERROR] Vault directory not found: {VAULT_DIR}", file=sys.stderr)
        sys.exit(1)

    print("=================================================================")
    print("Evelyn Obsidian Vault Watcher Active")
    print(f"Monitoring: {VAULT_DIR}")
    print(f"Debounce Window: {DEBOUNCE_SECONDS}s")
    print("=================================================================", flush=True)

    handler = DebouncedVaultEventHandler(debounce_interval=DEBOUNCE_SECONDS)
    observer = Observer()
    observer.schedule(handler, path=VAULT_DIR, recursive=True)
    observer.start()

    def handle_sigterm(signum, frame):
        print("[WATCHER] Termination signal received. Stopping observer...", flush=True)
        observer.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_sigterm)
    signal.signal(signal.SIGTERM, handle_sigterm)

    try:
        while True:
            time.sleep(0.5)
            handler.check_and_run()
    except (KeyboardInterrupt, SystemExit):
        observer.stop()
    except Exception as e:
        print(f"[WATCHER FATAL] Observer loop crashed: {e}", file=sys.stderr, flush=True)
        observer.stop()
    finally:
        observer.join()


if __name__ == "__main__":
    main()
