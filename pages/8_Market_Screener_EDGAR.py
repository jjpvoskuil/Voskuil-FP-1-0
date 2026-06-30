import streamlit as st
import requests
import pandas as pd
from io import StringIO
import time
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from claude_utils import ask_claude_about_equity
from superinvestor_utils import get_conviction_data, get_superinvestor_conviction
from sec_utils import get_ticker_cik_map, fetch_company_facts_with_cik
from edgar_concept_map import FINANCIAL_SIC_CODES, CYCLICAL_SIC_CODES
import concurrent.futures

st.set_page_config(page_title="Market Screener — EDGAR", layout="wide")

APP_URL = "https://voskuil-fp-1-0-k85bd7afbw8dnqeftzxwbu.streamlit.app"

DEFAULT_WEIGHTS = {
    "FCF Yield":              20,
    "ROIC":                   10,
    "Debt / FCF":             20,
    "Gross Margin":           15,
    "Interest Coverage":      10,
    "Price / Owner Earnings": 25,
}

THRESHOLDS = {
    "fcf_yield_good":           0.04,
    "fcf_yield_great":          0.06,
    "roic_good":                0.12,
    "roic_great":               0.20,
    "debt_fcf_safe":            3.0,
    "debt_fcf_warning":         5.0,
    "interest_coverage_safe":   5.0,
    "gross_margin_good":        0.40,
    "gross_margin_great":       0.60,
    "poe_bargain":              15.0,
    "poe_fair":                 25.0,
    "poe_stretched":            35.0,
}

# Quality-floor for Stage 1 — companies must clear this on the
# price-independent 65 points (ROIC + Debt/FCF + Gross Margin + Interest
# Coverage) before they're worth a price lookup in Stage 2.
STAGE1_QUALITY_FLOOR = 0.55  # 55% of available quality points


import re

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
        "MARKET SCREEN RESULTS — Voskuil Owner's Framework\n",
        f"Investment context: Buffett + Munger concentrated value philosophy.",
        f"Investor: {_age_str} | Portfolio: ${_pv/1e6:.1f}M | Monthly target: ${_wd:,.0f} | Inflation assumption: {_inf:.1f}%. Hold horizon 5-10 years.\n",
        f"Top {len(df)} results from S&P 500 screen:\n",
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
        lines.append(
            f"{row['ticker']} ({row.get('name','')}) | Score: {int(row['score'])}/100 | "
            f"FCF Yield: {f(row.get('fcf_yield'))} | ROIC: {f(row.get('roic'))} | "
            f"Debt/FCF: {f(row.get('debt_to_fcf'),'ratio')} | Gross Margin: {f(row.get('gross_margin'))} | "
            f"P/OE: {f(row.get('price_owner_earn'),'ratio')} | Div: {f(row.get('dividend_yield'))} | "
            f"Sector: {row.get('sector','N/A')}{si_str}"
        )
    return "\n".join(lines)


# ── Helper: fetch 10-K sections for a list of tickers in parallel ─────
def fetch_filings_parallel(tickers: list) -> dict:
    """Returns {ticker: filing_result} fetched concurrently."""
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_map = {executor.submit(fetch_10k_sections, t): t for t in tickers}
        for future in concurrent.futures.as_completed(future_map):
            ticker = future_map[future]
            try:
                results[ticker] = future.result()
            except Exception as e:
                results[ticker] = {"sections": {}, "error": str(e)}
    return results


# ── Helper: build deep-dive context with filing sections ──────────────
def build_deep_dive_context(df, filings: dict, question: str) -> str:
    lines = [build_ms_context(df), "\n\n=== SEC 10-K FILING EXCERPTS ===\n"]
    # Scale section size down as company count increases to stay within token limits
    n_companies  = len(filings)
    # Minimum 2500 chars/section for 3-5 companies; more for 1-2
    section_limit = max(2500, 7500 // max(n_companies, 1))
    for ticker, filing in filings.items():
        sections = filing.get("sections", {})
        err      = filing.get("error")
        lines.append(f"\n--- {ticker} ---")
        if err:
            lines.append(f"[Filing unavailable: {err}]")
            continue
        for key, label in [
            ("business",     "BUSINESS"),
            ("risk_factors", "RISK FACTORS"),
            ("mda",          "MD&A"),
        ]:
            text = sections.get(key, "")
            if text:
                lines.append(f"[{label}]: {text[:section_limit]}")
    lines.append(f"\n\nQUESTION: {question}")
    return "\n".join(lines)


# ── Helper: extract ticker mentions from a message ────────────────────
def extract_tickers_from_text(text: str, valid_tickers: list) -> list:
    """Find uppercase 1-5 letter words in text that match valid tickers."""
    words   = re.findall(r'\b[A-Z]{1,5}\b', text)
    matches = [w for w in words if w in valid_tickers]
    return list(dict.fromkeys(matches))  # deduplicate preserving order


import re



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
def fetch_full_us_equity_universe() -> list:
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
# SEC's official SIC code reference table — fetched once and cached.
# Used to power the industry / sub-industry filter dropdowns. SIC codes
# are hierarchical: first 2 digits = major group (e.g. "28" = Chemicals),
# full 4 digits = specific sub-industry (e.g. "2834" = Pharmaceutical
# Preparations).
SIC_LIST_URL = "https://www.sec.gov/search-filings/standard-industrial-classification-sic-code-list"

# Static fallback for the major-group (2-digit) names — these are the
# stable top-level SIC divisions and rarely change, so a hardcoded
# fallback keeps the industry dropdown usable even if the live SEC page
# fetch fails or its HTML structure changes.
SIC_MAJOR_GROUP_FALLBACK = {
    "01": "Agricultural Production - Crops",        "02": "Agricultural Production - Livestock",
    "07": "Agricultural Services",                  "08": "Forestry",
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
    "45": "Air Transportation",                      "47": "Transportation Services",
    "48": "Communications",                          "49": "Electric, Gas & Sanitary Services",
    "50": "Wholesale Trade - Durable Goods",         "51": "Wholesale Trade - Nondurable Goods",
    "52": "Building Materials & Garden Supplies",    "53": "General Merchandise Stores",
    "54": "Food Stores",                             "55": "Auto Dealers & Gas Stations",
    "56": "Apparel & Accessory Stores",              "57": "Home Furniture & Equipment Stores",
    "58": "Eating & Drinking Places",                "59": "Miscellaneous Retail",
    "60": "Depository Institutions (Banks)",         "61": "Non-Depository Credit Institutions",
    "62": "Security & Commodity Brokers",            "63": "Insurance Carriers",
    "64": "Insurance Agents & Brokers",               "65": "Real Estate",
    "67": "Holding & Investment Offices",
    "70": "Hotels & Lodging",                        "72": "Personal Services",
    "73": "Business Services",                       "75": "Auto Repair Services",
    "76": "Misc. Repair Services",                   "78": "Motion Pictures",
    "79": "Amusement & Recreation",                  "80": "Health Services",
    "81": "Legal Services",                          "82": "Educational Services",
    "83": "Social Services",                         "84": "Museums & Botanical/Zoological Gardens",
    "86": "Membership Organizations",                "87": "Engineering & Management Services",
    "88": "Private Households",                      "89": "Services, NEC",
    "91": "Executive & Legislative Government",      "92": "Justice, Public Order & Safety",
    "93": "Public Finance, Taxation & Monetary Policy", "94": "Administration of Human Resources",
    "95": "Environmental Quality & Housing",         "96": "Administration of Economic Programs",
    "97": "National Security & International Affairs", "99": "Nonclassifiable Establishments",
}


@st.cache_data(ttl=604800)  # SIC codes are static — cache for a week
def fetch_sic_industry_map() -> dict:
    """
    Fetch the SEC's official SIC code -> industry name table and parse
    it into a structured lookup. Falls back to the hardcoded major-group
    names above if the live fetch fails.

    Returns:
    {
        "full":  {"2834": "PHARMACEUTICAL PREPARATIONS", ...},  # 4-digit
        "major": {"28": "Chemicals & Allied Products", ...},     # 2-digit, title case
    }
    """
    full_map = {}
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; VoskuilFP/1.0)"}
        resp = requests.get(SIC_LIST_URL, headers=headers, timeout=15)
        if resp.status_code == 200:
            # Table rows look like: | 2834 | Office of Life Sciences | PHARMACEUTICAL PREPARATIONS |
            import re as _re
            rows = _re.findall(
                r'\|\s*(\d{2,4})\s*\|[^|]+\|\s*([A-Z0-9&,.\'\-\s()]+?)\s*\|',
                resp.text
            )
            for code, title in rows:
                code = code.zfill(4) if len(code) <= 4 else code
                full_map[code] = title.strip()
    except Exception:
        pass

    return {
        "full":  full_map,
        "major": SIC_MAJOR_GROUP_FALLBACK,
    }


def sic_major_name(sic: str, sic_map: dict) -> str:
    """Get the major-group (2-digit) industry name for a SIC code."""
    if not sic or len(sic) < 2:
        return "Unclassified"
    return sic_map.get("major", {}).get(sic[:2], f"SIC {sic[:2]}xx")


def sic_full_name(sic: str, sic_map: dict) -> str:
    """Get the full 4-digit sub-industry name for a SIC code, title-cased."""
    if not sic:
        return "Unclassified"
    name = sic_map.get("full", {}).get(sic.zfill(4))
    if name:
        return name.title()
    return f"SIC {sic}"


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


def fetch_quality_edgar(ticker: str, cik: str) -> dict:
    """
    Fetches fundamentals from EDGAR Company Facts using a pre-resolved CIK
    (no redundant ticker->CIK lookup per call — see get_ticker_cik_map()).
    Returns only the price-independent fields: ROIC, Debt/FCF, Gross Margin,
    Interest Coverage, plus identity/sector/financial/cyclical flags.
    Does NOT fetch price — that happens only for Stage 1 survivors.
    """
    facts = fetch_company_facts_with_cik(ticker, cik)
    if facts.get("error"):
        return None

    latest = facts.get("latest", {})
    meta   = facts.get("meta", {})

    fcf            = latest.get("fcf")
    if fcf is None or fcf <= 0:
        return None  # negative/no FCF — same hard filter as the original screener

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

    # Market cap — fetched upfront for every Stage 1 ticker so the
    # market-cap-tier filter can apply before Stage 2 pricing/scoring.
    # Uses yfinance's lightweight fast_info (no full .info call) to keep
    # this as cheap as possible at full-universe scan scale.
    market_cap = None
    try:
        import yfinance as yf
        market_cap = getattr(yf.Ticker(ticker).fast_info, "market_cap", None)
    except Exception:
        market_cap = None

    return {
        "ticker":            ticker,
        "name":              meta.get("company_name", ticker),
        "sic":               meta.get("sic"),
        "is_financial":      meta.get("is_financial", False),
        "is_cyclical":       meta.get("is_cyclical", False),
        "market_cap":        market_cap,
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
        "_latest":           latest,
    }


def score_quality_only(data, weights):
    """
    Stage 1 scoring — only the 4 price-independent criteria.
    Returns (points_earned, points_max) for ranking/filtering purposes.
    """
    pts_earned = 0
    pts_max    = 0

    roic = data.get("roic")
    max_pts = weights["ROIC"]
    pts_max += max_pts
    if roic is not None:
        if roic >= THRESHOLDS["roic_great"]:   pts_earned += max_pts
        elif roic >= THRESHOLDS["roic_good"]:  pts_earned += round(max_pts * 0.60)
        elif roic > 0:                         pts_earned += round(max_pts * 0.20)

    debt_fcf = data.get("debt_to_fcf")
    ic       = data.get("interest_coverage") or 0
    is_nc    = data.get("is_net_creditor", False)
    max_pts  = weights["Debt / FCF"]
    pts_max += max_pts
    if debt_fcf is not None:
        if debt_fcf < THRESHOLDS["debt_fcf_safe"]:      pts_earned += max_pts
        elif debt_fcf < THRESHOLDS["debt_fcf_warning"]: pts_earned += round(max_pts * 0.50)
        elif ic >= THRESHOLDS["interest_coverage_safe"] or is_nc:
                                                         pts_earned += round(max_pts * 0.50)

    gm = data.get("gross_margin")
    max_pts = weights["Gross Margin"]
    pts_max += max_pts
    if gm is not None:
        if gm >= THRESHOLDS["gross_margin_great"]:  pts_earned += max_pts
        elif gm >= THRESHOLDS["gross_margin_good"]: pts_earned += round(max_pts * 0.67)
        else:                                       pts_earned += round(max_pts * 0.20)

    ic_val = data.get("interest_coverage")
    max_pts = weights["Interest Coverage"]
    pts_max += max_pts
    if is_nc:
        pts_earned += max_pts
    elif ic_val is not None:
        if ic_val >= THRESHOLDS["interest_coverage_safe"]: pts_earned += max_pts
        elif ic_val >= 2.5:                                pts_earned += round(max_pts * 0.50)
        elif ic_val > 0:                                   pts_earned += round(max_pts * 0.15)

    return pts_earned, pts_max


# ── Stage 2: Price + final full scoring for survivors only ─────────────
def fetch_price_data(ticker: str) -> dict:
    """Lightweight yfinance price/market cap/dividend fetch — Stage 2 only."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        return {
            "price":          safe_float(info.get("currentPrice") or info.get("regularMarketPrice")),
            "market_cap":     safe_float(info.get("marketCap")),
            "shares":         safe_float(info.get("sharesOutstanding")),
            "dividend_yield": safe_float(info.get("dividendYield")),
            "sector":         info.get("sector", "N/A"),
        }
    except Exception:
        return {"price": None, "market_cap": None, "shares": None,
                "dividend_yield": None, "sector": "N/A"}


def score_stock(data, weights):
    """Full 6-criteria scoring — identical logic to Equity Scout EDGAR / original screener."""
    criteria = []

    max_pts   = weights["FCF Yield"]
    fcf_yield = data.get('fcf_yield')
    if fcf_yield is not None:
        if fcf_yield >= THRESHOLDS['fcf_yield_great']:   pts = max_pts
        elif fcf_yield >= THRESHOLDS['fcf_yield_good']:  pts = round(max_pts * 0.60)
        elif fcf_yield > 0:                              pts = round(max_pts * 0.15)
        else:                                            pts = 0
    else:
        pts = 0
    criteria.append({"name": "FCF Yield", "points_earned": pts, "points_max": max_pts, "missing": fcf_yield is None})

    max_pts = weights["ROIC"]
    roic    = data.get('roic')
    if roic is not None:
        if roic >= THRESHOLDS['roic_great']:   pts = max_pts
        elif roic >= THRESHOLDS['roic_good']:  pts = round(max_pts * 0.60)
        elif roic > 0:                         pts = round(max_pts * 0.20)
        else:                                  pts = 0
    else:
        pts = 0
    criteria.append({"name": "ROIC", "points_earned": pts, "points_max": max_pts, "missing": roic is None})

    max_pts  = weights["Debt / FCF"]
    debt_fcf = data.get('debt_to_fcf')
    ic       = data.get('interest_coverage') or 0
    is_nc    = data.get('is_net_creditor', False)
    if debt_fcf is not None:
        if debt_fcf < THRESHOLDS['debt_fcf_safe']:        pts = max_pts
        elif debt_fcf < THRESHOLDS['debt_fcf_warning']:   pts = round(max_pts * 0.50)
        elif ic >= THRESHOLDS['interest_coverage_safe'] or is_nc: pts = round(max_pts * 0.50)
        else:                                              pts = 0
    else:
        pts = 0
    criteria.append({"name": "Debt/FCF", "points_earned": pts, "points_max": max_pts, "missing": debt_fcf is None})

    max_pts = weights["Gross Margin"]
    gm      = data.get('gross_margin')
    if gm is not None:
        if gm >= THRESHOLDS['gross_margin_great']:  pts = max_pts
        elif gm >= THRESHOLDS['gross_margin_good']: pts = round(max_pts * 0.67)
        else:                                       pts = round(max_pts * 0.20)
    else:
        pts = 0
    criteria.append({"name": "Gross Margin", "points_earned": pts, "points_max": max_pts, "missing": gm is None})

    max_pts = weights["Interest Coverage"]
    ic_val  = data.get('interest_coverage')
    if is_nc:
        pts = max_pts
    elif ic_val is not None:
        if ic_val >= THRESHOLDS['interest_coverage_safe']: pts = max_pts
        elif ic_val >= 2.5:                                pts = round(max_pts * 0.50)
        elif ic_val > 0:                                   pts = round(max_pts * 0.15)
        else:                                              pts = 0
    else:
        pts = 0
    criteria.append({"name": "Interest Coverage", "points_earned": pts, "points_max": max_pts,
                     "missing": (not is_nc and ic_val is None)})

    max_pts = weights["Price / Owner Earnings"]
    poe     = data.get('price_owner_earn')
    if poe is not None:
        if poe <= THRESHOLDS['poe_bargain']:     pts = max_pts
        elif poe <= THRESHOLDS['poe_fair']:      pts = round(max_pts * 0.67)
        elif poe <= THRESHOLDS['poe_stretched']: pts = round(max_pts * 0.25)
        else:                                    pts = 0
    else:
        pts = 0
    criteria.append({"name": "Price/Owner Earnings", "points_earned": pts, "points_max": max_pts, "missing": poe is None})

    raw_score     = sum(c['points_earned'] for c in criteria)
    missing_pts   = sum(c['points_max'] for c in criteria if c.get('missing'))
    available_pts = 100 - missing_pts
    rebalanced    = round(raw_score / available_pts * 100) if available_pts > 0 else raw_score
    return rebalanced


def score_to_label(score):
    if score >= 80:   return "Strong Buy", "🟢"
    elif score >= 65: return "Watch", "🟡"
    elif score >= 45: return "Caution", "🟠"
    else:             return "Avoid", "🔴"


# ── Page UI ──────────────────────────────────────────────────────────
st.title("📡 Market Screener — EDGAR")
st.caption("Two-stage screen: quality first via SEC EDGAR (free, no rate limits at this scale), valuation second via live pricing.")
st.info(
    "**🏛️ EDGAR Validation Page** — Quality fundamentals (ROIC, Debt/FCF, Gross Margin, "
    "Interest Coverage) come directly from SEC Company Facts API. Only companies that clear "
    "the quality bar get a live price lookup for FCF Yield and Price/Owner Earnings — "
    "this is what makes a full-market scan practical without Polygon."
)
st.divider()

# ── Weight reset handler ────────────────────────────────────────────
_weight_map = [("w_fcf_e","FCF Yield"),("w_roic_e","ROIC"),("w_debt_e","Debt / FCF"),
               ("w_gm_e","Gross Margin"),("w_ic_e","Interest Coverage"),("w_poe_e","Price / Owner Earnings")]
for _wkey, _mkey in _weight_map:
    if st.session_state.pop(f"pending_reset_{_wkey}", False):
        st.session_state[_wkey] = DEFAULT_WEIGHTS[_mkey]

with st.expander("⚙️ Customize Scoring Weights", expanded=False):
    st.caption("Same weights as the main screener — shared via session state where possible.")
    if "scoring_weights" not in st.session_state:
        st.session_state.scoring_weights = DEFAULT_WEIGHTS.copy()
    if "committed_weights" not in st.session_state:
        st.session_state.committed_weights = DEFAULT_WEIGHTS.copy()
    sw = st.session_state.scoring_weights

    rc1, rc2, rc3 = st.columns([1.2, 1.2, 4])
    if rc1.button("↺ Reset to Defaults", key="ms_edgar_reset_weights"):
        st.session_state.scoring_weights   = DEFAULT_WEIGHTS.copy()
        st.session_state.committed_weights = DEFAULT_WEIGHTS.copy()
        for _wkey, _mkey in _weight_map:
            st.session_state[_wkey] = DEFAULT_WEIGHTS[_mkey]
        st.rerun()

    draft_weights = {
        "FCF Yield":              st.session_state.get("w_fcf_e",  sw["FCF Yield"]),
        "ROIC":                   st.session_state.get("w_roic_e", sw["ROIC"]),
        "Debt / FCF":             st.session_state.get("w_debt_e", sw["Debt / FCF"]),
        "Gross Margin":           st.session_state.get("w_gm_e",   sw["Gross Margin"]),
        "Interest Coverage":      st.session_state.get("w_ic_e",   sw["Interest Coverage"]),
        "Price / Owner Earnings": st.session_state.get("w_poe_e",  sw["Price / Owner Earnings"]),
    }
    draft_total = sum(draft_weights.values())
    apply_ok    = draft_total == 100
    if rc2.button("✅ Apply Weights", key="ms_edgar_apply_weights", type="primary", disabled=not apply_ok,
                  help="Activates weights for scoring." if apply_ok else f"Total must equal 100 (currently {draft_total})."):
        st.session_state.committed_weights = draft_weights.copy()
        st.session_state.scoring_weights   = draft_weights.copy()
        st.rerun()

    cw = st.session_state.committed_weights
    rc3.caption(
        f"**Active:** FCF {cw['FCF Yield']} · ROIC {cw['ROIC']} · Debt {cw['Debt / FCF']} · "
        f"GM {cw['Gross Margin']} · IC {cw['Interest Coverage']} · P/OE {cw['Price / Owner Earnings']}"
    )

    w_col1, w_col2 = st.columns(2)
    with w_col1:
        w_fcf  = st.slider("FCF Yield",  0, 60, sw["FCF Yield"],  step=5, key="w_fcf_e")
        w_roic = st.slider("ROIC",       0, 40, sw["ROIC"],       step=5, key="w_roic_e")
        w_debt = st.slider("Debt / FCF", 0, 40, sw["Debt / FCF"], step=5, key="w_debt_e")
    with w_col2:
        w_gm  = st.slider("Gross Margin",           0, 40, sw["Gross Margin"],           step=5, key="w_gm_e")
        w_ic  = st.slider("Interest Coverage",      0, 40, sw["Interest Coverage"],      step=5, key="w_ic_e")
        w_poe = st.slider("Price / Owner Earnings", 0, 60, sw["Price / Owner Earnings"], step=5, key="w_poe_e")

    active_weights = {
        "FCF Yield": w_fcf, "ROIC": w_roic, "Debt / FCF": w_debt,
        "Gross Margin": w_gm, "Interest Coverage": w_ic, "Price / Owner Earnings": w_poe,
    }
    st.session_state.scoring_weights = active_weights
    total_weight = sum(active_weights.values())
    if total_weight == 100:
        st.success(f"✅ Total: {total_weight} / 100 — click Apply Weights to activate")
    elif total_weight < 100:
        st.warning(f"⚠️ Total: {total_weight} / 100 — {100 - total_weight} pts unallocated")
    else:
        st.error(f"❌ Total: {total_weight} / 100 — over by {total_weight - 100} pts")

weights = st.session_state.get("committed_weights", DEFAULT_WEIGHTS.copy())

st.markdown("#### Ticker Universe")
universe_choice = st.radio(
    "Select the universe to scan",
    options=["S&P 500 (~500)", "All US Common Stocks (~6,000+)"],
    horizontal=True,
    help=(
        "S&P 500 sourced from Wikipedia. 'All US Common Stocks' is sourced free from "
        "Nasdaq Trader's public Symbol Directory (NASDAQ + NYSE + NYSE American + NYSE "
        "Arca), filtered to common stock only (no ETFs, SPACs warrants/units, or test "
        "issues). This is a much broader universe than the S&P 500 and a practical free "
        "proxy for Russell 1000/2000-scale coverage — FTSE Russell's own official "
        "constituent files are commercial-license-only, so there's no free exact match. "
        "Within this scan, you control how many tickers to actually screen via "
        "'Max stocks to scan' below."
    ),
)

col1, col2, col3 = st.columns(3)
with col1:
    top_n = st.number_input("Top results to show", min_value=5, max_value=50, value=15, step=5)
with col2:
    skip_financials = st.checkbox("Skip financial firms (banks/insurers)", value=True,
                                   help="Financial firms use different balance sheet structures — flagged via SIC code.")
    skip_cyclicals  = st.checkbox("Flag cyclical firms", value=False,
                                   help="Cyclicals aren't excluded, just labeled for full-cycle context.")
with col3:
    _default_max = {"S&P 500 (~500)": 500, "All US Common Stocks (~6,000+)": 1000}[universe_choice]
    max_scan = st.number_input(
        "Max stocks to scan (Stage 1)", min_value=10, max_value=8000,
        value=_default_max, step=50,
        help="Larger universes take longer on Stage 1. EDGAR has no hard rate limit at this scale, "
             "but expect several minutes for 1,000+ tickers."
    )
    min_div  = st.checkbox("Dividend payers only (Stage 2 filter)", value=False)

# ── Stage 1 filters: industry, market cap, superinvestor coverage ──────
st.markdown("#### Stage 1 Filters")
st.caption(
    "Applied after the quality scan, before Stage 2 pricing — narrowing here speeds up "
    "Stage 2 since fewer survivors need a price lookup."
)

_sic_map = fetch_sic_industry_map()

fcol1, fcol2, fcol3 = st.columns(3)
with fcol1:
    industry_filter = st.selectbox(
        "Industry",
        options=["All Industries"] + sorted(set(_sic_map.get("major", {}).values())),
        help="Major SIC industry group. Companies are classified by their primary SIC code in SEC filings.",
    )
with fcol2:
    # Sub-industry options narrow based on the selected major industry.
    # Sourced from the FULL Stage 1 quality-survivor pool (not the
    # narrow top_n Stage 2 results) so industries with many companies
    # actually show their sub-categories. Populated after a scan runs;
    # before that, only "All Sub-Industries" is available.
    _stage1_pool = st.session_state.get('ms_edgar_stage1_pool', [])
    if not _stage1_pool:
        # Fallback: derive from the last displayed results table if the
        # dedicated pool key is missing — e.g. right after an app redeploy
        # wipes session state but the results table was somehow retained,
        # or for backward compatibility with older saved sessions.
        _prior_df = st.session_state.get('ms_edgar_results_df')
        if _prior_df is not None and 'sic' in _prior_df.columns:
            _stage1_pool = _prior_df[['ticker', 'sic']].to_dict('records')
    sub_industry_options = ["All Sub-Industries"]
    if _stage1_pool:
        _sub_names = set()
        for d in _stage1_pool:
            sic = d.get("sic")
            if not sic:
                continue
            if industry_filter == "All Industries" or sic_major_name(str(sic), _sic_map) == industry_filter:
                _sub_names.add(sic_full_name(str(sic), _sic_map))
        sub_industry_options = ["All Sub-Industries"] + sorted(_sub_names)
    sub_industry_filter = st.selectbox(
        "Sub-Industry",
        options=sub_industry_options,
        help="Populated from the most recent scan's full Stage 1 results (all quality survivors, "
             "not just the top displayed). Run a scan first to see specific sub-industries.",
    )
    if _stage1_pool:
        st.caption(f"✅ {len(_stage1_pool)} companies in saved scan pool")
    else:
        st.caption("⚠️ No saved scan pool yet — run a screen below to populate sub-industries")
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

_est_min = max(1, round(max_scan / 8 / 60 * 1.3))  # rough: 8 parallel workers, ~1 req/sec/worker, 30% overhead
st.caption(f"⏱️ Estimated Stage 1 time for {max_scan} tickers: ~{_est_min} minute{'s' if _est_min != 1 else ''}. Stage 2 (price lookups on survivors) adds 10-60 seconds.")

st.divider()
run_screen = st.button("🚀 Run Two-Stage Screen", type="primary", use_container_width=True)

# ── Run screen ──────────────────────────────────────────────────────
if run_screen:
    if total_weight != 100:
        st.error(f"Weights must add up to 100. Currently at {total_weight}.")
        st.stop()

    with st.spinner(f"Loading {universe_choice} ticker list..."):
        if universe_choice == "S&P 500 (~500)":
            tickers = get_sp500_tickers()
        else:
            tickers = fetch_full_us_equity_universe()

    if not tickers:
        st.error(f"Could not load the {universe_choice} ticker list. Try again — Nasdaq Trader/Wikipedia data sources occasionally have transient issues.")
        st.stop()

    tickers_to_scan = tickers[:max_scan]
    total_tickers   = len(tickers_to_scan)

    # ── Build ticker -> CIK map ONCE (the key bulk-scan optimization) ──
    with st.spinner("Resolving tickers to SEC CIK numbers (one-time lookup)..."):
        ticker_cik_map = get_ticker_cik_map()

    if not ticker_cik_map:
        st.error("Could not load EDGAR ticker-to-CIK map. Try again in a moment.")
        st.stop()

    # ── Stage 1: Quality scan (EDGAR, parallel, no price) ──────────────
    st.markdown(f"### Stage 1 — Quality Scan ({total_tickers} companies, EDGAR fundamentals)")
    progress_bar = st.progress(0)
    status_text  = st.empty()
    stage1_results = []
    completed = 0

    def _stage1_worker(ticker):
        cik = ticker_cik_map.get(ticker.upper())
        if not cik:
            return None
        return fetch_quality_edgar(ticker, cik)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_stage1_worker, t): t for t in tickers_to_scan}
        for future in concurrent.futures.as_completed(futures):
            completed += 1
            pct = completed / total_tickers
            progress_bar.progress(pct)
            status_text.markdown(f"⏳ Stage 1: {completed} of {total_tickers} ({int(pct*100)}%) — {len(stage1_results)} candidates so far")
            try:
                data = future.result()
            except Exception:
                data = None
            if data is None:
                continue
            if skip_financials and data.get("is_financial"):
                continue
            q_earned, q_max = score_quality_only(data, weights)
            if q_max > 0 and (q_earned / q_max) >= STAGE1_QUALITY_FLOOR:
                data["_quality_score"] = q_earned
                data["_quality_max"]   = q_max
                stage1_results.append(data)

    progress_bar.progress(1.0)
    status_text.markdown(
        f"✅ Stage 1 complete — {len(stage1_results)} of {total_tickers} companies cleared the "
        f"quality floor ({int(STAGE1_QUALITY_FLOOR*100)}% of price-independent points)."
    )

    if not stage1_results:
        st.warning("No companies passed Stage 1 quality filters. Try lowering the quality floor or scanning more tickers.")
        st.stop()

    # Persist the FULL Stage 1 candidate pool (before industry/cap/SI
    # filters are applied below) so the Industry/Sub-Industry dropdowns
    # have the complete set of SIC codes to populate from on rerun —
    # not just the narrow top_n Stage 2 results, which was the bug:
    # only ~15 companies survive to the final table, so most industries
    # never had enough representation to show meaningful sub-categories.
    st.session_state['ms_edgar_stage1_pool'] = stage1_results.copy()

    # ── Apply Stage 1 filters: industry, sub-industry, market cap, SI ──
    _pre_filter_count = len(stage1_results)

    if industry_filter != "All Industries":
        stage1_results = [
            d for d in stage1_results
            if sic_major_name(str(d.get("sic") or ""), _sic_map) == industry_filter
        ]

    if sub_industry_filter != "All Sub-Industries":
        stage1_results = [
            d for d in stage1_results
            if sic_full_name(str(d.get("sic") or ""), _sic_map) == sub_industry_filter
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
        st.warning("No companies survived the Stage 1 filters you selected. Try relaxing industry, market cap, or SI coverage filters.")
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

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_stage2_worker, q): q["ticker"] for q in stage1_results}
        for future in concurrent.futures.as_completed(futures):
            completed2 += 1
            pct = completed2 / n_survivors
            progress_bar2.progress(pct)
            status_text2.markdown(f"⏳ Stage 2: {completed2} of {n_survivors} ({int(pct*100)}%)")
            try:
                qdata, price_data = future.result()
            except Exception:
                continue

            price      = price_data.get("price")
            market_cap = price_data.get("market_cap")
            shares     = price_data.get("shares")
            div_yield  = price_data.get("dividend_yield")
            sector     = price_data.get("sector", "N/A")

            if min_div and not div_yield:
                continue

            fcf        = qdata.get("fcf")
            owner_earn = qdata.get("owner_earnings")
            fcf_yield  = (fcf / market_cap) if (fcf and market_cap and market_cap > 0) else None
            poe        = (price / (owner_earn / shares)) if (owner_earn and owner_earn > 0 and shares and price) else None

            full_data = {
                **qdata,
                "price":            price,
                "market_cap":       market_cap or qdata.get("market_cap"),
                "sector":           sector,
                "fcf_yield":        fcf_yield,
                "price_owner_earn": poe,
                "dividend_yield":   div_yield,
                "industry":         sic_major_name(str(qdata.get("sic") or ""), _sic_map),
                "sub_industry":     sic_full_name(str(qdata.get("sic") or ""), _sic_map),
            }
            full_data["score"] = score_stock(full_data, weights)
            results.append(full_data)

    progress_bar2.progress(1.0)
    status_text2.markdown(f"✅ Stage 2 complete — {len(results)} fully scored companies.")

    if not results:
        st.warning("No results survived Stage 2. Try removing the dividend filter.")
        st.stop()

    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('score', ascending=False).head(top_n).reset_index(drop=True)

    st.session_state['ms_edgar_results_df']    = results_df
    st.session_state['ms_edgar_total_tickers'] = total_tickers
    st.session_state['ms_edgar_results_count'] = len(results)
    st.session_state['ms_claude_convo']        = []
    st.session_state['ms_claude_context_sent'] = False
    st.session_state['ms_selected_tickers']    = []
    st.session_state.pop('ms_filings', None)

# ── Render results (fresh or cached) ─────────────────────────────────
if 'ms_edgar_results_df' in st.session_state:
    results_df    = st.session_state['ms_edgar_results_df']
    total_tickers = st.session_state.get('ms_edgar_total_tickers', 0)

    if not run_screen:
        st.info("💡 Showing results from last screen run. Click **Run Screen** to refresh.")

    st.divider()
    st.markdown(f"## 🏆 Top {len(results_df)} Concentrated Opportunities")
    st.caption("Ranked by Voskuil Owner's Framework score.")

    def fmt(val, fmt_type):
        if val is None or (isinstance(val, float) and pd.isna(val)): return "N/A"
        if fmt_type == "pct":   return f"{val:.1%}"
        if fmt_type == "ratio": return f"{val:.1f}x"
        return str(val)

    # ── Superinvestor sort toggle (load button now lives in pre-scan filters) ──
    _si_loaded = "_si_full_map" in st.session_state
    si_col1, si_col2 = st.columns([2, 4])
    with si_col1:
        sort_by_si = False
        if _si_loaded:
            sort_by_si = st.checkbox("Sort by SI Conviction", value=False,
                                      help="Re-rank results by superinvestor conviction instead of Owner's Framework score")
        else:
            st.caption("🦁 Superinvestor data not loaded — use the filter section above to load it.")
    with si_col2:
        if _si_loaded:
            st.caption("Superinvestor holder counts are shown on each result below.")

    # ── Apply SI conviction data and optional re-sort ────────────────
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

        if sort_by_si:
            results_df = results_df.sort_values('si_score', ascending=False).reset_index(drop=True)

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
        score       = int(row['score'])
        label, icon = score_to_label(score)
        ticker      = row['ticker']
        is_checked  = ticker in _selected

        with st.container():
            _has_si = 'si_holders' in row.index
            if _has_si:
                c1, c2, c3, c4, c5, c6, c7, c8, c9, c10 = st.columns([1, 2.6, 1.7, 1.7, 1.7, 1.7, 1.7, 1.7, 1.2, 1.6])
            else:
                c1, c2, c3, c4, c5, c6, c7, c8, c10 = st.columns([1, 3, 2, 2, 2, 2, 2, 2, 1.5])
                c9 = None
            with c1:
                st.markdown(f"### {icon}")
                st.markdown(f"**#{rank+1}**")
            with c2:
                st.markdown(f"**{ticker}**")
                st.caption(row.get('name', ''))
                st.caption(row.get('sub_industry') or row.get('sector', ''))
            with c3: st.metric("Score",        f"{score}/100")
            with c4: st.metric("FCF Yield",    fmt(row.get('fcf_yield'),        "pct"))
            with c5: st.metric("ROIC",         fmt(row.get('roic'),             "pct"))
            with c6: st.metric("Gross Margin", fmt(row.get('gross_margin'),     "pct"))
            with c7: st.metric("Debt/FCF",     fmt(row.get('debt_to_fcf'),      "ratio"))
            with c8: st.metric("P/OE",         fmt(row.get('price_owner_earn'), "ratio"))
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

            div = row.get('dividend_yield')
            if div is not None and not (isinstance(div, float) and pd.isna(div)) and div > 0:
                st.caption(f"💰 Dividend Yield: {div:.2%}")
            if row.get('is_net_creditor'): st.caption("✨ Net Creditor")
            st.markdown(f"[🔍 Deep Dive in Equity Scout]({APP_URL}/equity_scout?ticker={ticker}&auto=1)")
            st.divider()

    st.markdown("### 📊 Screen Summary")
    s1, s2, s3, s4 = st.columns(4)
    with s1: st.metric("Scanned",           total_tickers)
    with s2: st.metric("Passed FCF Filter", st.session_state.get('ms_edgar_results_count', len(results_df)))
    with s3: st.metric("Avg Score",         f"{results_df['score'].mean():.0f}")
    with s4: st.metric("Strong Buys (80+)", len(results_df[results_df['score'] >= 80]))

    st.markdown("### 💾 Export Results")
    _export_cols = ['ticker','name','sector','industry','sub_industry','score','fcf_yield','roic','gross_margin',
                     'debt_to_fcf','interest_coverage','price_owner_earn','dividend_yield','price','market_cap']
    _export_names = ['Ticker','Name','Sector','Industry','Sub-Industry','Score','FCF Yield','ROIC','Gross Margin',
                      'Debt/FCF','Interest Coverage','Price/Owner Earnings','Dividend Yield','Price','Market Cap']
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
        "Claude reasons over the full screen results. Ask it to compare, rank by thesis fit, "
        "or flag risks. Use **Deep Dive Top 3** to pull the actual 10-K filings for the top scorers."
    )

    # ── Deep Dive buttons ────────────────────────────────────────────
    top3_tickers     = results_df['ticker'].head(3).tolist()
    selected_tickers = st.session_state.get('ms_selected_tickers', [])

    dd_col1, dd_col2, dd_col3 = st.columns([2, 2, 3])
    with dd_col1:
        _top3_disabled = 'ms_pending_deep_dive' in st.session_state
        if st.button("🔬 Deep Dive Top 3", type="primary", use_container_width=True,
                     disabled=_top3_disabled,
                     help="Fetch SEC 10-K filings for the top 3 scored tickers"):
            st.session_state['ms_pending_deep_dive'] = top3_tickers
            st.session_state['ms_selected_tickers']  = []
            st.rerun()
    with dd_col2:
        n_sel = len(selected_tickers)
        _sel_disabled = n_sel == 0 or 'ms_pending_deep_dive' in st.session_state
        if st.button(
            f"🔬 Deep Dive Selected ({n_sel})",
            type="primary" if n_sel > 0 else "secondary",
            use_container_width=True,
            disabled=_sel_disabled,
            help=f"Fetch SEC filings for: {', '.join(selected_tickers)}" if selected_tickers else "Check boxes next to results to select",
        ):
            st.session_state['ms_pending_deep_dive'] = selected_tickers.copy()
            st.session_state['ms_selected_tickers']  = []
            st.rerun()
    with dd_col3:
        if selected_tickers:
            st.caption(f"✅ Selected: {', '.join(selected_tickers)}")
        else:
            st.caption("☑️ Check boxes next to any result to select for deep dive (max 5)")

    # Show which tickers are loaded
    loaded_filings = st.session_state.get('ms_filings', {})
    if loaded_filings:
        loaded_str = ", ".join(
            f"{'✅' if not v.get('error') else '⚠️'} {k}"
            for k, v in loaded_filings.items()
        )
        with dd_col2:
            st.caption(f"Filings loaded: {loaded_str}")

    # Handle deep dive trigger — capture the tickers from session state
    _dive_tickers = st.session_state.pop('ms_pending_deep_dive', None)
    if _dive_tickers:
        with st.spinner(f"📄 Fetching 10-K filings for {', '.join(_dive_tickers)} in parallel..."):
            st.session_state['ms_filings'] = fetch_filings_parallel(_dive_tickers)
        from claude_utils import get_user_profile
        _p       = get_user_profile()
        _age     = _p.get('age', 57)
        _wd      = _p.get('monthly_withdrawal', 8000)
        _pv      = _p.get('portfolio_val', 3_790_000)
        _sage    = _p.get('spouse_age', '')
        _age_str = f"{_age}-year-old" + (f" and spouse age {_sage}" if _sage else "")
        n_co     = len(_dive_tickers)
        _comparison = "three companies" if n_co == 3 else f"{n_co} companies"
        st.session_state['ms_pending_claude_q'] = (
            f"I've now loaded the SEC 10-K filings for {', '.join(_dive_tickers)}. "
            f"Please do a full qualitative comparison of these {_comparison} using both "
            f"the quantitative scores and the actual filing text. Apply both Buffett and "
            f"Munger lenses — use Munger's inversion first (what could permanently destroy "
            f"value?), then assess moat durability, management quality, and pricing power. "
            f"Rank them for a {_age_str} household with a ${_pv/1e6:.1f}M portfolio "
            f"targeting ${_wd:,.0f}/month in retirement income. "
            f"Which one would you concentrate in and why?"
        )
        st.rerun()

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
            "Apply Munger's inversion — what could permanently destroy value in each?",
            "Compare the top 3 on moat durability using Buffett + Munger criteria.",
            "Which would Buffett most likely hold for 10 years and why?",
        ]
        for i, q in enumerate(ms_starters):
            with sq_cols[i % 2]:
                if st.button(q, key=f"ms_starter_{i}", use_container_width=True):
                    st.session_state["ms_pending_claude_q"] = q
                    st.rerun()

    ms_pending_q = st.session_state.pop("ms_pending_claude_q", None)
    ms_user_q    = st.chat_input("Ask Claude about these screen results...", key="ms_claude_input")
    ms_active_q  = ms_pending_q or ms_user_q

    if ms_active_q:
        # Check if user is requesting a filing for a specific ticker
        all_tickers   = results_df['ticker'].tolist()
        filings_cache = st.session_state.get('ms_filings', {})
        mentioned     = extract_tickers_from_text(ms_active_q, all_tickers)
        new_tickers   = [t for t in mentioned if t not in filings_cache]

        if new_tickers:
            with st.spinner(f"📄 Fetching 10-K filings for {', '.join(new_tickers)}..."):
                new_filings = fetch_filings_parallel(new_tickers)
                filings_cache.update(new_filings)
                st.session_state['ms_filings'] = filings_cache

        with st.chat_message("user"):
            st.markdown(ms_active_q)

        with st.chat_message("assistant", avatar="🤖"):
            with st.spinner("Analyzing..."):
                # Build context — include filing sections if available
                if filings_cache:
                    context_str = build_deep_dive_context(results_df, filings_cache, ms_active_q)
                else:
                    context_str = build_ms_context(results_df) + f"\n\n---\nQUESTION: {ms_active_q}"

                if not st.session_state[ms_context_key]:
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
            st.session_state.pop('ms_filings', None)
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
    7. **Completes scoring** with FCF Yield and Price/Owner Earnings
    8. **Returns top results** ranked by full conviction score

    This mirrors Buffett/Munger philosophy structurally: a company can't screen well by being
    cheap — it has to earn its way to Stage 2 on business quality first.

    ### Features
    - 🤖 **Ask Claude** — compare results, rank by thesis fit, or pull SEC 10-K filings for any ticker
    - 🔬 **Deep Dive Top 3** — fetches actual 10-K filings for the top 3 scorers in parallel
    - 🦁 **Superinvestor Conviction** — see how many of 82 tracked value investors hold each result
    - **Net Creditor detection** — companies earning more interest than they pay score full points
    - **Financial firm filtering** — banks/insurers excluded by default (different statement structure)

    ---
    **Score guide:** 🟢 80+ Strong Buy · 🟡 65-79 Watch · 🟠 45-64 Caution · 🔴 <45 Avoid

    *Fundamentals sourced directly from SEC EDGAR Company Facts API — free, no rate-limit risk at this scale, no third-party normalization layer.*
    """)
