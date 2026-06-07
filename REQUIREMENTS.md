---
title: REQUIREMENTS.md
date created: 2026-05-13 20:27:49
date modified: 2026-06-06 19:06:55
tags: requirements, dependencies, system, hardware, environment
---

# Evelyn Engine — Full System Requirements

> [!IMPORTANT]
> This document covers **all** dependencies — not just Python packages.
> For Python-only installs, see [[requirements.txt]]

---

## 1. Runtime Environment

| Component      | Required  | Tested Version | Notes                                             |
| -------------- | --------- | -------------- | ------------------------------------------------- |
| **Python**     | 3.11+     | 3.11.9         | System install; no venv used for the main project |
| **PowerShell** | 5.1+ / 7+ | PS5.1 / PS7    | Used for startup/wait scripts (wait_for_ollama)   |
| **Windows**    | 10/11     | Windows 11     | Tested platform; Linux untested                   |

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
| `requests` | ≥2.32   | Synchronous HTTP — vault map generator (`generate_vault_map.py`)                                       |

### Tools

| Package            | Version | Purpose                                                                                    |
| ------------------ | ------- | ------------------------------------------------------------------------------------------ |
| `ddgs`             | ≥9.0    | DuckDuckGo search — powers the `web_search` tool                                           |
| `PyMuPDF`          | ≥1.27   | PDF text extraction with font metadata — `extract_pdf_library.py`. Import as `import fitz` |

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



### Obsidian (Optional — Knowledge Base UI)

| Detail         | Value                                          |
| -------------- | ---------------------------------------------- |
| **What**       | Markdown knowledge base — Evelyn's "vault"     |
| **Install**    | https://obsidian.md                            |
| **Vault Path** | `G:\My Drive\Obsidian_Vault`                   |
| **Usage**      | Launched via `Start-Process 'obsidian://open'` |


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

| Component   | Minimum   | Recommended (Current Setup)            |
| ----------- | --------- | -------------------------------------- |
| **GPU**     | 8 GB VRAM | NVIDIA RTX 4070 (12 GB VRAM)           |
| **RAM**     | 16 GB     | 32 GB DDR5-6000                        |
| **CPU**     | 8 cores   | AMD Ryzen 7 7800X3D (8C/16T, 96 MB L3) |
| **Storage** | SSD       | NVMe SSD for project + model weights   |

The 12B parameter dense model (Q4_0 QAT quantization) uses ~7.6 GB and fits 100% in VRAM.
`NUM_CTX=32768` is the active window (KV cache quantized to 8-bit using `OLLAMA_KV_CACHE_TYPE=q8_0` uses ~2.6 GB VRAM).

---

## 6. Directory Structure

```
C:\Projects\LocalAI\             # Project root
├── evelyn_server.py             # Main server (FastAPI)
├── evelyn_config.py             # All configuration
├── requirements.txt             # Python dependencies
├── evelyn_chat.db               # SQLite chat history
├── chroma_db\                   # ChromaDB persistent storage
├── Evelyn\
│   ├── persona\                 # System prompt, directives
│   ├── tools\                   # All Python tools
│   └── workflows\               # ComfyUI workflow JSONs
├── data\                        # SQLite databases (chat, context, vault)
├── evelyn_ui\                   # Chat web UI (HTML + favicon)
└── reference\                   # System specs, benchmarks
```

External data path:
```
G:\My Drive\Obsidian_Vault\      # Obsidian vault (Google Drive synced)
└── Evelyn\                      # Evelyn's memory, journal, context entries
```

---

## 7. Quick Start

```powershell
# 1. Clone the repo
git clone https://github.com/Rathius-Saranoth/Evelyn-Engine.git C:\Projects\LocalAI

# 2. Install Python dependencies
pip install -r requirements.txt # [[python_requirements.md]]

# 3. Install and start Ollama
# Download from https://ollama.com/download
ollama pull gemma4:26b

# 4. Set the API key
$env:EVELYN_API_KEY = "your-secret-key"

# 5. Start Ollama (terminal 1)
$env:OLLAMA_KEEP_ALIVE = "-1"
$env:OLLAMA_FLASH_ATTENTION = "1"
$env:OLLAMA_KV_CACHE_TYPE = "q8_0"
ollama serve

# 6. Start Evelyn (terminal 2)
python evelyn_server.py

# 7. Open in browser
# http://localhost:7860
```
[python_requirements.md]: python_requirements.md "python_requirements.md"
[system_specs.md]: reference/system/system_specs.md "RICKY-PC System Specifications"
