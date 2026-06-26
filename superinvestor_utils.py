"""
superinvestor_utils.py — Superinvestor conviction tracker for Voskuil FP 1.0

Phase 1: Fetches the Dataroma Grand Portfolio — all stocks held by all
superinvestors in one page. Cached in session state for instant lookups.

Data source: https://www.dataroma.com/m/g/portfolio.php
Updated quarterly after 13F filings. No API key required.
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

GRAND_PORTFOLIO_URL = "https://www.dataroma.com/m/g/portfolio.php"
STOCK_DETAIL_URL    = "https://www.dataroma.com/m/stock.php?sym={ticker}"


def fetch_grand_portfolio() -> dict:
    """
    Fetch the Dataroma Grand Portfolio page and parse all holdings.

    Returns dict keyed by uppercase ticker:
    {
        "AAPL": {
            "ticker":    "AAPL",
            "name":      "Apple Inc.",
            "owners":    12,         # number of superinvestors holding
            "pct_grand": 8.45,       # % of grand portfolio
        },
        ...
    }
    Returns empty dict on failure.
    """
    try:
        resp = requests.get(GRAND_PORTFOLIO_URL, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return {"_error": f"HTTP {resp.status_code} from Dataroma"}

        soup = BeautifulSoup(resp.text, "html.parser")

        # Dataroma uses <table id="grid"> for the holdings table
        grid = soup.find("table", {"id": "grid"})
        if not grid:
            # Try any table with stock data
            tables = soup.find_all("table")
            grid   = tables[0] if tables else None

        if not grid:
            return {"_error": "No data table found on Grand Portfolio page"}

        portfolio = {}
        rows      = grid.find_all("tr")[1:]  # skip header

        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            try:
                # Grand Portfolio columns vary — try to detect layout
                # Common layout: Stock | No. of Investors | % of Grand Portfolio | ...
                cell_texts = [c.text.strip() for c in cells]

                # First cell usually has ticker and name: "AAPL - Apple Inc."
                stock_cell = cell_texts[0]
                if " - " in stock_cell:
                    ticker, name = stock_cell.split(" - ", 1)
                elif stock_cell:
                    ticker = stock_cell.split()[0]
                    name   = stock_cell
                else:
                    continue

                ticker = ticker.strip().upper()
                if not ticker or len(ticker) > 6:
                    continue

                # Parse owner count and grand portfolio %
                # Try different column positions
                owners    = 0
                pct_grand = 0.0

                for cell_text in cell_texts[1:]:
                    clean = cell_text.replace(",", "").replace("%", "").strip()
                    try:
                        val = float(clean)
                        if val == int(val) and 1 <= val <= 200 and owners == 0:
                            owners = int(val)
                        elif 0 < val < 100 and pct_grand == 0.0:
                            pct_grand = val
                    except ValueError:
                        continue

                portfolio[ticker] = {
                    "ticker":    ticker,
                    "name":      name.strip(),
                    "owners":    owners,
                    "pct_grand": pct_grand,
                }

            except Exception:
                continue

        if portfolio:
            portfolio["_meta"] = {
                "total_stocks": len(portfolio),
                "source": "Dataroma Grand Portfolio",
            }

        return portfolio

    except requests.Timeout:
        return {"_error": "Timeout fetching Dataroma Grand Portfolio"}
    except Exception as e:
        return {"_error": str(e)}


def get_grand_portfolio() -> dict:
    """
    Returns cached Grand Portfolio dict. Fetches on first call per session.
    Cached in st.session_state['_gp_data'].
    """
    import streamlit as st

    if "_gp_data" not in st.session_state:
        with st.spinner("📊 Loading superinvestor Grand Portfolio from Dataroma..."):
            data = fetch_grand_portfolio()
        st.session_state["_gp_data"] = data

    return st.session_state.get("_gp_data", {})


def clear_superinvestor_cache():
    """Clear Grand Portfolio cache — forces re-fetch on next call."""
    import streamlit as st
    st.session_state.pop("_gp_data", None)
    # Also clear per-ticker conviction cache
    for key in list(st.session_state.keys()):
        if key.startswith("si_"):
            del st.session_state[key]


def get_superinvestor_conviction(ticker: str) -> dict:
    """
    Look up conviction data for a ticker from the cached Grand Portfolio.

    Returns:
        holder_count:     int — number of superinvestors holding
        pct_grand:        float — % of aggregate grand portfolio
        conviction_score: int 0-100
        period:           str
        error:            str or None
    """
    gp = get_grand_portfolio()

    error = gp.get("_error")
    if error:
        return {
            "holders":          [],
            "holder_count":     0,
            "conviction_score": 0,
            "pct_grand":        0.0,
            "period":           "Dataroma",
            "error":            f"Grand Portfolio fetch failed: {error}",
        }

    ticker_upper = ticker.upper()
    entry        = gp.get(ticker_upper)

    if not entry:
        return {
            "holders":          [],
            "holder_count":     0,
            "conviction_score": 0,
            "pct_grand":        0.0,
            "period":           "Latest 13F (Dataroma)",
            "error":            None,  # simply not held — not an error
        }

    owners    = entry.get("owners",    0)
    pct_grand = entry.get("pct_grand", 0.0)

    # Conviction score: 0-100
    # Up to 70 pts for breadth (owners / ~100 total SIs on Dataroma)
    # Up to 30 pts for grand portfolio weight
    breadth = min(70, int(owners / 80 * 70))
    weight  = min(30, int(pct_grand / 5 * 30))   # 5%+ of grand portfolio = full 30 pts
    score   = breadth + weight

    meta   = gp.get("_meta", {})
    period = "Latest 13F (Dataroma)"

    return {
        "holders":          [],   # Phase 2 will populate per-investor detail
        "holder_count":     owners,
        "conviction_score": score,
        "pct_grand":        pct_grand,
        "period":           period,
        "total_stocks":     meta.get("total_stocks", 0),
        "error":            None,
    }


def get_all_tickers_with_conviction() -> dict:
    """
    Returns the full Grand Portfolio dict for use in Market Screener bulk scoring.
    Keys are tickers, values include owners and pct_grand.
    """
    gp = get_grand_portfolio()
    return {k: v for k, v in gp.items() if not k.startswith("_")}
"""
superinvestor_utils.py — Superinvestor conviction tracker for Voskuil FP 1.0

Phase 1: Fetches the Dataroma Grand Portfolio — all stocks held by all
superinvestors in one page. Cached in session state for instant lookups.

Data source: https://www.dataroma.com/m/g/portfolio.php
Updated quarterly after 13F filings. No API key required.
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

GRAND_PORTFOLIO_URL = "https://www.dataroma.com/m/g/portfolio.php"
STOCK_DETAIL_URL    = "https://www.dataroma.com/m/stock.php?sym={ticker}"


def fetch_grand_portfolio() -> dict:
    """
    Fetch the Dataroma Grand Portfolio page and parse all holdings.

    Returns dict keyed by uppercase ticker:
    {
        "AAPL": {
            "ticker":    "AAPL",
            "name":      "Apple Inc.",
            "owners":    12,         # number of superinvestors holding
            "pct_grand": 8.45,       # % of grand portfolio
        },
        ...
    }
    Returns empty dict on failure.
    """
    try:
        resp = requests.get(GRAND_PORTFOLIO_URL, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return {"_error": f"HTTP {resp.status_code} from Dataroma"}

        soup = BeautifulSoup(resp.text, "html.parser")

        # Dataroma uses <table id="grid"> for the holdings table
        grid = soup.find("table", {"id": "grid"})
        if not grid:
            # Try any table with stock data
            tables = soup.find_all("table")
            grid   = tables[0] if tables else None

        if not grid:
            return {"_error": "No data table found on Grand Portfolio page"}

        portfolio = {}
        rows      = grid.find_all("tr")[1:]  # skip header

        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            try:
                # Grand Portfolio columns vary — try to detect layout
                # Common layout: Stock | No. of Investors | % of Grand Portfolio | ...
                cell_texts = [c.text.strip() for c in cells]

                # First cell usually has ticker and name: "AAPL - Apple Inc."
                stock_cell = cell_texts[0]
                if " - " in stock_cell:
                    ticker, name = stock_cell.split(" - ", 1)
                elif stock_cell:
                    ticker = stock_cell.split()[0]
                    name   = stock_cell
                else:
                    continue

                ticker = ticker.strip().upper()
                if not ticker or len(ticker) > 6:
                    continue

                # Parse owner count and grand portfolio %
                # Try different column positions
                owners    = 0
                pct_grand = 0.0

                for cell_text in cell_texts[1:]:
                    clean = cell_text.replace(",", "").replace("%", "").strip()
                    try:
                        val = float(clean)
                        if val == int(val) and 1 <= val <= 200 and owners == 0:
                            owners = int(val)
                        elif 0 < val < 100 and pct_grand == 0.0:
                            pct_grand = val
                    except ValueError:
                        continue

                portfolio[ticker] = {
                    "ticker":    ticker,
                    "name":      name.strip(),
                    "owners":    owners,
                    "pct_grand": pct_grand,
                }

            except Exception:
                continue

        if portfolio:
            portfolio["_meta"] = {
                "total_stocks": len(portfolio),
                "source": "Dataroma Grand Portfolio",
            }

        return portfolio

    except requests.Timeout:
        return {"_error": "Timeout fetching Dataroma Grand Portfolio"}
    except Exception as e:
        return {"_error": str(e)}


def get_grand_portfolio() -> dict:
    """
    Returns cached Grand Portfolio dict. Fetches on first call per session.
    Cached in st.session_state['_gp_data'].
    """
    import streamlit as st

    if "_gp_data" not in st.session_state:
        with st.spinner("📊 Loading superinvestor Grand Portfolio from Dataroma..."):
            data = fetch_grand_portfolio()
        st.session_state["_gp_data"] = data

    return st.session_state.get("_gp_data", {})


def clear_superinvestor_cache():
    """Clear Grand Portfolio cache — forces re-fetch on next call."""
    import streamlit as st
    st.session_state.pop("_gp_data", None)
    # Also clear per-ticker conviction cache
    for key in list(st.session_state.keys()):
        if key.startswith("si_"):
            del st.session_state[key]


def get_superinvestor_conviction(ticker: str) -> dict:
    """
    Look up conviction data for a ticker from the cached Grand Portfolio.

    Returns:
        holder_count:     int — number of superinvestors holding
        pct_grand:        float — % of aggregate grand portfolio
        conviction_score: int 0-100
        period:           str
        error:            str or None
    """
    gp = get_grand_portfolio()

    error = gp.get("_error")
    if error:
        return {
            "holders":          [],
            "holder_count":     0,
            "conviction_score": 0,
            "pct_grand":        0.0,
            "period":           "Dataroma",
            "error":            f"Grand Portfolio fetch failed: {error}",
        }

    ticker_upper = ticker.upper()
    entry        = gp.get(ticker_upper)

    if not entry:
        return {
            "holders":          [],
            "holder_count":     0,
            "conviction_score": 0,
            "pct_grand":        0.0,
            "period":           "Latest 13F (Dataroma)",
            "error":            None,  # simply not held — not an error
        }

    owners    = entry.get("owners",    0)
    pct_grand = entry.get("pct_grand", 0.0)

    # Conviction score: 0-100
    # Up to 70 pts for breadth (owners / ~100 total SIs on Dataroma)
    # Up to 30 pts for grand portfolio weight
    breadth = min(70, int(owners / 80 * 70))
    weight  = min(30, int(pct_grand / 5 * 30))   # 5%+ of grand portfolio = full 30 pts
    score   = breadth + weight

    meta   = gp.get("_meta", {})
    period = "Latest 13F (Dataroma)"

    return {
        "holders":          [],   # Phase 2 will populate per-investor detail
        "holder_count":     owners,
        "conviction_score": score,
        "pct_grand":        pct_grand,
        "period":           period,
        "total_stocks":     meta.get("total_stocks", 0),
        "error":            None,
    }


def get_all_tickers_with_conviction() -> dict:
    """
    Returns the full Grand Portfolio dict for use in Market Screener bulk scoring.
    Keys are tickers, values include owners and pct_grand.
    """
    gp = get_grand_portfolio()
    return {k: v for k, v in gp.items() if not k.startswith("_")}
