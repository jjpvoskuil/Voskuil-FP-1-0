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
            sub_resp = requests.get(
                f"{EDGAR_BASE}/submissions/{fname}",
                headers=HEADERS, timeout=10
            )
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
    Fetch the filing index page and find the primary 10-K document URL.

    EDGAR index URL pattern (NO subfolder in path):
      https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_dashed}-index.html

    Documents in the index are listed as bare filenames. The full document URL is:
      https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{filename}

    Returns (doc_url, index_url) or (None, index_url).
    """
    cik_int          = str(int(cik))
    accession_nodash = accession_dashed.replace("-", "")

    # Index page — no subfolder, .html extension
    index_url = (
        f"{SEC_BASE}/Archives/edgar/data/{cik_int}/"
        f"{accession_dashed}-index.html"
    )
    # Base path for resolving document filenames
    doc_base = (
        f"{SEC_BASE}/Archives/edgar/data/{cik_int}/{accession_nodash}/"
    )

    try:
        resp = requests.get(index_url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            # Try .htm fallback
            index_url = index_url.replace("-index.html", "-index.htm")
            resp      = requests.get(index_url, headers=HEADERS, timeout=10)
            if resp.status_code != 200:
                return None, index_url

        text = resp.text

        # Parse the document table — look for rows with type 10-K
        # The table has columns: Seq | Description | Document | Type | Size
        # We want the Document filename where Type = 10-K
        rows = re.findall(
            r'<tr[^>]*>(.*?)</tr>',
            text, re.IGNORECASE | re.DOTALL
        )

        for row in rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.IGNORECASE | re.DOTALL)
            if len(cells) >= 4:
                # Strip tags from each cell
                cell_text = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
                # Type column (index 3) should be "10-K"
                if cell_text[3] in ("10-K", "10-K/A"):
                    # Document column (index 2) has the filename — extract from href or text
                    doc_match = re.search(r'href="([^"]+\.htm[l]?)"', cells[2], re.IGNORECASE)
                    if doc_match:
                        fname = doc_match.group(1)
                        # fname may be bare filename or partial path
                        if fname.startswith("http"):
                            return fname, index_url
                        elif fname.startswith("/"):
                            return SEC_BASE + fname, index_url
                        else:
                            return doc_base + fname, index_url

        # Fallback: first .htm link that isn't an exhibit or data file
        exclude = re.compile(
            r'(xex|ex-|exhibit|_htm\.xml|\.xsd|_cal\.|_def\.|_lab\.|_pre\.)',
            re.IGNORECASE
        )
        all_links = re.findall(r'href="([^"]+\.htm[l]?)"', text, re.IGNORECASE)
        for link in all_links:
            if not exclude.search(link):
                if link.startswith("http"):
                    return link, index_url
                elif link.startswith("/"):
                    return SEC_BASE + link, index_url
                else:
                    return doc_base + link, index_url

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
            # Second match skips table of contents
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
                "error": f"Found 10-K index but could not locate main document. "
                         f"Accession: {accession}. Index: {index_url}"}

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
