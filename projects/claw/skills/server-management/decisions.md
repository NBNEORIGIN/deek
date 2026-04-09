# Server Management — Decision Log
# Covers: Hetzner ops, Docker, SSH automation

---

## 2026-04-06 — Always Use SSH Tool, Never Paste Commands

**Context**: Server commands were presented to the user as a paste block instead of
being executed directly via the Bash tool.

**Decision**: All server operations must be executed autonomously via:
```bash
ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@178.104.1.152 "<commands>"
```

Never ask the user to run server commands manually. This is the chief engineer model.

---

## 2026-04-06 — Hetzner Docker Compose: -p Flag is Mandatory

**Problem**: Docker derives the project name from the compose file path when no `-p`
flag is given. Since the shared compose file lives in `/opt/nbne/shared/docker/`,
Docker uses `docker` as the project name, creating `docker-frontend-1` on the
`docker_default` network. The backend is on `northumberland-karate_default`. They
cannot communicate.

**Solution**: Always pass `-p <client-slug>` to docker compose:
```bash
docker compose -p northumberland-karate \
  --env-file .env \
  -f /opt/nbne/shared/docker/docker-compose.client.yml \
  build --no-cache frontend
```

---

## 2026-04-06 — Full Phloe Client Rebuild Sequence

```bash
# 1. Pull latest code
cd /opt/nbne/shared && git pull

# 2. Check for untracked files that would corrupt the build
git status --short

# 3. Rebuild frontend (always --no-cache after code changes)
cd /opt/nbne/instances/<SLUG>
docker compose -p <SLUG> --env-file .env \
  -f /opt/nbne/shared/docker/docker-compose.client.yml \
  build --no-cache frontend

# 4. Restart frontend container
docker compose -p <SLUG> --env-file .env \
  -f /opt/nbne/shared/docker/docker-compose.client.yml \
  up -d --no-deps frontend

# 5. Verify
sleep 4 && curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:<PORT>/
```

Replace `<SLUG>` and `<PORT>` with instance values from `/opt/nbne/instances/<SLUG>/.env`.

---

## Instance Reference

| Slug                   | Domain                         | Frontend | Backend |
|------------------------|--------------------------------|----------|---------|
| northumberland-karate  | ganbarukai.co.uk               | 3005     | 8005    |
| demnurse               | demnurse.co.uk                 | 3006     | 8006    |
| mind-department        | app.nbnesigns.co.uk            | 3007     | 8007    |
| napco-pizza            | budlepizza.com                 | 3014     | 8014    |
| amble-pincushion       | amble-pincushion.phloe.co.uk   | 3013     | 8013    |
| manufacture            | manufacture.nbnesigns.co.uk    | 3015     | 8015    |
| salon-x                | (demo)                         | 3001     | 8001    |
| restaurant-x           | (demo)                         | 3002     | 8002    |
| health-club-x          | (demo)                         | 3003     | 8003    |
| pizza-shack-x          | (demo)                         | 3004     | 8004    |

---

## 2026-04-06 — nbne_platform is the Build Source, Not nbne_production

`/opt/nbne/shared` = clone of `github.com/NBNEORIGIN/nbne_platform`.
Push live changes to `nbne_platform/main`. The `nbne_production` repo is separate
and does NOT drive the shared Docker build context.
