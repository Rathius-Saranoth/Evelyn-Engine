---
title: README.md
tags: [system/engine]
date created: 2026-08-28 14:41:00
date modified: 2026-08-29 12:56:01
---
> [!NOTE]
> **Project Status: Personal / As-Is**  
> This project is tailored to personal workflows and hardware configurations. It is shared publicly as an architectural reference and portfolio project, not as a managed open-source product. No official support, feature requests, or troubleshooting are provided.

# 🌌 Evelyn Engine

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.135%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![Ollama](https://img.shields.io/badge/Inference-Ollama-black.svg)](https://ollama.com/)
[![ChromaDB](https://img.shields.io/badge/Vector%20DB-ChromaDB-orange.svg)](https://www.trychroma.com/)
[![Obsidian](https://img.shields.io/badge/Knowledge%20Base-Obsidian-purple.svg)](https://obsidian.md/)
[![AI-Generated & Human-Architected](https://img.shields.io/badge/Code-AI%20Created%20%7C%20Human%20Architected-blueviolet.svg)](#-project-origins--ai-collaboration)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

> **A sovereign, privacy-first, locally-hosted AI companion and autonomous cognitive assistant.**  
> Built for deep memory retention, bidirectional Obsidian vault synchronization, autonomous research, and multi-modal intelligence — completely offline and on your own hardware.
>
> *Heavily AI-created through iterative agentic pair programming, guided by human system architecture, hardware profiling, and code review.*

---

## ✨ Features at a Glance

### 🧠 Persistent Long-Term Memory & Evolving Persona
- **Multi-Tier Memory Architecture**: Combines active conversation sliding windows, high-speed SQLite context tables, and ChromaDB vector search (`BAAI/bge-large-en-v1.5` embeddings).
- **Autonomous Idle-Time Maintenance**: While you sleep or take a break, Evelyn's background workers extract facts, consolidate redundant observations, resolve conflicts, and evolve persona directives based on your conversations.
- **Dynamic Thinking & Reasoning**: Multi-tiered thinking effort control (fast pre-classification, tool-driven escalation, and UI overrides) for complex problem-solving.

### 📓 Native Obsidian Vault Synchronization
- **Bidirectional Knowledge Loop**: Watches your Obsidian markdown vault in real-time with an inotify watchdog and updates vector indexes automatically.
- **Direct Note & Journal Management**: Automatically logs daily reflections, structures reference summaries, and indexes your personal notes without third-party cloud lock-in.

### 🔬 Autonomous Deep Research Engine
- **Self-Directed Research Subprocess**: Formulates search plans, crawls the web with Trafilatura, extracts multi-source evidence with discovered technical aliases, and synthesizes 5-part reference guides directly into Obsidian.
- **Circadian Awareness & Safety**: Respects active hours, pauses cleanly at step boundaries, and coordinates under a centralized mutex registry to prevent CPU/GPU thrashing.

### 🎙️ Multimodal & Real-Time Voice
- **Expressive Local TTS**: High-speed, natural speech synthesis powered by Chatterbox (isolated on dedicated NUMA sockets).
- **Visual Memory & Attachments**: Local vision pipeline extracts OCR, image captions, and EXIF metadata to vectorize media attachments.
- **Image Generation**: Seamlessly bridges to local or network FLUX image generators.

### 🛠️ Agentic Tools & Personal Health Intelligence
- **Vitals & Health Tracking**: Integrates Oura Ring API v2 sleep/readiness metrics with Android Health Connect exports for holistic health awareness.
- **Calendar & Daily Agenda**: Synchronizes with Google Calendar for real-time agenda awareness, meeting reminders, and event scheduling.
- **Guarded Terminal & File Operations**: Interactive staged approvals for file edits and shell executions.

---

## 🏗️ Architecture Overview

```text
                        +---------------------------------------+
                        |      Tablet / Phone / Desktop UI      |
                        +-------------------+-------------------+
                                            | (SSE / REST API)
                                            v
+-----------------------------------------------------------------------------------+
|                            Evelyn Core Orchestrator                               |
|                              (evelyn_server.py)                                   |
+-------------------+-----------------------+-------------------+-------------------+
                    |                       |                   |
                    v                       v                   v
        +-----------------------+ +-------------------+ +-------------------+
        |    Storage & Memory   | |   Local Inference | | Ingestion Engine  |
        |-----------------------| |-------------------| |-------------------|
        | • evelyn_chat.db      | | • Ollama (LLMs)   | | • Vault Watcher   |
        | • evelyn_memory.db    | | • ChromaDB (RAG)  | | • Fact Extractor  |
        | • evelyn_vault.db     | | • Chatterbox TTS  | | • Deep Research   |
        | • Chroma Vector Store | | • FLUX Image Gen  | | • Tag Librarian   |
        +-----------------------+ +-------------------+ +-------------------+
                                            |
                                            v
                               +-------------------------+
                               |     Obsidian Vault      |
                               | (Personal Knowledge &   |
                               |   Journals via P2P)     |
                               +-------------------------+
```

---

## 🚀 Quick Start

### 1. Prerequisites
- **Linux** (Arch Linux, Ubuntu 22.04+, Debian 12+) with `systemd`
- **Python 3.11+**
- **[Ollama](https://ollama.com/)** for local model inference

### 2. Installation

```bash
# Clone repository
git clone https://github.com/Rathius-Saranoth/Evelyn-Engine.git ~/evelyn
cd ~/evelyn

# Setup virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Pull recommended LLM model
ollama pull gemma4:12b
```

### 3. Run the Setup Wizard

Launch the interactive configuration CLI to customize assistant identity, operator names, vault paths, and starter templates:

```bash
python evelyn_setup.py
```

### 4. Start Services

```bash
# Start in the foreground:
./scripts/start_evelyn_services.sh

# Or start via systemd (recommended for always-on operation):
sudo systemctl start ollama evelyn evelyn-tts
```

Open your browser at **`http://localhost:8000`** to start chatting with Evelyn!  
*(Access developer triaging & consolidation dashboard at `http://localhost:8000/ui/dev.html`)*.

---

## 🧪 Testing

Run the full pytest suite to verify all database layers, tools, and vector components:

```bash
PYTHONPATH=. ./venv/bin/pytest Evelyn/tests
```

---

## 📚 Documentation & Knowledge Graph

The workspace documentation is fully structured with bidirectional `[[WikiLinks]]` for Obsidian knowledge graph visualization and navigation.

### Core Architecture & API
- 📐 **[[engine_architecture.md]]** — Master structural blueprint, system topology, and background pipelines.
- 🔌 **[[endpoints.md]]** — Single source of truth for FastAPI REST & SSE endpoints.
- 🏷️ **[[xml_injection_conventions.md]]** — Standards for in-flight XML context injection and telemetry envelopes.
- 🔑 **[[google_access.md]]** — Google Cloud OAuth scopes, tokens, and service mappings.
- 📝 **[[docstring_guide.md]]** — Google-style docstrings and background pipeline architecture notes.

### Deployment & Specifications
- 📖 **[[SETUP_GUIDE.md]]** — Step-by-step Linux installation, hardware tiers, and systemd service scripts.
- 📋 **[[REQUIREMENTS.md]]** — Full runtime environment, Python dependencies, and system packages.
- 🖥️ **[[system_specs.md]]** & **[[HPE Server Specs.md]]** — Hardware profiling, NUMA pinning, and VRAM budgeting.
- 🖼️ **[[REQUIREMENTS_IMAGE_HOST.md]]** — FLUX.1 image generation microservice requirements and setup.

### Operations, Governance & Workflows
- 🗺️ **[[ROADMAP.md]]** — Milestones, completed capabilities, and active enhancements.
- 📜 **[[CHANGELOG.md]]** — Zero-padded version history and migration audit log.
- 🤖 **[[AGENTS.md]]** — Operational rules, coding standards, and AI contracts.
- 🛡️ **[[SUPPORT.md]]** & **[[ROLLBACK.md]]** — Support boundaries and pre-sanitization disaster recovery.
- 🚀 **Workflows**: **[[start-services.md]]** · **[[restart-services.md]]** · **[[stop-services.md]]** · **[[debug-chat-db.md]]** · **[[backup-to-github.md]]** · **[[quality-review.md]]**

### Persona & Starter Templates
- 🧠 **Active Persona**: **[[System_Directives.md]]** · **[[Evelyn_Narrative_Persona.md]]** · **[[Ricky_Narrative_Profile.md]]**
- 📄 **Starter Templates**: **[[System_Directives.example.md]]** · **[[Assistant_Persona.example.md]]** · **[[User_Profile.example.md]]** · **[[Physical_Description.example.md]]**

---

## 🤖 Project Origins & AI Collaboration

The Evelyn Engine represents a deep exploration in **human-architected, AI-partnered software engineering**:

- ⚡ **AI Code Generation**: The engine's codebase, background workers, tools, and UI integrations were authored in close collaboration with advanced AI coding models.
- 🏛️ **Human Architecture & Oversight**: System topology, NUMA multi-socket isolation, Chroma SQLite staging queues, task mutex standards, security boundaries, and memory taxonomies were designed and engineered under human architectural direction.
- 🔍 **Rigorous Review & Benchmarking**: Every component underwent human code review, structural auditing, and extensive testing to ensure performance, safety, and reliability on physical hardware.

---

## 🛡️ License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
