# refresh_memory.py
# date created: 2026-05-21 20:34:11
# date modified: 2026-06-07 10:28:48
# tags: #refresh, #memory, #master_runner, #synchronization, #pipeline

"""
refresh_memory.py — Unified memory refresh runner for Evelyn.

Runs three phases in strict sequential order:
  Phase 1 (vault_map):        vault_indexer.py — builds/updates SQLite evelyn_vault.db
  Phase 2 (ingest_knowledge): ingest_obsidian_knowledge.py — pushes core docs to Chroma
  Phase 3 (ingest_gists):     ingest_gists.py — pushes gist entries to Chroma

Each phase is launched as its own subprocess so memory is fully reaped between
phases and VRAM is never shared with parallel Ollama calls.

Stdout is tagged with [PHASE_START:name], [PHASE_DONE:name], [PHASE_FAIL:name]
markers so the async server wrapper can parse phase transitions without
coupling to internal script output formats.

Usage (standalone):
    python Evelyn/tools/refresh_memory.py

Usage (via evelyn_server.py):
    asyncio.create_subprocess_exec(sys.executable, '-u', script_path, ...)
"""

import os
import sys
import subprocess

# ---------------------------------------------------------------------------
# Absolute path anchoring — behaves identically whether called by the FastAPI
# server daemon or directly from a PowerShell/Termux prompt.
# ---------------------------------------------------------------------------
import evelyn_config as cfg

ROOT_DIR  = getattr(cfg, "BASE_DIR", r"/home/rathius/evelyn")
TOOLS_DIR = getattr(cfg, "TOOLS_DIR", r"/home/rathius/evelyn/Evelyn/tools")

for _d in (ROOT_DIR, TOOLS_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)


def run_phase_subprocess(name: str, args: list[str]) -> None:
    """Run a single phase as a child subprocess, forwarding its output.

    Args:
        name: Short identifier used in the bracketed phase tags.
        args: Command-line arguments to pass after sys.executable.

    Raises:
        SystemExit: If the subprocess returns a non-zero exit code.
    """
    print(f"[PHASE_START:{name}]", flush=True)
    result = subprocess.run(
        [sys.executable, "-u"] + args,
        stdout=sys.stdout,
        stderr=sys.stderr,
        cwd=ROOT_DIR,
    )
    if result.returncode != 0:
        print(
            f"[PHASE_FAIL:{name}] Subprocess returned non-zero exit code {result.returncode}",
            flush=True,
        )
        sys.exit(result.returncode)
    print(f"[PHASE_DONE:{name}]", flush=True)


def main() -> None:
    """Execute all three memory refresh phases in sequence.

    Returns:
        None
    """
    print("[START] Unified Memory Refresh Pipeline", flush=True)

    # Phase 1 — Vault Map (heavy: reads every vault file, calls Ollama for gists)
    run_phase_subprocess(
        "vault_map",
        [os.path.join(TOOLS_DIR, "vault_indexer.py")],
    )

    # Phase 2 — Core Knowledge Ingest (pushes processed vault docs to Chroma)
    run_phase_subprocess(
        "ingest_knowledge",
        [os.path.join(TOOLS_DIR, "ingest_obsidian_knowledge.py")],
    )

    # Phase 3 — Gist Ingest (pushes gist summaries to Chroma)
    run_phase_subprocess(
        "ingest_gists",
        [os.path.join(TOOLS_DIR, "ingest_gists.py")],
    )

    print("[SUCCESS] All memory refresh phases completed.", flush=True)


if __name__ == "__main__":
    main()
