"""
build_modelfile.py — Compiles and registers a versioned Evelyn Ollama model.

Reads three source markdown files and assembles them into an Ollama ``Modelfile``
that is then registered as a new versioned model (``evelyn:vN``) via the
``ollama create`` CLI command.

Source files:
  PERSONA_FILE      — Evelyn's narrative persona (identity, tone, background).
  USER_PROFILE_FILE — Ricky's compiled profile (preferences, context).
  PROTOCOL_FILE     — System directives and operational boundaries.

Versioning:
  Each run increments the version number by scanning ``VERSIONS_DIR`` for
  existing ``vN`` subfolders. The Modelfile is written to ``versions/vN/Modelfile``
  and the model is created in Ollama as ``evelyn:vN``.

Run directly: ``python build_modelfile.py [base_model_name]``
"""
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
    """
    Determines the next sequential version string for a model build.

    Scans ``VERSIONS_DIR`` for folders matching the pattern ``vN`` and returns
    the next integer as a formatted string.

    Returns:
        str: Version string, e.g. ``"v3"`` if ``v1`` and ``v2`` already exist.
            Returns ``"v1"`` if no versioned folders exist yet.
    """
    os.makedirs(VERSIONS_DIR, exist_ok=True)
    existing_versions = glob.glob(os.path.join(VERSIONS_DIR, "v*"))
    max_ver = 0
    for folder in existing_versions:
        match = re.search(r"v(\d+)$", os.path.basename(folder))
        if match:
            max_ver = max(max_ver, int(match.group(1)))
    return f"v{max_ver + 1}"


def read_file(filepath):
    """
    Reads and returns the full text content of a file.

    Args:
        filepath: Absolute path to the file to read.

    Returns:
        str: Complete file contents as a UTF-8 decoded string.
    """
    print(f"Reading: {filepath}")
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


def build_modelfile(base_model=BASE_MODEL):
    """
    Builds the Evelyn Modelfile and registers it with Ollama.

    Pipeline:
      1. Determine the next version string via ``get_next_version()``.
      2. Read the persona, user profile, and protocol markdown files.
      3. Compose a system prompt by combining all three sections.
      4. Write the full Ollama ``Modelfile`` format to ``versions/vN/Modelfile``.
      5. Run ``ollama create evelyn:vN -f <Modelfile>`` to register the model.

    Args:
        base_model: The Ollama base model to use as the ``FROM`` directive.
            Defaults to ``BASE_MODEL`` (currently ``mistral-small3.1``).
    """
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
