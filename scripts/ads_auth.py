"""
Amazon Ads API OAuth flow — NBNE / Cairn Intelligence
=====================================================

Captures refresh tokens and marketplace profile IDs across all three
Ads API regions (EU, NA, FE) in one run.

Usage:
    python ads_auth.py

Prerequisites:
    - AMAZON_ADS_CLIENT_ID and AMAZON_ADS_CLIENT_SECRET in D:\\claw\\.env
    - Redirect URI http://localhost:8766/callback added to the
      Cairn_Intelligence LWA security profile's allowed return URLs
      at developer.amazon.com/loginwithamazon
    - A browser available on this machine
    - You are signed into Amazon with the correct seller account for
      each region when prompted

Output:
    Appends AMAZON_ADS_* entries to D:\\claw\\.env
    Prints refresh tokens and profile IDs to console for sanity check

Why three runs:
    Authorization codes are region-scoped. NBNE has seller accounts in
    EU (NBNE Ltd — UK+DE), NA (Origin Designers — US+CA), and FE
    (OriginDesigned — AU). Each region needs its own OAuth flow against
    its own authorization endpoint and returns a refresh token valid
    only for that region's Ads API endpoint.
"""

import argparse
import http.server
import json
import re
import secrets
import socketserver
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CALLBACK_PORT = 8766  # 8765 is occupied by Cairn FastAPI; must match LWA Allowed Return URL
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"
ENV_PATH = Path(r"D:\claw\.env")
PROFILES_PATH = Path(r"D:\claw\amazon_ads_profiles.json")


def _load_credentials_from_env() -> tuple[str, str]:
    """Read AMAZON_ADS_CLIENT_ID and AMAZON_ADS_CLIENT_SECRET from .env.
    Credentials are never hard-coded — this script refuses to run without them."""
    if not ENV_PATH.exists():
        raise SystemExit(f"{ENV_PATH} not found. Populate AMAZON_ADS_CLIENT_ID "
                         f"and AMAZON_ADS_CLIENT_SECRET there before running.")
    text = ENV_PATH.read_text(encoding="utf-8")
    values: dict[str, str] = {}
    for key in ("AMAZON_ADS_CLIENT_ID", "AMAZON_ADS_CLIENT_SECRET"):
        # take the last occurrence — matches python-dotenv semantics
        import re as _re
        matches = _re.findall(rf"^{_re.escape(key)}=(.*)$", text, _re.MULTILINE)
        if not matches or not matches[-1].strip():
            raise SystemExit(f"{key} missing from {ENV_PATH}. Populate it first.")
        values[key] = matches[-1].strip()
    return values["AMAZON_ADS_CLIENT_ID"], values["AMAZON_ADS_CLIENT_SECRET"]


CLIENT_ID, CLIENT_SECRET = _load_credentials_from_env()

# Region → (auth host, ads API host)
REGIONS = {
    "EU": ("https://eu.account.amazon.com/ap/oa", "https://advertising-api-eu.amazon.com"),
    "NA": ("https://www.amazon.com/ap/oa",         "https://advertising-api.amazon.com"),
    "FE": ("https://apac.account.amazon.com/ap/oa","https://advertising-api-fe.amazon.com"),
}

# Token endpoint is unified — LWA apps are not region-specific for token exchange
TOKEN_URL = "https://api.amazon.com/auth/o2/token"
SCOPE = "advertising::campaign_management"


# ---------------------------------------------------------------------------
# Local callback server — catches the redirect from Amazon
# ---------------------------------------------------------------------------
class _Catcher(http.server.BaseHTTPRequestHandler):
    auth_code = None
    state_received = None

    def do_GET(self):
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        _Catcher.auth_code = params.get("code", [None])[0]
        _Catcher.state_received = params.get("state", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = b"<h1>Got it. You can close this tab.</h1>"
        self.wfile.write(msg)

    def log_message(self, *args, **kwargs):
        pass  # silence access log


def _probe_port(port: int) -> None:
    """Fail loudly if `port` is already bound. Windows TCPServer will silently
    'succeed' in some SO_REUSEADDR cases, so do an explicit probe first."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("localhost", port))
    except OSError as exc:
        raise SystemExit(
            f"Port {port} is already in use ({exc}).\n"
            f"Something else is bound to localhost:{port} — likely a dev server.\n"
            f"Either stop it, or change CALLBACK_PORT in this script AND add the\n"
            f"new http://localhost:<port>/callback to the Cairn_Intelligence LWA\n"
            f"profile's Allowed Return URLs before re-running."
        )
    finally:
        s.close()


def _await_callback(timeout_s: int = 300) -> str:
    """Start local server, wait for the redirect, return the auth code."""
    _Catcher.auth_code = None
    _probe_port(CALLBACK_PORT)
    server = socketserver.TCPServer(("localhost", CALLBACK_PORT), _Catcher)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    import time
    waited = 0
    while _Catcher.auth_code is None and waited < timeout_s:
        time.sleep(0.5)
        waited += 0.5

    server.shutdown()
    server.server_close()

    if _Catcher.auth_code is None:
        raise TimeoutError("No callback received within timeout")
    return _Catcher.auth_code


# ---------------------------------------------------------------------------
# OAuth flow for one region
# ---------------------------------------------------------------------------
def run_region(region_key: str) -> dict:
    auth_host, api_host = REGIONS[region_key]
    print(f"\n{'='*60}")
    print(f"Region: {region_key}")
    print(f"Auth host: {auth_host}")
    print(f"API host:  {api_host}")
    print(f"{'='*60}")
    input(f"Sign into Amazon with the {region_key} seller account, "
          f"then press ENTER to open the browser...")

    state = secrets.token_urlsafe(16)
    params = {
        "client_id": CLIENT_ID,
        "scope": SCOPE,
        "response_type": "code",
        "state": state,
        "redirect_uri": REDIRECT_URI,
    }
    auth_url = f"{auth_host}?{urllib.parse.urlencode(params)}"
    print(f"Opening browser:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    code = _await_callback()
    print(f"[{region_key}] Auth code received.")

    # Exchange code for tokens
    tok_resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if tok_resp.status_code != 200:
        raise RuntimeError(
            f"Token exchange failed [{tok_resp.status_code}]: {tok_resp.text}"
        )
    tokens = tok_resp.json()
    access_token = tokens["access_token"]
    refresh_token = tokens["refresh_token"]
    print(f"[{region_key}] Refresh token captured.")

    # List profiles for this region
    prof_resp = requests.get(
        f"{api_host}/v2/profiles",
        headers={
            "Amazon-Advertising-API-ClientId": CLIENT_ID,
            "Authorization": f"Bearer {access_token}",
        },
        timeout=30,
    )
    if prof_resp.status_code != 200:
        raise RuntimeError(
            f"[{region_key}] /v2/profiles failed [{prof_resp.status_code}]: "
            f"{prof_resp.text}"
        )
    profiles = prof_resp.json()
    print(f"[{region_key}] {len(profiles)} profile(s) found.")
    for p in profiles:
        cc = p.get("countryCode")
        pid = p.get("profileId")
        name = p.get("accountInfo", {}).get("name")
        print(f"    {cc}  profileId={pid}  name={name}")

    return {"refresh_token": refresh_token, "profiles": profiles}


# ---------------------------------------------------------------------------
# Env writer
# ---------------------------------------------------------------------------
def _upsert_env_keys(path: Path, updates: dict) -> None:
    """Replace existing keys in `.env` in place, append new ones at end.
    Prevents the duplicate-key accumulation `.env` parsers silently collapse."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = existing.splitlines()
    seen = set()
    for i, line in enumerate(lines):
        m = re.match(r"^([A-Z_][A-Z0-9_]*)\s*=", line)
        if m and m.group(1) in updates:
            key = m.group(1)
            lines[i] = f"{key}={updates[key]}"
            seen.add(key)
    for key, val in updates.items():
        if key not in seen:
            lines.append(f"{key}={val}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_results(results: dict) -> None:
    # 1. Refresh tokens -> .env (upsert, no duplicates, no CLIENT_ID/SECRET rewrite)
    token_updates = {
        f"AMAZON_ADS_REFRESH_TOKEN_{region}": data["refresh_token"]
        for region, data in results.items()
    }
    _upsert_env_keys(ENV_PATH, token_updates)
    print(f"\nUpserted {len(token_updates)} refresh token(s) in {ENV_PATH}")

    # 2. Profiles -> JSON sidecar, merged with any prior regions already captured.
    #    Key by region so a partial re-run (e.g. NA only) doesn't clobber EU/FE.
    existing = {}
    if PROFILES_PATH.exists():
        try:
            existing = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"  WARNING: {PROFILES_PATH} was unreadable; overwriting.")
    for region, data in results.items():
        existing[region] = [
            {
                "profileId": p.get("profileId"),
                "countryCode": p.get("countryCode"),
                "currencyCode": p.get("currencyCode"),
                "accountName": (p.get("accountInfo") or {}).get("name"),
                "accountType": (p.get("accountInfo") or {}).get("type"),
                "accountId": (p.get("accountInfo") or {}).get("id"),
                "marketplaceStringId": (p.get("accountInfo") or {}).get("marketplaceStringId"),
            }
            for p in data["profiles"]
        ]
    PROFILES_PATH.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    total = sum(len(v) for v in existing.values())
    print(f"Wrote {total} profile(s) across {len(existing)} region(s) to {PROFILES_PATH}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Amazon Ads API OAuth capture.")
    parser.add_argument(
        "--regions",
        default="EU,NA,FE",
        help="Comma-separated subset of EU,NA,FE to run (default: all three). "
             "Partial runs merge into amazon_ads_profiles.json without clobbering "
             "previously captured regions.",
    )
    args = parser.parse_args()
    selected = [r.strip().upper() for r in args.regions.split(",") if r.strip()]
    invalid = [r for r in selected if r not in REGIONS]
    if invalid:
        raise SystemExit(f"Unknown region(s): {invalid}. Valid: {list(REGIONS)}")

    results = {}
    for region in selected:
        try:
            results[region] = run_region(region)
        except Exception as exc:
            print(f"[{region}] FAILED: {exc}")
            if len(selected) > 1:
                cont = input(f"Continue with next region? (y/N): ").strip().lower()
                if cont != "y":
                    break

    if results:
        write_results(results)
    print("\nDone.")