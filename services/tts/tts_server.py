# tts_server.py
# date created: 2026-05-22 21:36:21
# date modified: 2026-10-06 19:40:21
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

os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
import asyncio
import contextlib
import gc
import json
import re
import threading
import time
import uuid
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

try:
    import soundfile as sf
except ImportError:
    sf = None
import torch
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

# Execution device: "cpu" (default, zero VRAM impact, Ollama never unloads) or "cuda"
DEVICE = (cfg.TTS_DEVICE if cfg and hasattr(cfg, "TTS_DEVICE") else os.environ.get("EVELYN_TTS_DEVICE", "cpu")).lower()

# Minimum character floor for Chunk 0 fast-dispatch (avoids microscopic 3-word fragments)
MIN_CHUNK0_CHARS = (
    cfg.TTS_MIN_CHUNK0_CHARS
    if cfg and hasattr(cfg, "TTS_MIN_CHUNK0_CHARS")
    else int(os.environ.get("EVELYN_TTS_MIN_CHUNK0_CHARS", "35"))
)

# PyTorch thread allocation on CPU to optimize RTF
if DEVICE == "cpu":
    cpu_threads = int(os.environ.get("OMP_NUM_THREADS", "8"))
    try:
        torch.set_num_threads(cpu_threads)
    except (RuntimeError, ValueError) as e:
        print(f"[TTS] Warning: could not set torch threads: {e}", flush=True)

# Unload model after this many seconds of inactivity to free system memory / VRAM.
UNLOAD_TIMEOUT_S = (
    cfg.TTS_UNLOAD_TIMEOUT_S
    if cfg and hasattr(cfg, "TTS_UNLOAD_TIMEOUT_S")
    else int(os.environ.get("EVELYN_TTS_UNLOAD_TIMEOUT_S", "300"))
)

# Cleanup generated audio chunk files after delivery.
FILE_CLEANUP_DELAY_S = 600  # 10 minutes

# Silence appended to the tail of each chunk (seconds).
SENTENCE_SILENCE_S = 0.0

# Number of sentences fallback cap if manual override is supplied.
CHUNK_SENTENCES = int(os.environ.get("EVELYN_TTS_CHUNK_SENTENCES", "3"))

CALIBRATION_FILE = BASE_DIR / "audio" / "rtf_calibration.json"


class RTFTracker:
    """Tracks exponential moving average (EMA) of synthesis Real-Time Factor (RTF).

    Persists calibration to disk per-device so calibrations survive restarts and model unloads.
    """

    def __init__(
        self,
        initial_rtf: float | None = None,
        device: str = "cpu",
        calibration_file: Path | None = None,
    ):
        self.device = device.lower()
        self.calibration_file = calibration_file
        self.alpha = 0.35
        self.lock = threading.Lock()

        default_val = initial_rtf if initial_rtf is not None else (2.09 if self.device == "cpu" else 0.25)
        self.ema_rtf = self._load_persisted(default_val)

    def _load_persisted(self, default_val: float) -> float:
        if self.calibration_file and self.calibration_file.exists():
            try:
                with open(self.calibration_file, encoding="utf-8") as f:
                    data = json.load(f)
                    if self.device in data and isinstance(data[self.device], (int, float)):
                        val = float(data[self.device])
                        print(f"[TTS] Loaded persisted RTF calibration for {self.device}: {val:.2f}x", flush=True)
                        return val
            except (OSError, json.JSONDecodeError, ValueError) as e:
                print(f"[TTS] Warning: could not load RTF calibration: {e}", flush=True)
        return default_val

    def _save_persisted(self):
        if not self.calibration_file:
            return
        try:
            data = {}
            if self.calibration_file.exists():
                with contextlib.suppress(OSError, json.JSONDecodeError), open(self.calibration_file, encoding="utf-8") as f:
                    data = json.load(f)
            data[self.device] = round(self.ema_rtf, 3)
            self.calibration_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.calibration_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except (OSError, TypeError, ValueError) as e:
            print(f"[TTS] Warning: could not save RTF calibration: {e}", flush=True)

    def record(self, gen_seconds: float, audio_seconds: float) -> float:
        if audio_seconds <= 0.1:
            return self.ema_rtf
        measured = gen_seconds / audio_seconds
        with self.lock:
            self.ema_rtf = (self.alpha * measured) + ((1.0 - self.alpha) * self.ema_rtf)
            self._save_persisted()
            return self.ema_rtf

    @property
    def value(self) -> float:
        with self.lock:
            return self.ema_rtf


_rtf_tracker = RTFTracker(device=DEVICE, calibration_file=CALIBRATION_FILE)


def calculate_chunk_plan(text: str, rtf_ema: float) -> list[str]:
    """Calculate ratio-balanced chunk plan tailored to device performance.

    Chunk 0: 1 sentence (fast dispatch to minimize Time to First Audio), subject
             to MIN_CHUNK0_CHARS floor so short greetings like 'Yes.' merge forward.
    Chunk 1: On high-RTF CPU (RTF >= 1.0), kept to a stepping-stone single sentence
             to keep synthesis latency tightly aligned with Chunk 0 playback duration,
             preventing noticeable silence gap before subsequent sections.
    Chunks 2+: Scaled character target based on rtf_ema to balance natural pauses
               and manageable batch computation.
    """
    clean_text = text.strip()
    if not clean_text:
        return [""]

    paragraphs = [p.strip() for p in re.split(r'\n+', clean_text) if p.strip()]
    if not paragraphs:
        return [""]

    all_sentences = []
    for para in paragraphs:
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', para) if s.strip()]
        if not sentences and para:
            sentences = [para]
        all_sentences.extend(sentences)

    if not all_sentences:
        return [text]
    if len(all_sentences) == 1:
        return all_sentences

    chunks = []
    s0 = all_sentences[0]
    idx = 1
    while len(s0) < MIN_CHUNK0_CHARS and idx < len(all_sentences):
        s0 = f"{s0} {all_sentences[idx]}"
        idx += 1
    chunks.append(s0)

    if idx >= len(all_sentences):
        return chunks

    # Stepping-stone for Chunk 1 on CPU/slow devices:
    # A single sentence synthesizes in ~8-11s, closely matching Chunk 0 playback
    # duration (~7-9s) and eliminating the large 15-20s pause.
    if rtf_ema >= 1.0 and idx < len(all_sentences):
        s1 = all_sentences[idx]
        idx += 1
        while len(s1) < MIN_CHUNK0_CHARS and idx < len(all_sentences):
            s1 = f"{s1} {all_sentences[idx]}"
            idx += 1
        chunks.append(s1)

    if idx >= len(all_sentences):
        return chunks

    target_chars = 120 if rtf_ema < 0.6 else int(min(180, max(130, 90 * rtf_ema)))

    current_group = []
    current_len = 0
    for s in all_sentences[idx:]:
        current_group.append(s)
        current_len += len(s)
        if current_len >= target_chars:
            chunks.append(" ".join(current_group))
            current_group = []
            current_len = 0

    if current_group:
        chunks.append(" ".join(current_group))

    return chunks


SUPPORTED_TTS_TAGS = {
    "laugh", "sigh", "chuckle", "cough", "gasp",
    "groan", "sniff", "shush", "clear throat",
}


def sanitize_and_tag_speech(text: str) -> str:
    """Prepare text for Chatterbox Turbo TTS synthesis.

    1. Normalizes unicode quotes, apostrophes, dashes, and ellipses.
    2. Preserves bold text while translating asterisk vocal emotes to native paralinguistic audio tags.
    3. Strips remaining visual/bodily stage directions (*I lean in...*) so they are never voiced aloud.
    4. Converts soft delivery markers ([softly], [whispering]) into natural pauses while preserving supported tags.
    5. Strips markdown links, images, and extra noise.
    """
    clean = text.strip()
    if not clean:
        return ""

    # 1. Normalize quotes, apostrophes, dashes, ellipses
    clean = clean.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    clean = clean.replace("—", ", ").replace("–", ", ").replace("…", ", ")

    # 2. Unwrap markdown images and links
    clean = re.sub(r'!\[.*?\]\(.*?\)', '', clean)
    clean = re.sub(r'(?<!\!)\[(.*?)\]\(.*?\)', r'\1', clean)
    clean = re.sub(r'\[\[(.*?)\]\]', r'\1', clean)

    # 3. Preserve bold words as plain text before asterisk action processing
    clean = re.sub(r'\*\*(.*?)\*\*', r'\1', clean)
    clean = re.sub(r'__(.*?)__', r'\1', clean)

    # 4. Translate vocal actions in asterisks to native Chatterbox tags
    vocal_patterns = [
        (r'\*[^*]*(?:melodious\s+laugh|soft\s+laugh|small\s+laugh|airy\s+laugh|chuckles?\s+and\s+laughs?|burst\s+of\s+laughter|laughing|laughs?)[^*]*\*', ' [laugh] '),
        (r'\*[^*]*(?:chuckles?|giggles?|smirks?|snickers?)[^*]*\*', ' [chuckle] '),
        (r'\*[^*]*(?:long,?\s+contented\s+breath|deep\s+breath|slow\s+breath|contented\s+sigh|soft\s+sigh|sighs?|exhales?)[^*]*\*', ' [sigh] '),
        (r'\*[^*]*(?:gasps?)[^*]*\*', ' [gasp] '),
        (r'\*[^*]*(?:clears?\s+(?:my|her|the)?\s*throat)[^*]*\*', ' [clear throat] '),
        (r'\*[^*]*(?:coughs?)[^*]*\*', ' [cough] '),
        (r'\*[^*]*(?:groans?|grunts?)[^*]*\*', ' [groan] '),
        (r'\*[^*]*(?:pause[sd]?|silent\s+pause)[^*]*\*', ', '),
    ]
    for pattern, tag in vocal_patterns:
        clean = re.sub(pattern, tag, clean, flags=re.IGNORECASE)

    # 5. Strip any remaining asterisk actions (visual/bodily stage directions)
    clean = re.sub(r'\*[^*]+?\*', '', clean)
    clean = clean.replace('*', '')

    # 6. Handle bracketed delivery markers: preserve supported tags, filter unsupported ones
    def _bracket_filter(match):
        inner = match.group(1).lower().strip()
        if inner in SUPPORTED_TTS_TAGS:
            return f' [{inner}] '
        return ', ' if inner in {'softly', 'gently', 'quietly', 'whispering', 'tenderly'} else ''

    clean = re.sub(r'\[(.*?)\]', _bracket_filter, clean)

    # 7. Punctuation and whitespace normalization
    clean = re.sub(r'\.\s*\.\s*\.', ',', clean)
    clean = re.sub(r'[^\w\s,.!?;:\'\"-\[\]]', '', clean)
    clean = clean.replace('_', ' ').replace('#', '')
    clean = re.sub(r'([!?.]){2,}', r'\1', clean)
    clean = re.sub(r',\s*,+', ',', clean)
    clean = re.sub(r'(\n+)\s*,', r'\1', clean)
    clean = re.sub(r'[ \t]+', ' ', clean)
    clean = re.sub(r'\n{3,}', '\n\n', clean).strip()

    return clean


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
_current_voice: str | None = None
_active_requests: int = 0
_request_lock = threading.Lock()


def _load_model():
    """Load Chatterbox Turbo onto configured device (CPU or CUDA). Called under _model_lock."""
    global _model, _current_voice
    if _model is not None:
        return

    print(f"[TTS] Loading Chatterbox Turbo on {DEVICE.upper()}...", flush=True)
    t0 = time.perf_counter()
    from chatterbox.tts_turbo import ChatterboxTurboTTS
    _model = ChatterboxTurboTTS.from_pretrained(device=DEVICE)
    elapsed = time.perf_counter() - t0
    if DEVICE == "cuda" and torch.cuda.is_available():
        vram_mb = torch.cuda.memory_allocated() / 1024**2
        print(f"[TTS] Model loaded in {elapsed:.1f}s ({vram_mb:.0f} MB VRAM)", flush=True)
    else:
        print(f"[TTS] Model loaded in {elapsed:.1f}s on CPU", flush=True)

    # Pre-cache voice conditionals for reference audio to eliminate per-chunk extraction overhead
    if os.path.exists(REF_AUDIO):
        print(f"[TTS] Pre-caching voice conditionals from {Path(REF_AUDIO).name}...", flush=True)
        t_ref = time.perf_counter()
        _model.prepare_conditionals(REF_AUDIO, exaggeration=0.0, norm_loudness=True)
        _current_voice = REF_AUDIO
        print(f"[TTS] Voice conditionals cached in {time.perf_counter() - t_ref:.2f}s", flush=True)


def _teardown_model_vram():
    """Internal helper to dismantle model references, run GC, and purge PyTorch CUDA cache."""
    global _model, _current_voice
    if _model is not None:
        for attr in ("t3", "s3gen", "ve", "conds", "watermarker", "tokenizer"):
            if hasattr(_model, attr):
                with contextlib.suppress(AttributeError, TypeError):
                    delattr(_model, attr)
        del _model
        _model = None
    _current_voice = None

    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        if hasattr(torch.cuda, "ipc_collect"):
            torch.cuda.ipc_collect()
        vram_mb = torch.cuda.memory_allocated() / 1024**2
        reserved_mb = torch.cuda.memory_reserved() / 1024**2
        print(f"[TTS] VRAM purged ({vram_mb:.0f} MB allocated, {reserved_mb:.0f} MB reserved remaining)", flush=True)


def _unload_model():
    """Unload model and free VRAM. Called by the inactivity timer."""
    global _model
    with _model_lock:
        if _model is None:
            return
        with _request_lock:
            if _active_requests > 0:
                # Active request in flight — do not unload! Reschedule.
                _schedule_unload()
                return
        idle = time.time() - _last_used
        if idle < UNLOAD_TIMEOUT_S:
            # Not idle long enough (raced with a new request) — reschedule
            _schedule_unload()
            return
        print(f"[TTS] Idle for {idle:.0f}s — unloading model to free VRAM", flush=True)
        _teardown_model_vram()


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
        with _request_lock:
            if _active_requests > 0:
                print(f"[TTS] Force unload requested but {_active_requests} request(s) still active — deferred", flush=True)
                return
        print("[TTS] Unloading Chatterbox model immediately to free VRAM for Ollama", flush=True)
        _teardown_model_vram()


def _unload_ollama():
    """Instruct Ollama to unload the current model from VRAM."""
    if not cfg:
        return
    import urllib.error
    import urllib.request
    url = f"{cfg.OLLAMA_URL}/api/generate"
    payload = json.dumps({"model": cfg.MODEL_NAME, "keep_alive": 0}).encode()
    try:
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
        print(f"[TTS] Sent unload signal for {cfg.MODEL_NAME} to Ollama", flush=True)
        time.sleep(0.8)
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as e:
        print(f"[TTS] Failed to unload Ollama: {e}", flush=True)


def _prefetch_ollama():
    """Trigger Ollama to reload the model into VRAM after verifying VRAM is clear."""
    if not cfg:
        return
    import urllib.error
    import urllib.request

    # Active verification gate: wait up to 6s for VRAM to be fully cleared
    max_wait_s = 6.0
    start = time.time()
    while time.time() - start < max_wait_s:
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            allocated = torch.cuda.memory_allocated() / 1024**2
            reserved = torch.cuda.memory_reserved() / 1024**2
            if allocated < 100 and reserved < 600:
                break
        time.sleep(0.3)

    url = f"{cfg.OLLAMA_URL}/api/generate"
    payload = json.dumps({"model": cfg.MODEL_NAME, "prompt": "", "keep_alive": -1}).encode()
    try:
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        print(f"[TTS] Reloaded {cfg.MODEL_NAME} into Ollama VRAM", flush=True)
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as e:
        print(f"[TTS] Failed to prefetch Ollama: {e}", flush=True)

    # Sanity-check Ollama allocation status
    try:
        ps_url = f"{cfg.OLLAMA_URL}/api/ps"
        ps_req = urllib.request.Request(ps_url)
        with urllib.request.urlopen(ps_req, timeout=5) as ps_resp:
            ps_data = json.loads(ps_resp.read().decode())
            for m in ps_data.get("models", []):
                if m.get("name") == cfg.MODEL_NAME or m.get("model") == cfg.MODEL_NAME:
                    proc = m.get("details", {}).get("processor", "") or m.get("processor", "")
                    if "CPU" in proc:
                        print(f"[TTS] WARNING: {cfg.MODEL_NAME} loaded with partial CPU offload: {proc}", flush=True)
                    else:
                        print(f"[TTS] Verified {cfg.MODEL_NAME} on GPU: {proc}", flush=True)
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError, json.JSONDecodeError, KeyError, IndexError):
        pass


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
    is_final: bool = True
    session_id: str = ""


# ---------------------------------------------------------------------------
# File cleanup
# ---------------------------------------------------------------------------

async def _delete_after_delay(filepath: str, delay: int = FILE_CLEANUP_DELAY_S):
    """Delete a generated audio file after delivery delay."""
    await asyncio.sleep(delay)
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except OSError as e:
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
    text = sanitize_and_tag_speech(data.input)

    if not text:
        raise HTTPException(status_code=400, detail="Missing or empty 'input' field after cleaning")

    chunks = calculate_chunk_plan(text, _rtf_tracker.value)

    async def _stream():
        global _current_voice, _last_used
        loop = asyncio.get_event_loop()
        job_id = uuid.uuid4().hex[:8]

        with _request_lock:
            global _active_requests
            _active_requests += 1

        # Unload Ollama only if running on CUDA (CPU mode does not touch VRAM)
        if DEVICE == "cuda":
            _unload_ollama()
        model = get_model()
        if model is None:
            with _request_lock:
                _active_requests = max(0, _active_requests - 1)
            raise RuntimeError("TTS model could not be loaded")

        # Determine voice reference audio path and ensure conditionals are cached
        voice_path = REF_AUDIO
        if data.voice and os.path.exists(data.voice):
            voice_path = data.voice

        if _current_voice != voice_path or getattr(model, "conds", None) is None:
            print(f"[TTS] Preparing voice conditionals for {Path(voice_path).name}...", flush=True)
            model.prepare_conditionals(voice_path, exaggeration=0.0, norm_loudness=True)
            _current_voice = voice_path

        try:
            for i, chunk in enumerate(chunks):
                def _gen(c=chunk, m=model):
                    # audio_prompt_path=None leverages the pre-cached conditionals in m.conds
                    return m.generate(text=c, audio_prompt_path=None)

                t_chunk_start = time.perf_counter()
                wav = await loop.run_in_executor(None, _gen)
                gen_time_s = time.perf_counter() - t_chunk_start
                wav_np = wav.squeeze().cpu().numpy()
                audio_dur_s = len(wav_np) / SAMPLE_RATE
                curr_ema = _rtf_tracker.record(gen_time_s, audio_dur_s)
                measured_rtf = gen_time_s / max(0.01, audio_dur_s)
                print(
                    f"[TTS] Chunk {i:03d} synthesized in {gen_time_s:.2f}s "
                    f"({audio_dur_s:.2f}s audio, RTF: {measured_rtf:.2f}x, EMA: {curr_ema:.2f}x)",
                    flush=True,
                )

                if SENTENCE_SILENCE_S > 0:
                    silence = np.zeros(int(SAMPLE_RATE * SENTENCE_SILENCE_S), dtype=np.float32)
                    wav_np = np.concatenate([wav_np, silence])

                filename = f"tts_{job_id}_{i:03d}.wav"
                filepath = str(OUTPUT_DIR / filename)
                if sf is not None:
                    sf.write(filepath, wav_np, SAMPLE_RATE)

                # Schedule file cleanup independently of the stream lifecycle.
                asyncio.get_event_loop().create_task(_delete_after_delay(filepath))

                with _model_lock:
                    _last_used = time.time()
                    _schedule_unload()

                yield f'data: {{"chunk": "{filename}"}}\n\n'

        except (RuntimeError, ValueError, TypeError, OSError) as e:
            print(f"[TTS] Stream generation error: {e}", flush=True)
            yield f'data: {{"error": "{e!s}"}}\n\n'
        finally:
            with _request_lock:
                _active_requests = max(0, _active_requests - 1)
            with _model_lock:
                _last_used = time.time()
            with contextlib.suppress(NameError):
                del model
            if DEVICE == "cuda":
                if getattr(data, "is_final", True):
                    _unload_model_force()
                    threading.Thread(target=_prefetch_ollama, daemon=True).start()
            else:
                _schedule_unload()

        yield 'data: {"done": true}\n\n'

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/health")
async def health():
    """Health check — returns model load status and device usage."""
    loaded = _model is not None
    vram_mb = (torch.cuda.memory_allocated() / 1024**2) if (loaded and DEVICE == "cuda" and torch.cuda.is_available()) else 0
    idle = time.time() - _last_used if _last_used > 0 else None
    return {
        "status": "ok",
        "device": DEVICE,
        "model_loaded": loaded,
        "model": "ChatterboxTurboTTS",
        "rtf_ema": round(_rtf_tracker.value, 2),
        "vram_mb": round(vram_mb, 1),
        "idle_seconds": round(idle, 1) if idle is not None else None,
        "unload_timeout_s": UNLOAD_TIMEOUT_S,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("[TTS] Evelyn TTS Server (Chatterbox Turbo)")
    print(f"[TTS] Listening on {HOST}:{PORT}")
    print(f"[TTS] Reference audio: {REF_AUDIO}")
    print(f"[TTS] Model loads on first request, unloads after {UNLOAD_TIMEOUT_S}s idle")
    print("[TTS] Supported tags: [laugh] [sigh] [chuckle] [cough] [gasp] [groan] [sniff] [shush] [clear throat]")
    uvicorn.run(app, host=HOST, port=PORT)
