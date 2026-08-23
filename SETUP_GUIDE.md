---
title: SETUP_GUIDE.md
date created: 2026-08-22 15:00:00
date modified: 2026-08-23 08:02:11
tags: setup, guide, installation, configuration, deployment, evelyn
---

# Evelyn Engine — Full Setup & Installation Guide

> Navigation: [[README.md]] · [[REQUIREMENTS.md]] · [[engine_architecture.md]] · [[start-services.md]] · [[REQUIREMENTS_IMAGE_HOST.md]]

This guide walks through deploying the **Evelyn Engine** on a fresh Linux system (Ubuntu / Debian / Arch Linux), including OS prerequisites, local LLM runtime, Python dependencies, the interactive setup wizard, and system services.

---

## 1. Prerequisites & System Packages

### Linux Operating System
The Evelyn Engine is optimized for modern Linux distributions with `systemd`.

### Ubuntu / Debian
```bash
sudo apt update && sudo apt install -y \
    python3 \
    python3-venv \
    python3-pip \
    sqlite3 \
    git \
    curl \
    build-essential \
    ffmpeg
```

### Arch Linux
```bash
sudo pacman -Syu --noconfirm \
    python \
    python-pip \
    sqlite \
    git \
    curl \
    base-devel \
    ffmpeg
```

---

## 2. Supporting Applications & Services

### A. Ollama (Local LLM Inference Server)
The Evelyn Engine relies on Ollama for all conversational inference, fact extraction, and reasoning tasks.

1. **Install Ollama**:
   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ```
2. **Start and Enable Ollama Service**:
   ```bash
   sudo systemctl enable --now ollama
   ```
3. **Pull Core Models by Hardware Tier**:
   - **Light Tier (8 GB VRAM)**: `ollama pull qwen2.5:7b-instruct`
   - **Standard Tier (16–24 GB VRAM)**: `ollama pull qwen2.5:14b-instruct`
   - **Power Tier (32+ GB VRAM)**: `ollama pull qwen2.5:32b-instruct`

4. **Pull Embedding Model** (for fast local vector RAG):
   ```bash
   ollama pull nomic-embed-text
   ```

### B. Obsidian (Optional — Knowledge Base UI)
Evelyn stores memory, journals, and extracted facts as plain Markdown notes inside an Obsidian Vault.
- Download and install Obsidian from [obsidian.md](https://obsidian.md).
- Create or open a local vault directory (e.g. `~/obsidian_vault`).

---

## 3. Project Setup & Python Environment

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/Rathius-Saranoth/Evelyn-Engine.git ~/evelyn
   cd ~/evelyn
   ```

2. **Create Python Virtual Environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

---

## 4. Run the Setup Wizard

The interactive wizard configures persona identities, creates vault directory scaffolding, updates `evelyn_config.py`, and deploys starter markdown templates.

```bash
python evelyn_setup.py
```

### Wizard Prompts
1. **Assistant Name**: Custom name for the companion (default: `Evelyn`).
2. **Operator / User Name**: Your preferred user name (default: `Ricky` or `Operator`).
3. **Obsidian Vault Path**: Absolute path to your vault (default: `~/obsidian_vault`).
4. **Deploy Starter Templates**: Copies structured starter notes (`Assistant Narrative Persona.md`, `User Narrative Profile.md`, and `System Directives.md`) directly into your vault.

*(For non-interactive / automated provisioning, run `python evelyn_setup.py --defaults`)*.

---

## 5. Starting the Evelyn Engine

### Method 1: Foreground / Script Runner
```bash
./scripts/start_evelyn_services.sh
```

### Method 2: Systemd Services (Recommended for Always-On Companions)
Create `/etc/systemd/system/evelyn.service`:
```ini
[Unit]
Description=Evelyn Engine Core FastAPI Server
After=network.target ollama.service

[Service]
Type=simple
User=rathius
WorkingDirectory=/home/rathius/evelyn
Environment="PYTHONPATH=/home/rathius/evelyn"
ExecStart=/home/rathius/evelyn/venv/bin/python evelyn_server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now evelyn
```

---

## 6. Accessing the Dashboard & Web UI

Once started, the engine provides two web interfaces:
- **Chat Interface**: `http://localhost:8000/ui/index.html` (or `http://localhost:8000/`)
- **Triage & Developer Dashboard**: `http://localhost:8000/ui/dev.html`

### API Authentication
The server is protected by thin API authentication. Pass your configured `EVELYN_API_KEY` (set in `evelyn_config.py` or environment variable) in the `X-Evelyn-Key` header, or input it when prompted by the web UI.

---

## 7. Verifying the Installation

Run the automated test suite to ensure all subsystems, tools, and vector indexes are operating properly:
```bash
PYTHONPATH=. /home/rathius/evelyn/venv/bin/pytest Evelyn/tests/
```
All 78 unit and integration tests should pass.
