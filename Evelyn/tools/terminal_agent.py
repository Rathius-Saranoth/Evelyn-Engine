# terminal_agent.py
# date created: 2026-06-27 09:37:19
# date modified: 2026-08-17 19:08:10
# tags:

# Evelyn/tools/terminal_agent.py
# date created: 2026-06-27 15:30:00
# date modified: 2026-06-27 15:30:00
# tags: #terminal, #tools, #agent, #safety

"""Terminal and file access agent tools for Evelyn.

Gives Evelyn the capability to execute safe bash/shell commands, read files, and write
files in allowed workspace paths with a multi-layered safety check and user approval gate.
"""

import importlib
import json
import os
import re
import subprocess
import sys
import time
import uuid

import evelyn_config as cfg

# Multi-layered safety pattern rules
TERMINAL_BLOCKED_PATTERNS = [
    # Privilege escalation
    r"(?i)\b(sudo|su|doas|pkexec)\b",

    # Destructive disk / filesystem formatting & raw device writes
    r"(?i)\b(mkfs|fdisk|parted|sfdisk|gdisk)\b",
    r"(?i)\bdd\s+if=",
    r"(?i)>\s*/dev/sd[a-z]",
    r"(?i)format\s+[a-z]:",              # Windows format C:

    # Dangerous recursive deletes
    r"(?i)\brm\s+-(?:[a-zA-Z]*r[a-zA-Z]*f|[a-zA-Z]*f[a-zA-Z]*r)\b", # rm -rf / rm -fr
    r"(?i)\brm\s+--recursive\s+--force\b",
    r"(?i)\brm\s+.*-(?:rf|fr)\s+(?:/|/root|/etc|/boot|/sys|/proc|/dev|~|\*|\.)\b",
    r"(?i)del\s+/[sq]",                  # Windows del /s /q
    r"(?i)rmdir\s+/[sq]",               # Windows rmdir /s /q

    # System shutdown & reboot
    r"(?i)\b(shutdown|reboot|poweroff|halt|init\s+[06])\b",
    r"(?i)\bsystemctl\s+(poweroff|reboot|halt)\b",
    r"(?i)restart.*computer",

    # User / account / credential manipulation
    r"(?i)\b(useradd|userdel|usermod|groupadd|groupdel|passwd|chpasswd)\b",
    r"(?i)net\s+(user|localgroup)",      # Windows user mgmt
    r"(?i)reg\s+(add|delete)",           # Windows registry

    # Dangerous permission stripping / recursive ownership takeovers
    r"(?i)\bchmod\s+(-R\s+)?(?:777|000|\+x\s+/)\b",
    r"(?i)\bchown\s+-R\b",

    # Remote execution piping & shell injections
    r"(?i)\b(?:curl|wget|fetch|base64)\b[^|]*\|\s*(?:ba)?sh",
    r"(?i)invoke-webrequest",
    r"(?i):\(\)\s*\{\s*:\|:&\s*\};:",   # Fork bomb

    # Global package installations (allow user-scoped or local)
    r"(?i)\b(apt|apt-get|dpkg|dnf|yum|pacman|zypper)\b",
    r"(?i)pip\s+install(?!\s+--user)",   # Global pip install (allow --user)
    r"(?i)npm\s+install\s+-g",          # Global npm install
    r"(?i)npm\s+-g\s+install",
    r"(?i)Set-ExecutionPolicy",          # PowerShell policy change
]

TERMINAL_APPROVAL_PATTERNS = [
    # File and directory mutations / deletions
    r"(?i)\b(rm|rmdir|unlink)\s+",       # Linux deletions
    r"(?i)\b(mv|cp)\s+",                 # Linux file moves / copies
    r"(?i)\b(touch|mkdir)\s+",           # Creating files / dirs
    r"(?i)Remove-Item",                  # PowerShell file deletion
    r"(?i)Move-Item|Rename-Item",        # PowerShell file moves
    r"(?i)del\s+",                       # Windows del command
    r"(?i)move\s+",                      # Windows move command
    r"(?i)ren\s+",                       # Windows ren command

    # In-place file modification / truncation / redirection
    r"(?i)>>?\s*\S+",                    # Output redirection (> or >>)
    r"(?i)\bsed\s+-i\b",                # In-place sed
    r"(?i)\btruncate\s+",                # File truncation

    # Process / Service management
    r"(?i)\b(kill|pkill|killall)\s+",    # Terminating processes
    r"(?i)\bsystemctl\s+(start|stop|restart|reload|disable|enable)\b",
    r"(?i)\bservice\s+\S+\s+(start|stop|restart)\b",

    # Packages & models
    r"(?i)pip\s+(install|uninstall)",    # Pip installs / uninstalls
    r"(?i)npm\s+(install|uninstall|update)", # NPM installs / uninstalls
    r"(?i)ollama\s+(pull|rm|create|cp)", # Model management

    # Destructive / state-changing git operations
    r"(?i)git\s+(push|reset|rebase|clean|restore|branch\s+-[dD]|checkout\s+\.)",

    # Server / process execution
    r"(?i)python\s+.*server",            # Starting server processes
    r"(?i)\b(uvicorn|gunicorn|fastapi\s+run)\b",
]

TERMINAL_SAFE_PATTERNS = [
    # Python & Testing execution
    r"(?i)^python\s+-c\s+",              # Python one-liners
    r"(?i)^python\s+.*\.py$",             # Running Python scripts (no args)
    r"(?i)^python\s+-m\s+(pytest|unittest)", # Running unit tests via python -m
    r"(?i)^pytest\b",                    # Pytest test execution

    # Read-only Git
    r"(?i)^git\s+(status|log|diff|branch|show|remote|tag|describe|rev-parse)",

    # Read-only Ollama
    r"(?i)^ollama\s+(list|ps|show)",     # Read-only Ollama

    # Read-only FS & System inspection
    r"(?i)^(ls|dir|type|cat|head|tail|less|more|find|where|which|whereis|echo|printf)",
    r"(?i)^(grep|egrep|fgrep|rg|awk|wc|diff|stat|file|tree|jq|readlink|realpath)",
    r"(?i)^(ps|top|htop|uptime|free|uname|whoami|id|date|env|printenv|df|du|pwd)",

    # Package info queries
    r"(?i)^pip\s+(list|show|freeze|check)", # pip info
    r"(?i)^npm\s+(list|view|outdated|audit)", # npm info

    # PowerShell read-only equivalents
    r"(?i)^Get-(Content|ChildItem|Item|Process|Service)", # PS read-only
    r"(?i)^Select-String",                # grep equivalent
]

# Excluded system, synchronization, and metadata folders
TERMINAL_BLOCKED_SUBPATHS = [
    ".obsidian",
    ".stfolder",
    ".stversions",
    ".trash",
    ".git",
    "syncthing",
]

# Prohibited OS system root directories
SYSTEM_BLOCKED_ROOTS = [
    "/etc",
    "/boot",
    "/sys",
    "/proc",
    "/dev",
    "/root",
    "/usr",
    "/var/run",
]

# Pending approvals storage (maintained as an empty dict for backward compatibility,
# though get_pending_approvals() and disk-based lookups are now preferred)
_pending_approvals: dict[str, dict] = {}

APPROVALS_FILE = getattr(cfg, "TERMINAL_APPROVALS_PATH", r"/home/rathius/evelyn/data/terminal_approvals.json")


def _load_approvals() -> dict:
    """Load approvals from persistent JSON storage.

    Returns:
        dict: A dictionary of approval records.
    """
    if not os.path.exists(APPROVALS_FILE):
        return {}
    try:
        with open(APPROVALS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, ValueError) as e:
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
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"[Terminal Agent] Error saving approvals: {e}")


def cleanup_stale_approvals():
    """Update pending approvals older than 10 minutes (600s) to expired status,

    and purge records older than 7 days.
    """
    approvals = _load_approvals()
    now = time.time()
    changed = False

    # 1. Update pending approvals older than 10 minutes to 'expired'
    for v in approvals.values():
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


def get_approval_details(approval_id: str) -> dict | None:
    """Retrieve full persistent details including raw content for a specific approval ID.

    Args:
        approval_id: The unique approval identifier.

    Returns:
        dict | None: The complete approval dict with 'id' injected, or None if not found.
    """
    cleanup_stale_approvals()
    approvals = _load_approvals()
    item = approvals.get(approval_id)
    if not item:
        return None
    return {"id": approval_id, **item}


def resolve_file_path(file_path: str) -> str:
    """Resolve a relative or absolute file path to its canonical target.

    If file_path is relative, it checks whether the path points to a known
    Obsidian Vault directory or already exists in the vault; otherwise defaults to
    the Evelyn workspace directory.

    Args:
        file_path: Absolute or relative file path string.

    Returns:
        str: Absolute resolved path string.
    """
    if not file_path:
        return ""
    if os.path.isabs(file_path):
        return os.path.abspath(file_path)

    importlib.reload(cfg)
    vault_base = getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")
    evelyn_base = getattr(cfg, "BASE_DIR", r"/home/rathius/evelyn")

    # Normalize slashes for inspection
    norm = file_path.replace("\\", "/").strip("/")
    parts = norm.split("/")
    top_dir = parts[0] if parts else ""

    # Check if target explicitly exists under vault
    vault_candidate = os.path.abspath(os.path.join(vault_base, norm))
    evelyn_candidate = os.path.abspath(os.path.join(evelyn_base, norm))

    if os.path.exists(vault_candidate) and not os.path.exists(evelyn_candidate):
        return vault_candidate
    if os.path.exists(evelyn_candidate):
        return evelyn_candidate

    # Check top-level folder names in Obsidian Vault
    known_vault_dirs = {
        "notes", "projects", cfg.USER_NAME.lower(), cfg.ASSISTANT_NAME.lower(), "genealogy", "contacts",
        "templates", "attachments", "bases", "dream journal", "dungeons & dragons",
        "learning lab", "reference library", "music", "pets",
        "programs", "prompt lab", "recipes", "tech quick reference", "video games",
        "vault", "obsidian",
    }
    if top_dir.lower() in known_vault_dirs or (os.path.exists(vault_base) and os.path.isdir(os.path.join(vault_base, top_dir))):
        return vault_candidate

    return evelyn_candidate


def is_path_allowed(path: str) -> bool:
    """Check if the resolved absolute path falls under any allowed prefix

    and does not enter any excluded system or metadata folders.

    Args:
        path: Path string to check.

    Returns:
        bool: True if path is allowed, False otherwise.
    """
    try:
        resolved = os.path.normcase(os.path.abspath(os.path.realpath(path)))
        importlib.reload(cfg)

        # 1. Check against OS system root blacklists
        for sys_root in SYSTEM_BLOCKED_ROOTS:
            sys_root_norm = os.path.normcase(os.path.abspath(os.path.realpath(sys_root)))
            if resolved == sys_root_norm or resolved.startswith(sys_root_norm + os.sep):
                return False

        # 2. Check allowed roots
        allowed_paths = getattr(cfg, "TERMINAL_ALLOWED_PATHS", [
            r"/home/rathius/evelyn",
            r"/home/rathius/obsidian_vault",
            r"/tmp",
        ])

        is_under_allowed = False
        for allowed in allowed_paths:
            allowed_abs = os.path.normcase(os.path.abspath(os.path.realpath(allowed)))
            if resolved == allowed_abs or resolved.startswith(allowed_abs + os.sep):
                is_under_allowed = True
                break

        if not is_under_allowed:
            return False

        # 3. Check blocked subpaths / system folders (.obsidian, .stfolder, .trash, .git, etc.)
        norm_path = resolved.replace("\\", "/")
        path_segments = [s.lower() for s in norm_path.split("/") if s]
        for blocked in TERMINAL_BLOCKED_SUBPATHS:
            blocked_lower = blocked.lower()
            if blocked_lower in path_segments:
                return False

        return True
    except (ValueError, TypeError, OSError):
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
    cwd_abs = resolve_file_path(cwd)
    if not is_path_allowed(cwd_abs):
        return f"Error: Working directory '{cwd}' is outside allowed paths or in a protected system directory."

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
            f"Waiting for {cfg.USER_NAME} to approve or deny this command."
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
    except (subprocess.SubprocessError, OSError) as e:
        return f"Error executing command: {e}"


def read_file(file_path: str, max_lines: int = 200) -> str:
    """Read contents of a file within allowed workspace or vault paths.

    Args:
        file_path: Absolute or relative file path.
        max_lines: Maximum lines to return.

    Returns:
        str: Numbered file content or error description.
    """
    cleanup_stale_approvals()

    # Resolve path
    abs_path = resolve_file_path(file_path)

    # Path safety check
    if not is_path_allowed(abs_path):
        return f"Error: Path '{file_path}' is outside allowed paths or in a protected system directory."

    try:
        with open(abs_path, encoding="utf-8") as f:
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
    except OSError as e:
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
    abs_path = resolve_file_path(file_path)

    # Path safety check
    if not is_path_allowed(abs_path):
        return f"Error: Path '{file_path}' is outside allowed paths or in a protected system directory."

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
        f"Waiting for {cfg.USER_NAME} to approve or deny this file write."
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
        except (OSError, ValueError, KeyError) as e:
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
