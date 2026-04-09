#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Ark Restore Script — /opt/ark/ark-restore.sh
# Rebuilds the entire Phloe platform on a fresh Ubuntu 24 server from a
# Contabo Object Storage backup.
#
# Usage:
#   ./ark-restore.sh --date YYYY-MM-DD --cf-token <cloudflare_api_token> [--dry-run]
#
# Run this ON the fresh server as root, or via SSH:
#   ssh root@<new-server-ip> "bash -s" < ark-restore.sh -- --date 2026-03-31 --cf-token TOKEN
#
# Prerequisites on fresh server:
#   - Ubuntu 24.04 LTS
#   - Root access
#   - Internet connectivity
#
# What it does:
#   1. Install Docker, nginx, certbot, rclone, python3, postfix
#   2. Configure rclone with Contabo S3 credentials
#   3. Download backup for the given date from Contabo
#   4. Restore SSH deploy keys (needed for git clone)
#   5. Clone production and shared repos at recorded git hashes
#   6. Restore per-tenant: .env, Docker stack, database, media volume
#   7. Restore nginx configs and SSL certificates
#   8. Update Cloudflare DNS A records to this server's IP
#   9. Enable nginx sites and reload
#  10. Verify all tenant domains respond HTTP 200/301
#  11. Install ark cron on new server
# ---------------------------------------------------------------------------

CONTABO_ENDPOINT="eu2.contabostorage.com"
CONTABO_BUCKET="ark-backups"
CONTABO_ACCESS_KEY="21653fd73003b0428c4801ec0aeb5cff"
CONTABO_SECRET_KEY="258f1e9ec43c499ea256dd4e02bbde07"

RESTORE_ROOT="/tmp/ark-restore"
INSTANCES_DIR="/opt/nbne/instances"
SHARED_DIR="/opt/nbne/shared"
PRODUCTION_DIR="/opt/nbne/production"
BACKUP_LOG="/var/log/ark-restore.log"

REPO_PRODUCTION="git@github-production:NBNEORIGIN/nbne_production.git"
REPO_SHARED="git@github-platform:NBNEORIGIN/nbne_platform.git"

DRY_RUN=0
SKIP_DNS=0
RESTORE_DATE=""
CF_TOKEN=""
THIS_IP=""

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --date)       RESTORE_DATE="$2"; shift 2 ;;
        --cf-token)   CF_TOKEN="$2"; shift 2 ;;
        --dry-run)    DRY_RUN=1; shift ;;
        --skip-dns)   SKIP_DNS=1; shift ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

if [[ -z "$RESTORE_DATE" ]]; then
    echo "ERROR: --date YYYY-MM-DD is required"
    exit 1
fi

if [[ -z "$CF_TOKEN" && $SKIP_DNS -eq 0 ]]; then
    echo "ERROR: --cf-token is required unless --skip-dns is set"
    echo "       Use --skip-dns for drill runs to avoid flipping live client DNS"
    exit 1
fi

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
mkdir -p "$(dirname "$BACKUP_LOG")"
exec > >(tee -a "$BACKUP_LOG") 2>&1

log() {
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
}

run() {
    # Wrapper: skips execution in dry-run mode
    if [[ $DRY_RUN -eq 1 ]]; then
        log "[DRY-RUN] $*"
    else
        "$@"
    fi
}

log "========================================================"
log "ARK RESTORE starting — date=$RESTORE_DATE dry_run=$DRY_RUN"
log "========================================================"

# ---------------------------------------------------------------------------
# Step 0: Detect this server's public IP
# ---------------------------------------------------------------------------
THIS_IP="$(curl -fsSL https://api.ipify.org 2>/dev/null || curl -fsSL https://ifconfig.me 2>/dev/null || echo '')"
if [[ -z "$THIS_IP" ]]; then
    log "ERROR: Could not determine this server's public IP — DNS cutover will fail"
    exit 1
fi
log "This server's public IP: $THIS_IP"

# ---------------------------------------------------------------------------
# Step 1: Install dependencies
# ---------------------------------------------------------------------------
log "--- Step 1: Installing dependencies ---"

run apt-get update -qq
run apt-get install -y -qq \
    ca-certificates curl gnupg lsb-release \
    nginx certbot python3-certbot-nginx \
    python3 python3-pip \
    git jq postfix mailutils \
    2>/dev/null

# Docker
if ! command -v docker &>/dev/null; then
    log "Installing Docker..."
    run curl -fsSL https://get.docker.com | bash
    run systemctl enable docker
    run systemctl start docker
else
    log "Docker already installed: $(docker --version)"
fi

# rclone
if ! command -v rclone &>/dev/null; then
    log "Installing rclone..."
    run curl -fsSL https://rclone.org/install.sh | bash
else
    log "rclone already installed: $(rclone --version | head -1)"
fi

# ---------------------------------------------------------------------------
# Step 2: Configure rclone for Contabo
# ---------------------------------------------------------------------------
log "--- Step 2: Configuring rclone ---"

run mkdir -p /root/.config/rclone
if [[ $DRY_RUN -eq 0 ]]; then
    cat > /root/.config/rclone/rclone.conf <<RCLONE_EOF
[contabo]
type = s3
provider = Other
access_key_id = ${CONTABO_ACCESS_KEY}
secret_access_key = ${CONTABO_SECRET_KEY}
endpoint = ${CONTABO_ENDPOINT}
acl = private
RCLONE_EOF
fi
log "rclone configured"

# ---------------------------------------------------------------------------
# Step 3: Download backup from Contabo
# ---------------------------------------------------------------------------
log "--- Step 3: Downloading backup for $RESTORE_DATE ---"

run mkdir -p "$RESTORE_ROOT"

if [[ $DRY_RUN -eq 0 ]]; then
    log "Downloading from contabo:${CONTABO_BUCKET}/daily/*/${RESTORE_DATE}/..."
    rclone copy "contabo:${CONTABO_BUCKET}/daily" "$RESTORE_ROOT" \
        --include "*/${RESTORE_DATE}/**" \
        --transfers 4 \
        --progress
    log "Download complete"

    # Verify we got something
    TENANT_COUNT=$(find "$RESTORE_ROOT" -maxdepth 1 -mindepth 1 -type d \
        ! -name platform | wc -l)
    if [[ $TENANT_COUNT -eq 0 ]]; then
        log "ERROR: No tenant backup directories found for $RESTORE_DATE in $RESTORE_ROOT"
        log "       Check the date and that the backup ran successfully"
        exit 1
    fi
    log "Found $TENANT_COUNT tenant backup(s)"
else
    log "[DRY-RUN] Would download contabo:${CONTABO_BUCKET}/daily/*/${RESTORE_DATE}/"
    TENANT_COUNT=9
fi

PLATFORM_RESTORE="$RESTORE_ROOT/platform/$RESTORE_DATE"

# ---------------------------------------------------------------------------
# Step 4: Restore SSH deploy keys
# ---------------------------------------------------------------------------
log "--- Step 4: Restoring SSH deploy keys ---"

SSH_KEYS_ARCHIVE="$PLATFORM_RESTORE/ssh-keys.tar.gz"
if [[ -f "$SSH_KEYS_ARCHIVE" ]]; then
    run mkdir -p /root/.ssh
    run tar xzf "$SSH_KEYS_ARCHIVE" -C /root/.ssh
    run chmod 600 /root/.ssh/id_ed25519 /root/.ssh/id_ed25519_production 2>/dev/null || true
    run chmod 644 /root/.ssh/id_ed25519.pub /root/.ssh/id_ed25519_production.pub 2>/dev/null || true
    log "SSH deploy keys restored"

    # Write SSH config if not present
    if [[ ! -f /root/.ssh/config ]]; then
        cat > /root/.ssh/config <<SSH_EOF
Host github-platform
  HostName github.com
  User git
  IdentityFile ~/.ssh/id_ed25519
  StrictHostKeyChecking no

Host github-production
  HostName github.com
  User git
  IdentityFile ~/.ssh/id_ed25519_production
  StrictHostKeyChecking no
SSH_EOF
        chmod 600 /root/.ssh/config
        log "SSH config written"
    fi
else
    log "WARNING: ssh-keys.tar.gz not found in platform backup — git clone will fail"
    log "         Manually copy SSH keys before cloning or use HTTPS with a token"
fi

# ---------------------------------------------------------------------------
# Step 5: Clone production and shared repos
# ---------------------------------------------------------------------------
log "--- Step 5: Cloning repositories ---"

GIT_PRODUCTION_HASH="$(cat "$PLATFORM_RESTORE/git-production.txt" 2>/dev/null || echo 'HEAD')"
GIT_SHARED_HASH="$(cat "$PLATFORM_RESTORE/git-shared.txt" 2>/dev/null || echo 'HEAD')"
log "git-production hash: $GIT_PRODUCTION_HASH"
log "git-shared hash:     $GIT_SHARED_HASH"

run mkdir -p /opt/nbne

if [[ ! -d "$PRODUCTION_DIR/.git" ]]; then
    log "Cloning production repo..."
    run git clone "$REPO_PRODUCTION" "$PRODUCTION_DIR"
else
    log "Production repo already present — fetching..."
    run git -C "$PRODUCTION_DIR" fetch origin
fi

if [[ "$GIT_PRODUCTION_HASH" != "HEAD" && "$GIT_PRODUCTION_HASH" != "not a git repo" ]]; then
    run git -C "$PRODUCTION_DIR" checkout "$GIT_PRODUCTION_HASH"
    log "Checked out production at $GIT_PRODUCTION_HASH"
fi

# NOTE: On the production server, /opt/nbne/shared tracks nbne_production commits
# (not nbne_platform), because it has the production remote added and is checked out
# at the production HEAD. We replicate this here by cloning production to shared too,
# then adding the platform remote for completeness.
if [[ ! -d "$SHARED_DIR/.git" ]]; then
    log "Cloning production repo to shared dir (matches production server state)..."
    run git clone "$REPO_PRODUCTION" "$SHARED_DIR"
    # Add platform remote for reference
    run git -C "$SHARED_DIR" remote add platform "$REPO_SHARED" 2>/dev/null || true
else
    log "Shared repo already present — fetching production remote..."
    run git -C "$SHARED_DIR" remote add production "$REPO_PRODUCTION" 2>/dev/null || true
    run git -C "$SHARED_DIR" fetch production 2>>"$BACKUP_LOG" || true
fi

if [[ "$GIT_PRODUCTION_HASH" != "HEAD" && "$GIT_PRODUCTION_HASH" != "not a git repo" ]]; then
    if git -C "$SHARED_DIR" checkout "$GIT_PRODUCTION_HASH" 2>>"$BACKUP_LOG"; then
        log "Checked out shared at production hash $GIT_PRODUCTION_HASH"
    else
        log "WARNING: Could not checkout shared at $GIT_PRODUCTION_HASH — using HEAD"
    fi
fi

# ---------------------------------------------------------------------------
# Step 6: Per-tenant restore
# ---------------------------------------------------------------------------
log "--- Step 6: Restoring tenants ---"

TENANT_FAILURES=()

restore_tenant() {
    local slug="$1"
    local tenant_backup="$RESTORE_ROOT/$slug/$RESTORE_DATE"
    local env_file="$INSTANCES_DIR/$slug/.env"
    local db_container="${slug}-db-1"
    local media_volume="${slug}_media"
    local compose_file="$SHARED_DIR/docker/docker-compose.client.yml"

    log "  [$slug] Starting restore..."

    # --- a. Restore .env ---
    if [[ ! -f "$tenant_backup/.env.bak" ]]; then
        log "  [$slug] ERROR: .env.bak not found in backup — skipping tenant"
        return 1
    fi
    run mkdir -p "$INSTANCES_DIR/$slug"
    run cp "$tenant_backup/.env.bak" "$env_file"
    run chmod 600 "$env_file"
    log "  [$slug] .env restored"

    # --- b. Start Docker stack ---
    log "  [$slug] Starting Docker stack..."
    # Build errors (e.g. frontend compile) are non-fatal — DB may still start
    docker compose \
        -p "$slug" \
        --env-file "$env_file" \
        -f "$compose_file" \
        up -d --build 2>>"$BACKUP_LOG" || log "  [$slug] WARNING: docker compose up returned non-zero (frontend build may have failed — DB restore will still be attempted)"

    # --- c. Wait for DB container healthy ---
    log "  [$slug] Waiting for DB container to be healthy..."
    local attempts=0
    while [[ $DRY_RUN -eq 0 ]]; do
        if docker inspect --format '{{.State.Health.Status}}' "$db_container" 2>/dev/null \
                | grep -q '^healthy$'; then
            break
        fi
        attempts=$((attempts + 1))
        if [[ $attempts -ge 30 ]]; then
            log "  [$slug] ERROR: DB container not healthy after 60s — skipping DB restore"
            return 1
        fi
        sleep 2
    done
    log "  [$slug] DB container healthy"

    # --- d. Restore database ---
    local sql_gz="$tenant_backup/$slug.sql.gz"
    if [[ -f "$sql_gz" ]]; then
        local sql_size
        sql_size="$(stat -c '%s' "$sql_gz")"
        if [[ $sql_size -gt 20 ]]; then
            log "  [$slug] Restoring database (${sql_size} bytes compressed)..."
            if [[ $DRY_RUN -eq 0 ]]; then
                gunzip -c "$sql_gz" | docker exec -i "$db_container" \
                    psql -U nbne -d "$slug" -q 2>>"$BACKUP_LOG"
            fi
            log "  [$slug] Database restored"
        else
            log "  [$slug] WARNING: SQL dump is ${sql_size} bytes — likely empty, skipping"
        fi
    else
        log "  [$slug] WARNING: No SQL dump found — database will be empty"
    fi

    # --- e. Restore media volume ---
    local media_tar="$tenant_backup/media.tar.gz"
    if [[ -f "$media_tar" ]]; then
        local media_size
        media_size="$(stat -c '%s' "$media_tar")"
        if [[ $media_size -gt 20 ]]; then
            log "  [$slug] Restoring media volume (${media_size} bytes compressed)..."
            run docker run --rm \
                -v "${media_volume}:/data" \
                -v "${tenant_backup}:/restore:ro" \
                alpine:latest \
                tar xzf /restore/media.tar.gz -C /data
            log "  [$slug] Media volume restored"
        else
            log "  [$slug] Media volume empty — skipping"
        fi
    fi

    log "  [$slug] Restore complete"
}

# Discover tenants from backup
TENANTS=()
while IFS= read -r -d '' entry; do
    slug="$(basename "$entry")"
    if [[ "$slug" != "platform" && "$slug" != *.* ]]; then
        TENANTS+=("$slug")
    fi
done < <(find "$RESTORE_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%p\0' 2>/dev/null \
    || find "$RESTORE_ROOT" -mindepth 1 -maxdepth 1 -type d -print0)

log "Tenants to restore: ${TENANTS[*]:-none}"

for slug in "${TENANTS[@]}"; do
    restore_tenant "$slug" || {
        log "  [$slug] FAILED — recording and continuing"
        TENANT_FAILURES+=("$slug")
    }
done

# ---------------------------------------------------------------------------
# Step 7: Restore nginx configs and SSL certificates
# ---------------------------------------------------------------------------
log "--- Step 7: Restoring nginx and SSL ---"

# nginx configs
NGINX_ARCHIVE="$PLATFORM_RESTORE/nginx.tar.gz"
if [[ -f "$NGINX_ARCHIVE" ]]; then
    run tar xzf "$NGINX_ARCHIVE" -C / 2>>"$BACKUP_LOG"
    log "nginx configs restored to /etc/nginx/sites-available/"
else
    log "WARNING: nginx.tar.gz not found — nginx configs not restored"
fi

# Let's Encrypt certs
LE_ARCHIVE="$PLATFORM_RESTORE/letsencrypt.tar.gz"
if [[ -f "$LE_ARCHIVE" ]]; then
    run tar xzf "$LE_ARCHIVE" -C / 2>>"$BACKUP_LOG"
    log "Let's Encrypt certs restored to /etc/letsencrypt/"
else
    log "WARNING: letsencrypt.tar.gz not found — certs will need re-issuing"
    log "         Run: certbot certonly --nginx -d <domain> for each tenant"
fi

# Cloudflare origin certs
CF_CERT_ARCHIVE="$PLATFORM_RESTORE/cloudflare-certs.tar.gz"
if [[ -f "$CF_CERT_ARCHIVE" ]]; then
    run mkdir -p /etc/ssl/cloudflare
    run tar xzf "$CF_CERT_ARCHIVE" -C / 2>>"$BACKUP_LOG"
    log "Cloudflare origin certs restored to /etc/ssl/cloudflare/"
else
    log "WARNING: cloudflare-certs.tar.gz not found — Cloudflare-cert sites will need manual cert restore"
fi

# Enable all nginx sites from backup
log "Enabling nginx sites..."
for conf in /etc/nginx/sites-available/*.conf; do
    site="$(basename "$conf")"
    symlink="/etc/nginx/sites-enabled/$site"
    if [[ ! -L "$symlink" ]]; then
        run ln -s "$conf" "$symlink"
        log "  Enabled: $site"
    fi
done

# Test nginx config before reload
if [[ $DRY_RUN -eq 0 ]]; then
    if nginx -t 2>>"$BACKUP_LOG"; then
        run systemctl reload nginx
        log "nginx reloaded OK"
    else
        log "ERROR: nginx config test failed — not reloading"
        log "       Fix configs then run: nginx -t && systemctl reload nginx"
    fi
fi

# ---------------------------------------------------------------------------
# Step 8: Cloudflare DNS cutover
# ---------------------------------------------------------------------------
if [[ $SKIP_DNS -eq 1 ]]; then
    log "--- Step 8: Cloudflare DNS cutover SKIPPED (--skip-dns) ---"
    log "    To verify the restore manually: curl -H 'Host: demnurse.nbne.uk' http://$THIS_IP/"
    log "    For a real disaster: re-run without --skip-dns to flip live DNS"
else
log "--- Step 8: Cloudflare DNS cutover → $THIS_IP ---"

/usr/bin/python3 - <<PYEOF
import urllib.request, urllib.error, json, sys, os

CF_TOKEN = "${CF_TOKEN}"
NEW_IP = "${THIS_IP}"
INSTANCES_DIR = "${INSTANCES_DIR}"
DRY_RUN = ${DRY_RUN}

headers = {
    "Authorization": f"Bearer {CF_TOKEN}",
    "Content-Type": "application/json"
}

def cf_api(method, path, data=None):
    url = f"https://api.cloudflare.com/client/v4{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"success": False, "error": str(e), "body": e.read().decode()}

# Discover all domains from tenant .env files
domains = set()
if os.path.isdir(INSTANCES_DIR):
    for slug in os.listdir(INSTANCES_DIR):
        env_path = os.path.join(INSTANCES_DIR, slug, ".env")
        if not os.path.isfile(env_path):
            continue
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DOMAIN="):
                    domains.add(line.split("=", 1)[1])
                elif line.startswith("EXTRA_DOMAINS="):
                    for d in line.split("=", 1)[1].split(","):
                        d = d.strip()
                        if d:
                            domains.add(d)

print(f"Domains to update: {sorted(domains)}")

# Get all zones this token can access
zones_resp = cf_api("GET", "/zones?per_page=100")
if not zones_resp.get("success"):
    print(f"ERROR: Could not list Cloudflare zones: {zones_resp}")
    sys.exit(1)

zones = {z["name"]: z["id"] for z in zones_resp.get("result", [])}
print(f"Cloudflare zones accessible: {list(zones.keys())}")

updated = []
skipped = []

for domain in sorted(domains):
    # Find which zone covers this domain (longest suffix match)
    zone_id = None
    zone_name = None
    for zname, zid in zones.items():
        if domain == zname or domain.endswith("." + zname):
            if zone_name is None or len(zname) > len(zone_name):
                zone_name = zname
                zone_id = zid

    if not zone_id:
        print(f"  SKIP {domain} — no matching Cloudflare zone (token may not have access)")
        skipped.append(domain)
        continue

    # Find the A record for this domain
    records_resp = cf_api("GET", f"/zones/{zone_id}/dns_records?type=A&name={domain}")
    records = records_resp.get("result", [])

    if not records:
        print(f"  SKIP {domain} — no A record found in zone {zone_name}")
        skipped.append(domain)
        continue

    for record in records:
        old_ip = record["content"]
        record_id = record["id"]
        if old_ip == NEW_IP:
            print(f"  OK   {domain} — already points to {NEW_IP}")
            updated.append(domain)
            continue

        if DRY_RUN:
            print(f"  DRY  {domain} — would update {old_ip} → {NEW_IP}")
            updated.append(domain)
        else:
            update_resp = cf_api("PATCH", f"/zones/{zone_id}/dns_records/{record_id}", {
                "content": NEW_IP,
                "proxied": record.get("proxied", True)
            })
            if update_resp.get("success"):
                print(f"  DONE {domain} — updated {old_ip} → {NEW_IP}")
                updated.append(domain)
            else:
                print(f"  FAIL {domain} — {update_resp}")
                skipped.append(domain)

print(f"\nDNS cutover: {len(updated)} updated, {len(skipped)} skipped")
if skipped:
    print(f"Manual action needed for: {skipped}")
PYEOF

log "Cloudflare DNS cutover complete"
fi # end SKIP_DNS

# ---------------------------------------------------------------------------
# Step 9: Verify tenant domains
# ---------------------------------------------------------------------------
log "--- Step 9: Verifying tenant responses ---"

if [[ $SKIP_DNS -eq 0 ]]; then
    log "Waiting 15s for DNS propagation..."
    if [[ $DRY_RUN -eq 0 ]]; then sleep 15; fi
fi

VERIFY_PASS=()
VERIFY_FAIL=()

if [[ -d "$INSTANCES_DIR" ]]; then
    for slug_dir in "$INSTANCES_DIR"/*/; do
        slug="$(basename "$slug_dir")"
        env_file="$slug_dir/.env"
        [[ -f "$env_file" ]] || continue

        domain="$(grep '^DOMAIN=' "$env_file" | cut -d= -f2)"
        [[ -n "$domain" ]] || continue

        if [[ $DRY_RUN -eq 1 ]]; then
            log "  [DRY-RUN] Would verify https://$domain"
            continue
        fi

        if [[ $SKIP_DNS -eq 1 ]]; then
            # Drill mode: test via IP with Host header (HTTP only — no valid cert on drill server)
            frontend_port="$(grep '^FRONTEND_PORT=' "$env_file" | cut -d= -f2)"
            http_code="$(curl -s -o /dev/null -w '%{http_code}' \
                --max-time 10 --retry 2 \
                -H "Host: $domain" \
                "http://127.0.0.1:${frontend_port}/" 2>/dev/null || echo '000')"
        else
            http_code="$(curl -fsSL -o /dev/null -w '%{http_code}' \
                --max-time 10 --retry 2 \
                "https://$domain" 2>/dev/null || echo '000')"
        fi

        if [[ "$http_code" =~ ^(200|301|302)$ ]]; then
            log "  PASS $domain — HTTP $http_code"
            VERIFY_PASS+=("$domain")
        else
            log "  FAIL $domain — HTTP $http_code (drill: check docker ps and backend logs)"
            VERIFY_FAIL+=("$domain")
        fi
    done
fi

# ---------------------------------------------------------------------------
# Step 10: Install ark cron on this server
# ---------------------------------------------------------------------------
log "--- Step 10: Installing ark backup cron ---"

run mkdir -p /opt/ark
run cp "$0" /opt/ark/backup.sh 2>/dev/null || true  # Copy backup.sh if available

if ! crontab -l 2>/dev/null | grep -q '/opt/ark/backup.sh'; then
    (crontab -l 2>/dev/null; echo "30 3 * * * /opt/ark/backup.sh >> /var/log/ark-backup.log 2>&1") | crontab -
    log "ark backup cron installed (03:30 UTC daily)"
else
    log "ark backup cron already present"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
log "========================================================"
log "ARK RESTORE complete — $RESTORE_DATE"
log "  Tenants restored  : ${#TENANTS[@]}"
log "  Tenant failures   : ${#TENANT_FAILURES[@]}"
if [[ ${#TENANT_FAILURES[@]} -gt 0 ]]; then
    for slug in "${TENANT_FAILURES[@]}"; do
        log "    FAILED: $slug"
    done
fi
log "  Verify passed     : ${#VERIFY_PASS[@]}"
log "  Verify failed     : ${#VERIFY_FAIL[@]}"
if [[ ${#VERIFY_FAIL[@]} -gt 0 ]]; then
    for domain in "${VERIFY_FAIL[@]}"; do
        log "    FAILED: $domain"
    done
fi
log "  New server IP     : $THIS_IP"
log "========================================================"

if [[ ${#TENANT_FAILURES[@]} -gt 0 || ${#VERIFY_FAIL[@]} -gt 0 ]]; then
    log "RESTORE COMPLETED WITH ERRORS — manual intervention required"
    exit 1
else
    log "RESTORE SUCCESSFUL"
    exit 0
fi
