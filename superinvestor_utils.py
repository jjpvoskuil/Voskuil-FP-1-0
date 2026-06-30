"""
superinvestor_utils.py — Superinvestor conviction tracker for Voskuil FP 1.0

Fetches complete portfolios for all ~82 superinvestors tracked by Dataroma.
Builds a full ticker -> investors lookup covering all ~1,676 holdings.

Strategy:
  1. Fetch managers.php to discover all manager codes dynamically
  2. Fetch each manager's holdings.php in parallel batches
  3. Build ticker_map: {ticker: [{name, pct, activity}]}
  4. Cache entire map in session state

First load: ~30-60 seconds. Subsequent lookups: instant.
Data source: Dataroma.com (aggregates SEC 13F filings). No API key required.
"""

import re
import time
import requests
import concurrent.futures
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer":         "https://www.dataroma.com/",
    "Connection":      "keep-alive",
}

BASE_URL     = "https://www.dataroma.com"
MANAGERS_URL = f"{BASE_URL}/m/managers.php"
HOLDINGS_URL = f"{BASE_URL}/m/holdings.php?m="

# Parallel workers — keep low to be polite to Dataroma
MAX_WORKERS  = 5
REQUEST_DELAY = 0.3   # seconds between requests per worker


def fetch_manager_list() -> list:
    """
    Fetch managers.php and extract all manager {name, code} pairs.
    Returns list of dicts: [{name, code}]
    """
    try:
        resp = requests.get(MANAGERS_URL, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return []

        soup     = BeautifulSoup(resp.text, "html.parser")
        managers = []
        seen     = set()

        for link in soup.find_all("a", href=re.compile(r"/m/holdings\.php\?m=")):
            href       = link.get("href", "")
            code_match = re.search(r"\?m=([^&\s]+)", href)
            if not code_match:
                continue
            code = code_match.group(1).strip()
            if code in seen:
                continue
            seen.add(code)

            # Manager name: use link text or parent cell text
            name = link.text.strip()
            if not name:
                td = link.find_parent("td")
                name = td.text.strip().split("\n")[0] if td else code

            if code and name:
                managers.append({"name": name, "code": code})

        return managers
    except Exception:
        return []


def fetch_one_portfolio(manager: dict) -> dict:
    """
    Fetch complete holdings for one manager.
    Returns {name, code, holdings: [{ticker, pct, activity}]}
    """
    name = manager["name"]
    code = manager["code"]
    try:
        time.sleep(REQUEST_DELAY)
        resp = requests.get(f"{HOLDINGS_URL}{code}", headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return {"name": name, "code": code, "holdings": [], "error": f"HTTP {resp.status_code}"}

        soup = BeautifulSoup(resp.text, "html.parser")
        grid = soup.find("table", {"id": "grid"})
        if not grid:
            return {"name": name, "code": code, "holdings": [], "error": "No grid table"}

        holdings = []
        rows     = grid.find_all("tr")[1:]   # skip header

        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 4:
                continue
            try:
                # Col 0: ≡ history button (skip)
                # Col 1: "TICKER - Company Name"
                # Col 2: % of portfolio
                # Col 3: Recent activity OR shares (activity is optional)
                if len(cells) < 3:
                    continue

                stock_text = cells[1].text.strip()
                if " - " in stock_text:
                    ticker = stock_text.split(" - ")[0].strip().upper()
                elif stock_text:
                    ticker = stock_text.split()[0].strip().upper()
                else:
                    continue

                if not ticker or len(ticker) > 6 or not ticker.replace(".", "").replace("-", "").isalpha():
                    continue

                # Col 2: % of portfolio
                pct = 0.0
                try:
                    pct_text = cells[2].text.strip().replace("%", "").replace(",", "").strip()
                    if pct_text and pct_text not in ("-", "N/A", ""):
                        pct = float(pct_text)
                except (ValueError, IndexError):
                    pct = 0.0
                # If pct is 0 try col 3 in case row has no history column
                if pct == 0.0 and len(cells) > 3:
                    try:
                        pct_text = cells[3].text.strip().replace("%", "").replace(",", "").strip()
                        val = float(pct_text)
                        if 0 < val < 100:
                            pct = val
                    except (ValueError, IndexError):
                        pass

                # Col 3: Recent activity if present (Add X%, Reduce X%, New, Sold)
                # Sometimes col 3 is shares if no activity — detect by checking for letters
                activity = ""
                try:
                    col3 = cells[3].text.strip()
                    if any(w in col3 for w in ["Add", "Reduce", "New", "Sold", "Buy"]):
                        activity = col3
                except IndexError:
                    pass

                holdings.append({
                    "ticker":   ticker,
                    "pct":      pct,
                    "activity": activity,
                })
            except Exception:
                continue

        return {"name": name, "code": code, "holdings": holdings, "error": None}

    except requests.Timeout:
        return {"name": name, "code": code, "holdings": [], "error": "Timeout"}
    except Exception as e:
        return {"name": name, "code": code, "holdings": [], "error": str(e)}


def build_full_conviction_map() -> dict:
    """
    Fetch all manager portfolios and build a complete ticker->investors map.
    Returns:
    {
        "ticker_map": {
            "ABBV": [{"investor": "Li Lu", "pct": 4.2, "activity": "Add 15%"}, ...],
            ...
        },
        "managers": [{"name", "code", "holdings", "error"}],
        "total_managers": 82,
        "total_holdings": 2456,
        "error": None or str
    }
    """
    # Step 1: get manager list
    managers = fetch_manager_list()
    if not managers:
        return {
            "ticker_map": {}, "managers": [], "total_managers": 0,
            "total_holdings": 0, "error": "Could not fetch manager list from Dataroma",
        }

    # Step 2: fetch all portfolios in parallel
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_one_portfolio, m): m for m in managers}
        for future in concurrent.futures.as_completed(futures):
            try:
                results.append(future.result())
            except Exception:
                pass

    # Step 3: build ticker map
    ticker_map     = {}
    total_holdings = 0

    for result in results:
        investor_name = result["name"]
        for holding in result.get("holdings", []):
            ticker   = holding["ticker"]
            pct      = holding["pct"]
            activity = holding["activity"]
            total_holdings += 1

            if ticker not in ticker_map:
                ticker_map[ticker] = []

            ticker_map[ticker].append({
                "investor": investor_name,
                "pct":      pct,
                "activity": activity,
            })

    # Sort each ticker's investors by portfolio % descending
    for ticker in ticker_map:
        ticker_map[ticker].sort(key=lambda x: x["pct"], reverse=True)

    return {
        "ticker_map":     ticker_map,
        "managers":       results,
        "total_managers": len(managers),
        "total_holdings": total_holdings,
        "error":          None,
    }


def get_conviction_data() -> dict:
    """Returns cached full conviction map. Builds on first call."""
    import streamlit as st
    if "_si_full_map" not in st.session_state:
        with st.spinner(
            "📊 Loading complete superinvestor portfolios from Dataroma "
            "(82 managers — first load ~30-60 seconds, then cached)..."
        ):
            st.session_state["_si_full_map"] = build_full_conviction_map()
    return st.session_state.get("_si_full_map", {})


def clear_superinvestor_cache():
    """Clear all cached superinvestor data."""
    import streamlit as st
    for key in ["_si_full_map", "_gp_data", "_si_data"]:
        st.session_state.pop(key, None)
    for k in list(st.session_state.keys()):
        if k.startswith("si_"):
            del st.session_state[k]


def get_superinvestor_conviction(ticker: str) -> dict:
    """
    Look up complete conviction data for a ticker.
    Returns all investors holding it with portfolio % and recent activity.
    """
    data       = get_conviction_data()
    error      = data.get("error")
    ticker_map = data.get("ticker_map", {})

    if error and not ticker_map:
        return {
            "holders": [], "holder_count": 0, "conviction_score": 0,
            "period": "Dataroma", "error": error,
        }

    ticker_upper = ticker.upper()
    holders      = ticker_map.get(ticker_upper, [])
    n            = len(holders)
    total_mgrs   = data.get("total_managers", 82)
    avg_pct      = sum(h["pct"] for h in holders) / n if n > 0 else 0

    # Score: breadth (up to 60 pts) + weight (up to 40 pts)
    # Breadth: 1 holder=10, 3=25, 5=40, 10=60, 20+=60
    breadth = min(60, n * 6)
    # Weight: avg portfolio % — 5%+ avg is very high conviction
    weight  = min(40, int(avg_pct / 5 * 40))
    score   = breadth + weight

    return {
        "holders":          holders,
        "holder_count":     n,
        "conviction_score": score,
        "avg_pct":          round(avg_pct, 2),
        "period":           "Latest 13F (via Dataroma)",
        "total_managers":   total_mgrs,
        "total_holdings":   data.get("total_holdings", 0),
        "error":            None,
    }


def get_all_tickers_with_conviction() -> dict:
    """
    Returns {ticker: {"owners": N, "avg_pct": X, "investors": [...]}}
    for use in Market Screener bulk scoring.
    """
    data       = get_conviction_data()
    ticker_map = data.get("ticker_map", {})
    result     = {}
    for ticker, holders in ticker_map.items():
        n       = len(holders)
        avg_pct = sum(h["pct"] for h in holders) / n if n > 0 else 0
        result[ticker] = {
            "owners":    n,
            "avg_pct":   round(avg_pct, 2),
            "investors": holders,
        }
