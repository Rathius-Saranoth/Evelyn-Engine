"""
qwen_tts_server.py — OpenAI-compatible TTS proxy bridging Open WebUI to ComfyUI.

Runs a FastAPI server on ``http://127.0.0.1:5050`` that exposes the OpenAI
TTS endpoint format (``POST /v1/audio/speech``) so Open WebUI can use ComfyUI's
Qwen3-TTS node as its voice synthesis backend.

Flow:
  1. Open WebUI sends a JSON body: ``{"model": "...", "input": "<text>", "voice": "..."}`.
  2. The server loads the ComfyUI API-format workflow from ``WORKFLOW_PATH``.
  3. It injects the input text into the TTS node (``target_text``, ``text``,
     or ``prompt`` field, searched in priority order).
  4. Submits the workflow to ComfyUI via HTTP and waits for completion over
     a WebSocket connection.
  5. Locates the output audio file from the ``SaveAudio`` node in the
     generation history.
  6. Returns the audio file as a ``FileResponse`` (``audio/flac``).
  7. Schedules the temp file for deletion 60 seconds after delivery.

Prerequisites:
  - ComfyUI running at ``COMFY_URL`` with the Qwen3-TTS workflow loaded.
  - ``pip install fastapi uvicorn websocket-client`` in the environment.

Run directly: ``python qwen_tts_server.py``
"""
import os
import json
import uuid
import urllib.request
import urllib.parse
import websocket
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn
import asyncio

app = FastAPI(title="Qwen3 TTS ComfyUI Wrapper")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tailscale + local origins
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["*"],
)

COMFY_HOST = "127.0.0.1"
COMFY_PORT = "8188"
COMFY_URL = f"http://{COMFY_HOST}:{COMFY_PORT}"
WS_URL = f"ws://{COMFY_HOST}:{COMFY_PORT}/ws"
OUTPUT_DIR = r"C:\Projects\ComfyUI\output"
WORKFLOW_PATH = r"C:\Projects\LocalAI\Evelyn\workflows\qwen_tts_api.json"

class SpeechRequest(BaseModel):
    model: str = ""
    input: str
    voice: str = ""

async def delete_file_after_delay(filepath: str, delay: int = 60):
    """
    Coroutine: deletes a file after a specified delay in seconds.

    Used as a FastAPI background task to clean up temporary audio files
    after they have been streamed to the client.

    Args:
        filepath: Absolute path to the file to delete.
        delay: Seconds to wait before attempting deletion. Defaults to 60.
    """
    await asyncio.sleep(delay)
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            print(f"Cleaned up temporary audio file: {filepath}")
    except Exception as e:
        print(f"Failed to clean up audio file {filepath}: {str(e)}")

@app.post("/v1/audio/speech")
def generate_speech(data: SpeechRequest, background_tasks: BackgroundTasks):
    """
    Synthesises speech from text using a ComfyUI TTS workflow.

    Accepts an OpenAI-format TTS request body (``model``, ``input``, ``voice``)
    and returns the generated audio as a FLAC file. The ``voice`` and ``model``
    fields are accepted for API compatibility but currently ignored — voice
    selection is managed inside the ComfyUI workflow itself.

    Args:
        data: ``SpeechRequest`` with at minimum a non-empty ``input`` field.
        background_tasks: FastAPI ``BackgroundTasks`` used to schedule cleanup
            of the temporary audio file 60 seconds after delivery.

    Returns:
        FileResponse: The generated FLAC audio file.

    Raises:
        HTTPException 400: ``input`` field is empty.
        HTTPException 500: ComfyUI is unreachable, workflow injection fails,
            WebSocket error, or the output file cannot be located.
    """
    
    # OpenAI format: {"model": "...", "input": "text to say", "voice": "..."}
    text = data.input
    if not text:
        raise HTTPException(status_code=400, detail="Missing 'input' field")

    # Load workflow
    try:
        with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
            workflow = json.load(f)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail=f"Workflow file not found at {WORKFLOW_PATH}")
    
    # Find the node that accepts the text input (usually Qwen3 TTS or similar)
    # We will look for a string input that might contain "text"
    injected = False
    for node_id, node_data in workflow.items():
        inputs = node_data.get("inputs", {})
        # Look for typical text input fields in TTS nodes
        if "target_text" in inputs:
            workflow[node_id]["inputs"]["target_text"] = text
            injected = True
            break
        elif "text" in inputs:
            workflow[node_id]["inputs"]["text"] = text
            injected = True
            break
        elif "prompt" in inputs and isinstance(inputs["prompt"], str):
            workflow[node_id]["inputs"]["prompt"] = text
            injected = True
            break
            
    if not injected:
        raise HTTPException(status_code=500, detail="Could not find a text input node in the ComfyUI workflow JSON.")

    client_id = str(uuid.uuid4())
    p = {"prompt": workflow, "client_id": client_id}
    req_data = json.dumps(p).encode('utf-8')
    req = urllib.request.Request(f"{COMFY_URL}/prompt", data=req_data, headers={'Content-Type': 'application/json'})

    try:
        with urllib.request.urlopen(req) as response:
            resp_data = json.loads(response.read())
            prompt_id = resp_data['prompt_id']
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to communicate with ComfyUI: {str(e)}")

    # Connect to WebSocket to wait for completion
    ws = websocket.WebSocket()
    try:
        ws.connect(f"{WS_URL}?clientId={client_id}")
        while True:
            out = ws.recv()
            if isinstance(out, str):
                msg = json.loads(out)
                if msg['type'] == 'executing':
                    msg_data = msg['data']
                    if msg_data['node'] is None and msg_data['prompt_id'] == prompt_id:
                        break # Execution Done
    except Exception as e:
        ws.close()
        raise HTTPException(status_code=500, detail=f"WebSocket error: {str(e)}")
    finally:
        ws.close()

    # Get history to find output audio file
    hist_req = urllib.request.Request(f"{COMFY_URL}/history/{prompt_id}")
    try:
        with urllib.request.urlopen(hist_req) as response:
            history = json.loads(response.read())
            
            output_audio_filename = None
            output_audio_subfolder = ""
            
            # The Qwen TTS process can break long text into chunks, outputting multiple audio 
            # objects in history. We specifically want the output of the SaveAudio node.
            for node_id, node_output in history[prompt_id]['outputs'].items():
                if workflow.get(node_id, {}).get("class_type") == "SaveAudio":
                     if 'audio' in node_output and len(node_output['audio']) > 0:
                          output_audio_filename = node_output['audio'][0].get('filename')
                          output_audio_subfolder = node_output['audio'][0].get('subfolder', '')
                          break
            
            # Fallback if no SaveAudio node was found
            if not output_audio_filename:
                for node_id, node_output in history[prompt_id]['outputs'].items():
                    if 'audio' in node_output and len(node_output['audio']) > 0:
                         output_audio_filename = node_output['audio'][0].get('filename')
                         output_audio_subfolder = node_output['audio'][0].get('subfolder', '')
                         break
                
            if output_audio_filename:
                filepath = os.path.join(OUTPUT_DIR, output_audio_subfolder, output_audio_filename)
                if os.path.exists(filepath):
                    background_tasks.add_task(delete_file_after_delay, filepath, 60)
                    return FileResponse(filepath, media_type="audio/flac")
                else:
                    raise HTTPException(status_code=500, detail=f"Audio file generated but not found at {filepath}")
            else:
                raise HTTPException(status_code=500, detail="Audio generated, but filename not found in ComfyUI history.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch history: {str(e)}")


if __name__ == "__main__":
    print(f"Starting Qwen3 TTS Wrapper. Please ensure ComfyUI is running at {COMFY_URL}")
    print(f"Also make sure you have saved your TTS workflow as API format to: {WORKFLOW_PATH}")
    uvicorn.run(app, host="127.0.0.1", port=5050)
