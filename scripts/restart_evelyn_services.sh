#!/usr/bin/env bash
# restart_evelyn_services.sh — Safely and cleanly restart Evelyn core services
# date created: 2026-08-27
# tags: #services, #systemd, #restart, #fastapi, #evelyn

set -euo pipefail

RESTART_OLLAMA=false
CHECKPOINT_WAL=true

for arg in "$@"; do
    case "$arg" in
        --all|--with-ollama)
            RESTART_OLLAMA=true
            ;;
        --no-wal)
            CHECKPOINT_WAL=false
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --all, --with-ollama     Also restart the Ollama LLM service"
            echo "  --no-wal                 Skip pre-restart SQLite WAL checkpoint"
            echo "  -h, --help               Show this help message"
            exit 0
            ;;
    esac
done

echo "🔄 Initiating clean restart of Evelyn services..."

# 1. Pre-restart SQLite WAL checkpoint to ensure zero uncommitted transactions
if [ "$CHECKPOINT_WAL" = true ]; then
    echo "💾 Checkpointing SQLite database WAL files..."
    DB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/data"
    if [ -d "$DB_DIR" ]; then
        for db in "$DB_DIR"/*.db "$DB_DIR"/health/*.db; do
            if [ -f "$db" ]; then
                sqlite3 "$db" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null 2>&1 || true
            fi
        done
        echo "  ✓ SQLite WAL checkpoint complete."
    fi
fi

# 2. Restart Ollama if requested
if [ "$RESTART_OLLAMA" = true ]; then
    echo "🦙 Restarting Ollama service..."
    sudo systemctl restart ollama
    echo "  ✓ ollama.service restarted."
fi

# 3. Restart Evelyn TTS & AI Core
echo "⚡ Restarting Evelyn TTS & Core Engine..."
sudo systemctl restart evelyn-tts evelyn
echo "  ✓ evelyn-tts.service and evelyn.service restarted."

# 4. Restart User Vault Watcher if active
if systemctl --user is-active --quiet evelyn-vault-watcher 2>/dev/null; then
    systemctl --user restart evelyn-vault-watcher
    echo "  ✓ evelyn-vault-watcher user service restarted."
fi

# 5. Wait for FastAPI backend initialization & verify status probe
echo "⏳ Waiting for Evelyn Engine to initialize..."
HEALTHY=false
for i in {1..12}; do
    sleep 1
    # Check if port 7860 is listening and endpoint responds
    if curl -sk https://127.0.0.1:7860/status >/dev/null 2>&1 || curl -s http://127.0.0.1:7860/status >/dev/null 2>&1; then
        HEALTHY=true
        break
    fi
done

# 6. Final verification report
if [ "$HEALTHY" = true ]; then
    echo "✅ Evelyn Engine is active and healthy!"
    # Display active statuses
    systemctl is-active ollama evelyn-tts evelyn | paste -sd " " - | awk '{print "  Services (Ollama, TTS, Engine): " $0}'
else
    echo "⚠️ Warning: Evelyn Engine took longer than 12s to respond to /status."
    echo "Check logs with: journalctl -u evelyn -n 30 --no-pager"
fi

echo "✨ Restart complete."
