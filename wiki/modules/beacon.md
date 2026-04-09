# Beacon — Google Ads Attribution Module

Beacon is NBNE's Google Ads integration and attribution system. It connects Google Ads spend to orders via a tracking pipeline, enabling cost-per-acquisition measurement across signage product categories.

## Status

Phase 1 complete as of 2026-04-07. Deployed at D:\beacon, ports 8017/3017.
GitHub: NBNEORIGIN/beacon

## Architecture

FastAPI backend + Next.js frontend. Per-row database encryption via Fernet (core/crypto.py, BEACON_ENC_KEY env var). PostgreSQL on port 5432.

## Google Ads OAuth

OAuth credentials are stored in `.env` only — never in wiki or source files. The redirect URI for the OAuth callback is `/oauth/google-ads/callback/`. Credential rotation: contact the developer if Google OAuth access is lost.

## Links

- [[wiki/modules/cairn]] — Cairn manages Beacon as a registered module
