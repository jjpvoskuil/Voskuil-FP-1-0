"""
push_files.py — Convert xlsx files and push to GitHub
======================================================
Converts the 5 downloaded MS xlsx files to CSV and pushes
them to the GitHub repo so Streamlit picks them up.

Run this after ms_download.py completes successfully.
"""

import os
import base64
import requests
import pandas as pd
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = os.environ.get("GITHUB_REPO",  "jjpvoskuil/Voskuil-FP-1-0")
DOWNLOADS    = Path(r"C:\Users\John Voskuil\Downloads")

# Map: local xlsx filename → GitHub CSV filename
FILES = {
    "ms_holdings.xlsx":             "ms_holdings.csv",
    "ms_transactions_ytd.xlsx":     "ms_transactions_ytd.csv",
    "ms_transactions_prior.xlsx":   "ms_transactions_prior.csv",
    "ms_realized_gl_current.xlsx":  "ms_realized_gl_current.csv",
    "ms_realized_gl_prior.xlsx":    "ms_realized_gl_prior.csv",
}

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def push_to_github(csv_bytes: bytes, repo_filename: str) -> bool:
    if not GITHUB_TOKEN:
        print(f"  WARNING: No GITHUB_TOKEN — skipping {repo_filename}")
        return False
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{repo_filename}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github+json",
    }
    sha = None
    r = requests.get(api_url, headers=headers, timeout=15)
    if r.status_code == 200:
        sha = r.json().get("sha")
    from datetime import datetime
    payload = {
        "message": f"Auto-refresh {repo_filename} — {datetime.today().strftime('%Y-%m-%d %H:%M')}",
        "content": base64.b64encode(csv_bytes).decode(),
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(api_url, headers=headers, json=payload, timeout=30)
    if r.status_code in (200, 201):
        return True
    else:
        print(f"  ERROR: GitHub push failed {r.status_code}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*60)
    print("  Voskuil FP — Convert & Push to GitHub")
    print("="*60)
    print(f"  GitHub repo: {GITHUB_REPO}\n")

    if not GITHUB_TOKEN:
        print("  ERROR: GITHUB_TOKEN not set as environment variable")
        print("  Run: setx GITHUB_TOKEN \"your_token_here\"")
        print("  Then close and reopen Command Prompt\n")
        return

    results = {}

    for xlsx_name, csv_name in FILES.items():
        xlsx_path = DOWNLOADS / xlsx_name
        print(f"  Processing {xlsx_name}...")

        if not xlsx_path.exists():
            print(f"  SKIP — file not found: {xlsx_path}")
            results[csv_name] = False
            continue

        try:
            df = pd.read_excel(xlsx_path, header=None)
            csv_bytes = df.to_csv(index=False, header=False).encode()
            print(f"  Converted — {len(df)} rows")
        except Exception as e:
            print(f"  ERROR converting {xlsx_name}: {e}")
            results[csv_name] = False
            continue

        ok = push_to_github(csv_bytes, csv_name)
        results[csv_name] = ok
        print(f"  {'Pushed' if ok else 'FAILED'} — {csv_name}\n")

    print("="*60)
    print("  SUMMARY")
    print("="*60)
    all_ok = True
    for csv_name, ok in results.items():
        status = "OK  " if ok else "FAIL"
        print(f"  {status} — {csv_name}")
        if not ok:
            all_ok = False
    print("="*60)

    if all_ok:
        print("\n  All files pushed. Reload Streamlit to see fresh data.\n")
    else:
        print("\n  Some files failed. Check messages above.\n")


if __name__ == "__main__":
    main()
