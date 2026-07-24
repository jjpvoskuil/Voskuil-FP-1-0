"""
yahoo_scan_core.py — framework-agnostic persistent cache for Yahoo
Finance price/market-cap/sector data, mirroring edgar_scan_core.py's
pattern for the same reasons (see that file's module docstring).

Runs two ways: inside the Streamlit app (fast, cache-first reads), and
as a standalone script (yahoo_full_scan_cloud.py) on a twice-daily
GitHub Actions schedule, keeping the persistent cache warm so the app
itself never needs to call yfinance directly.

IMPORTANT DIFFERENCE FROM THE EDGAR CACHE: yfinance is not an official,
rate-documented API -- it scrapes Yahoo Finance's own web endpoints,
and Yahoo's rate limits are undocumented and stricter in practice than
SEC EDGAR's. Getting this wrong risks the source IP being temporarily
blocked, which would take down price display for the WHOLE app (Dashboard,
Watchlist, Equity Scout all share this one cache), not just slow it
down -- so the pacing here (_YAHOO_MIN_REQUEST_INTERVAL) starts more
conservative than the SEC rate limiter's, on purpose, until real usage
proves it can be safely tightened.

Freshness window is hours, not days (EDGAR fundamentals barely move
week to week; price/market cap can move all day) -- see
YAHOO_CACHE_MAX_AGE_HOURS.
"""

import time
import threading
import zlib
from datetime import datetime, timezone

YAHOO_CACHE_NUM_SHARDS     = 40
YAHOO_CACHE_MAX_AGE_HOURS  = 18  # a bit more than half a day, so a twice-daily
                                  # refresh always lands inside the window with
                                  # some slack for a run finishing late/failing once

# Shared pacing across all threads -- mirrors sec_utils.py's _rate_lock
# pattern exactly, just with a slower, more conservative interval given
# yfinance's undocumented, stricter-in-practice limits (see module
# docstring). Start conservative; tighten later only if real measured
# runs show zero 429s/blocks at a faster pace, the same way the SEC
# limiter's own value was arrived at.
_yahoo_rate_lock          = threading.Lock()
_yahoo_last_request_time  = [0.0]
_YAHOO_MIN_REQUEST_INTERVAL = 0.5   # ~2 req/sec ceiling


def _yahoo_paced_call(fn, *args, **kwargs):
    """Runs fn(*args, **kwargs) after enforcing the shared pacing gap,
    same lock-then-release-before-the-real-call pattern as sec_utils.
    _sec_get() -- see that function's comments for why the sleep has to
    happen INSIDE the lock but the actual (slow) call has to happen
    OUTSIDE it, or concurrency across threads gets serialized for no
    reason."""
    with _yahoo_rate_lock:
        elapsed = time.monotonic() - _yahoo_last_request_time[0]
        if elapsed < _YAHOO_MIN_REQUEST_INTERVAL:
            time.sleep(_YAHOO_MIN_REQUEST_INTERVAL - elapsed)
        _yahoo_last_request_time[0] = time.monotonic()
    return fn(*args, **kwargs)


def _yahoo_cache_shard_path(ticker: str) -> str:
    shard = zlib.crc32(ticker.upper().encode()) % YAHOO_CACHE_NUM_SHARDS
    return f"yahoo_price_cache/shard_{shard:02d}.json"


def load_yahoo_cache_shards(tickers: list, get_json_fn) -> tuple:
    """Same contract as edgar_scan_core.load_facts_cache_shards()."""
    shard_paths = sorted({_yahoo_cache_shard_path(t) for t in tickers})
    cache = {}
    errors = []
    for path in shard_paths:
        data, _sha, err = get_json_fn(path)
        if err:
            errors.append(f"{path}: {err}")
        if data:
            cache.update(data)
    return cache, errors


def _yahoo_cache_entry_fresh(entry: dict) -> bool:
    if not entry or not entry.get("fetched_at"):
        return False
    try:
        fetched_at = datetime.fromisoformat(entry["fetched_at"])
    except (TypeError, ValueError):
        return False
    return (datetime.now(timezone.utc) - fetched_at).total_seconds() < YAHOO_CACHE_MAX_AGE_HOURS * 3600


def fetch_price_and_market_cap_live(ticker):
    """
    The actual yfinance call, paced through _yahoo_paced_call(). Same
    return shape and retry-once-on-miss behavior as the existing
    sec_utils.fetch_price_and_market_cap() (that function stays exactly
    as-is for any caller that still wants a direct, uncached live call
    -- this is a separate, cache-aware path used by the background
    refresh script and by get_price_maybe_cached() below).
    """
    from sec_utils import safe_float, _normalize_dividend_yield

    def _one_attempt():
        import yfinance as yf
        info = yf.Ticker(ticker).info
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        return info, price

    for attempt in range(2):
        try:
            info, price = _yahoo_paced_call(_one_attempt)
            if price is None and attempt == 0:
                time.sleep(1.5)
                continue
            return {
                "price":          safe_float(price),
                "market_cap":     safe_float(info.get("marketCap")),
                "shares":         safe_float(info.get("sharesOutstanding")),
                "dividend_yield": _normalize_dividend_yield(info.get("dividendYield")),
                "name":           info.get("longName") or info.get("shortName") or ticker,
                "sector":         info.get("sector", "N/A"),
                "description":    (info.get("longBusinessSummary", "")[:400] + "...") if info.get("longBusinessSummary") else "",
            }
        except Exception as e:
            if attempt == 0:
                time.sleep(1.5)
                continue
            return {"price": None, "market_cap": None, "shares": None,
                    "dividend_yield": None, "name": ticker, "sector": "N/A",
                    "description": "", "error": str(e)}
    return {"price": None, "market_cap": None, "shares": None,
            "dividend_yield": None, "name": ticker, "sector": "N/A",
            "description": "", "error": "no price after retry"}


_yahoo_cache_tls = threading.local()


def get_price_maybe_cached(ticker: str, cache: dict, force_refresh: bool = False) -> dict:
    """
    Cache-aware price/market-cap lookup -- the Yahoo equivalent of
    edgar_scan_core._get_facts_maybe_cached(). Serves a fresh-enough
    cached entry if one exists and force_refresh isn't set; otherwise
    calls fetch_price_and_market_cap_live() and stashes what should be
    persisted on _yahoo_cache_tls for the caller to pick up.
    """
    entry = None if force_refresh else (cache or {}).get(ticker.upper())
    if entry and _yahoo_cache_entry_fresh(entry):
        _yahoo_cache_tls.update = None
        return entry["data"]

    data = fetch_price_and_market_cap_live(ticker)
    if data.get("error") or data.get("price") is None:
        # Don't cache a miss -- same reasoning as the EDGAR cache: a
        # transient yfinance hiccup shouldn't "poison" the cache for
        # up to 18 hours.
        _yahoo_cache_tls.update = None
    else:
        _yahoo_cache_tls.update = {
            "ticker": ticker.upper(),
            "entry": {"fetched_at": datetime.now(timezone.utc).isoformat(), "data": data},
        }
    return data


def save_yahoo_cache_updates(updates: dict, get_json_fn, put_json_fn) -> list:
    """Same contract and shard-write/retry/pacing logic as
    edgar_scan_core.save_facts_cache_updates() -- see that function for
    the detailed reasoning (GitHub Contents API pacing, SHA-conflict
    retries, returning (path, reason) tuples for real failure
    visibility)."""
    if not updates:
        return []
    by_shard = {}
    for ticker, entry in updates.items():
        by_shard.setdefault(_yahoo_cache_shard_path(ticker), {})[ticker] = entry

    failed_shards = []
    shard_paths = list(by_shard.items())
    for i, (path, shard_updates) in enumerate(shard_paths):
        if i > 0:
            time.sleep(1.0)
        ok = False
        last_msg = ""
        for attempt in range(3):
            if attempt > 0:
                time.sleep(2 ** attempt)
            try:
                existing, _sha, _err = get_json_fn(path)
            except Exception as e:
                existing, last_msg = None, str(e)
                continue
            merged = dict(existing) if existing else {}
            merged.update(shard_updates)
            try:
                ok, last_msg = put_json_fn(
                    path, merged,
                    commit_message=f"Yahoo price cache update — {path} — {len(shard_updates)} ticker(s)",
                )
            except Exception as e:
                ok, last_msg = False, str(e)
            if ok:
                break
        if not ok:
            failed_shards.append((path, last_msg))
    return failed_shards
