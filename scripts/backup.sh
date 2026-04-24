#!/bin/bash
# Alf-E nightly backup — SQLite memory + playbooks → local archive → B2 (optional)
#
# Usage (manual):   ./scripts/backup.sh
# Usage (cron):     0 2 * * *  /home/alf-e/alf-e/scripts/backup.sh >> /var/log/alfe-backup.log 2>&1
#
# Environment:
#   B2_BUCKET         — target Backblaze B2 bucket (optional; if unset, local only)
#   B2_PREFIX         — path prefix inside bucket (default: alfe-backups)
#   RCLONE_REMOTE     — rclone remote name (default: b2)
#   ALFE_BACKUP_DIR   — local staging dir (default: /var/backups/alfe)
#   ALFE_BACKUP_KEEP  — how many local snapshots to keep (default: 7)

set -euo pipefail

BACKUP_DIR="${ALFE_BACKUP_DIR:-/var/backups/alfe}"
KEEP="${ALFE_BACKUP_KEEP:-7}"
B2_BUCKET="${B2_BUCKET:-}"
B2_PREFIX="${B2_PREFIX:-alfe-backups}"
RCLONE_REMOTE="${RCLONE_REMOTE:-b2}"

STAMP=$(date +%Y-%m-%d_%H%M)
STAGE="$BACKUP_DIR/$STAMP"
mkdir -p "$STAGE"

echo "[$(date -Iseconds)] Starting backup → $STAGE"

# ── SQLite memory (via docker volume) ────────────────────────────────────
# The alf-e container keeps /data mounted as a named volume. Use sqlite3
# inside the container so the backup is consistent (WAL-safe).
if docker ps --format '{{.Names}}' | grep -q '^alf-e$'; then
  docker exec alf-e sh -c 'sqlite3 /data/alfe_memory.db ".backup /data/backup_latest.db"' 2>/dev/null || {
    echo "  ⚠ sqlite3 .backup failed; falling back to file copy"
    docker exec alf-e sh -c 'cp /data/alfe_memory.db /data/backup_latest.db'
  }
  docker cp alf-e:/data/backup_latest.db "$STAGE/alfe_memory.db"
  docker exec alf-e rm -f /data/backup_latest.db
  echo "  ✓ memory.db snapshot taken"
else
  echo "  ⚠ alf-e container not running — skipping memory backup"
fi

# ── Playbooks + env ──────────────────────────────────────────────────────
if [ -d "$HOME/alf-e/playbooks" ]; then
  cp -r "$HOME/alf-e/playbooks" "$STAGE/playbooks"
  echo "  ✓ playbooks copied"
fi

if [ -f "$HOME/alf-e/.env" ]; then
  cp "$HOME/alf-e/.env" "$STAGE/.env"
  chmod 600 "$STAGE/.env"
  echo "  ✓ .env copied (600 perms)"
fi

# ── Tar it up ────────────────────────────────────────────────────────────
ARCHIVE="$BACKUP_DIR/alfe-$STAMP.tar.gz"
tar -czf "$ARCHIVE" -C "$BACKUP_DIR" "$STAMP"
rm -rf "$STAGE"
echo "  ✓ archive: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"

# ── Push to B2 if configured ─────────────────────────────────────────────
if [ -n "$B2_BUCKET" ] && command -v rclone >/dev/null 2>&1; then
  if rclone copy "$ARCHIVE" "$RCLONE_REMOTE:$B2_BUCKET/$B2_PREFIX/" 2>&1; then
    echo "  ✓ uploaded to $RCLONE_REMOTE:$B2_BUCKET/$B2_PREFIX/"
  else
    echo "  ✗ rclone upload failed — archive still held locally"
  fi
else
  [ -z "$B2_BUCKET" ] && echo "  ℹ B2_BUCKET unset — local backup only"
fi

# ── Prune local ──────────────────────────────────────────────────────────
cd "$BACKUP_DIR"
ls -1t alfe-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -v
echo "[$(date -Iseconds)] Backup complete."
