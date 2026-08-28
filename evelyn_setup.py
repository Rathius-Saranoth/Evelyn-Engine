#!/usr/bin/env python3
# evelyn_setup.py
"""
evelyn_setup.py — Interactive Setup & Configuration Wizard for the Evelyn Engine.

Guides new deployments through:
1. Persona & Operator Identity setup (Assistant Name, User Name, Subject Codes).
2. Obsidian Vault path and directory scaffolding.
3. Hardware tier & model parameter configuration.
4. Starter template deployment.

Usage:
  python evelyn_setup.py                # Interactive setup
  python evelyn_setup.py --defaults     # Non-interactive default configuration
"""

import argparse
import os
import re
import sys

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

CONFIG_PATH = os.path.join(ROOT_DIR, "evelyn_config.py")
TEMPLATES_DIR = os.path.join(ROOT_DIR, "templates")


def sanitize_name(val: str, default: str = "Evelyn") -> str:
    """Sanitize name input: alphanumeric and underscores only, max 30 chars."""
    cleaned = re.sub(r"[^\w\s-]", "", val).strip()
    cleaned = re.sub(r"[\s-]+", "_", cleaned)
    if not cleaned:
        return default
    return cleaned[:30]


def prompt_input(prompt_text: str, default_val: str = "") -> str:
    """Prompt user with default fallback."""
    display = f"{prompt_text} [{default_val}]: " if default_val else f"{prompt_text}: "
    try:
        res = input(display).strip()
        return res if res else default_val
    except (KeyboardInterrupt, EOFError):
        print("\nSetup cancelled.")
        sys.exit(0)


def prompt_choice(prompt_text: str, choices: list[str], default_idx: int = 0) -> str:
    """Prompt user to select from a list of options."""
    print(f"\n{prompt_text}")
    for i, choice in enumerate(choices, 1):
        marker = "*" if i - 1 == default_idx else " "
        print(f"  [{i}]{marker} {choice}")
    while True:
        try:
            val = input(f"Select (1-{len(choices)}) [default: {default_idx + 1}]: ").strip()
            if not val:
                return choices[default_idx]
            idx = int(val) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
            print(f"Please enter a number between 1 and {len(choices)}.")
        except ValueError:
            print("Please enter a valid number.")
        except (KeyboardInterrupt, EOFError):
            print("\nSetup cancelled.")
            sys.exit(0)


def scaffold_vault_structure(vault_dir: str, assistant_name: str, user_name: str) -> None:
    """Create essential subdirectories in the user's vault."""
    dirs_to_create = [
        os.path.join(vault_dir, assistant_name),
        os.path.join(vault_dir, assistant_name, f"{assistant_name}'s Journal"),
        os.path.join(vault_dir, assistant_name, f"{assistant_name}'s Context", "Context Entries"),
        os.path.join(vault_dir, assistant_name, "Deep_Research"),
        os.path.join(vault_dir, assistant_name, "Pending_Approvals"),
        os.path.join(vault_dir, user_name),
        os.path.join(vault_dir, "Notes", "Prompt Lab"),
        os.path.join(vault_dir, "Attachments"),
    ]
    for d in dirs_to_create:
        os.makedirs(d, exist_ok=True)
    print(f"[VAULT] Scaffolding created in: {vault_dir}")


def copy_starter_templates(vault_dir: str, assistant_name: str, user_name: str) -> None:
    """Deploy starter markdown templates to the configured vault directories."""
    persona_dst = os.path.join(vault_dir, assistant_name, f"{assistant_name} Narrative Persona.md")
    user_dst = os.path.join(vault_dir, user_name, f"{user_name} Narrative Profile.md")
    directives_dst = os.path.join(vault_dir, assistant_name, "System Directives.md")

    template_map = {
        "Assistant_Persona.example.md": persona_dst,
        "User_Profile.example.md": user_dst,
        "System_Directives.example.md": directives_dst,
    }

    for src_name, dst_path in template_map.items():
        src_file = os.path.join(TEMPLATES_DIR, src_name)
        if os.path.exists(src_file) and not os.path.exists(dst_path):
            with open(src_file, encoding="utf-8") as f:
                content = f.read()
            # Replace placeholder names
            content = content.replace("Assistant", assistant_name).replace("Operator", user_name)
            with open(dst_path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"  [TEMPLATE] Created starter note: {os.path.basename(dst_path)}")


def update_config_file(assistant_name: str, user_name: str, vault_dir: str) -> None:
    """Update identity variables in evelyn_config.py."""
    if not os.path.exists(CONFIG_PATH):
        print(f"[ERROR] Config file not found: {CONFIG_PATH}")
        return

    with open(CONFIG_PATH, encoding="utf-8") as f:
        content = f.read()

    # Update ASSISTANT_NAME and USER_NAME
    content = re.sub(r'ASSISTANT_NAME\s*=\s*".*?"', f'ASSISTANT_NAME = "{assistant_name}"', content)
    content = re.sub(r'USER_NAME\s*=\s*".*?"', f'USER_NAME = "{user_name}"', content)

    # Update VAULT_BASE_DIR if provided
    escaped_vault = vault_dir.replace("\\", "/")
    content = re.sub(r'VAULT_BASE_DIR\s*=\s*r?".*?"', f'VAULT_BASE_DIR = r"{escaped_vault}"', content)

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"[CONFIG] Updated configuration in: {CONFIG_PATH}")


def main():
    parser = argparse.ArgumentParser(description="Evelyn Engine Setup & Configuration Wizard")
    parser.add_argument("--defaults", action="store_true", help="Apply sensible defaults non-interactively")
    parser.add_argument("--assistant-name", default=None, help="Set assistant persona name")
    parser.add_argument("--user-name", default=None, help="Set user/operator name")
    parser.add_argument("--vault-path", default=None, help="Set Obsidian Vault base directory")
    args = parser.parse_args()

    print("=" * 70)
    print("      EVELYN ENGINE — SETUP & IDENTITY CONFIGURATION WIZARD       ")
    print("   For full OS prerequisites & Ollama setup, see SETUP_GUIDE.md   ")
    print("=" * 70)

    default_vault = os.path.expanduser("~/obsidian_vault")
    if args.defaults:
        assistant = sanitize_name(args.assistant_name or "Evelyn", "Evelyn")
        user = sanitize_name(args.user_name or "Ricky", "Ricky")
        vault = args.vault_path or default_vault
    else:
        print("\n1. Persona & Operator Identity")
        print("   Configure the AI companion persona and your operator name.")
        assistant_raw = prompt_input("   Enter Assistant Name", "Evelyn")
        assistant = sanitize_name(assistant_raw, "Evelyn")

        user_raw = prompt_input("   Enter Operator / User Name", "Ricky")
        user = sanitize_name(user_raw, "Ricky")

        print(f"\n   -> Assistant Identity: {assistant}")
        print(f"   -> Operator Identity:  {user}")

        print("\n2. Obsidian Vault Storage")
        print("   Specify the absolute path to your local Obsidian Vault.")
        vault_raw = prompt_input("   Enter Vault Path", default_vault)
        vault = os.path.abspath(os.path.expanduser(vault_raw))

        print(f"\n   -> Vault Root: {vault}")

        deploy_templates = prompt_choice(
            "3. Starter Templates & Scaffolding\n   Deploy starter persona files into your vault?",
            ["Yes — scaffold folders and copy starter notes", "No — keep existing vault files as-is"],
            default_idx=0
        )
        should_scaffold = "Yes" in deploy_templates

    print("\n" + "=" * 70)
    print("Applying configuration...")
    update_config_file(assistant, user, vault)

    if not args.defaults and should_scaffold:
        scaffold_vault_structure(vault, assistant, user)
        copy_starter_templates(vault, assistant, user)

    print("\n" + "=" * 70)
    print("SETUP COMPLETE!")
    print(f"  Assistant:    {assistant}")
    print(f"  User:         {user}")
    print(f"  Vault:        {vault}")
    print("=" * 70)
    print("\nNext steps:")
    print("  1. Start services: ./scripts/start_evelyn_services.sh")
    print("  2. Open dashboard: http://localhost:8000/ui/dev.html")
    print("=" * 70)


if __name__ == "__main__":
    main()
