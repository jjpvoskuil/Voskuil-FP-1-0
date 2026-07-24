"""
superinvestor_full_scan_cloud.py — Full Dataroma superinvestor scrape
(GitHub Actions / scheduled, no Streamlit runtime).
=====================================================================
Persistent-cache counterpart to edgar_full_scan_cloud.py / 
yahoo_full_scan_cloud.py (punch list #76 follow-up), for the third live
fetch identified in that audit: superinvestor_utils.py scrapes all ~82
Dataroma-tracked managers' full portfolios (~1,600-2,500 holdings) on
demand, taking 30-60 seconds -- and used to be cached ONLY in
st.session_state, so every new browser session or app reboot/redeploy
redid the whole scrape from scratch.

Much simpler than the EDGAR/Yahoo jobs: no sharding (the whole map is a
few hundred KB, comfortably under GitHub's ~1MB Contents API limit --
see superinvestor_utils.SUPERINVESTOR_CACHE_PATH's docstring), and no
incremental/freshness-skip logic (13F filings this aggregates are
quarterly, so there's no per-ticker "still fresh, skip it" concept the
way EDGAR/Yahoo have -- every run just does the full scrape and
replaces the persisted file outright).

ENVIRONMENT VARIABLES (set as GitHub Secrets, same pattern as the
EDGAR/Yahoo cloud scripts):
  GITHUB_TOKEN  — repo token (auto-provided by Actions)
  GITHUB_REPO   — e.g. jjpvoskuil/Voskuil-FP-1-0 (auto-provided)

Run locally for a dry run (doesn't write anywhere):
  python3 superinvestor_full_scan_cloud.py --dry-run
"""
import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from superinvestor_utils import build_full_conviction_map, SUPERINVESTOR_CACHE_PATH

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "jjpvoskuil/Voskuil-FP-1-0")
API_ROOT     = f"https://api.github.com/repos/{GITHUB_REPO}/contents"
HEADERS      = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept":        "application/vnd.github+json",
}


# ─────────────────────────────────────────────────────────────────────
# GitHub Contents API — same (data, sha, error) / (ok, message)
# contracts as github_store.py's github_get_json()/github_put_json(),
# just backed by a plain env-var token. Same pattern as
# edgar_full_scan_cloud.py's gh_get_json()/gh_put_json().
# ─────────────────────────────────────────────────────────────────────

def gh_get_json(path: str):
    if not GITHUB_TOKEN:
        return None, None, "GITHUB_TOKEN not set"
    try:
        r = requests.get(f"{API_ROOT}/{path}", headers=HEADERS, timeout=20)
        if r.status_code == 404:
            return None, None, None
        if r.status_code != 200:
            return None, None, f"GET failed: {r.status_code} {r.text[:150]}"
        body = r.json()
        return json.loads(base64.b64decode(body["content"]).decode()), body.get("sha"), None
    except Exception as e:
        return None, None, f"GET exception: {e}"


def gh_put_json(path: str, data, commit_message: str, sha: str = None):
    if not GITHUB_TOKEN:
        return False, "GITHUB_TOKEN not set"
    try:
        content_str = json.dumps(data)
        api = f"{API_ROOT}/{path}"
        if sha is None:
            r = requests.get(api, headers=HEADERS, timeout=20)
            sha = r.json().get("sha") if r.status_code == 200 else None
        payload = {"message": commit_message, "content": base64.b64encode(content_str.encode()).decode()}
        if sha:
            payload["sha"] = sha
        put_r = requests.put(api, headers=HEADERS, json=payload, timeout=60)
        if put_r.status_code in (200, 201):
            return True, "Synced"
        return False, f"PUSH failed: {put_r.status_code} {put_r.text[:200]}"
    except Exception as e:
        return False, f"PUSH exception: {e}"


def main():
    ap = argparse.ArgumentParser(description="Refresh the persistent Dataroma superinvestor cache.")
    ap.add_argument("--dry-run", action="store_true", help="Scrape and print a summary, but don't write to GitHub.")
    args = ap.parse_args()

    if not args.dry_run and not GITHUB_TOKEN:
        print("ERROR: GITHUB_TOKEN not set — nothing would get saved. Set it as an env var (or a GitHub Actions secret) and re-run.")
        sys.exit(1)

    print(f"Repo: {GITHUB_REPO}")
    print("Scraping all Dataroma-tracked manager portfolios...")
    start = time.time()
    data = build_full_conviction_map()
    elapsed = time.time() - start

    if data.get("error") and not data.get("ticker_map"):
        print(f"ERROR: scrape failed entirely: {data['error']}")
        sys.exit(1)

    print(f"Done in {elapsed:.1f}s — {data.get('total_managers', 0)} managers, "
          f"{data.get('total_holdings', 0)} total holdings, "
          f"{len(data.get('ticker_map', {}))} distinct tickers.")

    payload = {
        "fetched_at":     datetime.now(timezone.utc).isoformat(),
        "ticker_map":     data.get("ticker_map", {}),
        "total_managers": data.get("total_managers", 0),
        "total_holdings": data.get("total_holdings", 0),
    }
    size_kb = len(json.dumps(payload).encode()) / 1024
    print(f"Payload size: {size_kb:.0f} KB")

    if args.dry_run:
        print("--dry-run: not writing to GitHub.")
        return

    _existing, sha, _err = gh_get_json(SUPERINVESTOR_CACHE_PATH)
    ok, msg = gh_put_json(
        SUPERINVESTOR_CACHE_PATH, payload, sha=sha,
        commit_message=(f"Superinvestor data update — {payload['total_managers']} "
                         f"managers, {payload['total_holdings']} holdings"),
    )
    if ok:
        print(f"✅ Saved to {SUPERINVESTOR_CACHE_PATH}")
    else:
        print(f"❌ Save failed: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
