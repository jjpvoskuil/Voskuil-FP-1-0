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

EDGAR_BASE    = "https://data.sec.gov"
EDGAR_SEARCH  = "https://efts.sec.gov/LATEST/search-index"
HEADERS       = {"User-Agent": "VoskuilFP/1.0 research@voskuilfp.com"}

# Max chars to extract per section — keeps context window manageable
SECTION_LIMIT = 8_000


def get_cik(ticker: str) -> str | None:
    """Resolve ticker → zero-padded 10-digit CIK via EDGAR company search."""
    try:
        url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&dateRange=custom&startdt=2020-01-01&forms=10-K"
        # Use the company tickers JSON — most reliable mapping
        resp = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
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


def get_latest_10k_url(cik: str) -> tuple[str | None, str | None]:
    """
    Given a CIK, find the most recent 10-K filing index URL.
    Returns (filing_index_url, accession_number) or (None, None).
    """
    try:
        url = f"{EDGAR_BASE}/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=10-K&dateb=&owner=include&count=5&search_text=&output=atom"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None, None

        # Parse accession numbers from the atom feed
        text = resp.text
        accessions = re.findall(r'Accession Number:</b>\s*([\d-]+)', text)
        if not accessions:
            # Try alternate pattern
            accessions = re.findall(r'(\d{18}|\d{10}-\d{2}-\d{6})', text)

        # Use submissions JSON — cleaner approach
        sub_url = f"{EDGAR_BASE}/submissions/CIK{cik}.json"
        sub_resp = requests.get(sub_url, headers=HEADERS, timeout=10)
        if sub_resp.status_code != 200:
            return None, None

        sub_data = sub_resp.json()
        filings  = sub_data.get("filings", {}).get("recent", {})
        forms    = filings.get("form", [])
        accnos   = filings.get("accessionNumber", [])

        for i, form in enumerate(forms):
            if form in ("10-K", "10-K/A"):
                accno = accnos[i].replace("-", "")
                accno_dashed = accnos[i]
                index_url = (
                    f"https://www.sec.gov/Archives/edgar/data/"
                    f"{int(cik)}/{accno}/{accno_dashed}-index.htm"
                )
                return index_url, accno_dashed

        return None, None
    except Exception:
        return None, None


def get_10k_document_url(index_url: str) -> str | None:
    """
    Given the filing index page URL, find the URL of the actual 10-K document
    (the primary htm/html document, not exhibits).
    """
    try:
        resp = requests.get(index_url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None
        text = resp.text

        # Look for the primary document in the filing index table
        # Pattern: type=10-K document listed first
        matches = re.findall(
            r'href="(/Archives/edgar/data/[^"]+\.htm[l]?)"[^>]*>[^<]*(?:10-K|10k)',
            text, re.IGNORECASE
        )
        if matches:
            return "https://www.sec.gov" + matches[0]

        # Fallback: find all .htm files and take the largest one (usually the main doc)
        all_htm = re.findall(r'href="(/Archives/edgar/data/[^"]+\.htm[l]?)"', text)
        # Filter out exhibits (usually labeled ex-*)
        main_docs = [h for h in all_htm if not re.search(r'ex[-_]', h, re.IGNORECASE)]
        if main_docs:
            return "https://www.sec.gov" + main_docs[0]

        return None
    except Exception:
        return None


def extract_sections(doc_url: str) -> dict[str, str]:
    """
    Fetch the 10-K document and extract key sections by Item number.
    Returns dict with keys: business, risk_factors, mda, quantitative.
    Strips HTML tags and limits each section to SECTION_LIMIT chars.
    """
    try:
        resp = requests.get(doc_url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return {}

        raw = resp.text

        # Strip HTML tags
        clean = re.sub(r'<[^>]+>', ' ', raw)
        # Collapse whitespace
        clean = re.sub(r'\s+', ' ', clean).strip()

        sections = {}

        # Patterns for common Item headers in 10-Ks
        # Items can be formatted many ways: "Item 1.", "ITEM 1A.", "Item 1A —", etc.
        item_patterns = {
            "business":     r'Item\s+1[\.\s](?!A)[^\n]{0,40}(?:Business)',
            "risk_factors": r'Item\s+1A[\.\s][^\n]{0,40}(?:Risk Factor)',
            "mda":          r'Item\s+7[\.\s](?!A)[^\n]{0,60}(?:Management.{0,30}Discussion)',
            "quantitative": r'Item\s+7A[\.\s][^\n]{0,60}(?:Quantitative)',
        }

        # Find positions of each section
        positions = {}
        for key, pattern in item_patterns.items():
            match = re.search(pattern, clean, re.IGNORECASE)
            if match:
                positions[key] = match.start()

        # Extract content between sections (stop at next found section)
        sorted_keys = sorted(positions.keys(), key=lambda k: positions[k])
        for i, key in enumerate(sorted_keys):
            start = positions[key]
            # End at next section start, or SECTION_LIMIT chars, whichever is less
            if i + 1 < len(sorted_keys):
                next_key  = sorted_keys[i + 1]
                end       = min(positions[next_key], start + SECTION_LIMIT)
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
        return {"sections": {}, "filing_url": None, "error": f"Could not find CIK for {ticker} on EDGAR."}

    index_url, accno = get_latest_10k_url(cik)
    if not index_url:
        return {"sections": {}, "filing_url": None, "error": f"No 10-K filing found for {ticker}."}

    doc_url = get_10k_document_url(index_url)
    if not doc_url:
        return {"sections": {}, "filing_url": index_url, "error": "Found filing index but could not locate the main 10-K document."}

    sections = extract_sections(doc_url)
    if not sections:
        return {"sections": {}, "filing_url": index_url, "error": "Found the 10-K document but could not extract readable sections."}

    return {
        "sections":    sections,
        "filing_url":  index_url,
        "doc_url":     doc_url,
        "error":       None,
    }

