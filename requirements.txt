---
title: python_requirements.md
date created: 2026-05-13 20:27:01
date modified: 2026-05-25 20:27:32
tags: python, dependencies, requirements, installation, packages
---

## Evelyn Engine — Python Dependencies

- Install with:  pip install -r requirements.txt
- Python version: 3.11+
- Platform:       Windows (tested on Windows 11)

## Core Server (evelyn_server.py)

fastapi>=0.135,<1.0          # ASGI web framework — API endpoints, SSE streaming
uvicorn>=0.41,<1.0           # ASGI server — runs the FastAPI app
httpx>=0.28,<1.0             # Async HTTP client — Ollama API calls
pydantic>=2.12,<3.0          # Data validation — request/response models

## RAG Pipeline ([[chroma_rag.py]], ingest_*.py, benchmark_rag.py)

chromadb>=1.5,<2.0           # Vector database — HNSW index, cosine distance
sentence-transformers>=5.0   # Embedding model loader (for --compare benchmarks)
                             # NOTE: Default embeddings (all-MiniLM-L6-v2) are
                             # loaded via chromadb's built-in ONNX runtime and
                             # do NOT require this package at runtime. Only
                             # needed for [[benchmark_rag.py]] --compare.

chromadb>=1.5,<2.0           # Vector database — HNSW index, cosine distance
sentence-transformers>=5.0   # Embedding model loader (for --compare benchmarks)
                             # NOTE: Default embeddings (all-MiniLM-L6-v2) are
                             # loaded via chromadb's built-in ONNX runtime and
                             # do NOT require this package at runtime. Only
                             # needed for [[benchmark_rag.py]] --compare.

## LLM Integration & Data Parsing

PyYAML>=6.0,<7.0             # YAML parsing — fact extractor, consolidator,
                             #   pending reviewer, frontmatter processing
requests>=2.32,<3.0          # HTTP client — vault indexer (sync calls)

## Tools

ddgs>=9.0                    # DuckDuckGo search — web_search tool
PyMuPDF>=1.27,<2.0           # PDF text extraction — [[extract_pdf_library.py]]
                             # (import as: import fitz)
