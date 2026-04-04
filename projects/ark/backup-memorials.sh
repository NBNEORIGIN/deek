#!/usr/bin/env bash
# backup-memorials.sh — Ark backup for the Memorials standalone app.
# Source this from the main backup.sh or run standalone.
#
# Memorials is NOT a Phloe tenant. It uses:
#   - SQLite (not PostgreSQL) in Docker volume memorials-data
#   - Three Docker volumes: memorials-data, memorials-uploads, memorials-output
#   - Fixed container names: memorials-backend-1, memorials-frontend-1
#   - Deployed at /opt/nbne/memorials/

set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-/backups/ark}"
MEMORIALS_DIR="${MEMORIALS_DIR:-/opt/nbne/memorials}"
TODAY="$(date -u +%Y-%m-%d)"
LOG_FILE="${LOG_FILE:-/var/log/ark-backup.log}"

log() {
    local msg="[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [memorials] $*"
    echo "$msg" | tee -a "$LOG_FILE"
}

backup_memorials() {
    local backup_dir="$BACKUP_ROOT/memorials/$TODAY"
    mkdir -p "$backup_dir"

    log "Starting memorials backup to $backup_dir"

    # ── 1. SQLite database ────────────────────────────────────────
    # Copy via docker cp (atomic from container's perspective).
    # Also grab WAL files if SQLite is in WAL mode.
    if docker ps --format '{{.Names}}' | grep -q '^memorials-backend-1$'; then
        docker cp memorials-backend-1:/app/data/memorials.db "$backup_dir/memorials.db" 2>/dev/null \
            && log "  DB copied" \
            || log "  WARN: DB copy failed (may not exist yet)"

        # WAL + SHM (safe to skip if not in WAL mode)
        docker cp memorials-backend-1:/app/data/memorials.db-wal "$backup_dir/memorials.db-wal" 2>/dev/null || true
        docker cp memorials-backend-1:/app/data/memorials.db-shm "$backup_dir/memorials.db-shm" 2>/dev/null || true
    else
        log "  WARN: memorials-backend-1 not running, skipping DB"
    fi

    # ── 2. Uploads volume (customer order files + images) ──────���──
    if docker volume inspect memorials-uploads &>/dev/null; then
        docker run --rm \
            -v memorials-uploads:/data:ro \
            -v "$backup_dir":/out \
            alpine:latest \
            tar czf /out/memorials-uploads.tar.gz -C /data .
        log "  Uploads volume archived"
    else
        log "  WARN: memorials-uploads volume not found"
    fi

    # ── 3. Output volume (generated SVGs + CSVs) ─────────────────
    if docker volume inspect memorials-output &>/dev/null; then
        docker run --rm \
            -v memorials-output:/data:ro \
            -v "$backup_dir":/out \
            alpine:latest \
            tar czf /out/memorials-output.tar.gz -C /data .
        log "  Output volume archived"
    else
        log "  WARN: memorials-output volume not found"
    fi

    # ── 4. Environment file ───────────────────────────────────────
    if [[ -f "$MEMORIALS_DIR/.env" ]]; then
        cp "$MEMORIALS_DIR/.env" "$backup_dir/memorials.env"
        chmod 600 "$backup_dir/memorials.env"
        log "  .env copied"
    else
        log "  WARN: no .env found at $MEMORIALS_DIR/.env"
    fi

    # ── 5. Docker Compose file (for restore reference) ────────────
    if [[ -f "$MEMORIALS_DIR/docker-compose.yml" ]]; then
        cp "$MEMORIALS_DIR/docker-compose.yml" "$backup_dir/docker-compose.yml"
        log "  docker-compose.yml copied"
    fi

    # ── 6. Git commit hash ────────────────────────────────────────
    if [[ -d "$MEMORIALS_DIR/.git" ]]; then
        git -C "$MEMORIALS_DIR" rev-parse HEAD > "$backup_dir/git-memorials.txt" 2>/dev/null || true
        log "  Git hash recorded"
    fi

    log "Memorials backup complete: $backup_dir"
}

# Run if executed directly (not sourced)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    backup_memorials
fi
