---
title: ssh_device_setup.md
---

# SSH Remote Access -- Adding a New Android Device

**Purpose:** Reference for connecting a new Android device (phone or tablet) to the
Evelyn Engine tool launcher over Tailscale SSH.

> [!IMPORTANT]
> The **Windows PC setup is one-time only** (OpenSSH Server, PowerShell default shell).
> Once done, adding a new device is just the "New Device Steps" section below.
> Check whether the PC steps are already complete before running them again.

---

## PC Setup (One-Time)

These steps only need to be done once, ever. Skip if already done.

### 1. Install and Start OpenSSH Server

Run in an **Administrator PowerShell**:

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
```

The Windows Firewall rule for port 22 is added automatically.

### 2. Set PowerShell as the Default SSH Shell

Run in an **Administrator PowerShell**:

```powershell
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" `
    -Name DefaultShell `
    -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -PropertyType String -Force
```

This sets PS5 as the default. PS7 (`pwsh.exe`) cannot be set here reliably because
its path (`C:\Program Files\PowerShell\7\pwsh.exe`) contains a space that confuses
the OpenSSH shell resolver. Instead, `pwsh` is invoked directly in the `RemoteCommand`
(see Step 3), so the script itself runs under PS7 regardless.

Without this step, SSH sessions default to cmd.exe and can't run `.ps1` scripts.

### 3. Create the Authorized Keys File

The authorized keys file for admin accounts lives here (NOT in `%USERPROFILE%\.ssh\`):

```
C:\ProgramData\ssh\administrators_authorized_keys
```

> [!IMPORTANT]
> If your Windows account is in the Administrators group (true for most personal PCs),
> Windows OpenSSH ignores `%USERPROFILE%\.ssh\authorized_keys` entirely and only
> reads from `C:\ProgramData\ssh\administrators_authorized_keys`.

Create it empty and set permissions (required even before adding the first key):

```powershell
$dest = "C:\ProgramData\ssh\administrators_authorized_keys"
New-Item -Force -ItemType File $dest | Out-Null
icacls $dest /inheritance:r
icacls $dest /grant "NT AUTHORITY\SYSTEM:(F)"
icacls $dest /grant "BUILTIN\Administrators:(F)"
```

---

## New Device Steps (Repeat for Each Device)

### Step 1 -- On the Android Device (Termux)

Install Termux from F-Droid (not the Play Store version -- it's outdated).

```bash
# Install openssh
pkg update && pkg install openssh

# Generate a key
# IMPORTANT: Do NOT use the -C flag -- Termux's ssh-keygen rejects it
ssh-keygen -t ed25519 -f ~/.ssh/id_evelyn
# Press Enter twice at the passphrase prompt (no passphrase = easier mobile use)

# Display the public key to copy
cat ~/.ssh/id_evelyn.pub
```

The output looks like:
```
ssh-ed25519 AAAA...long base64 string... username@localhost
```

Copy the **entire line** including the `ssh-ed25519` prefix and the trailing `username@localhost`.

### Step 2 -- On the PC (PowerShell)

**APPEND** the new key -- do not overwrite or you'll break existing devices.

```powershell
$dest = "C:\ProgramData\ssh\administrators_authorized_keys"

# Paste the full pub key line from the device
$newKey = "ssh-ed25519 AAAA...paste full key here..."

# AppendAllText preserves Unix line endings -- critical, Set-Content will break auth
[System.IO.File]::AppendAllText($dest, "`n" + $newKey.Trim() + "`n")

# Restart to pick up the new key
Restart-Service sshd
```

> [!IMPORTANT]
> Always use `[System.IO.File]::WriteAllText()` or `::AppendAllText()` when writing
> to `authorized_keys`. PowerShell's `Set-Content` adds Windows CRLF line endings,
> which causes OpenSSH to silently reject the key and fall back to password auth.

**Verify the file looks right** (one key per line, each starting with `ssh-ed25519`):

```powershell
Get-Content "C:\ProgramData\ssh\administrators_authorized_keys"
```

### Step 3 -- Back on the Device (Termux)

```bash
# Create the SSH config
mkdir -p ~/.ssh
cat >> ~/.ssh/config << 'EOF'
Host evelyn
    HostName image-host.internal.net
    User ricky
    IdentityFile ~/.ssh/id_evelyn
    RemoteCommand pwsh -ExecutionPolicy Bypass -NoLogo -File C:\Projects\LocalAI\evelyn_tools.ps1
    RequestTTY yes
    ServerAliveInterval 30
    ServerAliveCountMax 3
EOF
chmod 600 ~/.ssh/config
```

> [!NOTE]
> `RemoteCommand` uses `pwsh` (PS7) directly rather than `powershell` (PS5). This ensures
> the launcher script runs under PS7, which handles UTF-8 files correctly. `pwsh` is in
> the system PATH so the space-in-path issue with `C:\Program Files\...` does not apply.

```bash
# Add the shortcut alias
echo "alias evelyn='ssh evelyn'" >> ~/.bashrc
source ~/.bashrc
```

### Step 4 -- First Connection

```bash
evelyn
```

When prompted:
```
The authenticity of host '...' can't be established. Are you sure you want to continue? (yes/no)
```
Type `yes` and press Enter. This is a one-time prompt -- SSH stores the host fingerprint
in `~/.ssh/known_hosts` and will not ask again.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `nc -zv ... 22` gives "unknown option" | Termux netcat doesn't support `-v` | Use `ssh ricky@image-host...` directly instead |
| `ssh-keygen -C` gives "too many arguments" | Termux ssh-keygen rejects `-C` flag | Drop the `-C` flag entirely |
| Still prompted for password after adding key | Wrong file, wrong perms, or CRLF line endings | See "Password auth fallback" below |
| Script runs but nothing appears | SSH session using cmd.exe, not PowerShell | Set DefaultShell registry key (PC Setup Step 2) |
| Box-drawing chars show as garbled text | `.ps1` file has Unicode chars, PS5 reads wrong encoding | `evelyn_tools.ps1` must use ASCII only -- no `=`, `-` alternatives |
| Session drops on screen lock | No SSH keepalive configured | `ServerAliveInterval 30` in `~/.ssh/config` (already in config above) |

### Password Auth Fallback Checklist

If the key is correct but SSH keeps asking for a password, check in order:

1. **Is the user in the Administrators group?**
   ```powershell
   net localgroup Administrators | findstr ricky
   ```
   If yes, the key MUST be in `C:\ProgramData\ssh\administrators_authorized_keys`.

2. **Are permissions correct?**
   ```powershell
   icacls "C:\ProgramData\ssh\administrators_authorized_keys"
   ```
   Should show only `NT AUTHORITY\SYSTEM:(F)` and `BUILTIN\Administrators:(F)`. No inherited entries.

3. **Are line endings correct?**
   Use `[System.IO.File]::AppendAllText()` -- never `Set-Content` or `Add-Content`.

4. **Is the full key there?** Including the `ssh-ed25519` prefix?
   ```powershell
   Get-Content "C:\ProgramData\ssh\administrators_authorized_keys"
   ```

5. **Did sshd restart after the change?**
   ```powershell
   Restart-Service sshd
   ```

---

## Adding a New Tool to the Launcher

Open `C:\Projects\LocalAI\evelyn_tools.ps1` and append to the `$TOOLS` array:

```powershell
@{
    Label  = "My New Tool"
    Script = "Evelyn\tools\my_new_tool.py"
    Desc   = "One-line description shown in the menu"
}
```

No other changes needed. The menu auto-numbers entries.

> [!IMPORTANT]
> `evelyn_tools.ps1` must remain **ASCII-only**. Unicode box-drawing characters, em/en
> dashes, checkmarks, and similar symbols fail through the SSH/Termux terminal pipeline
> regardless of PowerShell version or encoding settings. Use `=`, `-`, `[OK]`, `[!!]`,
> `[X]` etc. as substitutes.
