# Etsy Direct API Publish — Render Implementation Brief

---

Read PROTOCOL.md and this file completely before starting.
Pull memory for project "render" and "etsy-intelligence".

---

## Context

Render currently exports Etsy listings as an XLSX file (`export_etsy.py`) formatted
for the **Etsy Shop Uploader** third-party tool. The user downloads the XLSX, then
manually uploads it to Shop Uploader, which creates the listings on Etsy.

NBNE now has Etsy API v3 OAuth working in the Cairn codebase (`core/etsy_intel/`).
The OAuth flow is live at `cairn.nbnesigns.co.uk/etsy/oauth/` with `transactions_r`
and `shops_r` scopes. The next step is to add `listings_w` scope and build direct
API publishing in Render — eliminating Shop Uploader from the workflow entirely.

eBay already has direct API publishing (`ebay_api.py`). Follow the same pattern.

## The Goal

Replace the XLSX→Shop Uploader workflow with direct Etsy API calls:
**QA-approved product → one click → live draft listing on Etsy.**

Keep `export_etsy.py` as a fallback — don't delete it.

## What to Build

### 1. Etsy Auth Module (`etsy_auth.py`)

Mirror `ebay_auth.py` pattern. This module handles:
- OAuth 2.0 Authorization Code Grant with PKCE (Etsy requires it)
- Token storage in a local JSON file (`etsy_tokens.json`, gitignored)
- Auto-refresh when token expires (1hr access, 90-day refresh)
- Auth header construction: `x-api-key: {keystring}:{shared_secret}` + `Authorization: Bearer {token}`

**Etsy OAuth details:**
- Auth URL: `https://www.etsy.com/oauth/connect`
- Token URL: `https://api.etsy.com/v3/public/oauth/token`
- PKCE: Required. Code verifier = 43-128 chars from `[A-Za-z0-9._~-]`. Challenge = base64url(SHA256(verifier))
- Scopes needed: `listings_w listings_r transactions_r shops_r images:write`
- Redirect URI: register in Etsy app settings (can use `http://localhost:5000/etsy/oauth/callback` for dev)
- Token response: `{ "access_token": "userid.token...", "refresh_token": "...", "expires_in": 3600 }`
- Refresh: POST to token URL with `grant_type=refresh_token`, `client_id`, `refresh_token`
- Feb 2026 change: `x-api-key` header requires `keystring:shared_secret` (colon-separated), not just the keystring

**Environment variables** (add to `.env.example`):
```
ETSY_API_KEY=mcalbkdw9sd4xzwhnqv6a30p
ETSY_SHARED_SECRET=57sk14cuxp
ETSY_SHOP_ID=11706740
ETSY_REDIRECT_URI=http://localhost:5000/etsy/oauth/callback
```

### 2. Etsy API Client (`etsy_api.py`)

Mirror `ebay_api.py` structure. Key Etsy API v3 endpoints:

**Create a draft listing:**
```
POST /v3/application/shops/{shop_id}/listings
Content-Type: application/json
x-api-key: {keystring}:{shared_secret}
Authorization: Bearer {access_token}

{
  "title": "No Entry Sign – 110mm x 95mm Brushed Aluminium",
  "description": "...",
  "price": 11.99,
  "quantity": 999,
  "taxonomy_id": 2844,           // Signs category
  "who_made": "i_did",
  "when_made": "made_to_order",
  "is_supply": false,
  "shipping_profile_id": 208230423243,
  "return_policy_id": 1074420280634,
  "tags": ["sign", "aluminium", "safety sign", ...],  // max 13 tags, each max 20 chars
  "materials": ["brushed aluminium"],
  "type": "physical",
  "state": "draft",               // always create as draft for QA
  "is_taxable": true,
  "is_customizable": false,
  "is_personalizable": false
}
```

**Upload images to a listing:**
```
POST /v3/application/shops/{shop_id}/listings/{listing_id}/images
Content-Type: multipart/form-data

image: <binary file data>
rank: 1                           // 1-10, determines display order
overwrite: true
```

Images must be uploaded from the local server. Render generates them at
`/images/{m_number} - {NNN}.jpg`. Download from the public URL or read
from the local filesystem, then upload via multipart.

Image order: 001 (main), 002 (dimensions), 003 (peel-and-stick), 004 (rear), 006 (lifestyle)

**Create product variations (size/colour):**
Etsy uses the Inventory API for variations:
```
PUT /v3/application/listings/{listing_id}/inventory
{
  "products": [
    {
      "sku": "M1001",
      "property_values": [
        { "property_id": 100, "value_ids": [...], "values": ["110mm x 95mm"] },
        { "property_id": 200, "value_ids": [...], "values": ["Silver"] }
      ],
      "offerings": [
        { "price": 11.99, "quantity": 999, "is_enabled": true }
      ]
    }
  ],
  "price_on_property": [100],
  "quantity_on_property": [],
  "sku_on_property": [100, 200]
}
```

Note: property IDs for Size and Color vary by taxonomy. Look up the correct IDs
for taxonomy 2844 via `GET /v3/application/seller-taxonomy/nodes/{taxonomy_id}/properties`.

**Rate limiting:** 5 QPS, 5K QPD. Use a simple sleep/semaphore.

### 3. Flask Routes

Add to `app.py`:

```python
# ── Etsy OAuth ───────────────────────────────────────────────────────────
GET  /etsy/oauth/connect          → redirect to Etsy consent page
GET  /etsy/oauth/callback         → exchange code for tokens, store locally
GET  /etsy/oauth/status           → { connected: bool, user_id, expires_at }

# ── Etsy Publish ─────────────────────────────────────────────────────────
POST /api/etsy/publish            → publish QA-approved products to Etsy
     Body: { "m_numbers": ["M1001", "M1002"] } or { "all_approved": true }
     Response: { "published": 5, "failed": 1, "results": [...] }

GET  /api/etsy/publish/status     → last publish run status
```

### 4. Publish Workflow

For each product to publish:
1. Verify QA status is "approved" — **reject if not approved** (hard rule)
2. Generate Claude AI content if not already generated (title, description, tags)
3. Create draft listing via `POST /shops/{id}/listings`
4. Upload images 001-004 + 006 (lifestyle) via `POST /listings/{id}/images`
5. If product has size/colour variants, set inventory via `PUT /listings/{id}/inventory`
6. Store the Etsy `listing_id` back on the product record for future updates
7. Return results with listing URLs

### 5. UI Integration

Add an "Etsy" publish button alongside the existing eBay publish button in the
product management UI. The button should:
- Only be enabled for QA-approved products
- Show a confirmation dialog with the product count
- Call `POST /api/etsy/publish` with selected M-numbers
- Display results (success/failure per product with Etsy listing URLs)

Also add an Etsy OAuth connection status indicator in the settings/admin area,
with a "Connect Etsy" button that triggers the OAuth flow.

## Reference Files

| File | Purpose |
|------|---------|
| `export_etsy.py` | Current XLSX export — has size/price config, tag generation, Shop Uploader column mapping. **Keep as fallback.** |
| `ebay_api.py` | eBay direct publish — follow this pattern exactly |
| `ebay_auth.py` | eBay OAuth — mirror for Etsy OAuth |
| `content_generator.py` | AI content generation (Claude) — reuse for Etsy titles/descriptions |
| `config.py` | Single source of truth for sizes, prices, colours — **read from here, never duplicate** |
| `image_generator.py` | Image generation pipeline — images are at `{PUBLIC_BASE_URL}/{m_number} - {NNN}.jpg` |
| `D:\claw\core\etsy_intel\api_client.py` | Cairn's Etsy API client — reference for auth header format and rate limiting |

## Config Values (from `export_etsy.py` and Etsy account)

```python
ETSY_SHOP_ID = 11706740                    # NorthByNorthEastSign
ETSY_TAXONOMY_ID = 2844                    # Signs category
ETSY_SHIPPING_PROFILE_ID = 208230423243    # "Postage 2025"
ETSY_RETURN_POLICY_ID = 1074420280634      # return=true, exchange=true, 30 days
```

## Constraints

- **QA gate is non-negotiable.** Never publish a product that isn't QA-approved.
- **Always create as draft.** Never publish directly to active — staff review first.
- **Read sizes/prices from `config.py`**, not from `export_etsy.py` or hardcoded values.
- **Rate limit: 5 QPS.** Build throttling into every API call path.
- **Keep `export_etsy.py`** as a fallback for bulk operations or if API is down.
- **Store Etsy listing_id** on the product record after creation for future updates/deletes.
- **Images are served from the Render server** — use `PUBLIC_BASE_URL` env var for the URL base.
- **Don't touch Cairn's etsy_intel module** — that's read-only analytics. This is Render's publish pipeline.

## Testing

1. OAuth: visit `/etsy/oauth/connect`, complete flow, verify `/etsy/oauth/status` shows connected
2. Publish: select a QA-approved product, click Etsy publish, verify draft appears on Etsy
3. Images: verify all 5 images uploaded in correct order on the Etsy listing
4. Fallback: verify `POST /api/export/etsy` still generates the Shop Uploader XLSX
