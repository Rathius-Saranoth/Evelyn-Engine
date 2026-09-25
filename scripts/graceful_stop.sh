#!/usr/bin/env bash
# graceful_stop.sh — Stop evelyn.service and prove the shutdown handler actually ran
# date created: 2026-09-25
# tags: #services, #systemd, #shutdown, #chroma, #evelyn
#
# Sourced by restart_evelyn_services.sh and stop_evelyn_services.sh. Not executable on its own.
#
# Why this exists
# ---------------
# `systemctl restart evelyn` looks clean and is not. The engine's shutdown handler
# (`clean_shutdown_all_tasks`) terminates write producers and then drains the Chroma write
# queue — and on 2026-09-25 it was found never to have run. Uvicorn waits for in-flight
# connections before running lifespan shutdown, the chat UI holds an SSE stream that never
# closes, so the engine sat at "Waiting for connections to close" until systemd's SIGKILL at
# TimeoutStopSec. The Chroma custodian holds the single-writer lease for its whole life, so
# every restart with a browser tab open killed the writer mid-lease and relied on the startup
# reaper clearing a stale lock afterwards. Recovery worked; the shutdown never did.
#
# v000.006.232 capped the two unbounded waits so the drain is reachable. This file is the other
# half: it *verifies* the drain ran rather than assuming it, because a silent regression here
# looks exactly like success.

# The line clean_shutdown_all_tasks() prints last. Its absence means the drain did not finish.
_EVELYN_SHUTDOWN_SENTINEL="Clean shutdown complete"

# Stop evelyn.service and report whether it shut down gracefully.
#
# Returns 0 when the shutdown handler completed, 1 when it did not. Callers should warn loudly
# on 1 rather than continue silently: the vector store may have been killed mid-write, and
# `ROLLBACK.md` -> "Repairing a Single Chroma Collection" is the procedure if it was.
evelyn_graceful_stop() {
    local since
    since="$(date '+%Y-%m-%d %H:%M:%S')"

    if ! systemctl is-active --quiet evelyn 2>/dev/null; then
        echo "  - evelyn.service was not running."
        return 0
    fi

    echo "🛑 Stopping evelyn.service (waiting for the Chroma drain)..."
    sudo systemctl stop evelyn

    local result
    result="$(systemctl show evelyn.service -p Result --value 2>/dev/null)"

    if journalctl -u evelyn.service --since "$since" --no-pager 2>/dev/null \
        | grep -q "$_EVELYN_SHUTDOWN_SENTINEL"; then
        echo "  ✓ Graceful shutdown confirmed (Chroma queue drained)."
        return 0
    fi

    echo ""
    echo "  ⚠️  GRACEFUL SHUTDOWN DID NOT COMPLETE (systemd result: ${result:-unknown})."
    echo "     The Chroma write queue was not drained before the process ended, so the"
    echo "     single-writer custodian may have been killed mid-write."
    echo ""
    echo "     Check:  journalctl -u evelyn.service --since '$since' --no-pager | grep SHUTDOWN"
    echo "     If the vector store misbehaves afterwards, see ROLLBACK.md ->"
    echo "     'Repairing a Single Chroma Collection'. Do not rebuild while the engine runs."
    echo ""
    return 1
}

# Flush SQLite WAL logs for every database. Safe to call while stopped; pointless while running,
# since the engine reopens and re-grows them immediately.
evelyn_checkpoint_wal() {
    local db_dir="$1"
    [ -d "$db_dir" ] || return 0
    echo "💾 Checkpointing SQLite database WAL files..."
    local db
    for db in "$db_dir"/*.db "$db_dir"/health/*.db; do
        if [ -f "$db" ]; then
            sqlite3 "$db" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null 2>&1 || true
        fi
    done
    echo "  ✓ SQLite WAL checkpoint complete."
}
