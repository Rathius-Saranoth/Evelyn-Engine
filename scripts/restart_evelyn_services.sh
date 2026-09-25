#!/usr/bin/env bash
# restart_evelyn_services.sh — Safely and cleanly restart Evelyn core services
# date created: 2026-08-27
# tags: #services, #systemd, #restart, #fastapi, #evelyn

set -euo pipefail

# Shared graceful-stop + WAL helpers. `systemctl restart` alone never lets the engine reach its
# own shutdown handler, so the Chroma drain is skipped — see scripts/graceful_stop.sh.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=graceful_stop.sh
source "${SCRIPT_DIR}/graceful_stop.sh"

GRACEFUL_OK=true
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
    evelyn_checkpoint_wal "${SCRIPT_DIR}/../data"
fi

# 2. Restart Ollama if requested
if [ "$RESTART_OLLAMA" = true ]; then
    echo "🦙 Restarting Ollama service..."
    sudo systemctl restart ollama
    echo "  ✓ ollama.service restarted."
fi

# 3. Restart Evelyn TTS, STT & AI Core
#
# The engine is stopped on its own and verified before anything starts again. `systemctl
# restart` gives no seam to check in, and a shutdown that was SIGKILLed mid-Chroma-write looks
# identical to a clean one from the outside.
echo "⚡ Restarting Evelyn Voice (TTS/STT) & Core Engine..."
evelyn_graceful_stop || GRACEFUL_OK=false

if systemctl is-active --quiet evelyn-stt 2>/dev/null || systemctl is-enabled --quiet evelyn-stt 2>/dev/null; then
    sudo systemctl restart evelyn-tts evelyn-stt
    sudo systemctl start evelyn
    echo "  ✓ evelyn-tts.service, evelyn-stt.service, and evelyn.service restarted."
else
    sudo systemctl restart evelyn-tts
    sudo systemctl start evelyn
    echo "  ✓ evelyn-tts.service and evelyn.service restarted."
fi

# 4. Restart User Vault Watcher & Syncthing if active
if systemctl --user is-active --quiet evelyn-vault-watcher 2>/dev/null; then
    systemctl --user restart evelyn-vault-watcher
    echo "  ✓ evelyn-vault-watcher user service restarted."
fi
if systemctl --user is-active --quiet syncthing 2>/dev/null; then
    systemctl --user restart syncthing
    echo "  ✓ syncthing user service restarted."
fi

# 5. Wait for FastAPI backend initialization & verify status probe
echo "⏳ Waiting for Evelyn Engine to initialize..."
HEALTHY=false
API_KEY=$(grep -oP '(?<=EVELYN_API_KEY=)[^\"]+' /etc/systemd/system/evelyn.service 2>/dev/null || echo "${EVELYN_API_KEY:-}")
for i in {1..12}; do
    sleep 1
    # Check if port 7860 is listening and endpoint responds with 200 OK
    if curl -sk -H "X-Evelyn-Key: ${API_KEY}" https://127.0.0.1:7860/status | grep -q '"status":"ok"'; then
        HEALTHY=true
        break
    fi
done

# 6. Final verification report
if [ "$HEALTHY" = true ]; then
    echo "✅ Evelyn Engine is active and healthy!"
    # Display active statuses
    systemctl is-active ollama evelyn-tts evelyn-stt evelyn 2>/dev/null | paste -sd " " - | awk '{print "  Services (Ollama, TTS, STT, Engine): " $0}'
else
    echo "⚠️ Warning: Evelyn Engine took longer than 12s to respond to /status."
    echo "Check logs with: journalctl -u evelyn -n 30 --no-pager"
fi

if [ "$GRACEFUL_OK" != true ]; then
    echo "⚠️  Restart complete, but the shutdown was NOT graceful — see the warning above."
    exit 1
fi

echo "✨ Restart complete."
