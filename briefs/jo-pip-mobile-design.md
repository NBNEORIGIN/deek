# Jo's Pip — Mobile Interface Design

**Document type:** UX + interaction design
**Status:** Draft, awaiting Toby's review
**Date:** April 2026
**Companion to:** `jo-pip-v0-spec.md` (deployment + boundaries)
**Purpose:** Design Jo's mobile experience deliberately, rather than pointing her phone at the existing `/voice` PWA and hoping.

---

## 1. The thinking that drives the design

Jo isn't a power user. Her relationship with her Pip will be built through dozens of small interactions per day, mostly on her phone, mostly between meetings or on the shop floor. The mobile experience is the experience.

**Three honest constraints we have to design around:**

1. **Tailscale on a personal phone is friction.** It's a VPN-feeling thing she has to install and trust. Power users tolerate it; staff resent it after week three when battery saver kills the connection silently.
2. **The /voice PWA was built for Toby.** It assumes voice-first interaction and complex chat. For Jo's daily HR/finance/D2C beats, it's overengineered.
3. **Telegram already works.** Daily brief delivery is shipped (PR #52). Inbound chat through her bot is shipped (PR #49 + extensions). A reply loop with the conversational normaliser is shipped (PR #41).

The conclusion that follows: **Telegram is the v0 mobile interface. Tailscale-bridged PWA is v0.5 for power-user tasks only.**

This isn't a compromise. It's recognising that the right primary surface for Jo's daily use is the chat thread she already has open on her phone for everything else.

---

## 2. The four mobile surfaces and which Jo gets when

| Surface | When Jo uses it | Status |
|---|---|---|
| **Telegram chat with Rex** | Daily — brief, ad-hoc memory captures, quick lookups, share-back. Push-notification advantage. | v0 day 1 |
| **PWA (Tailscale-bridged)** | Same brief surfaced inside the app + memory search + recent activity | v0 day 1 (minimal) |
| **PWA (full)** | Memory audit, bulk delete, settings, mode review, exports | v0.5 |
| **Voice (PWA `/voice`)** | Hands-free + complex back-and-forth | v0.5 |
| **Native mobile app** | Never (v2 of full Pip) | n/a |

**v0 ships rows 1 + 2 (Telegram + minimal PWA).** Both surfaces are co-primary because Toby flagged 2026-04-27 that the morning brief should be visible inside the PWA, not just in the Telegram thread. Reasoning: Telegram is best for push notifications + quick responses; PWA is best when she wants to think before replying or browse her own memory.

**Both surfaces share state.** Reply once via either channel → both show it as answered. There is one brief per day, not one-per-channel.

---

## 3. Telegram — the primary mobile interface

This is where 80%+ of Jo's daily interaction lives. Designing it deliberately, not as "the same bot Toby has."

### 3.1 Visual + identity differentiation

Jo's Pip should feel distinct from any other Telegram bot she has — and distinct from NBNE-Deek's surface — so she's never in doubt about which channel she's in.

- **Bot avatar:** distinct image (Jo picks). Not the NBNE logo. Not a generic AI avatar. Something that signals "this is mine."
- **Bot username:** Jo's choice, e.g. `JoNbneAssistantBot` (suggestion: avoid the word "Pip" so her relationship is with HER bot, not "the Pip product").
- **Standard footer on every Pip-initiated message:** `🔒 jo.nbne.local`. A small reminder that this conversation lives on the office server, only Jo can see it. After two weeks of seeing the marker, it becomes invisible — which is fine; it's there for the moments where it matters.
- **Standard prefix on the daily brief:** `🌅 Morning Jo —` (warm, by-name, distinct from any "system notification" feel).
- **Confidentiality reminder when she shares back to NBNE-Deek:** explicit confirmation message with preview before send. Discussed in §3.4.

### 3.2 What Jo can do via Telegram (v0 capabilities)

Concrete list, in order of frequency she'll actually do them:

- **Reply to the morning brief.** 4 questions (HR / finance / D2C / open). Plain prose. Conversational normaliser maps to the right Q. Already shipped.
- **Capture a memory.** Free-text or voice note. *"Remember that Becky in marketing has a child starting reception in September."* The bot stores it tagged `source=user_stated`, role inferred from content. No special command — just say it.
- **Quick lookup.** *"What did I say about supplier price changes last week?"* Bot retrieves + answers from her own memory only.
- **Share an item to NBNE-Deek.** *"Share that supplier note with the team."* Triggers the share-back confirmation flow (§3.4).
- **Ask about a project.** Bot reads from NBNE-Deek (read-only, per §9.2 of v1 spec) and answers.
- **Voice notes.** Telegram supports recording audio messages. The bot transcribes locally + treats as text input. Same pipeline as text.
- **Photos.** Photo attached to a Telegram message can include a caption ("invoice from supplier — note the wrong VAT rate"). Bot stores image + caption together.
- **Slash commands** — minimal:
    - `/help` — what she can do
    - `/projects` — quick search of NBNE projects (read-only)
    - `/audit` — link to PWA audit view (when v0.5 ships)
    - `/export` — placeholder for v1 PMF export
    - `/quiet` — disable notifications for the rest of today

### 3.3 What Jo CANNOT do via Telegram (and why)

These are deliberate v0 omissions, not gaps:

- **Bulk delete memory.** Telegram chat is wrong UI for "delete everything tagged X." Wait for PWA in v0.5.
- **Memory inspection across topics.** Same reason.
- **Switch strict/adaptive mode.** Comes with v1 anyway.
- **Browse historic conversations.** Telegram's native search covers this badly; better via PWA.

The principle: Telegram is for the active conversation. The PWA is for looking at the corpus. Don't try to make Telegram do both.

### 3.4 Share-back-to-NBNE-Deek flow

This is the most consent-sensitive interaction in v0 and it deserves a careful UI.

**Default:** nothing flows from Jo's Pip to NBNE-Deek without an explicit per-item confirmation step from Jo.

**Trigger:** Jo says *"share this with the team"* or *"add this to the project notes"* or taps a share button. (For v0: just the prose trigger; inline buttons can come later.)

**Confirmation flow (Telegram):**

```
Pip: Share-to-team preview:

      "Becky in marketing flagged that the new
       supplier's pricing schedule is missing
       VAT — needs clarification before we
       order again."

      Going to: NBNE-Deek > Operations recommendations
      Tagged: high priority, supplier-pricing
      Visible to: Toby, Ivan, anyone with NBNE-Deek access

      Reply "yes" to share, "no" to cancel,
      or rewrite the text and reply with your version.
```

Jo replies. If "yes," the share happens via existing `write_crm_memory` tool path. If "no," nothing leaves her Pip. If she rewrites, the new text goes through.

**Audit trail:** every share-back creates a row in `cairn_intel.share_events` (new table — to design) with the original Pip-side memory id, the NBNE-Deek destination, the timestamp, and Jo's confirmation message. She can view this list later via PWA.

### 3.5 Notification discipline

The morning brief is the only proactive Pip-to-Jo message in v0. **Nothing else.**

- No "you haven't checked in for X days"
- No "did you see the new Deek recommendation?"
- No marketing-style nudges
- No daily summary of what she did

If we want to nudge her about anything later (stalled HR follow-up, looming financial deadline), it's a per-category opt-in shipped after 2 weeks of observation.

The default state is: she sends, Pip responds. Only deviation is the daily brief.

---

## 4. The PWA — minimal in v0, expanded in v0.5

The PWA is a **v0 deliverable** (Toby 2026-04-27) — but only with the minimum feature set Jo needs the morning brief to live there. Everything richer waits for v0.5.

### 4.1 Connectivity model

Jo installs Tailscale on her phone (one-time setup, ~5 min with Toby). When she opens `jo.nbne.local` in mobile Safari/Chrome, Tailscale routes the request through her tailnet to nbne1.

**Reliability concern:** iOS Tailscale has historically had silent disconnects (battery saver, low memory). Mitigations:
- PWA detects "can't reach jo.nbne.local" and shows a clear "Tailscale not connected — open Tailscale app to reconnect" rather than spinning forever.
- Critical actions (memory deletion, exports — v0.5+) require Tailscale-authenticated session — so even if Tailscale flakes mid-action, no harmful operation happens against a half-authenticated request.
- **Telegram is always available as a fallback.** Even if Tailscale is broken on her phone, she can still respond to her brief and chat with Rex via Telegram. The morning brief lands in BOTH surfaces — if PWA is unreachable, Telegram still got the same brief through.

### 4.2 v0 PWA — minimum feature set

1. **Today's brief at the top.** If unanswered, the four questions are inline with reply boxes. If answered, it shows what she said (and lets her edit if it's still the same day).
2. **Reply box per question.** Plain prose; the conversational normaliser maps her wording to the right Q. Same backend as Telegram replies.
3. **Recent chat history.** Read-only view of her last ~50 conversation turns from the Telegram thread, rendered cleanly in the PWA. Continuity across surfaces.
4. **Memory search.** Single search box: *"what did I say about X?"* — returns matching memory chunks with source + date.
5. **Recent memory write events.** Chronological list of the last 30 things Rex has stored, latest first. So Jo can see what's been added without searching.
6. **Header banner: 🔒 Rex — jo.nbne.local.** Persistent confidentiality cue, same role as the Telegram footer.

### 4.3 v0.5 PWA features (still deferred)

1. **Memory audit view.** Browse what Rex remembers, organised by topic + role-tag.
2. **Bulk delete.** Multi-select + confirm + real deletion (chunks + embeddings + attached files).
3. **Settings panel.** API budget visible. Notification preferences. Quiet hours.
4. **Share-to-NBNE-Deek activity log** — every share-back with full provenance + source linking.
5. **Mode switch** — when v1 ships strict/adaptive.
6. **PMF export** — when v1 ships the format.

### 4.4 What the v0 PWA deliberately doesn't do

- **Doesn't replace Telegram as the chat surface.** PWA shows recent history but *replying* primarily happens in the place Jo thinks of as a chat — her Telegram thread. The PWA's chat history is for context + continuity, not for sustained back-and-forth.
- **Doesn't have voice in v0.** Voice deferred to v0.5 alongside the broader audit work.
- **Doesn't run when offline.** No PWA caching of memory data — always fresh from server. Sovereignty depends on the server being the only place data lives.

### 4.5 Visual design — minimal but distinct

Three concrete decisions:

- **Colour scheme:** muted, calm, not NBNE brand colours. Suggest soft sage / warm white background. Signals "personal" rather than "corporate."
- **Header:** `🔒 Rex — jo.nbne.local` persistent at top. Reinforces ownership + locality.
- **No NBNE branding in the chrome.** This is HER tool, not a corporate one. Avatar matches the Telegram bot avatar (Jo picks once, used in both places).

---

## 5. Voice — `/voice` extension or new path?

The existing `/voice` PWA is Toby's. Jo gets it for free if she points her browser at her instance, but the design choices baked in (large central HAL eye, voice-first, etc.) may not fit her use cases.

**v0 decision:** voice is unchanged in v0. Jo can use the existing `/voice` path via Tailscale on her phone if she wants. We watch what she does. If she uses it heavily, we redesign for her use case in v0.5 alongside the PWA work.

**Not breaking the existing /voice for Toby.** Jo's instance gets its own copy via the deployment-isolated codebase. Changes to her version don't affect his.

---

## 6. The first-conversation experience

Jo's first interaction with her Pip is the moment that defines the relationship. Designing it deliberately.

### 6.1 Onboarding sequence (v0)

Lighter than v1's structured baseline-memory interview (~30 min, 50-100 explicit memory entries), but still deliberate.

When Jo first messages her Telegram bot after pairing:

```
🌅 Morning Jo —

Welcome to your Pip. I'm yours, locally hosted at
jo.nbne.local, only you can see this conversation.

Toby's set me up with the basics: I know you handle HR,
finance, and D2C, and that you're based in Alnwick.
Anything I have wrong?

A few things worth knowing:

  • I store everything you tell me — you can ask me
    "what do you remember?" any time
  • I don't talk to anyone else's Pip and I don't write
    to NBNE-Deek without you saying so explicitly
  • You'll get 4 short questions every morning at 7:32 —
    answer in plain English, "nothing" is always valid
  • Type /help any time to see what I can do

What's something you'd like me to remember from today?
```

Jo replies. Whatever she says becomes her first explicit memory. The first-conversation success criterion is: **she sends ONE substantive reply.**

### 6.2 First-week prompts

Starting on day 2, the morning brief has a small extra section the first week only:

```
[End of normal brief]

P.S. while we're getting to know each other, anything
worth me knowing about: people you work with closely,
the projects most on your mind right now, things you
keep meaning to fix?
```

This is the gentlest possible structured-memory-interview equivalent. No forms. No lists. Just one extra question per day for 5 days, capturing baseline context. After day 7 the P.S. drops off.

### 6.3 Two-week retrospective (with Toby)

End of week 2, Toby + Jo sit together for ~20 min. She walks him through:
- Did the daily brief feel relevant or generic?
- Were the questions too HR/finance/D2C-specific or about right?
- Did she share anything to NBNE-Deek? Was the share flow clear?
- Did she try the PWA? Useful or noise?
- What does she wish her Pip could do that it can't?

This output feeds v0.5 design + v1 spec v0.4.

---

## 7. Risks specific to mobile

| Risk | Mitigation |
|---|---|
| Tailscale on her phone flakes | Telegram is always available — primary daily UI doesn't depend on Tailscale |
| Telegram bot rate-limited or banned by Telegram | Migration path: same chat code runs on WhatsApp Business or Signal CLI later if needed. v0 accepts this small dependency. |
| Voice transcription via Telegram fails | Falls back to text — Telegram supports both natively |
| Jo's phone runs out of storage / battery | Telegram + Tailscale together are <100MB. Daily use battery cost is negligible. |
| Jo loses her phone | No Pip data on her phone. All memory is on nbne1. New phone, install Telegram + Tailscale, sign in, continue. |
| Jo accidentally messages the wrong bot (NBNE-Deek vs her Pip) | Distinct avatars + footer on every Pip message. Worst case: a private message lands in NBNE-Deek context, which is the same scope as today's email triage so no new exposure. |
| Jo wants to use her Pip from outside NBNE network without Tailscale | v0 says no; v1 adds either USB carrier or hosted access. v0 → v1 migration path covers it. |

---

## 8. What success looks like

After 2 weeks of v0:

- Jo replies to her morning brief at least 5 days/week (via either Telegram or PWA — both count)
- She sends at least one ad-hoc memory or question per day on average
- She's used the share-back-to-NBNE-Deek flow at least once and didn't experience it as friction
- Tailscale is set up on her phone and reliably reaches `jo.nbne.local` — meaning the PWA + Telegram dual-surface model holds up in real use
- She can articulate, unprompted, that her conversations are private to her instance
- The conversational normaliser parses her replies with > 90% intent-correctness (verified by manual review of the first ~30 replies)
- Reply behaviour split between channels gives us a real signal about which surface she actually prefers

After 2 weeks Jo is using a tool she trusts. v0.5 PWA work (audit + bulk delete + settings + activity log) begins informed by what she said in the retro.

---

## 9. The principle

The mobile interface IS the product for Jo. Designing it as "the same as Toby's web chat with a phone-shaped browser" is a failure mode dressed as efficiency.

Telegram first, because it works and she already knows how to use it. PWA later, for the things Telegram can't do. Voice when she asks for it. Notifications minimum. Confidentiality cues persistent.

Trust is built through small consistent interactions over weeks. The design's job is to not get in the way of that.
