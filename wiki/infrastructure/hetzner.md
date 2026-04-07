# Hetzner Server — 178.104.1.152

## Access

```bash
ssh root@178.104.1.152 -i ~/.ssh/id_ed25519
```

Key: `~/.ssh/id_ed25519` (ed25519, passwordless root access)

## Layout

| Path | Contents |
|------|----------|
| `/opt/nbne/` | All project deployments |
| `/opt/nbne/manufacture/` | Manufacture standalone stack |
| `/opt/nbne/cairn/deploy/` | Cairn (FastAPI + Next.js) |
| `/opt/nbne/shared/` | Shared Phloe platform (may lag nbne_platform) |
| `/opt/nbne/production/` | NBNEORIGIN/nbne_production (mind-department) |
| `/opt/nbne/instances/<slug>/` | Per-client .env files |
| `/etc/nginx/sites-enabled/` | Nginx virtual host configs |
| `/etc/ssl/cloudflare/` | Cloudflare origin certs |

## Deployed Services

| Domain | Compose project | Ports (host) | Deploy path |
|--------|----------------|--------------|-------------|
| manufacture.nbnesigns.co.uk | `docker` | 8015/3015 | `/opt/nbne/manufacture/docker/` |
| app.nbnesigns.co.uk | `mind-department` | 8010/3010 | `/opt/nbne/production/` |
| cairn.nbnesigns.co.uk | `deploy` | 8765/3000 | `/opt/nbne/cairn/deploy/` |
| amble-pincushion.phloe.co.uk | `amble-pincushion` | 8013/3013 | `/opt/nbne/shared/docker/` |
| budlepizza.com | `napco-pizza` | 8014/3014 | `/opt/nbne/shared/docker/` |
| northumberland-karate.phloe.co.uk | `northumberland-karate` | 8005/3005 | `/opt/nbne/shared/docker/` |
| demnurse.phloe.co.uk | `demnurse` | 8011/3011 | `/opt/nbne/shared/docker/` |
| ledger.nbnesigns.co.uk | `ledger` | 8001/3001 | `/opt/nbne/ledger/` |

## Common Commands

```bash
# Hotpatch a Python file (no rebuild needed)
docker cp local/file.py <container>:/app/path/file.py
docker kill --signal=HUP <container>    # reload gunicorn workers

# Full restart with updated env
cd /opt/nbne/<project>/docker
docker compose --env-file /opt/nbne/<project>/.env up -d --no-build backend

# Full rebuild (image change)
CACHEBUST=$(date +%s) docker compose --env-file /opt/nbne/<project>/.env up -d --build backend

# Check logs
docker logs <container> --tail 50 -f

# Run Django management command
docker exec <container> python manage.py <command>
```

## Manufacture-specific

```bash
# Manufacture container names (project name = 'docker' — parent dir of compose file)
docker-backend-1    # Django/gunicorn on port 8015
docker-frontend-1   # Next.js on port 3015
docker-db-1         # PostgreSQL

# Manufacture .env location
/opt/nbne/manufacture/.env

# Symlink: docker dir reads from parent .env
/opt/nbne/manufacture/docker/.env -> /opt/nbne/manufacture/.env
```

## Phloe Client Commands

```bash
# Rebuild client frontend
cd /opt/nbne/shared/docker
docker compose -p <slug> --env-file /opt/nbne/instances/<slug>/.env -f docker-compose.client.yml up -d --build --no-deps frontend
```

## Cairn Network

Cairn runs on `deploy_default` Docker network. Manufacture backend joins `cairn_net` (external alias for `deploy_default`) so they can talk container-to-container. However, Cairn is started with `--host 0.0.0.0` but cross-network HTTP may timeout due to iptables — use direct SP-API calls from Manufacture rather than relying on Cairn HTTP.
