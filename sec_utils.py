"""
sec_utils.py — SEC EDGAR filing fetcher for Voskuil FP 1.0

Two data paths:
1. fetch_10k_sections()    — Qualitative: pulls 10-K narrative text for Claude analysis.
2. fetch_company_facts()   — Quantitative: pulls XBRL Company Facts for scoring engine.

The Company Facts API (data.sec.gov/api/xbrl/companyfacts/) returns every
XBRL-tagged value from every filing ever submitted — the authoritative primary
source, free and permanent. Concept → field mapping is in edgar_concept_map.py.

Data model design: all historical annual periods are retained, not just the
latest. This is the foundation for 10-year ROIC trending (#34/#40), full-cycle
analysis (#37), and the historical normalization layer (#52).
"""

import re
import requests
import streamlit as st
from edgar_concept_map import CONCEPT_MAP, FINANCIAL_SIC_CODES, CYCLICAL_SIC_CODES

EDGAR_BASE    = "https://data.sec.gov"
SEC_BASE      = "https://www.sec.gov"
HEADERS       = {"User-Agent": "VoskuilFP/1.0 jvoskuil@foxdenholdings.com"}
SECTION_LIMIT = 8_000


def get_cik(ticker: str):
    """
    Single-ticker CIK lookup. For bulk lookups (Market Screener scanning
    hundreds of tickers), use get_ticker_cik_map() instead and look up
    from the returned dict — this avoids re-downloading the full
    company_tickers.json file (10,000+ entries) on every call.
    """
    try:
        resp = requests.get(f"{SEC_BASE}/files/company_tickers.json", headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None, f"company_tickers.json returned {resp.status_code}"
        for entry in resp.json().values():
            if entry.get("ticker", "").upper() == ticker.upper():
                return str(entry["cik_str"]).zfill(10), None
        return None, f"Ticker {ticker} not found in EDGAR tickers list"
    except Exception as e:
        return None, str(e)


def get_ticker_cik_map() -> dict:
    """
    Fetch the full EDGAR ticker -> CIK mapping ONCE.
    Returns {"AAPL": "0000320193", "MSFT": "0000789019", ...}

    This is the key optimization for bulk scanning (Market Screener):
    one ~1MB download instead of one redundant download per ticker.
    Cache this in st.session_state at the call site for the duration
    of a screen run.
    """
    try:
        resp = requests.get(f"{SEC_BASE}/files/company_tickers.json", headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        return {
            entry.get("ticker", "").upper(): str(entry["cik_str"]).zfill(10)
            for entry in data.values()
            if entry.get("ticker")
        }
    except Exception:
        return {}


def fetch_company_facts_with_cik(ticker: str, cik: str) -> dict:
    """
    Same as fetch_company_facts() but accepts a pre-resolved CIK,
    skipping the CIK lookup step entirely. Used by bulk scanners
    (Market Screener) that already built a ticker_cik_map once via
    get_ticker_cik_map().
    """
    if not cik:
        return {"latest": {}, "history": {}, "meta": {}, "missing": [],
                "error": f"No CIK provided for {ticker}"}
    return _fetch_company_facts_for_cik(ticker, cik)



def fetch_company_facts(ticker: str) -> dict:
    """
    Fetch XBRL Company Facts from SEC EDGAR for a given ticker.

    Returns a dict with two top-level keys:

    "latest"  → dict of scoring fields → most recent annual value
                 e.g. {"op_cf": 13335000000, "net_income": 8099000000, ...}
                 This is what the scoring engine consumes directly.

    "history" → dict of scoring fields → list of annual observations,
                 sorted oldest → newest:
                 e.g. {"op_cf": [
                     {"period": "2015", "end": "2015-08-30", "value": 4285000000},
                     {"period": "2016", "end": "2016-08-28", "value": 4601000000},
                     ...
                 ]}
                 This powers 10-year ROIC trending (#34/#40), full-cycle
                 analysis (#37), and the historical normalization layer (#52).

    Also returns:
    "meta"    → {"ticker", "cik", "company_name", "sic", "is_financial",
                  "is_cyclical", "fiscal_year_end", "last_annual_period"}
    "error"   → None on success, error string on failure
    "missing" → list of scoring fields not found in this company's XBRL data
    """
    cik, err = get_cik(ticker)
    if not cik:
        return {"latest": {}, "history": {}, "meta": {}, "missing": [],
                "error": f"CIK lookup failed: {err}"}
    return _fetch_company_facts_for_cik(ticker, cik)


def _fetch_company_facts_for_cik(ticker: str, cik: str) -> dict:
    """Internal: does the actual Company Facts fetch + parse once CIK is known."""
    # Fetch Company Facts JSON
    # This returns ALL XBRL concepts ever filed — typically 2-8MB for large caps
    url = f"{EDGAR_BASE}/api/xbrl/companyfacts/CIK{cik}.json"
    try:

        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            return {"latest": {}, "history": {}, "meta": {}, "missing": [],
                    "error": f"Company Facts API returned {resp.status_code} for CIK {cik}"}
        data = resp.json()
    except requests.Timeout:
        return {"latest": {}, "history": {}, "meta": {}, "missing": [],
                "error": "Timeout fetching Company Facts (>30s)"}
    except Exception as e:
        return {"latest": {}, "history": {}, "meta": {}, "missing": [],
                "error": f"Error fetching Company Facts: {e}"}

    # 3. Extract company metadata
    entity_name = data.get("entityName", ticker)
    facts       = data.get("facts", {})
    us_gaap     = facts.get("us-gaap", {})

    # Get SIC from submissions API (lightweight call, cached by EDGAR)
    sic = None
    try:
        sub_resp = requests.get(
            f"{EDGAR_BASE}/submissions/CIK{cik}.json",
            headers=HEADERS, timeout=10
        )
        if sub_resp.status_code == 200:
            sic = str(sub_resp.json().get("sic", ""))
    except Exception:
        pass

    meta = {
        "ticker":            ticker.upper(),
        "cik":               cik,
        "company_name":      entity_name,
        "sic":               sic,
        "is_financial":      sic in FINANCIAL_SIC_CODES if sic else False,
        "is_cyclical":       sic in CYCLICAL_SIC_CODES  if sic else False,
        "last_annual_period": None,
        "fiscal_year_end":   None,
    }

    # 4. For each scoring field, try concept candidates in priority order
    latest  = {}   # field → most recent annual value (float or None)
    history = {}   # field → sorted list of annual observations

    all_annual_ends = []  # track all period end dates to find fiscal year

    for field, concepts in CONCEPT_MAP.items():
        field_history = []

        for concept in concepts:
            if concept not in us_gaap:
                continue

            concept_data = us_gaap[concept]
            units        = concept_data.get("units", {})

            # Most financial concepts use USD; shares use "shares"
            unit_key = "USD"
            if field in ("diluted_shares",):
                unit_key = "shares"
                if unit_key not in units:
                    unit_key = "USD"  # some filers tag shares in USD units

            observations = units.get(unit_key, [])
            if not observations:
                # Try the other unit key as fallback
                alt = "shares" if unit_key == "USD" else "USD"
                observations = units.get(alt, [])

            # Filter to annual (10-K) filings only
            # EDGAR uses "form" field: "10-K", "10-K/A", "20-F" (foreign filers)
            # Also validate period duration — annual periods span ~340-370 days.
            # Some concepts include quarterly sub-period values even inside 10-K filings
            # (segment data, interim comparatives). Filter those out by duration.
            def is_annual_duration(obs):
                start = obs.get("start")
                end   = obs.get("end")
                if not start or not end:
                    return True  # no dates to check — allow through
                try:
                    from datetime import date
                    d0 = date.fromisoformat(start)
                    d1 = date.fromisoformat(end)
                    days = (d1 - d0).days
                    return 340 <= days <= 400  # full fiscal year window
                except Exception:
                    return True

            annual_obs = [
                o for o in observations
                if o.get("form") in ("10-K", "10-K/A", "20-F", "20-F/A")
                and o.get("end")
                and is_annual_duration(o)
            ]

            if not annual_obs:
                continue

            # Deduplicate: if multiple entries share the same end date
            # (e.g. original + amended), prefer the latest filed
            seen_ends = {}
            for o in sorted(annual_obs, key=lambda x: x.get("filed", "")):
                seen_ends[o["end"]] = o
            annual_obs = sorted(seen_ends.values(), key=lambda x: x["end"])

            # Build history list for this field
            field_history = [
                {
                    "period": o["end"][:4],          # fiscal year as string e.g. "2024"
                    "end":    o["end"],               # exact period end date
                    "value":  o.get("val"),           # raw value in USD or shares
                    "filed":  o.get("filed", ""),     # filing date
                    "form":   o.get("form", ""),
                }
                for o in annual_obs
                if o.get("val") is not None
            ]

            if field_history:
                all_annual_ends.extend([h["end"] for h in field_history])
                break  # found a working concept — stop trying aliases

        if field_history:
            history[field] = field_history
            latest[field]  = field_history[-1]["value"]  # most recent annual

    # 5. Identify missing fields
    missing = [f for f in CONCEPT_MAP if f not in latest]

    # 6. Determine last annual period and fiscal year end
    if all_annual_ends:
        last_end = max(all_annual_ends)
        meta["last_annual_period"] = last_end[:4]
        meta["fiscal_year_end"]    = last_end

    # 7. Compute derived fields on the latest period
    # These are stored in latest[] so the scoring engine can use them directly
    op_cf   = latest.get("op_cf")
    inv_cf  = latest.get("inv_cf")
    capex   = latest.get("capex")
    net_inc = latest.get("net_income")
    dna     = latest.get("dna")
    eq      = latest.get("total_equity")
    ltd     = latest.get("long_term_debt", 0) or 0
    std     = latest.get("short_term_debt", 0) or 0
    cash    = latest.get("cash", 0) or 0
    op_inc  = latest.get("op_income")
    int_pd  = latest.get("interest_paid") or latest.get("interest_expense")

    # FCF: operating CF + investing CF (investing is negative, so this subtracts capex proxy)
    if op_cf is not None and inv_cf is not None:
        latest["fcf"] = op_cf + inv_cf
    elif op_cf is not None and capex is not None:
        latest["fcf"] = op_cf - abs(capex)

    # Invested capital
    if eq is not None:
        latest["invested_cap"] = eq + ltd + std

    # Total debt
    latest["total_debt"] = ltd + std

    # Net debt
    latest["net_debt"] = ltd + std - cash

    # ROIC
    inv_cap = latest.get("invested_cap")
    if net_inc is not None and inv_cap and inv_cap != 0:
        latest["roic"] = net_inc / inv_cap

    # Debt / FCF
    fcf = latest.get("fcf")
    if fcf and fcf > 0 and (ltd + std) > 0:
        latest["debt_to_fcf"] = (ltd + std) / fcf

    # Gross margin — with sanity check and COGS fallback
    # Some companies (e.g. NVDA) don't tag GrossProfit cleanly in XBRL,
    # causing multi-period rollup values that make GM appear > 100%.
    # Fix: validate GM is in (0, 1], fall back to Revenue - COGS if not.
    rev = latest.get("revenue")
    gp  = latest.get("gross_profit")
    cor = latest.get("cost_of_revenue")

    if gp is not None and rev is not None and rev > 0:
        gm_check = gp / rev
        if gm_check > 1.0 or gm_check < -0.5:
            # Bad XBRL value — try COGS fallback
            if cor is not None and rev is not None:
                gp = rev - cor
                latest["gross_profit"] = gp
            else:
                gp = None
                latest["gross_profit"] = None
    elif gp is None and rev is not None and cor is not None:
        # GrossProfit concept not tagged — derive from COGS
        gp = rev - cor
        latest["gross_profit"] = gp

    if rev and rev > 0 and gp is not None:
        gm = gp / rev
        # Final sanity gate — if still nonsensical after COGS fallback, null it
        if 0.0 <= gm <= 1.0:
            latest["gross_margin"] = gm

    # Interest coverage
    if op_inc is not None and int_pd and int_pd > 0:
        latest["int_coverage"] = op_inc / int_pd

    # Owner earnings (Buffett: net income + D&A - maintenance capex)
    capex_val = capex if capex is not None else (inv_cf if inv_cf is not None else None)
    if net_inc is not None and dna is not None and capex_val is not None:
        latest["owner_earnings"] = net_inc + dna - abs(capex_val)
    elif net_inc is not None and op_cf is not None:
        # Proxy: use op_cf - net_income as D&A proxy when D&A not available
        dna_proxy = op_cf - net_inc
        if capex_val is not None:
            latest["owner_earnings"] = net_inc + dna_proxy - abs(capex_val)

    return {
        "latest":  latest,
        "history": history,
        "meta":    meta,
        "missing": missing,
        "error":   None,
    }


def get_latest_10k_accession(cik: str):
    """
    Returns (accession_dashed, filing_date, error).
    Skips 10-K/A amendments — we want the original filing.
    """
    try:
        resp = requests.get(f"{EDGAR_BASE}/submissions/CIK{cik}.json", headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None, None, f"submissions API returned {resp.status_code}"
        data   = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        forms  = recent.get("form", [])
        accnos = recent.get("accessionNumber", [])
        dates  = recent.get("filingDate", [])

        # Prefer original 10-K over 10-K/A amendment
        for i, form in enumerate(forms):
            if form == "10-K":
                return accnos[i], dates[i], None

        # Fall back to 10-K/A if no original found
        for i, form in enumerate(forms):
            if form == "10-K/A":
                return accnos[i], dates[i], None

        # Check older filing pages
        for file_entry in data.get("filings", {}).get("files", []):
            fname    = file_entry.get("name", "")
            sub_resp = requests.get(f"{EDGAR_BASE}/submissions/{fname}", headers=HEADERS, timeout=10)
            if sub_resp.status_code == 200:
                sub = sub_resp.json()
                for i, form in enumerate(sub.get("form", [])):
                    if form == "10-K":
                        return sub["accessionNumber"][i], sub["filingDate"][i], None

        return None, None, "No 10-K found in submissions"
    except Exception as e:
        return None, None, str(e)


def get_complete_submission_url(cik: str, accession_dashed: str) -> str:
    """
    Build the URL for the complete submission .txt file.
    Format: /Archives/edgar/data/{cik_int}/{accession_nodash}/{accession_dashed}.txt
    """
    cik_int          = str(int(cik))
    accession_nodash = accession_dashed.replace("-", "")
    return (
        f"{SEC_BASE}/Archives/edgar/data/{cik_int}/"
        f"{accession_nodash}/{accession_dashed}.txt"
    )


def extract_10k_body(submission_text: str) -> str:
    """
    Parse the complete submission .txt file and extract the 10-K body document.

    The .txt format wraps each document like:
        <DOCUMENT>
        <TYPE>10-K
        <SEQUENCE>1
        <FILENAME>mo-20241231.htm
        <DESCRIPTION>10-K
        <TEXT>
        ...actual filing content...
        </TEXT>
        </DOCUMENT>

    We find the first DOCUMENT block with TYPE=10-K and extract its TEXT content.
    """
    # Find all DOCUMENT blocks
    doc_blocks = re.split(r'<DOCUMENT>', submission_text, flags=re.IGNORECASE)

    for block in doc_blocks[1:]:  # skip content before first <DOCUMENT>
        # Get the TYPE for this block
        type_match = re.search(r'<TYPE>\s*(\S+)', block, re.IGNORECASE)
        if not type_match:
            continue
        doc_type = type_match.group(1).strip().upper()

        if doc_type != "10-K":
            continue

        # Extract text between <TEXT> and </TEXT>
        text_match = re.search(r'<TEXT>(.*?)(?:</TEXT>|</DOCUMENT>)', block, re.IGNORECASE | re.DOTALL)
        if text_match:
            return text_match.group(1).strip()

    return ""


def clean_filing_text(raw: str) -> str:
    """
    Strip HTML/SGML tags and clean the filing body text.
    Handles both plain text and HTML-wrapped filings.
    """
    # Remove SGML/HTML tags
    clean = re.sub(r'<[^>]+>', ' ', raw)
    # Decode common HTML entities
    clean = clean.replace('&nbsp;', ' ')
    clean = clean.replace('&amp;',  '&')
    clean = clean.replace('&lt;',   '<')
    clean = clean.replace('&gt;',   '>')
    clean = re.sub(r'&[a-zA-Z#0-9]+;', ' ', clean)
    # Collapse whitespace
    clean = re.sub(r'[ \t]+', ' ', clean)
    clean = re.sub(r'\n{3,}', '\n\n', clean)
    clean = clean.strip()
    return clean


def extract_sections(clean_text: str) -> dict:
    """
    Extract key 10-K sections from cleaned plain text.
    Uses multiple pattern strategies to handle different filing formats.
    """
    # Primary patterns — match "Item N." or "Item N " in any capitalisation
    item_patterns = {
        "business":     r'Item\s+1(?:\.|\s)(?!A\b).{0,80}?Business\b',
        "risk_factors": r'Item\s+1A(?:\.|\s).{0,80}?Risk\s+Factor',
        "mda":          r'Item\s+7(?:\.|\s)(?!A\b).{0,100}?(?:Management|MD&A).{0,60}?(?:Discussion|Analysis)',
        "quantitative": r'Item\s+7A(?:\.|\s).{0,80}?Quantitative',
    }

    positions = {}
    for key, pattern in item_patterns.items():
        matches = list(re.finditer(pattern, clean_text, re.IGNORECASE))
        # Skip table of contents — use second occurrence if available
        if len(matches) >= 2:
            positions[key] = matches[1].start()
        elif len(matches) == 1:
            positions[key] = matches[0].start()

    # Fallback: simpler numeric patterns
    if len(positions) < 2:
        fallback = {
            "business":     r'(?:^|\n)\s*1\.\s{1,10}Business\b',
            "risk_factors": r'(?:^|\n)\s*1A\.\s{1,10}Risk',
            "mda":          r'(?:^|\n)\s*7\.\s{1,10}(?:Management|MD&A)',
            "quantitative": r'(?:^|\n)\s*7A\.\s{1,10}Quantitative',
        }
        for key, pattern in fallback.items():
            if key not in positions:
                matches = list(re.finditer(pattern, clean_text, re.IGNORECASE | re.MULTILINE))
                if len(matches) >= 2:
                    positions[key] = matches[1].start()
                elif len(matches) == 1:
                    positions[key] = matches[0].start()

    # Last resort: return a large body chunk
    if not positions:
        mid = clean_text[5_000:29_000]
        return {"business": mid} if mid else {}

    sections    = {}
    sorted_keys = sorted(positions.keys(), key=lambda k: positions[k])
    for i, key in enumerate(sorted_keys):
        start = positions[key]
        end   = positions[sorted_keys[i + 1]] if i + 1 < len(sorted_keys) else start + SECTION_LIMIT
        end   = min(end, start + SECTION_LIMIT)
        sections[key] = clean_text[start:end].strip()

    return sections


def fetch_10k_sections(ticker: str) -> dict:
    """
    Main entry point. Fetches the complete submission .txt file from EDGAR
    and extracts 10-K narrative sections for qualitative analysis.

    Returns dict: {sections, filing_url, doc_url, filing_date, error}
    """
    # 1. Resolve ticker -> CIK
    cik, err = get_cik(ticker)
    if not cik:
        return {"sections": {}, "filing_url": None,
                "error": f"CIK lookup failed: {err}"}

    # 2. Find most recent 10-K accession number
    accession, filing_date, err = get_latest_10k_accession(cik)
    if not accession:
        return {"sections": {}, "filing_url": None,
                "error": f"10-K accession lookup failed: {err}"}

    # 3. Build filing index URL (for display)
    cik_int          = str(int(cik))
    accession_nodash = accession.replace("-", "")
    index_url = (
        f"{SEC_BASE}/Archives/edgar/data/{cik_int}/"
        f"{accession_nodash}/{accession}-index.htm"
    )

    # 4. Fetch the complete submission .txt file
    # These files can be 20MB+. We stream and stop after capturing the 10-K body
    # to avoid loading the entire file (exhibits can be huge).
    txt_url = get_complete_submission_url(cik, accession)
    try:
        resp = requests.get(txt_url, headers=HEADERS, timeout=60, stream=True)
        if resp.status_code != 200:
            return {"sections": {}, "filing_url": index_url,
                    "error": f"Complete submission file returned HTTP {resp.status_code}. URL: {txt_url}"}

        # Read in chunks, stop once we've found and closed the 10-K DOCUMENT block
        MAX_BYTES      = 15 * 1024 * 1024  # 15MB cap
        chunks         = []
        total          = 0
        found_10k_end  = False

        for chunk in resp.iter_content(chunk_size=65536):
            if chunk:
                chunks.append(chunk.decode('utf-8', errors='replace'))
                total += len(chunk)
                partial = ''.join(chunks)
                # Stop once we've passed the first 10-K </DOCUMENT> block
                if re.search(r'<TYPE>10-K', partial, re.IGNORECASE):
                    end_pos = partial.find('</DOCUMENT>', partial.find('<TYPE>10-K'))
                    if end_pos > -1:
                        chunks = [partial[:end_pos + 11]]
                        found_10k_end = True
                        break
                if total >= MAX_BYTES:
                    break

        submission_text = ''.join(chunks)

    except requests.Timeout:
        return {"sections": {}, "filing_url": index_url,
                "error": "Timeout fetching complete submission file (>60s)."}
    except Exception as e:
        return {"sections": {}, "filing_url": index_url,
                "error": f"Error fetching submission file: {e}"}

    # 5. Extract the 10-K body from the submission
    body = extract_10k_body(submission_text)
    if not body:
        return {"sections": {}, "filing_url": index_url,
                "error": f"Could not find 10-K body in complete submission file ({len(submission_text):,} chars). "
                         f"File may use an unexpected format."}

    # 6. Clean the text
    clean_text = clean_filing_text(body)
    if len(clean_text) < 5_000:
        return {"sections": {}, "filing_url": index_url,
                "error": f"10-K body cleaned to only {len(clean_text):,} chars — likely corrupt or empty."}

    # 7. Extract sections
    sections = extract_sections(clean_text)
    if not sections:
        return {"sections": {}, "filing_url": index_url,
                "error": f"Extracted 10-K body ({len(clean_text):,} chars) but could not locate Item sections."}

    return {
        "sections":    sections,
        "filing_url":  index_url,
        "doc_url":     txt_url,
        "filing_date": filing_date,
        "error":       None,
    }


# ── Shared helpers: value formatting + fundamentals fetch ────────────────────
# Moved here from pages/7_Equity_Scout_EDGAR.py (originally page-local) so that
# any page needing full fundamentals — Equity Scout, Market Screener deep-dive,
# and the Compare Stocks page (#60) — can call one canonical implementation
# instead of drifting copies.

def safe_float(val):
    """Coerce to float, returning None instead of raising on bad input."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def fmt_val(val, fmt="money"):
    """Format a numeric value for display: money ($B/$M), pct, or ratio (x)."""
    if val is None:
        return "N/A"
    if fmt == "money":
        return f"${val/1e9:.2f}B" if abs(val) >= 1e9 else f"${val/1e6:.1f}M"
    if fmt == "pct":
        return f"{val:.1%}"
    if fmt == "ratio":
        return f"{val:.1f}x"
    return str(val)


@st.cache_data(ttl=900)
def fetch_price_and_market_cap(ticker):
    """
    Fetch current price and market cap from yfinance.
    EDGAR provides shares outstanding; we use yfinance for live price only.
    Returns dict with price, market_cap, shares, dividend_yield.
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        price      = info.get("currentPrice") or info.get("regularMarketPrice")
        market_cap = info.get("marketCap")
        shares     = info.get("sharesOutstanding")
        div_yield  = info.get("dividendYield")
        name       = info.get("longName") or info.get("shortName") or ticker
        sector     = info.get("sector", "N/A")
        description = (info.get("longBusinessSummary", "")[:400] + "...") if info.get("longBusinessSummary") else ""
        return {
            "price":         safe_float(price),
            "market_cap":    safe_float(market_cap),
            "shares":        safe_float(shares),
            "dividend_yield": safe_float(div_yield),
            "name":          name,
            "sector":        sector,
            "description":   description,
        }
    except Exception as e:
        return {"price": None, "market_cap": None, "shares": None,
                "dividend_yield": None, "name": ticker, "sector": "N/A",
                "description": "", "error": str(e)}


@st.cache_data(ttl=3600)
def fetch_fundamentals_edgar(ticker):
    """
    Primary data fetch using SEC EDGAR Company Facts API.
    Falls back gracefully when concepts are missing.
    Returns a data dict compatible with score_stock() in Equity Scout EDGAR
    and Market Screener EDGAR, plus raw "_history"/"_latest" for trend charts
    (Compare Stocks page, #60).
    """
    # 1. Fetch EDGAR company facts (fundamentals + history)
    facts = fetch_company_facts(ticker)
    if facts.get("error"):
        return {"error": facts["error"]}

    latest = facts["latest"]
    meta   = facts["meta"]
    missing = facts.get("missing", [])

    # 2. Fetch live price + market cap from yfinance
    price_data = fetch_price_and_market_cap(ticker)
    price      = price_data.get("price")
    market_cap = price_data.get("market_cap")
    shares     = price_data.get("shares") or latest.get("diluted_shares")
    div_yield  = price_data.get("dividend_yield")

    # Use yfinance name/sector/description as primary (richer than EDGAR entity name)
    name        = price_data.get("name") or meta.get("company_name", ticker)
    sector      = price_data.get("sector") or meta.get("sic", "N/A")
    description = price_data.get("description", "")

    # 3. Pull pre-computed scoring fields from EDGAR latest
    op_cf        = latest.get("op_cf")
    inv_cf       = latest.get("inv_cf")
    fcf          = latest.get("fcf")
    net_income   = latest.get("net_income")
    revenues     = latest.get("revenue")
    gross_profit = latest.get("gross_profit")
    gross_margin = latest.get("gross_margin")
    roic         = latest.get("roic")
    long_term_debt = latest.get("long_term_debt", 0) or 0
    short_term_debt = latest.get("short_term_debt", 0) or 0
    total_debt   = long_term_debt + short_term_debt
    debt_to_fcf  = latest.get("debt_to_fcf")
    int_coverage = latest.get("int_coverage")
    owner_earn   = latest.get("owner_earnings")
    dna          = latest.get("dna")

    # 4. Valuation metrics (need price)
    fcf_yield   = (fcf / market_cap) if (fcf and market_cap and market_cap > 0) else None
    poe         = None
    if owner_earn and owner_earn > 0 and shares and price:
        poe = price / (owner_earn / shares)

    # 5. FCF growth (compare latest vs prior year from history)
    fcf_growth = None
    history = facts.get("history", {})
    op_cf_hist = history.get("op_cf", [])
    inv_cf_hist = history.get("inv_cf", [])
    if len(op_cf_hist) >= 2 and len(inv_cf_hist) >= 2:
        try:
            fcf_prior = op_cf_hist[-2]["value"] + inv_cf_hist[-2]["value"]
            if fcf_prior and fcf_prior != 0 and fcf:
                fcf_growth = (fcf / fcf_prior) - 1
        except Exception:
            pass

    # 6. Interest coverage — prefer cash-basis interest paid
    is_net_creditor = False
    int_exp = latest.get("interest_paid") or latest.get("interest_expense")
    op_inc  = latest.get("op_income")
    if int_exp and int_exp > 0 and op_inc is not None:
        int_coverage = op_inc / int_exp
    elif int_exp is None or int_exp == 0:
        # No interest expense — likely net creditor
        cash     = latest.get("cash", 0) or 0
        if cash > total_debt:
            is_net_creditor = True

    return {
        # Identity
        "name":             name,
        "sector":           sector,
        "description":      description,
        "ticker":           ticker.upper(),
        "cik":              meta.get("cik"),
        "is_financial":     meta.get("is_financial", False),
        "is_cyclical":      meta.get("is_cyclical", False),
        "fiscal_year":      meta.get("last_annual_period"),
        "sic":              meta.get("sic"),
        "data_source":      "SEC EDGAR Company Facts",
        "missing_concepts": missing,

        # Pricing (yfinance)
        "price":            price,
        "market_cap":       market_cap,
        "shares":           shares,

        # Cash flow
        "op_cf":            op_cf,
        "inv_cf":           inv_cf,
        "fcf":              fcf,
        "fcf_yield":        fcf_yield,
        "fcf_growth":       fcf_growth,

        # Income
        "revenues":         revenues,
        "gross_profit":     gross_profit,
        "gross_margin":     gross_margin,
        "net_income":       net_income,

        # Quality metrics
        "roic":             roic,
        "long_term_debt":   long_term_debt,
        "short_term_debt":  short_term_debt,
        "total_debt":       total_debt,
        "debt_to_fcf":      debt_to_fcf,
        "interest_coverage": int_coverage,
        "is_net_creditor":  is_net_creditor,

        # Owner earnings
        "owner_earnings":   owner_earn,
        "price_owner_earn": poe,
        "dna":              dna,

        # Income
        "dividend_yield":   div_yield,

        # Raw EDGAR history (for ROIC trending, Compare Stocks trend charts)
        "_history":         history,
        "_latest":          latest,
    }
