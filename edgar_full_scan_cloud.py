"""
edgar_full_scan_cloud.py — Full-universe EDGAR facts cache refresh
(GitHub Actions / scheduled, no Streamlit runtime).
=====================================================================
This is the "do it right" fix for the Market Screener's full-universe
scan being 2-3x slower inside the deployed Streamlit Cloud app than
the exact same fetch logic run as a bare script (measured: ~242
tickers/min local vs. ~75-130/min in the app, same rate limiter, same
connection-pooled Session, same 2 requests/ticker — see edgar_scan_core.py's
module docstring for the full comparison). Rather than trying to make
the live app's background scan fast under whatever resource
constraints Streamlit Cloud's container imposes, this script moves the
expensive part -- fetching and caching raw EDGAR facts for the WHOLE
~6,000+ ticker universe -- out of the app's runtime entirely, and runs
it here instead, on a schedule (see .github/workflows/edgar_full_scan.yml).

What this does NOT do: compute Stage 1 scoring (funnel pass/fail,
market cap/sector via yfinance). Only the sharded facts cache
(edgar_facts_cache/shard_*.json) gets refreshed here -- scoring stays
cheap, local, in-app, and always uses the latest cached facts. That
also keeps this job focused on its one expensive dependency (SEC
EDGAR) instead of adding yfinance rate-limit exposure for data nobody
persists anyway.

Incremental by design: loads the existing shard cache first and skips
any ticker whose entry is still within the 7-day freshness window (see
edgar_scan_core.EDGAR_FACTS_CACHE_MAX_AGE_DAYS), so a normal scheduled
run only actually fetches whatever's gone stale or is new to the
universe since the last run -- not all ~6,000+ tickers from scratch
every time. --force-refresh overrides that for a genuine full cold
run.

ENVIRONMENT VARIABLES (set as GitHub Secrets, same pattern as
ms_download_cloud.py):
  GITHUB_TOKEN  — repo token (auto-provided by Actions)
  GITHUB_REPO   — e.g. jjpvoskuil/Voskuil-FP-1-0 (auto-provided)

Run locally for a dry run / smaller test:
  python3 edgar_full_scan_cloud.py --sample 200 --workers 8
"""

import argparse
import base64
import json
import os
import sys
import time
import concurrent.futures
from datetime import datetime, timezone

import requests

from sec_utils import get_ticker_cik_map
from edgar_scan_core import (
    fetch_full_us_equity_universe,
    load_facts_cache_shards,
    save_facts_cache_updates,
    _get_facts_maybe_cached,
    _facts_cache_tls,
)

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "jjpvoskuil/Voskuil-FP-1-0")
API_ROOT     = f"https://api.github.com/repos/{GITHUB_REPO}/contents"
HEADERS      = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept":        "application/vnd.github+json",
}

CHECKPOINT_INTERVAL = 500  # save to GitHub every N newly-fetched tickers


# ─────────────────────────────────────────────────────────────────────
# GitHub Contents API — same (data, sha, error) / (ok, message)
# contracts as github_store.py's github_get_json()/github_put_json(),
# just backed by a plain env-var token instead of st.secrets. This is
# the same pattern already proven in ms_download_cloud.py's
# push_to_github() for the (separate) Morgan Stanley refresh job.
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


def gh_put_json(path: str, data, commit_message: str):
    if not GITHUB_TOKEN:
        return False, "GITHUB_TOKEN not set"
    try:
        content_str = json.dumps(data)
        api = f"{API_ROOT}/{path}"
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
    ap = argparse.ArgumentParser(description="Refresh the full-universe EDGAR facts cache.")
    ap.add_argument("--workers", type=int, default=20, help="Concurrent worker threads (default 20, matches the app's Stage 1 pool)")
    ap.add_argument("--sample", type=int, default=None, help="Limit to first N tickers (testing only — omit for a real full run)")
    ap.add_argument("--force-refresh", action="store_true", help="Ignore the 7-day freshness window and refetch every ticker")
    args = ap.parse_args()

    if not GITHUB_TOKEN:
        print("ERROR: GITHUB_TOKEN not set — nothing would get saved. Set it as an env var (or a GitHub Actions secret) and re-run.")
        sys.exit(1)

    print(f"Repo: {GITHUB_REPO}")
    print("Loading full US equity universe (Nasdaq Trader symbol directories)...")
    tickers = fetch_full_us_equity_universe(universe="all_us")
    if args.sample:
        tickers = tickers[:args.sample]
    print(f"  {len(tickers)} tickers in scope")

    print("Loading ticker -> CIK map from SEC EDGAR...")
    ticker_cik_map = get_ticker_cik_map()
    print(f"  {len(ticker_cik_map)} CIKs resolved")

    print("Loading existing facts cache shards (for incremental skip)...")
    facts_cache, load_errors = load_facts_cache_shards(tickers, gh_get_json)
    print(f"  {len(facts_cache)} cached entries loaded" + (f", {len(load_errors)} shard read errors" if load_errors else ""))
    for err in load_errors:
        print(f"    ! {err}")

    def worker(ticker):
        cik = ticker_cik_map.get(ticker.upper())
        if not cik:
            return ticker, "no_cik", None
        facts = _get_facts_maybe_cached(ticker, cik, facts_cache, args.force_refresh)
        update = getattr(_facts_cache_tls, "update", None)
        status = "error" if facts.get("error") else ("fetched" if update else "cache_hit")
        return ticker, status, update

    facts_cache_updates = {}
    counts = {"no_cik": 0, "cache_hit": 0, "fetched": 0, "error": 0}
    done = 0
    start = time.time()

    print(f"\nScanning {len(tickers)} tickers with {args.workers} workers"
          f"{' (force-refresh)' if args.force_refresh else ''}...\n")

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(worker, t): t for t in tickers}
        for future in concurrent.futures.as_completed(futures):
            ticker = futures[future]
            try:
                _t, status, update = future.result()
            except Exception as e:
                status, update = "error", None
                print(f"  ! {ticker}: unexpected exception: {e}")
            counts[status] = counts.get(status, 0) + 1
            if update:
                facts_cache_updates[update["ticker"]] = update["entry"]

            done += 1
            if done % 100 == 0 or done == len(tickers):
                elapsed = time.time() - start
                rate = done / elapsed * 60 if elapsed > 0 else 0
                print(f"  [{done}/{len(tickers)}] {rate:.1f}/min | "
                      f"hits={counts['cache_hit']} fetched={counts['fetched']} "
                      f"errors={counts['error']} no_cik={counts['no_cik']} | "
                      f"{len(facts_cache_updates)} pending checkpoint")

            if len(facts_cache_updates) >= CHECKPOINT_INTERVAL:
                print(f"  --> checkpoint: saving {len(facts_cache_updates)} updated tickers to GitHub...")
                failed = save_facts_cache_updates(dict(facts_cache_updates), gh_get_json, gh_put_json)
                if failed:
                    print(f"      ! {len(failed)} shard(s) failed to save: {failed}")
                facts_cache_updates.clear()

    if facts_cache_updates:
        print(f"\nFinal checkpoint: saving {len(facts_cache_updates)} remaining updated tickers to GitHub...")
        failed = save_facts_cache_updates(dict(facts_cache_updates), gh_get_json, gh_put_json)
        if failed:
            print(f"  ! {len(failed)} shard(s) failed to save: {failed}")

    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"Done in {elapsed/60:.1f} minutes ({len(tickers)/elapsed*60:.1f} tickers/min)")
    print(f"  Cache hits (skipped, still fresh): {counts['cache_hit']}")
    print(f"  Freshly fetched:                   {counts['fetched']}")
    print(f"  No CIK match:                      {counts['no_cik']}")
    print(f"  Errors:                            {counts['error']}")
    print(f"  Finished at:                        {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    main()
