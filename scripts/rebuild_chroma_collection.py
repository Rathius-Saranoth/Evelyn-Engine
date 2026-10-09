#!/usr/bin/env python3
# rebuild_chroma_collection.py
# date created: 2026-09-22 17:45:00
# date modified: 2026-09-23 21:46:27
# tags: #chroma, #vector, #repair, #maintenance, #cli

"""rebuild_chroma_collection.py — Targeted repair for a single Chroma collection.

The engine's built-in fallback, ``chroma_rag.repair_corrupted_chroma()``, deletes the
*entire* vector store and re-syncs everything. That is far too blunt when one collection
is unreadable and the rest are fine: it discards healthy vectors and re-embeds tens of
thousands of documents to fix one segment. This tool rebuilds one named collection and
leaves the others untouched.

A corrupt HNSW segment fails as **SIGSEGV**, not as an exception — the Rust bindings abort
the process, so no ``try``/``except`` in this file could catch it. Every probe therefore
runs in a **child process** and is judged by its exit code. That is the whole reason this
script can report on a collection that crashes anything which opens it.

Usage:
    python scripts/rebuild_chroma_collection.py --list
    python scripts/rebuild_chroma_collection.py --orphans
    python scripts/rebuild_chroma_collection.py --collection evelyn_memory
    python scripts/rebuild_chroma_collection.py --collection evelyn_memory --execute
    python scripts/rebuild_chroma_collection.py --reclaim-orphans --execute

Nothing is modified unless ``--execute`` is passed.

``--execute`` refuses to run while ``evelyn.service`` is up. Every process keeps its own copy
of an HNSW index and persists it on write, so a rebuild beside the running engine is two
writers on one segment — on 2026-09-23 that grew ``evelyn_reference`` to 891GB and filled the
disk. Stop the engine, run this, start the engine. The one exception is the engine's own
startup repair, which runs this as its child before its custodian starts: the rebuild then
only re-enqueues, and the custodian does the embedding.
"""

import argparse
import contextlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import evelyn_config as cfg
from Evelyn.tools import chroma_rag

CHROMA_DIR = cfg.CHROMA_DB_PATH
CHROMA_SQLITE = os.path.join(CHROMA_DIR, "chroma.sqlite3")
BACKUP_DIR = os.path.join(cfg.DATA_DIR, "backups")

PROBE_TIMEOUT = 300

# Canonical in Evelyn/tools/chroma_rag.py so the engine's own auto-repair and this tool
# cannot drift apart about what is rebuildable.
REBUILD_STRATEGIES = chroma_rag.REBUILD_STRATEGIES


def _run_child(code: str) -> tuple[bool, str]:
    """Execute a snippet in a child process so a native crash cannot kill this one.

    Args:
        code: Python source to run in the child.

    Returns:
        tuple[bool, str]: (survived, detail). ``detail`` names the signal on a crash.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT,
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
            env={**os.environ, "PYTHONPATH": "."},
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {PROBE_TIMEOUT}s"
    if proc.returncode == 0:
        return True, (proc.stdout.strip().splitlines() or [""])[-1]
    if proc.returncode < 0:
        return False, f"killed by signal {-proc.returncode} (SIGSEGV = 11)"
    if proc.returncode == 139:
        return False, "segmentation fault (exit 139)"
    err = (proc.stderr.strip().splitlines() or ["unknown error"])[-1]
    return False, f"exit {proc.returncode}: {err[:160]}"


def probe_collection(name: str) -> tuple[bool, str]:
    """Report whether a collection can be read at all, without risking this process."""
    return chroma_rag.probe_collection_health(name, timeout=PROBE_TIMEOUT)


def registered_segments() -> dict[str, str]:
    """Return {segment_id: collection_name} for every segment Chroma has registered."""
    con = sqlite3.connect(CHROMA_SQLITE)
    try:
        rows = con.execute(
            "SELECT s.id, c.name FROM segments s JOIN collections c ON s.collection = c.id"
        ).fetchall()
    finally:
        con.close()
    return dict(rows)


def _dir_bytes(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            with contextlib.suppress(OSError):
                total += os.path.getsize(os.path.join(root, f))
    return total


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024.0
    return f"{n}"


def find_orphans() -> list[tuple[str, int]]:
    """Return [(segment_dir, bytes)] for on-disk segments no collection references.

    Metadata segments live inside chroma.sqlite3 and have no directory, so absence of a
    directory is normal; only the reverse — a directory nothing references — is an orphan.
    """
    known = set(registered_segments())
    orphans = []
    for entry in sorted(os.listdir(CHROMA_DIR)):
        full = os.path.join(CHROMA_DIR, entry)
        if not os.path.isdir(full) or len(entry) != 36 or entry in known:
            continue
        orphans.append((entry, _dir_bytes(full)))
    return orphans


def ensure_backup(label: str) -> str:
    """Archive the whole Chroma directory before any destructive step."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dest = os.path.join(BACKUP_DIR, f"chroma_db_{label}_{time.strftime('%Y%m%d_%H%M%S')}.tar")
    print(f"  Archiving {CHROMA_DIR} -> {dest} ...", flush=True)
    with tarfile.open(dest, "w") as tar:
        tar.add(CHROMA_DIR, arcname=os.path.basename(CHROMA_DIR))
    print(f"  Backup written ({_human(os.path.getsize(dest))}).", flush=True)
    return dest


def cmd_list() -> int:
    """Print every collection with its readability, row count and rebuild support."""
    con = sqlite3.connect(CHROMA_SQLITE)
    try:
        rows = con.execute(
            """SELECT c.name, COUNT(e.id) FROM collections c
               LEFT JOIN segments s ON s.collection = c.id
               LEFT JOIN embeddings e ON e.segment_id = s.id
               GROUP BY c.name ORDER BY c.name"""
        ).fetchall()
    finally:
        con.close()

    # One batched probe rather than one child per collection: the embedding model load
    # dominates, so probing separately multiplied it by the number of collections.
    health = chroma_rag.check_chroma_health()
    unhealthy = set(health["unhealthy"])

    print(f"{'collection':<36} {'rows':>8}  {'readable':<10} {'rebuildable':<12} detail")
    print("-" * 104)
    for name, count in rows:
        ok = name not in unhealthy
        detail = health["collections"].get(name, "not probed")
        print(
            f"{name:<36} {count:>8}  {'yes' if ok else 'NO':<10} "
            f"{'yes' if name in REBUILD_STRATEGIES else 'no':<12} {detail}"
        )
    return 0


def cmd_orphans(execute: bool) -> int:
    """Report, and optionally reclaim, segment directories nothing references."""
    orphans = find_orphans()
    if not orphans:
        print("No orphaned segment directories.")
        return 0

    total = sum(size for _, size in orphans)
    print(f"Orphaned segment directories ({len(orphans)}, {_human(total)} reclaimable):")
    for seg, size in orphans:
        mtime = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(os.path.join(CHROMA_DIR, seg))))
        print(f"  {seg}  {_human(size):>9}  last modified {mtime}")

    if not execute:
        print("\nDry run. Re-run with --execute to reclaim.")
        return 0

    ensure_backup("pre_orphan_reclaim")
    for seg, size in orphans:
        path = os.path.join(CHROMA_DIR, seg)
        print(f"  Removing {seg} ({_human(size)}) ...", flush=True)
        shutil.rmtree(path, ignore_errors=True)
    print(f"Reclaimed {_human(total)}.")
    return 0


def cmd_rebuild(name: str, execute: bool, force: bool, engine_invoked: bool = False) -> int:
    """Drop one collection and regenerate it from its canonical source.

    ``engine_invoked`` stops after re-enqueueing: the engine's custodian embeds, and this
    process must not drain beside it.
    """
    strategy = REBUILD_STRATEGIES.get(name)
    if not strategy:
        print(f"[ABORT] No rebuild strategy for '{name}'.")
        print(f"        Supported: {', '.join(sorted(REBUILD_STRATEGIES))}")
        print("        Dropping a collection that cannot be regenerated would lose data.")
        return 2

    print(f"Collection : {name}")
    print(f"Source     : {strategy['description']}")
    readable, detail = probe_collection(name)
    print(f"Readable   : {'yes' if readable else 'NO'} ({detail})")

    if readable and not force:
        print("\n[SKIP] Collection is readable; nothing to repair. Use --force to rebuild anyway.")
        return 0

    module_name, func_name = strategy["entrypoint"]
    state_paths = [getattr(cfg, key) for key in strategy["state_files"] if hasattr(cfg, key)]

    print("\nPlanned actions:")
    print(f"  1. Archive {CHROMA_DIR}")
    print(f"  2. Delete collection '{name}' (its vectors only)")
    for sp in state_paths:
        print(f"  3. Clear sync state {sp}")
    print(f"  4. Re-enqueue via {module_name}.{func_name}()")
    print("  5. Drain the embedding queue (this process only; the engine must be stopped)")

    if not execute:
        print("\nDry run. Re-run with --execute to apply.")
        return 0

    ensure_backup(f"pre_rebuild_{name}")

    print(f"\n  Deleting collection '{name}' ...", flush=True)
    ok, detail = _run_child(
        "from Evelyn.tools import chroma_rag\n"
        f"chroma_rag._get_client().delete_collection({name!r})\n"
        "print('deleted')\n"
    )
    if not ok:
        # A segment too corrupt to open is also too corrupt to delete through the client.
        print(f"  Client delete failed ({detail}); falling back to offline removal.", flush=True)
        if not _offline_drop(name):
            print("[ABORT] Offline removal failed. Restore from the archive above.")
            return 1
    print("  Collection dropped.", flush=True)

    for sp in state_paths:
        if os.path.exists(sp):
            os.remove(sp)
            print(f"  Cleared sync state {sp}", flush=True)

    print(f"\n  Re-enqueueing via {module_name}.{func_name}() ...", flush=True)
    __import__(module_name)
    getattr(sys.modules[module_name], func_name)()

    if engine_invoked:
        chroma_rag.release_chroma_writer()
        print("\n  Queued. The engine's custodian embeds it once startup completes.", flush=True)
        return 0

    print("\n  Draining embedding queue (this is the slow part) ...", flush=True)
    total = 0
    while True:
        drained = chroma_rag.drain_sync_queue(50)
        if not drained:
            break
        total += drained
        print(f"    embedded {total} ...", flush=True)

    ok, detail = probe_collection(name)
    print(f"\nRebuilt. Readable: {'yes' if ok else 'NO'} ({detail})")
    return 0 if ok else 1


def _offline_drop(name: str) -> bool:
    """Remove a collection by editing chroma.sqlite3 directly, for unopenable segments."""
    con = sqlite3.connect(CHROMA_SQLITE)
    try:
        row = con.execute("SELECT id FROM collections WHERE name = ?", (name,)).fetchone()
        if not row:
            return True
        coll_id = row[0]
        seg_ids = [r[0] for r in con.execute(
            "SELECT id FROM segments WHERE collection = ?", (coll_id,)
        ).fetchall()]
        for sid in seg_ids:
            con.execute("DELETE FROM embeddings WHERE segment_id = ?", (sid,))
        con.execute("DELETE FROM segments WHERE collection = ?", (coll_id,))
        con.execute("DELETE FROM collections WHERE id = ?", (coll_id,))
        con.commit()
    except sqlite3.Error as e:
        print(f"  Offline removal error: {e}")
        return False
    finally:
        con.close()

    for sid in seg_ids:
        path = os.path.join(CHROMA_DIR, sid)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild a single Chroma collection.")
    parser.add_argument("--collection", help="Collection to rebuild")
    parser.add_argument("--list", action="store_true", help="Show all collections and their health")
    parser.add_argument("--orphans", action="store_true", help="Report unreferenced segment directories")
    parser.add_argument("--reclaim-orphans", action="store_true", help="Remove unreferenced segment directories")
    parser.add_argument("--execute", action="store_true", help="Apply changes (default: dry run)")
    parser.add_argument("--force", action="store_true", help="Rebuild even if the collection reads fine")
    args = parser.parse_args()

    if not os.path.exists(CHROMA_SQLITE):
        print(f"[ABORT] No Chroma store at {CHROMA_SQLITE}")
        return 2

    engine_invoked = False
    if args.execute and (args.collection or args.reclaim_orphans):
        code, engine_invoked = chroma_rag.acquire_offline_writer(
            f"rebuild_chroma_collection.py {' '.join(sys.argv[1:])}"
        )
        if code:
            return code

    if args.list:
        return cmd_list()
    if args.orphans or args.reclaim_orphans:
        return cmd_orphans(execute=args.execute and args.reclaim_orphans)
    if args.collection:
        return cmd_rebuild(args.collection, execute=args.execute, force=args.force,
                           engine_invoked=engine_invoked)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
