# tts_server.py
# date created: 2026-05-22 21:36:21
# date modified: 2026-06-06 19:51:27
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
import re
import time
import uuid
import asyncio
import threading
import warnings

# Suppress noisy terminal warnings
warnings.filterwarnings("ignore", category=FutureWarning)
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[2]))
try:
    import evelyn_config as cfg
except ImportError:
    cfg = None

import numpy as np
import torch
import soundfile as sf
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
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

# Cleanup generated audio chunk files after delivery.
# Must be longer than the maximum expected total playback duration — later chunks
# are not fetched until earlier ones finish playing, so a long response (e.g. 15
# sentences × 10s each = 150s of audio) needs all files to survive until then.
FILE_CLEANUP_DELAY_S = 600  # 10 minutes

# Silence appended to the tail of each chunk (seconds).
# Set to 0.0 for seamless playback; increase (e.g. 0.15) for a natural breath pause.
SENTENCE_SILENCE_S = 0.0

# Number of sentences to group into a single synthesized audio chunk.
# Higher values = fewer Audio→Audio transitions = smoother playback, but longer
# wait for the first chunk to appear. 3 is a good default for conversational responses.
CHUNK_SENTENCES = 3

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

# Ensure output directory exists and serve generated WAV chunks by URL.
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/tts-audio", StaticFiles(directory=str(OUTPUT_DIR)), name="tts-audio")

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


def _unload_model_force():
    """Unload model and free VRAM immediately."""
    global _model, _unload_timer
    with _model_lock:
        if _unload_timer is not None:
            _unload_timer.cancel()
            _unload_timer = None
        if _model is None:
            return
        print("[TTS] Unloading Chatterbox model immediately to free VRAM for Ollama", flush=True)
        del _model
        _model = None
        torch.cuda.empty_cache()
        vram_mb = torch.cuda.memory_allocated() / 1024**2
        print(f"[TTS] Model unloaded ({vram_mb:.0f} MB VRAM remaining)", flush=True)


def _unload_ollama():
    """Instruct Ollama to unload the current model from VRAM."""
    if not cfg:
        return
    import urllib.request
    import json
    url = f"{cfg.OLLAMA_URL}/api/generate"
    payload = json.dumps({"model": cfg.MODEL_NAME, "keep_alive": 0}).encode()
    try:
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
        print(f"[TTS] Sent unload signal for {cfg.MODEL_NAME} to Ollama", flush=True)
    except Exception as e:
        print(f"[TTS] Failed to unload Ollama: {e}", flush=True)


def _prefetch_ollama():
    """Trigger Ollama to reload the model into VRAM in the background."""
    if not cfg:
        return
    import urllib.request
    import json
    # Wait 0.5s to ensure the OS/GPU has fully registered the Chatterbox release
    time.sleep(0.5)
    url = f"{cfg.OLLAMA_URL}/api/generate"
    payload = json.dumps({"model": cfg.MODEL_NAME, "prompt": "", "keep_alive": -1}).encode()
    try:
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        print(f"[TTS] Reloaded {cfg.MODEL_NAME} into Ollama VRAM", flush=True)
    except Exception as e:
        print(f"[TTS] Failed to prefetch Ollama: {e}", flush=True)


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


@app.post("/v1/audio/speech/stream")
async def generate_speech_stream(data: SpeechRequest):
    """Generate speech chunk-by-chunk, emitting one SSE event per sentence group.

    Accepts the same OpenAI-format TTS body as the old endpoint.
    Supports paralinguistic tags: [laugh], [sigh], [chuckle], [cough], [gasp],
    [groan], [sniff], [shush], [clear throat].

    Sentences are grouped into chunks of CHUNK_SENTENCES (default 3) so TTS
    synthesizes natural multi-sentence audio segments rather than one sentence
    at a time, reducing the number of Audio→Audio transitions on the client.

    SSE event format:
        data: {"chunk": "<filename.wav>"}  — one per group, available at /tts-audio/<filename>
        data: {"done": true}               — terminal event after all chunks
        data: {"error": "<message>"}       — emitted if generation fails

    Ollama is unloaded once before synthesis and reloaded once after. There is
    no per-chunk VRAM swap — the model stays resident for the full generation.

    Args:
        data: SpeechRequest with at minimum a non-empty ``input`` field.

    Returns:
        StreamingResponse: SSE stream of chunk events.
    """
    text = data.input.strip()

    # --- Text cleaning (remove markdown artifacts that cause garbled speech) ---
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)           # image markdown
    text = re.sub(r'(?<!\!)\[(.*?)\]\(.*?\)', r'\1', text) # links → keep text
    text = re.sub(r'\[\[(.*?)\]\]', r'\1', text)           # wiki links
    text = text.replace('**', '').replace('*', '').replace('__', '').replace('_', '')
    text = text.replace('#', '')
    text = text.replace('—', ', ').replace('–', ', ')       # dashes → natural pause
    text = re.sub(r'\.\s*\.\s*\.', ',', text)             # ellipsis → comma
    text = re.sub(r'[^\w\s,.!?;:\'"-\[\]]', '', text)      # strict whitelist
    text = text.replace('_', ' ')
    text = re.sub(r'([!?.]){2,}', r'\1', text)             # collapse repeated punctuation
    text = re.sub(r'[ \t]+', ' ', text)           # collapse horizontal whitespace only
    text = re.sub(r'\n{3,}', '\n\n', text).strip() # cap blank lines at two

    if not text:
        raise HTTPException(status_code=400, detail="Missing or empty 'input' field after cleaning")

    # Chunk strategy: paragraph breaks are the primary boundary; CHUNK_SENTENCES
    # is a secondary cap within a long paragraph. Whichever comes first wins.
    # e.g. a 2-sentence paragraph → 1 chunk; a 5-sentence paragraph → [3, 2].
    paragraphs = [p.strip() for p in re.split(r'\n+', text) if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    chunks = []
    for para in paragraphs:
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', para) if s.strip()]
        if not sentences:
            if para:
                chunks.append(para)
            continue
        if len(sentences) <= CHUNK_SENTENCES:
            chunks.append(' '.join(sentences))
        else:
            for i in range(0, len(sentences), CHUNK_SENTENCES):
                group = ' '.join(sentences[i:i + CHUNK_SENTENCES])
                if group:
                    chunks.append(group)

    if not chunks:
        chunks = [text]

    async def _stream():
        loop = asyncio.get_event_loop()
        job_id = uuid.uuid4().hex[:8]

        # Unload Ollama and load Chatterbox once for the entire job.
        _unload_ollama()
        model = get_model()

        try:
            for i, chunk in enumerate(chunks):
                wav = await loop.run_in_executor(
                    None,
                    lambda c=chunk: model.generate(
                        text=c,
                        audio_prompt_path=REF_AUDIO,
                    )
                )
                wav_np = wav.squeeze().cpu().numpy()

                if SENTENCE_SILENCE_S > 0:
                    silence = np.zeros(int(SAMPLE_RATE * SENTENCE_SILENCE_S), dtype=np.float32)
                    wav_np = np.concatenate([wav_np, silence])

                filename = f"tts_{job_id}_{i:03d}.wav"
                filepath = str(OUTPUT_DIR / filename)
                sf.write(filepath, wav_np, SAMPLE_RATE)

                # Schedule file cleanup independently of the stream lifecycle.
                asyncio.get_event_loop().create_task(_delete_after_delay(filepath))

                yield f'data: {{"chunk": "{filename}"}}\n\n'

        except Exception as e:
            print(f"[TTS] Stream generation error: {e}", flush=True)
            yield f'data: {{"error": "{str(e)}"}}\n\n'
        finally:
            _unload_model_force()
            threading.Thread(target=_prefetch_ollama, daemon=True).start()

        yield 'data: {"done": true}\n\n'

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
