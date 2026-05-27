"""
plaid_data.py — Plaid data fetching for Voskuil FP.

Provides balance and transaction data from connected Plaid accounts.
Holdings are NOT available from Plaid for Morgan Stanley — those stay CSV-only.

Imported by app.py. All functions return None gracefully if no token is connected.
"""
import requests
import streamlit as st
from datetime import date, timedelta


def _plaid_cfg():
    """Return (base_url, client_id, secret) from Streamlit secrets."""
    env    = st.secrets.get("PLAID_ENV", "sandbox")
    cid    = st.secrets.get("PLAID_CLIENT_ID", "")
    secret = (
        st.secrets.get("PLAID_SECRET_SANDBOX")
        if env == "sandbox"
        else st.secrets.get("PLAID_SECRET_PRODUCTION")
    )
    base = {
        "sandbox":    "https://sandbox.plaid.com",
        "production": "https://production.plaid.com",
    }.get(env, "https://sandbox.plaid.com")
    return base, cid, secret


def _plaid_post(endpoint, body):
    """Authenticated POST to Plaid API. Returns dict or None on failure."""
    try:
        base, cid, secret = _plaid_cfg()
        if not cid or not secret:
            return None
        resp = requests.post(
            f"{base}{endpoint}",
            json={"client_id": cid, "secret": secret, **body},
            timeout=15,
        )
        data = resp.json()
        if "error_code" in data:
            return None
        return data
    except Exception:
        return None


def get_plaid_token():
    """Return the stored access token from session state, or None."""
    return st.session_state.get("plaid_token")


@st.cache_data(ttl=3600)
def fetch_plaid_balances(access_token):
    """
    Fetch account balances from Plaid.
    Returns list of account dicts, or empty list.
    TTL 1 hour — balances are updated daily by Plaid anyway.
    """
    result = _plaid_post("/accounts/balance/get", {"access_token": access_token})
    if result and "accounts" in result:
        return result["accounts"]
    return []


@st.cache_data(ttl=3600)
def fetch_plaid_transactions(access_token, start_date=None, end_date=None):
    """
    Fetch transactions from Plaid for a date range.
    Defaults to Jan 1 of the current year through today (YTD).
    Returns list of transaction dicts, or empty list.
    """
    if start_date is None:
        start_date = date(date.today().year, 1, 1).isoformat()
    if end_date is None:
        end_date = date.today().isoformat()

    # Plaid transactions pagination — fetch up to 500 at a time
    all_txns = []
    offset   = 0
    while True:
        result = _plaid_post("/transactions/get", {
            "access_token": access_token,
            "start_date":   start_date,
            "end_date":     end_date,
            "options":      {"count": 500, "offset": offset},
        })
        if not result or "transactions" not in result:
            break
        batch = result["transactions"]
        all_txns.extend(batch)
        total = result.get("total_transactions", 0)
        offset += len(batch)
        if offset >= total:
            break

    return all_txns


def compute_plaid_metrics(access_token):
    """
    Compute the Power Bar and Cash Flow Monitor metrics from Plaid data.

    Returns dict with keys:
      total_val           — sum of all account current balances
      ytd_dividends       — sum of dividend transactions YTD
      ytd_interest        — sum of interest transactions YTD
      taxable_gain_total  — not available from Plaid (returns 0.0)
      ira_gain_total      — not available from Plaid (returns 0.0)
      accounts            — raw account list for display
      plaid_available     — True (so app knows Plaid data was used)
    """
    # ── Balances ────────────────────────────────────────────────────────────
    accounts  = fetch_plaid_balances(access_token)
    total_val = sum(
        acct.get("balances", {}).get("current") or 0
        for acct in accounts
    )

    # ── Transactions ────────────────────────────────────────────────────────
    transactions = fetch_plaid_transactions(access_token)

    ytd_dividends = 0.0
    ytd_interest  = 0.0

    for txn in transactions:
        name       = (txn.get("name") or "").lower()
        amount     = txn.get("amount") or 0   # Plaid: positive = debit, negative = credit
        categories = [c.lower() for c in (txn.get("category") or [])]

        # Plaid signs: credits (money coming in) are negative amounts
        # Dividends and interest are inflows → negative in Plaid convention
        inflow = -amount if amount < 0 else 0

        is_dividend = (
            "dividend" in name
            or "div " in name
            or "dividends" in categories
        )
        is_interest = (
            "interest" in name
            or "int " in name
            or "bank fees" in categories   # MS uses this category for interest sometimes
        )

        if is_dividend and inflow > 0:
            ytd_dividends += inflow
        elif is_interest and inflow > 0:
            ytd_interest  += inflow

    return {
        "total_val":          total_val,
        "ytd_dividends":      ytd_dividends,
        "ytd_interest":       ytd_interest,
        "taxable_gain_total": 0.0,   # not available via Plaid for MS
        "ira_gain_total":     0.0,   # not available via Plaid for MS
        "accounts":           accounts,
        "plaid_available":    True,
    }


def plaid_status_badge():
    """Return a small status string for display in the sidebar or header."""
    token = get_plaid_token()
    if token:
        env   = st.secrets.get("PLAID_ENV", "sandbox")
        label = "Sandbox" if env == "sandbox" else "Live"
        return f"🔗 Plaid {label} connected"
    return "📂 CSV data only"

