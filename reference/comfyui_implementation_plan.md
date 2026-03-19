# ComfyUI Integration Plan for Evelyn

## Goal Description
Integrate ComfyUI into Evelyn (via Open WebUI) to provide powerful image/video generation capabilities. Additionally, utilize Qwen3 TTS via ComfyUI to replace the current Kokoro TTS model, allowing for higher quality voice generation and voice cloning.

## User Review Required
> [!IMPORTANT]
> **Qwen3 TTS Integration:** Open WebUI requires an OpenAI-compatible API for its system voice (TTS). Since ComfyUI does not provide an OpenAI-compatible endpoint natively, I propose creating a lightweight FastAPI wrapper (`qwen_tts_server.py`). This script will act like an OpenAI server, accept requests from Open WebUI, and translate them into ComfyUI workflow executions to generate the audio. 
> 
> *Do you approve of this middleman API approach, or would you prefer a different method (like running a standalone Qwen3 TTS server outside of ComfyUI)?*

> [!NOTE]
> **Workflows Needed:** The Image/Video generation tool will require "API Format" workflow JSON files from ComfyUI to function. You will need to build and export your preferred image/video workflows (e.g., using Flux, SDXL, or Video models) in ComfyUI beforehand.

## Proposed Changes

### ComfyUI Image/Video Generation Tool
We will create a Python script that acts as an Open WebUI Tool, allowing Evelyn to trigger generations directly.

#### [NEW] `C:\Projects\LocalAI\Evelyn\tools\comfy_image_gen.py`
A python tool specifically structured for Open WebUI.
- Exposes `generate_media(prompt: str, media_type: str)` to the LLM.
- Communicates with ComfyUI's REST and WebSocket APIs (`127.0.0.1:8188`) to queue prompts and track progress.
- Retrieves the output media and returns it to the chat interface.

### Qwen3 TTS Wrapper for Open WebUI
To bypass the limitations of Kokoro-TTS, we will set up a bridge to use ComfyUI's Qwen3 TTS.

#### [NEW] `C:\Projects\LocalAI\Evelyn\tools\qwen_tts_server.py`
A small FastAPI script.
- Hosts an endpoint at `http://127.0.0.1:5050/v1/audio/speech`.
- Converts OpenAI-formatted TTS requests into a ComfyUI Qwen3 TTS workflow payload.
- Returns the generated `.wav` or `.mp3` to Open WebUI.

#### [MODIFY] `C:\Projects\LocalAI\start_evelyn.ps1`
- Remove or comment out the Kokoro TTS Docker container startup.
- Add a step to launch the new `qwen_tts_server.py` in the background.

## Verification Plan

### Manual Verification
1. **TTS Server Test:** Run `qwen_tts_server.py` and send a test `curl` request to generate speech. Verify audio is returned.
2. **Open WebUI TTS Config:** In Open WebUI, point the Audio settings to `http://127.0.0.1:5050/v1` and click to test the voice. 
3. **Image Gen Test:** Upload the `comfy_image_gen.py` tool in Open WebUI, assign it to Evelyn, and ask Evelyn: "Generate an image of a cybernetic cat." Verify the request reaches ComfyUI and the image returns to the chat.
