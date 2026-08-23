#!/usr/bin/env bash
# stop_evelyn_services.sh — Gracefully stop Evelyn services and background daemons
# date created: 2026-08-11
# date modified: 2026-08-23

set -euo pipefail

STOP_OLLAMA=false
CHECKPOINT_WAL=false

for arg in "$@"; do
    case "$arg" in
        --all|--with-ollama)
            STOP_OLLAMA=true
            ;;
        --checkpoint-wal|--flush-wal)
            CHECKPOINT_WAL=true
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --all, --with-ollama     Also stop the Ollama LLM service"
            echo "  --checkpoint-wal         Flush/truncate SQLite WAL logs for all databases"
            echo "  -h, --help               Show this help message"
            exit 0
            ;;
    esac
done

echo "🛑 Stopping Evelyn services..."

# 1. Stop Evelyn AI Core & TTS server
if sudo systemctl is-active --quiet evelyn || sudo systemctl is-active --quiet evelyn-tts; then
    sudo systemctl stop evelyn evelyn-tts
    echo "  ✓ Stopped systemd services: evelyn, evelyn-tts"
else
    echo "  - evelyn and evelyn-tts were not running."
fi

# 2. Stop User Watcher Service if active
if systemctl --user is-active --quiet evelyn-vault-watcher 2>/dev/null; then
    systemctl --user stop evelyn-vault-watcher
    echo "  ✓ Stopped user service: evelyn-vault-watcher"
elif systemctl --user -M "${USER}@" is-active --quiet evelyn-vault-watcher 2>/dev/null; then
    systemctl --user -M "${USER}@" stop evelyn-vault-watcher
    echo "  ✓ Stopped user service: evelyn-vault-watcher (session)"
fi

# 3. Stop Ollama if requested
if [ "$STOP_OLLAMA" = true ]; then
    if sudo systemctl is-active --quiet ollama; then
        sudo systemctl stop ollama
        echo "  ✓ Stopped systemd service: ollama"
    else
        echo "  - ollama was not running."
    fi
fi

# 4. Checkpoint SQLite databases if requested
if [ "$CHECKPOINT_WAL" = true ]; then
    echo "💾 Checkpointing SQLite database WAL files..."
    DB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/data"
    if [ -d "$DB_DIR" ]; then
        for db in "$DB_DIR"/*.db "$DB_DIR"/health/*.db; do
            if [ -f "$db" ]; then
                sqlite3 "$db" "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
            fi
        done
        echo "  ✓ SQLite WAL checkpoint complete."
    fi
fi

echo "✨ Evelyn services stop complete."
