"""
sec_utils.py — SEC EDGAR filing fetcher for Voskuil FP 1.0
"""

import re
import requests

EDGAR_BASE    = "https://data.sec.gov"
SEC_BASE      = "https://www.sec.gov"
HEADERS       = {"User-Agent": "VoskuilFP/1.0 jvoskuil@foxdenholdings.com"}
SECTION_LIMIT = 8_000


def get_cik(ticker: str):
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


def get_latest_10k_accession(cik: str):
    try:
        resp = requests.get(f"{EDGAR_BASE}/submissions/CIK{cik}.json", headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None, None, f"submissions API returned {resp.status_code}"
        data   = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        forms  = recent.get("form", [])
        accnos = recent.get("accessionNumber", [])
        dates  = recent.get("filingDate", [])
        for i, form in enumerate(forms):
            if form in ("10-K", "10-K/A"):
                return accnos[i], dates[i], None
        for file_entry in data.get("filings", {}).get("files", []):
            fname    = file_entry.get("name", "")
            sub_resp = requests.get(f"{EDGAR_BASE}/submissions/{fname}", headers=HEADERS, timeout=10)
            if sub_resp.status_code == 200:
                sub = sub_resp.json()
                for i, form in enumerate(sub.get("form", [])):
                    if form in ("10-K", "10-K/A"):
                        return sub["accessionNumber"][i], sub["filingDate"][i], None
        return None, None, "No 10-K found in submissions"
    except Exception as e:
        return None, None, str(e)


def resolve_doc_url(raw_href: str, doc_base: str) -> str:
    """
    Convert a raw href from the EDGAR index page into a full document URL.
    Key fix: strip the /ix?doc= inline XBRL viewer prefix that EDGAR wraps iXBRL docs in.
    """
    # Strip inline XBRL viewer wrapper: /ix?doc=/Archives/... -> /Archives/...
    href = re.sub(r'^/ix\?doc=', '', raw_href)

    if href.startswith("http"):
        return href
    elif href.startswith("/"):
        return SEC_BASE + href
    else:
        return doc_base + href


def get_10k_document_url(cik: str, accession_dashed: str):
    """
    Returns (doc_url, index_url, debug_info).
    """
    cik_int          = str(int(cik))
    accession_nodash = accession_dashed.replace("-", "")
    doc_base         = f"{SEC_BASE}/Archives/edgar/data/{cik_int}/{accession_nodash}/"
    debug            = []

    candidate_index_urls = [
        f"{SEC_BASE}/Archives/edgar/data/{cik_int}/{accession_dashed}-index.html",
        f"{SEC_BASE}/Archives/edgar/data/{cik_int}/{accession_dashed}-index.htm",
        f"{SEC_BASE}/Archives/edgar/data/{cik_int}/{accession_nodash}/{accession_dashed}-index.html",
        f"{SEC_BASE}/Archives/edgar/data/{cik_int}/{accession_nodash}/{accession_dashed}-index.htm",
    ]

    index_text    = None
    index_url_hit = None

    for idx_url in candidate_index_urls:
        resp = requests.get(idx_url, headers=HEADERS, timeout=10)
        debug.append(f"{idx_url} -> {resp.status_code}")
        if resp.status_code == 200:
            index_text    = resp.text
            index_url_hit = idx_url
            break

    if not index_text:
        return None, candidate_index_urls[0], " | ".join(debug)

    # Strategy 1: find table row where Type column = 10-K, grab its document href
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', index_text, re.IGNORECASE | re.DOTALL)
    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.IGNORECASE | re.DOTALL)
        if len(cells) >= 4:
            cell_text = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
            if cell_text[3] in ("10-K", "10-K/A"):
                doc_match = re.search(r'href="([^"]+\.htm[l]?)"', cells[2], re.IGNORECASE)
                if doc_match:
                    raw_href = doc_match.group(1)
                    doc_url  = resolve_doc_url(raw_href, doc_base)
                    debug.append(f"Strategy 1: raw={raw_href} resolved={doc_url}")
                    return doc_url, index_url_hit, " | ".join(debug)

    # Strategy 2: any .htm link that isn't an exhibit or data file
    debug.append("Strategy 1 failed — trying strategy 2")
    all_links = re.findall(r'href="([^"]+\.htm[l]?)"', index_text, re.IGNORECASE)
    debug.append(f"Links found: {all_links[:8]}")
    exclude = re.compile(r'(xex|ex-|exhibit|_htm\.xml|\.xsd|_cal\.|_def\.|_lab\.|_pre\.)', re.IGNORECASE)
    for link in all_links:
        if not exclude.search(link):
            doc_url = resolve_doc_url(link, doc_base)
            debug.append(f"Strategy 2 selected: {link} -> {doc_url}")
            return doc_url, index_url_hit, " | ".join(debug)

    debug.append(f"All strategies failed. HTML snippet: {index_text[:300]}")
    return None, index_url_hit, " | ".join(debug)


def extract_sections(doc_url: str) -> dict:
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
    cik, err = get_cik(ticker)
    if not cik:
        return {"sections": {}, "filing_url": None, "error": f"CIK lookup failed: {err}"}

    accession, filing_date, err = get_latest_10k_accession(cik)
    if not accession:
        return {"sections": {}, "filing_url": None, "error": f"10-K accession lookup failed: {err}"}

    doc_url, index_url, debug = get_10k_document_url(cik, accession)
    if not doc_url:
        return {"sections": {}, "filing_url": index_url,
                "error": f"Document URL resolution failed. Debug: {debug}"}

    sections = extract_sections(doc_url)
    if not sections:
        return {"sections": {}, "filing_url": index_url,
                "error": f"Fetched doc ({doc_url}) but could not extract sections."}

    return {
        "sections":    sections,
        "filing_url":  index_url,
        "doc_url":     doc_url,
        "filing_date": filing_date,
        "error":       None,
    }
