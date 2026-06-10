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

SECTION_LIMIT = 8_000


def get_cik(ticker: str):
    """Resolve ticker -> zero-padded 10-digit CIK."""
    try:
        resp = requests.get(
            f"{SEC_BASE}/files/company_tickers.json",
            headers=HEADERS, timeout=10
        )
        if resp.status_code != 200:
            return None
        for entry in resp.json().values():
            if entry.get("ticker", "").upper() == ticker.upper():
                return str(entry["cik_str"]).zfill(10)
        return None
    except Exception:
        return None


def get_latest_10k_accession(cik: str):
    """
    Use EDGAR submissions REST API to find most recent 10-K.
    Returns (accession_dashed, filing_date) or (None, None).
    """
    try:
        resp = requests.get(
            f"{EDGAR_BASE}/submissions/CIK{cik}.json",
            headers=HEADERS, timeout=10
        )
        if resp.status_code != 200:
            return None, None

        data   = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        forms  = recent.get("form", [])
        accnos = recent.get("accessionNumber", [])
        dates  = recent.get("filingDate", [])

        for i, form in enumerate(forms):
            if form in ("10-K", "10-K/A"):
                return accnos[i], dates[i]

        # Check older filing pages
        for file_entry in data.get("filings", {}).get("files", []):
            fname    = file_entry.get("name", "")
            sub_resp = requests.get(f"{EDGAR_BASE}/submissions/{fname}", headers=HEADERS, timeout=10)
            if sub_resp.status_code == 200:
                sub = sub_resp.json()
                for i, form in enumerate(sub.get("form", [])):
                    if form in ("10-K", "10-K/A"):
                        return sub["accessionNumber"][i], sub["filingDate"][i]

        return None, None
    except Exception:
        return None, None


def get_10k_document_url(cik: str, accession_dashed: str):
    """
    Fetch the filing index HTML page and extract the primary 10-K document URL.
    Returns (doc_url, index_url) or (None, index_url).
    """
    accession_nodash = accession_dashed.replace("-", "")
    cik_int          = str(int(cik))
    index_url        = f"{SEC_BASE}/Archives/edgar/data/{cik_int}/{accession_nodash}/{accession_dashed}-index.htm"

    try:
        resp = requests.get(index_url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            # Try .html extension
            index_url = index_url.replace("-index.htm", "-index.html")
            resp      = requests.get(index_url, headers=HEADERS, timeout=10)
            if resp.status_code != 200:
                return None, index_url

        text = resp.text

        # Find all document links in the index table
        # Pattern matches href="/Archives/edgar/data/.../filename.htm"
        all_links = re.findall(
            r'href="(/Archives/edgar/data/[^"]+\.(htm|html))"',
            text, re.IGNORECASE
        )

        # Filter to likely main documents — exclude exhibits and data files
        exclude = re.compile(
            r'(ex[-_]|exhibit|xbrl|_htm\.xml|def\.xml|lab\.xml|pre\.xml|\.xsd)',
            re.IGNORECASE
        )

        candidates = [
            SEC_BASE + href
            for href, _ in all_links
            if not exclude.search(href)
        ]

        if candidates:
            return candidates[0], index_url

        # Last resort: any .htm that isn't clearly an exhibit
        if all_links:
            return SEC_BASE + all_links[0][0], index_url

        return None, index_url

    except Exception:
        return None, index_url


def extract_sections(doc_url: str) -> dict:
    """
    Fetch the 10-K document and extract key sections.
    Uses second occurrence of each Item header (first = table of contents).
    """
    try:
        resp = requests.get(doc_url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            return {}

        # Strip HTML and clean text
        clean = re.sub(r'<[^>]+>', ' ', resp.text)
        clean = re.sub(r'&[a-zA-Z#0-9]+;', ' ', clean)
        clean = re.sub(r'\s+', ' ', clean).strip()

        item_patterns = {
            "business":     r'ITEM\s+1[\.\s](?!A\b).{0,60}?BUSINESS\b',
            "risk_factors": r'ITEM\s+1A[\.\s].{0,60}?RISK\s+FACTOR',
            "mda":          r'ITEM\s+7[\.\s](?!A\b).{0,80}?MANAGEMENT.{0,40}?DISCUSSION',
            "quantitative": r'ITEM\s+7A[\.\s].{0,80}?QUANTITATIVE',
        }

        positions = {}
        for key, pattern in item_patterns.items():
            matches = list(re.finditer(pattern, clean, re.IGNORECASE))
            # Use second match if available (skip table of contents)
            if len(matches) >= 2:
                positions[key] = matches[1].start()
            elif len(matches) == 1:
                positions[key] = matches[0].start()

        if not positions:
            return {}

        sections    = {}
        sorted_keys = sorted(positions.keys(), key=lambda k: positions[k])
        for i, key in enumerate(sorted_keys):
            start = positions[key]
            end   = positions[sorted_keys[i + 1]] if i + 1 < len(sorted_keys) else start + SECTION_LIMIT
            end   = min(end, start + SECTION_LIMIT)
            sections[key] = clean[start:end].strip()

        return sections

    except Exception:
        return {}


def fetch_10k_sections(ticker: str) -> dict:
    """
    Main entry point.
    Returns dict: {sections, filing_url, doc_url, filing_date, error}
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
                "error": f"Found 10-K index ({accession}) but could not locate the main document. "
                         f"Check manually: {index_url}"}

    sections = extract_sections(doc_url)
    if not sections:
        return {"sections": {}, "filing_url": index_url,
                "error": "Fetched the 10-K document but could not extract Item sections. "
                         "The filing may use an unusual format."}

    return {
        "sections":    sections,
        "filing_url":  index_url,
        "doc_url":     doc_url,
        "filing_date": filing_date,
        "error":       None,
    }
