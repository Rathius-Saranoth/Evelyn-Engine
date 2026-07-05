---
description: A protective workflow for backing up the project to GitHub while ensuring private data remains local
title: backup-to-github.md
date created: 2026-03-14 22:50:31
date modified: 2026-03-14 22:50:40
tags: git, github, backup, workflow, command
---

# Safe Backup Workflow

This workflow ensures your code "Engine" is backed up to GitHub while your private "Soul" data stays safely on your local machine.

## 1. Safety Check (Dry Run)

// turbo
Verify what files are tracked by Git. Our `.gitignore` should prevent private data from being staged.

```powershell
git status
```

> [!WARNING]
> If you see `Ricky_Narrative_Profile.md` or any `.env` files in "Untracked files", DO NOT PROCEED. Update `.gitignore` first.

## 2. Stage and Commit Changes

### 2a. Review What Changed

Stage all files first, then inspect the diff so you can write a meaningful message.

```powershell
git add .
git diff --cached --stat
```

Also check the most recent commit for context on what was previously saved:

```powershell
git log -1 --pretty=format:"%h %s (%ar)"
```

> [!IMPORTANT]
> **Do NOT skip this step.** Read the diff output carefully. You are responsible for synthesizing a commit message that accurately describes what changed — not just that a backup occurred.

### 2b. Commit with a Meaningful Message

Using what you learned from the diff, write a commit subject line in this format:

```
[Area]: Brief description of what changed
```

Examples:
- `Evelyn: Add web search tool to research engine`
- `Config: Update NUM_CTX and temperature for research preset`
- `Tools: Fix finalization queue logic in dev.html`
- `Docs: Update ROADMAP with completed Phase 2 milestones`

If multiple unrelated areas changed, use a multi-line message:

```powershell
git commit -m "Primary change summary

- Area 1: what changed
- Area 2: what changed"
```

Only fall back to a generic timestamped message if **nothing meaningful changed** (e.g., only whitespace or auto-generated files):

```powershell
git commit -m "Chore: Minor housekeeping $(Get-Date -Format 'yyyy-MM-dd')"
```

## 3. Push to GitHub

If you have a remote configured:

```powershell
git push origin main
```

## 4. Recovering from a "Broken" State

If you ever feel the local repository is broken or "stuck":

1. **Save your work** elsewhere if possible.
2. Run `git status` to see where you are.
3. If things are really bad, we can use `git reset --hard` to return to the last known good backup.
