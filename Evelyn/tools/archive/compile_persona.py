"""
compile_persona.py — Assembles Evelyn's compiled persona core for Modelfile injection.

Scans the Context Category Summaries directory for all ``Cat*-E.md`` files
(the "-E" suffix denotes Evelyn's identity and personality categories) and
concatenates their bodies into a single ``compiled_persona.md`` output file.

The compiled file is used as the base persona section when building Evelyn's
Ollama Modelfile via ``build_modelfile.py``. It also injects the current
system date so Evelyn has temporal awareness during conversation.

Run directly: ``python compile_persona.py``
"""
import os
import glob
import re

# Configuration
SOURCE_DIR = r"G:\My Drive\Obsidian_Vault\Evelyn\Evelyn's Context\Context Categories\Category Summaries"
OUTPUT_FILE = r"C:\Projects\LocalAI\Evelyn\persona\compiled_persona.md"


def extract_content(filepath):
    """
    Reads a markdown file, strips YAML frontmatter (between ---),
    and returns the body content.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Regex to remove YAML frontmatter
    # Matches --- at start of file, anything in between, and closing ---
    content = re.sub(r"^---\n.*?\n---\n", "", content, flags=re.DOTALL)

    # Optional: Remove the "# Alias/Title" if it's redundant, but for now we keep headers
    # to maintain structure in the compiled prompt.

    return content.strip()


def compile_persona():
    """
    Scans ``SOURCE_DIR`` for all ``Cat*-E.md`` category summary files, strips
    their YAML frontmatter, and writes a combined markdown document to
    ``OUTPUT_FILE``.

    Files are sorted by filename so categories appear in order (Cat01, Cat02, …).
    Prepends the current date (``CURRENT SYSTEM DATE: YYYY-MM-DD``) to give
    Evelyn temporal awareness. Each section is prefixed with a horizontal rule
    and a heading for clear category separation inside the compiled prompt.

    Prints progress and a final success/failure message to stdout.
    """
    print(f"Scanning for persona files in: {SOURCE_DIR}")

    # Find all Cat*-E.md files (Evelyn's summaries)
    # Pattern: Cat??-E.md
    files = glob.glob(os.path.join(SOURCE_DIR, "Cat*-E.md"))

    # Sort files by name to ensure consistent order (Cat01, Cat02, ...)
    files.sort()

    if not files:
        print("No persona files found!")
        return

    print(f"Found {len(files)} files. Compiling...")

    compiled_text = []

    # Header for the compiled file
    compiled_text.append("# EVELYN - COMPILED PERSONA CORE")
    compiled_text.append(f"<!-- Generated from {SOURCE_DIR} -->\n")

    import datetime

    current_date = datetime.date.today().strftime("%Y-%m-%d")
    compiled_text.append(f"CURRENT SYSTEM DATE: {current_date}\n")

    for filepath in files:
        filename = os.path.basename(filepath)
        print(f"Processing: {filename}")

        body = extract_content(filepath)

        # Add a clear separator and the filename as context
        compiled_text.append(f"\n\n--- \n## Section: {filename}\n")
        compiled_text.append(body)

    # Write to output
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(compiled_text))

    print(f"\nSuccess! Compiled persona written to: {OUTPUT_FILE}")


if __name__ == "__main__":
    compile_persona()
