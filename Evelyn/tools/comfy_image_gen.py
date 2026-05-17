"""
title: ComfyUI Image Generation
description: A tool that tells ComfyUI to generate an image using a specific prompt and returns the image back to the chat.
author: Ricky
version: 1.1.0
"""

# comfy_image_gen.py
# --- Module Overview ---
# This file is an Evelyn tool registered via evelyn_tools.py.
# It exposes a single callable: `Tools.generate_image(params)`.
#
# The tool accepts five structured prompt fields (ActionParams), injects them
# into named PrimitiveStringMultiline nodes in a ComfyUI API workflow JSON,
# submits the job, waits for completion over a WebSocket, fetches the output
# filename from the generation history, and returns an Obsidian-style markdown
# image embed pointing at the Tailscale public URL of the ComfyUI server.
#
# Configurable via `Valves` (defaults sourced from evelyn_config.py constants):
#   comfyui_url          — Local ComfyUI HTTP endpoint.
#   websocket_url        — Local ComfyUI WebSocket endpoint.
#   public_comfyui_url   — Tailscale/public URL embedded in the returned image link.
#   default_workflow_path— Path to the ComfyUI API-export workflow JSON.
#   output_dir           — Directory where ComfyUI saves generated images.

import json
import urllib.request
import urllib.parse
import uuid
import websocket
from pydantic import BaseModel, Field

# Requires: pip install websocket-client

COMFY_URL = "127.0.0.1:8188"
COMFY_HTTP_URL = f"http://{COMFY_URL}"


class Tools:
    class Valves(BaseModel):
        comfyui_url: str = Field(
            default=COMFY_HTTP_URL, description="The URL of the ComfyUI server"
        )
        websocket_url: str = Field(
            default=COMFY_URL, description="The WebSocket URL for ComfyUI"
        )
        public_comfyui_url: str = Field(
            default="http://ricky-pc.tail0e161b.ts.net:8188",
            description="The public/Tailscale URL to embed in the markdown image.",
        )
        # Replace with your actual basic workflow JSON as a string or load from a file
        default_workflow_path: str = Field(
            default=r"C:\Projects\LocalAI\Evelyn\workflows\comfy_image_gen.json",
            description="Path to the ComfyUI API workflow JSON",
        )
        output_dir: str = Field(
            default=r"C:\Projects\ComfyUI\output",
            description="Where ComfyUI saves its outputs",
        )

    class ActionParams(BaseModel):
        art_and_style: str = Field(
            description='Detailed description of the art medium, artist styles, lighting, and overall aesthetic (e.g., "This image is an oil painting, masterpiece, dramatic lighting by Greg Rutkowski"). DO NOT LEAVE BLANK.'
        )
        camera_style: str = Field(
            description='Detailed description of the camera angle, lens, shot type, and cinematography (e.g., "It uses a low angle, wide shot, 35mm lens, depth of field"). DO NOT LEAVE BLANK.'
        )
        composition_style: str = Field(
            description='Description of how elements are arranged, symmetry, framing, and visual flow (e.g., "Compose it using the rule of thirds, dynamic composition, central framing"). DO NOT LEAVE BLANK.'
        )
        character_description: str = Field(
            description="Highly detailed description of the main subject(s), their clothing, expression, and appearance, or other descriptions if this is an object instead of a character. DO NOT LEAVE BLANK."
        )
        setting_and_actions: str = Field(
            description="What the subject is doing, the environment they are in, or certain motions in the scene. DO NOT LEAVE BLANK."
        )

    def __init__(self):
        self.valves = self.Valves()

    def generate_image(self, params: ActionParams) -> str:
        """
        Generates an image via ComfyUI and returns a markdown embed to the chat.

        Provide highly detailed descriptions for ALL five ActionParams fields.
        DO NOT leave any field blank — sparse prompts produce poor results.

        Workflow injection:
          The tool looks for ``PrimitiveStringMultiline`` nodes in the workflow
          JSON whose ``_meta.title`` matches one of the five field names
          ("Art & Style", "Camera Style", "Composition Style",
          "Character Description", "Setting & Actions") and injects the
          corresponding param value. If none are found (e.g. the workflow was
          changed), it falls back to combining all fields into a single string
          and injecting it into the first ``CLIPTextEncode`` positive prompt node.

        Blocking behaviour:
          This call blocks until ComfyUI signals completion via the WebSocket
          ``executing`` event with ``node: null``. Expect 5–60 seconds depending
          on hardware and resolution.

        Returns:
            str: A natural-language confirmation message containing the exact
            markdown image embed (``![Generated Image](<url>)``) for the Evelyn
            chat UI to render inline. Returns an error string on any failure.
        """
        if isinstance(params, dict):
            params = self.ActionParams(**params)

        client_id = str(uuid.uuid4())

        # Load workflow
        try:
            with open(self.valves.default_workflow_path, "r", encoding="utf-8") as f:
                workflow = json.load(f)
        except Exception as e:
            return f"Error loading ComfyUI workflow from {self.valves.default_workflow_path}: {e}"

        # Inject the prompts into the 5 corresponding PrimitiveStringMultiline nodes
        mappings = {
            "Art & Style": params.art_and_style,
            "Camera Style": params.camera_style,
            "Composition Style": params.composition_style,
            "Character Description": params.character_description,
            "Setting & Actions": params.setting_and_actions,
        }

        injected_count = 0
        for node_id, node_data in workflow.items():
            title = node_data.get("_meta", {}).get("title", "")
            if (
                node_data.get("class_type") == "PrimitiveStringMultiline"
                and title in mappings
            ):
                workflow[node_id]["inputs"]["value"] = mappings[title]
                injected_count += 1

        # Fallback to standard CLIPTextEncode if we didn't inject anything (e.g. they changed workflows)
        if injected_count == 0:
            combined_prompt = f"{params.art_and_style}, {params.camera_style}, {params.composition_style}, {params.character_description}, {params.setting_and_actions}"
            prompt_node_id = None
            for node_id, node_data in workflow.items():
                if node_data.get(
                    "class_type"
                ) == "CLIPTextEncode" and "text" in node_data.get("inputs", {}):
                    if "positive" in str(node_data).lower() or prompt_node_id is None:
                        prompt_node_id = node_id

            if prompt_node_id:
                workflow[prompt_node_id]["inputs"]["text"] = combined_prompt
            else:
                return "Could not determine where to inject the prompt in the workflow JSON."

        # Send request
        p = {"prompt": workflow, "client_id": client_id}
        data = json.dumps(p).encode("utf-8")
        req = urllib.request.Request(
            f"{self.valves.comfyui_url}/prompt",
            data=data,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(req) as response:
                response_data = json.loads(response.read())
                prompt_id = response_data["prompt_id"]
        except Exception as e:
            return f"Error sending request to ComfyUI: {e}. Is ComfyUI running?"

        # Connect to websocket to wait for completion
        ws = websocket.WebSocket()
        try:
            ws.connect(f"ws://{self.valves.websocket_url}/ws?clientId={client_id}")
            while True:
                out = ws.recv()
                if isinstance(out, str):
                    message = json.loads(out)
                    if message["type"] == "executing":
                        data = message["data"]
                        if data["node"] is None and data["prompt_id"] == prompt_id:
                            break  # Execution is done
        except Exception as e:
            return f"Error during ComfyUI websocket communication: {e}"
        finally:
            ws.close()

        # Fetch history to get the output filename
        req = urllib.request.Request(f"{self.valves.comfyui_url}/history/{prompt_id}")
        try:
            with urllib.request.urlopen(req) as response:
                history = json.loads(response.read())
                output_images = []
                for node_id, node_output in history[prompt_id]["outputs"].items():
                    if "images" in node_output:
                        for image in node_output["images"]:
                            output_images.append(image)

                if output_images:
                    img_data = output_images[0]
                    filename = img_data["filename"]
                    subfolder = img_data.get("subfolder", "")
                    type_str = img_data.get("type", "output")

                    try:
                        safe_filename = urllib.parse.quote(filename)
                        safe_subfolder = urllib.parse.quote(subfolder)

                        image_url = f"{self.valves.public_comfyui_url}/view?filename={safe_filename}&type={type_str}&subfolder={safe_subfolder}"
                        markdown_image = f"![Generated Image]({image_url})"

                        return f"Image successfully generated!\n\nPlease present the following EXACT markdown to display the image to the user:\n\n{markdown_image}\n\nYou do not need to repeat or list the image parameters in your response. Just show the image and give a brief, natural conversational reply."
                    except Exception as e:
                        return f"Image generated successfully, but failed to construct the URL: {e}"
                else:
                    return "Image generated, but could not determine output filename from history."
        except Exception as e:
            return f"Error retrieving generation history: {e}"
