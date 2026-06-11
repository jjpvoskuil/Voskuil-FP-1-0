"""
sec_utils.py — SEC EDGAR filing fetcher for Voskuil FP 1.0

Uses the complete submission .txt file from EDGAR — plain text, no iXBRL,
no JavaScript required. Extracts the 10-K body document then parses
Item 1, 1A, 7, 7A sections for qualitative analysis.
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
