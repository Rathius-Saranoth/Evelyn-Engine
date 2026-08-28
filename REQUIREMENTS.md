---
title: REQUIREMENTS.md
date created: 2026-05-13 20:27:49
date modified: 2026-08-28 16:43:08
tags: [requirements, dependencies, system, hardware, environment, evelyn]
---

# Evelyn Engine — Full System Requirements

> Navigation: [[README.md]] · [[SETUP_GUIDE.md]] · [[system_specs.md]] · [[engine_architecture.md]]

> [!IMPORTANT]
> This document covers **all** dependencies — not just Python packages.
> For Python-only installs, see `requirements.txt` / [[SETUP_GUIDE.md]].

---

## 1. Runtime Environment

| Component   | Required | Tested Version       | Notes                                          |
| ----------- | -------- | -------------------- | ---------------------------------------------- |
| **Python**  | 3.11+    | Python 3.14 (venv)   | Active virtualenv for system services          |
| **Linux**   | 6.x      | Arch Linux (x86_64) | Tested production platform (`sanctum`)          |

---

## 2. Python Packages

Install all at once:
```
pip install -r requirements.txt
```

### Core Server (`evelyn_server.py`)

| Package    | Version | Purpose                                                                              |
| ---------- | ------- | ------------------------------------------------------------------------------------ |
| `fastapi`  | ≥0.135  | ASGI web framework — API endpoints, SSE streaming, static file serving               |
| `uvicorn`  | ≥0.41   | ASGI server — runs the FastAPI application                                           |
| `httpx`    | ≥0.28   | Async HTTP client — all Ollama API calls (chat, summarizer, extractor, consolidator) |
| `pydantic` | ≥2.12   | Data validation — request/response models                                            |

### RAG Pipeline (`chroma_rag.py`, `ingest_*.py`)

| Package                 | Version | Purpose                                                                                                                                   |
| ----------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `chromadb`              | ≥1.5    | Vector database with HNSW index and cosine distance. Includes built-in ONNX runtime for `all-MiniLM-L6-v2` embeddings (CPU, ~100ms/query) |
| `sentence-transformers` | ≥5.0    | **Optional** — only needed for `benchmark_rag.py --compare` model evaluation. Not required at runtime                                     |

### LLM Integration & Data Parsing

| Package    | Version | Purpose                                                                                                |
| ---------- | ------- | ------------------------------------------------------------------------------------------------------ |
| `PyYAML`   | ≥6.0    | YAML parsing — fact extractor output, consolidator proposals, pending reviewer, frontmatter processing |
| `requests` | ≥2.32   | Synchronous HTTP — vault indexer (`vault_indexer.py`)                                                  |

### Tools & Workspace Sync

| Package                     | Version | Purpose                                                                                    |
| --------------------------- | ------- | ------------------------------------------------------------------------------------------ |
| `ddgs`                      | ≥9.0    | DuckDuckGo search — powers the `web_search` tool                                           |
| `PyMuPDF`                   | ≥1.27   | PDF text extraction and page rendering — `extract_pdf_library.py`, `document_vision_processor.py` |
| `watchdog`                  | ≥6.0    | Debounced filesystem watcher — `scripts/obsidian_vault_watcher.py`                         |
| `beautifulsoup4`            | ≥4.13   | HTML DOM parser & asset cleaner — `gdrive_knowledge_importer.py`                           |
| `markdownify`               | ≥1.2    | HTML to Obsidian Markdown conversion with table & image preservation                       |
| `html2text`                 | ≥2024.2 | Fallback HTML to clean text/markdown converter                                             |
| `google-api-python-client`  | ≥2.100  | Google Drive, Docs, Sheets & Tasks API client — `gdrive_sync.py`, `gdrive_knowledge_importer.py` |
| `google-auth-oauthlib`      | ≥1.2    | Google OAuth 2.0 InstalledAppFlow client credential management                             |
| `google-auth-httplib2`      | ≥0.2    | Google HTTP transport authentication layer                                                 |
| `mcp`                       | ≥2.0    | Model Context Protocol SDK for workspace database and vector inspection — `sqlite_mcp_server.py` |

### Standard Library (No Install Needed)

These are used extensively but ship with Python:

`asyncio`, `argparse`, `collections`, `contextlib`, `dataclasses`, `datetime`, `glob`, `hashlib`, `importlib`, `json`, `os`, `pathlib`, `re`, `sqlite3`, `subprocess`, `sys`, `threading`, `time`, `urllib`, `uuid`

---

## 3. External Services

> [!IMPORTANT]
> These are separate applications that run alongside the Evelyn server.
> They are started via the VS Code task runner (`Start Evelyn Services`) or manually.

### Ollama (Required)

| Detail       | Value                                                                           |
| ------------ | ------------------------------------------------------------------------------- |
| **What**     | Local LLM inference server                                                      |
| **Version**  | ≥0.20.3 (tested: 0.23.1)                                                        |
| **Install**  | https://ollama.com/download                                                     |
| **Model**    | `gemma4:12b` (active), `gemma4:26b` (supported), `magistral:24b` (fallback)      |
| **Startup**  | `ollama serve`                                                                  |
| **Env Vars** | `OLLAMA_KEEP_ALIVE=-1`, `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0` |

Pull the active model after installation:
```
ollama pull gemma4:12b
```

### Tailscale (Optional — Remote Access)

| Detail      | Value                                                                  |
| ----------- | ---------------------------------------------------------------------- |
| **What**    | Mesh VPN for secure remote access to Evelyn from mobile devices        |
| **Install** | https://tailscale.com/download                                         |
| **Usage**   | `tailscale serve --bg 8080` — exposes the Evelyn server over Tailscale |

### Image Generation Microservice (Optional — FLUX.1 Schnell NF4)

| Detail         | Value                                                                             |
| -------------- | --------------------------------------------------------------------------------- |
| **What**       | Standalone FastAPI microservice running FLUX.1 [schnell] NF4 text-to-image pipeline |
| **Hardware**   | Dedicated NVIDIA GPU with ≥ 12 GB VRAM (RTX 4070 / RTX 3080 or better)             |
| **Model**      | `magespace/FLUX.1-schnell-bnb-nf4` (4-step distilled schnell with bitsandbytes NF4) |
| **Location**   | `services/image/` (`services/image/image_server.py`)                              |
| **Restoration**| See [[REQUIREMENTS_IMAGE_HOST.md]] for full setup, GPU drivers, and firewall guide|
| **Startup**    | `python services/image/image_server.py` or `./scripts/start_image_server.sh`      |

### Obsidian (Optional — Knowledge Base UI)

| Detail         | Value                                     |
| -------------- | ----------------------------------------- |
| **What**       | Markdown knowledge base — Evelyn's "vault"|
| **Install**    | https://obsidian.md                       |
| **Vault Path** | `/home/rathius/obsidian_vault`            |

---

## 4. Environment Variables

| Variable                 | Required    | Purpose                                                                            |
| ------------------------ | ----------- | ---------------------------------------------------------------------------------- |
| `EVELYN_API_KEY`         | Yes         | API key for thin auth on all endpoints. Set in system env or pass via VS Code task |
| `OLLAMA_KEEP_ALIVE`      | Recommended | Set to `-1` to keep the model loaded permanently (avoids cold-start penalty)       |
| `OLLAMA_FLASH_ATTENTION` | Recommended | Set to `1` for faster inference on supported GPUs                                  |
| `OLLAMA_KV_CACHE_TYPE`   | Recommended | Set to `q8_0` for quantized KV cache (saves VRAM)                                  |

---

## 5. Hardware Recommendations

> [!NOTE]
> See [[system_specs.md]] for the full hardware analysis.

| Component   | Minimum   | Recommended (Current Setup)                  |
| ----------- | --------- | -------------------------------------------- |
| **GPU**     | 8 GB VRAM | NVIDIA Tesla T4 (16 GB GDDR6 VRAM)           |
| **RAM**     | 16 GB     | 192 GB DDR4-2666 ECC                         |
| **CPU**     | 8 cores   | 2x Intel Xeon Gold 5220R (48C/96T, 36.6 MB L3)|
| **Storage** | SSD       | Enterprise SATA SSDs (`/` and `/data`)       |

The 12B parameter dense model (Q4_0 QAT quantization) uses ~7.6 GB and fits 100% in VRAM.
`NUM_CTX=32768` is the active window (KV cache quantized to 8-bit using `OLLAMA_KV_CACHE_TYPE=q8_0` uses ~2.6 GB VRAM).

---

## 6. Directory Structure

```
/home/rathius/evelyn/            # Project root
├── evelyn_server.py             # Main server (FastAPI)
├── evelyn_config.py             # All configuration
├── requirements.txt             # Python dependencies
├── Evelyn/
│   ├── persona/                 # System prompt, directives
│   └── tools/                   # All Python tools
├── data/                        # Persistent databases & indexes
│   ├── evelyn_chat.db           # SQLite chat history
│   ├── evelyn_memory.db         # SQLite memory database
│   ├── evelyn_vault.db          # SQLite vault database
│   └── chroma_db/               # ChromaDB persistent storage
├── evelyn_ui/                   # Chat web UI (HTML + vendor assets + favicon)
│   ├── dev.html                 # Developer & review dashboard
│   ├── index.html               # Main Chat UI
│   └── vendor/                  # Vendored client-side JS (marked.min.js, purify.min.js)
├── reference/                   # System specs, benchmarks
```

External data path:
```
/home/rathius/obsidian_vault/    # Obsidian vault
└── Evelyn/                      # Evelyn's memory, journal, context entries
```

---

## 7. Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/Rathius-Saranoth/Evelyn-Engine.git /home/rathius/evelyn

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Pull active LLM model
ollama pull gemma4:12b

# 4. Set environment variables
export EVELYN_API_KEY="your-secret-key"
export OLLAMA_KEEP_ALIVE="-1"
export OLLAMA_FLASH_ATTENTION="1"
export OLLAMA_KV_CACHE_TYPE="q8_0"

# 5. Start systemd services
sudo systemctl start ollama evelyn evelyn-tts

# 6. Open in browser
# http://localhost:7860
```
[system_specs.md]: reference/system/system_specs.md "Sanctum System Specifications"
