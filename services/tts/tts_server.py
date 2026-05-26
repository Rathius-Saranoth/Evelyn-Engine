# tts_server.py
# date created: 2026-05-22 21:36:21
# date modified: 2026-05-25 19:50:52
# tags: #tts, #chatterbox, #audio, #fastapi, #server

"""tts_server.py — Standalone Chatterbox Turbo TTS server for Evelyn.

Uses ChatterboxTurboTTS which supports paralinguistic tags ([laugh],
[sigh], [chuckle], etc.) with context-aware emotional delivery.

API contract (OpenAI-compatible):
    POST /v1/audio/speech  {"model": "...", "input": "<text>", "voice": "..."}
    → Returns audio/wav

VRAM management:
    Chatterbox Turbo uses ~4.2 GB VRAM, which cannot coexist with Ollama's
    ~9.2 GB footprint on a 12 GB GPU. The model is loaded lazily on first
    request and unloaded after UNLOAD_TIMEOUT_S of inactivity to return
    VRAM to Ollama.

Port: 5050 (matches evelyn_config.py TTS_SERVER_URL — zero config changes)

Run:
    & "services\\tts\\venv\\Scripts\\python.exe" "services\\tts\\tts_server.py"
"""

import os
import time
import asyncio
import threading
import warnings

# Suppress noisy terminal warnings
warnings.filterwarnings("ignore", category=FutureWarning)
from pathlib import Path

import torch
import soundfile as sf
import uvicorn
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
REF_AUDIO = str(BASE_DIR / "audio" / "reference" / "kbaudio_clip.mp3")
OUTPUT_DIR = BASE_DIR / "audio" / "output"

HOST = "127.0.0.1"
PORT = 5050
SAMPLE_RATE = 24000

# Unload model after this many seconds of inactivity to free VRAM for Ollama.
# Chatterbox Turbo uses ~4.2 GB — too much to leave resident alongside Ollama.
UNLOAD_TIMEOUT_S = 120  # 2 minutes

# Cleanup generated audio files after delivery
FILE_CLEANUP_DELAY_S = 60

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Evelyn TTS Server (Chatterbox Turbo)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Model lifecycle — lazy load, auto-unload
# ---------------------------------------------------------------------------

_model = None
_model_lock = threading.Lock()
_last_used: float = 0.0
_unload_timer: threading.Timer | None = None


def _load_model():
    """Load Chatterbox Turbo onto GPU. Called under _model_lock."""
    global _model
    if _model is not None:
        return

    print("[TTS] Loading Chatterbox Turbo...", flush=True)
    t0 = time.perf_counter()
    from chatterbox.tts_turbo import ChatterboxTurboTTS
    _model = ChatterboxTurboTTS.from_pretrained(device="cuda")
    elapsed = time.perf_counter() - t0
    vram_mb = torch.cuda.memory_allocated() / 1024**2
    print(f"[TTS] Model loaded in {elapsed:.1f}s ({vram_mb:.0f} MB VRAM)", flush=True)


def _unload_model():
    """Unload model and free VRAM. Called by the inactivity timer."""
    global _model
    with _model_lock:
        if _model is None:
            return
        idle = time.time() - _last_used
        if idle < UNLOAD_TIMEOUT_S:
            # Not idle long enough (raced with a new request) — reschedule
            _schedule_unload()
            return
        print(f"[TTS] Idle for {idle:.0f}s — unloading model to free VRAM", flush=True)
        del _model
        _model = None
        torch.cuda.empty_cache()
        vram_mb = torch.cuda.memory_allocated() / 1024**2
        print(f"[TTS] Model unloaded ({vram_mb:.0f} MB VRAM remaining)", flush=True)


def _schedule_unload():
    """Schedule (or reschedule) the unload timer."""
    global _unload_timer
    if _unload_timer is not None:
        _unload_timer.cancel()
    _unload_timer = threading.Timer(UNLOAD_TIMEOUT_S, _unload_model)
    _unload_timer.daemon = True
    _unload_timer.start()


def get_model():
    """Get the loaded model, loading it if necessary. Thread-safe."""
    global _last_used
    with _model_lock:
        _load_model()
        _last_used = time.time()
        _schedule_unload()
        return _model


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------

class SpeechRequest(BaseModel):
    model: str = ""
    input: str
    voice: str = ""


# ---------------------------------------------------------------------------
# File cleanup
# ---------------------------------------------------------------------------

async def _delete_after_delay(filepath: str, delay: int = FILE_CLEANUP_DELAY_S):
    """Delete a generated audio file after delivery delay."""
    await asyncio.sleep(delay)
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except Exception as e:
        print(f"[TTS] Cleanup failed for {filepath}: {e}", flush=True)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/v1/audio/speech")
async def generate_speech(data: SpeechRequest, background_tasks: BackgroundTasks):
    """Generate speech from text using Chatterbox Turbo with voice cloning.

    Accepts OpenAI-format TTS body. Supports paralinguistic tags in the input
    text: [laugh], [sigh], [chuckle], [cough], [gasp], [groan], [sniff],
    [shush], [clear throat]. Tags are context-aware — the same [laugh] will
    sound different depending on surrounding sentence emotion.

    Args:
        data: SpeechRequest with at minimum a non-empty ``input`` field.
        background_tasks: Used to schedule audio file cleanup.

    Returns:
        FileResponse: Generated WAV audio.
    """
    import re
    text = data.input.strip()
    
    # Clean up text for TTS (remove markdown artifacts that cause it to speak 'in tongues')
    # Remove image markdown
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
    # Remove standard markdown links but keep text
    text = re.sub(r'(?<!\!)\[(.*?)\]\(.*?\)', r'\1', text)
    # Remove wiki links but keep text
    text = re.sub(r'\[\[(.*?)\]\]', r'\1', text)
    # Remove markdown bold/italics
    text = text.replace('**', '').replace('*', '').replace('__', '').replace('_', '')
    # Remove hashes
    text = text.replace('#', '')
    
    # --- Punctuation normalization to prevent auto-regressive garbling ---
    # Replace em-dash and en-dash with a comma for a natural pause
    text = text.replace('—', ', ').replace('–', ', ')
    # Replace ellipsis (and spaced ellipsis) with a comma instead of a period
    # This prevents the sentence from being split into two chunks and preserves the trailing inflection
    text = re.sub(r'\.\s*\.\s*\.', ',', text)
    
    # Strict Whitelist: Keep only alphanumeric, spaces, standard punctuation, and tags
    # \w includes letters and numbers. We manually strip underscores next.
    text = re.sub(r'[^\w\s,.!?;:\'"\-\[\]]', '', text)
    text = text.replace('_', ' ')
    
    # Reduce multiple punctuation marks (e.g. !!! -> !)
    text = re.sub(r'([!?.]){2,}', r'\1', text)

    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    if not text:
        raise HTTPException(status_code=400, detail="Missing or empty 'input' field after cleaning")

    # Load model (lazy, thread-safe)
    model = get_model()

    # Generate audio in chunks (sentence by sentence) to prevent sequence degradation
    import numpy as np
    # Split on whitespace preceded by sentence-ending punctuation
    chunks = [c.strip() for c in re.split(r'(?<=[.!?])\s+', text) if c.strip()]
    if not chunks:
        chunks = [text]

    all_wavs = []
    # 0.15s silence gap between sentences
    silence = np.zeros(int(SAMPLE_RATE * 0.15), dtype=np.float32)

    try:
        for chunk in chunks:
            wav = model.generate(
                text=chunk,
                audio_prompt_path=REF_AUDIO,
            )
            wav_np = wav.squeeze().cpu().numpy()
            all_wavs.append(wav_np)
            all_wavs.append(silence)
            
        if all_wavs:
            all_wavs.pop() # Remove trailing silence
            
        final_wav = np.concatenate(all_wavs) if all_wavs else np.zeros(0, dtype=np.float32)
    except Exception as e:
        print(f"[TTS] Generation error: {e}", flush=True)
        raise HTTPException(status_code=500, detail=f"TTS generation failed: {e}")

    # Save to temp file
    filename = f"tts_{int(time.time() * 1000)}.wav"
    filepath = str(OUTPUT_DIR / filename)
    sf.write(filepath, final_wav, SAMPLE_RATE)

    # Schedule cleanup
    background_tasks.add_task(_delete_after_delay, filepath)

    return FileResponse(filepath, media_type="audio/wav")


@app.get("/health")
async def health():
    """Health check — returns model load status and VRAM usage."""
    loaded = _model is not None
    vram_mb = torch.cuda.memory_allocated() / 1024**2 if loaded else 0
    idle = time.time() - _last_used if _last_used > 0 else None
    return {
        "status": "ok",
        "model_loaded": loaded,
        "model": "ChatterboxTurboTTS",
        "vram_mb": round(vram_mb, 1),
        "idle_seconds": round(idle, 1) if idle is not None else None,
        "unload_timeout_s": UNLOAD_TIMEOUT_S,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"[TTS] Evelyn TTS Server (Chatterbox Turbo)")
    print(f"[TTS] Listening on {HOST}:{PORT}")
    print(f"[TTS] Reference audio: {REF_AUDIO}")
    print(f"[TTS] Model loads on first request, unloads after {UNLOAD_TIMEOUT_S}s idle")
    print(f"[TTS] Supported tags: [laugh] [sigh] [chuckle] [cough] [gasp] [groan] [sniff] [shush] [clear throat]")
    uvicorn.run(app, host=HOST, port=PORT)
