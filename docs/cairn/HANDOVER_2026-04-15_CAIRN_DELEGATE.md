# Handover — cairn_delegate Tool (brief from 2026-04-15)

**From:** closeout of Beacon Phase 1 session, 2026-04-15
**For:** fresh CC session picking up the `cairn_delegate` brief
**Status:** Brief received, Step 1 recon done, no code written yet.
**Why fresh session:** Rule 3 — prior session was ~35 turns deep in Beacon-specific
context. This task is foundational, deserves a clean window.

The brief itself is the source of truth for requirements, acceptance, and decisions
D-A through D-F. This note is only the recon findings so the fresh session doesn't
redo the discovery I already did.

---

## Reusable infrastructure that already exists

### 1. Cairn MCP server is live and registered
- **File:** `D:\claw\mcp\cairn_mcp_server.py` (336 lines, 7 tools today)
- **Registration:** `~/.claude/settings.json` points CC at it with `CLAW_API_KEY` env.
- **Shape:** Tool defs in `TOOLS` list (with JSON-schema `inputSchema`), dispatch
  branches in `@server.call_tool()`. Every tool is a thin wrapper around a
  FastAPI endpoint under `http://localhost:8765` — MCP server does NO business
  logic, just forwards with `X-API-Key` header.
- **Implication for `cairn_delegate`:** just add one more `types.Tool` entry + one
  more `elif name == "cairn_delegate"` branch that POSTs to a new
  `/delegation/call` endpoint. Secrets stay server-side.
- **Existing tools there:** `retrieve_codebase_context`, `retrieve_chat_history`,
  `update_memory`, `list_projects`, `get_project_status`, `get_business_context`,
  `log_cost`.

### 2. OpenRouter is already in use — but via a heavy chat-interface client
- **File:** `D:\claw\core\models\openai_client.py` — full `OpenAIClient` with
  tool-calling, history, system prompts, image support. Used from `core/agent.py`
  at line 80 with `base_url=https://openrouter.ai/api/v1`.
- **Don't reuse this for delegation.** It's built for multi-turn agent loops
  with tool use. The brief's §0 step 4 explicitly asks for a thin httpx
  wrapper — do that instead. OpenAIClient is overkill and carries baggage
  (message_normaliser, caching, tool format translation) irrelevant to a
  one-shot delegation.
- **Other consumers** of `OPENROUTER_API_KEY`: `core/models/router.py:267`,
  `core/social/drafter.py:47`, `core/wiki/compiler.py:107`. None are appropriate
  to reuse for delegation.

### 3. A `router.py` exists already — but it's NOT what the brief wants
- **File:** `D:\claw\core\models\router.py` (~320 lines)
- **Purpose:** routes between Ollama / DeepSeek / Claude / OpenRouter-fallback
  inside Cairn's own agent loop using `TaskTier` + `ModelChoice` enums and
  hardware profile (`CAIRN_HARDWARE_PROFILE`).
- **Implication:** do NOT extend this for `cairn_delegate`. The existing
  router is about Cairn's internal agent orchestration. `cairn_delegate` is a
  new concern: external CC sessions delegating into Cairn. Keep the new
  routing logic in `delegation/` per D-A, with a single-line comment noting
  the distinction.

### 4. Cost logging infra exists — but the table schema is wrong for this
- **Endpoint:** `POST /costs/log` at `api/main.py:1698`, already exposed as
  the `log_cost` MCP tool.
- **Storage:** SQLite via `core.memory.store.MemoryStore.add_message()`
  (piggybacks on the conversation table with `role='assistant'` and a
  `[cost-log]` content prefix) + append-only `data/cost_log.csv`.
- **Schema mismatch:** The existing cost log is prompt-level — one row per
  prompt summary with a list of per-model costs. The brief's
  `cairn_delegation_log` is call-level — one row per `cairn_delegate()`
  invocation with `delegating_session`, `rationale`, `schema_valid`,
  `output_excerpt`, `outcome` enum. Different grain.
- **Recommendation for §0 step 3:** create `cairn_delegation_log` as a new
  table. Do NOT reuse the CSV or the SQLite conversation table. Rationale to
  log in D-log: call-level grain needed for the brief's §2 context-endpoint
  aggregations (per-module, per-model, schema-failure-rate). The existing
  `log_cost` tool stays; the brief's tool writes alongside, not instead.
- **Migration style:** Cairn uses raw SQL migrations — check
  `D:\claw\core\memory\` and `D:\claw\data\` for patterns. SQLite file is
  per-project under `CLAW_DATA_DIR` (default `./data`).

---

## Blockers / open questions the fresh session must resolve before §0 step 2

### A. `OPENROUTER_API_KEY` is not set in `D:\claw\.env`
- `grep -i openrouter D:\claw\.env` returns nothing. The `.env.example` also
  has no OR line.
- The 5 consumer files I listed above all `os.getenv('OPENROUTER_API_KEY', '')`
  and handle the empty case — so Cairn today effectively has no live
  OpenRouter path, just stubs.
- Toby's brief says "the api key is in the claw / cairn .env files" but it
  isn't. Either he's going to add it before you start, or the key lives in
  some other store (1Password, shell profile, Hetzner deploy env). Ask
  before §0 step 2.

### B. Verify pricing in the brief's `MODEL_PRICING` table
Brief says:
```
x-ai/grok-4-fast:           $0.20 input / $0.50 output per 1M
anthropic/claude-haiku-4.5: $1.00 input / $5.00 output per 1M
```
Verify against OpenRouter's model page before hardcoding. Grok pricing has
changed several times. Haiku 4.5 on OpenRouter may carry a markup vs
anthropic-direct. Use `WebFetch` against
`https://openrouter.ai/models/x-ai/grok-4-fast` and
`https://openrouter.ai/models/anthropic/claude-haiku-4.5` — do this in §0
before writing the `MODEL_PRICING` constant.

### C. Sovereignty claim in D-D needs a real check before wiki authoring
Brief D-D asserts: "Both Grok Fast and Haiku route through US/UK
infrastructure. Neither retains code for training under their terms."
- Haiku via OpenRouter → Anthropic TOS applies, generally no training on
  API traffic, but **verify the OpenRouter-specific TOS doesn't retain on
  the proxy side**.
- Grok Fast via OpenRouter → xAI TOS applies via OpenRouter's intermediary.
  xAI terms on training retention have changed; verify current.
- Don't write the wiki article in §3 copy-pasting D-D. Confirm from the
  live TOS pages and cite dates in the wiki.

### D. Cairn MCP server does NOT appear to be live in CC sessions today
- `~/.claude/settings.json` registers it, but the CC session I ran Beacon
  Phase 1 in did not see any `mcp__cairn__*` tools — I used `curl` against
  the Cairn FastAPI directly instead.
- Unclear whether the Python MCP server failed to start, or CC's tool-search
  is deferring it (cairn tools were NOT in the deferred-tools list at
  session start).
- **The fresh session will not be able to `call_tool` `cairn_delegate` at
  all unless the MCP server is actually connecting to CC.** Diagnose this
  before §1 acceptance testing. Start by running:
  ```
  D:\claw\.venv\Scripts\python.exe D:\claw\mcp\cairn_mcp_server.py
  ```
  manually and watching for errors. If it runs cleanly, the problem is in
  how CC is launching it (check CC logs).
- Alternative for §1 acceptance: call the new `/delegation/call` endpoint
  directly via `curl` to prove the backend works, and verify MCP exposure
  separately. Don't let the MCP issue block merging §1 code.

### E. The `cairn` project isn't registered inside Cairn itself
- `D:\claw\projects\` has `claw`, `phloe`, `render`, `beacon`, etc. — but no
  `cairn`. The `claw` project IS Cairn's own self-reference.
- The brief's memory write-backs use `project="cairn"`. Either:
  - Use `project="claw"` (existing convention, `claw` IS Cairn), OR
  - Register a new `cairn` project first
- **Recommend:** use `project="claw"`. The memory is already semantic-
  searchable across all projects; splitting cairn-vs-claw creates two
  retrieval surfaces for the same codebase.

---

## Concrete starter actions for the fresh session

The fresh session's STEP 1 retrieval is already done — these queries returned
useful context, so don't redo them, just ask for new ones if needed:
- `retrieve_memory("delegation tier hierarchy")` → n/a, empty in beacon; try
  `project="claw"`
- `retrieve_memory("openrouter")` → hits in `core/agent.py`, `router.py`,
  `social/drafter.py`, `wiki/compiler.py`
- `retrieve_memory("cost ledger")` → hits in `api/main.py:1698`,
  `core/social/cost.py`

Proposed order of first 5 calls in the fresh session:

1. Read this handover note.
2. Read `D:\claw\mcp\cairn_mcp_server.py` fully (it's the template for where
   the new tool plugs in).
3. Read `D:\claw\api\main.py` lines 1–120 (FastAPI app setup, route wiring
   convention) and 1698–1755 (existing cost log endpoint).
4. `WebFetch` the two OpenRouter model pages to verify pricing (blocker B
   above).
5. Ask Toby whether `OPENROUTER_API_KEY` is set somewhere non-obvious, or
   whether he'll add it to `D:\claw\.env` before §0 step 2 (blocker A
   above).

Then proceed into §0 per the brief.

---

## What I did NOT do (deliberate, because Rule 3)

- Did not write any code toward §0, §1, §2, §3.
- Did not commit or push anything in `D:\claw` for this brief.
- Did not update `MEMORY.md` or any memory store — the fresh session owns
  the D-log entries for this work so they're written in the right context.
- Did not touch `CLAUDE.md` Rule 1 (that's a §3 output).

## Delegation decision for this recon pass

Self — recon is a sequence of small targeted `grep`/`cat`/`ls` calls where
context cost per call is ~100 tokens. A sub-agent for this would burn more
tokens describing the task than the task costs to execute. Principal-tier
judgement was needed only once (deciding the openai_client is wrong to
reuse); that was one moment of thinking and doesn't warrant spinning up an
orchestration layer.

## Commits made in this recon pass

None. `D:\claw\git status` unchanged except for this new doc file, which
the fresh session can choose to commit or delete.

---

*Written by Opus 4.6 (1M), closing out the 2026-04-15 Beacon session before
it ran past turn 40. Start the next CC session fresh.*

---

# AMENDMENTS — 2026-04-15 diagnostic pass

Second Opus 4.6 pass, same day, walking blockers A–E to known-good state
before the fresh session picks up. Order worked: A, E (from Toby), then
D, B, C (from diagnosis). All amendments below supersede the original
handover body where they conflict.

## A — OPENROUTER_API_KEY (cleared by Toby)

Key is live in `D:\claw\.env` as `OPENROUTER_API_KEY=...` (single word —
no underscore between OPEN and ROUTER). Per-key restrictions and a credit
cap are set on the OpenRouter dashboard. **Do not read the value out, do
not echo it, do not put it in any committed file.** Original handover
body §A is wrong — key was already there, just not found by the grep
used in recon (likely due to env var name spelling assumption).

**Fresh-session action:** skip the blocker-A check. Proceed straight
into §0 step 2.

## E — Project name for memory write-backs: use `project="claw"`

Confirmed with Toby. Brief's `project="cairn"` was a mistake — `claw`
is the codebase, Cairn is the ecosystem brand; memory write-backs key
off codebase. `/projects` endpoint on Cairn API confirms:
- `claw` — `has_config: true, has_core_md: true, ready: true`
- `cairn` — `has_config: false, ready: false` (phantom entry, ignore)

**Fresh-session action:** when reading the brief, mentally replace every
`project="cairn"` with `project="claw"`. All D-log entries, retrieval
calls, and write-backs use `"claw"`. Do NOT register a `cairn` project.

## D — Cairn MCP server diagnosis: Outcome #1 (restart required)

**Verdict: server is healthy, CC just didn't pick it up this session.**

Diagnostic steps and results:
1. `~/.claude/settings.json` registers the server correctly under
   `mcpServers.cairn` with command `D:\claw\.venv\Scripts\python.exe`,
   args `["D:\claw\mcp\cairn_mcp_server.py"]`, env
   `CLAW_API_KEY=claw-dev-key-change-in-production`. No config change
   needed.
2. Cairn FastAPI backend is live: `GET http://localhost:8765/health`
   returned 200.
3. Venv Python exists, `import mcp; import httpx` both succeed.
4. Manual stdio handshake against the server (piped `initialize` +
   `notifications/initialized` + `tools/list` as JSON-RPC) returned a
   valid `protocolVersion: 2024-11-05` response with `serverInfo.name:
   "cairn"` and the full 7-tool list. Server speaks MCP correctly.

Why this session still had no `mcp__cairn__*` tools visible: CC's MCP
runtime didn't launch this server for the current conversation. Cause
is CC-side session state, not server-side code or config.

**Fresh-session action:** start the fresh CC session AFTER ensuring
Cairn API is running (it is — leave it alone). On session start, verify
`mcp__cairn__list_projects` appears in the tool list. If it does not,
the fix is a CC restart (close the window, reopen). No server code
change needed, no brief amendment needed. If after restart the tools
still don't appear, escalate to Toby — that would be outcome #2
(config) or #3 (server), neither of which this diagnostic found.

Interim fallback while MCP unavailable: call the Cairn FastAPI
endpoints directly via `curl` with `X-API-Key:
claw-dev-key-change-in-production` header. Same semantics, just no MCP
schema enforcement.

## B — MODEL_PRICING verified, no changes

WebFetch against OpenRouter model pages, 2026-04-15:
- `https://openrouter.ai/x-ai/grok-4-fast` → **$0.20 / 1M input, $0.50
  / 1M output**
- `https://openrouter.ai/anthropic/claude-haiku-4.5` → **$1.00 / 1M
  input, $5.00 / 1M output**

Both match the brief's §1 step 5 `MODEL_PRICING` constant exactly.
**No amendment needed** — hardcode as written in the brief.

## C — D-D sovereignty claim: sourced, with one caveat

Two fetches, 2026-04-15:

**OpenRouter** — `https://openrouter.ai/privacy` (Privacy Policy) +
`https://openrouter.ai/terms` (Terms of Service §5):
- Privacy Policy: `"We do not control, and are not responsible for,
  LLMs' handling of your Inputs or Outputs, including for use in their
  model training."` → OpenRouter itself defers training-data handling
  to the provider.
- ToS §5.1: `"Some AI Models may store or train on your Inputs for
  improving their own large language models and may allow you to
  opt-out of model training"` → provider-dependent.
- ToS §5.2: OpenRouter's own use of user content is **opt-in only**
  via account settings — default is no OR-side training use.
- ToS §5.3: Private input/output logging is a user-enabled feature
  only — default off.

**xAI** — Enterprise Terms of Service (via WebSearch; direct WebFetch
to `x.ai/legal/*` returns 403, bot-gated):
- `"xAI shall not use any User Content for any of its internal AI or
  other training purposes"` → Enterprise ToS, applies to API access.
- `"User Content is automatically deleted within 30 days"` unless
  legal/moderation flag. Response IDs retained 30 days for
  continuation.
- Zero Data Retention option exists (e.g., Grok Code Fast 1 in GitHub
  Copilot runs ZDR).
- xAI MAY create and use **de-identified** data for its own product
  development — this is a carve-out worth naming in the wiki article.

**Caveat for the wiki article (§3 output):** OpenRouter calls xAI as
the intermediary customer. The *Enterprise* ToS governs that
relationship, not the consumer-grade Terms bound to Grok chat on X.
State this distinction explicitly. The sovereignty claim is:
- Not trained on by OpenRouter (opt-in default-off).
- Not trained on by xAI for model improvement (Enterprise ToS).
- De-identified aggregates may persist on xAI side.
- Auto-deletion at 30 days unless flagged.

That is defensible as "not fed back into training," which is the D-D
claim that matters. It is NOT "nothing leaves the UK" — that is a
stronger claim the brief does not actually make. Do not overclaim in
the wiki.

**Sources to cite in the §3 wiki article** (with 2026-04-15 fetch
date):
- `https://openrouter.ai/privacy`
- `https://openrouter.ai/terms`
- xAI Enterprise ToS (`x.ai/legal/terms-of-service-enterprise`) — note
  in the wiki that the page is Cloudflare-gated against automated
  fetches; link is the authoritative reference for humans.

---

## Status at end of diagnostic pass

| Blocker | Status                                                         |
|---------|----------------------------------------------------------------|
| A       | Cleared. Key live in .env. Do not echo.                        |
| B       | Verified. No change to MODEL_PRICING.                          |
| C       | Sourced. Wiki article §3 has quotes + caveat above.            |
| D       | Outcome #1: restart CC. No brief amendment.                    |
| E       | Confirmed. Use `project="claw"` throughout.                    |

**Fresh session can proceed straight into §0 of the brief** after
restarting CC and verifying `mcp__cairn__*` tools appear.

*Diagnostic pass by Opus 4.6 (1M), 2026-04-15.*
