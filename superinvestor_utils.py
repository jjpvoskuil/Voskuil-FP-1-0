"""
superinvestor_utils.py — Superinvestor conviction tracker for Voskuil FP 1.0

Scrapes Dataroma.com for superinvestor holdings data.
Dataroma aggregates 13F filings into clean, per-investor portfolio pages
with ticker, % of portfolio, shares, recent activity, and value.

No API key required. Data sourced from public 13F filings via Dataroma.
"""

import re
import time
import requests
import streamlit as st
from bs4 import BeautifulSoup

# Browser headers to avoid bot detection
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

BASE_URL = "https://www.dataroma.com"

# Curated superinvestors with their Dataroma manager codes
# Format: display_name -> dataroma_code
SUPERINVESTORS = {
    "Warren Buffett (Berkshire)":  "BRK",
    "Bill Ackman (Pershing Sq)":   "PA",
    "Seth Klarman (Baupost)":      "BG",
    "David Tepper (Appaloosa)":    "APL",
    "David Einhorn (Greenlight)":  "GLC",
    "Chuck Akre (Akre Capital)":   "ACM",
    "Tom Gayner (Markel)":         "MKL",
    "Mohnish Pabrai (Pabrai Inv)": "PI",
    "Li Lu (Himalaya Capital)":    "HC",
    "Guy Spier (Aquamarine)":      "aq",
    "Chris Bloomstran (Semper)":   "SEM",
    "Pat Dorsey (Dorsey Asset)":   "DA",
    "Allan Mecham (Arlington)":    "AVI",
}


def _fetch_holdings(manager_code: str) -> list:
    """
    Fetch holdings for one manager from Dataroma.
    Returns list of dicts: {ticker, name, pct, activity, value}
    """
    url  = f"{BASE_URL}/m/holdings.php?m={manager_code}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        grid = soup.find("table", {"id": "grid"})
        if not grid:
            return []

        holdings = []
        rows = grid.find_all("tr")[1:]  # skip header row
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 5:
                continue
            try:
                # Column order: Stock | % of Portfolio | Shares | Recent Activity | Reported Price | Value
                stock_text    = cells[0].text.strip()  # e.g. "AAPL - Apple Inc."
                pct_text      = cells[1].text.strip()  # e.g. "25.96"
                activity_text = cells[3].text.strip()  # e.g. "Add 5.20%" or "New"

                # Parse ticker and company name
                if " - " in stock_text:
                    ticker, name = stock_text.split(" - ", 1)
                else:
                    ticker = stock_text
                    name   = stock_text

                holdings.append({
                    "ticker":   ticker.strip().upper(),
                    "name":     name.strip(),
                    "pct":      float(pct_text) if pct_text else 0.0,
                    "activity": activity_text,
                })
            except Exception:
                continue

        return holdings

    except Exception:
        return []


def clear_superinvestor_cache():
    """Clear per-ticker cached results."""
    for key in list(st.session_state.keys()):
        if key.startswith("si_"):
            del st.session_state[key]


def get_superinvestor_conviction(ticker: str) -> dict:
    """
    Main entry point. Scrapes Dataroma for each superinvestor and checks
    whether they hold the given ticker.

    Returns:
        holders:          list of {investor, pct, activity}
        holder_count:     int
        conviction_score: 0-100
        period:           str (data period label)
        error:            str or None
    """
    ticker_upper = ticker.upper()
    holders      = []
    errors       = []

    # Per-investor holdings are cached to avoid re-fetching on rerun
    for investor, code in SUPERINVESTORS.items():
        cache_key = f"_dr_holdings_{code}"
        if cache_key not in st.session_state:
            holdings = _fetch_holdings(code)
            st.session_state[cache_key] = holdings
            time.sleep(0.3)  # be polite — don't hammer Dataroma
        else:
            holdings = st.session_state[cache_key]

        if not holdings:
            errors.append(f"{investor}: no data")
            continue

        # Check if ticker is in this investor's portfolio
        match = next((h for h in holdings if h["ticker"] == ticker_upper), None)
        if match:
            holders.append({
                "investor": investor,
                "pct":      match["pct"],
                "activity": match["activity"],
            })

    holders.sort(key=lambda x: x["pct"], reverse=True)

    n       = len(holders)
    max_n   = len(SUPERINVESTORS)
    avg_pct = sum(h["pct"] for h in holders) / n if n > 0 else 0
    breadth = min(60, int(n / max_n * 60))
    weight  = min(40, int(avg_pct / 10 * 40))
    score   = breadth + weight

    return {
        "holders":          holders,
        "holder_count":     n,
        "conviction_score": score,
        "period":           "Latest 13F (via Dataroma)",
        "error":            "; ".join(errors) if errors and n == 0 else None,
    }
