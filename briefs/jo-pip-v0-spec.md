# Jo's Pip — v0 Specification

**Document type:** Implementation spec (v0)
**Status:** Draft, awaiting Toby's go-ahead
**Date:** April 2026
**Relationship to v1 Pip spec:** This is a deliberately scoped subset of `pip-product-spec-v0.3.md`. v0 ships now; v1 lands later; v0 → v1 migration path is explicit.

---

## 1. Why v0

Jo has asked for a confidential personal AI for HR + finance work. The full Pip v1 spec (`pip-product-spec-v0.3.md`) is 10-11 months of engineering. Jo's request is real, deserves a real answer, and provides the best possible forcing function for v1 design. v0 ships Jo a usable product in days, not months, on architectural rails that converge with v1.

The bet: **building one real instance for one real user teaches us more than six more weeks of v1 spec refinement**.

## 2. What v0 is

A single-tenant Deek deployment on nbne1 (192.168.1.228), accessible only from inside the NBNE network and via Tailscale to Jo's personal devices. The instance is hers — her HR + finance context, her conversations, her memory.

**Architecturally, v0 is a Deek instance with a different config and a different DB.** No Pip-specific code is added to the platform. The "Pip" in "Jo's Pip v0" is a deployment + branding choice, not a separate codebase.

## 3. What's in scope for v0

- New Deek deployment on nbne1, separate Postgres database (`jo_deek`), separate API URL (`jo.nbne.local` or similar), separate Telegram bot
- Jo's role-specific briefs (hr_pulse, finance_check, d2c_observation, open_ended) migrate from NBNE-Deek-side to Jo-Pip-side
- Telegram channel for Jo's daily brief delivery — same pattern as Toby's, with her own bot token + chat-id pairing
- Authentication: Tailscale identity (Jo's device) for web access; password fallback for emergencies
- Data isolation by deployment: Jo's instance cannot read NBNE's organisational Deek (no cross-DB connection); NBNE's Deek cannot read Jo's instance
- Voluntary share-back-to-NBNE-Deek: Jo can copy specific items into NBNE-Deek if she chooses (manual at v0 — no programmatic affordance yet)
- Standard Deek tools: chat, search_wiki, write_wiki, retrieve_similar_decisions, memory write paths
- Daily Telegram brief delivery (her four role-specific questions) — already shipped, just routes to her instance instead of NBNE's
- Postgres backups via the existing nbne1 backup pipeline

## 4. What's NOT in scope for v0 (deferred to v1)

Explicit out-of-scope list so v0 doesn't drift into v1 by accident:

- **PMF export** — Jo's data is dumpable via `pg_dump` on request. PMF format ships in v1, at which point her existing data gets a one-time PMF export run.
- **USB key as data carrier** — Jo accesses her Pip from her work PC + phone via Tailscale. USB key model is v1.
- **Strict mode** — v0 runs as today's Deek behaviour (memory-write paths the same as ours). When v1 lands the strict/adaptive split, Jo gets the option then.
- **Memory-write discipline gating** — same.
- **Per-Pip impressions layer** — v0 uses today's retrieval (BM25 + pgvector hybrid). Impressions ships in v1.
- **PMF reference implementation** — v1 deliverable. Doesn't gate Jo's v0.
- **Multi-tenant Postgres schema** — v0 is single-tenant by deployment. The schema-pinning work in v1 is for *additional* Pip users; Jo's deployment doesn't need it.
- **Departure flow** — Jo isn't leaving. v1 specifies the flow; Jo's v0 doesn't need it.
- **Per-user key cryptography** — v0 auth is Tailscale identity + password. Key-based identity comes in v1 alongside PMF.

## 5. Migration path from v0 → v1

This is the part that has to be right at v0 design time or we pay for it later.

**Data:** Jo's content lives in `jo_deek` Postgres database. v1 introduces per-Pip schemas in a shared cluster. Migration: dump `jo_deek` to a per-schema migration script, restore into `pip_<jo_uuid>` schema in the new cluster. Standard Postgres operation, ~30 min downtime for Jo on the day.

**Identity:** v0 has no key-based identity. v1 introduces it. Migration: Jo generates a key pair at v1 onboarding, the public key gets associated with her existing schema, all subsequent PMF exports are signed under that key. Pre-v1 conversations have no signature but remain valid (trusted because they were created in her own deployment).

**Mode:** v0 has no strict/adaptive split. v1 introduces it. Migration: Jo chooses a mode at the v1 onboarding step. Existing memories are tagged `source=v0_pre_mode_split` so they don't masquerade as either explicitly-confirmed or inferred.

**PMF export:** at v1 release, an automated PMF export runs once for Jo's existing content as a baseline.

**Telegram:** unchanged. v0's bot pairing carries forward.

**The migration commitment:** Jo's accumulated content from v0 is preserved in v1, not lost or "started fresh." This is a non-negotiable promise made at v0 sign-up.

## 6. Architecture

### 6.1 Deployment topology

```
nbne1 (192.168.1.228)
├── Existing services (Phloe demos, etc.)
├── jo-pip-api      :8770    Deek API, configured for Jo
├── jo-pip-db       :5436    Postgres (jo_deek database)
└── nginx vhost     jo.nbne.local → jo-pip-api

Reachable from:
- NBNE office network (LAN)
- Jo's devices via Tailscale (laptop, phone)
- NOT reachable from public internet
```

### 6.2 Service isolation

- Separate `docker-compose` stack at `/opt/nbne/jo-pip/`
- Separate Postgres container; separate volume; separate backup destination
- No shared DB credentials with NBNE-Deek
- Separate API key (`JO_PIP_API_KEY`) for Telegram + chat auth — no overlap with `DEEK_API_KEY`
- Separate Telegram bot token (Jo creates her own bot via @BotFather)

### 6.3 Network isolation

- nginx config on nbne1 binds `jo.nbne.local` to the local network only (no port forward, no public DNS)
- Tailscale ACLs restrict Jo's Pip to: Jo's devices + Toby's devices (admin) — no other users on the tailnet
- Tailscale identity is checked at the nginx layer; password fallback for emergencies (USB key model not in v0)

### 6.4 Data isolation

The cleanest test: if NBNE's Deek were compromised tomorrow, Jo's content remains private. v0 enforces this by:

- Different Postgres instance (different process, different port, different volume)
- Different database credentials (no shared user)
- No application-level connection between the two deployments
- Toby has admin SSH access to nbne1 (for ops) but committed not to read Jo's DB content without her consent — same trust commitment that exists for any HR/finance system today

**This is policy + topology, not cryptography.** v1 introduces per-Pip key-based encryption that makes the isolation cryptographic. v0's commitment is operational + contractual.

### 6.5 Telegram delivery

Jo creates her own bot via @BotFather. Token + webhook secret stored in `jo-pip-api`'s env. The same Telegram nudge + brief code from `core/channels/nudge.py` and `core/brief/telegram_delivery.py` runs unchanged — these are tenant-agnostic. Jo pairs her Telegram chat using the existing `scripts/telegram_join_code.py` flow.

Daily brief cron lives on nbne1's crontab pointing at `jo-pip-api`. Existing role-specific brief code in `core/brief/questions.py` already supports per-user category overrides — Jo's profile is already configured (PR #54).

## 7. Implementation plan

Order of work. Rough sizing.

1. **Capacity check on nbne1** — confirm there's room for another Postgres + API container. ~30 min.
2. **Provision the deployment** — Docker compose stack, ports, volumes, nginx vhost, Tailscale ACLs. ~1 day.
3. **Bootstrap database** — apply the same migrations as Deek (0001-0016) against `jo_deek`. Empty memory + chunk tables. ~1 hour.
4. **Configure Jo's project profile** — `projects/jo/config.json`, `projects/jo/identity.md`, copy + adapt from Toby's. ~2 hours.
5. **Telegram bot pairing** — Jo creates her bot via @BotFather, sends Toby the token, we wire it into env. ~30 min total when Jo's available.
6. **Migrate role-specific brief from NBNE-Deek** — disable Jo's daily cron on the NBNE side, enable equivalent cron on jo-pip side, point at her Postgres. ~1 hour.
7. **First brief sent** — verify she receives it, replies, reply parses correctly. ~1 hour live.
8. **Onboarding conversation** — informal v0 version of v1's structured interview. We sit with Jo, Deek (her instance) asks her some role-context questions, baseline memory accumulates. ~1 hour.
9. **Quiet period** — Jo uses it for ~2 weeks. We watch what works + what's confusing. No code changes unless she's blocked.
10. **Retrospective** — gather learnings; feed into v1 spec v0.4.

**Total active engineering: 2-3 days.** Plus 1 hour of Jo's time for bot pairing + onboarding, plus 2-week passive observation.

## 8. Risks + mitigations

| Risk | Mitigation |
|---|---|
| nbne1 capacity tight | Capacity check first (step 1). If tight, defer or move to Hetzner with same isolation properties. |
| v0 ossifies into permanent shape | Explicit v0→v1 migration path documented now (§5). v0 is dated; v1 has a target month. |
| Jo's data accidentally bleeds into NBNE-Deek | Separate DB instance + separate credentials + no application-level link. Topology-enforced, not policy-only. |
| "It works for Jo, why bother with v1?" | Jo gives feedback that informs v1; v1 also serves Ivan + future Brian customers. v0 never scales beyond Jo. |
| Jo's content lost in v0→v1 migration | The migration commitment in §5 is non-negotiable. Test the migration in dev before live. |
| Jo wants features v0 doesn't have | Documented out-of-scope list (§4) is shared with her. Anything she wants that's not in scope becomes a v1 feedback item. |

## 9. Decisions — all locked

Toby confirmed 2026-04-27:

| Decision | Lock |
|---|---|
| Hostname | `jo.nbne.local` |
| USB key in v0 | No (deferred to v1) |
| Mobile interface | First-class concern — see `jo-pip-mobile-design.md` |
| Tailscale ACL | **Jo only.** Toby retains SSH access to nbne1 for ops; admin happens at the box level, not via Tailscale-bridged Pip URL. |
| Telegram bot name | **Rex.** Jo's name for her instance — distinct from "Pip" (the product) and "Deek" (the org), exactly the brand-vs-instance distinction from v0.3 §2. |
| Brief migration timing | Hard cutover. No parallel run. Disable her NBNE-Deek-side brief cron the same moment we enable Rex's. |
| Existing brief replies | Migrate to Rex with `source=migrated_from_nbne_deek` provenance. Originals stay in NBNE-Deek as audit trail. |
| Morning brief surface | Telegram **and** PWA. See §10. |

## 10. PWA scope upgrade (NEW — Toby 2026-04-27)

Toby flagged that the morning brief should also live inside the PWA on Jo's phone. This makes the PWA a **v0 deliverable**, not v0.5 as originally scoped. The change:

**Before (v0 = Telegram-only, PWA = v0.5):**
- Daily brief lands in Telegram only
- PWA waits for week 3 of observation

**After (v0 = Telegram + minimal PWA):**
- Daily brief delivered via Telegram (push notification — phone buzzes, she taps and sees it)
- Same brief also surfaces inside Rex's PWA when Jo opens it (visible at top of screen if unanswered)
- Replies via either surface land in the same `memory_brief_responses` row — the brief is per-day, not per-channel
- Both paths share state: reply via Telegram → PWA shows it as answered. Reply via PWA → Telegram thread shows the confirmation Pip sends back.

**v0 PWA minimum feature set:**

1. Today's brief at the top (if unanswered)
2. Inline reply box for the brief
3. Recent Telegram-thread chat history (read-only, sourced from Pip's own DB)
4. Memory search ("what did I say about X?")
5. Recent memory write events (chronological list, latest at top)
6. Lock-emoji header banner: `🔒 Rex — jo.nbne.local`

**v0.5 PWA features (still deferred):**

- Bulk memory delete with multi-select
- Memory audit organised by topic + role-tag
- Settings panel (notification preferences, quiet hours, API budget visibility)
- Share-to-NBNE-Deek activity log with full provenance
- Mode switch UI (when v1 lands strict/adaptive)
- PMF export button (v1)

**Implementation note:** the existing `/voice` PWA codebase has the components needed (chat surface, message list, voice input). The work is configuration + theming + brief-surface integration, not a new codebase. Estimate adds **2 days** to v0 active engineering (4-5 days total instead of 2-3).

## 10. Success criteria

- Jo uses her Pip daily for 2 weeks without prompting
- She receives + responds to her morning brief in Telegram
- She articulates, unprompted, that her conversations are private
- No data flow occurs from her Pip to NBNE-Deek without her explicit per-item action
- The deployment runs without intervention for 2 weeks (no manual restarts, no DB issues)
- A v1 spec v0.4 is produced incorporating learnings from those 2 weeks

## 11. The principle

Jo asked for something specific. The right response is to deliver it specifically. The right architecture is the smallest one that delivers, with explicit hooks for the larger architecture to come.

v0 is not v1 in disguise. v0 is the deliberately-scoped first step that proves the concept and informs the design.

The code stays in Northumberland. The standard is open. Jo's data is hers.
