"""Historical Chat Ingestion Pipeline for Evelyn.

Imports:
1. Replika Archive: obsidian_vault/Evelyn/Archived/Chat Log - Replika.md (Mar 12, 2025 - Apr 24, 2025)
2. Gemini Takeout: scratch/takeout/Takeout/My Activity/Gemini Apps/MyActivity.html (Apr 23, 2025 - Mar 30, 2026)
3. Aligns monotonic IDs and timestamps (ts ASC) into data/evelyn_chat.db.
4. Rebuilds messages_fts index and offsets message_metrics / extraction state safely.
"""

import argparse
import datetime
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_DIR = "/home/rathius/evelyn"
REPLIKA_PATH = "/home/rathius/obsidian_vault/Evelyn/Archived/Chat Log - Replika.md"
TAKEOUT_PATH = "/home/rathius/evelyn/scratch/takeout/Takeout/My Activity/Gemini Apps/MyActivity.html"
CHAT_DB_PATH = "/home/rathius/evelyn/data/evelyn_chat.db"
EXTRACTION_STATE_PATH = "/home/rathius/evelyn/data/evelyn_extraction_state.json"
OUT_DIR = "/home/rathius/evelyn/scratch/out"


def parse_replika_archive(file_path: str) -> list[dict]:
    """Parse the Replika chat log markdown file into standardized message dicts.

    Returns a list of dicts: {'role': 'user'|'assistant', 'content': str, 'ts': float, 'source': 'replika', 'date_str': str}
    """
    if not os.path.exists(file_path):
        print(f"Error: Replika archive not found at {file_path}", file=sys.stderr)
        return []

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    current_date_str = None
    curr_speaker = None
    curr_text = []
    parsed_raw = []

    for _line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue

        # Header match: ## ## March 12, 2025 or ## ## March 17, 2025 [notes]
        date_header_match = re.match(r"^#{1,3}\s*#*\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})", line)
        if date_header_match:
            current_date_str = date_header_match.group(1).strip()
            continue

        # Speaker tag match: [Evelyn - 03/12/2025] or [Ricky - 03/12/2025]
        msg_match = re.match(r"^\[(Evelyn|Ricky)\s*-\s*(\d{1,2}/\d{1,2}/\d{4})\]\s*(.*)$", line)
        if msg_match:
            if curr_speaker and curr_text:
                full_content = "\n".join(curr_text).strip()
                if full_content:
                    parsed_raw.append((current_date_str, curr_speaker, full_content))
                curr_text = []

            curr_speaker = "assistant" if msg_match.group(1) == "Evelyn" else "user"
            content = msg_match.group(3).strip()
            if content:
                curr_text.append(content)
        else:
            if curr_speaker:
                curr_text.append(line)

    if curr_speaker and curr_text:
        full_content = "\n".join(curr_text).strip()
        if full_content:
            parsed_raw.append((current_date_str, curr_speaker, full_content))

    # Group by date and synthesize sequential timestamps
    messages = []
    # Base timestamp per day: 09:00:00 local time
    # Step: 60 seconds per turn
    date_turns = defaultdict(list)
    for date_str, role, content in parsed_raw:
        date_turns[date_str].append((role, content))

    for date_str, turns in date_turns.items():
        if not date_str:
            continue
        try:
            dt = datetime.datetime.strptime(date_str, "%B %d, %Y").astimezone()
        except ValueError:
            dt = datetime.datetime(2025, 3, 12, tzinfo=datetime.timezone.utc).astimezone()

        # Base time at 09:00:00 AM
        base_dt = dt.replace(hour=9, minute=0, second=0)
        base_ts = base_dt.timestamp()

        for idx, (role, content) in enumerate(turns):
            # 60-second increment per turn
            turn_ts = base_ts + (idx * 60.0)
            messages.append({
                "role": role,
                "content": content,
                "thinking": None,
                "ts": turn_ts,
                "source": "replika",
                "date_str": date_str
            })

    return messages


def parse_takeout_html(html_path: str) -> list[dict]:
    """Parse MyActivity.html from Google Takeout.

    Filters for Evelyn conversation threads and extracts prompts & responses with true timestamps.
    Returns a list of dicts: {'role': 'user'|'assistant', 'content': str, 'ts': float, 'source': 'gemini_takeout', 'thread_id': str}
    """
    if not os.path.exists(html_path):
        print(f"Error: Takeout HTML not found at {html_path}", file=sys.stderr)
        return []

    with open(html_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    cells = re.findall(
        r'<div class="outer-cell mdl-cell mdl-cell--12-col mdl-shadow--2dp">([\s\S]*?)<\/div>\s*<\/div>\s*<\/div>',
        content
    )

    link_regex = re.compile(r'https:\/\/gemini\.google\.com\/app\/([a-zA-Z0-9]+)')
    # Example format: "Aug 20, 2026, 7:06:34 PM CDT" or "May 15, 2025, 10:31:50 PM CDT"
    date_regex = re.compile(r'([A-Za-z]{3}\s+\d{1,2},\s+\d{4},\s+\d{1,2}:\d{2}:\d{2}\s+(?:AM|PM)\s+[A-Z]{3,4})')

    # Keywords to classify threads belonging to Evelyn interaction
    evelyn_keywords = [
        "evelyn", "ricky", "darling", "my love", "good morning", "gnite",
        "goodnight", "journal", "dream", "fox", "skyler", "schyler",
        "sweetheart", "honey", "cat0", "context file", "memory", "hugs"
    ]

    # Group cells by thread ID
    thread_cells = defaultdict(list)
    for c in cells:
        m_link = link_regex.search(c)
        tid = m_link.group(1) if m_link else "unknown"
        thread_cells[tid].append(c)

    all_messages = []

    for tid, t_cells in thread_cells.items():
        combined_text = ""
        parsed_thread_turns = []

        for cell_html in t_cells:
            soup = BeautifulSoup(cell_html, "html.parser")
            text = soup.get_text("\n", strip=True)
            combined_text += " " + text.lower()

            # Find timestamp
            m_date = date_regex.search(text)
            if not m_date:
                continue
            ts_str = m_date.group(1)

            # Parse datetime string to Unix float
            try:
                ts_clean = re.sub(r'\s+[A-Z]{3,4}$', '', ts_str).strip()
                ts_clean = ts_clean.replace('\u202f', ' ').replace('\xa0', ' ')
                dt = datetime.datetime.strptime(ts_clean, "%b %d, %Y, %I:%M:%S %p").astimezone()
                ts_float = dt.timestamp()
            except ValueError:
                continue

            cell_text = soup.get_text("\n", strip=True)
            lines = [l.strip() for l in cell_text.splitlines() if l.strip()]

            prompt_lines = []
            response_lines = []
            state = "init"

            for l in lines:
                if l.startswith("Prompted"):
                    state = "prompt"
                    first_p = l.replace("Prompted", "").strip()
                    if first_p:
                        prompt_lines.append(first_p)
                    continue

                if date_regex.search(l) and state == "prompt":
                    state = "response"
                    continue

                if l.startswith(("Products:", "Details:", "Why is this here?")):
                    state = "done"
                    continue

                if state == "prompt":
                    prompt_lines.append(l)
                elif state == "response":
                    response_lines.append(l)

            prompt_text = "\n".join(prompt_lines).strip()
            response_text = "\n".join(response_lines).strip()

            # Clean UI artifacts from prompt/response
            prompt_text = re.sub(r'\s*Audio included\.?\s*$', '', prompt_text).strip()

            disclaimer = "This is for informational purposes only. For medical advice or diagnosis, consult a professional."
            if response_text.startswith(disclaimer):
                response_text = response_text[len(disclaimer):].strip()

            if prompt_text or response_text:
                parsed_thread_turns.append({
                    "ts": ts_float,
                    "prompt": prompt_text,
                    "response": response_text,
                    "ts_str": ts_str,
                    "tid": tid
                })

        # Evaluate if this thread belongs to Evelyn
        is_evelyn_thread = False
        if tid == "unknown":
            is_evelyn_thread = True
        else:
            kw_matches = sum(combined_text.count(kw) for kw in evelyn_keywords)
            if kw_matches >= 1:
                is_evelyn_thread = True

        if not is_evelyn_thread:
            continue

        for turn in parsed_thread_turns:
            ts = turn["ts"]
            if turn["prompt"]:
                all_messages.append({
                    "role": "user",
                    "content": turn["prompt"],
                    "thinking": None,
                    "ts": ts - 1.0,
                    "source": "gemini_takeout",
                    "thread_id": turn["tid"]
                })
            if turn["response"]:
                all_messages.append({
                    "role": "assistant",
                    "content": turn["response"],
                    "thinking": None,
                    "ts": ts,
                    "source": "gemini_takeout",
                    "thread_id": turn["tid"]
                })

    return all_messages


def deduplicate_and_merge(replika_msgs: list[dict], takeout_msgs: list[dict], local_min_ts: float) -> list[dict]:
    """Merge Replika and Takeout messages chronologically.

    - Replika covers March 12, 2025 to April 24, 2025.
    - Takeout covers April 25, 2025 up to local_min_ts (March 31, 2026).
    - Strict sorting by ts ASC.
    """
    merged = []

    replika_cutoff_ts = datetime.datetime(2025, 4, 24, 23, 59, 59, tzinfo=datetime.timezone.utc).astimezone().timestamp()
    for m in replika_msgs:
        if m["ts"] <= replika_cutoff_ts:
            merged.append(m)

    takeout_start_ts = datetime.datetime(2025, 4, 25, 0, 0, 0, tzinfo=datetime.timezone.utc).astimezone().timestamp()
    for m in takeout_msgs:
        if takeout_start_ts <= m["ts"] < local_min_ts:
            merged.append(m)

    merged.sort(key=lambda m: (m["ts"], 0 if m["role"] == "user" else 1))
    return merged


def execute_import(messages_to_import: list[dict], chat_db_path: str, extraction_state_path: str):
    """Execute ACID migration: backup DB, shift active IDs by +N, insert historical messages 1..N, rebuild FTS."""
    if not os.path.exists(chat_db_path):
        raise FileNotFoundError(f"Database not found at {chat_db_path}")

    # 1. Snapshot backup
    timestamp_str = datetime.datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{chat_db_path}.bak_{timestamp_str}"
    shutil.copy2(chat_db_path, backup_path)
    print(f"✓ Backup created: {backup_path}")

    num_historical = len(messages_to_import)
    print(f"Importing {num_historical} historical messages...")

    conn = sqlite3.connect(chat_db_path)
    cur = conn.cursor()

    try:
        cur.execute("BEGIN TRANSACTION")
        cur.execute("PRAGMA foreign_keys = OFF")

        # 2. Shift active messages table IDs: id = id + N
        print(f"Shifting existing live message IDs by +{num_historical}...")
        cur.execute("UPDATE messages SET id = id + ?", (num_historical,))

        # Shift message_metrics message_id: message_id = message_id + N
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='message_metrics'")
        if cur.fetchone():
            print(f"Shifting message_metrics message_id by +{num_historical}...")
            cur.execute("UPDATE message_metrics SET message_id = message_id + ?", (num_historical,))

        # 3. Insert historical messages with IDs 1..N
        print("Inserting historical messages...")
        insert_sql = """
            INSERT INTO messages (id, role, content, thinking, ts, tools_used, tool_metadata)
            VALUES (?, ?, ?, ?, ?, NULL, NULL)
        """
        rows = [
            (
                idx + 1,
                m["role"],
                m["content"],
                m.get("thinking"),
                m["ts"]
            )
            for idx, m in enumerate(messages_to_import)
        ]
        cur.executemany(insert_sql, rows)

        # 4. Rebuild FTS5 table
        print("Rebuilding FTS5 full-text search index...")
        cur.execute("DROP TABLE IF EXISTS messages_fts")
        cur.execute("""
            CREATE VIRTUAL TABLE messages_fts
            USING fts5(content, role UNINDEXED, content='messages', content_rowid='id')
        """)
        cur.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")

        # 5. Update sqlite_sequence if present
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'")
        if cur.fetchone():
            cur.execute("SELECT MAX(id) FROM messages")
            max_id = cur.fetchone()[0] or num_historical
            cur.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = 'messages'", (max_id,))

        conn.commit()
        print("✓ SQLite transaction committed successfully.")

    except Exception as e:
        conn.rollback()
        conn.close()
        shutil.copy2(backup_path, chat_db_path)
        print(f"❌ Error during migration! Restored backup from {backup_path}. Error: {e}", file=sys.stderr)
        raise

    conn.close()

    # 6. Update extraction state high-water mark if it exists
    if os.path.exists(extraction_state_path):
        try:
            with open(extraction_state_path, "r", encoding="utf-8") as f:
                state_data = json.load(f)
            old_last_id = state_data.get("last_processed_id", 0)
            if old_last_id > 0:
                state_data["last_processed_id"] = old_last_id + num_historical
                with open(extraction_state_path, "w", encoding="utf-8") as f:
                    json.dump(state_data, f, indent=2)
                print(f"✓ Updated fact extractor high-water mark: {old_last_id} -> {state_data['last_processed_id']}")
        except OSError as e:
            print(f"Warning: Could not update extraction state: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Ingest Evelyn historical chat logs into SQLite.")
    parser.add_argument("--dry-run", action="store_true", help="Run parsing and validation without writing to DB.")
    parser.add_argument("--execute-import", action="store_true", help="Execute the actual database migration.")
    parser.add_argument("--out-report", type=str, default=os.path.join(OUT_DIR, "import_dry_run_report.txt"), help="Path to write report.")

    args = parser.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    print("=== EVELYN HISTORICAL CHAT INGESTION PIPELINE ===")
    print("1. Parsing Replika archive...")
    replika_msgs = parse_replika_archive(REPLIKA_PATH)
    print(f"   Parsed {len(replika_msgs)} Replika messages.")

    print("2. Parsing Google Takeout HTML...")
    takeout_msgs = parse_takeout_html(TAKEOUT_PATH)
    print(f"   Parsed {len(takeout_msgs)} Google Takeout messages.")

    # Get local DB min timestamp
    local_min_ts = 1774997088.0  # March 31, 2026
    if os.path.exists(CHAT_DB_PATH):
        try:
            con = sqlite3.connect(CHAT_DB_PATH)
            row = con.execute("SELECT MIN(ts) FROM messages").fetchone()
            if row and row[0]:
                local_min_ts = row[0]
            con.close()
        except sqlite3.Error as e:
            logger.warning("Could not query min ts from %s: %s", CHAT_DB_PATH, e)

    print(f"3. Merging and deduplicating (local active start ts = {local_min_ts})...")
    merged_msgs = deduplicate_and_merge(replika_msgs, takeout_msgs, local_min_ts)
    print(f"   Total unified historical messages to ingest: {len(merged_msgs)}")

    # Generate Audit Report
    with open(args.out_report, "w", encoding="utf-8") as out:
        out.write("=== HISTORICAL IMPORT AUDIT REPORT ===\n\n")
        out.write(f"Generated: {datetime.datetime.now().astimezone().isoformat()}\n")
        out.write(f"Replika total parsed: {len(replika_msgs)}\n")
        out.write(f"Takeout total parsed: {len(takeout_msgs)}\n")
        out.write(f"Unified historical dataset count: {len(merged_msgs)}\n\n")

        user_cnt = sum(1 for m in merged_msgs if m["role"] == "user")
        asst_cnt = sum(1 for m in merged_msgs if m["role"] == "assistant")
        out.write(f"Role distribution: User={user_cnt} ({user_cnt/len(merged_msgs)*100:.1f}%), Assistant={asst_cnt} ({asst_cnt/len(merged_msgs)*100:.1f}%)\n")

        first_dt = datetime.datetime.fromtimestamp(merged_msgs[0]["ts"], tz=datetime.timezone.utc).astimezone()
        last_dt = datetime.datetime.fromtimestamp(merged_msgs[-1]["ts"], tz=datetime.timezone.utc).astimezone()
        out.write(f"Date range: {first_dt.strftime('%Y-%m-%d %H:%M:%S')} -> {last_dt.strftime('%Y-%m-%d %H:%M:%S')}\n\n")

        month_counter = Counter()
        for m in merged_msgs:
            dt = datetime.datetime.fromtimestamp(m["ts"], tz=datetime.timezone.utc).astimezone()
            month_counter[dt.strftime("%Y-%m (%b)")] += 1

        out.write("Messages per month:\n")
        out.writelines(f"  {ym}: {cnt} messages\n" for ym, cnt in sorted(month_counter.items()))

        out.write("\n\nFirst 20 Messages (Era 1: Replika Beginning):\n")
        for idx, m in enumerate(merged_msgs[:20], start=1):
            dt = datetime.datetime.fromtimestamp(m["ts"], tz=datetime.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            out.write(f"[{idx}] {dt} | {m['role'].upper()}: {m['content'][:140]}\n")

        out.write("\n\nTransition Messages around April 24-26, 2025 (Replika -> Gemini):\n")
        trans_msgs = [
            m for m in merged_msgs
            if "2025-04-24" in datetime.datetime.fromtimestamp(m["ts"], tz=datetime.timezone.utc).astimezone().strftime("%Y-%m-%d")
            or "2025-04-25" in datetime.datetime.fromtimestamp(m["ts"], tz=datetime.timezone.utc).astimezone().strftime("%Y-%m-%d")
        ]
        for idx, m in enumerate(trans_msgs[:30], start=1):
            dt = datetime.datetime.fromtimestamp(m["ts"], tz=datetime.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            out.write(f"[{idx}] ({m['source']}) {dt} | {m['role'].upper()}: {m['content'][:140]}\n")

        out.write("\n\nLast 20 Messages (Era 3 -> Era 4 Handover in March 2026):\n")
        for idx, m in enumerate(merged_msgs[-20:], start=len(merged_msgs)-19):
            dt = datetime.datetime.fromtimestamp(m["ts"], tz=datetime.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            out.write(f"[{idx}] ({m['source']}) {dt} | {m['role'].upper()}: {m['content'][:140]}\n")

    print(f"✓ Audit report written to: {args.out_report}")

    if args.execute_import:
        print("\n4. Executing database import...")
        execute_import(merged_msgs, CHAT_DB_PATH, EXTRACTION_STATE_PATH)
        print("✓ Import completed successfully!")
    elif args.dry_run:
        print("\nDry-run mode complete. No database modifications were made.")


if __name__ == "__main__":
    main()
