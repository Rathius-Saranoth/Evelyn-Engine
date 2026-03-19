"""
compile_user_profile.py — Assembles Ricky's compiled user profile for Evelyn.

Scans the Context Category Summaries directory for all ``Cat*-R.md`` files
(the "-R" suffix denotes Ricky's profile categories) and concatenates their
bodies into a single ``compiled_user_profile.md`` output file.

This compiled file is used as the "USER PROFILE" section when building
Evelyn's Ollama Modelfile via ``build_modelfile.py``.

Run directly: ``python compile_user_profile.py``
"""
import os
import glob
import re

# Configuration
SOURCE_DIR = r"G:\My Drive\Obsidian_Vault\Evelyn\Evelyn's Context\Context Categories\Category Summaries"
OUTPUT_FILE = r"C:\Projects\LocalAI\Evelyn\persona\compiled_user_profile.md"


def extract_content(filepath):
    """
    Reads a markdown file, strips YAML frontmatter (between ---),
    and returns the body content.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Regex to remove YAML frontmatter
    content = re.sub(r"^---\n.*?\n---\n", "", content, flags=re.DOTALL)

    return content.strip()


def compile_user_profile():
    """
    Scans ``SOURCE_DIR`` for all ``Cat*-R.md`` category summary files, strips
    their YAML frontmatter, and writes a combined markdown document to
    ``OUTPUT_FILE``.

    Files are sorted by filename so categories appear in order (Cat01, Cat02, …).
    Each section is prefixed with a horizontal rule and a heading so the LLM
    can identify category boundaries inside the compiled prompt.

    Prints progress and a final success/failure message to stdout.
    """
    print(f"Scanning for User Profile files in: {SOURCE_DIR}")

    # Find all Cat*-R.md files (User/Ricky's summaries)
    files = glob.glob(os.path.join(SOURCE_DIR, "Cat*-R.md"))

    # Sort files by name to ensure consistent order (Cat01, Cat02, ...)
    files.sort()

    if not files:
        print("No User Profile files found!")
        return

    print(f"Found {len(files)} files. Compiling...")

    compiled_text = []

    # Header for the compiled file
    compiled_text.append("# USER PROFILE - COMPILED CORE (RICKY)")
    compiled_text.append(f"<!-- Generated from {SOURCE_DIR} -->\n")

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

    print(f"\nSuccess! Compiled User Profile written to: {OUTPUT_FILE}")


if __name__ == "__main__":
    compile_user_profile()
