# Ricky-PC Standalone Image Generation Host Requirements & Restoration Guide

This document defines the system requirements, environment configuration, dependency installation, model setup, and service restoration procedures for the standalone **FLUX.1 [schnell] NF4 Image Generation Server** running on `image-host` (`image-host.internal.net:5055`).

Use this guide if the environment on `image-host` is wiped, cleaned up, or needs to be reconfigured from scratch.

---

## 1. System & Hardware Requirements

| Category | Requirement | Notes |
| :--- | :--- | :--- |
| **OS** | Windows 10/11 64-bit (or Linux x86_64) | Tested on Windows 11 host (`image-host`) |
| **GPU** | NVIDIA GPU with ≥ 12 GB VRAM | Recommended: RTX 4070 or better |
| **CUDA Driver** | NVIDIA Display Driver supporting CUDA 12.x | Run `nvidia-smi` to verify driver state |
| **Python** | Python 3.10, 3.11, or 3.12 (64-bit) | Ensure `python` and `pip` are on PATH |
| **Tailscale** | Tailscale network client logged into tailnet | Enables `image-host.internal.net` routing |
| **Disk Space** | ~15 GB free disk space | Required for PyTorch, HuggingFace model cache, and output images |

---

## 2. Directory Structure

On `image-host`, place or clone the repository to your chosen project path (e.g. `C:\evelyn` or `%USERPROFILE%\evelyn`).

The image service files are located at:
```text
evelyn/
├── evelyn_config.py
├── scripts/
│   ├── start_image_server.ps1
│   └── start_image_server.sh
└── services/
    └── image/
        ├── image_server.py
        ├── requirements.txt
        ├── REQUIREMENTS_IMAGE_HOST.md
        └── output/             <-- Generated image files stored here
```

---

## 3. Environment Restoration Procedure

### Step 1: Create Virtual Environment
Open PowerShell (or Command Prompt) on `image-host` and navigate to the project directory:

```powershell
cd services\image
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### Step 2: Install PyTorch with CUDA Support
Ensure PyTorch with CUDA support is installed before other dependencies:

```powershell
pip install torch --index-url https://download.pytorch.org/whl/cu121
```

### Step 3: Install Required Dependencies
Install the pinned requirements:

```powershell
pip install -r requirements.txt
```

*Required packages:*
- `torch` (PyTorch with CUDA)
- `diffusers` (HuggingFace Diffusers pipeline)
- `transformers` (HuggingFace Transformers)
- `bitsandbytes` (NF4 quantization loader for FLUX.1)
- `accelerate` (Model CPU offload and acceleration)
- `fastapi` & `uvicorn` (REST API server)
- `pydantic` (Request/response schemas)
- `pillow` (Image output processing)

---

## 4. Model Pre-fetching (Optional but Recommended)

The server loads the model on demand from HuggingFace Hub:
- Model ID: `magespace/FLUX.1-schnell-bnb-nf4`

To pre-download the weights into the HuggingFace cache directory (`~/.cache/huggingface/hub` or `C:\Users\<user>\.cache\huggingface\hub`), run:

```powershell
python -c "from diffusers import DiffusionPipeline; import torch; DiffusionPipeline.from_pretrained('magespace/FLUX.1-schnell-bnb-nf4', torch_dtype=torch.bfloat16)"
```

---

## 5. Network & Firewall Configuration

1. **Tailscale Binding**:
   - Ensure Tailscale is running and `image-host` is connected to the `internal.net` network.
   - Verify `image-host` IP using `tailscale ip -4` (e.g. `100.127.160.114`).

2. **Windows Firewall Rule**:
   Allow inbound TCP traffic on port `5055` for PowerShell / Python / Uvicorn:

   ```powershell
   New-NetFirewallRule -DisplayName "Evelyn Image Server (Port 5055)" -Direction Inbound -LocalPort 5055 -Protocol TCP -Action Allow
   ```

---

## 6. Service Management & Execution

### Running Manually (PowerShell)
```powershell
$env:IMAGE_SERVER_HOST = "0.0.0.0"
$env:IMAGE_SERVER_PORT = "5055"
$env:IMAGE_SERVER_UNLOAD_TIMEOUT = "120"
python services\image\image_server.py
```

### Running via Provided Helper Script
```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_image_server.ps1
```

---

## 7. Service Verification & Testing

1. **Health Check Endpoint**:
   ```bash
   curl http://image-host.internal.net:5055/health
   ```
   *Expected Response:*
   ```json
   {
     "status": "ok",
     "model_loaded": false,
     "model": "FLUX.1-schnell-nf4",
     "vram_mb": 0.0,
     "idle_seconds": null,
     "unload_timeout_s": 120
   }
   ```

2. **Image Generation Test Endpoint**:
   ```bash
   curl -X POST http://image-host.internal.net:5055/generate \
     -H "Content-Type: application/json" \
     -d '{"prompt": "A serene foggy pine forest at sunrise", "aspect_ratio": "16:9", "short_title": "test_forest"}'
   ```

---

## 8. Troubleshooting

- **Out of Memory (OOM) / CUDA Error**:
  `image_server.py` implements CPU model offload (`_pipeline.enable_model_cpu_offload()`) and auto-unloads after 120s inactivity. If OOM occurs, verify no other heavy applications (e.g. game or local LLM server) are locking GPU memory.
- **Connection Refused on Port 5055**:
  Ensure `IMAGE_SERVER_HOST` is set to `"0.0.0.0"` in `image_server.py` (not `"127.0.0.1"`) and Windows Firewall rule allows inbound TCP port 5055.
- **Missing `bitsandbytes` CUDA libraries on Windows**:
  Ensure `bitsandbytes>=0.43.0` is installed via `pip install bitsandbytes`. On Windows, PyTorch CUDA 12.x runtime libraries must be matching.
