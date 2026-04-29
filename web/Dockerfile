# Deek web UI — Next.js 14 production image.
#
# Two-stage build:
#   1. builder  — install deps, run `next build` with output: 'standalone'
#   2. runner   — node:20-alpine with just the trimmed standalone server
#                 bundle, .next/static, and public/ (manifest, icons, sw.js)
#
# The deployed container runs on port 3000 inside the Docker network and
# is exposed to the host at 127.0.0.1:3020 via the cairn deploy compose
# file. No external ports — nginx fronts it at cairn.nbnesigns.co.uk.

FROM node:20-alpine AS builder
WORKDIR /app

# Install deps first so they're cached independently of source edits.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# Copy the rest and build.
COPY . .

# Cache-bust the build layer when the caller passes a fresh commit hash
# (same pattern as deploy/Dockerfile.api). Default 'dev' caches normally.
ARG GIT_COMMIT=dev
RUN echo "deek-web build git_commit=${GIT_COMMIT}" > /app/.build_commit

# Next.js inlines process.env.* into route handler code at build time,
# regardless of function-wrapping tricks — so the key MUST be passed
# as a build arg. If it isn't, we fail the build loudly rather than
# bake a placeholder into production. This class of bug cost us a
# full session on 2026-04-19 when the placeholder 'deek-dev-key-…'
# got compiled into prod and every chat request 502'd.
ARG DEEK_API_URL=http://deek-api:8765
ARG DEEK_API_KEY
RUN test -n "$DEEK_API_KEY" && [ "$DEEK_API_KEY" != "deek-dev-key-change-in-production" ] || (echo "FATAL: DEEK_API_KEY build-arg missing or placeholder. Pass --build-arg DEEK_API_KEY=\$DEEK_API_KEY from deploy/.env." >&2 && exit 1)
ENV DEEK_API_URL=${DEEK_API_URL}
ENV DEEK_API_KEY=${DEEK_API_KEY}
ENV CLAW_API_URL=${DEEK_API_URL}
ENV CLAW_API_KEY=${DEEK_API_KEY}

# Optional brand override — Next.js inlines NEXT_PUBLIC_* into the
# client bundle at build time, so this MUST be a build arg, not a
# runtime env. Default 'Deek' when unset; pass --build-arg
# NEXT_PUBLIC_DEEK_BRAND_NAME=Rex for jo-pip's PWA.
ARG NEXT_PUBLIC_DEEK_BRAND_NAME=Deek
ENV NEXT_PUBLIC_DEEK_BRAND_NAME=${NEXT_PUBLIC_DEEK_BRAND_NAME}

# Brand overlay — when a brand-specific public/brand/<lowercase>/
# directory exists, copy its files OVER the defaults in /app/public/
# before next build. This lets Rex (and any future brand) ship its
# own icons + manifest without conditional logic in layout.tsx.
# When the brand is Deek (default) or has no brand subdir, this is
# a no-op.
RUN brand_lower=$(echo "$NEXT_PUBLIC_DEEK_BRAND_NAME" | tr '[:upper:]' '[:lower:]') && \
    if [ -d "/app/public/brand/$brand_lower" ] && [ "$brand_lower" != "deek" ]; then \
      echo "[brand-overlay] applying $brand_lower" && \
      cp -fv /app/public/brand/$brand_lower/* /app/public/ ; \
    else \
      echo "[brand-overlay] no overlay (brand=$brand_lower)" ; \
    fi

RUN npm run build

# ── Runner stage ──────────────────────────────────────────────────────

FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# Non-root user for basic hardening. node:20-alpine already has the
# `node` user (uid 1000).
RUN chown -R node:node /app
USER node

# The standalone output includes a self-contained server.js plus a
# pruned node_modules tree. Static assets are NOT in standalone so they
# must be copied separately.
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public
COPY --from=builder --chown=node:node /app/.build_commit ./.build_commit

EXPOSE 3000
CMD ["node", "server.js"]
