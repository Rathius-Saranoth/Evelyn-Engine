---
title: SETUP_GUIDE.md
date created: 2026-08-22 15:00:00
date modified: 2026-10-09 12:09:40
tags: [setup, guide, installation, configuration, deployment, bare-metal, sanctum, evelyn]
---

# Evelyn Engine — Full Setup & Bare-Metal Installation Guide

> Navigation: [[README.md]] · [[REQUIREMENTS.md]] · [[engine_architecture.md]] · [[system_specs.md]] · [[HPE Server Specs.md]] · [[start-services.md]]

This guide provides end-to-end instructions for deploying the **Evelyn Engine** on dedicated enterprise bare-metal hardware (**Sanctum** — HPE ProLiant DL360 Gen10) running **Ubuntu Server 24.04 LTS (Noble Numbat)**, as well as desktop development environments.

It covers bare-metal drive partitioning, OS prerequisites, NUMA domain tuning, Ollama configuration, multi-virtual environment isolation, Syncthing mesh networking, safe state migration, configuration scaling, and canonical systemd management.

---

## 1. Hardware Architecture & Storage Partitioning

Sanctum is configured as a 24/7 dedicated production companion host. The hardware profile is optimized for dual-socket compute, dedicated GPU acceleration, and failure-isolated storage.

### Physical Hardware Profile (Sanctum)
- **Platform**: HPE ProLiant DL360 Gen10 (1U Enterprise Server)
- **CPUs**: 2x Intel(R) Xeon(R) Gold 5220R @ 2.20 GHz (48 Cores / 96 Threads total, 36.6 MB L3 Cache)
- **System RAM**: 192 GB DDR4-2666 ECC RDIMMs (24x 8 GB fully populated, Advanced ECC AMP Mode)
- **GPU Accelerator**: NVIDIA Tesla T4 (16 GB GDDR6 VRAM, PCIe Slot 1 wired to CPU Socket 0 / NUMA Node 0)
- **Storage Drives**: 2x WD Blue SA510 2.5" 1000GB SATA SSDs (Front Bays 1 & 2)
- **Out-of-Band Management**: HPE iLO 5 (System ROM `U32 v3.66`, iLO Firmware `3.20`)
- **Host Networking**: Static LAN IP (`192.168.1.X`), Tailscale Mesh IP (`100.X.Y.Z`)

### Storage Role Allocation & Partitioning Schema

| Drive | Slot / Bay | Raw Capacity | Filesystem / Volume Layout | Role & Mount Point |
| :--- | :--- | :--- | :--- | :--- |
| **`sda`** | Bay 1 | ~1,000 GB | **LVM on GPT**:<br>• `sda1`: 1 GB EFI (`/boot/efi`, vfat)<br>• `sda2`: 2 GB Boot (`/boot`, ext4)<br>• `sda3`: LVM Physical Volume (`vg_sanctum`):<br>&nbsp;&nbsp;- `lv_root`: 120 GB ext4 (`/`)<br>&nbsp;&nbsp;- `lv_swap`: 32 GB swap<br>&nbsp;&nbsp;- **Unallocated Pool**: ~776 GB free | **Operating System & Active Runtime**<br>Houses Ubuntu Server 24.04 LTS, Ollama models, Python environments, and active database operations. Free LVM pool provides zero-downtime volume expansion. |
| **`sdb`** | Bay 2 | ~1,000 GB | **Standard GPT**:<br>• `sdb1`: ~1,000 GB ext4 mounted at `/data` via `/etc/fstab` | **Dedicated Backups & Media**<br>Isolated volume for automated nightly database backups, Chroma vector snapshots, persistent audio logs, and media assets. |

---

## 2. Operating System Prerequisites & Host Preparation

### Linux Operating System & Systemd Lingering
The production server runs **Ubuntu Server 24.04 LTS (Noble Numbat)**. To ensure always-on background daemons (`syncthing`, `evelyn-vault-watcher`) survive terminal disconnections and reboots without an active user session, enable persistent systemd lingering:

```bash
sudo loginctl enable-linger $USER
```

### Base System Packages
Install foundational build tools, runtime libraries, audio decoders, and NUMA inspection utilities:

```bash
sudo apt update && sudo apt install -y \
    build-essential \
    python3 \
    python3-venv \
    python3.12-venv \
    python3-pip \
    sqlite3 \
    git \
    curl \
    ffmpeg \
    numactl \
    hwloc
```

> [!NOTE]
> On Ubuntu 24.04, the `numastat` binary is included directly inside the `numactl` package; do not attempt to install `numastat` as a standalone apt package.

### Headless NVIDIA Enterprise Drivers
For headless enterprise accelerators (Tesla T4, Turing TU104), install the proprietary server driver. Avoid `-open` kernel module packages which are incompatible with Turing architecture:

```bash
# Install proprietary headless enterprise driver and utilities
sudo apt install -y nvidia-headless-550-server nvidia-utils-550-server

# Reboot to initialize kernel modules (or reload nvidia modules)
sudo reboot

# Verify driver initialization and 16 GB VRAM detection
nvidia-smi
```

### Tailscale Mesh Networking
Evelyn relies on Tailscale for secure, encrypted peer-to-peer access across workstations and mobile devices:

```bash
# Install Tailscale
curl -fsSL https://tailscale.com/install.sh | sh

# Authenticate and join the mesh network
sudo tailscale up --hostname=<SERVER_HOSTNAME>

# Verify assigned Tailscale IP (e.g. 100.X.Y.Z)
tailscale ip -4
```

---

## 3. Ollama Runtime & NUMA Optimization

Ollama powers Evelyn's primary conversational reasoning, fact extraction, and vector embedding pipelines.

### 1. Install Ollama
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### 2. NUMA Node 0 Binding (Systemd Drop-In)
The Tesla T4 sits in PCIe Slot 1, electrically routed to **CPU Socket 0 (NUMA Node 0)**. Binding Ollama to Node 0 eliminates Ultra Path Interconnect (UPI) bus cross-socket traversal, reducing memory latency for model weights and KV caches.

Create `/etc/systemd/system/ollama.service.d/override.conf`:
```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo tee /etc/systemd/system/ollama.service.d/override.conf << 'EOF'
[Service]
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
Environment="OLLAMA_NUM_PARALLEL=1"
ExecStart=
ExecStart=/usr/bin/numactl --cpunodebind=0 --membind=0 /usr/local/bin/ollama serve
EOF
```

Reload systemd and start Ollama:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ollama
```

### 3. Pull Core Models
```bash
# Primary conversational & tool-calling model (12B parameter Q4_0 QAT, ~7.6 GB VRAM)
ollama pull gemma4:12b

# Fast vector embedding model for ChromaDB RAG (~274 MB)
ollama pull nomic-embed-text
```

---

## 4. Multi-Virtual Environment Architecture

To prevent dependency version drift, CUDA library collisions, and package conflicts between PyTorch/TTS/Whisper and core web frameworks, Evelyn uses decoupled virtual environments.

```
/home/rathius/evelyn/
├── venv/                     # Core Engine Virtual Environment (FastAPI, ChromaDB, PyMuPDF)
├── services/
│   ├── tts/
│   │   └── venv/             # Chatterbox TTS Virtual Environment (CUDA PyTorch, F5-TTS)
│   └── stt/                  # Faster-Whisper STT Server (runs in core venv or dedicated venv)
```

### 1. Clone Repository
```bash
git clone https://github.com/Rathius-Saranoth/Evelyn-Engine.git /home/rathius/evelyn
cd /home/rathius/evelyn
```

### 2. Core Engine Virtual Environment
```bash
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
```

### 3. Chatterbox TTS Virtual Environment
```bash
python3 -m venv services/tts/venv
services/tts/venv/bin/pip install --upgrade pip
services/tts/venv/bin/pip install -r services/tts/requirements.txt
```

---

## 5. Multi-Device Obsidian Sync (Syncthing Mesh over Tailscale)

Evelyn stores memory, daily journals, and extracted facts as plain Markdown notes inside an Obsidian Vault. Synchronizing this vault across desktop, mobile, and server nodes occurs via **Syncthing** over **Tailscale**.

### 1. Install & Enable Syncthing on Sanctum
```bash
sudo apt install -y syncthing
systemctl --user daemon-reload
systemctl --user enable --now syncthing
```

### 2. Configure Headless Web GUI Access
By default, Syncthing binds its GUI strictly to `127.0.0.1:8384`. Update the configuration to bind to `0.0.0.0:8384` so the interface is accessible via LAN (`<SERVER_LAN_IP>:8384`) or Tailscale (`<SERVER_TAILSCALE_IP>:8384`):

```bash
sed -i 's/<address>127.0.0.1:8384<\/address>/<address>0.0.0.0:8384<\/address>/' ~/.local/state/syncthing/config.xml
systemctl --user restart syncthing
```

### 3. Pre-Populate `.stignore` Before Pairing Devices

> [!CAUTION]
> **Mandatory Pre-Sync Step**: You **must** create `/home/rathius/obsidian_vault` and populate `.stignore` **BEFORE** adding remote devices or sharing folders in Syncthing. Failing to do this causes mobile and desktop workspace caches, temporary sync locks, and window states to flood into the vault, triggering cascade re-index loops in Chroma.

Create the vault root and populate `.stignore`:
```bash
mkdir -p /home/rathius/obsidian_vault
cat << 'EOF' > /home/rathius/obsidian_vault/.stignore
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
EOF
```

### 4. Deploy Vault Watcher Service (`evelyn-vault-watcher.service`)
`obsidian_vault_watcher.py` monitors `/home/rathius/obsidian_vault` via inotify, debounces writes (4.0s), and automatically updates SQLite (`evelyn_vault.db`) and ChromaDB vectors:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/evelyn-vault-watcher.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now evelyn-vault-watcher
```

---

## 6. Safe State Migration Procedure (Workstation -> Sanctum)

When migrating active databases and vector embeddings from a previous host (e.g. source development workstation) to Sanctum, strict procedural hygiene is required to avoid corrupting Chroma's single-writer lease or SQLite WAL buffers.

### Step 1: Graceful Shutdown on Source Host
On the source machine, run the canonical stop script with full WAL checkpointing:
```bash
# Execute on source workstation
./scripts/stop_evelyn_services.sh --all --checkpoint-wal
```

Ensure the terminal confirms:
```text
  ✓ Graceful shutdown confirmed (Chroma queue drained).
  ✓ SQLite WAL checkpoint complete.
```

### Step 2: Synchronize Data & Environment Over Tailscale
Transfer the validated database directory and environment file to Sanctum:
```bash
# Execute on source machine
rsync -avzP --delete /home/rathius/evelyn/data/ <USER>@<SERVER_TAILSCALE_IP>:/home/rathius/evelyn/data/
rsync -avzP /home/rathius/evelyn/.env <USER>@<SERVER_TAILSCALE_IP>:/home/rathius/evelyn/.env
rsync -avzP /home/rathius/evelyn/Evelyn/persona/ <USER>@<SERVER_TAILSCALE_IP>:/home/rathius/evelyn/Evelyn/persona/
```

### Step 3: Verify Database & Vector Store Integrity on Sanctum
On Sanctum, verify that all SQLite databases arrived intact:
```bash
for db in /home/rathius/evelyn/data/*.db; do
    echo -n "$db: "
    sqlite3 "$db" "PRAGMA integrity_check;"
done
```

If ChromaDB collections require initial index reconciliation before starting `evelyn.service`:
```bash
# Ensure evelyn.service is stopped before executing direct Chroma rebuilds
PYTHONPATH=. /home/rathius/evelyn/venv/bin/python scripts/rebuild_chroma_collection.py --execute
```

---

## 7. Engine Configuration Scaling (Power Tier on Sanctum)

With 192 GB of system RAM and a dedicated Tesla T4, Sanctum runs at the **Power Tier**. Ensure `evelyn_config.py` (or `.env`) reflects these scaled parameters:

```python
# Context window: 32K active tokens (KV cache fits easily in Tesla T4 VRAM)
NUM_CTX = 32768

# Conversational memory: 20 user/assistant turns
MAX_HISTORY_MESSAGES = 40

# RAG Recall: 8 chunks retrieved per query
RAG_TOP_K = 8

# SQLite / Chroma cache sizing
MMAP_CACHE_SIZE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB mmap
PAGE_CACHE_SIZE_KB = 65536                      # 64 MB page cache

# Voice generation: CUDA mode for sub-0.4 Real-Time Factor (RTF)
TTS_DEVICE = "cuda"
```

> [!NOTE]
> **Environment Variable Precedence**: When copying `.env` from a CPU-only workstation, ensure `EVELYN_TTS_DEVICE=cuda` is set in `.env` (or removed from `.env` so `systemd/evelyn-tts.service`'s `Environment="EVELYN_TTS_DEVICE=cuda"` takes effect). If `.env` explicitly contains `EVELYN_TTS_DEVICE=cpu`, python-dotenv will override systemd.

---

## 8. Deploying & Managing Systemd Services

Evelyn provides canonical unit templates in `systemd/` for core services.

### Service Overview & Port Mapping

| Service Unit | Execution File | Port | NUMA Affinity | Description |
| :--- | :--- | :--- | :--- | :--- |
| `ollama.service` | `/usr/local/bin/ollama` | `11434` | Node 0 (`0-23, 48-71`) | Ollama LLM Inference Server |
| `evelyn.service` | `evelyn_server.py` | `7860` | Node 0 (`0-23, 48-71`) | Evelyn AI Core FastAPI Server |
| `evelyn-tts.service` | `services/tts/tts_server.py` | `5050` | Node 0 (`0-23, 48-71`) | Chatterbox Turbo TTS (CUDA Mode) |
| `evelyn-stt.service` | `services/stt/stt_server.py` | `5060` | Node 1 (`24-47, 72-95`) | Faster-Whisper STT (CPU int8 Mode) |
| `syncthing.service` | `/usr/bin/syncthing` | `8384` | Default | P2P Vault Mesh Sync (User unit) |
| `evelyn-vault-watcher.service` | `scripts/obsidian_vault_watcher.py` | — | Default | Real-Time Vault Ingestion (User unit) |

### 1. Install Systemd Units
```bash
# Copy system services
sudo cp systemd/evelyn.service systemd/evelyn-tts.service systemd/evelyn-stt.service /etc/systemd/system/

# Copy user services
mkdir -p ~/.config/systemd/user
cp systemd/evelyn-vault-watcher.service ~/.config/systemd/user/

# Reload systemd daemons
sudo systemctl daemon-reload
systemctl --user daemon-reload

# Enable services for automated boot start
sudo systemctl enable evelyn evelyn-tts evelyn-stt
systemctl --user enable evelyn-vault-watcher
```

### 2. Service Lifecycle Management (Mandatory Scripts)

> [!WARNING]
> **Graceful Shutdown Is Mandatory**: Never invoke bare `sudo systemctl restart evelyn` or `sudo systemctl stop evelyn`.
> Evelyn's Chroma custodian holds a single-writer lease for its entire lifetime. A bare systemctl stop or restart kills the process before uvicorn completes its lifespan shutdown, skipping the Chroma write queue drain and risking segment corruption.
> Always manage services using the canonical scripts:

```bash
# Start all services cleanly:
./scripts/start_evelyn_services.sh

# Safely restart services (verifies Chroma drain and checkpoints WAL):
./scripts/restart_evelyn_services.sh

# Restart all services including Ollama:
./scripts/restart_evelyn_services.sh --all

# Gracefully stop services:
./scripts/stop_evelyn_services.sh

# Full stop with SQLite WAL checkpointing:
./scripts/stop_evelyn_services.sh --all --checkpoint-wal
```

---

## 9. Verification & Health Probes

### 1. Verify Active Port Bindings
```bash
ss -tulpn | grep -E ':(11434|5050|5060|7860|8384)'
```

### 2. Comprehensive Engine Status Probe
Run the bundled ecosystem diagnostic script:
```bash
./scripts/check_evelyn_status.sh
```

### 3. Direct Health Endpoint Checks
```bash
# Core Server Status Probe
API_KEY=$(grep -oP '(?<=EVELYN_API_KEY=)[^\"]+' .env 2>/dev/null || echo "${EVELYN_API_KEY}")
curl -sk -H "X-Evelyn-Key: ${API_KEY}" https://localhost:7860/status

# Chatterbox TTS Health
curl http://localhost:5050/health

# Faster-Whisper STT Health
curl http://localhost:5060/health
```

### 4. Code Hygiene & Test Verification
Run targeted test suites to confirm that tools, vector indexes, and AST wiring are verified:
```bash
# Verify all tools end-to-end
PYTHONPATH=. /home/rathius/evelyn/venv/bin/pytest Evelyn/tests/test_all_tools_end_to_end.py

# Verify code hygiene gate (5 stages: compile, ruff, AST wiring, vulture, privacy)
PYTHONPATH=. /home/rathius/evelyn/venv/bin/python scripts/check_code_hygiene.py
```

---

## 10. Security, TLS/SSL Certificates & HTTPS Provisioning

While internal communication over Tailscale is encrypted at the WireGuard network layer, **HTTPS is mandatory** for full client browser functionality:
- **Microphone Access**: Modern browsers (Chrome, Edge, Safari, Firefox on mobile and desktop) strictly disable the Web Audio API and `getUserMedia` on insecure HTTP origins when connecting from non-localhost IPs (e.g. LAN or Tailscale IPs). Voice chat and STT transcription will fail without HTTPS.

`evelyn_server.py` checks for `server.crt` and `server.key` at startup. If present, it binds port 7860 over TLS automatically.

### Method A: Self-Signed Multi-SAN OpenSSL Certificate (Immediate & Universal)
Generate a 10-year certificate covering the server hostname, LAN IP, Tailscale IP, and MagicDNS:

```bash
openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
  -keyout /home/rathius/evelyn/server.key \
  -out /home/rathius/evelyn/server.crt \
  -subj "/CN=<HOSTNAME>" \
  -addext "subjectAltName=DNS:<HOSTNAME>,DNS:<HOSTNAME>.<TAILNET>.ts.net,DNS:localhost,IP:<LAN_IP>,IP:<TAILSCALE_IP>,IP:127.0.0.1"
```

### Method B: Tailscale Native TLS (Let's Encrypt CA)
If HTTPS Certificates are enabled in the Tailscale Admin Console, Tailscale can issue a globally trusted certificate:

```bash
sudo tailscale cert --cert-file /home/rathius/evelyn/server.crt --key-file /home/rathius/evelyn/server.key <HOSTNAME>.<TAILNET>.ts.net
```

### Custom Certificate Paths
If storing certificates outside the project root, specify them via `.env`:
```ini
EVELYN_SSL_CERT=/path/to/server.crt
EVELYN_SSL_KEY=/path/to/server.key
```
