# terminal_agent.py
# date created: 2026-06-27 09:37:19
# date modified: 2026-06-27 09:39:05
# tags: 

# Evelyn/tools/terminal_agent.py
# date created: 2026-06-27 15:30:00
# date modified: 2026-06-27 15:30:00
# tags: #terminal, #tools, #agent, #safety

"""Terminal and file access agent tools for Evelyn.

Gives Evelyn the capability to execute safe bash/shell commands, read files, and write
files in allowed workspace paths with a multi-layered safety check and user approval gate.
"""

import os
import sys
import json
import re
import subprocess
import time
import uuid
import importlib
import evelyn_config as cfg

# Multi-layered safety pattern rules
TERMINAL_BLOCKED_PATTERNS = [
    r"(?i)format\s+[a-z]:",              # format C:
    r"(?i)del\s+/[sq]",                  # del /s, del /q (recursive delete)
    r"(?i)rmdir\s+/[sq]",               # rmdir /s /q
    r"(?i)rm\s+-r[f]?\s",               # rm -rf
    r"(?i)shutdown|restart.*computer",    # System shutdown
    r"(?i)reg\s+(add|delete)",           # Registry modification
    r"(?i)net\s+(user|localgroup)",      # User account manipulation
    r"(?i)base64\s.*\|\s*(ba)?sh",       # Encoded shell injection
    r"(?i)invoke-webrequest|curl.*-o",   # Downloading executables
    r"(?i)pip\s+install(?!\s+--user)",   # Global pip install (allow --user)
    r"(?i)npm\s+install\s+-g",          # Global npm install
    r"(?i)Set-ExecutionPolicy",          # PowerShell policy change
]

TERMINAL_APPROVAL_PATTERNS = [
    r"(?i)pip\s+install",               # Any pip install
    r"(?i)pip\s+uninstall",             # Package removal
    r"(?i)git\s+(push|reset|rebase)",   # Destructive git operations
    r"(?i)Remove-Item",                 # PowerShell file deletion
    r"(?i)del\s+",                      # Any del command
    r"(?i)move\s+",                     # File moves
    r"(?i)ren\s+",                      # File renames
    r"(?i)python\s+.*server",           # Starting server processes
    r"(?i)ollama\s+(pull|rm|create)",   # Model management
    r"(?i)>\s*\S+",                     # Output redirection (overwrite)
]

TERMINAL_SAFE_PATTERNS = [
    r"(?i)^python\s+-c\s+",            # Python one-liners
    r"(?i)^python\s+.*\.py$",           # Running Python scripts (no args)
    r"(?i)^git\s+(status|log|diff|branch|show)", # Read-only git
    r"(?i)^ollama\s+(list|ps|show)",    # Read-only Ollama
    r"(?i)^(ls|dir|type|cat|head|tail|find|where|echo)", # Read-only FS
    r"(?i)^Get-(Content|ChildItem|Item|Process|Service)", # PS read-only
    r"(?i)^Select-String",              # grep equivalent
    r"(?i)^pip\s+(list|show|freeze)",   # pip info
]

# Pending approvals storage (maintained as an empty dict for backward compatibility,
# though get_pending_approvals() and disk-based lookups are now preferred)
_pending_approvals: dict[str, dict] = {}

import evelyn_config as cfg

APPROVALS_FILE = getattr(cfg, "TERMINAL_APPROVALS_PATH", r"/home/rathius/evelyn/data/terminal_approvals.json")


def _load_approvals() -> dict:
    """Load approvals from persistent JSON storage.

    Returns:
        dict: A dictionary of approval records.
    """
    if not os.path.exists(APPROVALS_FILE):
        return {}
    try:
        with open(APPROVALS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Terminal Agent] Error loading approvals: {e}")
        return {}


def _save_approvals(data: dict):
    """Save approvals to persistent JSON storage.

    Args:
        data: The dictionary of approval records to serialize.
    """
    try:
        os.makedirs(os.path.dirname(APPROVALS_FILE), exist_ok=True)
        with open(APPROVALS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[Terminal Agent] Error saving approvals: {e}")


def cleanup_stale_approvals():
    """Update pending approvals older than 10 minutes (600s) to expired status,

    and purge records older than 7 days.
    """
    approvals = _load_approvals()
    now = time.time()
    changed = False

    # 1. Update pending approvals older than 10 minutes to 'expired'
    for k, v in approvals.items():
        if v.get("status") == "pending" and now - v.get("created_at", 0) > 600:
            v["status"] = "expired"
            changed = True

    # 2. Purge records older than 7 days
    to_delete = [
        k for k, v in approvals.items()
        if now - v.get("created_at", 0) > 7 * 86400
    ]
    for k in to_delete:
        approvals.pop(k, None)
        changed = True

    if changed:
        _save_approvals(approvals)


def get_pending_approvals() -> list[dict]:
    """Fetch all active pending approvals.

    Returns:
        list[dict]: A list of pending approval dicts with 'id' injected.
    """
    cleanup_stale_approvals()
    approvals = _load_approvals()
    return [
        {"id": k, **{kk: vv for kk, vv in v.items() if kk != "content" and kk != "result"}}
        for k, v in approvals.items()
        if v.get("status") == "pending"
    ]


def get_approval_status(approval_id: str) -> dict:
    """Retrieve the persistent status and metadata for a specific approval ID.

    Args:
        approval_id: The unique approval identifier.

    Returns:
        dict: A status dictionary containing 'id', 'status', and metadata.
    """
    cleanup_stale_approvals()
    approvals = _load_approvals()
    item = approvals.get(approval_id)
    if not item:
        return {"id": approval_id, "status": "unknown"}
    # Return all fields except the raw 'content' block to save bandwidth
    return {
        "id": approval_id,
        **{k: v for k, v in item.items() if k != "content"}
    }


def is_path_allowed(path: str) -> bool:
    """Check if the resolved absolute path falls under any allowed prefix.

    Args:
        path: Path string to check.

    Returns:
        bool: True if path is allowed, False otherwise.
    """
    try:
        resolved = os.path.normcase(os.path.abspath(os.path.realpath(path)))
        importlib.reload(cfg)
        allowed_paths = getattr(cfg, "TERMINAL_ALLOWED_PATHS", [r"/home/rathius/evelyn"])
        for allowed in allowed_paths:
            allowed_abs = os.path.normcase(os.path.abspath(os.path.realpath(allowed)))
            if resolved == allowed_abs:
                return True
            if resolved.startswith(allowed_abs + os.sep):
                return True
        return False
    except Exception:
        return False


def run_command(command: str, cwd: str = r"/home/rathius/evelyn", timeout: int = 30) -> str:
    """Execute a shell command in the Evelyn workspace with safety checks.

    Args:
        command: The bash command string to execute.
        cwd: Working directory. Must be in TERMINAL_ALLOWED_PATHS.
        timeout: Maximum execution time in seconds.

    Returns:
        str: Command output, or warning if approval is required, or error message.
    """
    cleanup_stale_approvals()
    importlib.reload(cfg)
    
    # 1. Path scoping check
    cwd_abs = os.path.abspath(cwd)
    if not is_path_allowed(cwd_abs):
        return f"Error: Working directory '{cwd}' is outside allowed paths."

    # 2. Blocked pattern check
    for pattern in TERMINAL_BLOCKED_PATTERNS:
        if re.search(pattern, command):
            return f"Error: Command blocked by safety filter. Pattern: {pattern}"

    # 3. Check if approval required
    needs_approval = False
    for pattern in TERMINAL_APPROVAL_PATTERNS:
        if re.search(pattern, command):
            needs_approval = True
            break

    # 4. Check if auto-approved overrides approval pattern
    if needs_approval:
        for pattern in TERMINAL_SAFE_PATTERNS:
            if re.search(pattern, command):
                needs_approval = False
                break

    # 5. Stage command if approval is needed
    if needs_approval:
        approval_id = f"cmd_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        limit_timeout = min(timeout, getattr(cfg, "TERMINAL_MAX_TIMEOUT", 300))
        approvals = _load_approvals()
        approvals[approval_id] = {
            "type": "command",
            "command": command,
            "cwd": cwd_abs,
            "timeout": limit_timeout,
            "created_at": time.time(),
            "status": "pending",
            "result": None,
        }
        _save_approvals(approvals)
        return (
            f"⚠️ This command requires approval before execution:\n"
            f"```\n{command}\n```\n"
            f"Approval ID: {approval_id}\n"
            f"Waiting for Ricky to approve or deny this command."
        )

    # 6. Execute safe/auto-approved command
    limit_timeout = min(timeout, getattr(cfg, "TERMINAL_MAX_TIMEOUT", 300))
    return _execute_command(command, cwd_abs, limit_timeout)


def _execute_command(command: str, cwd: str, timeout: int) -> str:
    """Execute a command via bash and capture stdout/stderr.

    Args:
        command: The bash command string to execute.
        cwd: Directory context for execution.
        timeout: Execution timeout in seconds.

    Returns:
        str: Captured execution output or error description.
    """
    importlib.reload(cfg)
    try:
        max_chars = getattr(cfg, "TERMINAL_MAX_OUTPUT_CHARS", 10000)
        run_kwargs = {
            "cwd": cwd,
            "capture_output": True,
            "text": True,
            "timeout": timeout,
        }
        if sys.platform == "win32":
            run_kwargs["creationflags"] = 0x08000000
        result = subprocess.run(
            ["/bin/bash", "-c", command],
            **run_kwargs,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += f"\n[stderr]:\n{result.stderr}"

        if len(output) > max_chars:
            output = output[:max_chars] + f"\n\n[Output truncated at {max_chars} chars]"

        if not output.strip():
            output = f"[Command completed with exit code {result.returncode}]"

        return output
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout} seconds."
    except Exception as e:
        return f"Error executing command: {e}"


def read_file(file_path: str, max_lines: int = 200) -> str:
    """Read contents of a file within allowed workspace paths.

    Args:
        file_path: Absolute or relative file path.
        max_lines: Maximum lines to return.

    Returns:
        str: Numbered file content or error description.
    """
    cleanup_stale_approvals()
    
    # Resolve path
    if not os.path.isabs(file_path):
        file_path = os.path.join(r"/home/rathius/evelyn", file_path)
    abs_path = os.path.abspath(file_path)

    # Path safety check
    if not is_path_allowed(abs_path):
        return f"Error: Path '{file_path}' is outside allowed paths."

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        total = len(lines)
        if total > max_lines:
            lines = lines[:max_lines]
            truncated = f"\n[Showing first {max_lines} of {total} lines]"
        else:
            truncated = ""

        numbered = "".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))
        return f"--- {abs_path} ({total} lines) ---\n{numbered}{truncated}"
    except FileNotFoundError:
        return f"Error: File not found: {file_path}"
    except UnicodeDecodeError:
        return f"Error: File is not valid UTF-8 text: {file_path}"
    except Exception as e:
        return f"Error reading file: {e}"


def write_file(file_path: str, content: str, mode: str = "overwrite") -> str:
    """Write or append text content to a file within allowed paths.

    Always stages for user approval — there is no auto-approve path for file writes.

    Args:
        file_path: Absolute or relative file path.
        content: The text content to write.
        mode: Write mode ('overwrite' or 'append').

    Returns:
        str: Confirmation warning with approval ID.
    """
    cleanup_stale_approvals()
    
    # Resolve path
    if not os.path.isabs(file_path):
        file_path = os.path.join(r"/home/rathius/evelyn", file_path)
    abs_path = os.path.abspath(file_path)

    # Path safety check
    if not is_path_allowed(abs_path):
        return f"Error: Path '{file_path}' is outside allowed paths."

    approval_id = f"write_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    approvals = _load_approvals()
    approvals[approval_id] = {
        "type": "write",
        "file_path": abs_path,
        "content": content,
        "mode": mode,
        "created_at": time.time(),
        "status": "pending",
        "result": None,
    }
    _save_approvals(approvals)

    preview = content[:500] + ("..." if len(content) > 500 else "")
    return (
        f"⚠️ File write requires approval:\n"
        f"**Path:** `{abs_path}`\n"
        f"**Mode:** {mode}\n"
        f"**Preview:**\n```\n{preview}\n```\n"
        f"Approval ID: {approval_id}\n"
        f"Waiting for Ricky to approve or deny this file write."
    )


def approve_command(approval_id: str) -> str:
    """Execute or save a pending approved command or file write.

    Args:
        approval_id: Staged approval ID.

    Returns:
        str: Execution results or error message.
    """
    cleanup_stale_approvals()
    approvals = _load_approvals()
    pending = approvals.get(approval_id)
    if not pending:
        return "Error: Approval ID not found or expired."
    if pending.get("status") != "pending":
        return f"Error: Command is already {pending.get('status')}."

    if pending.get("type") == "write":
        try:
            mode_flag = "a" if pending["mode"] == "append" else "w"
            # Ensure directories exist
            os.makedirs(os.path.dirname(pending["file_path"]), exist_ok=True)
            with open(pending["file_path"], mode_flag, encoding="utf-8") as f:
                f.write(pending["content"])
            result = f"[Success] File written to {pending['file_path']}"
            pending["status"] = "approved"
            pending["result"] = result
            _save_approvals(approvals)
            return result
        except Exception as e:
            result = f"Error writing file: {e}"
            pending["status"] = "failed"
            pending["result"] = result
            _save_approvals(approvals)
            return result
    else:
        result = _execute_command(pending["command"], pending["cwd"], pending["timeout"])
        pending["status"] = "approved" if not result.startswith("Error") else "failed"
        pending["result"] = result
        _save_approvals(approvals)
        return result


def deny_command(approval_id: str) -> str:
    """Deny and mark a pending command as denied.

    Args:
        approval_id: Staged approval ID.

    Returns:
        str: Rejection status message.
    """
    cleanup_stale_approvals()
    approvals = _load_approvals()
    pending = approvals.get(approval_id)
    if pending:
        if pending.get("status") != "pending":
            return f"Error: Command is already {pending.get('status')}."
        pending["status"] = "denied"
        _save_approvals(approvals)
        return "Command denied."
    return "Error: Approval ID not found."
