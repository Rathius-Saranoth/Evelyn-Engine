#!/bin/bash
# Stop all Evelyn services via systemd
sudo systemctl stop evelyn evelyn-tts
echo "Evelyn services stopped."
sudo systemctl status evelyn evelyn-tts --no-pager
