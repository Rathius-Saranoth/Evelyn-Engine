#!/usr/bin/env bash
# start_evelyn_services.sh — Linux Systemd Service Controller
# date created: 2026-08-11

if systemctl is-enabled --quiet evelyn-stt 2>/dev/null || systemctl is-active --quiet evelyn-stt 2>/dev/null; then
    echo "[SYSTEMD] Starting Evelyn engine services (ollama, evelyn, evelyn-tts, evelyn-stt)..."
    sudo systemctl daemon-reload
    sudo systemctl start ollama evelyn evelyn-tts evelyn-stt
else
    echo "[SYSTEMD] Starting Evelyn engine services (ollama, evelyn, evelyn-tts)..."
    sudo systemctl daemon-reload
    sudo systemctl start ollama evelyn evelyn-tts
fi

echo "[SYSTEMD] Starting user services (syncthing, evelyn-vault-watcher)..."
systemctl --user daemon-reload
systemctl --user start syncthing evelyn-vault-watcher

echo "[SYSTEMD] System Services Status:"
if systemctl is-enabled --quiet evelyn-stt 2>/dev/null || systemctl is-active --quiet evelyn-stt 2>/dev/null; then
    sudo systemctl status ollama evelyn evelyn-tts evelyn-stt --no-pager
else
    sudo systemctl status ollama evelyn evelyn-tts --no-pager
fi

echo "[SYSTEMD] User Services Status:"
systemctl --user status syncthing evelyn-vault-watcher --no-pager
