"""Fetch Make.com usage data and write it to data/make_usage.json.

Reads credentials from .env (via python-dotenv). The token is loaded into
memory only — it is never printed to stdout, logs, or the output file.

Run from the project root:
    python scripts/fetch_make_usage.py
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


# GitHub Actions injects unset secrets as the literal string "" (two quote
# chars) rather than an empty string. Same risk for sentinels like None/null
# pasted from other tools. Normalize once so downstream code treats them as
# truly empty and falls back to organizationId.
_EMPTY_SENTINELS = {"", "none", "null", "undefined", "nil", "n/a"}


def _env(name, default=""):
    raw = os.environ.get(name, default)
    if raw is None:
        return ""
    s = str(raw).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        s = s[1:-1].strip()
    if s.lower() in _EMPTY_SENTINELS:
        return ""
    return s


def _env_int(name, default=0):
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_float(name, default=0.0):
    raw = _env(name)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


TOKEN = _env("MAKE_API_TOKEN")
BASE_URL = _env("MAKE_API_BASE_URL").rstrip("/")
ORG_ID = _env("MAKE_ORGANIZATION_ID")
TEAM_ID = _env("MAKE_TEAM_ID")
PLAN_CREDITS = _env_int("MAKE_PLAN_CREDITS", 0)
MONTHLY_COST = _env_float("MAKE_MONTHLY_COST_EUR", 0.0)
EXTRA_CREDITS = _env_int("MAKE_EXTRA_CREDITS", 0)
EXTRA_COST = _env_float("MAKE_EXTRA_COST_EUR", 0.0)
CURRENCY = _env("CURRENCY") or "EUR"

# Pre-compute cost-per-credit once: same value used for scenarios, folders, totals.
TOTAL_CREDITS = (PLAN_CREDITS or 0) + (EXTRA_CREDITS or 0)
TOTAL_COST_EUR = (MONTHLY_COST or 0) + (EXTRA_COST or 0)
COST_PER_CREDIT = (TOTAL_COST_EUR / TOTAL_CREDITS) if TOTAL_CREDITS > 0 else 0.0


def die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


if not TOKEN:
    die("MAKE_API_TOKEN is missing in .env")
if not BASE_URL:
    die("MAKE_API_BASE_URL is missing in .env")
if not ORG_ID:
    die("MAKE_ORGANIZATION_ID is missing in .env")


HEADERS = {"Authorization": f"Token {TOKEN}"}
# Make's /scenarios/{id}/logs caps pg[limit] at 50; 50 is universally accepted.
PAGE_LIMIT = 50
SANS_FOLDER = "Sans folder / org-level"

notes = []


def _to_num(v):
    return v if isinstance(v, (int, float)) else 0


def _cost(credits):
    return round(_to_num(credits) * COST_PER_CREDIT, 2)


def api(path, params=None):
    url = f"{BASE_URL}{path}"
    resp = requests.get(url, headers=HEADERS, params=params or {}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def paginate(path, base_params=None, list_key=None):
    """Yield items page-by-page using Make.com's pg[offset]/pg[limit] params."""
    base_params = dict(base_params or {})
    offset = 0
    detected_key = list_key
    while True:
        params = dict(base_params)
        params["pg[offset]"] = offset
        params["pg[limit]"] = PAGE_LIMIT
        data = api(path, params=params)
        if detected_key is None:
            for k, v in data.items():
                if isinstance(v, list):
                    detected_key = k
                    break
        batch = data.get(detected_key, []) if detected_key else []
        if not batch:
            break
        for item in batch:
            yield item
        if len(batch) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT


def safe(label, fn, fallback):
    try:
        return fn()
    except Exception as e:
        notes.append(f"{label}: {type(e).__name__}: {e}")
        return fallback


# --- Team auto-discovery (only when MAKE_TEAM_ID is empty) ---
# Folders are team-scoped in Make. If the user didn't set MAKE_TEAM_ID, try to
# discover a single team in the org. With 0 or >1 teams, we leave it empty and
# scenarios fall under "Sans folder / org-level".
auto_team_id = None
if not TEAM_ID:
    teams = safe(
        "teams_discovery",
        lambda: list(paginate(
            "/teams", base_params={"organizationId": ORG_ID}, list_key="teams",
        )),
        [],
    )
    if len(teams) == 1:
        auto_team_id = teams[0].get("id")
        notes.append("auto-discovered a single team in the org -> using it for folders")
    elif len(teams) > 1:
        notes.append(
            f"multiple teams ({len(teams)}) found in the org -> folders skipped "
            "(set MAKE_TEAM_ID in .env to pick one)"
        )
    else:
        notes.append("no team found in the organization -> folders skipped")

effective_team_id = TEAM_ID or auto_team_id


# --- Scope for scenarios ---
if TEAM_ID:
    scope_label = "team"
    scope_params = {"teamId": TEAM_ID}
else:
    scope_label = "organization"
    scope_params = {"organizationId": ORG_ID}


# --- Scenarios (paginated) ---
scenarios = safe(
    "scenarios",
    lambda: list(paginate("/scenarios", base_params=scope_params, list_key="scenarios")),
    [],
)


# --- Folders (only if we have a team id, explicit or auto-discovered) ---
folders = []
if effective_team_id:
    folders = safe(
        "folders",
        lambda: list(paginate(
            "/scenarios-folders",
            base_params={"teamId": effective_team_id},
            list_key="scenariosFolders",
        )),
        [],
    )

folder_by_id = {f.get("id"): f for f in folders if f.get("id") is not None}


# --- Organization usage (last ~30 days, per day) ---
# Make returns the daily array under the "data" key (not "usage").
usage_by_day = safe(
    "organization_usage",
    lambda: api(f"/organizations/{ORG_ID}/usage").get("data", []),
    [],
)


# --- Per-scenario logs over the last 30 days ---
now = datetime.now(timezone.utc).replace(microsecond=0)
since_30 = now - timedelta(days=30)
since_7 = now - timedelta(days=7)


def iso_z(dt):
    # Make's /logs endpoint rejects ISO strings with microseconds or "+00:00".
    return dt.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def status_kind(status):
    # Make convention (best-effort): 1=success, 2=warning, 3=error, 4=incomplete.
    s = str(status).lower()
    if s in ("3", "error", "4", "incomplete"):
        return "error"
    if s in ("2", "warning"):
        return "warning"
    return "ok"


scenario_stats = {}
for sc in scenarios:
    sid = sc.get("id")
    if sid is None:
        continue
    logs = safe(
        "logs[scenario]",
        lambda sid=sid: list(paginate(
            f"/scenarios/{sid}/logs",
            # /logs uses dateFrom/dateTo for ISO; "from"/"to" expect unix timestamps.
            base_params={"dateFrom": iso_z(since_30), "dateTo": iso_z(now)},
            list_key="scenarioLogs",
        )),
        [],
    )
    ops_30 = ops_7 = err_30 = err_7 = warn_30 = warn_7 = 0
    for entry in logs:
        ts = parse_ts(entry.get("timestamp") or entry.get("date") or entry.get("imt_started"))
        ops = _to_num(entry.get("operations"))
        kind = status_kind(entry.get("status"))
        ops_30 += ops
        if kind == "error":
            err_30 += 1
        elif kind == "warning":
            warn_30 += 1
        if ts and ts >= since_7:
            ops_7 += ops
            if kind == "error":
                err_7 += 1
            elif kind == "warning":
                warn_7 += 1
    scenario_stats[sid] = dict(
        operations_30d=ops_30, operations_7d=ops_7,
        errors_30d=err_30, errors_7d=err_7,
        warnings_30d=warn_30, warnings_7d=warn_7,
    )


# --- Enrich scenarios: stats, folder grouping, cost ---
# Make's /scenarios response already includes "operations" / "dlqCount" per
# scenario for the current billing period. We use those as a reliable fallback
# when /logs returns nothing for a scenario.
enriched = []
for sc in scenarios:
    sid = sc.get("id")
    stats = scenario_stats.get(sid, {})
    folder_id = sc.get("folderId")
    folder = folder_by_id.get(folder_id) if folder_id else None
    folder_name = folder.get("name") if folder else SANS_FOLDER
    sc_ops_period = _to_num(sc.get("operations"))
    sc_dlq = _to_num(sc.get("dlqCount"))
    operations_30d = stats.get("operations_30d", 0) or sc_ops_period
    operations_7d = stats.get("operations_7d", 0)
    errors_30d_sc = stats.get("errors_30d", 0) or sc_dlq
    enriched.append({
        "id": sid,
        "name": sc.get("name"),
        "is_active": bool(sc.get("isActive", False)),
        "folder_id": folder_id,
        "folder_name": folder_name,
        "scheduling": sc.get("scheduling"),
        "last_edit": sc.get("lastEdit"),
        "operations_7d": operations_7d,
        "operations_30d": operations_30d,
        "cost_7d_eur": _cost(operations_7d),
        "cost_30d_eur": _cost(operations_30d),
        "errors_7d": stats.get("errors_7d", 0),
        "errors_30d": errors_30d_sc,
        "warnings_7d": stats.get("warnings_7d", 0),
        "warnings_30d": stats.get("warnings_30d", 0),
        "dlq_count": sc_dlq,
    })

enriched.sort(key=lambda s: s["operations_30d"], reverse=True)


# --- Folders summary: aggregate scenarios by folder, then cost & sort ---
folder_agg = {}
for sc in enriched:
    fid = sc["folder_id"]
    fname = sc["folder_name"]
    key = fid if fid is not None else "_none"
    if key not in folder_agg:
        folder_agg[key] = {
            "folder_id": fid,
            "folder_name": fname,
            "scenarios_count": 0,
            "active_scenarios_count": 0,
            "operations_7d": 0,
            "operations_30d": 0,
            "errors_7d": 0,
            "warnings_7d": 0,
        }
    agg = folder_agg[key]
    agg["scenarios_count"] += 1
    if sc["is_active"]:
        agg["active_scenarios_count"] += 1
    agg["operations_7d"] += sc["operations_7d"]
    agg["operations_30d"] += sc["operations_30d"]
    agg["errors_7d"] += sc["errors_7d"]
    agg["warnings_7d"] += sc["warnings_7d"]

folders_summary = list(folder_agg.values())
for f in folders_summary:
    f["cost_7d_eur"] = _cost(f["operations_7d"])
    f["cost_30d_eur"] = _cost(f["operations_30d"])
folders_summary.sort(key=lambda f: f["cost_30d_eur"], reverse=True)


# --- Org-level totals (authoritative from /usage) ---
month_operations = sum(_to_num(d.get("operations")) for d in usage_by_day)
week_operations = 0
for d in usage_by_day:
    ts = parse_ts(d.get("date"))
    if ts and ts >= since_7:
        week_operations += _to_num(d.get("operations"))

days_in_usage = len(usage_by_day) or 1
month_cost_eur = round(month_operations * COST_PER_CREDIT, 2)
week_cost_eur = round(week_operations * COST_PER_CREDIT, 2)
avg_daily_cost_eur = round(month_cost_eur / days_in_usage, 2)
quota_used_pct = round((month_operations / TOTAL_CREDITS * 100), 1) if TOTAL_CREDITS > 0 else 0.0

errors_30d_total = sum(s["errors_30d"] for s in enriched)
errors_7d_total = sum(s["errors_7d"] for s in enriched)
warnings_30d_total = sum(s["warnings_30d"] for s in enriched)
warnings_7d_total = sum(s["warnings_7d"] for s in enriched)


output = {
    "generated_at": now.isoformat(),
    "currency": CURRENCY,
    "scope": {
        "kind": scope_label,
        "has_team_id": bool(TEAM_ID),
        "auto_team_used": bool(auto_team_id),
    },
    "totals": {
        "month_operations": month_operations,
        "week_operations": week_operations,
        "plan_credits": PLAN_CREDITS,
        "extra_credits": EXTRA_CREDITS,
        "total_credits": TOTAL_CREDITS,
        "monthly_cost_eur": MONTHLY_COST,
        "extra_cost_eur": EXTRA_COST,
        "total_monthly_cost_eur": TOTAL_COST_EUR,
        "cost_per_credit_eur": round(COST_PER_CREDIT, 6),
        "month_cost_eur": month_cost_eur,
        "week_cost_eur": week_cost_eur,
        "avg_daily_cost_eur": avg_daily_cost_eur,
        "quota_used_pct": quota_used_pct,
        "errors_30d": errors_30d_total,
        "errors_7d": errors_7d_total,
        "warnings_30d": warnings_30d_total,
        "warnings_7d": warnings_7d_total,
    },
    "usage_by_day": usage_by_day,
    "scenarios": enriched,
    "folders": [
        {
            "id": f.get("id"),
            "name": f.get("name"),
            "scenarios_total": f.get("scenariosTotal"),
        }
        for f in folders
    ],
    "folders_summary": folders_summary,
    "notes": notes,
}


output_path = ROOT / "data" / "make_usage.json"
output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_text(
    json.dumps(output, indent=2, ensure_ascii=False),
    encoding="utf-8",
)


print(f"Wrote {output_path.relative_to(ROOT)}")
print(f"  scope            : {scope_label}{' (auto-team)' if auto_team_id else ''}")
print(f"  scenarios        : {len(enriched)}")
print(f"  folders          : {len(folders)} ({len(folders_summary)} groups incl. 'no folder')")
print(f"  operations 30d   : {month_operations}")
print(f"  operations 7d    : {week_operations}")
print(f"  errors 7d / 30d  : {errors_7d_total} / {errors_30d_total}")
print(f"  warnings 7d / 30d: {warnings_7d_total} / {warnings_30d_total}")
print(f"  quota used       : {quota_used_pct}%")
print(f"  cost / credit    : {round(COST_PER_CREDIT, 6)} {CURRENCY}")
print(f"  month cost (est) : {month_cost_eur} {CURRENCY}")
print(f"  week cost (est)  : {week_cost_eur} {CURRENCY}")
print(f"  avg daily (est)  : {avg_daily_cost_eur} {CURRENCY}")
if notes:
    print(f"  notes            : {len(notes)} (see data/make_usage.json)")
