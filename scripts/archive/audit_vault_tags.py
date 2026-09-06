#!/usr/bin/env python3
# audit_vault_tags.py
# date created: 2026-08-28
# date modified: 2026-09-05 19:17:01
# tags: #[tag, #librarian, #taxonomy, #audit, #cli, #maintenance, #deprecated, #evelyn]

"""audit_vault_tags.py — DEPRECATED: Unified into scripts/master_librarian.py.

This wrapper exists for backwards compatibility and transparently replaces its
process image via os.execv to invoke scripts/master_librarian.py.
"""

import os
import sys
from pathlib import Path


def main():
    target_script = Path(__file__).resolve().parent.parent / "master_librarian.py"
    if not target_script.exists():
        target_script = Path(__file__).resolve().parent / "master_librarian.py"
    if not target_script.exists():
        sys.stderr.write(f"[ERROR] Target script '{target_script}' not found.\n")
        sys.exit(1)

    # Translate arguments for master_librarian.py
    forwarded_args: list[str] = []
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--sync-taxonomy":
            forwarded_args.append("--rebalance-taxonomy")
        elif arg == "--continuous":
            forwarded_args.append("--all")
        else:
            forwarded_args.append(arg)
        i += 1

    sys.stderr.write(
        "\n[DEPRECATED] scripts/audit_vault_tags.py has been unified into Master Librarian.\n"
        f"Forwarding execution to: python {target_script.name} {' '.join(forwarded_args)}\n\n"
    )
    sys.stderr.flush()

    # Replaces the current process entirely, preserving exit codes, signals, and pipes.
    os.execv(sys.executable, [sys.executable, str(target_script), *forwarded_args])


if __name__ == "__main__":
    main()
