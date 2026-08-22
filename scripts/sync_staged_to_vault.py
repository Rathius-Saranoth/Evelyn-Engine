#!/usr/bin/env python3
# sync_staged_to_vault.py
# date created: 2026-08-16 20:32:00
# date modified: 2026-08-16 20:32:00
# tags: 

# scripts/sync_staged_to_vault.py
"""
sync_staged_to_vault.py — Sync verified staged knowledge and attachments into Obsidian Vault.
"""

import os
import sys
import shutil

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(ROOT_DIR, "Evelyn", "tools")
for d in (ROOT_DIR, TOOLS_DIR):
    if d not in sys.path:
        sys.path.insert(0, d)

import evelyn_config as cfg

VAULT_DIR = getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")
STAGING_DIR = getattr(cfg, "STAGING_DIR", os.path.join(ROOT_DIR, "data", "staging"))
ATTACHMENTS_DIR = os.path.join(STAGING_DIR, "Attachments")
VAULT_ATTACHMENTS_DIR = os.path.join(VAULT_DIR, "Attachments")

# Folder routing map (staging folder -> vault destination relative path)
FOLDER_MAP = {
    "Reference Library": "Reference Library",
    "Prompt Lab": "Notes/Prompt Lab",
    "Pets": "Notes/Pets",
    "Recipes": "Notes/Recipes",
    "Financial": f"{getattr(cfg, 'USER_NAME', 'Ricky')}/Financial",
    "Professional": f"{getattr(cfg, 'USER_NAME', 'Ricky')}/Professional",
    "Medical": f"{getattr(cfg, 'USER_NAME', 'Ricky')}/Medical",
    "Genealogy": "Genealogy",
    "Talonesti": "Projects/Talonesti",
    "Schyler": "Schyler",
    getattr(cfg, "ASSISTANT_NAME", "Evelyn"): getattr(cfg, "ASSISTANT_NAME", "Evelyn"),
    "Art Institute": "Projects/Art Institute"
}

def sync_attachments():
    """Sync all staging attachments to the Obsidian Vault Attachments directory."""
    if not os.path.exists(ATTACHMENTS_DIR):
        return 0
    os.makedirs(VAULT_ATTACHMENTS_DIR, exist_ok=True)
    count = 0
    for f in os.listdir(ATTACHMENTS_DIR):
        src = os.path.join(ATTACHMENTS_DIR, f)
        if os.path.isfile(src):
            dst = os.path.join(VAULT_ATTACHMENTS_DIR, f)
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
                count += 1
    return count

def sync_knowledge_notes():
    """Sync markdown and knowledge files to their designated vault folders."""
    synced_notes = 0
    for stage_folder, vault_rel in FOLDER_MAP.items():
        src_dir = os.path.join(STAGING_DIR, stage_folder)
        if not os.path.exists(src_dir):
            continue
            
        dst_dir = os.path.join(VAULT_DIR, vault_rel)
        os.makedirs(dst_dir, exist_ok=True)
        
        for root, _, files in os.walk(src_dir):
            for f in files:
                src_file = os.path.join(root, f)
                rel_to_stage = os.path.relpath(src_file, src_dir)
                dst_file = os.path.join(dst_dir, rel_to_stage)
                
                os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                if not os.path.exists(dst_file):
                    shutil.copy2(src_file, dst_file)
                    synced_notes += 1
                    
    return synced_notes

def main():
    print("=" * 70)
    print("SYNCING STAGED KNOWLEDGE INTO OBSIDIAN VAULT")
    print("=" * 70)
    
    attach_count = sync_attachments()
    print(f"Synced {attach_count} new image/document attachments to: {VAULT_ATTACHMENTS_DIR}")
    
    notes_count = sync_knowledge_notes()
    print(f"Synced {notes_count} new knowledge notes and documents to Vault.")
    
    print("\nVault Knowledge Sync Complete!")

if __name__ == "__main__":
    main()
