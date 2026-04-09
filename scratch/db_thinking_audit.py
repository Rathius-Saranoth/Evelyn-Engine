"""
db_thinking_audit.py — Compare thinking content across model eras.

Splits the DB into pre-switch (Magistral) and post-switch (Gemma 4)
and reports thinking rate, thinking length, and examples.

Output: scratch/out/db_thinking_audit.txt
"""

import sqlite3
import datetime
import statistics
import os

DB_PATH = r"C:\Projects\LocalAI\evelyn_chat.db"
OUT_DIR  = r"C:\Projects\LocalAI\scratch\out"
OUT_FILE = os.path.join(OUT_DIR, "db_thinking_audit.txt")

# Gemma switch: 2026-04-08 ~01:00 UTC
SWITCH_TS = datetime.datetime(2026, 4, 8, 1, 0, 0).timestamp()

os.makedirs(OUT_DIR, exist_ok=True)

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row
all_asst = con.execute(
    "SELECT * FROM messages WHERE role='assistant' AND content NOT LIKE '[Response interrupted%' ORDER BY id ASC"
).fetchall()
con.close()

magistral = [r for r in all_asst if r["ts"] < SWITCH_TS]
gemma4    = [r for r in all_asst if r["ts"] >= SWITCH_TS]

lines = []
def h(t): lines.append(f"\n{'='*60}\n{t}\n{'='*60}")
def p(t=""): lines.append(str(t))

def model_report(label, rows):
    h(label)
    if not rows:
        p("No messages found.")
        return
    with_think    = [r for r in rows if r["thinking"] and r["thinking"].strip()]
    without_think = [r for r in rows if not r["thinking"] or not r["thinking"].strip()]
    think_lengths = [len(r["thinking"]) for r in with_think]

    p(f"Total assistant responses:    {len(rows)}")
    p(f"Responses WITH thinking:      {len(with_think)}  ({len(with_think)/len(rows)*100:.0f}%)")
    p(f"Responses WITHOUT thinking:   {len(without_think)}  ({len(without_think)/len(rows)*100:.0f}%)")
    if think_lengths:
        p(f"\nThinking content length (chars):")
        p(f"  Min:    {min(think_lengths)}")
        p(f"  Median: {statistics.median(think_lengths):.0f}")
        p(f"  Max:    {max(think_lengths)}")
        p(f"  Mean:   {statistics.mean(think_lengths):.0f}")

    # Show thinking examples (first 2 with thinking)
    p(f"\n--- Sample thinking snippets (first 2) ---")
    shown = 0
    for r in with_think:
        if shown >= 2: break
        ts = datetime.datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M")
        p(f"\n[{ts}] id={r['id']} (thinking {len(r['thinking'])} chars):")
        p("  " + r["thinking"][:300].replace("\n", "\n  ") + ("..." if len(r["thinking"]) > 300 else ""))
        shown += 1

    # What kinds of prompts triggered thinking?
    p(f"\n--- Tool calls that had thinking ---")
    tool_with_think = [r for r in with_think if r["tools_used"]]
    p(f"  {len(tool_with_think)} of {len(with_think)} thinking responses also called a tool")

model_report("MAGISTRAL 24B (pre-switch)", magistral)
model_report("GEMMA 4 26B (post-switch)", gemma4)

h("SUMMARY COMPARISON")
def rate(rows): return len([r for r in rows if r["thinking"] and r["thinking"].strip()]) / max(len(rows), 1) * 100
p(f"Magistral thinking rate:  {rate(magistral):.0f}%  ({len(magistral)} total responses)")
p(f"Gemma 4 thinking rate:    {rate(gemma4):.0f}%  ({len(gemma4)} total responses)")
p("""
HOW THINKING IS CONTROLLED:
  1. `think: True` in Ollama payload  --> enables the CAPABILITY (native token extraction)
  2. System prompt instruction        --> tells the model WHEN to use it
  3. Model's own judgment             --> the model decides autonomously per-message

The system prompt currently says:
  "Keep thinking concise -- you don't need lengthy chains for casual conversation."

This means the model is DESIGNED to skip thinking on simple exchanges.
Neither model exposes a "thinking effort" slider through Ollama --
it's entirely the model's autonomous decision within those guardrails.
""")

output = "\n".join(lines)
with open(OUT_FILE, "w", encoding="utf-8") as f:
    f.write(output)
print(f"Done. Output: {OUT_FILE}")
