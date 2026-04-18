# DEEK VOICE & AMBIENT INTERFACE — v2 BUILD BRIEF

**Supersedes:** original "TWO-PHASE BUILD BRIEF"
**Date:** 2026-04-18
**Context at time of writing:** Deek hybrid architecture is live. Hetzner serves the public API. deek-gpu (RTX 3090, Tailscale-tunnelled) handles local inference. Qwen 2.5 7B Instruct is loaded and running at ~4s/turn for classification. faster-whisper is installed on deek-gpu (not yet wired). No PWA built yet.

---

## What we are building

A human interface layer for Deek — how staff speak to it, how it speaks back, and how it maintains a passive ambient presence in the building.

Interaction model: closer to HAL 9000 than a chatbot. You don't open an app. You speak, or glance at a screen. Deek knows what's happening in the business and responds to natural-language questions from anyone on the team.

Three phases:

- **Phase 0** — **Backend data contracts.** Define and build the endpoints the PWA will consume. Nothing user-facing.
- **Phase 1** — **Mobile PWA.** Prove the UX before buying hardware.
- **Phase 2** — **Pi hardware appliances.** Physical always-on units.

A prior version of this brief had no Phase 0. Building the PWA against speculative endpoints that didn't exist would have blown the budget. Phase 0 is half a day of work and derisks everything after it.

---

## Phase 0 — Backend data contracts

### Goal

Every piece of data the Phase 1 PWA displays or speaks about has a concrete API endpoint returning a concrete schema. The PWA team (or agent) never has to ask "where does this data come from".

### Endpoints to build

#### 1. `GET /api/deek/morning-number?location=workshop|office|home`

One headline metric per location. The single most important number for someone arriving at that station.

Response:
```json
{
  "number": "14",
  "unit": "units",
  "headline": "14 units to make today",
  "subtitle": "£2,400 across 7 orders",
  "trend": "up" | "down" | "flat" | null,
  "as_of": "2026-04-18T09:14:22Z",
  "source_module": "manufacture",
  "stale": false
}
```

Location-specific logic:

| Location | Headline source | Subtitle source |
|---|---|---|
| `workshop` | Manufacture snapshot — open orders needing production | Total £ value of those orders |
| `office` | CRM snapshot — follow-ups due today | Pipeline £ value |
| `home` | Ledger snapshot — cash position | Open AR (money owed to NBNE) |

Implementation: pulls from `claw_code_chunks` where `chunk_type='module_snapshot'` and `file_path='snapshots/{module}.md'`, then extracts the headline via a small regex/parser per module. No new data gathering — reuse existing federation snapshots.

If the relevant module snapshot is missing or >2 hours stale, `stale: true` and `trend: null`.

#### 2. `GET /api/deek/ambient?location=X`

Full payload for the ambient view. Called on load + every 60 seconds.

Response:
```json
{
  "location": "workshop",
  "morning_number": { /* same shape as above */ },
  "panels": [
    {
      "id": "machine_status",
      "title": "Machines",
      "items": [
        {"label": "ROLF", "status": "available", "detail": null},
        {"label": "MIMAKI", "status": "running", "detail": "M-2119 (45 min left)"},
        {"label": "MUTOH", "status": "available", "detail": null}
      ]
    },
    {
      "id": "make_list",
      "title": "Top 5 to make",
      "items": [
        {"label": "M-2125", "status": null, "detail": "Aynsley Planning — 4 plates"},
        ...
      ]
    }
  ],
  "recent_recommendation": {
    "text": "Reorder DONALD — 6 days cover left",
    "created_at": "2026-04-18T08:02:11Z",
    "dissent": "none" | "amber" | "red"
  },
  "generated_at": "2026-04-18T09:14:22Z"
}
```

Panels per location:

| Location | Panels |
|---|---|
| `workshop` | machine_status, make_list |
| `office` | inbox_triage, crm_followups |
| `home` | morning_briefing, calendar, business_health |

The panel content is derived from the existing module snapshots plus email_triage table (for inbox_triage panel). No new data gathering.

#### 3. `POST /api/deek/tasks` + `GET /api/deek/tasks?assignee=X`

Voice-captured ad-hoc notes and task items. This is where "add a note to Ben's queue: recheck DONALD stock" lands.

**Not a replacement for CRM follow-ups** — those are project-scoped. This is for the "hey Deek, remind me to…" use case.

New table `deek_tasks`:
```sql
CREATE TABLE IF NOT EXISTS deek_tasks (
    id SERIAL PRIMARY KEY,
    assignee VARCHAR(100) NOT NULL,
    content TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'open',  -- open, done, cancelled
    source VARCHAR(20) NOT NULL DEFAULT 'voice', -- voice, web, api
    location VARCHAR(20),                         -- workshop, office, home
    created_by VARCHAR(100),                      -- user who added it
    created_at TIMESTAMPTZ DEFAULT NOW(),
    due_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);
```

`POST /api/deek/tasks`:
```json
{
  "assignee": "ben",
  "content": "recheck DONALD stock",
  "source": "voice",
  "location": "workshop",
  "created_by": "toby",
  "due_at": null
}
```
Returns the created task with its ID.

`GET /api/deek/tasks?assignee=ben&status=open`:
```json
{
  "tasks": [
    {"id": 42, "content": "recheck DONALD stock", "created_at": "...", ...}
  ]
}
```

`PATCH /api/deek/tasks/{id}`:
```json
{"status": "done"}
```

### Acceptance

- `curl https://deek.nbnesigns.co.uk/api/deek/morning-number?location=workshop -H "X-API-Key: ..."` returns a valid JSON object with all required fields
- Same for `/ambient` for all three locations
- `POST` a task, `GET` it back, `PATCH` it to done, `GET` filtered by status works
- Each endpoint responds in <500ms (local query, no LLM calls)
- Staleness flag correctly flips to `true` when module snapshot is >2h old

---

## Phase 1 — Mobile-first PWA

### Goal

Any staff member on NBNE WiFi opens a PWA on their phone, sees the ambient view for their location, can press-to-talk and ask Deek a question, and gets a spoken + displayed response within 5 seconds. This is the interface spec the Phase 2 Pi hardware will replicate.

### Stack — revised

- **Next.js 14 PWA**, hosted on Hetzner alongside the existing web-business frontend
- **Web Speech API** for voice input (browser-native, zero backend)
- **SpeechSynthesis API** for voice output (browser-native, zero backend)
- **HTTP POST** to `/api/deek/chat/voice` for the LLM turn (no WebSocket in Phase 1)
- **Service worker** for offline ambient view (cached last-good morning_number)

**Dropped from original stack:**
- Whisper on backend → not needed for Phase 1, browser STT is fine
- Piper TTS on backend → same, browser TTS is fine
- WebSocket voice gateway → press-to-talk doesn't need streaming

These come back in Phase 1.5 for the Pi units (browsers on Raspberry Pi Chromium have weaker Web Speech support and no offline option).

### Authentication

Mandatory in Phase 1. No anonymous access.

- PWA gated by NextAuth against the CRM user table (same auth boundary as `crm.nbnesigns.co.uk`)
- Session cookie scoped to `*.nbnesigns.co.uk`
- First open: login screen. Saves session for 30 days.
- Location picker on first use ("Where are you — workshop, office, or home?") saved in localStorage
- API calls include bearer token from session

Without this, anyone on NBNE WiFi can ask Deek for the cash position. Not acceptable.

### Voice routing — model choice matters

Voice queries MUST route to `qwen2.5:7b-instruct` (already loaded, always-warm — 4-5s end-to-end).

NOT `qwen2.5-coder:32b`: first call after VRAM swap is 33 seconds. User experience is dead.

NOT `gemma4:e4b`: competes for VRAM with the coder model, swap penalty on every call.

Configure via `OLLAMA_VOICE_MODEL=qwen2.5:7b-instruct` env var; add a route flag in `/api/deek/chat/voice` that forces `tier=1` and `model=qwen2.5:7b-instruct`.

### Core screens

**Ambient view (default on open):**
- Clock + date (local, no API)
- Morning number panel
- Location-specific panels (from `/api/deek/ambient`)
- Last Deek recommendation with dissent colour
- "Tap to talk" button, bottom

**Conversation view:**
- Press-to-talk (large thumb target)
- Transcript
- Text + TTS responses
- Location badge top-right (editable)

### Queries Deek should handle — all verified working today

- "What should we make today?" → reads manufacture snapshot ✓
- "Any urgent emails?" → reads email_triage table ✓
- "How are we doing this month?" → reads ledger snapshot (when online) ✓
- "What's the status of the Miter Industrial job?" → `search_crm` ✓
- "Add a note to Ben's queue: recheck DONALD stock" → `POST /api/deek/tasks` (Phase 0)
- "What's the weather in Alnwick?" → LLM general knowledge (returns stale answer — acknowledge limitation)

### Acceptance criteria — including failure modes

**Happy path:**
- Press-to-talk, ask "what should we make today", get spoken response within 5s
- Response draws from live Manufacture module context
- Session persists during a conversation
- Works on iOS Safari and Android Chrome

**Failure modes:**
- Deek unreachable → PWA shows "Deek offline" + last-cached morning number + "tap to retry". No infinite spinners.
- Web Speech returns garbage or empty → PWA surfaces "Sorry, didn't catch that — tap to try again". Never silently sends empty to backend.
- Deek returns error → PWA shows the error text + "tap to retry" (don't hide failures).
- User asks a question Deek can't answer → Deek explicitly says "I don't know" (existing system prompt handles this).
- Two people speak at once in workshop → whichever starts first is captured; the other gets "listening" feedback and retries.

### Cost budget

Inherit the existing `DEEK_TRIAGE_DAILY_LIMIT` pattern:

```
DEEK_VOICE_DAILY_LIMIT=200  # queries per UTC day
DEEK_VOICE_DAILY_COST_GBP=0.50  # hard stop at this spend
```

Tripped budget → PWA shows "Deek is thinking less today — try again tomorrow", defaulting to stored wiki answers only.

Defends against jammed wake-word detectors, bugs, and curious staff.

### Privacy

- Audio is NEVER stored. STT runs in the browser; only the transcribed text hits the backend.
- Transcripts ARE stored (same retention as chat sessions — 30 days active, then archived).
- `deek_voice_session` records: user, location, transcript, response, model_used, cost, latency. Logged for telemetry.
- No biometric voiceprint in Phase 1. Identity comes from the authenticated session.

### Telemetry

Every voice interaction logs:
- `user_id`, `location`, `session_id`, `question_len_chars`, `response_len_chars`
- `model_used`, `cost_usd`, `latency_ms`, `outcome` (success | stt_empty | backend_error | budget_trip)

Dashboard lives at `/admin/voice-metrics` (Phase 1.5).

---

## Phase 1.5 — Backend voice services (before Pi hardware)

On deek-gpu (already on tailnet):

### Whisper STT service

Tiny FastAPI wrapper around `faster-whisper` (already installed):
- Port 9000 on deek-gpu, bound to tailnet IP only
- `POST /stt` accepts audio blob, returns transcription JSON
- Model: `large-v3`, FP16, running on CUDA
- Expected latency: ~200ms for a 5-second utterance

### Piper TTS service

Similar tiny wrapper:
- Port 9001 on deek-gpu, bound to tailnet IP only
- `POST /tts` accepts text, returns WAV audio
- Voice: pick one quality British voice (`en_GB-alan-medium` is decent)
- Expected latency: <1s for a 20-word response

### Voice gateway service

On deek-gpu, port 9002:
- `POST /voice/turn` — accepts audio, returns audio response text + audio
- Pipeline: audio → Whisper → Deek `/chat` (over tailnet to Hetzner) → Piper → audio
- Forces `OLLAMA_VOICE_MODEL` so voice uses the always-warm 7B model
- Logs telemetry

Why on deek-gpu and not Hetzner: audio has to go somewhere for STT/TTS; running the gateway here avoids three WAN round-trips per utterance.

### Pi units join the tailnet

Each Pi runs Tailscale. Pi talks to deek-gpu's voice gateway directly over tailnet (LAN speeds). Deek-gpu talks to Hetzner for the LLM portion.

---

## Phase 2 — Pi hardware appliances

Unchanged from original brief except:

### Hardware per unit — confirmed
- Raspberry Pi 5 (8GB) preferred; Pi 4 prototype acceptable
- ReSpeaker 4-Mic Array (far-field)
- 7" Pi touchscreen
- Powered speaker or speaker HAT
- £200-250/unit

### Software on Pi
- Raspberry Pi OS Lite
- Tailscale (so Pi can reach deek-gpu's voice gateway directly)
- openWakeWord for wake word — **see note on wake word below**
- Audio capture → HTTPS POST to `https://deek-gpu.tailnet/voice/turn`
- Chromium kiosk mode displaying the Phase 1 PWA

### Wake word

"Hey Deek" is NOT in openWakeWord's prebuilt library. Options:

1. **Use "Hey Jarvis" for Phase 2** (prebuilt, works today). Feels fine in practice.
2. **Train a custom "Hey Deek" model** — 1-2 days of recording samples + training. Defer to Phase 2.1.

Recommend option 1 to unblock deployment. Brand-name wake word is a nice-to-have, not a blocker.

### Ambient display

Panels per location as specified in Phase 0 Ambient endpoint. Refreshes every 60 seconds while the unit is idle; pauses refresh during active conversation.

### Physical dissent indicator

RGB LED driven by a small Python script that polls `/api/deek/ambient` and reads `recent_recommendation.dissent`:
- Green: no unreviewed dissent
- Amber: unreviewed recommendation with module disagreement
- Red: urgent item requires attention

Per-Pi volume/mute button required — staff will mute during phone calls.

### Failure modes
- deek-gpu unreachable → Pi shows last-cached ambient + "Deek offline" banner. Wake word detection still runs but "Hey Deek" triggers a "Deek is currently offline" spoken response.
- Pi powers up during home internet outage → Pi can still reach deek-gpu via tailnet LAN.
- Both offline → ambient shows last-good data from local cache, age indicator visible.

---

## Session continuity across devices

Each Pi has a fixed device token and fixed `location_context`. Toby authenticated via PWA login carries a `user_id`. Voice sessions are keyed on `(user_id, location, date)`.

Question "tell me more about that" on a different device: the voice gateway queries the user's most recent session in the last 10 minutes regardless of location, and attaches it as context. Tested behaviour, not magic — if the window expires the device says "I don't have recent context — what do you want to know?".

---

## Build order

1. **Phase 0** (half a day): endpoints landed, deployed, curl-verifiable
2. **Phase 1** (1 week): PWA with voice, auth, error states, cost budget, telemetry
3. **Phase 1.5** (3-5 days): Whisper + Piper + voice gateway on deek-gpu, tested via curl
4. **Phase 2** (2 weeks): one Pi unit end-to-end (workshop), then replicate office + home
5. **Phase 2.1** (optional): train custom "Hey Deek" wake word

Stop and evaluate after Phase 1. The PWA alone might be enough.

---

*End of brief v2.*
