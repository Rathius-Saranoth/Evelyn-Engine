---
title: SETUP_GUIDE.md
date created: 2026-08-22 15:00:00
date modified: 2026-09-11 17:45:52
tags: [setup, guide, installation, configuration, deployment, evelyn]
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
4. **Deploy Starter Templates**: Copies structured starter notes (`Assistant Profile.md`, `User Profile.md`, and `System Directives.md`) directly into your vault.

*(For non-interactive / automated provisioning, run `python evelyn_setup.py --defaults`)*.

---

## 5. Multi-Device Obsidian Sync (Syncthing Mesh over Tailscale)

The Evelyn Engine uses a decentralized peer-to-peer synchronization mesh via **Syncthing** over **Tailscale VPN** to synchronize the Obsidian Vault across desktop, mobile, and host environments without relying on third-party cloud storage.

### A. Topology & Architecture
- **Central Host Node (WSL2 / Linux Server)**: Runs the primary Syncthing daemon alongside the Evelyn Engine. Directly accesses the canonical vault at `/home/rathius/obsidian_vault`.
- **Desktop Workstation Node**: Windows/macOS/Linux running Obsidian Desktop and Syncthing (or SyncTrayzor) syncing to a local vault directory (e.g. `C:\Obsidian Vault`).
- **Mobile Nodes**: Android phone and tablet running Obsidian Mobile paired via Syncthing (or Syncthing-Fork).
- **Tailscale P2P Mesh**: All nodes communicate directly and securely via 100.x.x.x Tailscale CGNAT IPs, bypassing NAT traversal, port forwarding, and firewalls.

### B. Linux / WSL2 Server Setup
1. **Install Syncthing**:
   ```bash
   sudo apt update && sudo apt install -y syncthing
   ```
2. **Configure Port Separation (WSL2 / Multi-Instance)**:
   In WSL2 environments where the Windows host already runs Syncthing on default port `8384`, bind the Linux Syncthing GUI to port `8385` to eliminate localhost port collisions:
   - Config path: `~/.local/state/syncthing/config.xml` (or `~/.config/syncthing/config.xml`)
   - Listen Address: `tcp://0.0.0.0:22000` and `quic://0.0.0.0:22000`
   - GUI Address: `0.0.0.0:8385`
3. **Configure User Lingering & Systemd Service**:
   Enable systemd lingering so the user service runs continuously in the background across terminal sessions:
   ```bash
   sudo loginctl enable-linger $USER
   systemctl --user daemon-reload
   systemctl --user enable --now syncthing
   ```
4. **Access the Web GUI**:
   Open `http://localhost:8385` (or `http://<tailscale-ip>:8385`) in your browser to inspect device pairing, folder sync status, or scan pairing QR codes.

### C. Real-Time Vault Ingestion Watcher (`evelyn-vault-watcher.service`)
Whenever notes, documents, or staging files are dropped into the vault (via Syncthing or direct edits), `obsidian_vault_watcher.py` detects inotify changes, debounces rapid writes (4.0s), and automatically updates SQLite (`evelyn_vault.db`) and ChromaDB vector embeddings.

1. **Deploy Service Unit**:
   ```bash
   mkdir -p ~/.config/systemd/user
   cp systemd/evelyn-vault-watcher.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now evelyn-vault-watcher
   ```
2. **Verify Watcher Status**:
   ```bash
   systemctl --user status evelyn-vault-watcher
   ```

### D. Client Node Configuration

#### 1. Windows Workstation (SyncTrayzor / Syncthing)
- **Local Vault Path**: `C:\Obsidian Vault`
- **Add Remote Device**: In the Windows Syncthing GUI (`http://localhost:8384`), add the Linux/WSL node using its Device ID. Set the address to `tcp://<tailscale-ip>:22000` or `dynamic`.
- **Share Folder**: Share folder ID `obsidian-vault` between both nodes. Set folder type to *Send & Receive*.

#### 2. Mobile Nodes (Android Phone & Tablet)
- Install **Syncthing-Fork** or official Syncthing from F-Droid or Google Play.
- Add the central Linux/WSL node using the Device ID (or scan the Web GUI QR code from `http://<tailscale-ip>:8385`).
- Add the local mobile Obsidian vault folder with Folder ID `obsidian-vault`.
- Enable file watching with run conditions set to run on Tailscale / WiFi.

### E. Recommended Obsidian `.stignore` Patterns
To prevent syncing platform-specific workspace states, mobile layout caches, and temporary sync locks, place the following in `.stignore` at the root of the vault:

```text
// Ignore mobile/desktop workspace layout state and cache
(?d).obsidian/workspace.json
(?d).obsidian/workspace-mobile.json
(?d).obsidian/workspace*
(?d).obsidian/cache
(?d).obsidian/graph.json

// Syncthing & OS temporary/lock files
(?d).stversions
(?d).stfolder
(?d).syncthing.*.tmp
(?d)*.tmp
(?d)*.crswap
(?d).DS_Store
(?d)desktop.ini
(?d)thumbs.db
(?d).trash
```

---

## 6. Starting the Evelyn Engine

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

## 7. Accessing the Dashboard & Web UI

Once started, the engine provides two web interfaces:
- **Chat Interface**: `http://localhost:7860/ui/index.html` (or `http://localhost:7860/`)
- **Triage & Developer Dashboard**: `http://localhost:7860/ui/dev.html`

### API Authentication
The server is protected by thin API authentication. Pass your configured `EVELYN_API_KEY` (set in `evelyn_config.py` or environment variable) in the `X-Evelyn-Key` header, or input it when prompted by the web UI.

---

## 8. Verifying the Installation

Run the automated test suite to ensure all subsystems, tools, and vector indexes are operating properly:
```bash
PYTHONPATH=. /home/rathius/evelyn/venv/bin/pytest Evelyn/tests/
```
All unit and integration tests should pass.
