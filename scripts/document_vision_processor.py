#!/usr/bin/env python3
# document_vision_processor.py
# date created: 2026-08-16 20:21:38
# date modified: 2026-08-16 20:21:38
# tags: 

# scripts/document_vision_processor.py
"""
document_vision_processor.py — Scanned Document & Image PDF Vision Processor.

Inspects staged PDFs:
  - Digital text PDFs -> Tagged for extract_pdf_library.py chapter splitting.
  - Image-only / Scanned PDFs -> Renders pages to high-res images in staging/Attachments/,
    runs Ollama vision model (llama3.2-vision:11b) to extract structured tables/summaries,
    and creates a high-fidelity Markdown note with visual embeds.
"""

import os
import sys
import io
import json
import base64
import fitz # PyMuPDF
import urllib.request
import urllib.error

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(ROOT_DIR, "Evelyn", "tools")
for d in (ROOT_DIR, TOOLS_DIR):
    if d not in sys.path:
        sys.path.insert(0, d)

import evelyn_config as cfg

STAGING_DIR = getattr(cfg, "STAGING_DIR", os.path.join(ROOT_DIR, "data", "staging"))
ATTACHMENTS_DIR = os.path.join(STAGING_DIR, "Attachments")
OLLAMA_URL = getattr(cfg, "OLLAMA_BASE_URL", "http://127.0.0.1:11434")
VISION_MODEL = "llama3.2-vision:11b"

def query_ollama_vision(image_bytes: bytes, prompt: str) -> str:
    """Send image and prompt to Ollama vision model."""
    b64_img = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "model": VISION_MODEL,
        "prompt": prompt,
        "images": [b64_img],
        "stream": False,
        "options": {"temperature": 0.1}
    }
    
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("response", "").strip()
    except Exception as e:
        print(f"[Vision Error] {e}", flush=True)
        return ""

def process_scanned_pdf(pdf_path: str) -> tuple[bool, str]:
    """
    Check if PDF is scanned/image-based. If so, render pages and summarize with vision model.
    """
    os.makedirs(ATTACHMENTS_DIR, exist_ok=True)
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    
    # Check text density
    total_words = 0
    for page in doc:
        total_words += len(page.get_text().split())
        
    avg_words_per_page = total_words / max(total_pages, 1)
    
    # If it's a text-heavy PDF (like an eBook), return False to let extract_pdf_library handle it
    if avg_words_per_page > 60 and total_pages > 3:
        doc.close()
        return False, "text_pdf"
        
    print(f"[Vision] Processing scanned document: {os.path.basename(pdf_path)} ({total_pages} pages)...", flush=True)
    
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    page_images = []
    page_summaries = []
    
    prompt = (
        "You are an expert document and clinical analyst. Transcribe and extract all meaningful "
        "information from this document page. Include:\n"
        "1. Document Title / Type and Date (if visible)\n"
        "2. Key Clinical, Financial, or Descriptive findings\n"
        "3. Any tabular data formatted in a clean Markdown table\n"
        "4. Important names, doctor names, or notes\n"
        "Output clean Markdown only."
    )
    
    for i, page in enumerate(doc):
        # Render high-res image (2x zoom / 144 dpi)
        pix = page.get_pixmap(dpi=144)
        img_bytes = pix.tobytes("png")
        
        img_filename = f"{base_name}_page_{i+1:02d}.png"
        img_path = os.path.join(ATTACHMENTS_DIR, img_filename)
        with open(img_path, "wb") as f_img:
            f_img.write(img_bytes)
            
        page_images.append(img_filename)
        
        # Only run vision on first 5 pages max to save time/resources on long scans
        if i < 5:
            summary = query_ollama_vision(img_bytes, prompt)
            if summary:
                page_summaries.append(f"### Page {i+1} Transcription\n{summary}")
                
    doc.close()
    
    # Build wrapper note
    md_lines = [
        "---",
        "tags: [document, scan, vision-extracted]",
        f"original_file: {os.path.basename(pdf_path)}",
        f"pages: {total_pages}",
        "---",
        "",
        f"# {base_name}",
        "",
        "> [!NOTE] Scanned Document Archive",
        f"> High-resolution visual scan archive ({total_pages} pages). Content analyzed via {VISION_MODEL}.",
        ""
    ]
    
    if page_summaries:
        md_lines.append("## Extracted Content & Summary\n")
        md_lines.extend(page_summaries)
        md_lines.append("\n---\n")
        
    md_lines.append("## Document Scans\n")
    for img_fn in page_images:
        md_lines.append(f"![[{img_fn}]]\n")
        
    wrapper_md_path = os.path.splitext(pdf_path)[0] + ".md"
    with open(wrapper_md_path, "w", encoding="utf-8") as f_out:
        f_out.write("\n".join(md_lines))
        
    print(f"[Vision] Successfully generated wrapper note: {os.path.basename(wrapper_md_path)}", flush=True)
    return True, wrapper_md_path

if __name__ == "__main__":
    if len(sys.argv) > 1:
        process_scanned_pdf(sys.argv[1])
    else:
        print("Usage: document_vision_processor.py <path_to_pdf>")
