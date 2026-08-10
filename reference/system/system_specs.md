---
title: system_specs.md
date created: 2026-03-24 21:20:40
date modified: 2026-08-10 07:08:00
tags: markdown, reference, system-specs, requirements, sanctum, hpe
---

# SANCTUM Server System Specifications

> Hardware specifications for the primary host **Sanctum** (HPE ProLiant DL360 Gen10 Enterprise Server).
> AI agents should reference this file when making hardware-informed decisions (e.g., `NUM_CTX` sizing, RAM allocations, CUDA VRAM budgeting, and heavy task concurrency).
> Full hardware reference sheet: [[HPE Server Specs.md]]

## Host System & Operating System

| Property      | Value                               |
| ------------- | ----------------------------------- |
| Host Name     | `sanctum` (`sanctum.tail0e161b.ts.net`) |
| Platform      | HPE ProLiant DL360 Gen10 (1U Rack)  |
| Operating System | Arch Linux (Linux 6.x x86_64)    |
| Time Zone     | `America/Chicago` (CDT/CST, UTC-5)  |

## Processors (CPUs)

| Property        | Value                                            |
| --------------- | ------------------------------------------------ |
| Model           | 2x Intel Xeon Gold 5220R @ 2.20 GHz              |
| Cores / Threads | **48 Cores / 96 Threads** (24 cores per CPU)    |
| Base Clock      | 2.20 GHz                                         |
| Cache           | L1: 1.5 MB \| L2: 24.5 MB \| L3: **36.6 MB**     |
| Socket          | Dual LGA3647                                     |

## Memory (RAM)

| Property        | Value                                            |
| --------------- | ------------------------------------------------ |
| Total Installed | **192 GB DDR4**                                  |
| Configuration   | 24x 8 GB DDR4 RDIMMs (Fully populated)           |
| Speed           | **2666 MHz**                                     |
| Mode            | Advanced ECC (AMP Mode)                          |

## GPU (Inference Acceleration)

| Property        | Value                                            |
| --------------- | ------------------------------------------------ |
| Model           | **NVIDIA Tesla T4**                              |
| Architecture    | Turing (TU104)                                   |
| VRAM            | **16 GB GDDR6**                                  |
| Interface       | PCIe 3.0 x16                                     |
| Precision Support | FP32, FP16, INT8, INT4                          |

## Storage & Network Architecture

| Property        | Value                                            |
| --------------- | ------------------------------------------------ |
| Drives          | 2x HPE 240 GB Enterprise SATA SSDs (`/` and `/data`) |
| Network         | 4x 1GbE + 2x 10GbE SFP+ FlexLOM + Tailscale Mesh |
| Out-of-Band     | HPE iLO 5 (Integrated Lights-Out Management)     |

---

## AI / Inference Constraints & Tuning Targets

### Ollama LLM Configuration (`gemma4:12b`)

| Parameter | Current Setting | Recommended | Notes |
| :--- | :--- | :--- | :--- |
| **Model** | `gemma4:12b` | Dense Q4_0 | Model weight uses ~7.6 GB VRAM. Fits 100% in GPU VRAM. |
| **`NUM_CTX`** | `32768` | **32768** | 32K context window utilizing 8-bit quantized KV cache (`OLLAMA_KV_CACHE_TYPE=q8_0`, ~2.6 GB VRAM). |
| **VRAM Budgeting** | ~10.2–11.5 GB | Max 14.5 GB | Total GPU VRAM usage remains well within the Tesla T4 16 GB ceiling. |
| **`PYTORCH_CUDA_ALLOC_CONF`** | `expandable_segments:True` | Active | Prevents CUDA VRAM fragmentation during long generation sessions. |
| **Ollama Unload Cooldown** | `0.8s` | Active | Explicit 0.8s cooldown + `torch.cuda.empty_cache()` before starting TTS or background models. |

### Text-to-Speech (TTS) Engine

- **Service**: Evelyn TTS (`evelyn-tts.service`) running Chatterbox Turbo on port 5050.
- **Reference Voice**: Custom reference audio clip (`kbaudio_clip.mp3`).
- **Precision Requirement**: Librosa audio arrays explicitly loaded as `float32` to match Chatterbox PyTorch tensor requirements.

### Task Concurrency & Background Locks

- Centralized lock enforcement via `Evelyn/tools/task_manager.py`.
- **Heavy Tasks**: `fact_extractor`, `fact_consolidator`, `procedure_consolidator`, `profile_evolver`, `tag_librarian`, `refresh_memory`, `sync`, `vault_map`, `research_engine`.
- Heavy tasks run sequentially under `task_manager.is_any_running()` to prevent Ollama VRAM thrashing and CPU starvation.

---

## Reference Files

| File | Purpose |
| :--- | :--- |
| [[HPE Server Specs.md]] | Detailed HPE ProLiant DL360 Gen10 hardware reference sheet |
| [[evelyn_config.py]] | Active Evelyn environment and model configuration variables |
| [[ROADMAP.md]] | Project roadmap and planned infrastructure tasks |
| [[reference/system/Tests]] | System test matrix and QA verification suite |

[evelyn_config.py]: ../../evelyn_config.py "[[evelyn_config.py]]"
[ROADMAP.md]: ../../ROADMAP.md "Evelyn Project Roadmap"
