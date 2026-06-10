"""
sec_utils.py — SEC EDGAR filing fetcher for Voskuil FP 1.0

Fetches the most recent 10-K for a ticker and extracts key sections:
  - Item 1  (Business)
  - Item 1A (Risk Factors)
  - Item 7  (MD&A)
  - Item 7A (Quantitative Disclosures)

Uses only the public EDGAR REST API — no auth required.
"""

import re
import requests

EDGAR_BASE = "https://data.sec.gov"
SEC_BASE   = "https://www.sec.gov"
HEADERS    = {"User-Agent": "VoskuilFP/1.0 jvoskuil@foxdenholdings.com"}

# Max chars to extract per section
SECTION_LIMIT = 8_000


def get_cik(ticker: str):
    """Resolve ticker -> zero-padded 10-digit CIK via EDGAR company tickers JSON."""
    try:
        resp = requests.get(
            f"{SEC_BASE}/files/company_tickers.json",
            headers=HEADERS, timeout=10
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        ticker_upper = ticker.upper()
        for entry in data.values():
            if entry.get("ticker", "").upper() == ticker_upper:
                return str(entry["cik_str"]).zfill(10)
        return None
    except Exception:
        return None


def get_latest_10k_accession(cik: str):
    """
    Use the EDGAR submissions REST API to find the most recent 10-K.
    Returns (accession_number_dashed, filing_date) or (None, None).
    The submissions API returns JSON directly — no HTML parsing needed.
    """
    try:
        url  = f"{EDGAR_BASE}/submissions/CIK{cik}.json"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None, None

        data     = resp.json()
        recent   = data.get("filings", {}).get("recent", {})
        forms    = recent.get("form", [])
        accnos   = recent.get("accessionNumber", [])
        dates    = recent.get("filingDate", [])

        for i, form in enumerate(forms):
            if form in ("10-K", "10-K/A"):
                return accnos[i], dates[i]   # e.g. "0001551152-24-000007", "2024-02-16"

        # If not in recent, check older filings pages
        older_files = data.get("filings", {}).get("files", [])
        for file_entry in older_files:
            fname = file_entry.get("name", "")
            sub_resp = requests.get(f"{EDGAR_BASE}/submissions/{fname}", headers=HEADERS, timeout=10)
            if sub_resp.status_code == 200:
                sub_data = sub_resp.json()
                o_forms  = sub_data.get("form", [])
                o_accnos = sub_data.get("accessionNumber", [])
                o_dates  = sub_data.get("filingDate", [])
                for i, form in enumerate(o_forms):
                    if form in ("10-K", "10-K/A"):
                        return o_accnos[i], o_dates[i]

        return None, None
    except Exception:
        return None, None


def get_10k_document_url(cik: str, accession_dashed: str):
    """
    Given CIK and accession number, fetch the filing index JSON and
    find the URL of the primary 10-K htm document.
    """
    try:
        accession_nodash = accession_dashed.replace("-", "")
        index_url = (
            f"{EDGAR_BASE}/Archives/edgar/data/{int(cik)}/"
            f"{accession_nodash}/{accession_dashed}-index.json"
        )
        resp = requests.get(index_url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None, None

        data     = resp.json()
        docs     = data.get("documents", [])

        # Primary document is type 10-K
        for doc in docs:
            if doc.get("type") in ("10-K", "10-K/A") and doc.get("documentUrl"):
                doc_url = doc["documentUrl"]
                if not doc_url.startswith("http"):
                    doc_url = SEC_BASE + doc_url
                return doc_url, index_url.replace("-index.json", "-index.htm")

        # Fallback: first .htm file that isn't an exhibit
        for doc in docs:
            name = doc.get("name", "")
            if name.endswith((".htm", ".html")) and not re.search(r'ex[-_]', name, re.IGNORECASE):
                doc_url = doc.get("documentUrl", "")
                if not doc_url.startswith("http"):
                    doc_url = SEC_BASE + doc_url
                return doc_url, index_url.replace("-index.json", "-index.htm")

        return None, None
    except Exception:
        return None, None


def extract_sections(doc_url: str):
    """
    Fetch the 10-K document and extract key sections by Item number.
    Returns dict with keys: business, risk_factors, mda, quantitative.
    """
    try:
        resp = requests.get(doc_url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            return {}

        raw   = resp.text
        clean = re.sub(r'<[^>]+>', ' ', raw)
        clean = re.sub(r'&[a-zA-Z]+;', ' ', clean)   # HTML entities
        clean = re.sub(r'\s+', ' ', clean).strip()

        item_patterns = {
            "business":     r'Item\s+1[\.\s](?!A\b)[^\n]{0,60}?(?:Business\b)',
            "risk_factors": r'Item\s+1A[\.\s][^\n]{0,60}?(?:Risk\s+Factor)',
            "mda":          r'Item\s+7[\.\s](?!A\b)[^\n]{0,80}?(?:Management.{0,40}?Discussion)',
            "quantitative": r'Item\s+7A[\.\s][^\n]{0,80}?(?:Quantitative)',
        }

        positions = {}
        for key, pattern in item_patterns.items():
            # Find the SECOND occurrence — first is usually table of contents
            matches = list(re.finditer(pattern, clean, re.IGNORECASE))
            if len(matches) >= 2:
                positions[key] = matches[1].start()
            elif len(matches) == 1:
                positions[key] = matches[0].start()

        sections      = {}
        sorted_keys   = sorted(positions.keys(), key=lambda k: positions[k])
        for i, key in enumerate(sorted_keys):
            start = positions[key]
            if i + 1 < len(sorted_keys):
                end = min(positions[sorted_keys[i + 1]], start + SECTION_LIMIT)
            else:
                end = start + SECTION_LIMIT
            sections[key] = clean[start:end].strip()

        return sections

    except Exception:
        return {}


def fetch_10k_sections(ticker: str) -> dict:
    """
    Main entry point. Returns dict with:
      - sections: {business, risk_factors, mda, quantitative}
      - filing_url: the index page URL for display
      - error: error message string if something failed, else None
    """
    cik = get_cik(ticker)
    if not cik:
        return {"sections": {}, "filing_url": None,
                "error": f"Could not find CIK for {ticker} on EDGAR."}

    accession, filing_date = get_latest_10k_accession(cik)
    if not accession:
        return {"sections": {}, "filing_url": None,
                "error": f"No 10-K filing found for {ticker} in EDGAR submissions."}

    doc_url, index_url = get_10k_document_url(cik, accession)
    if not doc_url:
        return {"sections": {}, "filing_url": index_url,
                "error": "Found 10-K filing index but could not locate the main document."}

    sections = extract_sections(doc_url)
    if not sections:
        return {"sections": {}, "filing_url": index_url,
                "error": "Found the 10-K document but could not extract readable sections."}

    return {
        "sections":    sections,
        "filing_url":  index_url,
        "doc_url":     doc_url,
        "filing_date": filing_date,
        "error":       None,
    }
