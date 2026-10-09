#!/usr/bin/env bash
# backup_non_repo_data.sh — Backup non-repo engine data, SQLite DBs, .env, and Obsidian Vault to secondary drive
# date created: 2026-10-09
# tags: #backup, #secondary-drive, #sqlite, #vault, #evelyn

set -euo pipefail

DEST_ROOT="/data/backups"
TIMESTAMP="$(date +'%Y%m%d_%H%M%S')"
SNAPSHOT_DIR="${DEST_ROOT}/snapshot_${TIMESTAMP}"
LATEST_LINK="${DEST_ROOT}/latest"

EVELYN_DIR="/home/rathius/evelyn"
DATA_DIR="${EVELYN_DIR}/data"
ENV_FILE="${EVELYN_DIR}/.env"
VAULT_DIR="/home/rathius/obsidian_vault"

echo "========================================================="
echo "   Evelyn Non-Repo Data Backup -> Secondary Drive (/data)"
echo "========================================================="
echo "Timestamp: ${TIMESTAMP}"

# 1. Verify destination mount & write permissions
if [ ! -d "/data" ]; then
    echo "❌ Error: Mount point /data does not exist." >&2
    exit 1
fi

if [ ! -w "/data" ]; then
    echo "❌ Permission Denied: /data is not writable by $(whoami)." >&2
    echo "   Please grant permissions to user $(whoami) by running:" >&2
    echo "   sudo chown -R $(whoami):$(whoami) /data" >&2
    exit 1
fi

mkdir -p "${SNAPSHOT_DIR}"

# 2. Backup .env file
if [ -f "${ENV_FILE}" ]; then
    echo "📦 Backing up .env..."
    cp -p "${ENV_FILE}" "${SNAPSHOT_DIR}/.env"
else
    echo "⚠️  Notice: No .env file found at ${ENV_FILE}"
fi

# 3. Backup /data directory (SQLite DBs, Health, Planning, Attachments, Chroma, etc.)
echo "📦 Backing up ${DATA_DIR}..."
mkdir -p "${SNAPSHOT_DIR}/data"
rsync -a \
    --exclude='*.tmp' \
    --exclude='lost+found' \
    "${DATA_DIR}/" "${SNAPSHOT_DIR}/data/"

# 4. Backup Evelyn Persona directory (Soul & Directives, gitignored)
PERSONA_DIR="${EVELYN_DIR}/Evelyn/persona"
if [ -d "${PERSONA_DIR}" ]; then
    echo "📦 Backing up Evelyn Persona (${PERSONA_DIR})..."
    mkdir -p "${SNAPSHOT_DIR}/persona"
    rsync -a "${PERSONA_DIR}/" "${SNAPSHOT_DIR}/persona/"
else
    echo "⚠️  Notice: Evelyn Persona directory not found at ${PERSONA_DIR}"
fi

# 5. Backup Obsidian Vault
if [ -d "${VAULT_DIR}" ]; then
    echo "📦 Backing up Obsidian Vault (${VAULT_DIR})..."
    mkdir -p "${SNAPSHOT_DIR}/obsidian_vault"
    rsync -a \
        --exclude='.obsidian/workspace*' \
        --exclude='.trash/' \
        "${VAULT_DIR}/" "${SNAPSHOT_DIR}/obsidian_vault/"
else
    echo "⚠️  Notice: Obsidian Vault not found at ${VAULT_DIR}"
fi

# 6. Update latest symlink
rm -f "${LATEST_LINK}"
ln -s "${SNAPSHOT_DIR}" "${LATEST_LINK}"

# 7. Verify and summarize
BACKUP_SIZE="$(du -sh "${SNAPSHOT_DIR}" | cut -f1)"
TOTAL_AVAIL="$(df -h /data | awk 'NR==2 {print $4}')"

echo "========================================================="
echo "✔ Backup completed successfully!"
echo "  Location: ${SNAPSHOT_DIR}"
echo "  Symlink:  ${LATEST_LINK}"
echo "  Size:     ${BACKUP_SIZE}"
echo "  Free on /data: ${TOTAL_AVAIL} remaining"
echo "========================================================="
