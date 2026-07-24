"""
edgar_scan_core.py — framework-agnostic core of the Market Screener's
Stage 1 EDGAR fetch-and-score pipeline.

Extracted from app_pages/8_Market_Screener_EDGAR.py (2026-07-23) so the
exact same fetch-and-score logic can run two ways without duplicating
it, and without the two copies drifting apart over time:

  1. Inside the Streamlit app itself, for interactive scans.
  2. As a standalone script with no Streamlit runtime at all — see
     edgar_full_scan_cloud.py, run on a schedule via GitHub Actions —
     for the full ~6,000+ ticker universe scan.

Why (2) exists: live-measured, the identical fetch mechanics (shared
SEC rate limiter, connection-pooled Session, 2 requests/ticker) ran at
~242 tickers/min as a bare local script vs. ~75-130 tickers/min inside
the deployed Streamlit Cloud app — same code, same network conditions,
just a much slower wall-clock result when run inside the app's process
alongside Streamlit's own UI/websocket/fragment-rerun work. Rather than
trying to make the live app itself fast, the full-universe fetch is
moved out of the app's runtime entirely: a scheduled job keeps the
persistent facts cache (edgar_facts_cache/shard_*.json) warm for the
WHOLE universe, so the in-app scan becomes mostly cache reads + local
scoring — cheap regardless of the app container's resources — instead
of ~6,000 live EDGAR fetches every time someone runs it.

This file does import streamlit (sec_utils.py and the
fetch_full_us_equity_universe() @st.cache_data decorator both need it),
but never touches anything that requires an actual running app --
st.cache_data degrades to a plain in-memory, per-process cache when
there's no Streamlit runtime (confirmed: works fine from a bare
`python script.py`, just logs "No runtime found, using
MemoryCacheStorageManager"). That's what makes (2) possible without
any real Streamlit server involved. GitHub Contents API reads/writes
(for the sharded facts cache) are passed in as plain callables rather
than imported directly, so each caller wires up whatever credential
source is available to it:
  - the Streamlit app passes github_store.github_get_json/put_json
    (reads GITHUB_TOKEN from st.secrets)
  - the CI script passes its own thin wrappers around a plain
    GITHUB_TOKEN env var (same pattern already used by
    ms_download_cloud.py for the Morgan Stanley refresh job)
"""

import zlib
import time
import threading
from datetime import datetime, timezone
import requests
import streamlit as st

from sec_utils import (
    fetch_company_facts_with_cik,
    evaluate_buffett_funnel, FUNNEL_THRESHOLDS,
    evaluate_financial_firm_funnel, score_financial_firm_breakdown,
)

# (2026-07-24, punch list #76 follow-up) Raised 40 -> 500. Each cached
# entry stores full multi-year history per financial concept -- rich
# enough that measured shards were already averaging ~29KB/ticker with
# only 25-46 tickers each (some shards already 1.2-1.66MB). GitHub's
# Contents API silently omits the file body (no usable "content" field)
# for anything over ~1MB, which made those already-oversized shards
# unreadable -- and a failed read used to get silently treated as "this
# shard is empty" and overwritten with just the current checkpoint's
# batch (see save_facts_cache_updates()'s docstring for the full
# mechanism), which is what kept the whole cache stuck around ~1,000
# tickers instead of growing toward the ~6,157-ticker universe. At 500
# shards, the full universe averages ~12 tickers/shard (~350KB) --
# comfortable headroom under the 1MB ceiling even for a shard well
# above average, and still room to grow before this needs revisiting.
# Old shard_00.json..shard_39.json content was migrated into the new
# scheme (see migrate_edgar_shards.py, run once) rather than re-fetched.
EDGAR_FACTS_CACHE_NUM_SHARDS   = 500
EDGAR_FACTS_CACHE_MAX_AGE_DAYS = 7


def _facts_cache_shard_path(ticker: str) -> str:
    shard = zlib.crc32(ticker.upper().encode()) % EDGAR_FACTS_CACHE_NUM_SHARDS
    return f"edgar_facts_cache/shard_{shard:03d}.json"


def load_facts_cache_shards(tickers: list, get_json_fn) -> tuple:
    """
    Loads every shard file touched by this ticker list, once, up front.
    get_json_fn(path) -> (data, sha, error), same contract as
    github_store.github_get_json(). Returns (cache, errors):
      cache  -- {TICKER: {"fetched_at": iso_str, "facts": {...}}}
      errors -- list of "{path}: {message}" strings for any shard that
                came back with a real error (a clean 404 just means
                that shard hasn't been written yet — cold, not a
                problem).
    """
    shard_paths = sorted({_facts_cache_shard_path(t) for t in tickers})
    cache = {}
    errors = []
    for path in shard_paths:
        data, _sha, err = get_json_fn(path)
        if err:
            errors.append(f"{path}: {err}")
        if data:
            cache.update(data)
    return cache, errors


def _facts_cache_entry_fresh(entry: dict) -> bool:
    """True if a cached entry is within the freshness window and safe to
    reuse instead of re-fetching from EDGAR."""
    if not entry or not entry.get("fetched_at"):
        return False
    try:
        fetched_at = datetime.fromisoformat(entry["fetched_at"])
    except (TypeError, ValueError):
        return False
    return (datetime.now(timezone.utc) - fetched_at).days < EDGAR_FACTS_CACHE_MAX_AGE_DAYS


# Thread-local scratch slot: fetch_quality_edgar() runs inside worker
# threads, one ticker at a time per thread, synchronously start to
# finish. Using a thread-local here instead of a shared dict + lock
# means the caller can read back "did this particular call actually
# hit EDGAR, and if so what did it get back" with zero cross-thread
# contention -- each thread only ever sees its own slot, overwritten
# fresh on every ticker that thread handles.
_facts_cache_tls = threading.local()


def _get_facts_maybe_cached(ticker: str, cik: str, facts_cache: dict, force_refresh: bool) -> dict:
    """
    Cache-aware replacement for a direct fetch_company_facts_with_cik()
    call -- used by fetch_quality_edgar() below. Serves a fresh-enough
    cached entry if one exists and force_refresh isn't set; otherwise
    fetches live from EDGAR as before. Either way, stashes what should
    be persisted (or None, if nothing changed) on _facts_cache_tls for
    the calling worker to pick up.
    """
    entry = None if force_refresh else (facts_cache or {}).get(ticker.upper())
    if entry and _facts_cache_entry_fresh(entry):
        _facts_cache_tls.update = None
        return entry["facts"]

    facts = fetch_company_facts_with_cik(ticker, cik)
    # Don't cache a fetch failure -- that would "poison" the cache for
    # up to 7 days on what's very likely a transient EDGAR/network
    # issue (see _sec_get()'s own retry logic in sec_utils.py, which
    # already handles the transient case; this is the layer above that).
    if facts.get("error"):
        _facts_cache_tls.update = None
    else:
        _facts_cache_tls.update = {
            "ticker": ticker.upper(),
            "entry": {"fetched_at": datetime.now(timezone.utc).isoformat(), "facts": facts},
        }
    return facts


def save_facts_cache_updates(updates: dict, get_json_fn, put_json_fn) -> list:
    """
    updates: {TICKER: {"fetched_at": iso_str, "facts": {...}}} -- newly
    fetched or refreshed entries only. Groups by shard and does one
    GitHub read+write per shard actually touched, not per ticker.
    get_json_fn(path) -> (data, sha, error), put_json_fn(path, data,
    commit_message=...) -> (ok, message) -- same contracts as
    github_store.github_get_json()/github_put_json().

    Returns a list of (path, reason) tuples for any shard that failed
    to save even after retries, so the caller can surface both what
    failed AND why -- added 2026-07-24 after a GitHub Actions run
    dropped 32 of ~38 shard writes with only "X shard(s) failed" and no
    indication of the actual cause. Backward compatible with anything
    that only calls len() on the result (the app's own display code
    does exactly that).

    (2026-07-24, punch list #76 follow-up) CRITICAL: never merges
    shard_updates into an empty dict and writes it just because a read
    attempt failed. The previous version treated any get_json_fn()
    failure the same as "existing is empty" (`merged = dict(existing)
    if existing else {}`), then still called put_json_fn(path, merged,
    sha=None) -- and put_json_fn's own "sha wasn't supplied, fetch a
    fresh one" fallback would find a perfectly valid CURRENT sha (the
    read failure was transient/content-shape related, not "file doesn't
    exist"), so the write would succeed and silently REPLACE the
    shard's real existing content with just this checkpoint's small
    batch. Confirmed as the actual mechanism behind a near-total EDGAR
    facts cache wipe: shard files rich enough to cross GitHub's ~1MB
    Contents-API inline-content limit stopped returning readable
    content, every read of them "failed" in exactly this way, and every
    subsequent checkpoint quietly reset them back down -- with ZERO
    entries in failed_shards, because the write itself never errored.
    Now a failed read is retried (same backoff as before) but NEVER
    treated as "safe to merge from" -- only a clean read (real data, or
    a genuine 404-doesn't-exist-yet, which get_json_fn distinguishes by
    returning error=None either way) reaches the merge+write step. If
    all 3 read attempts fail, the shard is marked failed and left
    completely untouched, same as a write failure always was.
    """
    if not updates:
        return []
    by_shard = {}
    for ticker, entry in updates.items():
        by_shard.setdefault(_facts_cache_shard_path(ticker), {})[ticker] = entry

    failed_shards = []
    shard_paths = list(by_shard.items())
    for i, (path, shard_updates) in enumerate(shard_paths):
        if i > 0:
            # Pacing, not just retry-after-the-fact: GitHub's own
            # guidance for the Contents API is roughly one
            # content-creating request per second sustained.
            time.sleep(1.0)

        ok = False
        last_msg = ""
        for attempt in range(3):
            if attempt > 0:
                time.sleep(2 ** attempt)  # 2s, then 4s
            try:
                existing, sha, err = get_json_fn(path)
            except Exception as e:
                existing, sha, err = None, None, str(e)
            if err:
                # A REAL read failure -- NOT the clean "file doesn't
                # exist yet" case (that comes back as (None, None,
                # None), err falsy). Do not proceed to merge/write on
                # this attempt; retry the read instead. See the
                # docstring above for why this specific branch is the
                # one that used to cause silent data loss.
                last_msg = err
                continue
            merged = dict(existing) if existing else {}
            merged.update(shard_updates)
            try:
                # (2026-07-24) Passing sha through from the read above --
                # NOT letting put_json_fn silently re-fetch its own
                # "current" sha -- is what makes this retry loop actually
                # detect a concurrent writer instead of overwriting it.
                # See gh_put_json()'s comment in edgar_full_scan_cloud.py.
                # Safe now even on a genuine sha=None (brand-new shard,
                # confirmed via a CLEAN read above, not a failed one).
                ok, last_msg = put_json_fn(
                    path, merged, sha=sha,
                    commit_message=f"EDGAR facts cache update — {path} — {len(shard_updates)} ticker(s)",
                )
            except Exception as e:
                ok, last_msg = False, str(e)
            if ok:
                break
        if not ok:
            failed_shards.append((path, last_msg))
    return failed_shards


def get_cached_facts_readonly(ticker: str, get_json_fn) -> dict:
    """
    Cache-only, no-live-fallback single-ticker read of the persistent
    EDGAR facts cache (punch list #76). Reads just the one shard file
    this ticker hashes to, not the whole cache. Unlike
    _get_facts_maybe_cached() above (used by the bulk Market Screener
    scan), this NEVER calls EDGAR live -- a miss or a stale entry both
    come back as-is, with the caller responsible for deciding what to
    show (e.g. "click Refresh EDGAR data in the sidebar"), per the
    owner's explicit preference for a manual trigger over a silent live
    fallback on a cache miss/stale entry.

    get_json_fn(path) -> (data, sha, error), same contract as
    github_store.github_get_json() -- passed in rather than imported
    directly, matching this module-s existing dependency-injection
    pattern (see module docstring).

    Returns:
      {"facts": dict or None, "fetched_at": iso_str or None,
       "is_stale": bool or None, "error": str or None}

    "facts" is None only for a genuine cache miss (this ticker's shard
    has no entry for it yet) or a real read error -- check "error" to
    tell those apart. A present-but-stale entry (fetched_at older than
    EDGAR_FACTS_CACHE_MAX_AGE_DAYS) still returns its facts with
    is_stale=True rather than None: the 7-day window is tight enough
    that slightly-old real data usually beats a blank page, so the
    caller shows it with a staleness note rather than refusing to
    display anything.
    """
    path = _facts_cache_shard_path(ticker)
    data, _sha, err = get_json_fn(path)
    if err:
        return {"facts": None, "fetched_at": None, "is_stale": None, "error": err}
    entry = (data or {}).get(ticker.upper())
    if not entry:
        return {"facts": None, "fetched_at": None, "is_stale": None, "error": None}
    return {
        "facts":      entry.get("facts"),
        "fetched_at": entry.get("fetched_at"),
        "is_stale":   not _facts_cache_entry_fresh(entry),
        "error":      None,
    }


def fetch_market_cap_and_sector(ticker: str):
    """
    Shared market cap / GICS sector lookup — used by both the standard
    and bank/insurer alt-scoring paths in fetch_quality_edgar() so a
    ticker's market-cap-tier and Sector filters work identically either
    way. fast_info covers market cap cheaply; sector requires the fuller
    .info call, which is slower.
    """
    market_cap = None
    sector     = "Unknown"
    try:
        import yfinance as yf
        yf_ticker  = yf.Ticker(ticker)
        market_cap = getattr(yf_ticker.fast_info, "market_cap", None)
        try:
            sector = yf_ticker.info.get("sector") or "Unknown"
        except Exception:
            sector = "Unknown"
    except Exception:
        market_cap = None
    return market_cap, sector


def fetch_quality_edgar(ticker: str, cik: str, funnel_thresholds: dict = None,
                         facts_cache: dict = None, force_refresh: bool = False) -> dict:
    """
    Fetches fundamentals from EDGAR Company Facts using a pre-resolved CIK
    (no redundant ticker->CIK lookup per call — see get_ticker_cik_map()).

    facts_cache/force_refresh (#52): if facts_cache is given and has a
    fresh-enough entry for this ticker, that's used instead of hitting
    EDGAR at all -- see _get_facts_maybe_cached() above. force_refresh
    bypasses the cache entirely regardless of what's in it. Both are
    optional and default to "no cache, always fetch live" so this
    function still works exactly as before for any caller that doesn't
    pass them (e.g. the Equity Scout debug panel further up this file).
    Returns the price-independent fields plus the Buffett/Munger funnel
    checklist breakdown (evaluate_buffett_funnel — 10-yr avg ROIC, 10-yr
    avg FCF margin, dual debt-hurdle check, dilution check). Legacy
    single-period fields (roic, gross_margin, debt_to_fcf, interest_
    coverage) are still returned for reference/export — they are not
    part of the funnel gate itself (#31, #33, #35).

    Bank/insurer tickers (financial_subtype "bank"/"insurance", #36) skip
    this standard path entirely and route to evaluate_financial_firm_funnel()
    + score_financial_firm_breakdown() instead — the FCF>0 pre-filter below
    doesn't even apply to them, since "FCF" as op_cf+inv_cf is dominated by
    loan/investment portfolio volume for a bank and isn't a meaningful cash
    flow figure at all. Other financial SIC codes (brokers, REITs, real
    estate, investment offices — financial_subtype "other_financial") still
    go through the standard path below and still respect the "skip
    financial firms" toggle upstream, same as before #36.

    Does NOT fetch price — that happens only for Stage 1 survivors.
    """
    facts = _get_facts_maybe_cached(ticker, cik, facts_cache, force_refresh)
    if facts.get("error"):
        if facts.get("status_code") == 404:
            # Permanent, not transient — this CIK has no XBRL Company Facts at
            # all. Almost always means it's not an operating company filing
            # 10-Ks: closed-end funds and other registered investment
            # companies (Investment Company Act filers) don't populate this
            # API the way a normal 10-K filer does, and it also catches
            # stale/bad ticker->CIK mappings. Re-running will NOT fix this —
            # unlike a 429 or timeout, there's nothing to retry.
            return {"_status": "no_xbrl_data", "ticker": ticker, "reason": facts["error"]}
        return {"_status": "fetch_failed", "ticker": ticker, "reason": facts["error"]}

    latest = facts.get("latest", {})
    meta   = facts.get("meta", {})

    financial_subtype = meta.get("financial_subtype")
    if financial_subtype in ("bank", "insurance"):
        fin_funnel              = evaluate_financial_firm_funnel(facts, financial_subtype)
        fin_score, fin_criteria = score_financial_firm_breakdown(latest, financial_subtype)
        market_cap, sector      = fetch_market_cap_and_sector(ticker)
        long_term_debt          = latest.get("long_term_debt", 0) or 0
        short_term_debt         = latest.get("short_term_debt", 0) or 0

        return {
            "_status":            "evaluated",
            "ticker":             ticker,
            "name":               meta.get("company_name", ticker),
            "sic":                meta.get("sic"),
            "is_financial":       True,
            "is_cyclical":        meta.get("is_cyclical", False),
            "is_negative_equity": latest.get("is_negative_equity", False),
            "market_cap":         market_cap,
            "sector":             sector,
            "financial_subtype":  financial_subtype,
            # (2026-07-23) Latest-single-year ROE -- distinct from
            # roe_avg (10-yr average) below. compute_residual_income_value()'s
            # single-stage model needs THIS specifically (today's actual
            # ROE, to contrast against the 10-yr average), and it was
            # missing entirely from this dict -- every Market Screener
            # bank/insurer row hit that function's very first guard
            # ("ROE unavailable") and showed "N/A" regardless of how good
            # the underlying data actually was. Confirmed on ALL: roe_avg
            # (12.6%) was already here and displaying correctly in the
            # checklist caption, but plain "roe" (33.6% latest year) was
            # never included.
            "roe":                latest.get("roe"),
            # Standard-framework fields don't apply to a bank/insurer —
            # left None/False rather than omitted, so downstream code
            # (results table columns, CSV export) doesn't KeyError
            # expecting a key that's always present for non-financial rows.
            "fcf": None, "roic": None, "gross_margin": None, "debt_to_fcf": None,
            "interest_coverage": None, "is_net_creditor": False,
            "owner_earnings": None,
            "net_income":         latest.get("net_income"),
            "revenues":           latest.get("revenue"),
            "long_term_debt":     long_term_debt,
            "total_debt":         long_term_debt + short_term_debt,
            "fcf_margin": None, "cash_available_debt_service": None,
            "debt_to_ni": None, "debt_to_cads": None, "interest_margin_cads": None,
            # ── Bank/Insurer alt funnel + score (#36) ────────────────────
            "funnel":               fin_funnel,
            "funnel_passed":        fin_funnel["overall_passed"],
            "financial_score":      fin_score,
            "financial_criteria":   fin_criteria,
            "roe_avg":              fin_funnel["roe_avg"]["avg"],
            "roe_avg_years":        fin_funnel["roe_avg"]["years_used"],
            "capital_ratio":        fin_funnel["capital_ratio"],
            "quality_leg":          fin_funnel["quality_leg"],
            "quality_value":        fin_funnel["quality_value"],
            "dilution_passed":      fin_funnel["dilution_pass"],
            "dilution_pct_change":  fin_funnel["dilution"]["pct_change"],
            "limited_history":      fin_funnel["limited_history"],
            "funnel_years_used":    fin_funnel["years_used"],
            # Standard-funnel-only fields, kept present (as None) for the
            # same KeyError-avoidance reason as above.
            "roic_avg": None, "roic_avg_years": None,
            "fcf_margin_avg": None, "fcf_margin_avg_years": None,
            "debt_hurdle_cleared": None,
            "roic_stale": False, "roic_stale_years": None,
            "roic_last_reliable_period": None,
            "_latest": latest,
        }

    fcf            = latest.get("fcf")
    if fcf is None or fcf <= 0:
        return {"_status": "excluded_fcf", "ticker": ticker}
        # negative/no FCF in the latest year — hard pre-filter, applied
        # before the funnel checklist even runs. Note: this can exclude
        # a business with a genuinely strong 10-year average that simply
        # had one weak year — worth revisiting if it starts dropping
        # companies you'd expect the checklist to catch instead.
        # (Distinct from a fetch failure above — this IS real EDGAR
        # data, the company just didn't clear the pre-filter.)

    funnel = evaluate_buffett_funnel(facts, funnel_thresholds or FUNNEL_THRESHOLDS)

    roic           = latest.get("roic")
    gross_margin   = latest.get("gross_margin")
    debt_to_fcf    = latest.get("debt_to_fcf")
    long_term_debt = latest.get("long_term_debt", 0) or 0
    short_term_debt = latest.get("short_term_debt", 0) or 0
    total_debt     = long_term_debt + short_term_debt
    owner_earn     = latest.get("owner_earnings")
    net_income     = latest.get("net_income")
    revenues       = latest.get("revenue")

    is_net_creditor = False
    int_exp = latest.get("interest_paid") or latest.get("interest_expense")
    op_inc  = latest.get("op_income")
    int_coverage = latest.get("int_coverage")
    if int_exp and int_exp > 0 and op_inc is not None:
        int_coverage = op_inc / int_exp
    elif int_exp is None or int_exp == 0:
        cash = latest.get("cash", 0) or 0
        if cash > total_debt:
            is_net_creditor = True

    # Market cap & sector — fetched upfront for every Stage 1 ticker so
    # the market-cap-tier and Sector filters can apply before Stage 2
    # pricing/scoring. See fetch_market_cap_and_sector() for the
    # fast_info/.info cost tradeoff notes.
    market_cap, sector = fetch_market_cap_and_sector(ticker)

    return {
        "_status":           "evaluated",
        "ticker":            ticker,
        "name":              meta.get("company_name", ticker),
        "sic":               meta.get("sic"),
        "is_financial":      meta.get("is_financial", False),
        "financial_subtype": meta.get("financial_subtype"),
        "is_cyclical":       meta.get("is_cyclical", False),
        "is_negative_equity": latest.get("is_negative_equity", False),
        "market_cap":        market_cap,
        "sector":            sector,
        "fcf":               fcf,
        "roic":              roic,
        "gross_margin":      gross_margin,
        "debt_to_fcf":       debt_to_fcf,
        "interest_coverage": int_coverage,
        "is_net_creditor":   is_net_creditor,
        "owner_earnings":    owner_earn,
        "net_income":        net_income,
        "revenues":          revenues,
        "long_term_debt":    long_term_debt,
        "total_debt":        total_debt,
        "fcf_margin":        latest.get("fcf_margin"),
        "cash_available_debt_service": latest.get("cash_available_debt_service"),
        "debt_to_ni":        latest.get("debt_to_ni"),
        "debt_to_cads":      latest.get("debt_to_cads"),
        "interest_margin_cads": latest.get("interest_margin_cads"),
        # ── Buffett/Munger funnel checklist (#63) ──────────────────────
        "funnel":               funnel,
        "funnel_passed":        funnel["overall_passed"],
        "roic_avg":             funnel["roic_avg"]["avg"],
        "roic_avg_years":       funnel["roic_avg"]["years_used"],
        "fcf_margin_avg":       funnel["fcf_margin_avg"]["avg"],
        "fcf_margin_avg_years": funnel["fcf_margin_avg"]["years_used"],
        "debt_hurdle_cleared":  funnel["debt_hurdle_cleared"],
        "dilution_passed":      funnel["dilution_pass"],
        "dilution_pct_change":  funnel["dilution"]["pct_change"],
        "limited_history":      funnel["limited_history"],
        "funnel_years_used":    funnel["years_used"],
        "roic_stale":           funnel["roic_stale"],
        "roic_stale_years":     funnel["roic_stale_years"],
        "roic_last_reliable_period": funnel["roic_last_reliable_period"],
        "_latest":           latest,
    }



# ═════════════════════════════════════════════════════════════════════
# Ticker universe source (moved here 2026-07-23 alongside the rest of
# the scan core -- see module docstring for why this file has to stay
# streamlit-import-free. @st.cache_data degrades gracefully to a
# per-process in-memory cache when there's no active Streamlit runtime
# (confirmed: importing/calling this outside `streamlit run` just logs
# "No runtime found, using MemoryCacheStorageManager" and works fine),
# so the decorator is safe to keep as-is for both callers.
#
# FTSE Russell's official Russell 1000/2000 constituent files are
# commercial-license-only (no free API exists). iShares used to publish
# free CSV exports of their tracking ETFs' holdings (IWB/IWM), but that
# direct-download endpoint has since been retired in favor of a
# JavaScript-rendered page that a simple HTTP request can't trigger.
#
# Instead we build a broad, free, market-cap-tiered universe directly
# from Nasdaq Trader's public Symbol Directory files -- the same files
# every exchange-listed security is registered in.
# ═════════════════════════════════════════════════════════════════════
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED_URL  = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"


@st.cache_data(ttl=86400)
def fetch_full_us_equity_universe(universe: str = "all") -> list:
    """
    Fetch the complete list of US-listed common stocks from Nasdaq
    Trader's public Symbol Directory (NASDAQ + NYSE + NYSE American +
    NYSE Arca + Cboe BZX). Filters out ETFs, test issues, warrants,
    units, rights, and other non-common-stock instruments.

    Returns a plain list of uppercase ticker symbols (~6,000-8,000).
    Cached 24 hours — these files update intraday but daily refresh
    is plenty for screening purposes.
    """
    headers = {"User-Agent": "Mozilla/5.0 (compatible; VoskuilFP/1.0)"}
    tickers = set()

    # nasdaqlisted.txt: Symbol|Security Name|Market Category|Test Issue|
    #                    Financial Status|Round Lot Size|ETF|NextShares
    try:
        resp = requests.get(NASDAQ_LISTED_URL, headers=headers, timeout=15)
        if resp.status_code == 200:
            lines = resp.text.strip().splitlines()
            for line in lines[1:]:  # skip header
                parts = line.split("|")
                if len(parts) < 7:
                    continue
                symbol, name, _cat, test_issue, _fin_status, _lot, is_etf = parts[:7]
                if test_issue.strip().upper() == "Y" or is_etf.strip().upper() == "Y":
                    continue
                name_upper = name.upper()
                if any(x in name_upper for x in (" RIGHT", " WARRANT", " UNIT", " ORDINARY SHARE")):
                    # Keep ADS/common but drop SPAC units/rights/warrants and
                    # non-US ordinary shares (different reporting regime)
                    if " ORDINARY SHARE" not in name_upper:
                        continue
                symbol = symbol.strip().upper()
                if symbol and len(symbol) <= 6 and "." not in symbol and "$" not in symbol:
                    tickers.add(symbol)
    except Exception:
        pass

    # otherlisted.txt: ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|
    #                   Round Lot Size|Test Issue|NASDAQ Symbol
    try:
        resp = requests.get(OTHER_LISTED_URL, headers=headers, timeout=15)
        if resp.status_code == 200:
            lines = resp.text.strip().splitlines()
            for line in lines[1:]:
                parts = line.split("|")
                if len(parts) < 7:
                    continue
                act_symbol, name, _exch, _cqs, is_etf, _lot, test_issue = parts[:7]
                if test_issue.strip().upper() == "Y" or is_etf.strip().upper() == "Y":
                    continue
                name_upper = name.upper()
                if any(x in name_upper for x in (" RIGHT", " WARRANT", " UNIT")):
                    continue
                symbol = act_symbol.strip().upper()
                if symbol and len(symbol) <= 6 and "." not in symbol and "$" not in symbol:
                    tickers.add(symbol)
    except Exception:
        pass

    return sorted(tickers)

