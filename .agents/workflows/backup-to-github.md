---
description: A protective workflow for backing up the project to GitHub while ensuring private data remains local
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

// turbo
Stage all non-ignored files and create a timestamped commit.

```powershell
git add .
git commit -m "Backup: $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
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
