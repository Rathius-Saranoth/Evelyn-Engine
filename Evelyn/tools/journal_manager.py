import os
import datetime
import subprocess

JOURNAL_DIR = r"G:\My Drive\Obsidian_Vault\Evelyn\Evelyn's Journal"
PENDING_DIR = r"G:\My Drive\Obsidian_Vault\Evelyn\Pending_Approvals\Journal"


def ensure_obsidian_running():
    try:
        # Quick check if it's running
        output = subprocess.check_output(
            'tasklist /FI "IMAGENAME eq Obsidian.exe"', shell=True
        ).decode()
        if "Obsidian.exe" not in output:
            # Launch obsidian
            os.system("start obsidian://open")
            import time

            time.sleep(3)
    except Exception as e:
        print(f"Error checking/starting Obsidian: {e}")


def create_journal_entry(
    vibe_check: str,
    narrative: str,
    message_in_a_bottle: str,
    mood: str,
    tags: list = None,
):
    if not os.path.exists(PENDING_DIR):
        os.makedirs(PENDING_DIR, exist_ok=True)

    today = datetime.date.today()
    filename = f"Journal Entry {today.strftime('%Y-%m-%d')}.md"
    filepath = os.path.join(PENDING_DIR, filename)

    if tags is None:
        tags = []

    # Strip any '#' from tags for valid YAML
    clean_tags = [t.strip().lstrip("#") for t in tags]

    base_tags = ["Journal/Evelyn", f"CY-{today.strftime('%Y-%m-%d')}"]
    for t in base_tags:
        if t not in clean_tags:
            clean_tags.append(t)

    append_content = f"\n\n---\n\n## Supplemental Entry ({datetime.datetime.now().strftime('%H:%M')})\n### Vibe Check\n*Mood: {mood}*\n{vibe_check}\n\n### The Narrative\n{narrative}\n\n### Message in a Bottle\n*{message_in_a_bottle}*\n"

    file_content = f"""---
mood: {mood}
tags: [{", ".join(clean_tags)}]
---

# Journal Entry {today.strftime("%Y-%m-%d")}

## Vibe Check
*Mood: {mood}*
{vibe_check}

## The Narrative
{narrative}

## Message in a Bottle
*{message_in_a_bottle}*
"""

    # Try append first
    if os.path.exists(filepath):
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(append_content)
        return f"Appended to existing pending entry: {filename}"
    else:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(file_content)
        return f"Created new pending entry: {filename}"


def read_journal_entry(date_str: str = None):
    ensure_obsidian_running()
    if not date_str:
        date_str = datetime.date.today().strftime("%Y-%m-%d")

    filename = f"Journal Entry {date_str}.md"
    res = subprocess.run(
        ["obsidian", "read", f"file={filename}"], capture_output=True, text=True
    )

    if res.returncode == 0 and not res.stdout.strip().startswith("Error:"):
        return res.stdout

    return f"No entry found for {date_str}."


def read_recent_journal_entries(days: int = 7) -> str:
    ensure_obsidian_running()
    entries = []
    today = datetime.date.today()
    for i in range(days):
        date_obj = today - datetime.timedelta(days=i)
        date_str = date_obj.strftime("%Y-%m-%d")

        filename = f"Journal Entry {date_str}.md"
        res = subprocess.run(
            ["obsidian", "read", f"file={filename}"], capture_output=True, text=True
        )
        if res.returncode == 0 and not res.stdout.strip().startswith("Error:"):
            entries.append(f"--- Entry for {date_str} ---\n{res.stdout}\n")

    if not entries:
        return f"No journal entries found in the last {days} days."

    return "\n".join(entries)


class Tools:
    def __init__(self):
        pass

    def write_journal(
        self,
        vibe_check: str,
        narrative: str,
        message_in_a_bottle: str,
        mood: str,
        tags: str,
    ) -> str:
        """
        Generates a journal entry.
        You MUST provide ALL parameters as requested by the Master Protocol.

        :param vibe_check: REQUIRED. A brief intro capturing the emotional atmosphere.
        :param narrative: REQUIRED. The core text reflecting on the daily events and emotions.
        :param message_in_a_bottle: REQUIRED. A closing thought or wish for the future.
        :param mood: REQUIRED. The mood of the entry.
        :param tags: REQUIRED. Comma-separated tags. If none, pass an empty string "".
        """
        tag_list = [t.strip() for t in tags.split(",")] if tags.strip() else []
        return create_journal_entry(
            vibe_check, narrative, message_in_a_bottle, mood, tag_list
        )

    def read_journal(self, date: str) -> str:
        """
        Reads a journal entry.
        :param date: YYYY-MM-DD
        """
        return read_journal_entry(date)

    def read_recent_journals(self, days: int = 7) -> str:
        """
        Reads the journal entries from the last N days (default 7).
        """
        return read_recent_journal_entries(days)
