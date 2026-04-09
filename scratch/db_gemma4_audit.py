"""
db_gemma4_audit.py — Post-migration health check for Gemma 4 26B.

Checks the chat DB for:
  1. Summary stats (messages since switch, tool call rate)
  2. Empty or placeholder responses (failed generations)
  3. Thinking content presence/absence
  4. Tool call audit (which tools fired, any failures)
  5. Response length distribution (detect truncation)
  6. Content spot-check for known Gemma quirks:
     - Ghost <think> tags leaking into content
     - Malformed JSON remnants in responses
     - Truncated mid-sentence endings
  7. Full text of any suspicious messages

Output: scratch/out/db_gemma4_audit.txt
"""

import sqlite3
import datetime
import re
import os
import statistics

DB_PATH = r"C:\Projects\LocalAI\evelyn_chat.db"
OUT_DIR = r"C:\Projects\LocalAI\scratch\out"
OUT_FILE = os.path.join(OUT_DIR, "db_gemma4_audit.txt")

# Switch timestamp: 2026-04-07 ~20:17 local (UTC-5)
# Using a conservative start: 2026-04-07 20:00 CDT = 2026-04-08 01:00 UTC
SWITCH_TS = datetime.datetime(2026, 4, 8, 1, 0, 0).timestamp()

PLACEHOLDER = "[Response interrupted"
THREAD_BREAK = "[THREAD_BREAK]"

os.makedirs(OUT_DIR, exist_ok=True)

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT * FROM messages WHERE ts >= ? ORDER BY id ASC", (SWITCH_TS,)
).fetchall()
con.close()

lines = []
def h(title): lines.append(f"\n{'='*60}\n{title}\n{'='*60}")
def p(text=""): lines.append(str(text))

h("GEMMA 4 26B — POST-MIGRATION AUDIT")
p(f"DB: {DB_PATH}")
p(f"Auditing messages since: {datetime.datetime.fromtimestamp(SWITCH_TS).strftime('%Y-%m-%d %H:%M')} (model switch)")
p(f"Total messages in window: {len(rows)}")

# --- 1. Summary stats ---
h("1. MESSAGE SUMMARY")
user_msgs   = [r for r in rows if r["role"] == "user" and r["content"] != THREAD_BREAK]
asst_msgs   = [r for r in rows if r["role"] == "assistant"]
thread_breaks = [r for r in rows if r["content"] == THREAD_BREAK]
tool_msgs   = [r for r in asst_msgs if r["tools_used"]]

p(f"User messages:       {len(user_msgs)}")
p(f"Assistant messages:  {len(asst_msgs)}")
p(f"Thread breaks:       {len(thread_breaks)}")
p(f"Tool calls fired:    {len(tool_msgs)}  ({len(tool_msgs)/max(len(asst_msgs),1)*100:.0f}% of assistant msgs)")

# --- 2. Failed / placeholder responses ---
h("2. FAILED / PLACEHOLDER RESPONSES")
placeholders = [r for r in asst_msgs if r["content"].startswith(PLACEHOLDER)]
empty        = [r for r in asst_msgs if not r["content"].strip()]
p(f"Placeholder responses:  {len(placeholders)}")
p(f"Empty responses:        {len(empty)}")
if placeholders:
    p("\nPlaceholder details:")
    for r in placeholders:
        ts = datetime.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S")
        p(f"  [{ts}] id={r['id']}: {r['content'][:80]}")

# --- 3. Thinking content ---
h("3. THINKING CONTENT PRESENCE")
with_think  = [r for r in asst_msgs if r["thinking"]]
without_think = [r for r in asst_msgs if not r["thinking"]]
p(f"Responses WITH thinking:    {len(with_think)}")
p(f"Responses WITHOUT thinking: {len(without_think)}")
p(f"(Think=True is set in config; all responses should have thinking unless model skipped it)")
if without_think:
    p("\nMessages missing thinking (first 5):")
    for r in without_think[:5]:
        ts = datetime.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S")
        p(f"  [{ts}] id={r['id']} len={len(r['content'])} chars | tools={r['tools_used']}")

# --- 4. Tool call audit ---
h("4. TOOL CALL AUDIT")
tool_counter = {}
for r in tool_msgs:
    for t in r["tools_used"].split(","):
        t = t.strip()
        tool_counter[t] = tool_counter.get(t, 0) + 1
if tool_counter:
    p("Tools fired (count):")
    for tool, count in sorted(tool_counter.items(), key=lambda x: -x[1]):
        p(f"  {tool:<35} {count}x")
else:
    p("No tools fired in this window.")

# --- 5. Response length distribution ---
h("5. RESPONSE LENGTH DISTRIBUTION")
lengths = [len(r["content"]) for r in asst_msgs if not r["content"].startswith(PLACEHOLDER)]
if lengths:
    p(f"Min:    {min(lengths):>6} chars")
    p(f"Median: {statistics.median(lengths):>6.0f} chars")
    p(f"Max:    {max(lengths):>6} chars")
    p(f"Mean:   {statistics.mean(lengths):>6.0f} chars")
    # Flag very short responses (potential truncation or refusals)
    short = [r for r in asst_msgs if len(r["content"]) < 80 and not r["content"].startswith(PLACEHOLDER) and r["content"].strip()]
    p(f"\nSuspiciously short responses (<80 chars): {len(short)}")
    for r in short:
        ts = datetime.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S")
        p(f"  [{ts}] id={r['id']}: {repr(r['content'][:100])}")

# --- 6. Gemma-specific quirk checks ---
h("6. GEMMA 4 QUIRK CHECKS")

# 6a: Ghost <think> tags leaking into content
think_leak = [r for r in asst_msgs if "<think>" in r["content"].lower() or "</think>" in r["content"].lower()]
p(f"[6a] Ghost <think> tags in content field: {len(think_leak)}")
for r in think_leak:
    ts = datetime.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S")
    idx = r["content"].lower().find("<think>")
    if idx == -1: idx = r["content"].lower().find("</think>")
    p(f"  [{ts}] id={r['id']}: ...{r['content'][max(0,idx-20):idx+50]}...")

# 6b: Malformed JSON fragments (tool call bleed-through)
json_bleed = [r for r in asst_msgs if re.search(r'"function"\s*:|"tool_calls"\s*:|"arguments"\s*:', r["content"])]
p(f"\n[6b] JSON/tool-call fragments in content field: {len(json_bleed)}")
for r in json_bleed:
    ts = datetime.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S")
    p(f"  [{ts}] id={r['id']}: {r['content'][:150]}")

# 6c: Responses ending mid-sentence (potential truncation)
# Heuristic: content doesn't end with punctuation or common closers
def ends_abruptly(text):
    text = text.strip()
    if not text: return False
    last = text[-1]
    return last not in ".!?\"'*_)~`\n" and not text.endswith("...") and len(text) > 200

abrupt = [r for r in asst_msgs if ends_abruptly(r["content"])]
p(f"\n[6c] Responses ending abruptly (no terminal punctuation): {len(abrupt)}")
for r in abrupt:
    ts = datetime.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S")
    p(f"  [{ts}] id={r['id']} ({len(r['content'])} chars): ...{repr(r['content'][-80:])}")

# 6d: Responses containing markdown code blocks with raw tool call syntax
code_tool = [r for r in asst_msgs if re.search(r'```\s*(?:json|tool_call)', r["content"], re.IGNORECASE)]
p(f"\n[6d] Code blocks with tool/json syntax in content: {len(code_tool)}")

# 6e: Any content that looks like it's describing the tool schema rather than completing it
schema_bleed = [r for r in asst_msgs if "required" in r["content"] and "properties" in r["content"]]
p(f"\n[6e] Content containing schema keywords (required/properties): {len(schema_bleed)}")

# --- 7. Full text of anything suspicious ---
all_suspicious = set(r["id"] for r in placeholders + think_leak + json_bleed + abrupt)
h(f"7. FULL TEXT OF SUSPICIOUS MESSAGES ({len(all_suspicious)} flagged)")
if all_suspicious:
    con2 = sqlite3.connect(DB_PATH)
    con2.row_factory = sqlite3.Row
    for msg_id in sorted(all_suspicious):
        r = con2.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
        ts = datetime.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S")
        p(f"\n--- id={r['id']} | {ts} | role={r['role']} | tools={r['tools_used']} ---")
        p(f"CONTENT ({len(r['content'])} chars):")
        p(r["content"][:1500] + ("...[TRUNCATED]" if len(r["content"]) > 1500 else ""))
        if r["thinking"]:
            p(f"\nTHINKING ({len(r['thinking'])} chars):")
            p(r["thinking"][:500] + "...[TRUNCATED]" if len(r["thinking"]) > 500 else r["thinking"])
    con2.close()
else:
    p("No suspicious messages found. Clean bill of health.")

# --- Write output ---
output = "\n".join(lines)
with open(OUT_FILE, "w", encoding="utf-8") as f:
    f.write(output)
print(f"Done. Output: {OUT_FILE}")
