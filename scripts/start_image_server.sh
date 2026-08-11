#!/usr/bin/env bash
# start_image_server.sh — Standalone Image Server Startup Script for Linux Hosts
# date created: 2026-08-11

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
IMAGE_DIR="$BASE_DIR/services/image"

cd "$IMAGE_DIR" || exit 1

# Activate virtual environment if available
if [ -d "$IMAGE_DIR/venv" ]; then
    echo "[IMAGE] Activating virtualenv at $IMAGE_DIR/venv..."
    source "$IMAGE_DIR/venv/bin/activate"
elif [ -d "$BASE_DIR/venv" ]; then
    echo "[IMAGE] Activating virtualenv at $BASE_DIR/venv..."
    source "$BASE_DIR/venv/bin/activate"
fi

export IMAGE_SERVER_HOST="${IMAGE_SERVER_HOST:-0.0.0.0}"
export IMAGE_SERVER_PORT="${IMAGE_SERVER_PORT:-5055}"
export IMAGE_SERVER_UNLOAD_TIMEOUT="${IMAGE_SERVER_UNLOAD_TIMEOUT:-120}"

echo "[IMAGE] Starting FLUX.1 [schnell] standalone image server on $IMAGE_SERVER_HOST:$IMAGE_SERVER_PORT..."
exec python image_server.py
