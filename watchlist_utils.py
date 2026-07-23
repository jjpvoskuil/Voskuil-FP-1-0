"""
watchlist_utils.py — persistent Watchlist store + watch-portfolio economics (#68).

Scope (expanded from the original punch-list item during scoping, July 2026):
  1. A ticker can be tagged onto the Watchlist from Dashboard (holdings),
     Equity Scout, Market Screener, or Compare Stocks. Tagging is add-only
     from those pages — removal only happens on the dedicated Watchlist
     page itself (deliberate: an accidental uncheck on a scan page should
     never silently delete a tracked position's transaction history).
  2. On the Watchlist page, any watchlisted ticker can be tagged into a
     "watch portfolio" and have hypothetical Buy/Sell $ transactions
     recorded against it — a paper-trading ledger, not real money.
  3. Watch-portfolio performance and a date-range comparison against the
     real Dashboard holdings both use the SAME money-weighted (XIRR) +
     simple-return methodology (see period_return()), so the comparison is
     apples-to-apples rather than two different calculations that happen
     to produce percentages.

Persistence: GitHub-backed via github_store.py (same Contents-API,
SHA-checked pattern as the punch list and Market Screener's scan cache) —
Streamlit Community Cloud wipes local disk on every reboot/redeploy, so
watchlist_data.json has to live in the repo, not on disk.
"""

import time
import uuid
from datetime import datetime, date, timedelta

import pandas as pd
import streamlit as st

from github_store import github_get_json, github_put_json

WATCHLIST_FILE = "watchlist_data.json"

# Mirrors the punch list's periodic remote-SHA re-check: best-effort
# staleness guard against two sessions/tabs editing the watchlist at once.
# There's no server-side locking on Streamlit Cloud, so this can't be made
# airtight — it just narrows the window.
_STALE_CHECK_INTERVAL = 30  # seconds

HOLDINGS_FILE     = "ms_holdings.csv"
TRANS_FILE_YTD    = "ms_transactions_ytd.csv"
TRANS_FILE_PRIOR  = "ms_transactions_prior.csv"


# ─────────────────────────────────────────────
# PERSISTENCE
# ─────────────────────────────────────────────

def _empty_watchlist():
    return {"items": {}}


def load_watchlist(force: bool = False):
    """Session-cached load from GitHub, re-checked periodically. Returns
    the watchlist dict (never None — falls back to an empty structure on
    error so callers don't need to guard every access)."""
    now = time.time()
    last_check = st.session_state.get("_wl_last_check", 0)
    if force or "_wl_data" not in st.session_state or (now - last_check) > _STALE_CHECK_INTERVAL:
        data, sha, err = github_get_json(WATCHLIST_FILE)
        st.session_state["_wl_last_check"] = now
        if err:
            # Transient GET failure -- keep whatever's already in session
            # rather than wiping the in-memory watchlist out from under the
            # user mid-session.
            st.session_state.setdefault("_wl_data", _empty_watchlist())
            st.session_state["_wl_load_error"] = err
        else:
            st.session_state["_wl_data"] = data if data else _empty_watchlist()
            st.session_state["_wl_load_error"] = None
    return st.session_state["_wl_data"]


def save_watchlist(data, commit_message: str):
    ok, msg = github_put_json(WATCHLIST_FILE, data, commit_message)
    if ok:
        st.session_state["_wl_data"] = data
        st.session_state["_wl_last_check"] = time.time()
    return ok, msg


# ─────────────────────────────────────────────
# WATCHLIST MEMBERSHIP
# ─────────────────────────────────────────────

def is_watchlisted(ticker: str) -> bool:
    return ticker in load_watchlist().get("items", {})


def add_to_watchlist(ticker: str, name: str = "", source: str = "",
                      starting_shares: float = None, starting_value: float = None):
    """
    Idempotent — safe to call on every rerun of a source-page checkbox.
    Only actually writes to GitHub the first time a ticker is added.

    starting_shares / starting_value: if both are positive, seeds the watch
    portfolio with a same-day "buy" sized to an actual real holding (used
    by Dashboard, per owner request July 2026) -- represents "I already
    hold this much in real life," so the paper portfolio starts from where
    the real one already is rather than from $0. Dated today, since MS's
    transaction export doesn't reliably go back to the real original
    purchase date -- this is a starting point, not a backdated re-creation
    of history. Fully editable afterward on the Watchlist page (delete the
    seed transaction, record a different Buy). Implicitly tags the ticker
    into the watch portfolio, same as any other buy.
    """
    ticker = ticker.upper().strip()
    data = load_watchlist()
    if ticker in data["items"]:
        return True, "Already on watchlist"
    item = {
        "ticker": ticker,
        "name": name or ticker,
        "source": source,
        "added_date": date.today().isoformat(),
        "in_watch_portfolio": False,
        "notes": "",
        "transactions": [],
    }
    commit_message = f"Watchlist: add {ticker} (via {source or 'manual'})"
    if starting_shares and starting_value and starting_shares > 0 and starting_value > 0:
        price = starting_value / starting_shares
        item["in_watch_portfolio"] = True
        item["transactions"].append({
            "id": uuid.uuid4().hex[:8],
            "date": date.today().isoformat(),
            "action": "buy",
            "shares": round(float(starting_shares), 6),
            "price": round(float(price), 4),
            "amount": round(float(starting_value), 2),
            "note": "Starting position — seeded from real holding",
        })
        commit_message = (f"Watchlist: add {ticker} with starting position "
                           f"${starting_value:,.2f} (via {source or 'manual'})")
    data["items"][ticker] = item
    return save_watchlist(data, commit_message)


def remove_from_watchlist(ticker: str):
    """Only ever called from the Watchlist page itself — see module
    docstring. Deletes the ticker's transaction history along with it."""
    data = load_watchlist()
    if ticker in data["items"]:
        del data["items"][ticker]
        return save_watchlist(data, f"Watchlist: remove {ticker}")
    return True, "Not on watchlist"


def set_in_watch_portfolio(ticker: str, flag: bool):
    data = load_watchlist()
    if ticker not in data["items"]:
        return False, "Ticker not on watchlist"
    data["items"][ticker]["in_watch_portfolio"] = flag
    return save_watchlist(data, f"Watchlist: {'tag' if flag else 'untag'} {ticker} for watch portfolio")


def update_notes(ticker: str, notes: str):
    data = load_watchlist()
    if ticker not in data["items"]:
        return False, "Ticker not on watchlist"
    data["items"][ticker]["notes"] = notes
    return save_watchlist(data, f"Watchlist: update notes for {ticker}")


# ─────────────────────────────────────────────
# BUY / SELL LEDGER
# ─────────────────────────────────────────────

def record_transaction(ticker: str, action: str, shares: float, price: float, tx_date: str = None):
    """action: 'buy' or 'sell'. A Buy implicitly tags the ticker into the
    watch portfolio (that's the whole point of buying it)."""
    assert action in ("buy", "sell")
    data = load_watchlist()
    if ticker not in data["items"]:
        return False, "Ticker not on watchlist"
    tx_date = tx_date or date.today().isoformat()
    item = data["items"][ticker]
    if action == "sell":
        held = position_summary(item, price)["shares_held"]
        if shares > held + 1e-6:
            return False, f"Can't sell {shares:.4g} shares — only {held:.4g} held."
    item["transactions"].append({
        "id": uuid.uuid4().hex[:8],
        "date": tx_date,
        "action": action,
        "shares": round(float(shares), 6),
        "price": round(float(price), 4),
        "amount": round(float(shares) * float(price), 2),
    })
    if action == "buy":
        item["in_watch_portfolio"] = True
    return save_watchlist(data, f"Watchlist: {action} {shares:.4g} {ticker} @ ${price:,.2f}")


def delete_transaction(ticker: str, tx_id: str):
    data = load_watchlist()
    item = data["items"].get(ticker)
    if not item:
        return False, "Ticker not on watchlist"
    before = len(item["transactions"])
    item["transactions"] = [t for t in item["transactions"] if t["id"] != tx_id]
    if len(item["transactions"]) == before:
        return False, "Transaction not found"
    return save_watchlist(data, f"Watchlist: delete transaction on {ticker}")


def position_summary(item: dict, current_price):
    """
    Average-cost-basis summary of a ticker's transaction ledger (average
    cost, not FIFO/LIFO lots — simplest defensible method for a paper
    portfolio, matches how most brokerages show "unrealized gain" by
    default).
    """
    shares_held = 0.0
    cost_basis  = 0.0
    realized_gain = 0.0
    total_invested = 0.0
    total_proceeds = 0.0
    for tx in sorted(item.get("transactions", []), key=lambda t: (t["date"], t.get("id", ""))):
        if tx["action"] == "buy":
            shares_held    += tx["shares"]
            cost_basis     += tx["amount"]
            total_invested += tx["amount"]
        else:
            if shares_held > 1e-9:
                avg_cost = cost_basis / shares_held
                sold_shares = min(tx["shares"], shares_held)
                cost_removed = avg_cost * sold_shares
                realized_gain += tx["amount"] - cost_removed
                cost_basis    -= cost_removed
            shares_held     = max(0.0, shares_held - tx["shares"])
            total_proceeds += tx["amount"]
    avg_cost = (cost_basis / shares_held) if shares_held > 1e-9 else None
    market_value = (shares_held * current_price) if (current_price and shares_held > 1e-9) else (0.0 if shares_held <= 1e-9 else None)
    unrealized_gain = (market_value - cost_basis) if market_value is not None else None
    return {
        "shares_held": shares_held,
        "cost_basis": cost_basis,
        "avg_cost": avg_cost,
        "market_value": market_value,
        "realized_gain": realized_gain,
        "unrealized_gain": unrealized_gain,
        "total_invested": total_invested,
        "total_proceeds": total_proceeds,
    }


# ─────────────────────────────────────────────
# PRICING (yfinance)
# ─────────────────────────────────────────────

@st.cache_data(ttl=3600)
def fetch_historical_price(ticker: str, on_date: str):
    """Nearest yfinance close on or before `on_date` (ISO string). Looks
    back up to 8 calendar days to cover weekends/holidays. Returns
    (price, actual_date_used_iso) or (None, None)."""
    try:
        import yfinance as yf
        target = datetime.fromisoformat(on_date).date()
        start = (target - timedelta(days=8)).isoformat()
        end   = (target + timedelta(days=1)).isoformat()
        hist = yf.Ticker(ticker).history(start=start, end=end)
        if hist.empty:
            return None, None
        hist = hist[hist.index.date <= target]
        if hist.empty:
            return None, None
        return float(hist.iloc[-1]["Close"]), hist.index[-1].date().isoformat()
    except Exception:
        return None, None


@st.cache_data(ttl=3600)
def fetch_price_series(ticker: str, start_date: str, end_date: str):
    """Daily closes for charting, [start_date, end_date] inclusive."""
    try:
        import yfinance as yf
        end_plus = (datetime.fromisoformat(end_date).date() + timedelta(days=1)).isoformat()
        hist = yf.Ticker(ticker).history(start=start_date, end=end_plus)
        if hist.empty:
            return pd.DataFrame()
        out = hist[["Close"]].reset_index()
        out.columns = ["date", "close"]
        out["date"] = out["date"].dt.date
        return out
    except Exception:
        return pd.DataFrame()


# ─────────────────────────────────────────────
# MONEY-WEIGHTED RETURN (XIRR)
# ─────────────────────────────────────────────

def xirr(cashflows):
    """
    cashflows: list of (date, amount) — date a datetime.date or ISO string,
    amount negative for money out (buys / period-starting value), positive
    for money in (sells / period-ending value). Solves for the annualized
    rate r such that sum(cf / (1+r)^(days/365)) == 0 — same concept as
    Excel/Sheets XIRR(). Newton's method with a bisection fallback.
    Returns None if it can't converge (e.g. no sign change in cashflows).
    """
    parsed = []
    for d, amt in cashflows:
        if isinstance(d, str):
            d = datetime.fromisoformat(d).date()
        parsed.append((d, float(amt)))
    if len(parsed) < 2:
        return None
    if not (any(a > 0 for _, a in parsed) and any(a < 0 for _, a in parsed)):
        return None
    t0 = min(d for d, _ in parsed)

    def npv(rate):
        return sum(a / (1 + rate) ** (((d - t0).days) / 365.0) for d, a in parsed)

    def dnpv(rate):
        return sum(-(((d - t0).days) / 365.0) * a / (1 + rate) ** ((((d - t0).days) / 365.0) + 1)
                   for d, a in parsed)

    rate = 0.1
    for _ in range(100):
        try:
            f, fp = npv(rate), dnpv(rate)
        except (OverflowError, ZeroDivisionError):
            break
        if abs(fp) < 1e-12:
            break
        new_rate = rate - f / fp
        if new_rate <= -0.9999:
            new_rate = (rate - 0.9999) / 2
        if abs(new_rate - rate) < 1e-7:
            return new_rate
        rate = new_rate

    # Newton didn't converge cleanly — bisection fallback over a wide,
    # sane range rather than trusting a possibly-diverged result.
    lo, hi = -0.9999, 10.0
    try:
        f_lo, f_hi = npv(lo), npv(hi)
    except (OverflowError, ZeroDivisionError):
        return None
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-6:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def period_return(transactions, start_date, end_date, begin_value, end_value):
    """
    Money-weighted (XIRR, annualized) + simple total return over
    (start_date, end_date] for a basket, given its begin/end market values
    and the transactions that fell strictly inside the window.

    Convention (standard portfolio-XIRR treatment): begin_value is a cash
    outflow (investment) at start_date, end_value a cash inflow
    (liquidation) at end_date. Buys inside the window are additional
    outflows on their real dates; sells are inflows on theirs. This is the
    SAME function used for both the watch portfolio and the reconstructed
    holdings basket, so the two "returns" being compared are computed the
    same way rather than two different methodologies that happen to both
    be percentages.

    transactions: list of dicts with 'date' (ISO str or date), 'action'
    ('buy'/'sell'), 'amount' (positive $ magnitude).
    """
    cashflows = []
    if begin_value:
        cashflows.append((start_date, -begin_value))

    net_contrib = 0.0  # net $ put in during the window (buys - sells)
    for tx in transactions:
        d = tx["date"]
        if isinstance(d, str):
            d = datetime.fromisoformat(d).date()
        if not (start_date < d <= end_date):
            continue
        amt = float(tx["amount"])
        if tx["action"] == "buy":
            cashflows.append((d, -amt))
            net_contrib += amt
        else:
            cashflows.append((d, amt))
            net_contrib -= amt

    if end_value is not None:
        cashflows.append((end_date, end_value))

    r = xirr(cashflows) if len(cashflows) >= 2 else None

    # Simple (non-annualized) Dietz-style total return, as a secondary,
    # easier-to-sanity-check figure alongside XIRR.
    simple_return = None
    if begin_value is not None and end_value is not None:
        basis = (begin_value or 0) + max(net_contrib, 0)
        if basis > 1e-9:
            gain = end_value - begin_value - net_contrib
            simple_return = gain / basis

    return {
        "xirr": r,
        "simple_return": simple_return,
        "net_contributions": net_contrib,
        "begin_value": begin_value,
        "end_value": end_value,
    }


# ─────────────────────────────────────────────
# WATCH-PORTFOLIO DATE-RANGE RETURN
# ─────────────────────────────────────────────

def watch_portfolio_period_return(watchlist_data, start_date, end_date, current_prices: dict):
    """
    Aggregates every tagged ('in_watch_portfolio') ticker's transactions
    into one basket and computes its period_return() over [start_date,
    end_date]. current_prices: {ticker: live price}, used when end_date is
    today; historical closes are fetched for other tickers/dates.
    """
    all_txs = []
    begin_total = 0.0
    end_total   = 0.0
    today = date.today()

    for ticker, item in watchlist_data.get("items", {}).items():
        if not item.get("in_watch_portfolio"):
            continue
        txs = item.get("transactions", [])
        if not txs:
            continue
        all_txs.extend({**t, "ticker": ticker} for t in txs)

        def shares_as_of(cutoff, _txs=txs):
            s = 0.0
            for t in sorted(_txs, key=lambda x: x["date"]):
                d = datetime.fromisoformat(t["date"]).date()
                if d > cutoff:
                    break
                s += t["shares"] if t["action"] == "buy" else -t["shares"]
            return max(0.0, s)

        shares_start = shares_as_of(start_date)
        if shares_start > 1e-9:
            price_start, _ = fetch_historical_price(ticker, start_date.isoformat())
            if price_start:
                begin_total += shares_start * price_start

        shares_end = shares_as_of(end_date)
        if shares_end > 1e-9:
            if end_date >= today and ticker in current_prices and current_prices[ticker]:
                end_total += shares_end * current_prices[ticker]
            else:
                price_end, _ = fetch_historical_price(ticker, end_date.isoformat())
                if price_end:
                    end_total += shares_end * price_end

    return period_return(all_txs, start_date, end_date, begin_total, end_total)


# ─────────────────────────────────────────────
# MS HOLDINGS RECONSTRUCTION (for the comparison view)
# ─────────────────────────────────────────────

def _get_clean_df(filename, anchor_text):
    """Same header-row-detection pattern as Dashboard's get_clean_df()."""
    try:
        with open(filename, "r") as f:
            lines = f.readlines()
        header_idx = next(i for i, line in enumerate(lines) if anchor_text in line)
        return pd.read_csv(filename, skiprows=header_idx)
    except Exception:
        return None


def load_ms_holdings_and_transactions():
    """
    Loads + lightly cleans the MS holdings and transaction CSVs for the
    holdings-comparison view. Returns (holdings_df, trans_df, error).
    trans_df combines YTD + prior year, columns: Symbol, date (python
    date), action ('buy'/'sell'), shares, amount.
    """
    holdings_df = _get_clean_df(HOLDINGS_FILE, "Account Number")
    if holdings_df is None:
        return None, None, "Could not load ms_holdings.csv"
    holdings_df.columns = [c.strip() for c in holdings_df.columns]
    holdings_df = holdings_df[~holdings_df.iloc[:, 0].astype(str).str.contains("Total", case=False, na=False)]
    for col in ["Quantity", "Market Value ($)"]:
        if col in holdings_df.columns:
            holdings_df[col] = pd.to_numeric(
                holdings_df[col].astype(str).str.replace(",", "").str.replace('"', ""), errors="coerce"
            )
    holdings_df = holdings_df.dropna(subset=["Symbol", "Quantity"])

    trans_frames = []
    for fname in (TRANS_FILE_YTD, TRANS_FILE_PRIOR):
        t = _get_clean_df(fname, "Activity Date")
        if t is None:
            continue
        t.columns = [c.strip() for c in t.columns]
        t = t[t["Activity"].isin(["Bought", "Sold"])].copy()
        if t.empty:
            continue
        t["Quantity"] = pd.to_numeric(t["Quantity"].astype(str).str.replace(",", ""), errors="coerce")
        t["Amount($)"] = pd.to_numeric(
            t["Amount($)"].astype(str).str.replace(",", "").str.replace('"', ""), errors="coerce"
        )
        t["_date"] = pd.to_datetime(t["Activity Date"], errors="coerce").dt.date
        t["_action"] = t["Activity"].map({"Bought": "buy", "Sold": "sell"})
        t = t.dropna(subset=["Symbol", "Quantity", "Amount($)", "_date"])
        trans_frames.append(t[["Symbol", "_date", "_action", "Quantity", "Amount($)"]])

    if trans_frames:
        trans_df = pd.concat(trans_frames, ignore_index=True)
        trans_df.columns = ["Symbol", "date", "action", "shares", "amount"]
        trans_df["amount"] = trans_df["amount"].abs()
    else:
        trans_df = pd.DataFrame(columns=["Symbol", "date", "action", "shares", "amount"])

    return holdings_df, trans_df, None


def holdings_basket_period_return(holdings_df, trans_df, start_date, end_date):
    """
    Reconstructs the current MS holdings basket's value at start_date and
    end_date and hands it to period_return() — the SAME function the watch
    portfolio uses. This is a best-effort reconstruction, not audited
    accounting: it starts from today's known share counts and walks
    backward using the Bought/Sold transaction log, so it's only as
    complete as that log is (MS only exports current + prior year
    activity — see the coverage note returned).
    """
    if holdings_df is None or holdings_df.empty:
        return None, "No holdings data available."

    by_symbol = holdings_df.groupby("Symbol").agg(
        shares=("Quantity", "sum"), market_value=("Market Value ($)", "sum")
    ).reset_index()
    total_current_value = by_symbol["market_value"].sum()

    today = date.today()
    begin_total, end_total = 0.0, 0.0
    covered_value = 0.0
    skipped = []
    window_txs = []
    earliest_tx_date = trans_df["date"].min() if trans_df is not None and not trans_df.empty else None

    for _, r in by_symbol.iterrows():
        sym, cur_shares, mkt_val = r["Symbol"], r["shares"], r["market_value"]
        sym_txs = trans_df[trans_df["Symbol"] == sym].to_dict("records") if trans_df is not None and not trans_df.empty else []

        def shares_before(cutoff, _sym_txs=sym_txs, _cur_shares=cur_shares):
            delta_after = sum(
                (t["shares"] if t["action"] == "buy" else -t["shares"])
                for t in _sym_txs if t["date"] > cutoff
            )
            return _cur_shares - delta_after

        shares_start = shares_before(start_date)
        price_start, _ = fetch_historical_price(sym, start_date.isoformat())
        if price_start is None:
            skipped.append(sym)
            continue
        begin_total += shares_start * price_start

        if end_date >= today:
            end_total += mkt_val
        else:
            shares_end = shares_before(end_date)
            price_end, _ = fetch_historical_price(sym, end_date.isoformat())
            if price_end is None:
                skipped.append(sym)
                continue
            end_total += shares_end * price_end

        covered_value += mkt_val
        window_txs.extend([{**t, "ticker": sym} for t in sym_txs if start_date < t["date"] <= end_date])

    coverage_pct = (covered_value / total_current_value * 100) if total_current_value else 0.0
    note = f"{len(by_symbol) - len(skipped)}/{len(by_symbol)} positions priced (~{coverage_pct:.0f}% of portfolio value)."
    if earliest_tx_date and start_date < earliest_tx_date:
        note += (f" Transaction history only goes back to {earliest_tx_date.isoformat()} — share counts "
                 f"before that are assumed unchanged from today's, which can understate or overstate the "
                 f"true starting position for a range that long.")
    if skipped:
        shown = ", ".join(skipped[:10]) + ("…" if len(skipped) > 10 else "")
        note += f" No price data for: {shown}."

    result = period_return(window_txs, start_date, end_date, begin_total, end_total)
    return result, note
