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
import threading
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
    ap.add_argument("--shard-count", type=int, default=1,
                     help="Split the universe into this many passes (for staying under GitHub Actions' 6-hour job limit -- "
                          "e.g. 2 for a two-run split). Default 1 = process the whole universe in one run.")
    ap.add_argument("--shard-index", type=int, default=0,
                     help="Which pass this run is (0-based, must be < --shard-count). Ignored if --shard-count is 1.")
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
        # Modulo slicing, not a contiguous chunk -- spreads any tickers
        # that are unevenly distributed alphabetically (e.g. clusters of
        # ADR/20-F filers, which are expensive -- see the module
        # docstring) evenly across passes instead of risking one pass
        # unluckily getting a disproportionate share of the slow ones.
        all_count = len(tickers)
        tickers = tickers[args.shard_index::args.shard_count]
        print(f"  Shard {args.shard_index + 1}/{args.shard_count}: {len(tickers)} of {all_count} tickers in scope this run")
    else:
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
        # (2026-07-24) Per-ticker wall-clock timing -- added after the
        # first real GitHub Actions run measured 9.6 tickers/min, far
        # below both the local bare-script baseline (~242/min) AND the
        # in-app Streamlit Cloud baseline (~75-130/min) this job exists
        # to beat. No 429s anywhere in that run's log, which rules out
        # active SEC rate-limiting as the cause -- so the next question
        # is whether the slowness is spread evenly across every ticker
        # (points to runner network latency/throughput) or concentrated
        # in a handful of outliers (points to a few large-payload
        # filers or retry-triggering timeouts on specific tickers).
        # fetch_time_s below answers that from the next run's summary.
        t0 = time.time()
        cik = ticker_cik_map.get(ticker.upper())
        if not cik:
            return ticker, "no_cik", None, time.time() - t0
        # In-flight concurrency tracking -- added alongside the timing
        # instrumentation to check a second hypothesis: the elevated
        # baseline latency (min=2.6s, p50=4.5s vs. ~2s locally) plus a
        # measured throughput far below what even that latency should
        # allow with 20 workers (Little's Law: ~19 workers should be
        # enough to approach the rate limiter's own ~250/min ceiling at
        # a 4.5s/ticker latency, but the actual run got 7-9.6/min --
        # ~25-30x short of that) raises the question of whether
        # requests are genuinely running concurrently on a GitHub-hosted
        # runner or something is serializing them. max_in_flight in the
        # summary answers that directly.
        with _inflight_lock:
            _inflight["current"] += 1
            _inflight["max"] = max(_inflight["max"], _inflight["current"])
        try:
            facts = _get_facts_maybe_cached(ticker, cik, facts_cache, args.force_refresh)
        finally:
            with _inflight_lock:
                _inflight["current"] -= 1
        update = getattr(_facts_cache_tls, "update", None)
        status = "error" if facts.get("error") else ("fetched" if update else "cache_hit")
        # (2026-07-24) First real full-universe run: 186 errors, first
        # time any run has seen a nonzero error count -- every sample
        # run (50-200 tickers) came back at exactly 0. Capturing the
        # actual reason per ticker (previously only counted, never
        # surfaced -- same class of blind spot as the shard-save
        # failures earlier today) so the next run says what's actually
        # failing instead of just how many.
        error_reason = facts.get("error") if status == "error" else None
        return ticker, status, update, time.time() - t0, error_reason

    facts_cache_updates = {}
    counts = {"no_cik": 0, "cache_hit": 0, "fetched": 0, "error": 0}
    fetch_times = []  # (ticker, seconds) -- only for status == "fetched" (live EDGAR hits, not cache hits)
    error_details = []  # (ticker, reason) -- for status == "error"
    _inflight_lock = threading.Lock()
    _inflight = {"current": 0, "max": 0}
    done = 0
    start = time.time()

    print(f"\nScanning {len(tickers)} tickers with {args.workers} workers"
          f"{' (force-refresh)' if args.force_refresh else ''}...\n")

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(worker, t): t for t in tickers}
        for future in concurrent.futures.as_completed(futures):
            ticker = futures[future]
            try:
                _t, status, update, fetch_time, error_reason = future.result()
            except Exception as e:
                status, update, fetch_time, error_reason = "error", None, None, str(e)
                print(f"  ! {ticker}: unexpected exception: {e}")
            counts[status] = counts.get(status, 0) + 1
            if update:
                facts_cache_updates[update["ticker"]] = update["entry"]
            if status == "fetched" and fetch_time is not None:
                fetch_times.append((ticker, fetch_time))
            if status == "error":
                error_details.append((ticker, error_reason or "unknown"))

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
                    print(f"      ! {len(failed)} shard(s) failed to save:")
                    for _path, _reason in failed:
                        print(f"          {_path}: {_reason}")
                facts_cache_updates.clear()

    if facts_cache_updates:
        print(f"\nFinal checkpoint: saving {len(facts_cache_updates)} remaining updated tickers to GitHub...")
        failed = save_facts_cache_updates(dict(facts_cache_updates), gh_get_json, gh_put_json)
        if failed:
            print(f"  ! {len(failed)} shard(s) failed to save:")
            for _path, _reason in failed:
                print(f"      {_path}: {_reason}")

    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"Done in {elapsed/60:.1f} minutes ({len(tickers)/elapsed*60:.1f} tickers/min)")
    print(f"  Cache hits (skipped, still fresh): {counts['cache_hit']}")
    print(f"  Freshly fetched:                   {counts['fetched']}")
    print(f"  No CIK match:                      {counts['no_cik']}")
    if fetch_times:
        secs = sorted(t for _tk, t in fetch_times)
        n = len(secs)
        p50 = secs[n // 2]
        p90 = secs[int(n * 0.9)] if n > 1 else secs[0]
        print(f"  Live fetch time/ticker (2 requests): min={secs[0]:.1f}s  p50={p50:.1f}s  "
              f"p90={p90:.1f}s  max={secs[-1]:.1f}s")
        slowest = sorted(fetch_times, key=lambda x: -x[1])[:10]
        print(f"  10 slowest fetches: " + ", ".join(f"{tk}={t:.1f}s" for tk, t in slowest))
    print(f"  Max concurrent in-flight fetches observed: {_inflight['max']} (of {args.workers} workers)")
    print(f"  Errors:                            {counts['error']}")
    if error_details:
        from collections import Counter
        # Group by a normalized reason prefix rather than the full
        # message (which often embeds the ticker/CIK/URL and would make
        # every message look "unique" even when the underlying cause is
        # identical) -- reveals whether 186 errors is "one root cause,
        # 186 times" or genuinely 186 different problems.
        def _normalize(reason):
            r = str(reason)
            return r[:80]
        reason_counts = Counter(_normalize(r) for _t, r in error_details)
        print(f"  Error reasons (grouped, {len(reason_counts)} distinct):")
        for reason, count in reason_counts.most_common(10):
            print(f"      {count:4d}x  {reason}")
        sample = error_details[:15]
        print(f"  First {len(sample)} error tickers: " + ", ".join(f"{tk}" for tk, _r in sample))
    print(f"  Finished at:                        {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    main()
