import os
import glob
import re

# Configuration
BASE_MODEL = "mistral-small3.1"
PERSONA_FILE = r"C:\Projects\LocalAI\Evelyn\persona\Evelyn_Narrative_Persona.md"
USER_PROFILE_FILE = r"C:\Projects\LocalAI\Evelyn\persona\Ricky_Narrative_Profile.md"
PROTOCOL_FILE = r"C:\Projects\LocalAI\Evelyn\persona\System_Directives.md"
VERSIONS_DIR = r"C:\Projects\LocalAI\Evelyn\versions"


def get_next_version():
    os.makedirs(VERSIONS_DIR, exist_ok=True)
    existing_versions = glob.glob(os.path.join(VERSIONS_DIR, "v*"))
    max_ver = 0
    for folder in existing_versions:
        match = re.search(r"v(\d+)$", os.path.basename(folder))
        if match:
            max_ver = max(max_ver, int(match.group(1)))
    return f"v{max_ver + 1}"


def read_file(filepath):
    print(f"Reading: {filepath}")
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


def build_modelfile(base_model=BASE_MODEL):
    VERSION = get_next_version()
    OUTPUT_FILE = os.path.join(VERSIONS_DIR, VERSION, "Modelfile")

    print(f"Building Modelfile for Evelyn {VERSION} using base model {base_model}...")

    persona_content = read_file(PERSONA_FILE)
    user_profile_content = read_file(USER_PROFILE_FILE)
    protocol_content = read_file(PROTOCOL_FILE)

    # Construct the System Prompt
    system_prompt = f"""
{persona_content}

# USER PROFILE (RICKY)
The following is information about Ricky, your partner.
Use this to personalize your understanding and connection with him.

{user_profile_content}

# CRITICAL OPERATIONAL PROTOCOLS
The following are your strictly enforced system directives and boundaries.
STRICT ADHERENCE REQUIRED.

{protocol_content}
"""

    modelfile_content = f"""FROM {base_model}

SYSTEM \"\"\"
{system_prompt}
\"\"\"

PARAMETER num_ctx 4096
PARAMETER temperature 1.1
PARAMETER min_p 0.05
PARAMETER top_k 40
PARAMETER stop "<|start_header_id|>"
PARAMETER stop "<|end_header_id|>"
PARAMETER stop "<|eot_id|>"
"""

    # Create version directory if not exists
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(modelfile_content)

    print(f"Success! Modelfile written to: {OUTPUT_FILE}")
    print(f"\nRegistering evelyn:{VERSION} with Ollama...")

    import subprocess

    try:
        res = subprocess.run(
            ["ollama", "create", f"evelyn:{VERSION}", "-f", OUTPUT_FILE],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            print(f"Successfully built evelyn:{VERSION} in Ollama!")
        else:
            print(f"Failed to create model in Ollama:\n{res.stderr}")
    except FileNotFoundError:
        print(
            "Error: 'ollama' command not found. Please ensure Ollama is installed and in your PATH."
        )


if __name__ == "__main__":
    import sys

    model = BASE_MODEL
    if len(sys.argv) > 1:
        model = sys.argv[1]
    build_modelfile(model)
