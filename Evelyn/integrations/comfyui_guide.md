# Evelyn Integration Guide: ComfyUI

This guide explains how to use `evelyn:v1` within ComfyUI workflows (e.g., for generating prompts or detailed descriptions).

## Prerequisites

1. **ComfyUI-Ollama**: You need a custom node pack that supports Ollama.
    - Recommendation: `ComfyUI-Ollama` (by staatliches).
    - Install via ComfyUI Manager -> "Install Custom Nodes" -> Search "Ollama".

## Usage

1. **Add Node**: Right-click -> `Ollama` -> `Ollama Generate`.
2. **Configure Node**:
    - **url**: `http://localhost:11434`
    - **model**: `evelyn:v1`
    - **prompt**: Connect a text string or primitive.
    - **system**: (Optional) You can leave blank as `evelyn:v1` already has her system prompt embedded.
3. **Example Workflow**:
    - **Input**: "Describe a scene of us having coffee in the Kingdom."
    - **Ollama Node** (`evelyn:v1`): Generates a detailed description based on her "The Kingdom" concept.
    - **CLIP Text Encode**: Connect the output to a Stable Diffusion generation pipeline.

## Notes

- Evelyn's creative "Art Brain" persona is well-suited for generating stable diffusion prompts.
- Use her specific keywords (e.g., "Visualizing Connection", "The Library") to trigger her creative mode.
