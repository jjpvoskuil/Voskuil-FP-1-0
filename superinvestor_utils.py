"""
superinvestor_utils.py — Superinvestor 13F conviction tracker for Voskuil FP 1.0

Uses the SEC's pre-extracted quarterly 13F flat-file data sets.
Data source: https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets
Updated quarterly. No API key required.
"""

import io
import re
import zipfile
import requests
import pandas as pd

HEADERS  = {"User-Agent": "VoskuilFP/1.0 jvoskuil@foxdenholdings.com"}
SEC_BASE = "https://www.sec.gov"

DATASET_URLS = [
    f"{SEC_BASE}/files/structureddata/data/form-13f-data-sets/01dec2025-28feb2026_form13f.zip",
    f"{SEC_BASE}/files/structureddata/data/form-13f-data-sets/01sep2025-30nov2025_form13f.zip",
    f"{SEC_BASE}/files/structureddata/data/form-13f-data-sets/01jun2025-31aug2025_form13f.zip",
    f"{SEC_BASE}/files/structureddata/data/form-13f-data-sets/01mar2025-31may2025_form13f.zip",
    f"{SEC_BASE}/files/structureddata/data/form-13f-data-sets/01dec2024-28feb2025_form13f.zip",
]

SUPERINVESTORS = {
    "Warren Buffett (Berkshire)":  "0001067983",
    "Bill Ackman (Pershing Sq)":   "0001336528",
    "David Tepper (Appaloosa)":    "0001656456",
    "David Einhorn (Greenlight)":  "0001079114",
    "Seth Klarman (Baupost)":      "0001061768",
    "Chuck Akre (Akre Capital)":   "0001112520",
    "Tom Gayner (Markel)":         "0001096343",
    "Li Lu (Himalaya Capital)":    "0001582202",
    "Mohnish Pabrai (Pabrai Inv)": "0001173334",
    "Chris Bloomstran (Semper)":   "0001403419",
    "Pat Dorsey (Dorsey Asset)":   "0001655888",
    "Allan Mecham (Arlington)":    "0001427571",
    "Guy Spier (Aquamarine)":      "0001286973",
}

CIK_TO_NAME = {str(int(v)): k for k, v in SUPERINVESTORS.items()}

TICKER_NAME_MAP = {
    "BRK.B": "BERKSHIRE",    "BRK.A": "BERKSHIRE",
    "ABBV":  "ABBVIE",       "BMY":   "BRISTOL-MYERS",
    "MO":    "ALTRIA",       "PM":    "PHILIP MORRIS",
    "AMP":   "AMERIPRISE",   "KO":    "COCA COLA",
    "GOOGL": "ALPHABET",     "GOOG":  "ALPHABET",
    "META":  "META PLATF",   "MSFT":  "MICROSOFT",
    "AMZN":  "AMAZON",       "AAPL":  "APPLE",
    "JPM":   "JPMORGAN",     "BAC":   "BANK OF AMER",
    "WFC":   "WELLS FARGO",  "USB":   "U.S. BANCORP",
    "CVX":   "CHEVRON",      "XOM":   "EXXON",
    "JNJ":   "JOHNSON",      "PFE":   "PFIZER",
    "UNH":   "UNITEDHEALTH", "V":     "VISA",
    "MA":    "MASTERCARD",   "COST":  "COSTCO",
    "WMT":   "WALMART",      "HD":    "HOME DEPOT",
    "ADBE":  "ADOBE",        "CRM":   "SALESFORCE",
    "ACN":   "ACCENTURE",    "BKNG":  "BOOKING",
    "OXY":   "OCCIDENTAL",   "DVN":   "DEVON",
    "MCO":   "MOODYS",       "CB":    "CHUBB",
    "AXP":   "AMERICAN EXPRESS", "COF": "CAPITAL ONE",
    "GS":    "GOLDMAN",      "MS":    "MORGAN STANLEY",
    "C":     "CITIGROUP",    "ALLY":  "ALLY FINL",
    "CHTR":  "CHARTER",      "NVO":   "NOVO NORDISK",
    "LLY":   "LILLY",        "NVDA":  "NVIDIA",
    "HCA":   "HCA HEALTH",   "AXON":  "AXON",
}


def safe_int(x) -> int:
    try:
        return int(str(x).replace(",", "").strip())
    except Exception:
        return 0


def _load_13f_dataset():
    """Try each URL in order. Returns (sub_df, info_df, period_str)."""
    for url in DATASET_URLS:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=90, stream=True)
            if resp.status_code != 200:
                continue
            zdata = io.BytesIO(resp.content)
            with zipfile.ZipFile(zdata) as zf:
                names     = zf.namelist()
                sub_file  = next((n for n in names if "submission" in n.lower()), None)
                info_file = next((n for n in names if "infotable"  in n.lower()), None)
                if not sub_file or not info_file:
                    continue
                sub_df  = pd.read_csv(io.BytesIO(zf.read(sub_file)),  sep="\t", dtype=str, low_memory=False)
                info_df = pd.read_csv(io.BytesIO(zf.read(info_file)), sep="\t", dtype=str, low_memory=False)
                m = re.search(r'(\d{2}[a-z]{3}\d{4}-\d{2}[a-z]{3}\d{4})', url)
                return sub_df, info_df, (m.group(1) if m else "2025-Q4")
        except Exception:
            continue
    return pd.DataFrame(), pd.DataFrame(), ""


def clear_superinvestor_cache():
    import streamlit as st
    for k in list(st.session_state.keys()):
        if k.startswith("si_"):
            del st.session_state[k]
    st.session_state.pop("_13f_dataset", None)


def _match_ticker(ticker: str, name_upper: str) -> bool:
    search = TICKER_NAME_MAP.get(ticker.upper(), ticker.upper())
    return search in name_upper


def get_superinvestor_conviction(ticker: str) -> dict:
    import streamlit as st

    # Load and cache dataset
    if "_13f_dataset" not in st.session_state:
        with st.spinner("Loading SEC 13F dataset (quarterly superinvestor holdings)..."):
            sub_df, info_df, period_str = _load_13f_dataset()
        if sub_df.empty or info_df.empty:
            return {"holders": [], "holder_count": 0, "conviction_score": 0,
                    "period": "", "error": "Could not load 13F dataset."}
        st.session_state["_13f_dataset"] = {"sub_df": sub_df, "info_df": info_df, "period": period_str}

    cached  = st.session_state["_13f_dataset"]
    sub_df  = cached["sub_df"].copy()
    info_df = cached["info_df"].copy()
    period  = cached.get("period", "2025-Q4")

    sub_df.columns  = [c.strip().upper() for c in sub_df.columns]
    info_df.columns = [c.strip().upper() for c in info_df.columns]

    # Detect columns
    cik_col   = next((c for c in sub_df.columns  if "CIK"          in c), None)
    acc_col   = next((c for c in sub_df.columns  if "ACCESSION"    in c), None)
    tvt_col   = next((c for c in sub_df.columns  if "TABLEVALUE"   in c or "TABLE_VALUE" in c), None)
    name_col  = next((c for c in info_df.columns if "NAMEOFISSUER" in c or "NAME_OF"     in c), None)
    val_col   = next((c for c in info_df.columns if c in ("VALUE", "MARKETVAL", "MARKET_VALUE")), None)
    acc_col_i = next((c for c in info_df.columns if "ACCESSION"    in c), None)

    if not all([cik_col, acc_col, name_col, val_col, acc_col_i]):
        return {"holders": [], "holder_count": 0, "conviction_score": 0, "period": period,
                "error": f"Columns missing. Sub:{list(sub_df.columns)[:6]} Info:{list(info_df.columns)[:6]}"}

    # Filter submissions to our CIKs
    our_ciks = set(str(int(v)) for v in SUPERINVESTORS.values())
    def norm_cik(x):
        try: return str(int(x)) if str(x).strip().isdigit() else ""
        except: return ""
    sub_df["_cik_norm"] = sub_df[cik_col].apply(norm_cik)
    si_subs = sub_df[sub_df["_cik_norm"].isin(our_ciks)]

    if si_subs.empty:
        return {"holders": [], "holder_count": 0, "conviction_score": 0, "period": period,
                "error": f"No superinvestors found in dataset. Sample CIKs: {sub_df[cik_col].head(3).tolist()}"}

    # Build accession maps
    acc_to_investor = {}
    acc_to_total    = {}
    for _, row in si_subs.iterrows():
        cik_val = row["_cik_norm"]
        name    = CIK_TO_NAME.get(cik_val, "")
        acc     = str(row[acc_col]).strip()
        if name and acc:
            acc_to_investor[acc] = name
            if tvt_col:
                acc_to_total[acc] = safe_int(row[tvt_col])

    # Filter infotable and deduplicate
    our_accs = set(acc_to_investor.keys())
    si_info  = info_df[info_df[acc_col_i].str.strip().isin(our_accs)].copy()

    if si_info.empty:
        return {"holders": [], "holder_count": 0, "conviction_score": 0, "period": period,
                "error": f"Holdings not matched. Accessions sample: {list(our_accs)[:3]}",
                "_debug_acc_investors": list(acc_to_investor.values()),
                "_debug_missing": [n for n in SUPERINVESTORS if n not in acc_to_investor.values()]}

    si_info = si_info.drop_duplicates(subset=[acc_col_i, name_col])
    si_info["_name_upper"] = si_info[name_col].str.upper().str.strip()
    matches = si_info[si_info["_name_upper"].apply(lambda n: _match_ticker(ticker.upper(), n))]

    # Debug info (always populated)
    brk_acc    = [a for a, n in acc_to_investor.items() if "Buffett" in n]
    brk_sample = []
    brk_match  = []
    if brk_acc:
        brk_rows   = si_info[si_info[acc_col_i].str.strip().isin(brk_acc)]
        brk_sample = brk_rows["_name_upper"].head(15).tolist()
        search_term = TICKER_NAME_MAP.get(ticker.upper(), ticker.upper())
        brk_match  = brk_rows[brk_rows["_name_upper"].str.contains(search_term, na=False)]["_name_upper"].tolist()

    debug = {
        "_debug_cols":          [name_col, val_col, acc_col_i],
        "_debug_acc_count":     len(acc_to_investor),
        "_debug_acc_investors": list(acc_to_investor.values()),
        "_debug_missing":       [n for n in SUPERINVESTORS if n not in acc_to_investor.values()],
        "_debug_brk_sample":    brk_sample,
        "_debug_brk_match":     brk_match,
    }

    if matches.empty:
        return {"holders": [], "holder_count": 0, "conviction_score": 0, "period": period,
                "error": f"{ticker} not found. Search term: '{TICKER_NAME_MAP.get(ticker.upper(), ticker.upper())}'. Berkshire sample: {brk_sample[:8]}",
                **debug}

    # Build holders list
    holders      = []
    no_match_inv = []  # investors in dataset but don't hold this ticker
    for acc, investor in acc_to_investor.items():
        inv_rows   = si_info[si_info[acc_col_i].str.strip() == acc]
        match_rows = matches[matches[acc_col_i].str.strip() == acc]
        if match_rows.empty:
            no_match_inv.append(f"{investor}({len(inv_rows)} holdings)")
            continue

        pos_val   = match_rows[val_col].apply(safe_int).sum()
        total_val = acc_to_total.get(acc, 0)
        if total_val == 0:
            total_val = inv_rows[val_col].apply(safe_int).sum()
        # Cap obviously inflated totals (dataset contains multiple quarters)
        # Real single-quarter totals: Berkshire ~$300B, most others <$50B
        # If total > $500B it's almost certainly summed across multiple filings
        if total_val > 500_000_000:  # in thousands, so this is $500B
            # Re-sum from matched accession's holdings only
            total_val = inv_rows[val_col].apply(safe_int).sum()

        pct = round(pos_val / total_val * 100, 2) if total_val > 0 else 0.0

        holders.append({
            "investor": investor,
            "pct":      pct,
            "value":    pos_val * 1000,
        })

    holders.sort(key=lambda x: x["pct"], reverse=True)

    n       = len(holders)
    avg_pct = sum(h["pct"] for h in holders) / n if n > 0 else 0
    score   = min(60, int(n / len(SUPERINVESTORS) * 60)) + min(40, int(avg_pct / 10 * 40))

    debug["_debug_no_match"] = no_match_inv
    return {"holders": holders, "holder_count": n, "conviction_score": score,
            "period": period, "error": None, **debug}
