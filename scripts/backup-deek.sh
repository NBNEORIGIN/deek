#!/usr/bin/env bash
# Daily backup of the Deek brain — DB dump + manual files. Pre-existing
# Phloe backup scripts (/opt/ark/backup.sh, /opt/nbne/scripts/backup.sh)
# only cover Phloe tenants and skip the cairn/deek deployment, so this
# script fills that gap. Created 2026-04-30 alongside the manuals
# upload UI rollout.
#
# What it backs up:
#   1. pg_dump of the deek db (claw_code_chunks: wiki + manual chunks +
#      embeddings + memory + CRM embeddings + everything else the agent
#      reads from)
#   2. /opt/nbne/manuals — the original PDFs/photos under their machine
#      subfolders, so a DB-loss event can be recovered by restoring the
#      dump and a manuals-loss event by re-running ingest_manuals.py
#
# Retention: 14 daily snapshots. Adjust BACKUP_RETENTION_DAYS if needed.

set -euo pipefail

BACKUP_ROOT="/backups/deek/daily"
TODAY="$(date -u +%Y-%m-%d)"
TARGET="$BACKUP_ROOT/$TODAY"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

mkdir -p "$TARGET"

echo "[$(date -u +%FT%TZ)] deek backup starting → $TARGET"

# 1. DB dump
if docker ps --format "{{.Names}}" | grep -q "^deploy-deek-db-1$"; then
  docker exec deploy-deek-db-1 pg_dump -U cairn -d cairn -Fc -Z 9 \
    > "$TARGET/deek.pg.dump" 2>"$TARGET/deek.pg.err" \
    && echo "  ok  pg_dump deek → $(du -sh "$TARGET/deek.pg.dump" | cut -f1)" \
    || echo "  ERR pg_dump deek (see deek.pg.err)"
else
  echo "  WARN deploy-deek-db-1 not running, skipping pg_dump"
fi

# 2. Manuals files
if [ -d /opt/nbne/manuals ]; then
  tar -czf "$TARGET/manuals.tar.gz" -C /opt/nbne manuals \
    && echo "  ok  manuals.tar.gz → $(du -sh "$TARGET/manuals.tar.gz" | cut -f1)" \
    || echo "  ERR manuals.tar.gz failed"
else
  echo "  skip /opt/nbne/manuals does not exist"
fi

# 3. Prune old snapshots
find "$BACKUP_ROOT" -maxdepth 1 -mindepth 1 -type d -mtime "+$BACKUP_RETENTION_DAYS" \
  -exec rm -rf {} \; 2>/dev/null || true

echo "[$(date -u +%FT%TZ)] deek backup done"
