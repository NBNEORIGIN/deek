# Phloe Deployment — Decision Log
# Covers: Docker build pipeline, Next.js compilation, Hetzner server ops

---

## 2026-04-06 — Repo Clarification: nbne_platform, Not nbne_production

**Context**: Two repos exist — `nbne_production` and `nbne_platform`. Memory said
Hetzner deployed from `nbne_production`. A month-long confusion resulted in pushing
changes to the wrong repo and wondering why they didn't appear on live sites.

**Decision**: `/opt/nbne/shared` on Hetzner (178.104.1.152) is a clone of
`github.com/NBNEORIGIN/nbne_platform` (remote alias `github-platform`). All client
Docker builds use this as their build context. Push live changes to `nbne_platform`.

**Evidence**: `root@nbne-prod-1:/opt/nbne/shared# git remote -v` shows `github-platform`.

**Action**: Push to `nbne_platform/main` → SSH `git pull` in `/opt/nbne/shared` →
rebuild container. `nbne_production` may serve some instances but is NOT the shared
build context.

---

## 2026-04-06 — Docker Compose Must Use -p slug Flag

**Context**: Running `docker compose -f /opt/nbne/shared/docker/docker-compose.client.yml`
from `/opt/nbne/instances/northumberland-karate/` without a project name caused Docker
to name the container `docker-frontend-1` (derived from the compose file's parent dir
`docker/`) instead of `northumberland-karate-frontend-1`.

**Problem**: The wrongly-named container was on a different Docker network than the
backend (`docker_default` vs `northumberland-karate_default`), so
`DJANGO_BACKEND_URL: http://backend:8000` couldn't resolve.

**Decision**: Always use `-p <client-slug>` in every docker compose command for
Phloe client instances.

**Correct commands**:
```bash
# Build
docker compose -p northumberland-karate --env-file .env \
  -f /opt/nbne/shared/docker/docker-compose.client.yml \
  build --no-cache frontend

# Start
docker compose -p northumberland-karate --env-file .env \
  -f /opt/nbne/shared/docker/docker-compose.client.yml \
  up -d --no-deps frontend
```

**Rule**: `-p <slug>` is mandatory. Without it the container network is wrong and
the frontend cannot reach the backend.

---

## 2026-04-06 — --no-cache Required for File Changes to Take Effect

**Context**: Docker layer caching means `COPY frontend/ ./` is only re-executed if
Docker detects a change in the image layers above it. After `git pull`, the files
on disk change but Docker's build cache still has the old COPY layer.

**Decision**: Always use `--no-cache` when rebuilding after a `git pull`. Never
assume a normal `build` will pick up new source files.

**Rule**: `docker compose ... build --no-cache frontend` — always `--no-cache`.

---

## 2026-04-06 — SWC Regression in Next.js 14.2.x — Babel Fallback Pattern

**Context**: Next.js 14.2.21 and 14.2.35 both contain an SWC parser regression
that produces "Unexpected token `div`. Expected jsx identifier" on certain valid
JSX patterns. The bug affects large functional components with complex control flow.

**Specific patterns that trigger the SWC bug**:
1. Ternary false branch with multiple sibling JSX children not wrapped in a fragment
2. IIFE (`(() => { return (<JSX />) })()`) used as a ternary branch in JSX
3. Large components with many early conditional returns followed by a main return

**Decision**: Add `frontend/.babelrc` to force Babel compilation and bypass SWC.

**Correct .babelrc**:
```json
{
  "presets": [
    ["next/babel", {
      "preset-env": {
        "targets": { "esmodules": true }
      },
      "transform-runtime": {
        "regenerator": false
      }
    }]
  ]
}
```

**Why `esmodules: true`**: Without this, `@babel/preset-env` transforms async/await
to generator functions, which require `regeneratorRuntime` at runtime. Since
`@babel/runtime` is not in the project's own `package.json`, the build succeeds but
the site crashes with `ReferenceError: regeneratorRuntime is not defined`.
`esmodules: true` targets browsers that support async/await natively — no transform,
no runtime needed.

**Why `regenerator: false`**: Belt-and-suspenders. Prevents `@babel/plugin-transform-runtime`
from trying to inject `@babel/runtime/regenerator` imports (which also fails at
build time since `@babel/runtime` isn't a direct dependency).

**Note**: Next.js warns "It looks like there is a custom Babel configuration that
can be removed." This warning is harmless — ignore it until SWC is fixed.

---

## 2026-04-06 — JSX Patterns That Must Be Fixed (Even With Babel)

The SWC bug revealed real JSX validity issues. Babel caught two bugs that SWC was
silently miscompiling:

### Pattern 1: Sibling JSX without fragment in ternary branch
**Wrong**:
```tsx
{loading ? (
  <div>Loading</div>
) : (
  <div>Items</div>
  {extra && <div>Extra</div>}   // ← INVALID: two siblings, no wrapper
)}
```
**Correct**:
```tsx
{loading ? (
  <div>Loading</div>
) : (
  <>
    <div>Items</div>
    {extra && <div>Extra</div>}
  </>
)}
```

### Pattern 2: IIFE computing variables inside JSX return
**Wrong** (causes Babel parse failure in ternary context):
```tsx
{step === 3 ? (() => {
  const url = computeUrl()
  return <div>{url}</div>
})() : <div>Step 1</div>}
```
**Correct** (compute before JSX return):
```tsx
// Before return statement:
const url = step === 3 ? computeUrl() : ''
// In JSX:
{step === 3 ? <div>{url}</div> : <div>Step 1</div>}
```

**Rule**: Never use IIFEs inside JSX. If you need computed variables, compute them
before the `return (` statement.

---

## 2026-04-06 — Untracked Files on Server Corrupt Docker Builds

**Context**: A directory `frontend/app/super-admin/tenants/[id]/landing-page/`
existed as an untracked file in `/opt/nbne/shared` (never committed to git).
Docker's `COPY frontend/ ./` picked it up, causing TypeScript to find the file
and fail: "Module '@/lib/api-superadmin' has no exported member 'getTenantLandingPage'".

**Decision**: Before any rebuild, check for untracked files in `/opt/nbne/shared`:
```bash
git -C /opt/nbne/shared status --short
```
Remove any untracked files that shouldn't be included in the build. Common culprits:
- Experimental pages/components developed directly on the server
- `next.config.js.bak`
- Debug scripts

**Rule**: The server's `/opt/nbne/shared` should always be clean (`git status` shows
only tracked files). Never develop directly on the server.

---

## 2026-04-06 — Playwright Config Must Be Excluded From TypeScript

**Context**: `frontend/playwright.config.ts` imports `@playwright/test` which is
not in the project's `package.json`. Next.js TypeScript build picks up all `**/*.ts`
files and fails: "Cannot find module '@playwright/test'".

**Decision**: Add `playwright.config.ts` and `tests` to `tsconfig.json` exclude list.

```json
{
  "exclude": ["node_modules", "playwright.config.ts", "tests"]
}
```

**This applies to all Phloe client instances** — the tsconfig fix is in `nbne_platform`
so it's inherited by all containers.

---

## 2026-04-06 — SSH Autonomy: Always Execute Server Commands Directly

**Context**: During a deployment session, server commands were presented as a block
for the user to paste manually. This breaks the autonomous agent model.

**Decision**: All Hetzner server operations must be executed via the Bash tool using
SSH. Never present server commands for the user to run manually.

**Pattern**:
```bash
ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@178.104.1.152 \
  "cd /opt/nbne/shared && git pull && cd /opt/nbne/instances/<slug> && \
  docker compose -p <slug> --env-file .env \
  -f /opt/nbne/shared/docker/docker-compose.client.yml \
  build --no-cache frontend && \
  docker compose -p <slug> --env-file .env \
  -f /opt/nbne/shared/docker/docker-compose.client.yml \
  up -d --no-deps frontend"
```

**The full deployment flow**:
1. Push to `nbne_platform/main`
2. SSH → `git pull` in `/opt/nbne/shared`
3. Check for untracked files (`git status --short`)
4. `docker compose -p <slug> ... build --no-cache frontend`
5. `docker compose -p <slug> ... up -d --no-deps frontend`
6. Verify: `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:<port>/`

---

## 2026-04-06 — Rollout Checklist for New Module to Existing Client

When deploying a new module (e.g. `ledger_lite`) to an existing client:

1. **Backend**: Ensure Django views, models, urls registered in `api_urls.py`
2. **Frontend pages**: Ensure `/admin/<module>/page.tsx` exists in `nbne_platform`
3. **Nav entry**: Add to `AdminLayoutClient.tsx` NAV_ITEMS with correct `module` key
4. **Tenant config**: Enable module in tenant's `enabled_modules` via Django admin
   or migration seed
5. **Build**: `git push nbne_platform/main` → rebuild container with `--no-cache`
6. **Verify**: Check nav item appears in sidebar, page loads, API responds

**Common miss**: Adding nav entry to `nbne_production/AdminLayoutClient.tsx` but
NOT to `nbne_platform/AdminLayoutClient.tsx`. The server builds from `nbne_platform`.
