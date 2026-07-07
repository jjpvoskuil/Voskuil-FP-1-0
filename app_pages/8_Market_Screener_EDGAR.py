import streamlit as st
import requests
import pandas as pd
from io import StringIO
import time
import random
from datetime import datetime, timezone
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from claude_utils import ask_claude_about_equity
from superinvestor_utils import get_conviction_data, get_superinvestor_conviction
from sec_utils import (
    get_ticker_cik_map, fetch_company_facts_with_cik, DEFAULT_WEIGHTS,
    evaluate_buffett_funnel, FUNNEL_THRESHOLDS,
)
from edgar_concept_map import FINANCIAL_SIC_CODES, CYCLICAL_SIC_CODES
from github_store import github_get_json, github_put_json
from ui_utils import force_scroll_to_top
import concurrent.futures

st.set_page_config(page_title="Market Screener — EDGAR", layout="wide")

SCAN_CACHE_PATH = "market_screener_scan_cache.json"

APP_URL = "https://voskuil-fp-1-0-k85bd7afbw8dnqeftzxwbu.streamlit.app"

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


def fetch_quality_edgar(ticker: str, cik: str, funnel_thresholds: dict = None) -> dict:
    """
    Fetches fundamentals from EDGAR Company Facts using a pre-resolved CIK
    (no redundant ticker->CIK lookup per call — see get_ticker_cik_map()).
    Returns the price-independent fields plus the Buffett/Munger funnel
    checklist breakdown (evaluate_buffett_funnel — 10-yr avg ROIC, 10-yr
    avg FCF margin, dual debt-hurdle check, dilution check). Legacy
    single-period fields (roic, gross_margin, debt_to_fcf, interest_
    coverage) are still returned for reference/export — they are not
    part of the funnel gate itself (#31, #33, #35).
    Does NOT fetch price — that happens only for Stage 1 survivors.
    """
    facts = fetch_company_facts_with_cik(ticker, cik)
    if facts.get("error"):
        return None

    latest = facts.get("latest", {})
    meta   = facts.get("meta", {})

    fcf            = latest.get("fcf")
    if fcf is None or fcf <= 0:
        return None  # negative/no FCF in the latest year — hard pre-filter,
        # applied before the funnel checklist even runs. Note: this can
        # exclude a business with a genuinely strong 10-year average that
        # simply had one weak year — worth revisiting if it starts
        # dropping companies you'd expect the checklist to catch instead.

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
    # pricing/scoring. fast_info covers market cap cheaply; sector
    # requires the fuller .info call, which is slower — this is the
    # cost of having Sector available before any scan completes, per
    # the user's choice to prioritize full pre-scan filtering over
    # Stage 1 speed.
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

    return {
        "ticker":            ticker,
        "name":              meta.get("company_name", ticker),
        "sic":               meta.get("sic"),
        "is_financial":      meta.get("is_financial", False),
        "is_cyclical":       meta.get("is_cyclical", False),
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
        "_latest":           latest,
    }


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
    skip_financials = st.checkbox("Skip financial firms (banks/insurers)", value=True,
                                   help="Financial firms use different balance sheet structures — flagged via SIC code.")
    flag_cyclicals  = st.checkbox("Flag cyclical firms", value=True,
                                   help="Cyclicals aren't excluded, just badged ⚠️ on their result card — a "
                                        "10-yr average still leans on wherever the cycle currently sits.")
with col3:
    _default_max = {"S&P 500 (~500)": 500, "All US Common Stocks (~6,000+)": 1000}[universe_choice]
    scan_all = st.checkbox(
        "Scan ALL tickers in universe",
        value=True,
        help="Bypasses the max-scan limit entirely and screens every ticker in the selected "
             "universe. For 'All US Common Stocks' this is 6,000-8,000+ tickers and will take "
             "significantly longer (see time estimate below) — but the result is cached "
             "persistently afterward, so this cost is paid once, not on every reboot."
    )
    max_scan = st.number_input(
        "Max stocks to scan (Stage 1)", min_value=10, max_value=8000,
        value=_default_max, step=50, disabled=scan_all,
        help="Larger universes take longer on Stage 1. EDGAR has no hard rate limit at this scale, "
             "but expect several minutes for 1,000+ tickers. When below the full universe size, "
             "a random sample is scanned (not an alphabetical slice) so results aren't biased "
             "toward tickers starting with 'A'."
    )
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
_effective_scan = _approx_universe_size if scan_all else max_scan
_est_min = max(1, round(_effective_scan / 8 / 60 * 1.6))  # rough: 8 parallel workers, ~1 req/sec/worker, 60% overhead (sector .info call adds latency vs. fast_info alone)
if scan_all:
    st.caption(f"⏱️ Estimated Stage 1 time for ALL ~{_approx_universe_size:,} tickers: ~{_est_min} minutes. Stage 2 (price lookups on survivors) adds 10-60 seconds.")
else:
    st.caption(f"⏱️ Estimated Stage 1 time for {max_scan} tickers: ~{_est_min} minute{'s' if _est_min != 1 else ''}. Stage 2 (price lookups on survivors) adds 10-60 seconds.")

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

if refilter_clicked and _has_cached_pool:
    run_filters_and_stage2(
        st.session_state['ms_edgar_stage1_raw_pool'],
        st.session_state.get('ms_edgar_stage1_raw_total', len(st.session_state['ms_edgar_stage1_raw_pool'])),
    )



if run_screen:
    with st.spinner(f"Loading {universe_choice} ticker list..."):
        if universe_choice == "S&P 500 (~500)":
            tickers = get_sp500_tickers()
        else:
            tickers = fetch_full_us_equity_universe(universe="all_us")

    if not tickers:
        st.error(f"Could not load the {universe_choice} ticker list. Try again — Nasdaq Trader/Wikipedia data sources occasionally have transient issues.")
        st.stop()

    st.caption(f"📋 {len(tickers):,} tickers loaded — {'scanning ALL of them' if scan_all else f'scanning a random sample of up to {max_scan:,}'}.")

    if scan_all or max_scan >= len(tickers):
        tickers_to_scan = tickers
    else:
        # Random sample, not an alphabetical slice — fetch_full_us_equity_universe()
        # returns tickers sorted alphabetically, so tickers[:max_scan] would always
        # scan the same 'A'-through-'D'-ish subset and never see the rest of the
        # alphabet. Seeded for reproducibility within a session/day (cache TTL is
        # 24h), so re-running the same scan gives consistent results.
        _rng = random.Random(f"{universe_choice}-{max_scan}-{time.strftime('%Y-%m-%d')}")
        tickers_to_scan = _rng.sample(tickers, max_scan)
    total_tickers   = len(tickers_to_scan)

    # ── Build ticker -> CIK map ONCE (the key bulk-scan optimization) ──
    with st.spinner("Resolving tickers to SEC CIK numbers (one-time lookup)..."):
        ticker_cik_map = get_ticker_cik_map()

    if not ticker_cik_map:
        st.error("Could not load EDGAR ticker-to-CIK map. Try again in a moment.")
        st.stop()

    # ── Stage 1: Buffett/Munger checklist scan (EDGAR, parallel, no price) ──
    st.markdown(f"### Stage 1 — Checklist Scan ({total_tickers} companies, EDGAR fundamentals)")
    progress_bar = st.progress(0)
    status_text  = st.empty()
    stage1_results = []
    completed = 0

    def _stage1_worker(ticker):
        cik = ticker_cik_map.get(ticker.upper())
        if not cik:
            return None
        return fetch_quality_edgar(ticker, cik, funnel_thresholds)

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
            if data.get("funnel_passed"):
                stage1_results.append(data)

    progress_bar.progress(1.0)
    status_text.markdown(
        f"✅ Stage 1 complete — {len(stage1_results)} of {total_tickers} companies cleared the "
        f"Buffett/Munger checklist (10-yr avg ROIC, 10-yr avg FCF margin, a debt hurdle, no dilution)."
    )

    if not stage1_results:
        st.warning("No companies passed Stage 1 quality filters. Try lowering the quality floor or scanning more tickers.")
        st.stop()

    # Cache the RAW, unfiltered Stage 1 pool so filters can be changed
    # and re-applied later via the "Re-apply Filters" button above,
    # without re-running the slow EDGAR fetch.
    st.session_state['ms_edgar_stage1_raw_pool']  = stage1_results
    st.session_state['ms_edgar_stage1_raw_total'] = total_tickers

    # Persist to GitHub so this survives a Streamlit Cloud reboot/redeploy —
    # the whole point of a full-universe scan being expensive (minutes) is
    # to not have to pay that cost again every time the app restarts.
    _scan_timestamp = datetime.now(timezone.utc).isoformat()
    with st.spinner("💾 Saving scan results persistently (survives reboots)..."):
        _ok, _msg = github_put_json(
            SCAN_CACHE_PATH,
            {
                "universe":              universe_choice,
                "scan_timestamp":        _scan_timestamp,
                "total_tickers_scanned": total_tickers,
                "stage1_survivors":      stage1_results,
            },
            commit_message=f"Market screener scan cache — {universe_choice} — {len(stage1_results)} survivors",
        )
    if _ok:
        st.session_state['ms_edgar_scan_timestamp'] = _scan_timestamp
        st.session_state['ms_edgar_scan_universe']  = universe_choice
        st.caption("✅ Scan cached persistently — will still be here after a reboot.")
    else:
        st.warning(f"⚠️ Scan completed but persistent save failed: {_msg}\n\n"
                   f"Results are available for this session, but a reboot/redeploy will lose them "
                   f"until you re-run the scan.")

    run_filters_and_stage2(stage1_results, total_tickers)

# ── Render results (fresh or cached) ─────────────────────────────────
if 'ms_edgar_results_df' in st.session_state:
    results_df    = st.session_state['ms_edgar_results_df']
    total_tickers = st.session_state.get('ms_edgar_total_tickers', 0)

    if not run_screen:
        st.info("💡 Showing results from last screen run. Click **Run Screen** to refresh.")

    st.divider()
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
                          "Years of History (High-Low)"]
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

    _sort_map = {
        "Ticker (A-Z)":                          ("ticker", True),
        "10yr Avg ROIC (High-Low)":              ("roic_avg", False),
        "10yr Avg FCF Margin (High-Low)":        ("fcf_margin_avg", False),
        "Years of History (High-Low)":           ("funnel_years_used", False),
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
                c1, c2, c3, c4, c5, c6, c7, c8, c9, c10 = st.columns([1, 2.6, 1.7, 1.7, 1.7, 1.7, 1.7, 1.7, 1.2, 1.6])
            else:
                c1, c2, c3, c4, c5, c6, c7, c8, c10 = st.columns([1, 3, 2, 2, 2, 2, 2, 2, 1.5])
                c9 = None
            with c1:
                st.markdown(f"### {hurdle_icon}")
                st.markdown(f"**#{rank+1}**")
            with c2:
                st.markdown(f"**{ticker}**")
                st.caption(row.get('name', ''))
                st.caption(row.get('sub_industry') or row.get('sector', ''))
                _badges = []
                if row.get('is_cyclical') and flag_cyclicals: _badges.append("⚠️ Cyclical")
                if row.get('limited_history'):
                    _badges.append(f"📏 Limited History ({row.get('funnel_years_used','?')}y)")
                if _badges:
                    st.caption(" · ".join(_badges))
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
    with s1: st.metric("Scanned",                total_tickers)
    with s2: st.metric("Checklist Survivors",    st.session_state.get('ms_edgar_results_count', len(results_df)))
    with s3: st.metric("Avg 10yr ROIC",          fmt(results_df['roic_avg'].mean() if 'roic_avg' in results_df else None, "pct"))
    with s4: st.metric("Cleared Both Hurdles",   len(results_df[results_df.get('debt_hurdle_cleared') == 'both']) if 'debt_hurdle_cleared' in results_df else 0)

    st.markdown("### 💾 Export Results")
    _export_cols = ['ticker','name','sector','industry','sub_industry',
                     'roic_avg','roic_avg_years','fcf_margin_avg','fcf_margin_avg_years',
                     'debt_to_ni','debt_to_cads','debt_hurdle_cleared',
                     'dilution_passed','dilution_pct_change','limited_history','funnel_years_used',
                     'is_cyclical','fcf_yield','price_owner_earn','dividend_yield','price','market_cap']
    _export_names = ['Ticker','Name','Sector','Industry','Sub-Industry',
                      'ROIC (10yr avg)','ROIC Years Used','FCF Margin (10yr avg)','FCF Margin Years Used',
                      'Debt/Net Income','Debt/CADS','Debt Hurdle Cleared',
                      'Dilution Passed','Shares Chg (5yr)','Limited History','Funnel Years Used',
                      'Cyclical','FCF Yield','Price/Owner Earnings','Dividend Yield','Price','Market Cap']
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

    ms_pending_q = st.session_state.pop("ms_pending_claude_q", None)
    ms_user_q    = st.chat_input("Ask Claude about these screen results...", key="ms_claude_input")
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

force_scroll_to_top()
