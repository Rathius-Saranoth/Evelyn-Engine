# llm_clean_summaries.py
# date created: 2026-05-18 18:43:21
# date modified: 2026-05-18 19:39:40

import os
import re
import sys
import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from collections import defaultdict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import evelyn_config as cfg

KEYWORDS = [
    "Core Identity",
    "Core Values", "Core Values and Beliefs", "Core Values & Beliefs",
    "Emotional Awareness",
    "Communication Style",
    "Preferences", "Preferences & Interests", "Preferences and Interests",
    "Relationship Dynamics",
    "Motivations and Aspirations", "Motivations & Aspirations",
    "Shared Experiences", "Shared Experiences & Daily Events", "Shared Experiences and Daily Events",
    "Cognitive & Decision-Making", "Cognitive and Decision-Making", "Decision-Making Style", "Cognitive Style",
    "Humor, Creativity", "Humor, Creativity, and Play", "Humor, Creativity, & Play",
    "Factual References", "Factual References & Knowledge", "Factual References and Knowledge",
    "Emotional States", "Emotional States & Responses", "Emotional States and Responses",
    "Goals & Future Planning", "Goals and Future Planning",
    "Platform & Environment", "Platform and Environment",
    "The Lexicon", "Lexicon",
    "Protocols & Routines", "Protocols and Routines",
    "Emotional Intelligence",
    "Love Languages",
    "Grounded Truth"
]

PROMPT = """You are an editor cleaning up context entries for an AI memory system.
Your task is to rewrite the summary to REMOVE any explicit mentions of taxonomy labels (like "Relationship Dynamics", "Emotional Intelligence", "Core Values", etc.) and the meta-commentary surrounding them (e.g. "This demonstrates...", "This aligns with...").

Keep ONLY the factual event, action, or observation itself.
Rewrite the sentence naturally if removing the words leaves it broken, but DO NOT add new information.
Do NOT add any introductory text like "Here is the summary" or "Cleaned:". Return ONLY the cleaned summary text.

Original Summary:
{summary}

Cleaned Summary:"""

def call_ollama(prompt: str) -> str:
    url = f"{cfg.OLLAMA_URL}/api/generate"
    data = {
        "model": cfg.MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1
        }
    }
    req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'})
    
    # 300 second timeout for large models falling back to CPU/RAM
    with urllib.request.urlopen(req, timeout=300) as response:
        result = json.loads(response.read().decode('utf-8'))
        text = result.get('response', '')
        
        # Strip <think> block if present
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
        return text

def main():
    ce_dir = Path(cfg.CONTEXT_ENTRIES_DIR)
    if not ce_dir.exists():
        print(f"Error: {ce_dir} does not exist.")
        return
        
    # Build regex pattern for fast matching
    sorted_keywords = sorted(KEYWORDS, key=len, reverse=True)
    pattern = re.compile(r'\b(' + '|'.join(map(re.escape, sorted_keywords)) + r')\b', re.IGNORECASE)
    
    # Gather files
    files_to_process = []
    for root, _, files in os.walk(ce_dir):
        for file in files:
            if not file.endswith(".md"):
                continue
            path = Path(root) / file
            try:
                content = path.read_text(encoding="utf-8")
                match = re.search(r"^(\*\*Summary:\*\*\s*)(.+)$", content, flags=re.MULTILINE)
                if match:
                    summary_text = match.group(2)
                    if pattern.search(summary_text):
                        files_to_process.append((path, match, summary_text))
            except Exception:
                pass
                
    total = len(files_to_process)
    print(f"Found {total} summaries containing taxonomy keywords. Beginning LLM cleanup...")
    print(f"Using model: {cfg.MODEL_NAME}. This will take some time.\n")
    
    success_count = 0
    fail_count = 0
    
    for i, (path, match, summary_text) in enumerate(files_to_process, 1):
        print(f"[{i}/{total}] Processing: {path.name}")
        
        # Check if the file still exists (could have been moved)
        if not path.exists():
            print(f"  -> File missing, skipping.")
            continue
            
        try:
            # Re-read content just in case it changed
            content = path.read_text(encoding="utf-8")
            current_match = re.search(r"^(\*\*Summary:\*\*\s*)(.+)$", content, flags=re.MULTILINE)
            if not current_match:
                continue
                
            prefix = current_match.group(1)
            original_text = current_match.group(2)
            
            # Call LLM
            t0 = time.time()
            cleaned_text = call_ollama(PROMPT.format(summary=original_text))
            t1 = time.time()
            
            # Safety checks: ensure LLM didn't return an empty string or hallucinate a massive response
            if len(cleaned_text) < 10 or len(cleaned_text) > len(original_text) + 50:
                print(f"  -> LLM returned suspicious output (len={len(cleaned_text)}). Skipping to be safe.")
                fail_count += 1
                continue
                
            # Remove any wrapping quotes if the LLM added them
            if cleaned_text.startswith('"') and cleaned_text.endswith('"'):
                cleaned_text = cleaned_text[1:-1]
            if cleaned_text.startswith('**Summary:** '):
                cleaned_text = cleaned_text[13:]
                
            if cleaned_text != original_text:
                new_content = content.replace(current_match.group(0), f"{prefix}{cleaned_text}")
                path.write_text(new_content, encoding="utf-8")
                success_count += 1
                print(f"  -> Cleaned! ({t1-t0:.1f}s)")
            else:
                print(f"  -> Unchanged.")
                
        except urllib.error.URLError as e:
            print(f"  -> Connection error (Ollama might be overloaded): {e}")
            fail_count += 1
            # Add a small delay if Ollama is crashing/struggling
            time.sleep(5)
        except Exception as e:
            print(f"  -> Error: {e}")
            fail_count += 1
            
        # Optional: Sleep briefly between requests to avoid completely redlining the GPU constantly
        time.sleep(0.5)

    print(f"\nFinished! Cleaned: {success_count}, Failed/Skipped: {fail_count}, Total: {total}")

if __name__ == "__main__":
    main()
