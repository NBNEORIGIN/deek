# CRM Brief — Quote generator with branded PDF + Xero push

**Target repo:** CRM (`D:\crm` / `NBNEORIGIN/crm`)
**Module:** CRM
**Consumer:** Claude Code (CRM session)
**Protocol:** Follow `NBNE_PROTOCOL.md`.
**Companion brief:** `briefs/deek-quote-intelligence.md` on the Deek
repo side. This brief ships the quote surface; that one layers
intelligence. Ship this one first; Deek's `/api/deek/quotes/*`
endpoints are consumed optionally — 404s degrade gracefully.

---

## Why this brief exists

Today a project has three fields on it — `quoteAmount`,
`quoteSentDate`, `quoteAcceptedDate`. That's it. No line items, no
PDF, no template, no versioning, no delivery workflow. Every quote
goes out from Toby's machine as a Word doc that's kept on disk in
the project folder. Replacing that with a structured, branded,
Xero-integrated flow is this brief.

**Adaptive format** is the headline requirement: a small job
(internal sign, £300, 3 line items) needs a one-page estimate;
a large job (£4k+ fit-out with survey + planning + install) needs
a multi-page quote with specs, lead-time, T&Cs, and optionally
case studies. The template logic picks the shape automatically
from line-item count + total value, with manual override.

---

## Pre-flight self-check

1. Read `CLAUDE.md` at the repo root for auth + deploy patterns.
2. Confirm the Prisma models touched: `Project`, `Client`,
   `ClientBusiness`, `Material`, `Supplier`, `LabourCost`. These
   are all read dependencies — no structural changes to them.
3. Confirm Postmark is already wired for outbound (Phloe bookings
   use it — the `POSTMARK_TOKEN` env var should already be set).
4. Confirm Xero OAuth is connected (the `xero-node` npm package
   should be in `package.json`). If not, that becomes Task 7.
5. The brand logo is at `D:\Google Drive\My Drive\002 NBNE ADMIN\056 WEBSITE\004 ASSETS\BW_LOGO.svg`
   — SVG wrapping an embedded 251×49 PNG. Usable as-is. Copy it
   into the repo at `public/brand/BW_LOGO.svg` and `src/brand/BW_LOGO_PNG.txt`
   (the base64 inside, for react-pdf's `<Image>` component which
   is easier with a data URL than an SVG).
6. Report findings before Task 1.

---

## Tasks

### Task 1 — Prisma models

Add three new models. Migration name
`2026_04_YY_quote_generator`.

```prisma
model Quote {
  id              String        @id @default(cuid())
  projectId       String        @map("project_id")
  project         Project       @relation(fields: [projectId], references: [id])
  quoteNumber     String        @unique @map("quote_number")   // e.g. Q-2026-00123
  status          QuoteStatus   @default(DRAFT)
  format          QuoteFormat   @default(SHORT)                // SHORT | LONG, auto-picked
  currentVersion  Int           @default(1) @map("current_version")

  // Snapshots captured at the moment of SENT so historical quotes are stable
  clientNameAtSend     String?   @map("client_name_at_send")
  clientEmailAtSend    String?   @map("client_email_at_send")
  clientAddressAtSend  String?   @map("client_address_at_send") @db.Text

  // Commercial summary (derived from line items — denormalised for fast listing)
  subtotalExVat   Decimal       @map("subtotal_ex_vat")     @db.Decimal(10, 2) @default(0)
  vatAmount       Decimal       @map("vat_amount")          @db.Decimal(10, 2) @default(0)
  totalIncVat     Decimal       @map("total_inc_vat")       @db.Decimal(10, 2) @default(0)
  leadTimeDays    Int?          @map("lead_time_days")
  validUntilDate  DateTime?     @map("valid_until_date")    @db.Date
  depositPercent  Int?          @map("deposit_percent")

  // Free-text sections (markdown). Scope / T&Cs are pulled from a template
  // but can be overridden per quote.
  scopeSummary    String?       @db.Text  @map("scope_summary")
  termsOverride   String?       @db.Text  @map("terms_override")
  coveringMessage String?       @db.Text  @map("covering_message")

  // Send / accept tracking
  sentAt          DateTime?     @map("sent_at")
  sentToEmail     String?       @map("sent_to_email")
  acceptedAt      DateTime?     @map("accepted_at")
  rejectedAt      DateTime?     @map("rejected_at")

  // Xero integration
  xeroQuoteId     String?       @map("xero_quote_id")
  xeroPushedAt    DateTime?     @map("xero_pushed_at")
  xeroPushError   String?       @db.Text @map("xero_push_error")

  createdById     String        @map("created_by_id")
  createdBy       User          @relation(fields: [createdById], references: [id])
  createdAt       DateTime      @default(now()) @map("created_at")
  updatedAt       DateTime      @updatedAt @map("updated_at")

  lineItems       QuoteLineItem[]
  versions        QuoteVersion[]

  @@index([projectId])
  @@index([status, createdAt])
  @@map("quotes")
}

enum QuoteStatus {
  DRAFT         // being edited
  SENT          // emailed to client
  ACCEPTED      // client accepted
  REJECTED      // client rejected
  EXPIRED       // past validUntilDate
  SUPERSEDED    // a newer version was sent
}

enum QuoteFormat {
  SHORT         // single-page estimate, auto-picked for simple jobs
  LONG          // multi-page formal quote
}

model QuoteLineItem {
  id              String   @id @default(cuid())
  quoteId         String   @map("quote_id")
  quote           Quote    @relation(fields: [quoteId], references: [id], onDelete: Cascade)

  displayOrder    Int      @map("display_order")
  category        String?  // 'materials' | 'labour' | 'install' | 'design' | etc. — freeform
  description     String   @db.Text
  quantity        Decimal  @db.Decimal(10, 3) @default(1)
  unit            String?  // 'ea' | 'm' | 'm2' | 'hr' | etc.
  unitPriceExVat  Decimal  @map("unit_price_ex_vat") @db.Decimal(10, 2)
  lineTotalExVat  Decimal  @map("line_total_ex_vat") @db.Decimal(10, 2)
  notes           String?  @db.Text

  // Optional traceability back to catalogue items so margin analysis
  // can aggregate across quotes
  materialId      String?  @map("material_id")
  material        Material? @relation(fields: [materialId], references: [id])

  createdAt       DateTime @default(now()) @map("created_at")

  @@index([quoteId, displayOrder])
  @@map("quote_line_items")
}

model QuoteVersion {
  id              String   @id @default(cuid())
  quoteId         String   @map("quote_id")
  quote           Quote    @relation(fields: [quoteId], references: [id], onDelete: Cascade)
  versionNumber   Int      @map("version_number")

  // Full snapshot of the quote at this version, stored as JSONB so
  // rendering an older version doesn't require re-hydrating relations
  snapshot        Json

  // Why this revision was created
  revisionNote    String?  @db.Text @map("revision_note")

  createdById     String   @map("created_by_id")
  createdBy       User     @relation(fields: [createdById], references: [id])
  createdAt       DateTime @default(now()) @map("created_at")

  @@unique([quoteId, versionNumber])
  @@index([quoteId, versionNumber])
  @@map("quote_versions")
}
```

Add back-references on `Project` (`quotes Quote[]`) and `Material`
(`quoteLineItems QuoteLineItem[]`).

`User` gets `createdQuotes Quote[]` and `createdQuoteVersions
QuoteVersion[]`.

### Task 2 — Quote template config

Create `config/quote-template.yaml`:

```yaml
# Branded quote template configuration. Non-code so staff can edit
# headers / footers / T&Cs without a deploy.

company:
  name: NBNE Signs
  address: |
    NBNE Signs Ltd
    <address line 1>
    <address line 2>
    Alnwick, Northumberland
    NE66 XXX
  phone: 01665 606741
  email: toby@nbnesigns.com
  web: nbnesigns.co.uk
  vat_number: GB XXXXXXXXX
  company_number: XXXXXXXX

branding:
  logo_path: brand/BW_LOGO.svg             # relative to public/
  primary_colour: "#1a1a1a"
  accent_colour:  "#5a5a5a"
  font_family: Helvetica

terms_default_markdown: |
  1. Quote valid for 30 days from issue unless otherwise stated.
  2. A 25% deposit is payable on acceptance; balance on completion.
  3. Prices exclude VAT unless otherwise stated.
  4. Lead times assume receipt of approved artwork + deposit.
  5. NBNE Signs retains copyright on all artwork unless transferred
     in writing.
  6. Installation access, power, and scaffold requirements to be
     confirmed prior to manufacture.
  7. See full terms at https://nbnesigns.co.uk/terms

format_rules:
  # SHORT chosen if ALL of these are true; else LONG.
  short_if:
    max_line_items: 6
    max_total_inc_vat: 1500
    requires_install: false
```

### Task 3 — PDF rendering (react-pdf)

Add `@react-pdf/renderer` to dependencies. Templates in
`src/quote-pdf/`:

```
src/quote-pdf/
  QuoteDocument.tsx      # top-level, picks SHORT vs LONG
  ShortQuote.tsx         # single-page estimate
  LongQuote.tsx          # multi-page formal quote
  components/
    Header.tsx           # logo + company block
    Footer.tsx           # page number + VAT/company info
    LineItemsTable.tsx   # the core table
    TermsSection.tsx
    CoveringLetter.tsx   # first page of LONG only
```

**Why react-pdf over Puppeteer**: lives in the Next.js build, no
chromium in the Docker image, no subprocess, templates diff cleanly.
Layout limits (no CSS grid, limited flex) are manageable for
business docs.

**API surface**:

```ts
export async function renderQuotePDF(quoteId: string): Promise<Buffer>
```

Pulls the quote + line items + project + client + template config,
chooses format, returns the PDF buffer. Called from the `/download`
endpoint and from the Postmark send path.

### Task 4 — API endpoints (`/api/cairn/quotes/*`)

All under the same Bearer-auth middleware as existing
`/api/cairn/*` endpoints.

```
POST    /api/cairn/projects/{id}/quotes
          Body: { lineItems: [...], scopeSummary, leadTimeDays,
                  validUntilDate, depositPercent, coveringMessage,
                  format?: 'SHORT' | 'LONG' | 'AUTO' }
          Creates a DRAFT quote against the project. quoteNumber
          is generated server-side as Q-YYYY-NNNNN (5-digit
          monotonic counter per year).

PATCH   /api/cairn/quotes/{id}
          Edits a DRAFT quote. Forbidden on SENT/ACCEPTED —
          those must clone-to-new-version via POST /{id}/versions.

POST    /api/cairn/quotes/{id}/versions
          Body: { revisionNote: string, changes: <partial quote> }
          Snapshots the current Quote into QuoteVersion at the
          CURRENT version number, then applies the changes and
          bumps currentVersion.

GET     /api/cairn/quotes/{id}
          Full quote with line items + version list.

GET     /api/cairn/quotes/{id}/versions/{n}
          Return a specific version's snapshot (for rendering
          historical PDFs).

GET     /api/cairn/quotes/{id}/pdf
          Renders + returns application/pdf bytes of the current
          version.

GET     /api/cairn/projects/{id}/quotes
          List all quotes on a project.

POST    /api/cairn/quotes/{id}/send
          Body: { toEmail, coveringMessage? }
          Renders PDF, sends via Postmark with PDF attached, sets
          status=SENT + sentAt. Logs Activity row.

POST    /api/cairn/quotes/{id}/accept
          Records client acceptance. Triggers Xero push (Task 6).
          Sets status=ACCEPTED + acceptedAt.

POST    /api/cairn/quotes/{id}/reject
          Body: { reason?: string }
          Sets status=REJECTED + rejectedAt.
```

Validation:
- Line item total = sum of lineTotalExVat; server recomputes
  rather than trusting client input
- VAT calculated at 20% (store rate on `Quote` so changes don't
  retro-break old quotes)
- `validUntilDate` defaults to `sentAt + 30 days`

### Task 5 — Admin UI (`/admin/projects/{id}/quote`)

Pages:

1. **List view** on the project detail page — shows all quotes for
   the project, their status, total, current version, sent date.
2. **Create / edit** page — line-item editor with:
     - Add-line / delete-line / drag-reorder
     - Optional Material-from-catalogue autocomplete (populates
       description + unitPriceExVat from the `Material` table)
     - Live-updating totals sidebar (subtotal, VAT, total)
     - Scope / covering message / T&Cs override tabs
     - Format selector (Auto / Short / Long) with rationale tooltip
     - **Preview button** — opens the rendered PDF in a new tab
     - Save-as-draft or Send
3. **Send modal** — shows the client's `clientEmail` + covering
   message editor; on confirm calls `/send` endpoint.
4. **Version history drawer** — list of past versions with their
   revision notes; clicking one opens that version's PDF.

Role-based access (all staff, per the brief):
- **View** — any authenticated user
- **Create / edit draft** — any authenticated user
- **Send** — users with `canSendQuotes` flag (a new column on
  `User` — default true for existing staff, gate new sign-ups
  behind it)
- **Accept / Reject on behalf of client** — same as Send

### Task 6 — Xero integration

On `POST /quotes/{id}/accept`, after recording acceptance, push
the quote to Xero as a **Quote** (not Invoice — Invoice creation
happens when the job is delivered; quote push creates the Xero
Quote record which can be converted to Invoice later from the
Xero UI).

Uses `xero-node`. Map:

| NBNE Quote field     | Xero Quote field |
|----------------------|------------------|
| quoteNumber          | Reference        |
| clientNameAtSend     | Contact (create if missing by email) |
| lineItems[]          | LineItems[]      |
| totalIncVat          | Total (recomputed Xero-side) |
| validUntilDate       | ExpiryDate       |
| coveringMessage      | Summary          |

Store the returned Xero `QuoteID` on `xeroQuoteId`. On failure
(network / auth), set `xeroPushError` to the error message — do
NOT block the acceptance flow. A cron can retry failed pushes
nightly.

### Task 7 — Delivery (Postmark)

Send the quote PDF as an attachment on `/quotes/{id}/send`.

```ts
postmark.sendEmailWithTemplate({
  From: 'toby@nbnesigns.com',          // or configurable per staff
  To: toEmail,
  Subject: `Quote ${quoteNumber} — ${project.name}`,
  TemplateAlias: 'quote-send',         // Postmark template
  TemplateModel: { ... },
  Attachments: [
    { Name: `${quoteNumber}.pdf`,
      Content: base64Pdf,
      ContentType: 'application/pdf' },
  ],
})
```

Postmark template `quote-send` renders the covering message. Set
`ReplyTo` to the sender's email so client replies don't go to
the shared mailbox.

### Task 8 — Activity logging

Every material action (created, edited, versioned, sent, accepted,
rejected, xero-pushed) writes an `Activity` row. Existing activity
feed on project detail surface already renders these.

### Task 9 — Tests

- Unit: quote-number generation (monotonic, yearly reset, no gaps)
- Unit: total recomputation correct (edge: 0 line items, negative
  quantities rejected, rounding at 2dp)
- Unit: template format picker (edge: exactly at threshold, manual
  override respected)
- Integration: create → edit → send → PDF renders → Postmark mock
  called → status=SENT → accept → Xero mock called → xeroQuoteId
  persisted
- Integration: version history — create v1 → send → POST /versions
  with changes → v2 active, v1 snapshot persists and renders same
  PDF bytes as original
- Regression: existing `/api/cairn/*` + CounterpartyRisk + Note
  endpoints unchanged

### Deliverable

Single PR with:
- migration
- models + endpoints + PDF templates
- admin UI pages
- Postmark + Xero integrations
- tests green
- updated `CLAUDE.md` / `CRM_FEATURES.md` documenting the quote
  surface

---

## Out of scope (follow-up briefs)

- **Deek intelligence consumption** — Deek ships
  `/api/deek/quotes/context`, `/quotes/similar`, `/quotes/review`
  (companion brief). The quote editor can call them once they're
  live to show "similar past quotes" + "margin sanity check" in
  the sidebar. Gracefully omit the sidebar on 404 until Deek
  deploys.
- **Client-facing accept-link** (client receives an email with a
  click-to-accept link → sets status without needing staff
  intervention). Worth doing but separate brief.
- **Quote-to-invoice conversion** when job is delivered.
- **Proposal-style output** (case studies, renders, project
  photography). Treat as future LongQuote extension; stub slot
  in `LongQuote.tsx`.

---

## Constraints

- No breaking changes to existing `/api/cairn/*` endpoints
- Every SENT quote is immutable — edits force a new version
- Quote numbers never reused even if rows are deleted
- Xero failures must not block user action; retry path separate
- Max 50 line items per quote (UI guard; DB doesn't enforce)
- PDF file size target <500KB — don't embed high-res images
- No new cloud dependencies (react-pdf is local; Postmark + Xero
  already in use)

---

## Rules of engagement

Stay in the CRM repo. Do NOT edit Deek, Phloe, or any other
module. If you want Deek intelligence endpoints to exist sooner,
stop and coordinate via a spanning brief — don't unilaterally add
them Deek-side. The Deek companion brief already exists at
`briefs/deek-quote-intelligence.md` in the Deek repo.
