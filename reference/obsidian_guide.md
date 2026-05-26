---
title: obsidian_guide.md
date created: 2026-02-12 19:09:17
date modified: 2026-05-25 20:00:06
tags: markdown, reference, obsidian, guide, tool
---

## Evelyn Integration Guide: Obsidian

This guide explains how to connect your Obsidian Vault to the local `evelyn:v1` model.

## Prerequisites

1. **Ollama** running locally (`http://localhost:11434`).
2. **Evelyn Model** created (`ollama list` should show `evelyn:v1`).

## Recommended Plugin: Smart Connections

1. **Install**: Search for "Smart Connections" in Obsidian Community Plugins.
2. **Configure**:
    - **API Type**: Local (Ollama).
    - **Ollama URL**: `http://localhost:11434`.
    - **Model**: Select `evelyn:v1` from the dropdown.
3. **Usage**:
    - Open the Smart Connections chat pane.
    - Ask questions. Evelyn will use her `SYSTEM` prompt (Persona + Protocols) to answer.
    - *Note*: Smart Connections uses its own RAG (vector search) on your notes, which complements Evelyn's built-in knowledge.

## Alternative: Copilot (by logancy)

1. **Install**: "Copilot" in Community Plugins.
2. **Configure**:
    - **Model**: Custom / Ollama.
    - **URL**: `http://localhost:11434`.
    - **Model Name**: `evelyn:v1`.
3. **Usage**:
    - Chat with Evelyn in the sidebar.
