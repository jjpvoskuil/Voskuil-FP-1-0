import streamlit as st
import requests
import pandas as pd
from io import StringIO
import time
import threading
import zlib
from datetime import datetime, timezone
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from claude_utils import ask_claude_about_equity
from superinvestor_utils import get_conviction_data, get_superinvestor_conviction
from sec_utils import (
    get_cik, get_ticker_cik_map, fetch_company_facts_with_cik, DEFAULT_WEIGHTS,
    evaluate_buffett_funnel, FUNNEL_THRESHOLDS,
    evaluate_financial_firm_funnel, score_financial_firm_breakdown,
    compute_dcf_value, compute_residual_income_value,
    fetch_price_and_market_cap,
)
from edgar_concept_map import FINANCIAL_SIC_CODES, CYCLICAL_SIC_CODES
from github_store import github_get_json, github_put_json
from watchlist_utils import add_to_watchlist, is_watchlisted
from ui_utils import scroll_to_element
import concurrent.futures

st.set_page_config(page_title="Market Screener — EDGAR", layout="wide")

SCAN_CACHE_PATH = "market_screener_scan_cache.json"

APP_URL = "https://voskuil-fp-1-0-k85bd7afbw8dnqeftzxwbu.streamlit.app"

# ═════════════════════════════════════════════════════════════════════
# EDGAR facts cache (#52 remaining scope)
#
# Caches fetch_company_facts_with_cik()'s normalized output -- the raw
# per-field "latest"/"history" facts, NOT the scan's computed scores --
# for up to EDGAR_FACTS_CACHE_MAX_AGE_DAYS, keyed by ticker, persisted
# to GitHub. Market Screener only (#52 was scoped down to just this
# page -- Equity Scout's single-ticker lookups are already fast enough
# live that caching there wasn't worth the added complexity).
#
# Sharded across a fixed number of files rather than either extreme:
#   - One file per company (the original punch-list wording,
#     historical/{TICKER}.json) means 500-7,000 individual GitHub API
#     writes per scan -- blows past GitHub's rate limits and floods the
#     repo with commits at the current universe size.
#   - One single consolidated file doesn't work either at the top end:
#     a full "All US Common Stocks" scan caching ~7,000 tickers' worth
#     of 10-year history serializes to well over 100MB, over GitHub's
#     hard per-file limit via the Contents API (and a bad idea to diff/
#     commit repeatedly even under that limit).
# Sharding by a stable hash of the ticker keeps each shard small (a few
# MB even at full 7,000-ticker scale) while a single scan still only
# ever touches a small, bounded number of files -- not thousands.
EDGAR_FACTS_CACHE_NUM_SHARDS   = 40
EDGAR_FACTS_CACHE_MAX_AGE_DAYS = 7


def _facts_cache_shard_path(ticker: str) -> str:
    shard = zlib.crc32(ticker.upper().encode()) % EDGAR_FACTS_CACHE_NUM_SHARDS
    return f"edgar_facts_cache/shard_{shard:02d}.json"


def _load_facts_cache_shards(tickers: list) -> tuple:
    """
    Loads every shard file touched by this ticker list, once, up front
    (called from the main thread before the background scan starts --
    same "load once, hand it to the workers" pattern as
    get_ticker_cik_map()). Returns (cache, errors):
      cache  -- {TICKER: {"fetched_at": iso_str, "facts": {...}}}
      errors -- list of "{path}: {message}" strings for any shard that
                came back with a real error (as opposed to a clean 404,
                which just means that shard hasn't been written yet --
                a cold cache, not a problem).

    First version of this discarded github_get_json()'s own error
    message entirely (read into a throwaway `_err` and never looked
    at) -- same class of bug as the original _save_facts_cache_updates()
    silently swallowing write failures. Surfacing read errors here too
    so a load-side problem shows up in the UI instead of just quietly
    looking like "cold cache, fetch everything fresh" with no
    indication anything was wrong.
    """
    shard_paths = sorted({_facts_cache_shard_path(t) for t in tickers})
    cache = {}
    errors = []
    for path in shard_paths:
        data, _sha, err = github_get_json(path)
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
# threads (see _run_stage1_scan_background's ThreadPoolExecutor), one
# ticker at a time per thread, synchronously start to finish. Using a
# thread-local here instead of a shared dict + lock means _worker() can
# read back "did this particular call actually hit EDGAR, and if so
# what did it get back" with zero cross-thread contention -- each
# thread only ever sees its own slot, and it's overwritten fresh on
# every ticker that thread handles.
_facts_cache_tls = threading.local()


def _get_facts_maybe_cached(ticker: str, cik: str, facts_cache: dict, force_refresh: bool) -> dict:
    """
    Cache-aware replacement for a direct fetch_company_facts_with_cik()
    call -- used by fetch_quality_edgar() below. Serves a fresh-enough
    cached entry if one exists and force_refresh isn't set; otherwise
    fetches live from EDGAR as before. Either way, stashes what should
    be persisted (or None, if nothing changed) on _facts_cache_tls for
    the calling worker to pick up -- see its own docstring for why a
    thread-local, not a shared structure, is used here.
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


def _save_facts_cache_updates(updates: dict) -> list:
    """
    updates: {TICKER: {"fetched_at": iso_str, "facts": {...}}} -- newly
    fetched or refreshed entries from the scan that just finished, only
    (tickers served from a still-fresh cache hit are NOT included here,
    since nothing about them needs to be re-persisted). Groups by shard
    and does one GitHub read+write per shard actually touched, not per
    ticker -- mirrors the scan-results cache's "one commit per scan"
    pattern, just per-shard instead of a single file.

    Returns a list of shard paths that failed to save even after
    retries, so the caller can surface that to the user rather than
    silently losing data -- see the retry loop below for why this
    matters in practice, not just in theory.

    CONFIRMED LIVE (first real scan after this feature shipped, S&P
    500 universe): a single scan can touch up to
    EDGAR_FACTS_CACHE_NUM_SHARDS (40) different shard files, and firing
    that many Contents-API writes back to back with no pacing tripped
    GitHub's secondary rate limiting -- 13 of 40 shard PUTs failed
    outright that run. The original version of this function wrapped
    each PUT in a bare try/except and never even looked at
    github_put_json()'s own (ok, msg) return value, so those 13
    failures were silently swallowed: no error, no log, just ~150
    successfully-fetched tickers quietly never making it into the
    cache. Fixed with a real retry loop (github_put_json doesn't raise
    on an HTTP-level failure -- it returns ok=False -- so "except
    Exception" alone was never going to catch this) plus a short pacing
    delay between shards so most scans stay under the limit in the
    first place instead of relying on retries to clean up after it.
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
            # content-creating request per second sustained. Spreading
            # writes out up front means most scans never need the
            # retry loop below at all, instead of firing everything as
            # fast as possible and cleaning up the fallout after.
            time.sleep(1.0)

        ok = False
        last_msg = ""
        for attempt in range(3):
            if attempt > 0:
                time.sleep(2 ** attempt)  # 2s, then 4s
            try:
                existing, _sha, _err = github_get_json(path)
            except Exception as e:
                existing, last_msg = None, str(e)
                continue
            merged = dict(existing) if existing else {}
            merged.update(shard_updates)
            try:
                ok, last_msg = github_put_json(
                    path, merged,
                    commit_message=f"EDGAR facts cache update — {path} — {len(shard_updates)} ticker(s)",
                )
            except Exception as e:
                ok, last_msg = False, str(e)
            if ok:
                break
        if not ok:
            # Best-effort persistence -- a shard that still fails after
            # 3 attempts just means those tickers get re-fetched from
            # EDGAR next time instead of served from cache. Never worth
            # failing the whole scan (whose real output --
            # stage1_results -- already saved successfully by this
            # point) over a cache write, but IS worth surfacing rather
            # than hiding, unlike before.
            failed_shards.append(path)
    return failed_shards


# ── Helper: build context string from results dataframe ──────────────
def build_ms_context(df):
    from claude_utils import get_user_profile as _gup
    _prof = _gup()
    _age  = _prof.get('age', 57)
    _sage = _prof.get('spouse_age', '')
    _wd   = _prof.get('monthly_withdrawal', 8000)
    _pv   = _prof.get('portfolio_val', 3_790_000)
    _inf  = _prof.get('inflation', 4.0)
    _age_str = f"{_age}-year-old" + (f" and spouse age {_sage}" if _sage else "")
    lines = [
        "MARKET SCREEN RESULTS — Voskuil Buffett/Munger Funnel\n",
        f"Investment context: Buffett + Munger concentrated value philosophy. All companies below "
        f"already PASSED a pass/fail checklist (10-yr avg ROIC, 10-yr avg FCF margin, a debt hurdle, "
        f"no share dilution) — this is not a weighted composite score, and there is no forced ranking.",
        f"Investor: {_age_str} | Portfolio: ${_pv/1e6:.1f}M | Monthly target: ${_wd:,.0f} | Inflation assumption: {_inf:.1f}%. Hold horizon 5-10 years.\n",
        f"{len(df)} checklist survivors:\n",
    ]
    for _, row in df.iterrows():
        def f(v, t="pct"):
            if v is None or (isinstance(v, float) and pd.isna(v)): return "N/A"
            if t == "pct":   return f"{v:.1%}"
            if t == "ratio": return f"{v:.1f}x"
            return str(v)
        si_str = ""
        if 'si_holders' in row.index:
            si_str = f" | Superinvestors: {int(row.get('si_holders',0))} holding (conviction {int(row.get('si_score',0))}/100)"
        flags = []
        if row.get('is_cyclical'):     flags.append("CYCLICAL")
        if row.get('limited_history'): flags.append(f"limited history ({row.get('funnel_years_used','?')}y)")
        if row.get('roic_stale'):      flags.append(f"stale ROIC (last reliable {row.get('roic_last_reliable_period','?')}, {row.get('roic_stale_years','?')}y old)")
        flag_str = f" | Flags: {', '.join(flags)}" if flags else ""
        lines.append(
            f"{row['ticker']} ({row.get('name','')}) | 10yr Avg ROIC: {f(row.get('roic_avg'))} | "
            f"10yr Avg FCF Margin: {f(row.get('fcf_margin_avg'))} | "
            f"Debt hurdle cleared: {row.get('debt_hurdle_cleared','?')} "
            f"(Debt/NI {f(row.get('debt_to_ni'),'ratio')}, Debt/CADS {f(row.get('debt_to_cads'),'ratio')}) | "
            f"Dilution check: {'passed' if row.get('dilution_passed') else 'failed'} | "
            f"FCF Yield: {f(row.get('fcf_yield'))} | P/OE: {f(row.get('price_owner_earn'),'ratio')} | "
            f"Div: {f(row.get('dividend_yield'))} | Sector: {row.get('sector','N/A')}{flag_str}{si_str}"
        )
    return "\n".join(lines)




# ── Ticker universe sources ─────────────────────────────────────────────
# FTSE Russell's official Russell 1000/2000 constituent files are
# commercial-license-only (no free API exists). iShares used to publish
# free CSV exports of their tracking ETFs' holdings (IWB/IWM), but that
# direct-download endpoint has since been retired in favor of a
# JavaScript-rendered page that a simple HTTP request can't trigger.
#
# Instead we build a broad, free, market-cap-tiered universe directly
# from Nasdaq Trader's public Symbol Directory files — the same files
# every exchange-listed security is registered in. This isn't an exact
# replica of official Russell membership, but Russell 1000/2000
# membership IS fundamentally a market-cap-rank construction (roughly:
# top ~1,000 US common stocks by float-adjusted market cap = Russell
# 1000; next ~2,000 = Russell 2000), so ranking this universe by market
# cap gives a very close practical approximation — without any
# commercial licensing dependency.
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


def get_sp500_tickers():

    try:
        headers  = {"User-Agent": "Mozilla/5.0 (compatible; VoskuilFP/1.0)"}
        response = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers=headers, timeout=10)
        tables   = pd.read_html(StringIO(response.text))
        tickers  = tables[0]['Symbol'].str.replace('.', '-', regex=False).tolist()
        return tickers
    except Exception as e:
        st.error(f"Could not fetch S&P 500 list: {e}")
        return []


def safe_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ── Stage 1: Quality-only EDGAR fetch (no price needed) ────────────────
# ── SIC industry name lookup ────────────────────────────────────────────
# Uses the complete static SIC code table in sic_codes.py — hand-
# transcribed from the SEC's official published list. SIC codes are
# frozen (last formally revised 1987) and the SEC's own page renders its
# table via JavaScript, so live scraping is unreliable (confirmed after
# two failed attempts: regex parsing, then BeautifulSoup, both returned
# empty results despite the page being reachable). A static file is the
# right call: this data never changes, dropdowns work before any scan
# ever runs, and there's zero network dependency or fetch latency.
#
# SIC codes are hierarchical: first 2 digits = major group (e.g. "28" =
# Chemicals), full 4 digits = specific sub-industry (e.g. "2834" =
# Pharmaceutical Preparations).
from sic_codes import SIC_FULL

# Hardcoded major-group (2-digit) names — the stable top-level SIC
# divisions. Used as a fallback / primary source for the Industry
# dropdown, since not every major group has a clean "X00"-style header
# row in the 4-digit table to derive a name from automatically.
SIC_MAJOR_GROUP_NAMES = {
    "01": "Agricultural Production - Crops",        "02": "Agricultural Production - Livestock",
    "07": "Agricultural Services",                  "08": "Forestry",
    "09": "Fishing, Hunting and Trapping",
    "10": "Metal Mining",                            "12": "Coal Mining",
    "13": "Oil & Gas Extraction",                    "14": "Mining & Quarrying (Nonmetallic)",
    "15": "Building Construction",                   "16": "Heavy Construction",
    "17": "Special Trade Contractors",
    "20": "Food & Kindred Products",                 "21": "Tobacco Products",
    "22": "Textile Mill Products",                   "23": "Apparel & Textile Products",
    "24": "Lumber & Wood Products",                  "25": "Furniture & Fixtures",
    "26": "Paper & Allied Products",                 "27": "Printing & Publishing",
    "28": "Chemicals & Allied Products",             "29": "Petroleum Refining",
    "30": "Rubber & Plastics Products",              "31": "Leather Products",
    "32": "Stone, Clay, Glass, Concrete",            "33": "Primary Metal Industries",
    "34": "Fabricated Metal Products",               "35": "Industrial Machinery & Equipment",
    "36": "Electronic & Electrical Equipment",       "37": "Transportation Equipment",
    "38": "Instruments & Measuring Devices",         "39": "Misc. Manufacturing",
    "40": "Railroad Transportation",                 "41": "Local Transit",
    "42": "Trucking & Warehousing",                  "44": "Water Transportation",
    "45": "Air Transportation",                      "46": "Pipelines (No Natural Gas)",
    "47": "Transportation Services",
    "48": "Communications",                          "49": "Electric, Gas & Sanitary Services",
    "50": "Wholesale Trade - Durable Goods",         "51": "Wholesale Trade - Nondurable Goods",
    "52": "Building Materials & Garden Supplies",    "53": "General Merchandise Stores",
    "54": "Food Stores",                             "55": "Auto Dealers & Gas Stations",
    "56": "Apparel & Accessory Stores",              "57": "Home Furniture & Equipment Stores",
    "58": "Eating & Drinking Places",                "59": "Miscellaneous Retail",
    "60": "Depository Institutions (Banks)",         "61": "Non-Depository Credit Institutions",
    "62": "Security & Commodity Brokers",            "63": "Insurance Carriers",
    "64": "Insurance Agents & Brokers",              "65": "Real Estate",
    "67": "Holding & Investment Offices",
    "70": "Hotels & Lodging",                        "72": "Personal Services",
    "73": "Business Services",                       "75": "Auto Repair Services",
    "76": "Misc. Repair Services",                   "78": "Motion Pictures",
    "79": "Amusement & Recreation",                  "80": "Health Services",
    "81": "Legal Services",                          "82": "Educational Services",
    "83": "Social Services",                         "84": "Museums & Botanical/Zoological Gardens",
    "86": "Membership Organizations",                "87": "Engineering & Management Services",
    "88": "American Depositary Receipts / Foreign Govts",  "89": "Services, NEC",
    "91": "Executive & Legislative Government",      "92": "Justice, Public Order & Safety",
    "93": "Public Finance, Taxation & Monetary Policy", "94": "Administration of Human Resources",
    "95": "Environmental Quality & Housing",         "96": "Administration of Economic Programs",
    "97": "International Affairs",                   "99": "Nonclassifiable Establishments",
}


@st.cache_data(ttl=604800)  # static data — cache for a week regardless
def fetch_sic_industry_map() -> dict:
    """
    Returns the SIC code lookup, built entirely from the static table.
    Kept as a function (rather than module-level constants used
    directly) so the rest of the page's call sites — which expect a
    dict with "full" and "major" keys — don't need to change.

    Returns:
    {
        "full":  {"2834": "Pharmaceutical Preparations", ...},  # 4-digit, all 444 SEC codes
        "major": {"28": "Chemicals & Allied Products", ...},     # 2-digit
    }
    """
    return {
        "full":  SIC_FULL,
        "major": SIC_MAJOR_GROUP_NAMES,
    }


def sic_major_name(sic: str, sic_map: dict) -> str:
    """Get the major-group (2-digit) industry name for a SIC code."""
    if not sic or len(sic) < 2:
        return "Unclassified"
    return sic_map.get("major", {}).get(sic[:2], f"SIC {sic[:2]}xx")


def sic_full_name(sic: str, sic_map: dict, sic_registry: dict = None) -> str:
    """
    Get the full 4-digit sub-industry name for a SIC code, from the
    static SEC table. Falls back to "SIC {code}" for the rare code not
    in the official ~444-row table (e.g. some obscure or retired codes).
    sic_registry is accepted for backward compatibility with call sites
    but is no longer needed now that the static table covers names
    directly.
    """
    if not sic:
        return "Unclassified"
    code = sic.zfill(4)
    name = sic_map.get("full", {}).get(code)
    if name:
        return name
    return f"SIC {code}"


def sub_industries_for_major(major_names, sic_map: dict, sic_registry: dict = None) -> list:
    """
    Returns sorted sub-industry names belonging to the given major
    industry group(s), sourced from the COMPLETE static SIC table.
    Fully populated before any scan ever runs.

    major_names: list of major industry names, or empty list for "all".
    """
    full_map  = sic_map.get("full", {})
    major_map = sic_map.get("major", {})
    major_set = set(major_names) if major_names else None  # None = no filter

    names = set()
    for code, title in full_map.items():
        code2 = code[:2] if len(code) >= 2 else code
        this_major = major_map.get(code2, f"SIC {code2}xx")
        if major_set is None or this_major in major_set:
            names.add(title)
    return sorted(names)


def market_cap_tier(cap) -> str:
    """Classify a market cap value into a size tier label."""
    if cap is None:
        return "Unknown"
    if cap >= 10_000_000_000:
        return "Large Cap (≥$10B)"
    if cap >= 2_000_000_000:
        return "Mid Cap ($2B–$10B)"
    if cap >= 300_000_000:
        return "Small Cap ($300M–$2B)"
    return "Micro Cap (<$300M)"


def _fetch_market_cap_and_sector(ticker: str):
    """
    Shared market cap / GICS sector lookup — used by both the standard
    and bank/insurer alt-scoring paths in fetch_quality_edgar() so a
    ticker's market-cap-tier and Sector filters work identically either
    way. fast_info covers market cap cheaply; sector requires the fuller
    .info call, which is slower — same cost/tradeoff noted where this
    logic used to live inline.
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
        market_cap, sector      = _fetch_market_cap_and_sector(ticker)
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
    # pricing/scoring. See _fetch_market_cap_and_sector() for the
    # fast_info/.info cost tradeoff notes.
    market_cap, sector = _fetch_market_cap_and_sector(ticker)

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
# Background Stage 1 scan (#69)
#
# Streamlit reruns the whole SCRIPT on every interaction (a click, a
# widget change — even ones that look unrelated). If Stage 1 runs
# inline in that script execution, ANY interaction — including
# navigating to another page, or an accidental click while scrolling —
# triggers a rerun that abandons the in-progress scan.
#
# Fix: run Stage 1 in a real background thread. Streamlit's rerun
# mechanism only stops/restarts the SCRIPT's own execution thread; a
# thread YOU spawn with threading.Thread(...).start() is a normal OS
# thread in the same process and keeps running regardless of what the
# script does.
#
# CRITICAL DETAIL: progress state must live behind st.cache_resource,
# NOT a bare module-level variable. Streamlit re-executes the page's
# entire script from scratch on every rerun — that's the actual reason
# st.session_state exists at all. A plain `_SCAN_STATE = {...}` literal
# gets silently recreated fresh on every single rerun, completely
# disconnected from whatever a background thread is still writing to;
# the background thread keeps a reference to the OLD dict object while
# the next rerun creates a brand new one with the same name. This was a
# real, confirmed bug in an earlier version of this fix — st.cache_resource
# is Streamlit's actual mechanism for a shared, mutable object that
# survives both reruns and (since it's a global cache) navigation.
#
# IMPORTANT LIMITATION: cache_resource is process-global, not per-
# session — same caveat as before, just via the correct mechanism now.
# Fine for a single-user instance (this app today), but if/when multi-
# user support (#15/#16) happens, this needs to become keyed by
# session/user before it's safe to ship — otherwise one user's scan
# would show up in everyone's browser. Flagging here so it isn't
# forgotten.
#
# ALSO NOTE: this only survives navigation/scrolling/interaction within
# the running app. It does NOT survive an actual app restart (a
# redeploy from a new git push, or Streamlit Cloud recycling the
# container) — the whole Python process, and this in-memory state with
# it, goes away in that case. That's a different, harder problem
# (would need a real out-of-process job queue) and is out of scope here.
# ═════════════════════════════════════════════════════════════════════

@st.cache_resource
def _get_scan_lock() -> threading.Lock:
    """Same Lock instance every call, across reruns and sessions — see the big comment above."""
    return threading.Lock()


@st.cache_resource
def _get_scan_state() -> dict:
    """Same dict instance every call, across reruns and sessions — see the big comment above."""
    return {
        "active":            False,
        "cancel_requested":  False,
        "universe":          None,
        "total":             0,
        "completed":         0,
        "stage1_results":    [],
        "fetch_failures":    [],
        "no_xbrl_tickers":   [],
        "waterfall":         {},
        "started_at":        None,
        "finished_at":       None,
        "cancelled":         False,
        "error":             None,
        "github_save_ok":    None,
        "github_save_msg":   "",
        "facts_cache_hits":   0,
        "facts_cache_misses": 0,
        "facts_cache_save_failures": [],
        "facts_cache_loaded_count": 0,
        "facts_cache_load_errors":  [],
    }


def _scan_snapshot() -> dict:
    """Thread-safe read of the full background scan state."""
    with _get_scan_lock():
        return dict(_get_scan_state())


def _start_stage1_scan_background(tickers_to_scan, ticker_cik_map, funnel_thresholds,
                                   skip_financials, universe_label, cache_path,
                                   facts_cache=None, force_refresh_facts=False,
                                   facts_cache_load_errors=None):
    """
    Initializes the cache_resource-backed scan state and spawns the background thread. Called
    from the MAIN script thread (the button-click handler), NOT from
    within the background thread itself — this matters: the caller
    calls st.rerun() immediately after this returns, and that rerun
    needs to see active=True right away. If "active" were set inside
    the background thread instead, there'd be a race — the thread might
    not get scheduled before the rerun re-checks the state, and the
    page would show nothing at all (this was an actual bug: the scan
    would appear to do nothing because the very first rerun after
    launching it still saw the stale "not active" state).
    """
    with _get_scan_lock():
        _get_scan_state().update({
            "active": True, "cancel_requested": False, "cancelled": False,
            "universe": universe_label, "total": len(tickers_to_scan),
            "completed": 0, "stage1_results": [], "fetch_failures": [],
            "no_xbrl_tickers": [],
            "waterfall": {
                "no_cik": 0, "no_xbrl_data": 0, "fetch_failed": 0,
                "excluded_fcf": 0, "excluded_financial": 0,
                "failed_roic": 0, "failed_fcf_margin": 0,
                "failed_debt": 0, "failed_dilution": 0, "passed": 0,
                # Bank/insurer alt framework (#36) — separate leg counters
                # since the checklist itself is different (ROE/quality/
                # capital/dilution, not ROIC/FCF-margin/debt/dilution).
                "failed_financial_roe": 0, "failed_financial_quality": 0,
                "failed_financial_capital": 0, "failed_financial_dilution": 0,
            },
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None, "error": None,
            "github_save_ok": None, "github_save_msg": "",
            "facts_cache_hits": 0, "facts_cache_misses": 0,
            "facts_cache_save_failures": [],
            "facts_cache_loaded_count": len(facts_cache or {}),
            "facts_cache_load_errors": list(facts_cache_load_errors or []),
        })
    threading.Thread(
        target=_run_stage1_scan_background,
        args=(tickers_to_scan, ticker_cik_map, funnel_thresholds, skip_financials, universe_label, cache_path,
              facts_cache, force_refresh_facts),
        daemon=True,
    ).start()


def _run_stage1_scan_background(tickers_to_scan, ticker_cik_map, funnel_thresholds,
                                 skip_financials, universe_label, cache_path,
                                 facts_cache=None, force_refresh_facts=False):
    """
    Does the actual scanning work on a background thread. State is
    already initialized by _start_stage1_scan_background() (on the main
    thread, before this thread was even spawned — see that function's
    docstring for why the ordering matters) — this function just does
    the work and updates progress as it goes. Mirrors the logic that
    used to run inline in the main script (waterfall tally,
    fetch_quality_edgar per ticker), but writes progress into the
    cache_resource-backed scan state instead of directly rendering Streamlit
    widgets — the main script's fragment (see render section below)
    reads this state to display live progress from whatever page/session
    happens to be looking at it.
    """
    def _worker(ticker):
        cik = ticker_cik_map.get(ticker.upper())
        if not cik:
            return {"_status": "no_cik", "ticker": ticker}
        result = fetch_quality_edgar(ticker, cik, funnel_thresholds,
                                      facts_cache=facts_cache, force_refresh=force_refresh_facts)
        # Picked up from the thread-local slot _get_facts_maybe_cached()
        # just wrote to, still on this same worker thread -- see that
        # function's docstring. None means this ticker was served from
        # cache (nothing new to persist).
        result["_facts_cache_update"] = getattr(_facts_cache_tls, "update", None)
        return result

    facts_cache_updates = {}  # collected on the consumer side below (single-threaded, no lock needed)

    try:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=8)
        futures = {executor.submit(_worker, t): t for t in tickers_to_scan}
        try:
            for future in concurrent.futures.as_completed(futures):
                with _get_scan_lock():
                    if _get_scan_state()["cancel_requested"]:
                        break
                try:
                    data = future.result()
                except Exception as e:
                    data = {"_status": "fetch_failed", "ticker": futures[future], "reason": str(e)}

                status = data.get("_status") if data else "no_cik"

                # Cache bookkeeping -- pop before status handling below
                # so this internal key never leaks into stage1_results/
                # fetch_failures/CSV export. Only meaningful for tickers
                # that actually reached a facts fetch (status != "no_cik").
                _cache_update = data.pop("_facts_cache_update", None) if data else None
                if status != "no_cik":
                    if _cache_update:
                        facts_cache_updates[_cache_update["ticker"]] = _cache_update["entry"]

                with _get_scan_lock():
                    _get_scan_state()["completed"] += 1
                    wf = _get_scan_state()["waterfall"]
                    if status != "no_cik":
                        if _cache_update:
                            _get_scan_state()["facts_cache_misses"] += 1
                        else:
                            _get_scan_state()["facts_cache_hits"] += 1

                    if status == "no_cik":
                        wf["no_cik"] += 1
                    elif status == "no_xbrl_data":
                        wf["no_xbrl_data"] += 1
                        _get_scan_state()["no_xbrl_tickers"].append(data)
                    elif status == "fetch_failed":
                        wf["fetch_failed"] += 1
                        _get_scan_state()["fetch_failures"].append(data)
                    elif status == "excluded_fcf":
                        wf["excluded_fcf"] += 1
                    else:
                        # "evaluated" — reached the checklist
                        subtype = data.get("financial_subtype")
                        if skip_financials and data.get("is_financial") and subtype not in ("bank", "insurance"):
                            # Only brokers/REITs/real estate/investment
                            # offices get hard-excluded now — banks and
                            # insurers have their own alt framework (#36)
                            # and are evaluated on their own terms below.
                            wf["excluded_financial"] += 1
                        elif subtype in ("bank", "insurance"):
                            funnel = data.get("funnel", {})
                            if not funnel.get("roe_pass"):      wf["failed_financial_roe"]      += 1
                            if not funnel.get("quality_pass"):  wf["failed_financial_quality"]  += 1
                            if not funnel.get("capital_pass"):  wf["failed_financial_capital"]  += 1
                            if not funnel.get("dilution_pass"): wf["failed_financial_dilution"] += 1
                            if data.get("funnel_passed"):
                                wf["passed"] += 1
                                _get_scan_state()["stage1_results"].append(data)
                        else:
                            funnel = data.get("funnel", {})
                            if not funnel.get("roic_pass"):       wf["failed_roic"]       += 1
                            if not funnel.get("fcf_margin_pass"): wf["failed_fcf_margin"] += 1
                            if not funnel.get("debt_pass"):       wf["failed_debt"]       += 1
                            if not funnel.get("dilution_pass"):   wf["failed_dilution"]   += 1
                            if data.get("funnel_passed"):
                                wf["passed"] += 1
                                _get_scan_state()["stage1_results"].append(data)
        finally:
            # cancel_futures needs Python 3.9+; falls back gracefully if unsupported.
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)
    except Exception as e:
        with _get_scan_lock():
            _get_scan_state()["error"] = str(e)

    with _get_scan_lock():
        was_cancelled = _get_scan_state()["cancel_requested"]
        _get_scan_state()["cancelled"]  = was_cancelled
        _get_scan_state()["active"]     = False
        _get_scan_state()["finished_at"] = datetime.now(timezone.utc).isoformat()
        stage1_results = list(_get_scan_state()["stage1_results"])
        total_scanned  = _get_scan_state()["completed"] if was_cancelled else _get_scan_state()["total"]

    # Persist any newly-fetched EDGAR facts (#52) regardless of whether
    # the scan was cancelled partway or found zero survivors -- tickers
    # already fetched fresh from EDGAR this run are still valid, still
    # worth caching for 7 days, and shouldn't be thrown away just
    # because the SCAN itself didn't finish or pass anything.
    if facts_cache_updates:
        _failed_shards = _save_facts_cache_updates(facts_cache_updates)
        if _failed_shards:
            with _get_scan_lock():
                _get_scan_state()["facts_cache_save_failures"] = _failed_shards

    # Persist to GitHub from the background thread itself — this way it
    # happens exactly once regardless of how many browser tabs/sessions
    # are watching, rather than each session trying to save its own copy.
    if stage1_results and not was_cancelled:
        _scan_timestamp = datetime.now(timezone.utc).isoformat()
        _ok, _msg = github_put_json(
            cache_path,
            {
                "universe":              universe_label,
                "scan_timestamp":        _scan_timestamp,
                "total_tickers_scanned": total_scanned,
                "stage1_survivors":      stage1_results,
            },
            commit_message=f"Market screener scan cache — {universe_label} — {len(stage1_results)} survivors",
        )
        with _get_scan_lock():
            _get_scan_state()["github_save_ok"]  = _ok
            _get_scan_state()["github_save_msg"] = _msg if not _ok else _scan_timestamp


def _build_waterfall_rows(waterfall: dict, total_tickers: int) -> list:
    """Shared waterfall row builder — used for both the live in-progress
    display and the persisted post-scan display, so they can't drift."""
    reached_checklist = (
        total_tickers - waterfall.get("no_cik", 0) - waterfall.get("no_xbrl_data", 0)
        - waterfall.get("fetch_failed", 0) - waterfall.get("excluded_fcf", 0)
    )
    return [
        ("Total scanned",                                              total_tickers, None),
        ("→ No CIK match in EDGAR",                                    waterfall.get("no_cik", 0), total_tickers),
        ("→ No XBRL data at all (404 — permanent, not a rate limit)",  waterfall.get("no_xbrl_data", 0), total_tickers),
        ("→ EDGAR fetch failed (rate-limited/timeout — transient)",    waterfall.get("fetch_failed", 0), total_tickers),
        ("→ Excluded: latest-year FCF ≤ 0",                            waterfall.get("excluded_fcf", 0), total_tickers),
        ("= Reached the checklist",                                    reached_checklist, total_tickers),
        ("→ Excluded: broker/REIT/real estate/other financial",       waterfall.get("excluded_financial", 0), reached_checklist),
        ("→ Failed ROIC leg",                                          waterfall.get("failed_roic", 0), reached_checklist),
        ("→ Failed FCF Margin leg",                                    waterfall.get("failed_fcf_margin", 0), reached_checklist),
        ("→ Failed Debt leg (both hurdles)",                           waterfall.get("failed_debt", 0), reached_checklist),
        ("→ Failed Dilution leg",                                      waterfall.get("failed_dilution", 0), reached_checklist),
        ("→ Bank/Insurer (#36): failed ROE leg",                      waterfall.get("failed_financial_roe", 0), reached_checklist),
        ("→ Bank/Insurer (#36): failed quality leg (efficiency/combined ratio)", waterfall.get("failed_financial_quality", 0), reached_checklist),
        ("→ Bank/Insurer (#36): failed capital leg",                  waterfall.get("failed_financial_capital", 0), reached_checklist),
        ("→ Bank/Insurer (#36): failed dilution leg",                 waterfall.get("failed_financial_dilution", 0), reached_checklist),
        ("= Passed all legs (standard OR bank/insurer framework)",     waterfall.get("passed", 0), reached_checklist),
    ]


def _render_waterfall_and_failures(waterfall, total_tickers, num_passed, fetch_failures, no_xbrl_tickers, expanded_default=False):
    """Shared renderer so the waterfall/failure expanders look identical
    whether shown live (mid-scan) or after the fact (persisted in
    session_state, e.g. when the user navigates back to this page)."""
    with st.expander(f"📊 Waterfall: how {total_tickers} tickers became {num_passed} survivors", expanded=expanded_default):
        wf_rows = _build_waterfall_rows(waterfall, total_tickers)
        st.caption(
            "Note: the 'Failed X leg' rows are NOT mutually exclusive — a company can fail more than "
            "one leg at once, so they won't sum to (Reached checklist − Passed). Banks and insurers "
            "(#36) run through a separate framework — ROE/quality-leg/capital/dilution instead of "
            "ROIC/FCF-margin/debt/dilution — since standard metrics don't describe a leveraged balance "
            "sheet; their leg results are tallied separately but count toward the same 'Passed' total. "
            "Brokers/REITs/real estate/investment offices still don't have an alt framework and are "
            "excluded outright when the toggle is on. 'No XBRL data' and 'fetch failed' are DIFFERENT "
            "problems — see the two expanders below."
        )
        _wf_df = pd.DataFrame([
            {"Stage": label, "Count": count,
             "% of denominator": f"{count/denom:.1%}" if denom else "N/A"}
            for label, count, denom in wf_rows
        ])
        st.dataframe(_wf_df, hide_index=True, use_container_width=True)

    if fetch_failures:
        with st.expander(f"⚠️ {len(fetch_failures)} TRANSIENT fetch failures — worth retrying"):
            st.caption(
                "These tickers errored out (SEC rate-limited us, timed out, etc.) before the checklist "
                "could even run — they are neither 'passed' nor 'failed', just unknown. If this list is "
                "long, SEC's fair-access limit was likely hit mid-scan; re-running (especially at a quieter "
                "time, or with a smaller universe) should get most of them through."
            )
            _fail_reasons = {}
            for f in fetch_failures:
                _fail_reasons.setdefault(f.get("reason", "unknown"), []).append(f.get("ticker", "?"))
            for reason, tickers in sorted(_fail_reasons.items(), key=lambda kv: -len(kv[1])):
                st.markdown(f"**{len(tickers)}x** — {reason}")
                st.caption(", ".join(tickers[:30]) + (f" … +{len(tickers)-30} more" if len(tickers) > 30 else ""))

    if no_xbrl_tickers:
        with st.expander(f"ℹ️ {len(no_xbrl_tickers)} tickers with NO XBRL data — permanent, re-running won't help"):
            st.caption(
                "These CIKs returned 404 on Company Facts — there's simply no XBRL data to fetch, so "
                "retrying changes nothing. Almost always one of: (1) a closed-end fund or other "
                "registered investment company (they file N-CSR, not a standard 10-K, so they never "
                "populate this API), (2) a preferred share, warrant, unit, or rights listing riding on "
                "a parent company's CIK, or (3) a stale/incorrect ticker→CIK mapping in the Nasdaq "
                "Trader universe file. If a name you specifically care about shows up here, it's worth "
                "checking by hand — otherwise these are expected noise in a full-market scan and safe "
                "to ignore."
            )
            _tickers_only = [f.get("ticker", "?") for f in no_xbrl_tickers]
            st.caption(", ".join(_tickers_only[:60]) + (f" … +{len(_tickers_only)-60} more" if len(_tickers_only) > 60 else ""))


@st.fragment(run_every=2)
def _render_scan_progress_fragment():
    """
    Auto-refreshing (every 2s) progress display for an active background
    scan. Only re-executes THIS fragment, not the whole page, while the
    scan is active — that's what lets the rest of the page (and other
    pages) stay fully interactive while Stage 1 runs. Once the
    background scan finishes, triggers a full app rerun (st.rerun()) so
    the main script can ingest the results — a fragment rerun alone
    can't do that, per Streamlit's fragment model.
    """
    snap = _scan_snapshot()
    if not snap["active"]:
        st.success("Scan finished — loading results...")
        st.rerun()
        return

    completed, total = snap["completed"], snap["total"]
    pct = (completed / total) if total else 0
    st.progress(pct)
    wf = snap["waterfall"]
    st.markdown(
        f"⏳ Stage 1 running in the background — {completed} of {total} ({int(pct*100)}%) — "
        f"{wf.get('passed', 0)} candidates so far"
        + (f", {len(snap['fetch_failures'])} transient errors" if snap["fetch_failures"] else "")
    )
    st.caption(
        "This keeps running even if you navigate to other pages or close this tab — come back "
        "anytime to check progress. (It does NOT survive an app restart/redeploy.)"
    )
    _loaded_count = snap.get("facts_cache_loaded_count", 0)
    st.caption(f"📦 EDGAR facts cache: {_loaded_count:,} cached ticker(s) loaded from GitHub before this scan started.")
    _load_errs = snap.get("facts_cache_load_errors", [])
    if _load_errs:
        with st.expander(f"⚠️ {len(_load_errs)} cache shard(s) failed to load"):
            st.caption(
                "These shards errored out on read (scan just fetches those tickers fresh from "
                "EDGAR instead, same as a cold cache):"
            )
            for e in _load_errs[:10]:
                st.caption(f"- {e}")
            if len(_load_errs) > 10:
                st.caption(f"… +{len(_load_errs)-10} more")
    if st.button("🛑 Cancel Scan", key="ms_cancel_scan_btn"):
        with _get_scan_lock():
            _get_scan_state()["cancel_requested"] = True
        st.warning("Cancelling — will stop after in-flight requests finish (a few seconds).")


# ── Stage 2: Price + final full scoring for survivors only ─────────────
def fetch_price_data(ticker: str) -> dict:
    """Lightweight yfinance price/market cap/dividend fetch — Stage 2 only.

    (2026-07-23, superseded same day) This used to be its own separate
    copy of the price-fetch logic, with its own retry loop -- and a real
    bug: it called _normalize_dividend_yield() without that name ever
    being imported into this module. Every successful yfinance fetch
    NameError'd while building the return dict, got swallowed by the
    broad except below, retried once, NameError'd again, and returned
    all-None -- meaning current price silently failed for EVERY ticker
    in Stage 2, not just ALL (confirmed by reproducing the exact
    NameError in isolation). Now a thin wrapper around
    sec_utils.fetch_price_and_market_cap() -- the SAME cached,
    retrying, correctly-scoped implementation Dashboard/Equity Scout/
    Compare Stocks/Watchlist already use, so there's exactly one price-
    fetch implementation for the whole app rather than two that can
    silently drift (again).
    """
    d = fetch_price_and_market_cap(ticker)
    return {
        "price":          d.get("price"),
        "market_cap":     d.get("market_cap"),
        "shares":         d.get("shares"),
        "dividend_yield": d.get("dividend_yield"),
        "sector":         d.get("sector", "N/A"),
    }



def hurdle_badge(cleared: str):
    """Icon + label for which debt hurdle(s) a funnel survivor cleared."""
    return {
        "both":    ("💪", "Both debt hurdles"),
        "simple":  ("✓",  "Simple debt hurdle only"),
        "refined": ("✓",  "Refined debt hurdle only"),
    }.get(cleared, ("?", "Debt hurdle unclear"))


# ── Page UI ──────────────────────────────────────────────────────────
_title_col, _info_col = st.columns([8, 1])
with _title_col:
    st.title("📡 Market Screener — EDGAR")
    st.caption("Two-stage screen: a Buffett/Munger quality checklist first via SEC EDGAR (free, no rate limits at this scale), valuation second via live pricing.")
with _info_col:
    with st.popover("❓ How this works", use_container_width=True):
        st.markdown("""
**Stage 1 — Buffett/Munger Checklist (pass/fail, not a weighted score)**

A company must clear all four of these to survive Stage 1. This is a
checklist, not a composite score — there's no partial credit for being
strong on one leg and weak on another.

| Check | Rule | Why |
|---|---|---|
| **ROIC** | 10-yr avg > 15% | Sustained high returns on capital are the clearest signal of a durable moat |
| **FCF Margin** | 10-yr avg > 10% | Quality-of-revenue check — is the business actually converting sales to cash |
| **Debt** | Debt/Net Income < 3.0x **OR** Debt/CADS < 3.0x | Two independent solvency checks run in parallel; passing *either* clears the gate — see below |
| **Dilution** | Shares outstanding today ≤ shares 5 years ago | Buybacks or a flat share count — management isn't funding itself by diluting you |

**The two debt hurdles, explained:**
- *Simple:* Total Debt (LT + ST) ÷ Net Income — cheap to compute, accrual-basis.
- *Refined:* Total Debt ÷ **CADS** (Cash Available for Debt Service = Operating Income + D&A − Capex) — a cash-basis, pre-interest measure that doesn't unfairly penalize businesses (like insurers) that responsibly leverage negative working-capital float.

A survivor's card shows which hurdle it cleared: 💪 *both*, or ✓ *one*.

**Minimum history:** needs at least 5 annual observations to compute an
average at all. Companies with less than the full 10 years are flagged
**"Limited History (Xy)"** with the actual year count shown, rather than
excluded outright or silently blended in with true 10-year track records.

**ROIC reliability guard:** a year is only counted toward the 10-yr ROIC
average if invested capital (equity + debt) is positive and at least
half the magnitude of that year's net income. This specifically targets
aggressive-buyback compounders (VeriSign, Domino's, AutoZone-type
businesses) whose book equity is negative or near zero — for these,
invested capital can swing through zero year to year, and an unguarded
average can produce a nonsense figure (confirmed case: VeriSign's raw
10-yr avg ROIC computed as +273% against a -87% latest-year figure for
the same business). Companies where this guard is active are flagged
**"📊 Negative Equity"** on their card regardless of whether it happened
to distort their current ROIC figure, since the underlying capital
structure is worth knowing either way.

**ROIC staleness flag:** excluding unreliable years (above) can leave the
average built entirely from OLDER history if a company's most recent
years happen to be the unreliable ones — the average would still look
strong, but it's silently going quiet on anything recent. Flagged
**"🕰️ Stale ROIC"** whenever the most recent reliable year is 2+ years
behind the company's actual latest filing, showing exactly which year
and how old it is, so a strong-looking average doesn't get mistaken for
a current one.

**Explicitly excluded from Stage 1** (by design, not oversight):
- *Gross Margin* — too context-dependent to be a universal moat signal (a 90%-margin business with no moat and a 13%-margin business with a deep one can both mislead a GM-based filter).
- *FCF Yield / Price-Owner-Earnings* — these are valuation metrics, not quality metrics. They're computed in **Stage 2** once a live price is available, as a secondary check — quality first, then "is the price reasonable."
- *Financial firms* (banks, insurers, brokers) — excluded by default (toggle below); standard FCF/debt metrics don't mean the same thing for their balance sheets.
- *Cyclicals* — not excluded, just flagged ⚠️; a single-period or even a 10-year average can still be mid-cycle-influenced.

**Ranking:** Stage 1 survivors aren't force-ranked by a composite score.
Use the "Sort results by" control below the results to sort manually —
by ROIC average, FCF margin average, ticker, or years of history.
        """)
st.info(
    "**🏛️ EDGAR Validation Page** — the Stage 1 checklist (ROIC, FCF Margin, Debt, Dilution) "
    "comes directly from SEC Company Facts API, no price needed. Only checklist survivors get "
    "a live price lookup in Stage 2 for FCF Yield and Price/Owner Earnings, shown as a secondary "
    "valuation reference — this is what makes a full-market scan practical."
)
st.divider()

# ── Funnel threshold reset/apply handling ───────────────────────────
if "committed_funnel_thresholds" not in st.session_state:
    st.session_state.committed_funnel_thresholds = FUNNEL_THRESHOLDS.copy()

with st.expander("⚙️ Customize Funnel Thresholds", expanded=False):
    st.caption(
        "These are the Stage 1 checklist hurdles themselves (not a weighted score) — "
        "tune them and re-run Stage 1 to change who survives the funnel."
    )
    ft = st.session_state.committed_funnel_thresholds

    tc1, tc2 = st.columns(2)
    with tc1:
        t_roic = st.number_input("Min 10-yr avg ROIC (%)", min_value=0.0, max_value=100.0,
                                  value=ft["roic_avg_min"] * 100, step=1.0, key="ft_roic") / 100
        t_fcfm = st.number_input("Min 10-yr avg FCF Margin (%)", min_value=0.0, max_value=100.0,
                                  value=ft["fcf_margin_avg_min"] * 100, step=1.0, key="ft_fcfm") / 100
        t_dni  = st.number_input("Max Debt / Net Income (simple hurdle)", min_value=0.0, max_value=20.0,
                                  value=ft["debt_to_ni_max"], step=0.5, key="ft_dni")
    with tc2:
        t_dcads = st.number_input("Max Debt / CADS (refined hurdle)", min_value=0.0, max_value=20.0,
                                   value=ft["debt_to_cads_max"], step=0.5, key="ft_dcads")
        t_minyr = st.number_input("Min years of history required", min_value=1, max_value=10,
                                   value=ft["min_history_years"], step=1, key="ft_minyr")
        t_dilyr = st.number_input("Dilution lookback (years)", min_value=1, max_value=10,
                                   value=ft["dilution_lookback_years"], step=1, key="ft_dilyr")

    fc1, fc2 = st.columns([1.3, 4])
    if fc1.button("↺ Reset to Defaults", key="ms_edgar_reset_thresholds"):
        st.session_state.committed_funnel_thresholds = FUNNEL_THRESHOLDS.copy()
        st.rerun()
    if fc2.button("✅ Apply Thresholds", key="ms_edgar_apply_thresholds", type="primary"):
        st.session_state.committed_funnel_thresholds = {
            "lookback_years":          10,
            "min_history_years":       int(t_minyr),
            "roic_avg_min":            t_roic,
            "fcf_margin_avg_min":      t_fcfm,
            "debt_to_ni_max":          t_dni,
            "debt_to_cads_max":        t_dcads,
            "dilution_lookback_years": int(t_dilyr),
        }
        st.success("Thresholds updated — re-run Stage 1 to apply.")

funnel_thresholds = st.session_state.get("committed_funnel_thresholds", FUNNEL_THRESHOLDS.copy())

with st.expander("🔬 Debug: Verify a Single Ticker", expanded=False):
    st.caption(
        "Runs the exact same funnel checklist a full scan uses, against one ticker, and shows every "
        "underlying number — not just pass/fail. Useful for sanity-checking against companies you "
        "already have a view on (e.g. a mega-cap you'd expect to clear the debt/dilution checks "
        "easily, or a business you'd expect to fail on margin quality) before trusting a full scan."
    )
    dbg_col1, dbg_col2 = st.columns([2, 1])
    with dbg_col1:
        dbg_ticker = st.text_input("Ticker", value="", placeholder="e.g. MSFT", key="ms_debug_ticker").strip().upper()
    with dbg_col2:
        st.write("")
        st.write("")
        dbg_run = st.button("Run Checklist", key="ms_debug_run", use_container_width=True)

    if dbg_run and dbg_ticker:
        with st.spinner(f"Fetching {dbg_ticker} from EDGAR..."):
            dbg_cik, dbg_cik_err = get_cik(dbg_ticker)
            if not dbg_cik:
                st.error(f"Could not resolve CIK for {dbg_ticker}: {dbg_cik_err}")
            else:
                dbg_facts = fetch_company_facts_with_cik(dbg_ticker, dbg_cik)
                if dbg_facts.get("error"):
                    st.error(f"EDGAR fetch failed: {dbg_facts['error']}")
                else:
                    dbg_latest  = dbg_facts.get("latest", {})
                    dbg_meta    = dbg_facts.get("meta", {})
                    dbg_history = dbg_facts.get("history", {})
                    dbg_subtype = dbg_meta.get("financial_subtype")

                    def _leg_row(label, passed, actual_str, rule_str, years_str=""):
                        icon = "✅" if passed else "❌"
                        st.markdown(f"{icon} **{label}** — {actual_str} (need {rule_str}){years_str}")

                    # (2026-07-23) This tool checked quality-funnel legs
                    # only -- no price, no DCF, nothing about valuation at
                    # all, on the one page whose whole job is letting the
                    # owner sanity-check a single ticker against what a
                    # full scan would produce. A full scan's own results
                    # table has shown Margin of Safety since the DCF-
                    # everywhere rollout; this debug tool never got it,
                    # so there was no way to check a ticker's MoS here
                    # even though the checklist legs right next to it
                    # were fully verifiable. Added: same compute_dcf_value()
                    # call every other page uses, with a live price fetch
                    # (this tool doesn't otherwise touch price at all).
                    dbg_price_data = fetch_price_data(dbg_ticker)
                    # (2026-07-23) yfinance's "sharesOutstanding" has a
                    # documented history of intermittently returning None
                    # for a given ticker even on retried requests --
                    # confirmed live for ALL specifically (a data gap in
                    # Yahoo's response, not a network blip). EDGAR already
                    # gives a reliable diluted share count regardless of
                    # what yfinance does -- same fallback
                    # fetch_fundamentals_edgar() already uses elsewhere.
                    dbg_shares = dbg_price_data.get("shares") or dbg_latest.get("diluted_shares")
                    dbg_dcf = compute_dcf_value({
                        **dbg_latest,
                        "price":      dbg_price_data.get("price"),
                        "shares":     dbg_shares,
                        "market_cap": dbg_price_data.get("market_cap"),
                        "_history":   {"fcf": dbg_history.get("fcf", [])},
                    })
                    # (2026-07-23, owner: "is there another way to
                    # calculate the IV for insurers that is accurate?")
                    # FCF-DCF above always errors for a bank/insurer by
                    # design (see compute_residual_income_value()'s
                    # module docstring for why). Residual income model
                    # computed here too so the financial-firm branch can
                    # show real numbers instead of "N/A".
                    dbg_ri = compute_residual_income_value({
                        **dbg_latest,
                        "price":      dbg_price_data.get("price"),
                        "shares":     dbg_shares,
                        "market_cap": dbg_price_data.get("market_cap"),
                        "_latest":    dbg_latest,
                        "_history":   {"roe": dbg_history.get("roe", [])},
                    })

                    def _show_dcf_block():
                        _is_fin = dbg_subtype in ("bank", "insurance")

                        if _is_fin:
                            # ── Residual income (single + multi stage),
                            # shown side by side rather than picking one --
                            # a big gap between them is itself the useful
                            # signal (today's ROE running well off this
                            # company's own normal range), not just a
                            # number to reconcile away. See
                            # compute_residual_income_value()'s module
                            # docstring in sec_utils.py for the full
                            # methodology.
                            st.markdown("#### Valuation (Residual Income Model — bank/insurer)")
                            st.caption(
                                "FCF-based DCF doesn't apply to a leveraged balance-sheet business "
                                "(loan/investment-portfolio volume dominates it, not real cash generation). "
                                "This values book value + the present value of returns earned above the "
                                "cost of equity instead."
                            )
                            _dv1, _dv2 = st.columns(2)
                            with _dv1:
                                _cp = dbg_price_data.get("price")
                                st.metric("Current Price", f"${_cp:.2f}" if _cp else "N/A")
                            with _dv2:
                                _bv = dbg_ri.get("book_value_per_share")
                                st.metric("Book Value/Share", f"${_bv:.2f}" if _bv is not None else "N/A")

                            if dbg_ri.get("error"):
                                st.caption(f"💰 **Residual Income Model:** — _{dbg_ri['error']}_")
                                return

                            st.caption(
                                f"Current ROE: {dbg_ri['current_roe']:.1%} · "
                                f"Normalized ROE (10-yr avg, {dbg_ri['normalized_roe_years_used']}y used): "
                                f"{dbg_ri['normalized_roe']:.1%}"
                            )
                            _rs1, _rs2 = st.columns(2)
                            with _rs1:
                                st.markdown("**Single-Stage** _(today's ROE held forever)_")
                                _s = dbg_ri["single_stage"]
                                if _s["error"]:
                                    st.caption(f"— _{_s['error']}_")
                                else:
                                    st.metric("Intrinsic Value", f"${_s['intrinsic_value_per_share']:.2f}/sh")
                                    _smos = _s["margin_of_safety"]
                                    st.caption(f"MoS: {_smos:+.0%}" if _smos is not None else "MoS: —")
                            with _rs2:
                                st.markdown("**Multi-Stage** _(ROE fades to normal)_")
                                _m = dbg_ri["multi_stage"]
                                if _m["error"]:
                                    st.caption(f"— _{_m['error']}_")
                                else:
                                    st.metric("Intrinsic Value", f"${_m['intrinsic_value_per_share']:.2f}/sh")
                                    _mmos = _m["margin_of_safety"]
                                    st.caption(f"MoS: {_mmos:+.0%}" if _mmos is not None else "MoS: —")

                            _div = dbg_ri.get("divergence")
                            if _div is not None:
                                if _div >= 0.30:
                                    st.warning(f"⚠️ {_div:.0%} gap between single- and multi-stage — current ROE looks well off this company's own normal range. Worth digging into why before trusting either number.")
                                elif _div >= 0.15:
                                    st.info(f"ℹ️ {_div:.0%} gap between single- and multi-stage — some divergence, worth a closer look.")
                                else:
                                    st.caption(f"✅ Only {_div:.0%} gap between single- and multi-stage — ROE looks fairly stable.")
                            return

                        # ── Standard FCF-based DCF (non-financial) ──────
                        # (2026-07-23) Current price used to only be shown
                        # if the DCF succeeded -- an early "return" on any
                        # DCF error meant price never showed either, even
                        # though the price fetch itself had nothing to do
                        # with the DCF failing. Price is now always shown;
                        # only IV/MoS fall back to N/A.
                        st.markdown("#### Valuation (DCF, default assumptions)")
                        _dv1, _dv2, _dv3 = st.columns(3)
                        with _dv1:
                            _cp = dbg_price_data.get("price")
                            st.metric("Current Price", f"${_cp:.2f}" if _cp else "N/A")
                        if dbg_dcf.get("error"):
                            with _dv2:
                                st.metric("Intrinsic Value", "N/A")
                            with _dv3:
                                st.metric("Margin of Safety", "—", help=dbg_dcf["error"])
                            return
                        with _dv2:
                            _iv = dbg_dcf.get("intrinsic_value_per_share")
                            st.metric("Intrinsic Value", f"${_iv:.2f}/sh" if _iv is not None else "N/A")
                        with _dv3:
                            _mos = dbg_dcf.get("margin_of_safety")
                            st.metric("Margin of Safety", f"{_mos:+.0%}" if _mos is not None else "—")

                    if dbg_subtype in ("bank", "insurance"):
                        # ── Bank/Insurer alt framework (#36) ────────────
                        dbg_funnel = evaluate_financial_firm_funnel(dbg_facts, dbg_subtype)
                        dbg_score, dbg_criteria = score_financial_firm_breakdown(dbg_latest, dbg_subtype)

                        overall_icon = "✅" if dbg_funnel["overall_passed"] else "❌"
                        _subtype_label = "🏦 Bank" if dbg_subtype == "bank" else "🛡️ Insurer"
                        st.markdown(f"### {overall_icon} {dbg_meta.get('company_name', dbg_ticker)} ({dbg_ticker})")
                        _tag_bits = [f"{_subtype_label} — alt scoring framework (#36)"]
                        if dbg_funnel["limited_history"]: _tag_bits.append(f"📏 Limited history ({dbg_funnel['years_used']}y)")
                        st.caption(" · ".join(_tag_bits))
                        if dbg_score is not None:
                            st.metric("Alt framework score", f"{dbg_score}/100")

                        st.markdown("#### Checklist Legs (bank/insurer framework)")
                        _roe = dbg_funnel["roe_avg"]
                        _leg_row(
                            "10-yr Avg ROE", dbg_funnel["roe_pass"],
                            f"{_roe['avg']:.1%}" if _roe["avg"] is not None else "N/A",
                            "> 10%",
                            f" · {_roe['years_used']} years used (min 5)",
                        )
                        _qv = dbg_funnel["quality_value"]
                        _qv_str = f"{_qv:.1%}" if _qv is not None else "N/A"
                        _q_rule = "≤ 70% (efficiency ratio)" if dbg_subtype == "bank" else "≤ 100% (10-yr avg combined ratio)"
                        _leg_row(f"Quality leg ({dbg_funnel['quality_leg']})", dbg_funnel["quality_pass"], _qv_str, _q_rule)
                        _cap = dbg_funnel["capital_ratio"]
                        _leg_row(
                            "Capital cushion (Equity / Assets)", dbg_funnel["capital_pass"],
                            f"{_cap:.1%}" if _cap is not None else "N/A", "≥ 6%",
                        )
                        _dil = dbg_funnel["dilution"]
                        _dil_pct = _dil.get("pct_change")
                        _dil_pct_str = f"{_dil_pct:+.1%}" if _dil_pct is not None else "N/A"
                        _leg_row(
                            "No Dilution", dbg_funnel["dilution_pass"],
                            f"shares chg {_dil_pct_str} over {_dil['years_compared'] or '?'}y",
                            "≤ 0% over 5y",
                        )

                        _show_dcf_block()

                        if dbg_criteria:
                            st.markdown("#### Score Breakdown")
                            st.dataframe(pd.DataFrame(dbg_criteria), hide_index=True, use_container_width=True)

                        st.markdown("#### Raw History Depth (bank/insurer fields)")
                        _fin_hist_fields = ["roe", "equity_to_assets", "nim_proxy", "efficiency_ratio",
                                             "provision_to_ni", "combined_ratio", "diluted_shares"]
                        _hist_rows = []
                        for field in _fin_hist_fields:
                            h = dbg_history.get(field, [])
                            _hist_rows.append({
                                "Field": field,
                                "Years of History": len(h),
                                "Earliest": h[0]["period"] if h else "N/A",
                                "Latest": h[-1]["period"] if h else "N/A",
                            })
                        st.dataframe(pd.DataFrame(_hist_rows), hide_index=True, use_container_width=True)

                    else:
                        dbg_funnel = evaluate_buffett_funnel(dbg_facts, funnel_thresholds)

                        overall_icon = "✅" if dbg_funnel["overall_passed"] else "❌"
                        st.markdown(f"### {overall_icon} {dbg_meta.get('company_name', dbg_ticker)} ({dbg_ticker})")
                        _tag_bits = []
                        if dbg_meta.get("is_financial"):
                            _fin_label = {"other_financial": "🏦 Financial firm (broker/REIT/real estate — no alt scoring yet)"}.get(dbg_subtype, "🏦 Financial firm")
                            _tag_bits.append(_fin_label)
                        if dbg_meta.get("is_cyclical"):  _tag_bits.append("⚠️ Cyclical")
                        if dbg_latest.get("is_negative_equity"): _tag_bits.append("📊 Negative Equity")
                        if dbg_funnel["limited_history"]: _tag_bits.append(f"📏 Limited history ({dbg_funnel['years_used']}y)")
                        if dbg_funnel["roic_stale"]:
                            _tag_bits.append(f"🕰️ Stale ROIC (last reliable: {dbg_funnel['roic_last_reliable_period']}, {dbg_funnel['roic_stale_years']}y old)")
                        if _tag_bits:
                            st.caption(" · ".join(_tag_bits))

                        st.markdown("#### Checklist Legs")
                        _roic = dbg_funnel["roic_avg"]
                        _leg_row(
                            "10-yr Avg ROIC", dbg_funnel["roic_pass"],
                            f"{_roic['avg']:.1%}" if _roic["avg"] is not None else "N/A",
                            f"> {funnel_thresholds['roic_avg_min']:.0%}",
                            f" · {_roic['years_used']} years used (min {funnel_thresholds['min_history_years']})",
                        )
                        _fcfm = dbg_funnel["fcf_margin_avg"]
                        _leg_row(
                            "10-yr Avg FCF Margin", dbg_funnel["fcf_margin_pass"],
                            f"{_fcfm['avg']:.1%}" if _fcfm["avg"] is not None else "N/A",
                            f"> {funnel_thresholds['fcf_margin_avg_min']:.0%}",
                            f" · {_fcfm['years_used']} years used (min {funnel_thresholds['min_history_years']})",
                        )
                        _dni  = dbg_funnel["debt_to_ni"]
                        _dcads = dbg_funnel["debt_to_cads"]
                        _leg_row(
                            "Debt Hurdle (either)", dbg_funnel["debt_pass"],
                            f"Debt/NI {f'{_dni:.1f}x' if _dni is not None else 'N/A'} · "
                            f"Debt/CADS {f'{_dcads:.1f}x' if _dcads is not None else 'N/A'}",
                            f"either < {funnel_thresholds['debt_to_ni_max']:.1f}x / {funnel_thresholds['debt_to_cads_max']:.1f}x",
                            f" · cleared: {dbg_funnel['debt_hurdle_cleared']}",
                        )
                        _dil = dbg_funnel["dilution"]
                        _dil_pct = _dil.get("pct_change")
                        _dil_pct_str = f"{_dil_pct:+.1%}" if _dil_pct is not None else "N/A"
                        _leg_row(
                            "No Dilution", dbg_funnel["dilution_pass"],
                            f"shares chg {_dil_pct_str} over {_dil['years_compared'] or '?'}y",
                            f"≤ 0% over {funnel_thresholds['dilution_lookback_years']}y",
                        )

                        _show_dcf_block()

                        st.markdown("#### Raw History Depth (validates the tag-merge fix — should NOT be truncated for a long-tenured filer)")
                        _hist_rows = []
                        for field in ["roic", "fcf_margin", "revenue", "net_income", "diluted_shares"]:
                            h = dbg_history.get(field, [])
                            _hist_rows.append({
                                "Field": field,
                                "Years of History": len(h),
                                "Earliest": h[0]["period"] if h else "N/A",
                                "Latest": h[-1]["period"] if h else "N/A",
                            })
                        st.dataframe(pd.DataFrame(_hist_rows), hide_index=True, use_container_width=True)

                        with st.expander("Full ROIC + FCF Margin history (year by year)"):
                            _yr_col1, _yr_col2 = st.columns(2)
                            with _yr_col1:
                                st.caption("ROIC")
                                st.dataframe(pd.DataFrame(dbg_history.get("roic", [])), hide_index=True, use_container_width=True)
                            with _yr_col2:
                                st.caption("FCF Margin")
                                st.dataframe(pd.DataFrame(dbg_history.get("fcf_margin", [])), hide_index=True, use_container_width=True)

st.markdown("#### Ticker Universe")
universe_choice = st.radio(
    "Select the universe to scan",
    options=["S&P 500 (~500)", "All US Common Stocks (~6,000+)"],
    index=1,
    horizontal=True,
    help=(
        "S&P 500 sourced from Wikipedia. 'All US Common Stocks' is sourced free from "
        "Nasdaq Trader's public Symbol Directory (NASDAQ + NYSE + NYSE American + NYSE "
        "Arca), filtered to common stock only (no ETFs, SPACs warrants/units, or test "
        "issues). This is a much broader universe than the S&P 500 and a practical free "
        "proxy for Russell 1000/2000-scale coverage — FTSE Russell's own official "
        "constituent files are commercial-license-only, so there's no free exact match. "
        "Defaults to the full universe since a completed scan is now cached persistently "
        "(survives reboots) — you rarely need to re-scan from scratch."
    ),
)

col1, col2, col3 = st.columns(3)
with col1:
    top_n = st.number_input("Top results to show", min_value=5, max_value=50, value=15, step=5)
with col2:
    skip_financials = st.checkbox("Skip brokers/REITs/real estate/mortgage insurers/other financials", value=True,
                                   help="Banks and insurers (#36) now run through their own alt scoring "
                                        "framework — ROE, efficiency ratio or combined ratio, capital "
                                        "cushion, dilution — instead of being excluded. This toggle now "
                                        "only excludes financial SIC codes that don't have an alt "
                                        "framework yet: brokers, REITs, real estate, investment offices, "
                                        "and monoline mortgage/credit/financial-guaranty insurers (MGIC, "
                                        "Radian, Essent, NMI, Enact, etc. — SIC 6351). That last group is "
                                        "SIC-classified as insurance but runs a structurally different "
                                        "balance sheet (58-78% equity/assets, single-digit combined ratios "
                                        "by business-model design) that scores near-perfect under any "
                                        "P&C-calibrated threshold — excluded here until #70 gives them "
                                        "their own metric set.")
    flag_cyclicals  = st.checkbox("Flag cyclical firms", value=True,
                                   help="Cyclicals aren't excluded, just badged ⚠️ on their result card — a "
                                        "10-yr average still leans on wherever the cycle currently sits.")
with col3:
    min_div  = st.checkbox("Dividend payers only (Stage 2 filter)", value=False)

# ── Stage 1 filters: industry, market cap, superinvestor coverage ──────
st.markdown("#### Stage 1 Filters")
st.caption(
    "Set these before running the scan. They're applied right after the quality scan "
    "completes (Stage 1), narrowing the candidate pool before Stage 2's price lookups."
)

_sic_map = fetch_sic_industry_map()
st.caption(
    f"📋 {len(_sic_map['full'])} SIC sub-industries available across {len(set(_sic_map['major'].values()))} "
    f"major industry groups — full SEC classification table, no scan required to populate."
)

GICS_SECTORS = [
    "Basic Materials", "Communication Services", "Consumer Cyclical",
    "Consumer Defensive", "Energy", "Financial Services", "Healthcare",
    "Industrials", "Real Estate", "Technology", "Utilities",
]

fcol0, fcol1, fcol2, fcol3 = st.columns(4)
with fcol0:
    sector_filter = st.multiselect(
        "Sector",
        options=GICS_SECTORS,
        default=[],
        help="GICS sector, via yfinance — the broadest classification level. Leave empty to "
             "include all sectors. Fetched for every Stage 1 candidate, so this adds some time "
             "to Stage 1 (same trade-off as Market Cap Tier).",
    )
with fcol1:
    industry_filter = st.multiselect(
        "Industry",
        options=sorted(set(_sic_map.get("major", {}).values())),
        default=[],
        help="Major SIC industry group(s). Leave empty to include all industries. "
             "Companies are classified by their primary SIC code in SEC filings. "
             "Independent of the Sector filter above — SIC and GICS are different "
             "classification systems, so combining both narrows further but they don't "
             "nest perfectly into each other.",
    )
with fcol2:
    # Sub-industry options sourced directly from the COMPLETE static SIC
    # table (same source as the Industry dropdown) — fully populated
    # before any scan ever runs. Narrows automatically based on the
    # selected major industries (if any).
    sub_industry_options = sub_industries_for_major(industry_filter, _sic_map)
    sub_industry_filter = st.multiselect(
        "Sub-Industry",
        options=sub_industry_options,
        default=[],
        help="Every sub-industry within the selected major group(s), per the SEC's official SIC "
             "code list. Leave empty to include all sub-industries within your industry selection. "
             "Not every sub-industry will necessarily have matches in your scanned universe.",
    )
with fcol3:
    cap_filter = st.multiselect(
        "Market Cap Tier",
        options=["Large Cap (≥$10B)", "Mid Cap ($2B–$10B)", "Small Cap ($300M–$2B)", "Micro Cap (<$300M)"],
        default=[],
        help="Leave empty to include all sizes. Market cap is fetched for every Stage 1 candidate "
             "(adds some time vs. deferring to Stage 2, but enables this filter).",
    )

# Superinvestor coverage filter — reuses the same load button pattern
# used elsewhere in the app, but offered here so it can act as a Stage 1
# filter rather than only a post-scan display enhancement.
_si_loaded_pre = "_si_full_map" in st.session_state
si_filt_col1, si_filt_col2 = st.columns([2, 4])
with si_filt_col1:
    if not _si_loaded_pre:
        if st.button("🦁 Load Superinvestor Conviction", use_container_width=True,
                     help="Fetches all 82 superinvestor portfolios from Dataroma (~30-60s, one-time per session). "
                          "Required to use the SI coverage filter."):
            st.session_state["_si_full_map"] = get_conviction_data()
            st.rerun()
        si_only_filter = False
    else:
        si_only_filter = st.checkbox("🦁 Only show companies with superinvestor coverage", value=False)
with si_filt_col2:
    if _si_loaded_pre:
        st.caption("Superinvestor data loaded — filter available below, and results will show holder counts.")
    else:
        st.caption("Optional — load to filter Stage 1 results to only companies held by at least one of 82 tracked superinvestors.")

_approx_universe_size = {"S&P 500 (~500)": 500, "All US Common Stocks (~6,000+)": 7000}[universe_choice]
_est_min = max(1, round(_approx_universe_size / 8 / 60 * 1.6))  # rough: 8 parallel workers, ~1 req/sec/worker, 60% overhead (sector .info call adds latency vs. fast_info alone)
st.caption(f"⏱️ Estimated Stage 1 time for ALL ~{_approx_universe_size:,} tickers: ~{_est_min} minutes. Stage 2 (price lookups on survivors) adds 10-60 seconds. Runs in the background — you can navigate elsewhere while it works.")

force_refresh_facts = st.checkbox(
    "🔄 Force fresh EDGAR fetch for every ticker (ignore the 7-day cache)",
    value=False,
    help="Each ticker's normalized EDGAR history is cached for 7 days so repeat scans "
         "skip most EDGAR calls and run far faster — this is on by default (unchecked "
         "here means 'use the cache'). Check this box to bypass the cache entirely and "
         "re-fetch every ticker fresh from EDGAR on this run instead, e.g. right after a "
         "company you care about files a new 10-K/10-Q, or if you suspect a cached value "
         "is stale or wrong. The freshly-fetched data replaces the old cache entries as "
         "usual either way.",
)

st.divider()
run_screen = st.button("🚀 Run Two-Stage Screen", type="primary", use_container_width=True)

# ── Run screen ──────────────────────────────────────────────────────
def run_filters_and_stage2(stage1_pool: list, total_tickers: int):
    """
    Applies the currently-selected Stage 1 filters (sector, industry,
    sub-industry, market cap, SI coverage) to an already-fetched Stage 1
    pool, then runs Stage 2 (price lookups + full scoring) on the
    survivors. This is split out from the Stage 1 EDGAR scan so filters
    can be changed and re-applied — including a fresh dividend/min-div
    or weight change — WITHOUT re-fetching EDGAR data, which is the slow
    and rate-limit-sensitive part. Stage 2 still re-fetches live prices
    each time it runs, since price is the one input that's genuinely
    time-sensitive.
    """
    stage1_results = stage1_pool

    # ── Apply Stage 1 filters: sector, industry, sub-industry, market cap, SI ──
    _pre_filter_count = len(stage1_results)

    if sector_filter:  # non-empty list = filter active
        stage1_results = [
            d for d in stage1_results
            if d.get("sector") in sector_filter
        ]

    if industry_filter:  # non-empty list = filter active
        stage1_results = [
            d for d in stage1_results
            if sic_major_name(str(d.get("sic") or ""), _sic_map) in industry_filter
        ]

    if sub_industry_filter:  # non-empty list = filter active
        stage1_results = [
            d for d in stage1_results
            if sic_full_name(str(d.get("sic") or ""), _sic_map) in sub_industry_filter
        ]

    if cap_filter:
        stage1_results = [
            d for d in stage1_results
            if market_cap_tier(d.get("market_cap")) in cap_filter
        ]

    if _si_loaded_pre and si_only_filter:
        stage1_results = [
            d for d in stage1_results
            if get_superinvestor_conviction(d["ticker"]).get("holder_count", 0) > 0
        ]

    if len(stage1_results) != _pre_filter_count:
        st.caption(f"🔍 Stage 1 filters applied: {_pre_filter_count} → {len(stage1_results)} companies.")

    if not stage1_results:
        st.warning("No companies survived the Stage 1 filters you selected. Try relaxing sector, industry, market cap, or SI coverage filters.")
        st.stop()

    # ── Stage 2: Price lookup for survivors only ────────────────────────
    st.markdown(f"### Stage 2 — Valuation Check ({len(stage1_results)} quality survivors, live pricing)")
    progress_bar2 = st.progress(0)
    status_text2  = st.empty()
    results = []
    completed2 = 0
    n_survivors = len(stage1_results)

    def _stage2_worker(qdata):
        ticker     = qdata["ticker"]
        price_data = fetch_price_data(ticker)
        return qdata, price_data

    # (2026-07-23) dropped_tickers used to be silent -- a ticker whose
    # yfinance price fetch threw (rate limit, transient timeout, etc.)
    # or that the dividend filter excluded just vanished from the results
    # with zero trace, making "why isn't X here" impossible to answer
    # after the fact (confirmed live: ALL is a genuine Stage 1 survivor
    # per the scan cache, but didn't appear in a Stage 2 run -- with no
    # record of which of these two paths dropped it, or whether it was
    # something else entirely). Now tracked and surfaced below the
    # results so a one-off transient failure is visibly distinguishable
    # from "this ticker doesn't qualify."
    dropped_tickers = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_stage2_worker, q): q["ticker"] for q in stage1_results}
        for future in concurrent.futures.as_completed(futures):
            completed2 += 1
            pct = completed2 / n_survivors
            progress_bar2.progress(pct)
            status_text2.markdown(f"⏳ Stage 2: {completed2} of {n_survivors} ({int(pct*100)}%)")
            _fut_ticker = futures[future]
            try:
                qdata, price_data = future.result()
            except Exception as _e:
                dropped_tickers.append((_fut_ticker, f"price fetch failed: {_e}"))
                continue

            price      = price_data.get("price")
            market_cap = price_data.get("market_cap")
            shares     = price_data.get("shares")
            div_yield  = price_data.get("dividend_yield")
            sector     = price_data.get("sector", "N/A")

            # (2026-07-23) fetch_price_data() catches its OWN exceptions
            # internally and returns an all-None dict rather than raising
            # -- so a transient yfinance failure (rate limit, timeout,
            # ticker-specific hiccup) for one ticker never hit the
            # try/except above at all, and the row still got added to
            # results with every price-dependent field blank (no price,
            # no MoS, no dividend yield), completely silently. Tracked
            # here too (kept in results rather than dropped, since Stage
            # 1 quality data is still valid and worth showing) so a row
            # that's blank because of THIS shows up in the same
            # visibility expander as an outright-dropped ticker, instead
            # of looking identical to "doesn't qualify."
            if price is None and market_cap is None and shares is None:
                dropped_tickers.append((_fut_ticker, "price fetch returned nothing (yfinance failure/rate limit) — row kept with blank price/MoS/dividend fields"))

            if min_div and not div_yield:
                dropped_tickers.append((_fut_ticker, "no dividend (Dividend payers only filter active)"))
                continue

            # (2026-07-23) yfinance's "sharesOutstanding" field has a
            # documented history of intermittently returning None for a
            # given ticker even on repeated/retried requests -- confirmed
            # live for ALL specifically (a data gap in Yahoo's response,
            # not a network blip a retry can fix). EDGAR filings already
            # give us a reliable diluted share count for every ticker
            # regardless of what yfinance does -- same fallback
            # fetch_fundamentals_edgar() already uses for Dashboard/
            # Equity Scout/Compare Stocks, just missing here until now.
            # Applied BEFORE the P/OE calc below so that benefits too,
            # not just the residual-income/DCF fields further down.
            shares = shares or (qdata.get("_latest") or {}).get("diluted_shares")

            fcf        = qdata.get("fcf")
            owner_earn = qdata.get("owner_earnings")
            fcf_yield  = (fcf / market_cap) if (fcf and market_cap and market_cap > 0) else None
            poe        = (price / (owner_earn / shares)) if (owner_earn and owner_earn > 0 and shares and price) else None

            full_data = {
                **qdata,
                "price":            price,
                "shares":           shares,
                # (2026-07-23) "shares" was fetched into a local variable
                # for the P/OE calc right above but never actually landed
                # in this dict -- meaning compute_dcf_value() downstream
                # NEVER had shares available for a single Market Screener
                # ticker, forcing every one of them into the market-cap-
                # basis MoS fallback and leaving intrinsic_value_per_share
                # None for literally every row, not just financial firms.
                "market_cap":       market_cap or qdata.get("market_cap"),
                "sector":           sector if sector and sector != "N/A" else qdata.get("sector", "Unknown"),
                "fcf_yield":        fcf_yield,
                "price_owner_earn": poe,
                "dividend_yield":   div_yield,
                "industry":         sic_major_name(str(qdata.get("sic") or ""), _sic_map),
                "sub_industry":     sic_full_name(str(qdata.get("sic") or ""), _sic_map),
            }
            # Score is NOT computed here — funnel pass/fail already happened in
            # Stage 1; this just attaches price-dependent reference fields.
            results.append(full_data)

    progress_bar2.progress(1.0)
    status_text2.markdown(f"✅ Stage 2 complete — {len(results)} priced companies.")
    if dropped_tickers:
        with st.expander(f"⚠️ {len(dropped_tickers)} Stage 1 survivor(s) had Stage 2 issues — click for why", expanded=False):
            st.caption("These passed the quality checklist but either didn't make it into the priced results below, or made it in with blank price/MoS/dividend fields.")
            st.dataframe(pd.DataFrame(dropped_tickers, columns=["Ticker", "Reason"]),
                         hide_index=True, use_container_width=True)

    if not results:
        st.warning("No results survived Stage 2. Try removing the dividend filter.")
        st.stop()

    # Cache the full PRICED pool (before truncation) so the display can
    # be rebuilt (re-sorted, re-truncated) without re-running Stage 2
    # pricing again.
    st.session_state['ms_edgar_stage2_priced_pool'] = results
    st.session_state['ms_edgar_total_tickers']      = total_tickers

    build_results_table(results)


def build_results_table(priced_pool: list):
    """
    Builds the displayed results table from an already-priced Stage 2
    pool. Deliberately does NOT force-rank survivors by a composite
    score — Stage 1 is a pass/fail checklist, not a weighted scorer, so
    there's no single "best" ordering to impose. Default order is
    ticker (A-Z); the results panel below offers a manual "Sort results
    by" control (ROIC avg, FCF margin avg, ticker, years of history).
    Truncates to top_n so a huge survivor pool stays browsable.
    """
    scored = [dict(d) for d in priced_pool]  # don't mutate the cached pool

    results_df = pd.DataFrame(scored)
    if not results_df.empty:
        results_df = results_df.sort_values('ticker', ascending=True).head(top_n).reset_index(drop=True)

    st.session_state['ms_edgar_results_df']    = results_df
    st.session_state['ms_edgar_results_count'] = len(scored)
    st.session_state['ms_claude_convo']        = []
    st.session_state['ms_claude_context_sent'] = False
    st.session_state['ms_selected_tickers']    = []

    st.session_state['ms_edgar_results_df']    = results_df
    st.session_state['ms_edgar_results_count'] = len(scored)
    st.session_state['ms_claude_convo']        = []
    st.session_state['ms_claude_context_sent'] = False
    st.session_state['ms_selected_tickers']    = []
    st.session_state.pop('ms_filings', None)


# ── Persistent scan cache — survives Streamlit Cloud reboots/redeploys ──────
# Stage 1's survivor pool (post quality-floor, pre-price — the same thing
# cached in session_state as 'ms_edgar_stage1_raw_pool') is small enough to
# store in the GitHub repo, unlike the full per-ticker scan which can take
# 10+ minutes for the whole US universe. Loaded once per session; a full
# scan (the button below) re-saves it after completing.
if 'ms_edgar_cache_load_attempted' not in st.session_state:
    st.session_state['ms_edgar_cache_load_attempted'] = True
    if 'ms_edgar_stage1_raw_pool' not in st.session_state:
        _cached, _sha, _err = github_get_json(SCAN_CACHE_PATH)
        if _cached and not _err:
            st.session_state['ms_edgar_stage1_raw_pool']    = _cached.get('stage1_survivors', [])
            st.session_state['ms_edgar_stage1_raw_total']   = _cached.get('total_tickers_scanned', 0)
            st.session_state['ms_edgar_scan_timestamp']     = _cached.get('scan_timestamp')
            st.session_state['ms_edgar_scan_universe']      = _cached.get('universe')
        elif _err:
            st.session_state['ms_edgar_cache_load_error'] = _err

_has_cached_pool  = 'ms_edgar_stage1_raw_pool' in st.session_state
_has_priced_pool  = 'ms_edgar_stage2_priced_pool' in st.session_state

if st.session_state.get('ms_edgar_cache_load_error'):
    st.caption(f"⚠️ Couldn't load persistent scan cache: {st.session_state['ms_edgar_cache_load_error']}")

action_col1, action_col3 = st.columns([2, 6])
with action_col1:
    refilter_clicked = st.button(
        "🔁 Re-apply Filters (no rescan)", use_container_width=True,
        disabled=not _has_cached_pool,
        help="Re-runs filtering + Stage 2 pricing on the cached Stage 1 pool from your last full "
             "scan — change Sector/Industry/Cap/SI filters above and click this to see new results "
             "in seconds, without re-fetching EDGAR data." if _has_cached_pool else
             "Run a full scan first (below) to enable fast re-filtering.",
    )
with action_col3:
    _scan_ts    = st.session_state.get('ms_edgar_scan_timestamp')
    _scan_univ  = st.session_state.get('ms_edgar_scan_universe', '')
    _last_scan_str = ""
    if _scan_ts:
        try:
            _dt = datetime.fromisoformat(_scan_ts)
            _last_scan_str = f" · Last full scan: {_dt.strftime('%b %d, %Y %H:%M UTC')} ({_scan_univ})"
        except Exception:
            _last_scan_str = f" · Last full scan: {_scan_ts} ({_scan_univ})"
    if _has_priced_pool:
        _priced_n = len(st.session_state['ms_edgar_stage2_priced_pool'])
        st.caption(f"💾 {_priced_n} priced companies cached{_last_scan_str}. Note: changing Funnel "
                   f"Thresholds above only affects a fresh scan — Re-apply Filters re-uses the "
                   f"pass/fail already computed at scan time.")
    elif _has_cached_pool:
        _cached_n = len(st.session_state['ms_edgar_stage1_raw_pool'])
        st.caption(f"💾 {_cached_n} companies cached{_last_scan_str} — click Re-apply Filters to price and see results, or change filters first.")
    else:
        st.caption("No cached scan yet — run a full scan below first. Once complete, it's saved persistently and survives reboots.")

# _scroll_to_ms_results (#75 follow-up): true only on the run that fresh
# results actually just landed -- either a synchronous re-filter of the
# cached Stage 1 pool, or a background full scan finishing and being
# ingested (_just_ingested, set further down). NOT true on later reruns
# that just redisplay st.session_state['ms_edgar_results_df'] (sorting,
# chat, the 2s progress-fragment ticking, etc.), so the user stays free
# to scroll wherever they want after results are already on screen.
_scroll_to_ms_results = False
if refilter_clicked and _has_cached_pool:
    _scroll_to_ms_results = True
    run_filters_and_stage2(
        st.session_state['ms_edgar_stage1_raw_pool'],
        st.session_state.get('ms_edgar_stage1_raw_total', len(st.session_state['ms_edgar_stage1_raw_pool'])),
    )



if run_screen:
    _snap = _scan_snapshot()
    if _snap["active"]:
        st.warning("A scan is already running in the background. Wait for it to finish, or cancel it below, before starting a new one.")
    else:
        with st.spinner(f"Loading {universe_choice} ticker list..."):
            if universe_choice == "S&P 500 (~500)":
                tickers = get_sp500_tickers()
            else:
                tickers = fetch_full_us_equity_universe(universe="all_us")

        if not tickers:
            st.error(f"Could not load the {universe_choice} ticker list. Try again — Nasdaq Trader/Wikipedia data sources occasionally have transient issues.")
            st.stop()

        st.caption(f"📋 {len(tickers):,} tickers loaded — scanning all of them.")
        tickers_to_scan = tickers

        # ── Build ticker -> CIK map ONCE (the key bulk-scan optimization) ──
        with st.spinner("Resolving tickers to SEC CIK numbers (one-time lookup)..."):
            ticker_cik_map = get_ticker_cik_map()

        if not ticker_cik_map:
            st.error("Could not load EDGAR ticker-to-CIK map. Try again in a moment.")
            st.stop()

        # ── Launch Stage 1 in the background (#69) and hand control back ──
        # to Streamlit immediately, instead of blocking this script
        # execution for potentially several minutes. State is initialized
        # synchronously here (see _start_stage1_scan_background's
        # docstring for why that ordering matters) before the rerun below.
        # Load whichever cache shards this ticker list touches, once,
        # up front (see _load_facts_cache_shards()'s docstring) -- unless
        # the user asked to bypass the cache entirely this run, in which
        # case there's no point spending the GitHub reads on a cache
        # every ticker is about to ignore anyway.
        _load_errors = []
        if force_refresh_facts:
            facts_cache = {}
        else:
            with st.spinner("Loading cached EDGAR history..."):
                facts_cache, _load_errors = _load_facts_cache_shards(tickers_to_scan)
        # NOTE: deliberately not st.caption()'d here -- this whole branch
        # is on the run that's about to call st.rerun() a few lines down
        # to hand off to the "scan active" state, so anything printed
        # here would flash for one frame and then vanish. Stashed into
        # the persistent scan state instead (see facts_cache_loaded_count/
        # facts_cache_load_errors below) and shown from
        # _render_scan_progress_fragment(), which survives the rerun.

        _start_stage1_scan_background(
            tickers_to_scan, ticker_cik_map, funnel_thresholds,
            skip_financials, universe_choice, SCAN_CACHE_PATH,
            facts_cache=facts_cache, force_refresh_facts=force_refresh_facts,
            facts_cache_load_errors=_load_errors,
        )
        st.rerun()

# ── Live progress if a scan is currently running (any session can see this) ──
_snap = _scan_snapshot()
_just_ingested = False
if _snap["active"]:
    # Remember that THIS session actually watched a scan in progress --
    # used below to decide whether ingesting its results should also
    # scroll to them. The background scan state is shared across every
    # session/tab; without this, simply opening the page fresh and
    # discovering an already-finished scan from earlier (that this
    # session never watched run) would look identical to "I was just
    # watching this and it finished" and wrongly trigger a scroll on a
    # plain navigation -- exactly the disruptive behavior #75 set out to
    # fix in the first place.
    st.session_state['ms_edgar_watched_active_scan'] = True
    st.markdown("### Stage 1 — Checklist Scan (running in background)")
    _render_scan_progress_fragment()

# ── Ingest a just-finished scan into THIS session, exactly once ────────
elif _snap["finished_at"] and st.session_state.get('ms_edgar_ingested_finish_ts') != _snap["finished_at"]:
    st.session_state['ms_edgar_ingested_finish_ts'] = _snap["finished_at"]
    _just_ingested = True
    # Only scroll to results if this session actually watched the scan
    # run (see comment above) -- not on a fresh page load that happens
    # to discover a scan someone else (or an earlier visit) finished.
    if st.session_state.pop('ms_edgar_watched_active_scan', False):
        _scroll_to_ms_results = True

    if _snap.get("error"):
        st.error(f"Scan failed: {_snap['error']}")
    if _snap.get("cancelled"):
        st.warning(f"Scan cancelled after {_snap['completed']} of {_snap['total']} tickers — partial results only, and NOT saved persistently (only completed full scans are cached).")

    stage1_results = _snap["stage1_results"]
    total_tickers  = _snap["completed"] if _snap.get("cancelled") else _snap["total"]

    # Persist the waterfall/failure breakdown into session_state too, so
    # it's still visible if the user navigates away and back later — not
    # just on the exact rerun where ingestion happened.
    st.session_state['ms_edgar_last_waterfall']      = _snap["waterfall"]
    st.session_state['ms_edgar_last_fetch_failures'] = _snap["fetch_failures"]
    st.session_state['ms_edgar_last_no_xbrl']        = _snap["no_xbrl_tickers"]
    st.session_state['ms_edgar_last_total_scanned']  = total_tickers
    st.session_state['ms_edgar_last_num_passed']     = len(stage1_results)

    if not stage1_results:
        st.warning("No companies passed Stage 1 quality filters. Try lowering the quality floor or scanning more tickers.")
    else:
        # Cache the RAW, unfiltered Stage 1 pool so filters can be changed
        # and re-applied later via the "Re-apply Filters" button above,
        # without re-running the slow EDGAR fetch.
        st.session_state['ms_edgar_stage1_raw_pool']  = stage1_results
        st.session_state['ms_edgar_stage1_raw_total'] = total_tickers

        # The persistent GitHub save already happened inside the
        # background worker itself (exactly once, regardless of how many
        # sessions/tabs are watching) — just reflect its outcome here.
        _facts_hits   = _snap.get("facts_cache_hits", 0)
        _facts_misses = _snap.get("facts_cache_misses", 0)
        if _facts_hits or _facts_misses:
            st.caption(
                f"📦 EDGAR facts cache: {_facts_hits:,} served from cache, "
                f"{_facts_misses:,} fetched fresh from EDGAR "
                f"({EDGAR_FACTS_CACHE_MAX_AGE_DAYS}-day freshness window)."
            )
        _facts_save_failures = _snap.get("facts_cache_save_failures", [])
        if _facts_save_failures:
            st.caption(
                f"⚠️ {len(_facts_save_failures)} cache shard(s) failed to save after retries — "
                f"those tickers' fresh data wasn't persisted this run and will simply be "
                f"re-fetched from EDGAR again next scan (nothing lost from the scan results "
                f"themselves, only from the cache)."
            )

        if _snap.get("github_save_ok") is True:
            st.session_state['ms_edgar_scan_timestamp'] = _snap["github_save_msg"]  # holds the timestamp on success
            st.session_state['ms_edgar_scan_universe']  = _snap["universe"]
            st.caption("✅ Scan cached persistently — will still be here after a reboot.")
        elif _snap.get("github_save_ok") is False:
            st.warning(f"⚠️ Scan completed but persistent save failed: {_snap['github_save_msg']}\n\n"
                       f"Results are available for this session, but a reboot/redeploy will lose them "
                       f"until you re-run the scan.")

        run_filters_and_stage2(stage1_results, total_tickers)

# ── Waterfall/failures from the most recent scan, if any — persists across reruns/navigation ──
if not _snap["active"] and st.session_state.get('ms_edgar_last_waterfall'):
    _render_waterfall_and_failures(
        st.session_state['ms_edgar_last_waterfall'],
        st.session_state.get('ms_edgar_last_total_scanned', 0),
        st.session_state.get('ms_edgar_last_num_passed', 0),
        st.session_state.get('ms_edgar_last_fetch_failures', []),
        st.session_state.get('ms_edgar_last_no_xbrl', []),
    )

# ── Render results (fresh or cached) ─────────────────────────────────
if 'ms_edgar_results_df' in st.session_state:
    results_df    = st.session_state['ms_edgar_results_df']
    total_tickers = st.session_state.get('ms_edgar_total_tickers', 0)

    if not run_screen and not _just_ingested:
        st.info("💡 Showing results from last screen run. Click **Run Screen** to refresh.")

    st.divider()
    st.markdown('<div id="ms-screener-results"></div>', unsafe_allow_html=True)
    if _scroll_to_ms_results:
        scroll_to_element("ms-screener-results")
    st.markdown(f"## 🏆 {len(results_df)} Checklist Survivors")
    st.caption("Cleared the Buffett/Munger funnel (10-yr avg ROIC > threshold, 10-yr avg FCF margin > threshold, "
               "a debt hurdle, no dilution). Not ranked by a composite score — sort manually below.")

    def fmt(val, fmt_type):
        if val is None or (isinstance(val, float) and pd.isna(val)): return "N/A"
        if fmt_type == "pct":   return f"{val:.1%}"
        if fmt_type == "ratio": return f"{val:.1f}x"
        return str(val)

    # ── Manual sort control (no forced composite ranking) ──────────────
    _si_loaded = "_si_full_map" in st.session_state
    sort_col1, sort_col2 = st.columns([2, 4])
    with sort_col1:
        _sort_options = ["Ticker (A-Z)", "10yr Avg ROIC (High-Low)", "10yr Avg FCF Margin (High-Low)",
                          "Years of History (High-Low)", "Margin of Safety (High-Low)"]
        if _si_loaded:
            _sort_options.append("Superinvestor Conviction (High-Low)")
        sort_choice = st.selectbox("Sort results by", _sort_options, index=0)
    with sort_col2:
        if _si_loaded:
            st.caption("Superinvestor holder counts are shown on each result below.")
        else:
            st.caption("🦁 Superinvestor data not loaded — use the filter section above to load it.")

    # ── Apply superinvestor conviction data if loaded ───────────────────
    if _si_loaded:
        si_scores = []
        for _, row in results_df.iterrows():
            si_result = get_superinvestor_conviction(row['ticker'])
            si_scores.append({
                "si_holders": si_result.get("holder_count", 0),
                "si_score":   si_result.get("conviction_score", 0),
            })
        results_df = results_df.reset_index(drop=True)
        results_df['si_holders'] = [s['si_holders'] for s in si_scores]
        results_df['si_score']   = [s['si_score']   for s in si_scores]

    # ── DCF Margin of Safety — materialized once here (not just computed
    # inline per row at display time) so it can be sorted and exported
    # like any other column (owner feedback: DCF/target price should be
    # visible everywhere, not just Equity Scout/Compare Stocks).
    results_df = results_df.reset_index(drop=True)
    _dcf_results = results_df.apply(lambda r: compute_dcf_value(r.to_dict()), axis=1)
    results_df['margin_of_safety']       = _dcf_results.apply(lambda d: d.get('margin_of_safety'))
    results_df['intrinsic_value_per_share'] = _dcf_results.apply(lambda d: d.get('intrinsic_value_per_share'))

    # ── Residual Income valuation — banks/insurers only ────────────────
    # FCF-DCF above always errors for these rows by design (see
    # compute_residual_income_value()'s module docstring in sec_utils.py
    # for why). No per-ticker _history carried through Stage 1/2 for
    # financial firms either (same market-wide-scan memory-footprint
    # tradeoff as the FCF-DCF's growth-rate estimate above), so the
    # normalized-ROE fade target uses the fixed default rather than each
    # company's own 10-yr average here -- the single-ticker debug tool
    # above fetches full history and gets the real figure instead.
    _ri_results = results_df.apply(lambda r: compute_residual_income_value(r.to_dict()), axis=1)
    results_df['ri_single_mos'] = _ri_results.apply(lambda d: d.get('single_stage', {}).get('margin_of_safety'))
    results_df['ri_single_iv']  = _ri_results.apply(lambda d: d.get('single_stage', {}).get('intrinsic_value_per_share'))
    results_df['ri_multi_mos']  = _ri_results.apply(lambda d: d.get('multi_stage', {}).get('margin_of_safety'))
    results_df['ri_multi_iv']   = _ri_results.apply(lambda d: d.get('multi_stage', {}).get('intrinsic_value_per_share'))
    results_df['ri_divergence'] = _ri_results.apply(lambda d: d.get('divergence'))

    _sort_map = {
        "Ticker (A-Z)":                          ("ticker", True),
        "10yr Avg ROIC (High-Low)":              ("roic_avg", False),
        "10yr Avg FCF Margin (High-Low)":        ("fcf_margin_avg", False),
        "Years of History (High-Low)":           ("funnel_years_used", False),
        "Margin of Safety (High-Low)":           ("margin_of_safety", False),
        "Superinvestor Conviction (High-Low)":   ("si_score", False),
    }
    _sort_col, _sort_asc = _sort_map[sort_choice]
    if _sort_col in results_df.columns:
        results_df = results_df.sort_values(_sort_col, ascending=_sort_asc, na_position='last').reset_index(drop=True)

    # ── Init checkbox selection state ───────────────────────────────
    if 'ms_selected_tickers' not in st.session_state:
        st.session_state['ms_selected_tickers'] = []

    # Clear selections when a new screen runs
    _selected = st.session_state.get('ms_selected_tickers', [])

    # Shrink metric value font so percentages/ratios fit their narrow
    # columns without truncating (e.g. "100.0%" was overflowing at the
    # default st.metric font size).
    st.markdown("""
        <style>
        div[data-testid="stMetricValue"] {
            font-size: 1.05rem;
            white-space: nowrap;
            overflow: visible;
        }
        div[data-testid="stMetricLabel"] {
            font-size: 0.78rem;
        }
        div[data-testid="stCheckbox"] label p {
            white-space: nowrap;
        }
        </style>
    """, unsafe_allow_html=True)

    for rank, row in results_df.iterrows():
        ticker      = row['ticker']
        is_checked  = ticker in _selected
        hurdle_icon, hurdle_label = hurdle_badge(row.get('debt_hurdle_cleared'))

        with st.container():
            _has_si = 'si_holders' in row.index
            if _has_si:
                c1, c2, c3, c4, c5, c6, c7, c8, c_mos, c9, c10 = st.columns(
                    [1, 2.2, 1.5, 1.5, 1.5, 1.5, 1.5, 1.5, 1.3, 1.1, 1.6])
            else:
                c1, c2, c3, c4, c5, c6, c7, c8, c_mos, c10 = st.columns(
                    [1, 2.6, 1.8, 1.8, 1.8, 1.8, 1.8, 1.8, 1.5, 1.4])
                c9 = None
            with c1:
                st.markdown(f"### {hurdle_icon}")
                st.markdown(f"**#{rank+1}**")
            with c2:
                st.markdown(f"**{ticker}**")
                st.caption(row.get('name', ''))
                st.caption(row.get('sub_industry') or row.get('sector', ''))
                _fin_subtype = row.get('financial_subtype')
                _badges = []
                if _fin_subtype == "bank":
                    _badges.append(f"🏦 Bank — alt score {row.get('financial_score', 'N/A')}/100")
                elif _fin_subtype == "insurance":
                    _badges.append(f"🛡️ Insurer — alt score {row.get('financial_score', 'N/A')}/100")
                if row.get('is_cyclical') and flag_cyclicals: _badges.append("⚠️ Cyclical")
                if row.get('is_negative_equity'):
                    _badges.append("📊 Negative Equity")
                if row.get('limited_history'):
                    _badges.append(f"📏 Limited History ({row.get('funnel_years_used','?')}y)")
                if row.get('roic_stale'):
                    _badges.append(f"🕰️ Stale ROIC (last reliable: {row.get('roic_last_reliable_period','?')}, {row.get('roic_stale_years','?')}y old)")
                if _badges:
                    st.caption(" · ".join(_badges))
                if _fin_subtype in ("bank", "insurance"):
                    _q_label = "Efficiency Ratio" if _fin_subtype == "bank" else "Combined Ratio (10yr avg)"
                    st.caption(
                        f"ROE (10yr avg): {fmt(row.get('roe_avg'), 'pct')} · "
                        f"{_q_label}: {fmt(row.get('quality_value'), 'pct')} · "
                        f"Equity/Assets: {fmt(row.get('capital_ratio'), 'pct')}"
                    )
            with c3: st.metric("ROIC (10yr avg)",      fmt(row.get('roic_avg'), "pct"),
                                help=f"{row.get('roic_avg_years','?')} years of history used")
            with c4: st.metric("FCF Margin (10yr avg)", fmt(row.get('fcf_margin_avg'), "pct"),
                                help=f"{row.get('fcf_margin_avg_years','?')} years of history used")
            with c5: st.metric("Debt Hurdle",           hurdle_label,
                                help=f"Debt/NI {fmt(row.get('debt_to_ni'),'ratio')} · Debt/CADS {fmt(row.get('debt_to_cads'),'ratio')}")
            with c6: st.metric("Dilution",              "✅ Passed" if row.get('dilution_passed') else "❌ Failed",
                                help=f"Shares chg: {fmt(row.get('dilution_pct_change'),'pct')}")
            with c7: st.metric("FCF Yield",             fmt(row.get('fcf_yield'), "pct"), help="Secondary valuation reference")
            with c8: st.metric("P/OE",                  fmt(row.get('price_owner_earn'), "ratio"), help="Secondary valuation reference")
            with c_mos:
                # margin_of_safety/intrinsic_value_per_share materialized
                # on results_df above (same DCF calc as Equity Scout/
                # Compare Stocks/Dashboard -- owner feedback: "would be
                # good to have this on all pages") so sorting/export and
                # the number shown here always agree, same pattern as
                # si_holders/si_score. Stage 2 already fetched real
                # price+shares for every survivor (see the P/OE calc just
                # above), so this uses the standard per-share DCF path.
                # No per-ticker _history carried through Stage 1/2 (keeps
                # a market-wide scan's memory footprint down), so the
                # growth-rate estimate uses compute_dcf_value's default
                # rather than this company's own historical FCF trend.
                # (2026-07-23, "+nan%" case) pd.notna(), not "is not
                # None" -- results_df's margin_of_safety/intrinsic_value_
                # per_share columns went through the same pandas .apply()
                # pattern as Dashboard's MoS column, which silently
                # upcasts a legitimate "couldn't compute" None into
                # float('nan') once the column is float64 -- see
                # sec_utils.compute_dcf_value()'s docstring for the fix
                # at the source too.
                # (2026-07-23) Two bugs owner caught testing this: (1)
                # current price was only ever shown INSIDE the "MoS
                # available" branch, so a ticker with no MoS showed
                # nothing at all, even when its price fetch had actually
                # succeeded -- price now shown unconditionally below,
                # decoupled from whether MoS/IV could be computed. (2)
                # bank/insurer rows always show "—" here, every time, by
                # design (see fetch_stage1_data_edgar()'s docstring: FCF
                # is deliberately left None for financial firms since
                # op_cf+inv_cf is dominated by loan/investment portfolio
                # volume, not a meaningful cash-flow figure for a
                # leveraged balance-sheet business) -- but a bare "—"
                # with no explanation reads identically to a bug. Labeled
                # explicitly instead so it's clearly "doesn't apply here"
                # rather than "broke."
                _mos    = row.get('margin_of_safety')
                _iv     = row.get('intrinsic_value_per_share')
                _price  = row.get('price')
                _is_fin = row.get('financial_subtype') in ("bank", "insurance")

                if _is_fin:
                    # (2026-07-23, owner: "is there another way to
                    # calculate the IV for insurers that is accurate?")
                    # Residual income model (materialized above) instead
                    # of the FCF-DCF, which always errors for these rows
                    # by design. Multi-stage (ROE fades toward normalized)
                    # shown as the headline number since it's the more
                    # defensible one for a cyclical name -- single-stage
                    # and the gap between them in the caption, same "show
                    # both, flag a big gap" approach as the debug tool.
                    _ri_multi_mos  = row.get('ri_multi_mos')
                    _ri_single_mos = row.get('ri_single_mos')
                    _ri_multi_iv   = row.get('ri_multi_iv')
                    _ri_div        = row.get('ri_divergence')
                    if pd.notna(_ri_multi_mos):
                        st.metric("MoS (Residual Income)", f"{_ri_multi_mos:+.0%}",
                                  help="Multi-stage residual income model (ROE fades to normalized) -- see Debug tool above for full detail")
                    else:
                        st.metric("MoS (Residual Income)", "N/A", help="ROE, book value, or shares unavailable for this ticker")
                    _bits = []
                    if pd.notna(_price):
                        _bits.append(f"${_price:.0f} now")
                    if pd.notna(_ri_multi_iv):
                        _bits.append(f"${_ri_multi_iv:.0f} target")
                    if pd.notna(_ri_single_mos):
                        _bits.append(f"single-stage: {_ri_single_mos:+.0%}")
                    if _bits:
                        st.caption(" → ".join(_bits))
                    if pd.notna(_ri_div) and _ri_div >= 0.30:
                        st.caption("⚠️ ROE well off normal — see Debug tool")
                elif pd.notna(_mos):
                    st.metric("Margin of Safety", f"{_mos:+.0%}",
                              help="DCF intrinsic value (default assumptions)")
                    _price_bits = []
                    if pd.notna(_price):
                        _price_bits.append(f"${_price:.0f} now")
                    if pd.notna(_iv):
                        _price_bits.append(f"${_iv:.0f} target")
                    if _price_bits:
                        st.caption(" → ".join(_price_bits))
                else:
                    st.metric("Margin of Safety", "—", help="FCF, price, or shares unavailable for this ticker")
                    if pd.notna(_price):
                        st.caption(f"${_price:.0f} now")
            if _has_si and c9 is not None:
                with c9:
                    si_n     = int(row.get('si_holders', 0))
                    si_score = int(row.get('si_score', 0))
                    si_color = "#2ecc71" if si_n >= 5 else "#f39c12" if si_n >= 2 else "#888"
                    st.markdown(
                        f"<div style='text-align:center'><span style='font-weight:bold; color:{si_color}; font-size:1.3em'>🦁 {si_n}</span></div>",
                        unsafe_allow_html=True
                    )
                    st.caption(f"{si_score}/100 SI")
            with c10:
                # Checkbox — limit selection to 5
                _at_limit = len(_selected) >= 5 and ticker not in _selected
                st.caption("🔬 Dive")
                checked = st.checkbox(
                    "Select",
                    value=is_checked,
                    key=f"ms_chk_{ticker}_{rank}",
                    disabled=_at_limit,
                    help="Max 5 selected" if _at_limit else f"Add {ticker} to deep dive",
                )
                if checked and ticker not in _selected:
                    _selected.append(ticker)
                    st.session_state['ms_selected_tickers'] = _selected
                elif not checked and ticker in _selected:
                    _selected.remove(ticker)
                    st.session_state['ms_selected_tickers'] = _selected

                # Add-only Watchlist control (#68) -- separate from the
                # "Select" checkbox above (that one drives Compare, this
                # one drives the Watchlist page). Removal only happens on
                # the Watchlist page itself.
                _already_watched = is_watchlisted(ticker)
                _watch_checked = st.checkbox(
                    "⭐ Watch",
                    value=_already_watched,
                    key=f"ms_watch_{ticker}_{rank}",
                    disabled=_already_watched,
                    help="On Watchlist" if _already_watched else f"Add {ticker} to Watchlist",
                )
                if _watch_checked and not _already_watched:
                    add_to_watchlist(ticker, name=row.get('name', ticker), source="Market Screener")
                    st.rerun()

            div = row.get('dividend_yield')
            if div is not None and not (isinstance(div, float) and pd.isna(div)) and div > 0:
                st.caption(f"💰 Dividend Yield: {div:.2%}")
            if row.get('is_net_creditor'): st.caption("✨ Net Creditor")
            st.markdown(f"[🔍 Deep Dive in Equity Scout]({APP_URL}/equity_scout?ticker={ticker}&auto=1)")
            st.divider()

    st.markdown("### 📊 Screen Summary")
    s1, s2, s3, s4 = st.columns(4)
    with s1: st.metric("Scanned",                total_tickers)
    with s2: st.metric("Checklist Survivors",    st.session_state.get('ms_edgar_results_count', len(results_df)))
    with s3: st.metric("Avg 10yr ROIC",          fmt(results_df['roic_avg'].mean() if 'roic_avg' in results_df else None, "pct"))
    with s4: st.metric("Cleared Both Hurdles",   len(results_df[results_df.get('debt_hurdle_cleared') == 'both']) if 'debt_hurdle_cleared' in results_df else 0)

    st.markdown("### 💾 Export Results")
    _export_cols = ['ticker','name','sector','industry','sub_industry',
                     'financial_subtype','financial_score','roe_avg','quality_leg','quality_value','capital_ratio',
                     'roic_avg','roic_avg_years','fcf_margin_avg','fcf_margin_avg_years',
                     'debt_to_ni','debt_to_cads','debt_hurdle_cleared',
                     'dilution_passed','dilution_pct_change','limited_history','funnel_years_used',
                     'is_cyclical','is_negative_equity','roic_stale','roic_stale_years','roic_last_reliable_period',
                     'fcf_yield','price_owner_earn','dividend_yield','price','market_cap',
                     'margin_of_safety','intrinsic_value_per_share']
    _export_names = ['Ticker','Name','Sector','Industry','Sub-Industry',
                      'Financial Subtype','Financial Alt Score','ROE (10yr avg)','Quality Leg','Quality Value','Equity/Assets',
                      'ROIC (10yr avg)','ROIC Years Used','FCF Margin (10yr avg)','FCF Margin Years Used',
                      'Debt/Net Income','Debt/CADS','Debt Hurdle Cleared',
                      'Dilution Passed','Shares Chg (5yr)','Limited History','Funnel Years Used',
                      'Cyclical','Negative Equity','Stale ROIC','Stale ROIC Years','ROIC Last Reliable Year',
                      'FCF Yield','Price/Owner Earnings','Dividend Yield','Price','Market Cap',
                      'Margin of Safety (DCF)','DCF Intrinsic Value/Share']
    if 'si_holders' in results_df.columns:
        _export_cols  += ['si_holders', 'si_score']
        _export_names += ['SI Holders', 'SI Conviction Score']
    # Guard against missing columns (e.g. if industry/sub_industry weren't populated)
    _available = [c for c in _export_cols if c in results_df.columns]
    _available_names = [n for c, n in zip(_export_cols, _export_names) if c in results_df.columns]
    export_df = results_df[_available].copy()
    export_df.columns = _available_names
    st.download_button(label="⬇️ Download Results as CSV", data=export_df.to_csv(index=False),
                        file_name="voskuil_screen_results.csv", mime="text/csv")

    # ── Ask Claude Panel ──────────────────────────────────────────────
    st.divider()
    st.markdown("### 🤖 Ask Claude — Analyze These Results")
    st.caption(
        "Claude reasons over the full screen results (scores, ratios, sectors) to help you "
        "narrow down candidates before you commit to a deeper look. For actual SEC filing "
        "text and qualitative analysis, select tickers below and use Compare — that page has "
        "its own Claude agent with 10-K access."
    )

    # ── Compare buttons ────────────────────────────────────────────
    top3_tickers     = results_df['ticker'].head(3).tolist()
    selected_tickers = st.session_state.get('ms_selected_tickers', [])

    dd_col1, dd_col2, dd_col3 = st.columns([2, 2, 3])
    with dd_col1:
        if st.button("⚖️ Compare Top 3", type="primary", use_container_width=True,
                     help="Open the Compare page for the top 3 scored tickers"):
            st.session_state['compare_tickers'] = top3_tickers
            st.session_state['compare_weights']  = DEFAULT_WEIGHTS.copy()
            st.session_state['ms_selected_tickers'] = []
            st.switch_page("app_pages/9_Compare_Stocks_EDGAR.py")
    with dd_col2:
        n_sel_cmp = len(selected_tickers)
        _cmp_disabled = n_sel_cmp < 2
        if st.button(
            f"⚖️ Compare Selected ({n_sel_cmp})",
            type="primary" if n_sel_cmp >= 2 else "secondary",
            use_container_width=True,
            disabled=_cmp_disabled,
            help=f"Side-by-side comparison for: {', '.join(selected_tickers)}" if selected_tickers else "Check at least 2 boxes to compare",
        ):
            st.session_state['compare_tickers'] = selected_tickers.copy()
            st.session_state['compare_weights']  = DEFAULT_WEIGHTS.copy()
            st.switch_page("app_pages/9_Compare_Stocks_EDGAR.py")
    with dd_col3:
        if selected_tickers:
            st.caption(f"✅ Selected: {', '.join(selected_tickers)}")
        else:
            st.caption("☑️ Check boxes next to any result to select for comparison (2-5), or use Compare Top 3")

    # ── Conversation state ────────────────────────────────────────────
    ms_convo_key   = "ms_claude_convo"
    ms_context_key = "ms_claude_context_sent"
    if ms_convo_key not in st.session_state:
        st.session_state[ms_convo_key]   = []
        st.session_state[ms_context_key] = False

    # Display history
    for msg in st.session_state[ms_convo_key]:
        role = msg["role"]
        content = msg["content"]
        if role == "user":
            if "\n---\nQUESTION: " in content:
                content = content.split("\n---\nQUESTION: ", 1)[-1]
            with st.chat_message("user"):
                st.markdown(content)
        else:
            with st.chat_message("assistant", avatar="🤖"):
                st.markdown(content)

    # Suggested starters (only before first message)
    if not st.session_state[ms_convo_key]:
        st.markdown("**Suggested questions:**")
        sq_cols = st.columns(2)
        from claude_utils import get_user_profile
        _sp  = get_user_profile()
        _wd2 = _sp.get('monthly_withdrawal', 8000)
        ms_starters = [
            f"Which fits best for our ${_wd2:,.0f}/month retirement income target?",
            "Which of these look like they'd survive Munger's inversion test?",
            "Group these by sector — where's the overlap and where's the diversification?",
            "Which 3-5 would you shortlist for a closer look, and why?",
        ]
        for i, q in enumerate(ms_starters):
            with sq_cols[i % 2]:
                if st.button(q, key=f"ms_starter_{i}", use_container_width=True):
                    st.session_state["ms_pending_claude_q"] = q
                    st.rerun()

    # ── Deferred chat_input mount (cold-load scroll fix, same as
    # Dashboard's/Equity Scout's/Compare Stocks') ───────────────────────
    # st.chat_input's mere presence makes Streamlit wrap the page in its
    # own auto-scroll-to-bottom chat container -- see ui_utils.py's
    # scroll-fix docstring for the full story. Deferring the widget
    # itself behind a click means nothing creates that container on a
    # fresh load of this page either.
    if "ms_chat_enabled" not in st.session_state:
        st.session_state["ms_chat_enabled"] = bool(st.session_state[ms_convo_key])

    ms_pending_q = st.session_state.pop("ms_pending_claude_q", None)
    if ms_pending_q:
        st.session_state["ms_chat_enabled"] = True

    if not st.session_state["ms_chat_enabled"]:
        if st.button("💬 Ask Claude about these screen results", key="ms_enable_chat"):
            st.session_state["ms_chat_enabled"] = True
            st.rerun()
        ms_user_q = None
    else:
        ms_user_q = st.chat_input("Ask Claude about these screen results...", key="ms_claude_input")
    ms_active_q  = ms_pending_q or ms_user_q

    if ms_active_q:
        with st.chat_message("user"):
            st.markdown(ms_active_q)

        with st.chat_message("assistant", avatar="🤖"):
            with st.spinner("Analyzing..."):
                # Quant-only context — no filing text. This chat is for narrowing
                # down candidates pre-selection; the Compare page has its own
                # Claude agent with actual 10-K access for the shortlist.
                if not st.session_state[ms_context_key]:
                    context_str = build_ms_context(results_df) + f"\n\n---\nQUESTION: {ms_active_q}"
                    response = ask_claude_about_equity(
                        ticker="SCREEN", data={}, scores={}, sections={},
                        user_question=context_str,
                        conversation_history=None,
                    )
                    st.session_state[ms_convo_key].append({"role": "user", "content": context_str})
                    st.session_state[ms_context_key] = True
                else:
                    response = ask_claude_about_equity(
                        ticker="SCREEN", data={}, scores={}, sections={},
                        user_question=ms_active_q,
                        conversation_history=st.session_state[ms_convo_key],
                    )
                    st.session_state[ms_convo_key].append({"role": "user", "content": ms_active_q})

                st.session_state[ms_convo_key].append({"role": "assistant", "content": response})
                st.markdown(response)

    if st.session_state.get(ms_convo_key):
        if st.button("🗑️ Clear conversation", key="ms_clear_convo"):
            st.session_state[ms_convo_key]   = []
            st.session_state[ms_context_key] = False
            st.rerun()

else:
    st.markdown("""
    ### What this screener does — Two-Stage Architecture

    **Stage 1 — Quality Scan (EDGAR, no price needed)**
    1. **Loads your selected universe** — S&P 500 (Wikipedia) or the full US common stock list (~6,000+, via Nasdaq Trader's free Symbol Directory)
    2. **Resolves all tickers to CIKs** in one shot (not one lookup per ticker)
    3. **Fetches fundamentals from SEC EDGAR** in parallel — ROIC, Debt/FCF, Gross Margin, Interest Coverage
    4. **Eliminates** companies with negative Free Cash Flow
    5. **Filters to quality survivors** — must clear 55% of price-independent points

    **Stage 2 — Valuation Check (only survivors)**
    6. **Fetches live price** via yfinance for quality survivors only — not all 500
    7. **Completes scoring** with FCF Yield, and shows Price/Owner Earnings as a reference valuation metric (not scored)
    8. **Returns top results** ranked by full conviction score

    This mirrors Buffett/Munger philosophy structurally: a company can't screen well by being
    cheap — it has to earn its way to Stage 2 on business quality first.

    ### Features
    - 🤖 **Ask Claude** — reasons over the full screen results to help narrow down candidates
    - ⚖️ **Compare Top 3 / Compare Selected** — opens the Compare Stocks page for a side-by-side
      breakdown (score, financials, historical trends) and its own Claude agent with SEC 10-K access
    - 🦁 **Superinvestor Conviction** — see how many of 82 tracked value investors hold each result
    - **Net Creditor detection** — companies earning more interest than they pay score full points
    - **Financial firm filtering** — banks/insurers excluded by default (different statement structure)

    ---
    **Score guide:** 🟢 80+ Strong Buy · 🟡 65-79 Watch · 🟠 45-64 Caution · 🔴 <45 Avoid

    *Fundamentals sourced directly from SEC EDGAR Company Facts API — free, no rate-limit risk at this scale, no third-party normalization layer.*
    """)

