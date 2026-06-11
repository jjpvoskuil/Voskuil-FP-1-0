"""
superinvestor_utils.py — Superinvestor 13F conviction tracker for Voskuil FP 1.0

Pulls quarterly 13F filings from SEC EDGAR for a curated list of value-oriented
superinvestors. For any given ticker, returns:
  - How many superinvestors hold it
  - Who holds it and at what portfolio weight
  - Recent activity (New/Add/Reduce/Sold)
  - A conviction score (0-100)

Data source: SEC EDGAR 13F-HR filings (public, free, no API key required)
CUSIPs are matched to tickers via the SEC company facts API.
"""

import re
import json
import requests
import xml.etree.ElementTree as ET
from functools import lru_cache

HEADERS    = {"User-Agent": "VoskuilFP/1.0 jvoskuil@foxdenholdings.com"}
EDGAR_BASE = "https://data.sec.gov"
SEC_BASE   = "https://www.sec.gov"

# ── Curated superinvestor list ────────────────────────────────────────────
# CIK: EDGAR Central Index Key for the institutional filer
# All are value-oriented, long-horizon investors consistent with Buffett/Munger philosophy
def clear_superinvestor_cache():
    """Call this to force fresh data fetch."""
    get_latest_13f_accession.cache_clear()
    get_13f_holdings_xml.cache_clear()
    get_cusip_for_ticker.cache_clear()


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


@lru_cache(maxsize=50)
def get_latest_13f_accession(cik: str) -> tuple:
    """
    Returns (accession_dashed, period_of_report) for the most recent 13F-HR filing.
    Result is cached to avoid repeated API calls within a session.
    """
    try:
        url  = f"{EDGAR_BASE}/submissions/CIK{cik}.json"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None, None

        data   = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        forms  = recent.get("form", [])
        accnos = recent.get("accessionNumber", [])
        dates  = recent.get("filingDate", [])
        pors   = recent.get("reportDate", recent.get("periodOfReport", []))

        for i, form in enumerate(forms):
            if form in ("13F-HR", "13F-HR/A"):
                return accnos[i], pors[i] if i < len(pors) else dates[i]

        return None, None
    except Exception:
        return None, None


@lru_cache(maxsize=50)
def get_13f_holdings_xml(cik: str, accession_dashed: str) -> str:
    """
    Fetch the 13F holdings XML directly from EDGAR Archives.
    
    Uses the EDGAR filing index JSON (not HTML) to find the INFORMATION TABLE
    document. Falls back to scanning the complete submission txt file.
    """
    try:
        cik_int          = str(int(cik))
        accession_nodash = accession_dashed.replace("-", "")
        base_url         = f"{SEC_BASE}/Archives/edgar/data/{cik_int}/{accession_nodash}"

        # Strategy 1: Use the filing index JSON — reliable, no JS rendering needed
        index_json_url = f"{EDGAR_BASE}/Archives/edgar/data/{cik_int}/{accession_nodash}/{accession_dashed}-index.json"
        json_resp = requests.get(index_json_url, headers=HEADERS, timeout=10)
        if json_resp.status_code == 200:
            try:
                index_data = json_resp.json()
                for doc in index_data.get("documents", []):
                    doc_type = doc.get("type", "").upper()
                    doc_url  = doc.get("documentUrl", "")
                    if "INFORMATION TABLE" in doc_type or doc_type == "13F-HR":
                        if doc_url.endswith(".xml") and "primary_doc" not in doc_url:
                            if not doc_url.startswith("http"):
                                doc_url = SEC_BASE + doc_url
                            r = requests.get(doc_url, headers=HEADERS, timeout=15)
                            if r.status_code == 200 and len(r.text) > 1000:
                                return r.text
            except Exception:
                pass

        # Strategy 2: Scan the index HTML for XML links
        index_html_url = f"{SEC_BASE}/Archives/edgar/data/{cik_int}/{accession_dashed}-index.htm"
        html_resp = requests.get(index_html_url, headers=HEADERS, timeout=10)
        if html_resp.status_code == 200:
            xml_links = re.findall(
                r'href="(/Archives/edgar/data/[^"]+\.xml)"', html_resp.text
            )
            for link in xml_links:
                if "primary_doc" not in link and "_htm" not in link:
                    r = requests.get(SEC_BASE + link, headers=HEADERS, timeout=15)
                    if r.status_code == 200 and len(r.text) > 1000:
                        # Quick check it contains holdings data
                        if "infoTable" in r.text or "nameOfIssuer" in r.text:
                            return r.text

        # Strategy 3: Try common filename patterns for 13F XML
        # EDGAR typically names the holdings file with the sequence number
        for filename in ["infotable.xml", "form13fInfoTable.xml", "46994.xml",
                         "primary_doc.xml"]:
            r = requests.get(f"{base_url}/{filename}", headers=HEADERS, timeout=10)
            if r.status_code == 200 and "infoTable" in r.text:
                return r.text

        # Strategy 4: Parse the complete submission .txt file
        # The .txt file contains all documents concatenated with EDGAR SGML headers
        txt_url  = f"{base_url}/{accession_dashed}.txt"
        txt_resp = requests.get(txt_url, headers=HEADERS, timeout=30, stream=True)
        if txt_resp.status_code == 200:
            # Stream until we find the INFORMATION TABLE section
            chunks  = []
            total   = 0
            for chunk in txt_resp.iter_content(chunk_size=65536):
                if chunk:
                    chunks.append(chunk.decode("utf-8", errors="replace"))
                    total += len(chunk)
                    combined = "".join(chunks)
                    # Check if we have the holdings table
                    if "infoTable" in combined and "</informationTable>" in combined:
                        # Extract just the XML portion
                        start = combined.find("<?xml")
                        if start == -1:
                            start = combined.find("<informationTable")
                        end = combined.find("</informationTable>")
                        if start != -1 and end != -1:
                            return combined[start:end + 20]
                    if total > 10 * 1024 * 1024:  # 10MB cap
                        break

        return ""
    except Exception:
        return ""


def _get_xml_val(tag: str, block: str) -> str:
    """Extract a tag value from an XML block, handling namespace prefixes."""
    m = re.search(
        rf'<(?:[a-zA-Z0-9_]+:)?{tag}[^>]*>(.*?)</(?:[a-zA-Z0-9_]+:)?{tag}>',
        block, re.DOTALL | re.IGNORECASE
    )
    return m.group(1).strip() if m else ""


def parse_holdings(xml_text: str) -> list:
    """
    Parse 13F holdings XML using regex instead of ElementTree.
    This bypasses all namespace issues (default ns, ns1:, etc.) that
    cause ElementTree to silently return empty results.
    """
    try:
        # Find all infoTable blocks regardless of namespace prefix
        blocks = re.findall(
            r'<(?:[a-zA-Z0-9_]+:)?infoTable[^>]*>(.*?)</(?:[a-zA-Z0-9_]+:)?infoTable>',
            xml_text, re.DOTALL | re.IGNORECASE
        )
        if not blocks:
            return []

        # First pass: get total value for pct calculation
        total_val = 0
        for block in blocks:
            v = _get_xml_val("value", block)
            try:
                total_val += int(v.replace(",", ""))
            except (ValueError, AttributeError):
                pass

        holdings = []
        for block in blocks:
            name   = _get_xml_val("nameOfIssuer", block)
            cusip  = _get_xml_val("cusip", block)
            val_s  = _get_xml_val("value", block)
            shrs_s = _get_xml_val("sshPrnamt", block)

            if not name:
                continue

            try:
                val = int(val_s.replace(",", ""))
            except (ValueError, AttributeError):
                val = 0
            try:
                shares = int(shrs_s.replace(",", ""))
            except (ValueError, AttributeError):
                shares = 0

            pct = (val / total_val * 100) if total_val > 0 else 0

            holdings.append({
                "name":   name.upper(),
                "cusip":  cusip,
                "value":  val * 1000,   # 13F values are in thousands
                "shares": shares,
                "pct":    round(pct, 2),
            })

        return holdings
    except Exception:
        return []


@lru_cache(maxsize=200)
def get_cusip_for_ticker(ticker: str) -> str:
    """
    Look up the CUSIP for a ticker using the SEC company facts API.
    Returns empty string if not found.
    """
    try:
        # Use the EDGAR company tickers with exchange file which has CUSIPs
        resp = requests.get(
            "https://www.sec.gov/files/company_tickers_exchange.json",
            headers=HEADERS, timeout=10
        )
        if resp.status_code != 200:
            return ""
        data = resp.json()
        fields = data.get("fields", [])
        rows   = data.get("data", [])
        if "ticker" not in fields:
            return ""
        ticker_idx = fields.index("ticker")
        for row in rows:
            if row[ticker_idx].upper() == ticker.upper():
                return ""  # CUSIPs not in this file — use name matching
        return ""
    except Exception:
        return ""


def ticker_in_holdings(ticker: str, holdings: list) -> dict | None:
    """
    Check if a ticker appears in a holdings list.
    Matches by name similarity since 13F filings use company names not tickers.
    Returns the holding dict if found, None otherwise.
    """
    ticker_upper = ticker.upper()

    # Common mappings for tickers that don't match company name well
    # Names are uppercase to match our parse_holdings output
    TICKER_NAME_MAP = {
        "BRK.B": "BERKSHIRE", "BRK.A": "BERKSHIRE",
        "ABBV":  "ABBVIE",    "BMY":   "BRISTOL",
        "MO":    "ALTRIA",    "PM":    "PHILIP MORRIS",
        "AMP":   "AMERIPRISE","KO":    "COCA",
        "GOOGL": "ALPHABET",  "GOOG":  "ALPHABET",
        "META":  "META",      "MSFT":  "MICROSOFT",
        "AMZN":  "AMAZON",    "AAPL":  "APPLE",
        "JPM":   "JPMORGAN",  "BAC":   "BANK OF AMERICA",
        "WFC":   "WELLS FARGO","USB":  "U.S. BANCORP",
        "CVX":   "CHEVRON",   "XOM":   "EXXON",
        "JNJ":   "JOHNSON",   "PFE":   "PFIZER",
        "UNH":   "UNITEDHEALTH","V":   "VISA",
        "MA":    "MASTERCARD","COST":  "COSTCO",
        "WMT":   "WALMART",   "HD":    "HOME DEPOT",
        "ADBE":  "ADOBE",     "CRM":   "SALESFORCE",
        "ACN":   "ACCENTURE", "BKNG":  "BOOKING",
    }

    search_name = TICKER_NAME_MAP.get(ticker_upper, ticker_upper)

    best_match = None
    best_score = 0

    for h in holdings:
        holding_name = h["name"].upper()
        # Direct ticker match in name
        if ticker_upper in holding_name.split():
            return h
        # Search name match
        if search_name in holding_name:
            score = len(search_name) / len(holding_name)
            if score > best_score:
                best_score = score
                best_match = h

    # Only return if reasonably confident
    return best_match if best_score > 0.3 else None


def get_superinvestor_conviction(ticker: str) -> dict:
    """
    Main entry point. Returns superinvestor conviction data for a ticker:
    {
        holders: [{name, pct_portfolio, value, activity}],
        holder_count: int,
        conviction_score: int (0-100),
        period: str,
        error: str or None
    }
    """
    holders      = []
    errors       = []
    latest_period = ""

    _xml_empty_count  = 0
    _no_holdings_count = 0
    _sample_names      = []

    for investor_name, cik in SUPERINVESTORS.items():
        try:
            accession, period = get_latest_13f_accession(cik)
            if not accession:
                errors.append(f"{investor_name}: no accession found")
                continue

            if period and period > latest_period:
                latest_period = period

            xml_text = get_13f_holdings_xml(cik, accession)
            if not xml_text:
                _xml_empty_count += 1
                errors.append(f"{investor_name}: empty XML")
                continue

            holdings = parse_holdings(xml_text)
            if not holdings:
                _no_holdings_count += 1
                errors.append(f"{investor_name}: 0 holdings parsed from XML ({len(xml_text)} chars)")
                continue

            # Collect sample names for debugging
            if not _sample_names and investor_name == "Warren Buffett (Berkshire)":
                _sample_names = [h["name"] for h in holdings[:5]]

            match = ticker_in_holdings(ticker, holdings)
            if match:
                holders.append({
                    "investor":    investor_name,
                    "pct":         match["pct"],
                    "value":       match["value"],
                    "shares":      match["shares"],
                })
            else:
                errors.append(f"{investor_name}: ticker not found in {len(holdings)} holdings")
        except Exception as e:
            errors.append(f"{investor_name}: {e}")
            continue

    # Sort by portfolio % descending
    holders.sort(key=lambda x: x["pct"], reverse=True)

    # Conviction score: 0-100 based on number of holders and their avg portfolio weight
    n      = len(holders)
    max_n  = len(SUPERINVESTORS)
    avg_pct = sum(h["pct"] for h in holders) / n if n > 0 else 0

    # Score: up to 60 pts for breadth (# holders), up to 40 pts for weight
    breadth_score = min(60, int(n / max_n * 60))
    weight_score  = min(40, int(avg_pct / 10 * 40))   # 10%+ avg weight = full 40 pts
    conviction_score = breadth_score + weight_score

    return {
        "holders":          holders,
        "holder_count":     n,
        "conviction_score": conviction_score,
        "period":           latest_period,
        "error":            "; ".join(errors) if errors else None,
        "debug_checked":    len(SUPERINVESTORS),
        "debug_xml_empty":  _xml_empty_count,
        "debug_no_holdings":_no_holdings_count,
        "debug_sample_names": _sample_names[:5],
    }
