"""
superinvestor_utils.py — Superinvestor 13F conviction tracker for Voskuil FP 1.0

Uses the SEC's pre-extracted quarterly 13F flat-file data sets instead of
parsing individual XML filings. Far more reliable — the SEC has already
extracted all holdings into clean TSV files.

Data source: https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets
Updated quarterly by the SEC. No API key required.
"""

import io
import re
import json
import zipfile
import requests
import pandas as pd
from datetime import datetime

HEADERS    = {"User-Agent": "VoskuilFP/1.0 jvoskuil@foxdenholdings.com"}
EDGAR_BASE = "https://data.sec.gov"
SEC_BASE   = "https://www.sec.gov"

# ── Curated superinvestor list ────────────────────────────────────────────
# CIK: EDGAR Central Index Key — used to filter the 13F flat files
SUPERINVESTORS = {
    "Warren Buffett (Berkshire)":  "0001067983",
    "Bill Ackman (Pershing Sq)":   "0001336528",
    "Seth Klarman (Baupost)":      "0000893818",
    "David Tepper (Appaloosa)":    "0001006438",
    "David Einhorn (Greenlight)":  "0001079114",
    "Mohnish Pabrai (Pabrai Inv)": "0001173334",
    "Li Lu (Himalaya Capital)":    "0001582202",
    "Chuck Akre (Akre Capital)":   "0001113928",
    "Tom Gayner (Markel)":         "0001096343",
    "Chris Bloomstran (Semper)":   "0001403419",
    "Pat Dorsey (Dorsey Asset)":   "0001655888",
    "Allan Mecham (Arlington)":    "0001427571",
    "Guy Spier (Aquamarine)":      "0001286973",
}

# Reverse map: CIK (no leading zeros) -> investor name
CIK_TO_NAME = {str(int(v)): k for k, v in SUPERINVESTORS.items()}

# Ticker -> partial company name for matching (uppercase)
TICKER_NAME_MAP = {
    "BRK.B": "BERKSHIRE",   "BRK.A": "BERKSHIRE",
    "ABBV":  "ABBVIE",      "BMY":   "BRISTOL-MYERS",
    "MO":    "ALTRIA",      "PM":    "PHILIP MORRIS",
    "AMP":   "AMERIPRISE",  "KO":    "COCA-COLA",
    "GOOGL": "ALPHABET",    "GOOG":  "ALPHABET",
    "META":  "META PLATF",  "MSFT":  "MICROSOFT",
    "AMZN":  "AMAZON",      "AAPL":  "APPLE",
    "JPM":   "JPMORGAN",    "BAC":   "BANK OF AMER",
    "WFC":   "WELLS FARGO", "USB":   "U.S. BANCORP",
    "CVX":   "CHEVRON",     "XOM":   "EXXON",
    "JNJ":   "JOHNSON",     "PFE":   "PFIZER",
    "UNH":   "UNITEDHEALTH","V":     "VISA",
    "MA":    "MASTERCARD",  "COST":  "COSTCO",
    "WMT":   "WALMART",     "HD":    "HOME DEPOT",
    "ADBE":  "ADOBE",       "CRM":   "SALESFORCE",
    "ACN":   "ACCENTURE",   "BKNG":  "BOOKING",
    "AXON":  "AXON",        "NVO":   "NOVO NORDISK",
    "ASML":  "ASML",        "TSM":   "TAIWAN SEMI",
    "OXY":   "OCCIDENTAL",  "DVN":   "DEVON ENERGY",
    "HCA":   "HCA HEALTHC", "MCO":   "MOODYS",
    "CB":    "CHUBB",       "AXP":   "AMERICAN EXPRESS",
    "COF":   "CAPITAL ONE", "GS":    "GOLDMAN",
    "MS":    "MORGAN STANLEY","C":   "CITIGROUP",
    "ALLY":  "ALLY FINL",   "CHTR":  "CHARTER COMM",
    "LSXMA": "LIBERTY MEDIA","FWONA":"FORMULA ONE",
}


# Dataset URL patterns — SEC uses date-range filenames after 2023
# Format: https://www.sec.gov/files/structureddata/data/form-13f-data-sets/{daterange}_form13f.zip
# Ordered most-recent-first so we try the latest available data first
DATASET_URLS = [
    f"{SEC_BASE}/files/structureddata/data/form-13f-data-sets/01dec2025-28feb2026_form13f.zip",
    f"{SEC_BASE}/files/structureddata/data/form-13f-data-sets/01sep2025-30nov2025_form13f.zip",
    f"{SEC_BASE}/files/structureddata/data/form-13f-data-sets/01jun2025-31aug2025_form13f.zip",
    f"{SEC_BASE}/files/structureddata/data/form-13f-data-sets/01mar2025-31may2025_form13f.zip",
    f"{SEC_BASE}/files/structureddata/data/form-13f-data-sets/01dec2024-28feb2025_form13f.zip",
]


def _get_latest_quarter() -> tuple[str, str]:
    """Kept for compatibility — not used in new URL scheme."""
    now = datetime.now()
    y, m = now.year, now.month
    if m >= 5:   return str(y), "q1"
    if m >= 2:   return str(y - 1), "q4"
    return str(y - 1), "q3"


def _build_dataset_url(year: str, quarter: str) -> str:
    """Not used — we now use DATASET_URLS list directly."""
    return DATASET_URLS[0]


def _load_13f_dataset(year: str = "", quarter: str = "") -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Download and parse the most recent available SEC 13F dataset zip.
    Tries URLs in DATASET_URLS order until one succeeds.
    Returns (submissions_df, infotable_df) — empty DataFrames on failure.
    """
    for url in DATASET_URLS:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=90, stream=True)
            if resp.status_code != 200:
                continue

            zdata = io.BytesIO(resp.content)
            with zipfile.ZipFile(zdata) as zf:
                names = zf.namelist()
                sub_file  = next((n for n in names if "submission" in n.lower()), None)
                info_file = next((n for n in names if "infotable" in n.lower()), None)

                if not sub_file or not info_file:
                    continue

                sub_df  = pd.read_csv(io.BytesIO(zf.read(sub_file)),  sep="\t", dtype=str, low_memory=False)
                info_df = pd.read_csv(io.BytesIO(zf.read(info_file)), sep="\t", dtype=str, low_memory=False)
                return sub_df, info_df
        except Exception:
            continue

    return pd.DataFrame(), pd.DataFrame()


def clear_superinvestor_cache():
    """Clear the cached dataset — forces re-download on next call."""
    import streamlit as st
    for key in list(st.session_state.keys()):
        if key.startswith("si_"):
            del st.session_state[key]
    if "_13f_dataset" in st.session_state:
        del st.session_state["_13f_dataset"]


def _match_ticker(ticker: str, name_upper: str) -> bool:
    """Check if a company name matches the given ticker."""
    search = TICKER_NAME_MAP.get(ticker.upper(), ticker.upper())
    return search in name_upper


def get_superinvestor_conviction(ticker: str) -> dict:
    """
    Main entry point. Uses the SEC quarterly 13F flat-file dataset.
    Returns conviction data for the given ticker.
    """
    import streamlit as st

    # Load dataset (cached in session state)
    ds_key = "_13f_dataset"
    if ds_key not in st.session_state:
        year, quarter = _get_latest_quarter()
        with st.spinner(f"Loading SEC 13F dataset ({year} {quarter.upper()})..."):
            sub_df, info_df = _load_13f_dataset()

        if sub_df.empty or info_df.empty:
            return {
                "holders": [], "holder_count": 0,
                "conviction_score": 0, "period": "",
                "error": f"Could not load 13F dataset for {year} {quarter}",
            }

        # Determine period from the URL that worked (or use latest known)
        period_str = "2025-Q4"
        st.session_state[ds_key] = {
            "sub_df":   sub_df,
            "info_df":  info_df,
            "year":     "2025",
            "quarter":  "q4",
            "period":   period_str,
        }

    cached  = st.session_state[ds_key]
    sub_df  = cached["sub_df"]
    info_df = cached["info_df"]
    period  = cached.get("period", "2025-Q4")

    # Normalise column names
    sub_df.columns  = [c.strip().upper() for c in sub_df.columns]
    info_df.columns = [c.strip().upper() for c in info_df.columns]

    # Filter submissions to our superinvestors
    cik_col = next((c for c in sub_df.columns if "CIK" in c), None)
    if not cik_col:
        return {"holders": [], "holder_count": 0, "conviction_score": 0,
                "period": period, "error": "CIK column not found in submissions"}

    our_ciks = set(str(int(v)) for v in SUPERINVESTORS.values())
    si_subs  = sub_df[sub_df[cik_col].apply(
        lambda x: str(int(x)) if x and str(x).strip().isdigit() else ""
    ).isin(our_ciks)]

    if si_subs.empty:
        return {"holders": [], "holder_count": 0, "conviction_score": 0,
                "period": period, "error": "No superinvestor filings found in dataset"}

    # Get accession numbers for our investors
    acc_col = next((c for c in sub_df.columns if "ACCESSION" in c), None)
    if not acc_col:
        return {"holders": [], "holder_count": 0, "conviction_score": 0,
                "period": period, "error": "Accession column not found"}

    # Build map: accession -> investor name
    acc_to_investor = {}
    for _, row in si_subs.iterrows():
        cik_val = str(int(row[cik_col])) if str(row[cik_col]).strip().isdigit() else ""
        name    = CIK_TO_NAME.get(cik_val, "")
        if name:
            acc_to_investor[row[acc_col].strip()] = name

    # Find ticker matches in infotable
    name_col  = next((c for c in info_df.columns if "NAMEOFISSUER" in c or "NAME_OF" in c), None)
    val_col   = next((c for c in info_df.columns if c in ("VALUE", "MARKETVAL")), None)
    acc_col_i = next((c for c in info_df.columns if "ACCESSION" in c), None)

    if not all([name_col, val_col, acc_col_i]):
        return {"holders": [], "holder_count": 0, "conviction_score": 0,
                "period": period,
                "error": f"Required columns not found. Available: {list(info_df.columns)[:10]}"}

    # Filter to our investors' accessions
    our_accs  = set(acc_to_investor.keys())
    si_info   = info_df[info_df[acc_col_i].str.strip().isin(our_accs)].copy()

    if si_info.empty:
        return {"holders": [], "holder_count": 0, "conviction_score": 0,
                "period": period, "error": "No holdings found for superinvestors in dataset"}

    # Match ticker in name column
    ticker_upper = ticker.upper()
    si_info["_name_upper"] = si_info[name_col].str.upper().str.strip()
    matches = si_info[si_info["_name_upper"].apply(lambda n: _match_ticker(ticker_upper, n))]

    if matches.empty:
        # Debug: show sample names from Berkshire
        brk_acc = [a for a, n in acc_to_investor.items() if "Buffett" in n]
        sample  = []
        if brk_acc:
            brk_rows = si_info[si_info[acc_col_i].str.strip().isin(brk_acc)]
            sample   = brk_rows["_name_upper"].head(10).tolist()
        return {"holders": [], "holder_count": 0, "conviction_score": 0,
                "period": period,
                "error": f"Ticker {ticker} not found. Sample Berkshire names: {sample}"}

    # Compute per-investor totals and positions
    holders = []
    for acc, investor in acc_to_investor.items():
        inv_rows  = si_info[si_info[acc_col_i].str.strip() == acc]
        if inv_rows.empty:
            continue

        # Total portfolio value (in thousands)
        def safe_int(x):
            try: return int(str(x).replace(",", "").strip())
            except: return 0

        total_val = inv_rows[val_col].apply(safe_int).sum()

        # Matched rows
        match_rows = matches[matches[acc_col_i].str.strip() == acc]
        if match_rows.empty:
            continue

        pos_val = match_rows[val_col].apply(safe_int).sum()
        pct     = (pos_val / total_val * 100) if total_val > 0 else 0

        holders.append({
            "investor": investor,
            "pct":      round(pct, 2),
            "value":    pos_val * 1000,   # dataset values in thousands
        })

    holders.sort(key=lambda x: x["pct"], reverse=True)

    n         = len(holders)
    max_n     = len(SUPERINVESTORS)
    avg_pct   = sum(h["pct"] for h in holders) / n if n > 0 else 0
    breadth   = min(60, int(n / max_n * 60))
    weight    = min(40, int(avg_pct / 10 * 40))
    score     = breadth + weight

    return {
        "holders":          holders,
        "holder_count":     n,
        "conviction_score": score,
        "period":           period,
        "error":            None,
    }
