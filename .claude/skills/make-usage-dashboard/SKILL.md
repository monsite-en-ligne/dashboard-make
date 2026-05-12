---
name: make-usage-dashboard
description: Use when working in the make-usage-dashboard project to fetch Make.com usage data, build the dashboard, or analyze operations/data-transfer consumption. Covers conventions for scripts/, dashboard/, and data/ folders and the .env file.
---

# make-usage-dashboard skill

Local project that analyzes Make.com consumption (operations, data transfer, scenarios).

## Folder conventions

- `scripts/` — code that calls the Make.com API and writes raw results into `data/`. One script per concern (e.g. `fetch_scenarios.py`, `fetch_usage.py`).
- `data/` — raw JSON/CSV from the API. Gitignored. Treat as cache, safe to delete and refetch.
- `dashboard/` — UI that **reads from `data/` only**, never calls the API directly. Keeps fetching and rendering decoupled.

## Secrets

- Real credentials live in `.env` (gitignored). Never commit them.
- `.env.example` holds placeholders only.
- Required variables: `MAKE_API_TOKEN`, `MAKE_API_BASE_URL`, `MAKE_ORGANIZATION_ID`, optional `MAKE_TEAM_ID`.
- Load them via a library (e.g. `python-dotenv`, `dotenv` for Node) — do not hardcode.

## Make.com API notes

- Base URL is zone-specific (`eu1`, `eu2`, `us1`, …). Use the value from `MAKE_API_BASE_URL`.
- Auth header: `Authorization: Token <MAKE_API_TOKEN>`.
- Relevant endpoints for usage analysis: `/organizations/{id}/usage`, `/scenarios`, `/scenarios/{id}/logs`.

## When helping in this project

1. Confirm the language/stack before writing a fetch script (not yet chosen — ask the user).
2. Keep API calls in `scripts/`, rendering in `dashboard/`, data in `data/`.
3. Never write a real token into any tracked file.
