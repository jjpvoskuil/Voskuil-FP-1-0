"""
ms_download_cloud.py — Morgan Stanley Downloader (GitHub Actions / Cloud)
=========================================================================
Headless version of ms_download.py designed to run in GitHub Actions.
Triggered via the ms_refresh.yml workflow.

NOTE ON MFA:
  MS Online requires MFA. This script handles the login flow up to the
  MFA prompt, then waits. In GitHub Actions, MFA is the remaining
  manual step — the workflow will pause at that point.

  Future improvement: Switch to Yodlee/Finicity API to eliminate MFA
  entirely. See punch list item for details.

ENVIRONMENT VARIABLES (set as GitHub Secrets):
  GITHUB_TOKEN  — repo token (auto-provided by Actions)
  GITHUB_REPO   — e.g. jjpvoskuil/Voskuil-FP-1-0 (auto-provided)
  MS_USERNAME   — your MS Online username/email
  MS_PASSWORD   — your MS Online password
"""

import os
import time
import base64
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = os.environ.get("GITHUB_REPO",  "jjpvoskuil/Voskuil-FP-1-0")
MS_USERNAME  = os.environ.get("MS_USERNAME",  "")
MS_PASSWORD  = os.environ.get("MS_PASSWORD",  "")

URL_LOGIN        = "https://login.morganstanleyclientserv.com/ux/#/home"
URL_HOLDINGS     = "https://mso.morganstanleyclientserv.com/atrium/#/accounts/holdings?referer=mso-menu"
URL_TRANSACTIONS = "https://mso.morganstanleyclientserv.com/atrium/#/accounts/activity"
URL_RGL          = "https://mso.morganstanleyclientserv.com/atrium/#/accounts/rgl/details?period=0&showChart=true"

DOWNLOAD_TIMEOUT = 60
PAGE_LOAD_WAIT   = 5

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def push_to_github(content_bytes: bytes, repo_filename: str) -> bool:
    """Push file contents directly to GitHub repo."""
    if not GITHUB_TOKEN:
        print(f"  ⚠️  No GITHUB_TOKEN — skipping {repo_filename}")
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

    payload = {
        "message": f"Auto-refresh {repo_filename} — {datetime.today().strftime('%Y-%m-%d %H:%M')}",
        "content": base64.b64encode(content_bytes).decode(),
    }
    if sha:
        payload["sha"] = sha

    r = requests.put(api_url, headers=headers, json=payload, timeout=30)
    if r.status_code in (200, 201):
        print(f"  ✅  Pushed {repo_filename} to GitHub")
        return True
    else:
        print(f"  ❌  Push failed for {repo_filename}: {r.status_code}")
        return False


def xlsx_bytes_to_csv_bytes(xlsx_path: Path) -> bytes:
    """Convert xlsx file to csv bytes."""
    df = pd.read_excel(xlsx_path, header=None)
    return df.to_csv(index=False, header=False).encode()


def wait_for_download(page, timeout: int = DOWNLOAD_TIMEOUT):
    """Wait for a download to complete and return the path."""
    try:
        with page.expect_download(timeout=timeout * 1000) as download_info:
            yield
        download = download_info.value
        tmp_path = Path(tempfile.mktemp(suffix=".xlsx"))
        download.save_as(str(tmp_path))
        return tmp_path
    except Exception as e:
        print(f"  ❌  Download failed: {e}")
        return None


def click_download_button(page, description: str) -> bool:
    """Find and click the MS Online Download button."""
    print(f"  🖱️  Clicking Download for {description}...")
    selectors = [
        "a:has-text('Download')",
        "span:has-text('Download')",
        "button:has-text('Download')",
        "[aria-label*='Download']",
        "a:has-text('Export')",
        "button:has-text('Export')",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=3000):
                btn.click()
                print(f"  ✅  Clicked: {sel}")
                return True
        except Exception:
            continue
    print(f"  ❌  Could not find Download button for {description}")
    return False


def select_rgl_period(page, label: str) -> bool:
    """Select a period from the Realized G/L dropdown."""
    print(f"  🔽  Selecting period: {label}")
    try:
        page.select_option("select", label=label)
        time.sleep(PAGE_LOAD_WAIT)
        print(f"  ✅  Selected: {label}")
        return True
    except Exception:
        pass

    for sel in [f"[role='option']:has-text('{label}')",
                f"li:has-text('{label}')",
                f"button:has-text('{label}')"]:
        try:
            opt = page.locator(sel).first
            if opt.is_visible(timeout=3000):
                opt.click()
                time.sleep(PAGE_LOAD_WAIT)
                print(f"  ✅  Selected: {label}")
                return True
        except Exception:
            continue

    print(f"  ❌  Could not select period '{label}'")
    return False


def download_and_push(page, description: str, repo_filename: str) -> bool:
    """Click download, capture the file, convert to CSV, push to GitHub."""
    try:
        with page.expect_download(timeout=DOWNLOAD_TIMEOUT * 1000) as dl_info:
            click_download_button(page, description)
        download = dl_info.value
        tmp_path = Path(tempfile.mktemp(suffix=".xlsx"))
        download.save_as(str(tmp_path))
        print(f"  📥  Downloaded: {tmp_path.name} ({tmp_path.stat().st_size} bytes)")
        csv_bytes = xlsx_bytes_to_csv_bytes(tmp_path)
        result = push_to_github(csv_bytes, repo_filename)
        tmp_path.unlink(missing_ok=True)
        return result
    except Exception as e:
        print(f"  ❌  Failed {description}: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    results = {}

    print("\n" + "═" * 60)
    print("  Voskuil FP — MS Data Refresh (Cloud)")
    print("═" * 60)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            accept_downloads=True,
            viewport={"width": 1400, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()

        # ── Login ────────────────────────────────────────────────────────────
        print("\nSTEP 1 — Login")
        page.goto(URL_LOGIN, timeout=30000)
        time.sleep(3)

        # Enter username
        try:
            page.fill("input[type='email'], input[name='username'], input[id*='user'], input[placeholder*='user'], input[placeholder*='email']", MS_USERNAME)
            time.sleep(1)
        except Exception:
            print("  ⚠️  Could not fill username field")

        # Enter password
        try:
            page.fill("input[type='password']", MS_PASSWORD)
            time.sleep(1)
        except Exception:
            print("  ⚠️  Could not fill password field")

        # Click login button
        for sel in ["button[type='submit']", "button:has-text('Log In')", "button:has-text('Sign In')", "input[type='submit']"]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2000):
                    btn.click()
                    print(f"  ✅  Clicked login button")
                    break
            except Exception:
                continue

        # Wait for post-login navigation
        print("  ⏳  Waiting for login + MFA...")
        try:
            page.wait_for_url("**/atrium/**", timeout=120000)
            print("  ✅  Login successful\n")
        except PWTimeout:
            print("  ❌  Login timeout — MFA may have blocked automated login")
            print("  💡  Tip: Use the local ms_download.py script for MFA-required runs")
            browser.close()
            return

        time.sleep(PAGE_LOAD_WAIT)

        # ── Holdings ─────────────────────────────────────────────────────────
        print("STEP 2 — Holdings")
        page.goto(URL_HOLDINGS, timeout=30000)
        time.sleep(PAGE_LOAD_WAIT)
        results["ms_holdings.csv"] = download_and_push(page, "Holdings", "ms_holdings.csv")
        print()

        # ── Transactions ─────────────────────────────────────────────────────
        print("STEP 3 — Transactions (rolling 12 months)")
        page.goto(URL_TRANSACTIONS, timeout=30000)
        time.sleep(PAGE_LOAD_WAIT)

        # Set date range
        today      = datetime.today()
        start_date = today - timedelta(days=365)
        try:
            date_fields = page.locator("input[type='date'], input[placeholder*='date'], input[placeholder*='From']")
            if date_fields.first.is_visible(timeout=3000):
                date_fields.first.fill(start_date.strftime("%m/%d/%Y"))
                time.sleep(1)
                for apply_sel in ["button:has-text('Apply')", "button:has-text('Search')", "button[type='submit']"]:
                    try:
                        btn = page.locator(apply_sel).first
                        if btn.is_visible(timeout=2000):
                            btn.click()
                            time.sleep(PAGE_LOAD_WAIT)
                            break
                    except Exception:
                        continue
        except Exception:
            print("  ⚠️  Could not set date range — using default")

        results["ms_transactions_12m.csv"] = download_and_push(page, "Transactions", "ms_transactions_12m.csv")
        print()

        # ── Realized G/L Current Year ─────────────────────────────────────
        print("STEP 4 — Realized G/L (Current Year)")
        page.goto(URL_RGL, timeout=30000)
        time.sleep(PAGE_LOAD_WAIT)
        select_rgl_period(page, "Current Year")
        results["ms_realized_gl_current.csv"] = download_and_push(page, "Realized GL Current", "ms_realized_gl_current.csv")
        print()

        # ── Realized G/L Prior Year ───────────────────────────────────────
        print("STEP 5 — Realized G/L (Prior Year)")
        select_rgl_period(page, "Prior Year")
        results["ms_realized_gl_prior.csv"] = download_and_push(page, "Realized GL Prior", "ms_realized_gl_prior.csv")
        print()

        browser.close()

    # ── Summary ──────────────────────────────────────────────────────────────
    print("═" * 60)
    print("  SUMMARY")
    print("═" * 60)
    all_ok = True
    for filename, ok in results.items():
        status = "✅  Pushed" if ok else "❌  Failed"
        print(f"  {status} — {filename}")
        if not ok:
            all_ok = False
    print("═" * 60)

    if not all_ok:
        exit(1)


if __name__ == "__main__":
    main()

