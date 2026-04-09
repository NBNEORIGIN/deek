#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Phloe Platform Backup Script — /opt/ark/backup.sh
# Backs up all tenants (pg_dump + media + .env) and platform nginx/git state.
# Logs to /var/log/ark-backup.log, emails toby@nbnesigns.com on any failure.
# ---------------------------------------------------------------------------

LOG_FILE="/var/log/ark-backup.log"
BACKUP_ROOT="/backups/ark/daily"
INSTANCES_DIR="/opt/nbne/instances"
TODAY="$(date -u +%Y-%m-%d)"
TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
EMAIL_TO="toby@nbnesigns.com"
OVERALL_EXIT=0

# ---------------------------------------------------------------------------
# Logging helper — writes timestamped lines and appends to log file
# ---------------------------------------------------------------------------
log() {
    local msg="[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
    echo "$msg" | tee -a "$LOG_FILE"
}

# ---------------------------------------------------------------------------
# Ensure backup root exists
# ---------------------------------------------------------------------------
mkdir -p "$BACKUP_ROOT"

# ---------------------------------------------------------------------------
# Redirect all further output to log (tee keeps stdout too)
# ---------------------------------------------------------------------------
exec > >(tee -a "$LOG_FILE") 2>&1

log "========================================================"
log "ARK backup starting — $TODAY"
log "========================================================"

# ---------------------------------------------------------------------------
# Discover tenants dynamically — skip anything with a dot in the name
# ---------------------------------------------------------------------------
TENANTS=()
if [[ -d "$INSTANCES_DIR" ]]; then
    while IFS= read -r -d '' entry; do
        slug="$(basename "$entry")"
        # Skip entries containing a dot (stray files like products_export_1.csv)
        if [[ "$slug" != *.* ]]; then
            TENANTS+=("$slug")
        fi
    done < <(find "$INSTANCES_DIR" -mindepth 1 -maxdepth 1 -printf '%p\0')
fi

if [[ ${#TENANTS[@]} -eq 0 ]]; then
    log "ERROR: No tenants discovered under $INSTANCES_DIR — aborting."
    exit 1
fi

log "Discovered ${#TENANTS[@]} tenant(s): ${TENANTS[*]}"

# ---------------------------------------------------------------------------
# Track failures for manifest and email
# ---------------------------------------------------------------------------
declare -A FAILURE_REASONS
FAILURES=()

# ---------------------------------------------------------------------------
# Per-tenant backup
# ---------------------------------------------------------------------------
backup_tenant() {
    local slug="$1"
    local tenant_dir="$BACKUP_ROOT/$slug/$TODAY"
    local env_file="$INSTANCES_DIR/$slug/.env"
    local db_container="${slug}-db-1"
    local media_volume="${slug}_media"
    local failed=0
    local fail_reasons=()

    log "--- Tenant: $slug ---"
    mkdir -p "$tenant_dir"

    # --- a. pg_dump --------------------------------------------------------
    log "[$slug] Running pg_dump..."
    if docker inspect --format '{{.State.Running}}' "$db_container" 2>/dev/null | grep -q '^true$'; then
        if docker exec "$db_container" \
                pg_dump --no-password -U nbne -d "$slug" \
            | gzip > "$tenant_dir/$slug.sql.gz" 2>>"$LOG_FILE"; then
            local sql_bytes
            sql_bytes="$(stat -c '%s' "$tenant_dir/$slug.sql.gz" 2>/dev/null || echo 0)"
            log "[$slug] pg_dump OK — ${sql_bytes} bytes"
        else
            local err="pg_dump failed for $slug (docker exec returned non-zero)"
            log "[$slug] ERROR: $err"
            fail_reasons+=("$err")
            failed=1
        fi
    else
        local err="DB container $db_container is not running"
        log "[$slug] ERROR: $err"
        fail_reasons+=("$err")
        failed=1
        # Write a 0-byte marker so stat won't fail on manifest generation
        : > "$tenant_dir/$slug.sql.gz"
    fi

    # --- b. Media volume ---------------------------------------------------
    log "[$slug] Backing up media volume $media_volume..."
    # Empty volume is acceptable — '|| true' suppresses tar errors on empty dirs
    docker run --rm \
        -v "${media_volume}:/data:ro" \
        -v "${tenant_dir}:/out" \
        alpine:latest \
        tar czf /out/media.tar.gz -C /data . 2>/dev/null || true
    local media_bytes
    media_bytes="$(stat -c '%s' "$tenant_dir/media.tar.gz" 2>/dev/null || echo 0)"
    log "[$slug] Media backup done — ${media_bytes} bytes"

    # --- c. .env copy ------------------------------------------------------
    local env_bak_present="false"
    if [[ -f "$env_file" ]]; then
        cp "$env_file" "$tenant_dir/.env.bak"
        chmod 600 "$tenant_dir/.env.bak"
        env_bak_present="true"
        log "[$slug] .env copied OK"
    else
        log "[$slug] WARNING: No .env found at $env_file"
    fi

    if [[ $failed -eq 1 ]]; then
        FAILURES+=("$slug")
        local joined_reasons
        joined_reasons="$(IFS='; '; echo "${fail_reasons[*]}")"
        FAILURE_REASONS["$slug"]="$joined_reasons"
        OVERALL_EXIT=1
    fi

    log "[$slug] Done (failed=$failed)"
}

for slug in "${TENANTS[@]}"; do
    backup_tenant "$slug" || {
        log "WARN: backup_tenant() itself threw for $slug — recording as failure"
        FAILURES+=("$slug")
        FAILURE_REASONS["$slug"]="Unexpected script error in backup_tenant()"
        OVERALL_EXIT=1
    }
done

# ---------------------------------------------------------------------------
# Platform backup
# ---------------------------------------------------------------------------
PLATFORM_DIR="$BACKUP_ROOT/platform/$TODAY"
mkdir -p "$PLATFORM_DIR"

log "--- Platform backup ---"

# nginx configs
log "Archiving nginx configs..."
if tar czf "$PLATFORM_DIR/nginx.tar.gz" /etc/nginx/sites-available/ 2>>"$LOG_FILE"; then
    log "nginx configs archived OK"
else
    log "WARNING: nginx tar failed (non-fatal)"
fi

# git hashes
log "Recording git hashes..."
git -C /opt/nbne/production rev-parse HEAD > "$PLATFORM_DIR/git-production.txt" 2>/dev/null \
    || echo "not a git repo" > "$PLATFORM_DIR/git-production.txt"

git -C /opt/nbne/shared rev-parse HEAD > "$PLATFORM_DIR/git-shared.txt" 2>/dev/null \
    || echo "not a git repo" > "$PLATFORM_DIR/git-shared.txt"

GIT_PRODUCTION="$(cat "$PLATFORM_DIR/git-production.txt")"
GIT_SHARED="$(cat "$PLATFORM_DIR/git-shared.txt")"
log "git-production: $GIT_PRODUCTION"
log "git-shared:     $GIT_SHARED"

# SSL certificates — Let's Encrypt
log "Archiving Let's Encrypt certificates..."
if [[ -d /etc/letsencrypt ]]; then
    if tar czf "$PLATFORM_DIR/letsencrypt.tar.gz" /etc/letsencrypt/ 2>>"$LOG_FILE"; then
        log "Let's Encrypt certs archived OK"
    else
        log "WARNING: letsencrypt tar failed (non-fatal)"
    fi
else
    log "WARNING: /etc/letsencrypt not found — skipping"
fi

# SSL certificates — Cloudflare origin certs
log "Archiving Cloudflare origin certificates..."
if [[ -d /etc/ssl/cloudflare ]]; then
    if tar czf "$PLATFORM_DIR/cloudflare-certs.tar.gz" /etc/ssl/cloudflare/ 2>>"$LOG_FILE"; then
        log "Cloudflare origin certs archived OK"
    else
        log "WARNING: cloudflare-certs tar failed (non-fatal)"
    fi
else
    log "WARNING: /etc/ssl/cloudflare not found — skipping"
fi

# SSH deploy keys (required for git clone on restore)
log "Archiving SSH deploy keys..."
if tar czf "$PLATFORM_DIR/ssh-keys.tar.gz" \
        -C /root/.ssh \
        id_ed25519 id_ed25519.pub \
        id_ed25519_production id_ed25519_production.pub \
        config \
        2>>"$LOG_FILE"; then
    chmod 600 "$PLATFORM_DIR/ssh-keys.tar.gz"
    log "SSH deploy keys archived OK"
else
    log "WARNING: SSH keys tar failed (non-fatal — check key filenames)"
fi

# ---------------------------------------------------------------------------
# Write manifest.json
# ---------------------------------------------------------------------------
log "Writing manifest.json..."

# Build tenant JSON array
tenant_json_entries=""
for slug in "${TENANTS[@]}"; do
    tenant_dir="$BACKUP_ROOT/$slug/$TODAY"

    sql_gz_bytes="$(stat -c '%s' "$tenant_dir/$slug.sql.gz" 2>/dev/null || echo 0)"
    media_tar_bytes="$(stat -c '%s' "$tenant_dir/media.tar.gz" 2>/dev/null || echo 0)"

    if [[ -f "$tenant_dir/.env.bak" ]]; then
        env_bak_present="true"
    else
        env_bak_present="false"
    fi

    tenant_json_entries="${tenant_json_entries}
        {\"slug\": \"${slug}\", \"sql_gz_bytes\": ${sql_gz_bytes}, \"media_tar_bytes\": ${media_tar_bytes}, \"env_bak_present\": ${env_bak_present}},"
done
# strip trailing comma from last entry
tenant_json_entries="${tenant_json_entries%,}"

nginx_tar_bytes="$(stat -c '%s' "$PLATFORM_DIR/nginx.tar.gz" 2>/dev/null || echo 0)"

# Build failures array
failures_json=""
for slug in "${FAILURES[@]}"; do
    reason="${FAILURE_REASONS[$slug]:-unknown}"
    # escape double-quotes in reason
    reason_escaped="${reason//\"/\\\"}"
    failures_json="${failures_json}{\"slug\": \"${slug}\", \"reason\": \"${reason_escaped}\"},"
done
failures_json="${failures_json%,}"

/usr/bin/python3 - <<PYEOF
import json, sys

tenants_raw = """${tenant_json_entries}"""
failures_raw = """${failures_json}"""

def parse_array(raw):
    raw = raw.strip()
    if not raw:
        return []
    try:
        return json.loads("[" + raw + "]")
    except Exception as e:
        return [{"parse_error": str(e), "raw": raw[:200]}]

manifest = {
    "timestamp": "${TIMESTAMP}",
    "tenant_count": ${#TENANTS[@]},
    "tenants": parse_array(tenants_raw),
    "platform": {
        "nginx_tar_bytes": ${nginx_tar_bytes},
        "git_production": "${GIT_PRODUCTION}",
        "git_shared": "${GIT_SHARED}"
    },
    "failures": parse_array(failures_raw)
}

with open("$PLATFORM_DIR/manifest.json", "w") as f:
    json.dump(manifest, f, indent=2)

print("manifest.json written OK")
PYEOF

log "manifest.json written to $PLATFORM_DIR/manifest.json"

# ---------------------------------------------------------------------------
# Off-site push to Contabo B2
# ---------------------------------------------------------------------------
RCLONE_REMOTE="contabo:ark-backups"
OFFSITE_EXIT=0

log "--- Off-site push ---"
log "Pushing $TODAY backup to $RCLONE_REMOTE..."

if rclone copy "$BACKUP_ROOT" "$RCLONE_REMOTE/daily" \
        --include "*/$TODAY/**" \
        --transfers 4 \
        --log-level INFO \
        2>>"$LOG_FILE"; then
    log "Off-site push OK — $RCLONE_REMOTE/daily"
else
    log "ERROR: Off-site push failed — local backup still intact"
    OFFSITE_EXIT=1
    OVERALL_EXIT=1
fi

# ---------------------------------------------------------------------------
# Remote retention pruning — keep 7 daily, 4 weekly (Sun), 3 monthly (1st)
# ---------------------------------------------------------------------------
log "Pruning remote backups..."
/usr/bin/python3 - <<'PYEOF'
import subprocess, re, sys
from datetime import datetime, timedelta

remote = "contabo:ark-backups/daily"

# List all date directories present on remote
result = subprocess.run(
    ["rclone", "lsd", remote],
    capture_output=True, text=True
)

dates = []
for line in result.stdout.splitlines():
    # rclone lsd output: "          -1 YYYY-MM-DD HH:MM:SS        -1 dirname"
    parts = line.split()
    if parts:
        d = parts[-1]
        if re.match(r'^\d{4}-\d{2}-\d{2}$', d):
            try:
                dates.append(datetime.strptime(d, "%Y-%m-%d").date())
            except ValueError:
                pass

dates.sort(reverse=True)
today = datetime.utcnow().date()

keep = set()

# 7 most recent daily
for d in dates[:7]:
    keep.add(d)

# 4 most recent Sundays
sundays = [d for d in dates if d.weekday() == 6]
for d in sundays[:4]:
    keep.add(d)

# 3 most recent 1st-of-month
firsts = [d for d in dates if d.day == 1]
for d in firsts[:3]:
    keep.add(d)

to_delete = [d for d in dates if d not in keep]

for d in to_delete:
    target = f"{remote}/{d}"
    print(f"Pruning remote: {target}")
    subprocess.run(["rclone", "purge", target], check=False)

print(f"Remote retention: kept {len(keep)}, pruned {len(to_delete)}")
PYEOF

log "Remote retention pruning complete"

# ---------------------------------------------------------------------------
# Email on failure
# ---------------------------------------------------------------------------
if [[ ${#FAILURES[@]} -gt 0 ]]; then
    log "Sending failure notification to $EMAIL_TO..."

    failure_lines=""
    for slug in "${FAILURES[@]}"; do
        reason="${FAILURE_REASONS[$slug]:-unknown}"
        failure_lines="$failure_lines  - $slug: $reason\n"
    done

    /usr/sbin/sendmail -t <<MAILEOF
To: $EMAIL_TO
From: ark-backup@nbnesigns.com
Subject: [ARK BACKUP FAILURE] $TODAY — ${#FAILURES[@]} tenant(s) failed

ARK Backup run on $TODAY encountered failures.

Timestamp: $TIMESTAMP
Host: $(hostname)
Off-site push: $([ $OFFSITE_EXIT -eq 0 ] && echo "OK" || echo "FAILED")

Failed tenants:
$(printf "$failure_lines")

Full log: $LOG_FILE

This is an automated message from the ARK backup script on $(hostname).
MAILEOF

    log "Failure email sent to $EMAIL_TO"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
log "========================================================"
log "ARK backup complete — $TODAY"
log "  Tenants backed up : ${#TENANTS[@]}"
log "  Failures          : ${#FAILURES[@]}"
if [[ ${#FAILURES[@]} -gt 0 ]]; then
    for slug in "${FAILURES[@]}"; do
        log "    FAILED: $slug — ${FAILURE_REASONS[$slug]:-unknown}"
    done
fi
log "  Platform dir      : $PLATFORM_DIR"
log "  Exit code         : $OVERALL_EXIT"
log "========================================================"

exit $OVERALL_EXIT
