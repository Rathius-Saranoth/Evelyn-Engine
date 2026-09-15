# stt_server.py
# date created: 2026-09-15 18:11:00
# date modified: 2026-09-15 18:16:17
# tags: #stt, #whisper, #faster-whisper, #audio, #fastapi, #server

"""stt_server.py — Standalone Faster-Whisper Speech-to-Text (STT) server for Evelyn.

Uses Faster-Whisper (CTranslate2) with Silero VAD filtering to transcribe
incoming browser audio streams (WebM, Opus, MP4, WAV) with sub-second latency
and zero VRAM competition against Ollama.

API contract (OpenAI-compatible):
    POST /v1/audio/transcriptions (multipart/form-data: file=<blob>)
    -> Returns {"text": "...", "duration_s": 2.45, "language": "en"}

Audio Normalization:
    Decodes arbitrary incoming browser containers to 16kHz mono float32 PCM
    via system ffmpeg pipe before passing to CTranslate2.

Port: 5060 (matches evelyn_config.py STT_SERVER_URL)
"""

import contextlib
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

# Add project root to sys.path to import evelyn_config if available
sys.path.append(str(Path(__file__).resolve().parents[2]))
try:
    import evelyn_config as cfg
except ImportError:
    cfg = None

import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from faster_whisper import WhisperModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("stt_server")

# Configuration defaults
DEFAULT_PORT = 5060
PORT = int(os.environ.get("EVELYN_STT_PORT", getattr(cfg, "STT_SERVER_URL", f"http://localhost:{DEFAULT_PORT}").split(":")[-1].split("/")[0]))
MODEL_SIZE = os.environ.get("EVELYN_STT_MODEL", getattr(cfg, "STT_MODEL_SIZE", "base.en"))
DEVICE = os.environ.get("EVELYN_STT_DEVICE", getattr(cfg, "STT_DEVICE", "cpu"))
COMPUTE_TYPE = os.environ.get("EVELYN_STT_COMPUTE_TYPE", getattr(cfg, "STT_COMPUTE_TYPE", "int8"))
MIN_AUDIO_DURATION_S = 0.5

_model: WhisperModel | None = None


def get_model() -> WhisperModel:
    """Retrieve or initialize the cached WhisperModel instance."""
    global _model
    if _model is None:
        logger.info("Loading Faster-Whisper model: %s (device=%s, compute_type=%s)", MODEL_SIZE, DEVICE, COMPUTE_TYPE)
        _model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
        logger.info("Faster-Whisper model %s loaded successfully", MODEL_SIZE)
    return _model


def decode_audio_to_pcm(audio_bytes: bytes) -> tuple[np.ndarray, float]:
    """Decode raw audio bytes into 16kHz mono float32 PCM numpy array using ffmpeg.

    Args:
        audio_bytes: Raw audio container bytes (WebM, Opus, MP4, WAV, etc.)

    Returns:
        tuple[np.ndarray, float]: (16kHz float32 audio array, duration in seconds)

    Raises:
        ValueError: If ffmpeg fails to decode the audio stream.
    """
    if not audio_bytes:
        raise ValueError("Audio buffer is empty")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-i", "pipe:0",
        "-f", "f32le",
        "-ac", "1",
        "-ar", "16000",
        "pipe:1",
    ]

    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout_data, stderr_data = proc.communicate(input=audio_bytes, timeout=15)
    except subprocess.TimeoutExpired:
        if proc:
            proc.kill()
        raise ValueError("Audio decoding timed out after 15 seconds") from None
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Failed to invoke ffmpeg: {exc}") from exc

    if proc is None or proc.returncode != 0:
        err_msg = stderr_data.decode("utf-8", errors="replace").strip() if stderr_data else "Unknown error"
        exit_code = proc.returncode if proc else -1
        raise ValueError(f"FFmpeg decoding failed (exit {exit_code}): {err_msg}")

    if not stdout_data:
        raise ValueError("FFmpeg produced empty PCM stream")

    pcm_data = np.frombuffer(stdout_data, dtype=np.float32)
    duration_s = len(pcm_data) / 16000.0
    return pcm_data, round(duration_s, 2)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    """Lifecycle hook: pre-warm Whisper model on startup."""
    try:
        get_model()
    except (RuntimeError, OSError, ValueError) as exc:
        logger.error("Failed to preload Whisper model on startup: %s", exc)
    yield
    logger.info("STT server shutting down.")


app = FastAPI(
    title="Evelyn STT Server",
    description="Local Speech-to-Text inference service using Faster-Whisper and ffmpeg",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    """Health and model readiness check."""
    return {
        "status": "ok",
        "service": "evelyn-stt",
        "model_size": MODEL_SIZE,
        "device": DEVICE,
        "compute_type": COMPUTE_TYPE,
        "model_loaded": _model is not None,
    }


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    language: str = Form("en"),
):
    """Transcribe an audio file to text.

    Accepts any browser audio container (WebM, Opus, MP4, WAV), decodes via
    ffmpeg to 16kHz mono PCM, applies VAD filtering to reject room silence,
    and returns transcribed text with utterance duration.
    """
    start_time = time.time()
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file provided")

    try:
        pcm_data, duration_s = decode_audio_to_pcm(raw_bytes)
    except ValueError as exc:
        logger.warning("Audio decoding failed: %s", exc)
        raise HTTPException(status_code=422, detail=f"Audio decoding error: {exc}") from exc

    if duration_s < MIN_AUDIO_DURATION_S:
        logger.info("Audio duration %.2fs below minimum threshold %.2fs — returning empty", duration_s, MIN_AUDIO_DURATION_S)
        return {
            "text": "",
            "duration_s": duration_s,
            "language": language,
            "inference_time_s": round(time.time() - start_time, 3),
        }

    try:
        model = get_model()
        segments, info = model.transcribe(
            pcm_data,
            language=language if language else None,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,
            beam_size=5,
        )

        texts = [segment.text.strip() for segment in segments]
        transcription = " ".join(t for t in texts if t).strip()

        elapsed = round(time.time() - start_time, 3)
        logger.info("Transcribed %.2fs audio in %.3fs: '%s'", duration_s, elapsed, transcription[:80])

        return {
            "text": transcription,
            "duration_s": duration_s,
            "language": info.language if hasattr(info, "language") else language,
            "inference_time_s": elapsed,
        }
    except Exception as exc:
        logger.error("Transcription inference failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Inference error: {exc}") from exc


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
