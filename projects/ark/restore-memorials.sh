#!/usr/bin/env bash
# restore-memorials.sh — Ark restore for the Memorials standalone app.
# Source this from ark-restore.sh or run standalone.
#
# Usage:
#   restore_memorials /path/to/backup/memorials/2026-04-04
#
# Expects the backup directory to contain:
#   memorials.db            — SQLite database
#   memorials-uploads.tar.gz — customer order files + images
#   memorials-output.tar.gz  — generated SVGs
#   memorials.env           — environment file
#   docker-compose.yml      — compose config
#   git-memorials.txt       — git commit hash (optional)

set -euo pipefail

MEMORIALS_DIR="${MEMORIALS_DIR:-/opt/nbne/memorials}"
MEMORIALS_REPO="${MEMORIALS_REPO:-git@github-production:NBNEORIGIN/memorials.git}"
LOG_FILE="${LOG_FILE:-/var/log/ark-restore.log}"

log() {
    local msg="[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [memorials-restore] $*"
    echo "$msg" | tee -a "$LOG_FILE"
}

restore_memorials() {
    local backup_dir="$1"

    if [[ ! -d "$backup_dir" ]]; then
        log "ERROR: Backup directory not found: $backup_dir"
        return 1
    fi

    log "Restoring memorials from $backup_dir"

    # ── 1. Ensure deployment directory exists ─────────────────────
    mkdir -p "$MEMORIALS_DIR"

    # ── 2. Clone repo at recorded commit (if git hash exists) ─────
    if [[ -f "$backup_dir/git-memorials.txt" ]]; then
        local git_hash
        git_hash="$(cat "$backup_dir/git-memorials.txt")"
        if [[ ! -d "$MEMORIALS_DIR/.git" ]]; then
            git clone "$MEMORIALS_REPO" "$MEMORIALS_DIR"
        fi
        git -C "$MEMORIALS_DIR" fetch origin
        git -C "$MEMORIALS_DIR" checkout "$git_hash" 2>/dev/null || \
            log "  WARN: Could not checkout $git_hash, using HEAD"
    fi

    # ── 3. Restore docker-compose.yml and .env ────────────────────
    if [[ -f "$backup_dir/docker-compose.yml" ]]; then
        cp "$backup_dir/docker-compose.yml" "$MEMORIALS_DIR/docker-compose.yml"
    fi

    if [[ -f "$backup_dir/memorials.env" ]]; then
        cp "$backup_dir/memorials.env" "$MEMORIALS_DIR/.env"
        chmod 600 "$MEMORIALS_DIR/.env"
        log "  .env restored"
    else
        log "  WARN: No .env in backup"
    fi

    # ── 4. Start containers (creates volumes if they don't exist) ─
    cd "$MEMORIALS_DIR"
    docker compose up -d --build
    log "  Containers started"

    # Wait for backend to be ready
    local attempts=0
    while [[ $attempts -lt 30 ]]; do
        if docker exec memorials-backend-1 curl -sf http://localhost:8000/api/health &>/dev/null; then
            break
        fi
        attempts=$((attempts + 1))
        sleep 2
    done

    if [[ $attempts -ge 30 ]]; then
        log "  WARN: Backend not healthy after 60s, proceeding anyway"
    fi

    # ── 5. Restore SQLite database ────────────────────────────────
    if [[ -f "$backup_dir/memorials.db" ]]; then
        # Stop backend briefly to avoid concurrent writes
        docker compose stop backend
        docker cp "$backup_dir/memorials.db" memorials-backend-1:/app/data/memorials.db 2>/dev/null || \
            docker run --rm \
                -v memorials-data:/data \
                -v "$backup_dir":/backup:ro \
                alpine:latest \
                cp /backup/memorials.db /data/memorials.db

        # Restore WAL files if present
        for wal_file in memorials.db-wal memorials.db-shm; do
            if [[ -f "$backup_dir/$wal_file" ]]; then
                docker run --rm \
                    -v memorials-data:/data \
                    -v "$backup_dir":/backup:ro \
                    alpine:latest \
                    cp "/backup/$wal_file" "/data/$wal_file"
            fi
        done

        docker compose start backend
        log "  Database restored"
    fi

    # ── 6. Restore uploads volume ─────────────────────────────────
    if [[ -f "$backup_dir/memorials-uploads.tar.gz" ]]; then
        docker run --rm \
            -v memorials-uploads:/data \
            -v "$backup_dir":/backup:ro \
            alpine:latest \
            sh -c "rm -rf /data/* && tar xzf /backup/memorials-uploads.tar.gz -C /data"
        log "  Uploads volume restored"
    fi

    # ── 7. Restore output volume ──────────────────────────────────
    if [[ -f "$backup_dir/memorials-output.tar.gz" ]]; then
        docker run --rm \
            -v memorials-output:/data \
            -v "$backup_dir":/backup:ro \
            alpine:latest \
            sh -c "rm -rf /data/* && tar xzf /backup/memorials-output.tar.gz -C /data"
        log "  Output volume restored"
    fi

    # ── 8. Final restart to pick up restored state ────────────────
    cd "$MEMORIALS_DIR" && docker compose restart
    log "Memorials restore complete"
}

# Run if executed directly (not sourced)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    if [[ $# -lt 1 ]]; then
        echo "Usage: $0 /path/to/backup/memorials/YYYY-MM-DD"
        exit 1
    fi
    restore_memorials "$1"
fi
