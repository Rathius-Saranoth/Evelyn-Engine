#!/usr/bin/env bash
# check_evelyn_status.sh — Comprehensive Evelyn System & NUMA Diagnostics for Linux Hosts
# date created: 2026-08-11

echo "=========================================="
echo "      Evelyn System Diagnostics (Linux)   "
echo "=========================================="

ALL_CLEAR=true

# 1. Check Ollama
if pgrep -x "ollama" > /dev/null || systemctl is-active --quiet ollama; then
    echo -e "\033[0;32m✅ [Ollama] Service is running (NUMA Node 0).\033[0m"
else
    echo -e "\033[0;31m❌ [Ollama] Service is NOT running.\033[0m"
    ALL_CLEAR=false
fi

# 2. Check Tailscale
if systemctl is-active --quiet tailscaled || pgrep -x "tailscaled" > /dev/null; then
    echo -e "\033[0;32m✅ [Tailscale] Proxy daemon is running.\033[0m"
else
    echo -e "\033[0;31m❌ [Tailscale] Proxy daemon is NOT running.\033[0m"
    ALL_CLEAR=false
fi

# 3. Check Chatterbox TTS Server
TTS_PORT=5050
if ss -tuln | grep -q ":$TTS_PORT "; then
    echo -e "\033[0;32m✅ [Chatterbox TTS] Server is listening on port $TTS_PORT (NUMA Node 1).\033[0m"
else
    echo -e "\033[0;31m❌ [Chatterbox TTS] Server is NOT running on port $TTS_PORT.\033[0m"
    ALL_CLEAR=false
fi

# 4. Check Evelyn AI Core Server
EVELYN_PORT=7860
if ss -tuln | grep -q ":$EVELYN_PORT "; then
    echo -e "\033[0;32m✅ [Evelyn Server] Core backend is listening on port $EVELYN_PORT (NUMA Node 0).\033[0m"
else
    echo -e "\033[0;31m❌ [Evelyn Server] Core backend is NOT running on port $EVELYN_PORT.\033[0m"
    ALL_CLEAR=false
fi

# 5. Check Remote FLUX Image Server on image-host
IMAGE_URL="http://image-host.internal.net:5055/health"
if curl -s --max-time 3 "$IMAGE_URL" | grep -q "ok" > /dev/null 2>&1; then
    echo -e "\033[0;32m✅ [Remote Image Host] FLUX.1 server reachable on image-host:5055.\033[0m"
else
    echo -e "\033[0;33m⚠️  [Remote Image Host] FLUX.1 server on image-host:5055 unreachable or offline.\033[0m"
fi

echo "------------------------------------------"
echo "  Hardware & NUMA Locality Summary        "
echo "------------------------------------------"

if command -v nvidia-smi &> /dev/null; then
    GPU_INFO=$(nvidia-smi --query-gpu=gpu_name,memory.used,memory.total,temperature.gpu,utilization.gpu --format=csv,noheader 2>/dev/null)
    echo "  GPU: $GPU_INFO"
fi

if command -v numastat &> /dev/null; then
    echo ""
    echo "  NUMA Process Memory Allocation (MB):"
    numastat -c ollama evelyn 2>/dev/null | head -n 10
fi

echo "=========================================="
if [ "$ALL_CLEAR" = true ]; then
    echo -e "\033[0;32mAll core systems operational on Sanctum!\033[0m"
else
    echo -e "\033[0;33mSome services offline. Run: sudo systemctl restart evelyn evelyn-tts ollama\033[0m"
fi
echo "=========================================="
