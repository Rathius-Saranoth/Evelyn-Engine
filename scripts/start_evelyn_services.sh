#!/usr/bin/env bash
# start_evelyn_services.sh — Linux Systemd Service Controller
# date created: 2026-08-11

echo "[SYSTEMD] Starting Evelyn engine services (ollama, evelyn, evelyn-tts)..."
sudo systemctl daemon-reload
sudo systemctl start ollama evelyn evelyn-tts

echo "[SYSTEMD] Starting user services (syncthing, evelyn-vault-watcher)..."
systemctl --user daemon-reload
systemctl --user start syncthing evelyn-vault-watcher

echo "[SYSTEMD] System Services Status:"
sudo systemctl status ollama evelyn evelyn-tts --no-pager

echo "[SYSTEMD] User Services Status:"
systemctl --user status syncthing evelyn-vault-watcher --no-pager
