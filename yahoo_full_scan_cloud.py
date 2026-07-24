"""
yahoo_full_scan_cloud.py — Full-universe Yahoo Finance price/market-cap
cache refresh (GitHub Actions / scheduled, no Streamlit runtime).
=====================================================================
The Yahoo Finance equivalent of edgar_full_scan_cloud.py -- keeps
yahoo_price_cache/shard_*.json warm for the full ~6,000+ ticker "All
US Common Stocks" universe, running independently of the app so
Dashboard/Watchlist/Equity Scout/Market Screener can all read price,
market cap, sector, and dividend yield from the persistent cache
instead of calling yfinance directly on every page load.

Meant to run twice a day (see .github/workflows/yahoo_price_refresh.yml)
-- price data goes stale much faster than EDGAR fundamentals, hence
the much shorter freshness window (yahoo_scan_core.YAHOO_CACHE_MAX_AGE_HOURS,
currently 18h) and higher-frequency schedule.

IMPORTANT: yfinance is not an official, rate-documented API the way
SEC EDGAR is -- it scrapes Yahoo Finance's own endpoints, and Yahoo's
limits are undocumented and stricter in practice. This script paces
requests conservatively (yahoo_scan_core._YAHOO_MIN_REQUEST_INTERVAL,
currently ~2/sec vs. SEC's ~8.3/sec) specifically to avoid getting the
source IP temporarily blocked -- which would take down live price
display for the WHOLE app, not just slow this job down, since every
page shares the same yfinance access. Start conservative; only widen
this after a run history shows it's genuinely safe to go faster.

ENVIRONMENT VARIABLES (same pattern as edgar_full_scan_cloud.py):
  GITHUB_TOKEN  — repo token (auto-provided by Actions)
  GITHUB_REPO   — e.g. jjpvoskuil/Voskuil-FP-1-0 (auto-provided)

Run locally for a dry run / smaller test:
  python3 yahoo_full_scan_cloud.py --sample 200 --workers 4
"""

import argparse
import base64
import json
import os
import sys
import time
import threading
import concurrent.futures
from datetime import datetime, timezone

import requests

from edgar_scan_core import fetch_full_us_equity_universe
from yahoo_scan_core import (
    load_yahoo_cache_shards,
    save_yahoo_cache_updates,
    get_price_maybe_cached,
    _yahoo_cache_tls,
)

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "jjpvoskuil/Voskuil-FP-1-0")
API_ROOT     = f"https://api.github.com/repos/{GITHUB_REPO}/contents"
HEADERS      = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept":        "application/vnd.github+json",
}

CHECKPOINT_INTERVAL = 500


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
    # (2026-07-24) `sha` should be the sha returned by the get_json_fn()
    # read that `data` was merged from -- NOT re-fetched fresh here.
    # Re-fetching a "current" sha right before every write defeats
    # GitHub's optimistic-concurrency check: if another process (e.g. a
    # concurrent local bootstrap run writing the same shard) saved a
    # newer version of this file in between our read and our write, a
    # freshly-fetched sha would match that newer version and let this
    # PUT succeed anyway -- silently overwriting the other process's
    # just-saved entries with our older, already-stale merge. Passing
    # through the original sha instead means a conflicting concurrent
    # write correctly causes this PUT to fail (409/422 -> caller's
    # retry loop re-reads, re-merges, and retries) instead of silently
    # clobbering data. Root-caused after two parallel full-universe
    # runs (GitHub Actions + local bootstrap) left several EDGAR facts
    # cache shards missing most of their entries despite both runs
    # reporting near-complete fetch counts -- same clobbering pattern
    # applies here since this script mirrors edgar_full_scan_cloud.py.
    # Falls back to a fresh GET if no sha is supplied, for backward
    # compatibility with any other caller.
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
    ap = argparse.ArgumentParser(description="Refresh the full-universe Yahoo Finance price cache.")
    ap.add_argument("--workers", type=int, default=8,
                     help="Concurrent worker threads (default 8 -- deliberately lower than the "
                          "EDGAR job's 20, since yfinance's undocumented limits are stricter; "
                          "the shared pacer caps real throughput regardless, this just bounds "
                          "how many threads are waiting on it at once)")
    ap.add_argument("--sample", type=int, default=None, help="Limit to first N tickers (testing only)")
    ap.add_argument("--force-refresh", action="store_true", help="Ignore the freshness window and refetch every ticker")
    ap.add_argument("--shard-count", type=int, default=1, help="Split the universe into this many passes")
    ap.add_argument("--shard-index", type=int, default=0, help="Which pass this run is (0-based)")
    args = ap.parse_args()

    if args.shard_count < 1 or not (0 <= args.shard_index < args.shard_count):
        print(f"ERROR: --shard-index must be in [0, --shard-count). Got shard-index={args.shard_index}, shard-count={args.shard_count}.")
        sys.exit(1)

    if not GITHUB_TOKEN:
        print("ERROR: GITHUB_TOKEN not set — nothing would get saved. Set it as an env var (or a GitHub Actions secret) and re-run.")
        sys.exit(1)

    print(f"Repo: {GITHUB_REPO}")
    print("Loading full US equity universe (Nasdaq Trader symbol directories)...")
    tickers = fetch_full_us_equity_universe(universe="all_us")
    if args.sample:
        tickers = tickers[:args.sample]
    if args.shard_count > 1:
        all_count = len(tickers)
        tickers = tickers[args.shard_index::args.shard_count]
        print(f"  Shard {args.shard_index + 1}/{args.shard_count}: {len(tickers)} of {all_count} tickers in scope this run")
    else:
        print(f"  {len(tickers)} tickers in scope")

    print("Loading existing price cache shards (for incremental skip)...")
    cache, load_errors = load_yahoo_cache_shards(tickers, gh_get_json)
    print(f"  {len(cache)} cached entries loaded" + (f", {len(load_errors)} shard read errors" if load_errors else ""))
    for err in load_errors:
        print(f"    ! {err}")

    def worker(ticker):
        t0 = time.time()
        data = get_price_maybe_cached(ticker, cache, args.force_refresh)
        update = getattr(_yahoo_cache_tls, "update", None)
        status = "error" if (data.get("error") or data.get("price") is None) else ("fetched" if update else "cache_hit")
        return ticker, status, update, time.time() - t0

    cache_updates = {}
    counts = {"cache_hit": 0, "fetched": 0, "error": 0}
    fetch_times = []
    done = 0
    start = time.time()

    print(f"\nScanning {len(tickers)} tickers with {args.workers} workers"
          f"{' (force-refresh)' if args.force_refresh else ''}...\n")

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(worker, t): t for t in tickers}
        for future in concurrent.futures.as_completed(futures):
            ticker = futures[future]
            try:
                _t, status, update, fetch_time = future.result()
            except Exception as e:
                status, update, fetch_time = "error", None, None
                print(f"  ! {ticker}: unexpected exception: {e}")
            counts[status] = counts.get(status, 0) + 1
            if update:
                cache_updates[update["ticker"]] = update["entry"]
            if status == "fetched" and fetch_time is not None:
                fetch_times.append((ticker, fetch_time))

            done += 1
            if done % 100 == 0 or done == len(tickers):
                elapsed = time.time() - start
                rate = done / elapsed * 60 if elapsed > 0 else 0
                print(f"  [{done}/{len(tickers)}] {rate:.1f}/min | "
                      f"hits={counts['cache_hit']} fetched={counts['fetched']} "
                      f"errors={counts['error']} | {len(cache_updates)} pending checkpoint")

            if len(cache_updates) >= CHECKPOINT_INTERVAL:
                print(f"  --> checkpoint: saving {len(cache_updates)} updated tickers to GitHub...")
                failed = save_yahoo_cache_updates(dict(cache_updates), gh_get_json, gh_put_json)
                if failed:
                    print(f"      ! {len(failed)} shard(s) failed to save:")
                    for _path, _reason in failed:
                        print(f"          {_path}: {_reason}")
                cache_updates.clear()

    if cache_updates:
        print(f"\nFinal checkpoint: saving {len(cache_updates)} remaining updated tickers to GitHub...")
        failed = save_yahoo_cache_updates(dict(cache_updates), gh_get_json, gh_put_json)
        if failed:
            print(f"  ! {len(failed)} shard(s) failed to save:")
            for _path, _reason in failed:
                print(f"      {_path}: {_reason}")

    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"Done in {elapsed/60:.1f} minutes ({len(tickers)/elapsed*60:.1f} tickers/min)")
    print(f"  Cache hits (skipped, still fresh): {counts['cache_hit']}")
    print(f"  Freshly fetched:                   {counts['fetched']}")
    print(f"  Errors:                            {counts['error']}")
    if fetch_times:
        secs = sorted(t for _tk, t in fetch_times)
        n = len(secs)
        p50 = secs[n // 2]
        p90 = secs[int(n * 0.9)] if n > 1 else secs[0]
        print(f"  Live fetch time/ticker: min={secs[0]:.1f}s  p50={p50:.1f}s  p90={p90:.1f}s  max={secs[-1]:.1f}s")
    print(f"  Finished at:                        {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    main()
