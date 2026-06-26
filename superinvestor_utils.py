"""
superinvestor_utils.py — Superinvestor conviction tracker for Voskuil FP 1.0

Fetches the Dataroma Grand Portfolio — all 1,600+ stocks held by all
superinvestors in one page, with owner count and % of grand portfolio.

Data source: https://www.dataroma.com/m/g/portfolio.php
No API key required.
"""

import requests
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

# URL returns all ~1600+ stocks sorted by grand portfolio %
# l=o sorts by ownership%, o=c shows all stocks, f=0 removes minimum filter
GRAND_PORTFOLIO_URL = "https://www.dataroma.com/m/g/portfolio.php?l=o&o=c&f=0"


def fetch_grand_portfolio() -> dict:
    """
    Fetch and parse the Dataroma Grand Portfolio.

    Column layout (confirmed from live page):
      Symbol | Stock Name | % of Grand Portfolio | No. of Owners | Hold Price | ...

    Returns dict keyed by uppercase ticker:
    {
        "ABBV": {"ticker": "ABBV", "name": "AbbVie Inc.", "owners": 7, "pct_grand": 0.45},
        ...
        "_meta": {"total_stocks": 1676, "source": "Dataroma"}
    }
    """
    try:
        resp = requests.get(GRAND_PORTFOLIO_URL, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            return {"_error": f"HTTP {resp.status_code} from Dataroma"}

        soup = BeautifulSoup(resp.text, "html.parser")

        # Find the holdings table
        grid = soup.find("table", {"id": "grid"})
        if not grid:
            tables = soup.find_all("table")
            # Pick the largest table
            grid = max(tables, key=lambda t: len(t.find_all("tr"))) if tables else None

        if not grid:
            return {"_error": "No data table found on Grand Portfolio page"}

        # Confirmed column layout from live Dataroma page:
        # Col 0: Symbol (MSFT, AMZN, etc.)
        # Col 1: Stock name (Microsoft Corp., Amazon.com Inc., etc.)
        # Col 2: % of Grand Portfolio (2.439, 2.123, etc.)
        # Col 3: Number of superinvestor owners (33, 26, etc.)
        # Col 4: Hold Price ($421.50, etc.)
        # Col 5: Max % (28.55, etc.)
        sym_idx  = 0
        name_idx = 1
        pct_idx  = 2
        own_idx  = 3

        headers = []

        portfolio = {}
        rows      = grid.find_all("tr")[1:]  # skip header

        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            try:
                cell_texts = [c.text.strip() for c in cells]

                # Symbol
                ticker = cell_texts[sym_idx].strip().upper()
                if not ticker or len(ticker) > 6 or not ticker.replace(".", "").replace("-", "").isalpha():
                    continue

                # Name
                name = cell_texts[name_idx].strip() if len(cell_texts) > name_idx else ticker

                # % of Grand Portfolio
                try:
                    pct_grand = float(cell_texts[pct_idx].replace("%", "").replace(",", "").strip())
                except (ValueError, IndexError):
                    pct_grand = 0.0

                # Number of owners
                try:
                    owners = int(cell_texts[own_idx].replace(",", "").strip())
                except (ValueError, IndexError):
                    owners = 0

                if ticker:
                    portfolio[ticker] = {
                        "ticker":    ticker,
                        "name":      name,
                        "owners":    owners,
                        "pct_grand": pct_grand,
                    }

            except Exception:
                continue

        portfolio["_meta"] = {
            "total_stocks": len(portfolio),
            "source":       "Dataroma Grand Portfolio",
        }

        return portfolio

    except requests.Timeout:
        return {"_error": "Timeout fetching Dataroma (30s). Try refreshing."}
    except Exception as e:
        return {"_error": str(e)}


def get_grand_portfolio() -> dict:
    """Returns cached Grand Portfolio. Fetches on first call per session."""
    import streamlit as st
    if "_gp_data" not in st.session_state:
        with st.spinner("📊 Loading superinvestor Grand Portfolio from Dataroma..."):
            st.session_state["_gp_data"] = fetch_grand_portfolio()
    return st.session_state.get("_gp_data", {})


def clear_superinvestor_cache():
    """Clear Grand Portfolio cache — forces re-fetch."""
    import streamlit as st
    st.session_state.pop("_gp_data", None)
    for key in list(st.session_state.keys()):
        if key.startswith("si_"):
            del st.session_state[key]


def get_superinvestor_conviction(ticker: str) -> dict:
    """Look up conviction data for a ticker from the Grand Portfolio."""
    gp    = get_grand_portfolio()
    error = gp.get("_error")

    if error:
        return {
            "holders": [], "holder_count": 0, "conviction_score": 0,
            "pct_grand": 0.0, "period": "Dataroma",
            "error": f"Grand Portfolio fetch failed: {error}",
        }

    ticker_upper = ticker.upper()
    entry        = gp.get(ticker_upper)
    meta         = gp.get("_meta", {})

    if not entry:
        return {
            "holders": [], "holder_count": 0, "conviction_score": 0,
            "pct_grand": 0.0, "period": "Latest 13F (Dataroma)",
            "total_stocks": meta.get("total_stocks", 0),
            "error": None,
        }

    owners    = entry.get("owners",    0)
    pct_grand = entry.get("pct_grand", 0.0)
    name      = entry.get("name",      ticker_upper)

    # Score: up to 70 pts breadth + 30 pts weight
    breadth = min(70, int(owners / 80 * 70))
    weight  = min(30, int(pct_grand / 5 * 30))
    score   = breadth + weight

    return {
        "holders":          [],
        "holder_count":     owners,
        "name":             name,
        "conviction_score": score,
        "pct_grand":        pct_grand,
        "period":           "Latest 13F (Dataroma)",
        "total_stocks":     meta.get("total_stocks", 0),
        "error":            None,
    }


def get_all_tickers_with_conviction() -> dict:
    """Returns full Grand Portfolio dict for bulk Market Screener scoring."""
    gp = get_grand_portfolio()
    return {k: v for k, v in gp.items() if not k.startswith("_")}
