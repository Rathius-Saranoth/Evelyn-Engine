"""
scratch/check_pending.py — List pending journal and context files awaiting approval

Output is written to scratch/out/check_pending.txt for clean reading.
"""

import os
import datetime

PENDING_JOURNAL  = r"G:\My Drive\Obsidian_Vault\Evelyn\Pending_Approvals\Journal"
PENDING_CONTEXT  = r"G:\My Drive\Obsidian_Vault\Evelyn\Pending_Approvals\Context"
OUT_PATH = r"C:\Projects\LocalAI\scratch\out\check_pending.txt"
os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

lines = []
lines.append("=== Pending Approval Files ===")
lines.append(f"Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
lines.append("")

for label, path in [("Journal", PENDING_JOURNAL), ("Context", PENDING_CONTEXT)]:
    lines.append(f"--- {label}: {path} ---")
    if not os.path.exists(path):
        lines.append("  (directory does not exist)")
    else:
        files = sorted(os.listdir(path))
        if not files:
            lines.append("  (no pending files)")
        for fname in files:
            fpath = os.path.join(path, fname)
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(fpath))
            size = os.path.getsize(fpath)
            lines.append(f"  {fname}  [{mtime.strftime('%Y-%m-%d %H:%M')}]  {size} bytes")
    lines.append("")

with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(lines))

print(f"Written to: {OUT_PATH}")
