# image_server.py
# date created: 2026-05-22 21:45:27
# date modified: 2026-05-25 19:50:52
# tags: #image, #generation, #flux, #fastapi, #server

"""image_server.py — Standalone FLUX.1 [schnell] NF4 Image Generation Server.

Runs a FastAPI server on http://127.0.0.1:5055 that handles on-demand,
headless image generation using the highly optimized and unfiltered
FLUX.1 [schnell] model with NF4 quantization.

Features:
  - OpenAI-compatible/simple API.
  - VRAM management: Lazy model loading + auto-unload after inactivity.
    This ensures that it does not hog the RTX 4070's 12GB VRAM alongside Ollama.
  - Multi-aspect ratio support with custom resolutions.
  - Static file serving for Tailscale/local network embeds.

API Endpoints:
  - POST /generate
    Request: {"prompt": str, "aspect_ratio": str, "seed": int}
    Response: {"filename": str, "url": str}
  - GET /health
  - GET /view/{filename}
"""

import os
import time
import torch
import random
import warnings

# Suppress noisy terminal warnings
warnings.filterwarnings("ignore", category=FutureWarning)
try:
    from transformers import logging as transformers_logging
    transformers_logging.set_verbosity_error()
except ImportError:
    pass
import asyncio
import threading
from pathlib import Path
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from diffusers import DiffusionPipeline
import uvicorn

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

HOST = "127.0.0.1"
PORT = 5055

# Auto-unload after 2 minutes (120s) of inactivity to free VRAM for Ollama
UNLOAD_TIMEOUT_S = 120

# ---------------------------------------------------------------------------
# Resolutions & Aspect Ratios
# ---------------------------------------------------------------------------
ASPECT_RATIOS = {
    "1:1": (1024, 1024),
    "16:9": (1344, 768),
    "9:16": (768, 1344),
    "4:3": (1152, 896),
    "3:4": (896, 1152)
}

# ---------------------------------------------------------------------------
# App initialization
# ---------------------------------------------------------------------------

app = FastAPI(title="Evelyn Image Engine (FLUX.1 [schnell] NF4)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount the output directory to serve static images directly
app.mount("/images", StaticFiles(directory=str(OUTPUT_DIR)), name="images")

# ---------------------------------------------------------------------------
# Model Lifecycle Manager (Lazy Loading & Unloading)
# ---------------------------------------------------------------------------

_pipeline = None
_model_lock = threading.Lock()
_last_used: float = 0.0
_unload_timer: threading.Timer | None = None

def _load_pipeline():
    """Load the FLUX.1 [schnell] NF4 model into VRAM. Called under _model_lock."""
    global _pipeline
    if _pipeline is not None:
        return

    print("[IMAGE] Loading FLUX.1 [schnell] NF4 pipeline...", flush=True)
    t0 = time.perf_counter()
    
    # Load the ungated community NF4 quantized FLUX.1-schnell model
    _pipeline = DiffusionPipeline.from_pretrained(
        "magespace/FLUX.1-schnell-bnb-nf4",
        torch_dtype=torch.bfloat16,
        device_map="cuda"
    )
    
    # Enable CPU offload to be extra safe with Ollama coexisting in VRAM
    print("[IMAGE] Enabling model CPU offload...", flush=True)
    _pipeline.enable_model_cpu_offload()
    
    elapsed = time.perf_counter() - t0
    vram_mb = torch.cuda.memory_allocated() / 1024**2
    print(f"[IMAGE] Pipeline loaded in {elapsed:.1f}s ({vram_mb:.0f} MB VRAM)", flush=True)

def _unload_pipeline():
    """Unload the pipeline and release VRAM. Called by inactivity timer."""
    global _pipeline
    with _model_lock:
        if _pipeline is None:
            return
        idle = time.time() - _last_used
        if idle < UNLOAD_TIMEOUT_S:
            # Raced with a new request, reschedule
            _schedule_unload()
            return
        print(f"[IMAGE] Idle for {idle:.0f}s — unloading pipeline to free VRAM", flush=True)
        del _pipeline
        _pipeline = None
        torch.cuda.empty_cache()
        vram_mb = torch.cuda.memory_allocated() / 1024**2
        print(f"[IMAGE] Pipeline unloaded successfully ({vram_mb:.0f} MB VRAM remaining)", flush=True)

def _schedule_unload():
    """Schedule the model to unload after inactivity."""
    global _unload_timer
    if _unload_timer is not None:
        _unload_timer.cancel()
    _unload_timer = threading.Timer(UNLOAD_TIMEOUT_S, _unload_pipeline)
    _unload_timer.daemon = True
    _unload_timer.start()

def get_pipeline():
    """Access the pipeline safely, loading it if not currently present."""
    global _last_used
    with _model_lock:
        _load_pipeline()
        _last_used = time.time()
        _schedule_unload()
        return _pipeline

# ---------------------------------------------------------------------------
# API Data Schemas
# ---------------------------------------------------------------------------

class ImageRequest(BaseModel):
    prompt: str
    aspect_ratio: str = "1:1"
    seed: int | None = None
    short_title: str | None = None

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/generate")
async def generate_image(request: ImageRequest):
    """Generate an image from the natural language prompt and specified aspect ratio."""
    prompt = request.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    aspect_ratio = request.aspect_ratio
    if aspect_ratio not in ASPECT_RATIOS:
        aspect_ratio = "1:1"
        
    width, height = ASPECT_RATIOS[aspect_ratio]
    
    # Determine the seed
    seed = request.seed if request.seed is not None else random.randint(0, 2**32 - 1)
    generator = torch.Generator(device="cuda").manual_seed(seed)
    
    print(f"[IMAGE] Generating: '{prompt}' ({width}x{height}, seed={seed})...", flush=True)
    t0 = time.perf_counter()
    
    # Get loaded pipeline
    pipe = get_pipeline()
    
    try:
        # Run inference using recommended settings for schnell: 4 steps, guidance_scale=0.0
        output = pipe(
            prompt=prompt,
            width=width,
            height=height,
            num_inference_steps=4,
            guidance_scale=0.0,
            generator=generator
        )
        image = output.images[0]
    except Exception as e:
        print(f"[IMAGE] Generation failed: {e}", flush=True)
        raise HTTPException(status_code=500, detail=f"Image generation failed: {e}")
        
    # Save image to the output folder
    import re
    from datetime import datetime
    title_slug = "image"
    if request.short_title:
        title_slug = re.sub(r'[^a-zA-Z0-9]', '_', request.short_title).strip('_').lower()
        if not title_slug:
            title_slug = "image"
            
    timestamp = datetime.now().strftime("%Y%m%d-%H%M")
    filename = f"image_{timestamp}_{title_slug}.png"
    filepath = OUTPUT_DIR / filename
    image.save(filepath)
    
    elapsed = time.perf_counter() - t0
    print(f"[IMAGE] Done in {elapsed:.2f} seconds -> Saved as {filename}", flush=True)
    
    return {
        "filename": filename,
        "url": f"/images/{filename}",
        "elapsed_seconds": round(elapsed, 2),
        "seed": seed,
        "aspect_ratio": aspect_ratio
    }

@app.get("/view/{filename}")
async def view_image(filename: str):
    """Serves a generated image by filename."""
    filepath = OUTPUT_DIR / filename
    if not filepath.exists():
        raise HTTPException(status_code=440, detail="Image file not found")
    return FileResponse(filepath)

@app.get("/health")
async def health():
    """Return pipeline loaded status and current VRAM usage details."""
    loaded = _pipeline is not None
    vram_mb = torch.cuda.memory_allocated() / 1024**2 if loaded else 0
    idle = time.time() - _last_used if _last_used > 0 else None
    return {
        "status": "ok",
        "model_loaded": loaded,
        "model": "FLUX.1-schnell-nf4",
        "vram_mb": round(vram_mb, 1),
        "idle_seconds": round(idle, 1) if idle is not None else None,
        "unload_timeout_s": UNLOAD_TIMEOUT_S,
    }

# ---------------------------------------------------------------------------
# Server Boot
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"[IMAGE] Evelyn FLUX.1 [schnell] NF4 Image Server")
    print(f"[IMAGE] Listening on {HOST}:{PORT}")
    print(f"[IMAGE] Model will load on demand and auto-unload after {UNLOAD_TIMEOUT_S}s idle")
    uvicorn.run(app, host=HOST, port=PORT)
