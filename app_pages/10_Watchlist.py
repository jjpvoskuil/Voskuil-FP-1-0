"""
10_Watchlist.py — dedicated Watchlist + paper "Watch Portfolio" page (#68).

Tickers are added here (add-only) from Dashboard, Equity Scout, Market
Screener, and Compare Stocks via a shared ☆ Watchlist checkbox — see
watchlist_utils.py's module docstring for the full design rationale
(persistence, why removal only happens on this page, and why the
watch-portfolio-vs-holdings comparison reuses one return-calc function for
both baskets).

Layout (reworked July 2026, per owner request):
  1. Full Watchlist — one compact data_editor row per ticker: price, DCF
     value, margin of safety, Owner's Framework score, action rating (same
     scoring/DCF/rating pipeline as Compare Stocks — see
     watchlist_utils.get_ticker_snapshot()), optional superinvestor count,
     and two editable checkboxes (tag into Watch Portfolio / Remove).
  2. Watch Portfolio — position table with inline Buy $/Sell $ columns and
     a single Execute Trades button, replacing the old separate form.
  3. Performance vs. Holdings — date-range XIRR/return comparison, plus a
     chart overlaying both baskets' value trends, indexed to 100 at the
     start date (the two are wildly different in raw dollar size).
"""

import sys, os
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from sec_utils import fetch_price_and_market_cap, safe_float, DEFAULT_WEIGHTS
import watchlist_utils as wl

st.set_page_config(page_title="Voskuil FP 1.0", layout="wide")
st.title("⭐ Watchlist")
st.caption(
    "Tickers tagged from Dashboard, Equity Scout, Market Screener, or Compare Stocks land here. "
    "Removing a ticker only happens on this page — unchecking its box on those pages won't delete "
    "it or its history. Tag any watchlisted ticker into the Watch Portfolio below to record "
    "hypothetical Buy/Sell trades and compare paper performance against your real holdings."
)

wl_data = wl.load_watchlist()
items = wl_data.get("items", {})

hdr_col1, hdr_col2 = st.columns([5, 1])
with hdr_col2:
    if st.button("🔄 Refresh", use_container_width=True):
        wl.load_watchlist(force=True)
        st.rerun()
if st.session_state.get("_wl_load_error"):
    st.warning(f"Watchlist may be showing stale data — GitHub read failed: {st.session_state['_wl_load_error']}")

if not items:
    st.info(
        "No tickers on your watchlist yet. Check the ⭐ Watchlist box on Dashboard, Equity Scout, "
        "Market Screener, or Compare Stocks to add one."
    )
    st.stop()

# Live prices for everything on the watchlist — used throughout the page.
current_prices = {}
with st.spinner("Fetching live prices..."):
    for _ticker in items:
        _pdata = fetch_price_and_market_cap(_ticker)
        current_prices[_ticker] = safe_float(_pdata.get("price"))

# ─────────────────────────────────────────────
# SECTION 1 — Full Watchlist (tight table)
# ─────────────────────────────────────────────
st.markdown("## 📋 Full Watchlist")
st.caption(f"{len(items)} ticker(s) · DCF/Score/Action use the same pipeline as Compare Stocks")

weights = st.session_state.get("committed_weights", DEFAULT_WEIGHTS.copy())

si_loaded = bool(st.session_state.get("_si_full_map", {}).get("ticker_map"))
if not si_loaded:
    si_col1, si_col2 = st.columns([2, 5])
    with si_col1:
        if st.button("🦁 Load Superinvestor Conviction", use_container_width=True,
                     help="Fetches all 82 superinvestor portfolios from Dataroma (~30-60s, one-time per session)"):
            from superinvestor_utils import get_conviction_data
            get_conviction_data()
            st.rerun()
    with si_col2:
        st.caption("Optional — adds an SI column showing how many of 82 tracked value investors hold each ticker.")

table_rows = []
_foreign_currency_tickers = {}
with st.spinner("Fetching price/score/DCF snapshots..."):
    for ticker in sorted(items.keys()):
        item = items[ticker]
        snap = wl.get_ticker_snapshot(ticker, weights)
        if snap.get("foreign_currency"):
            _foreign_currency_tickers[ticker] = snap["foreign_currency"]
        mos = snap.get("margin_of_safety")
        row = {
            "Ticker": ticker,
            "Name": item.get("name", ticker),
            "Source": item.get("source") or "manual",
            "Price": current_prices.get(ticker) or snap.get("price"),
            "DCF Value": snap.get("dcf_value"),
            "MoS %": (mos * 100) if mos is not None else None,
            "Score": snap.get("score"),
            "Action": (f"{snap.get('action_emoji', '')} {snap.get('action_label', '—')}").strip(),
        }
        if si_loaded:
            from superinvestor_utils import get_superinvestor_conviction
            row["SI"] = get_superinvestor_conviction(ticker).get("holder_count", 0)
        row["Watch Portfolio"] = item.get("in_watch_portfolio", False)
        row["Remove?"] = False
        table_rows.append(row)

wl_table_df = pd.DataFrame(table_rows)

wl_column_config = {
    "Ticker":          st.column_config.TextColumn(disabled=True),
    "Name":            st.column_config.TextColumn(disabled=True),
    "Source":          st.column_config.TextColumn(disabled=True),
    "Price":           st.column_config.NumberColumn(format="$%.2f", disabled=True),
    "DCF Value":       st.column_config.NumberColumn(format="$%.2f", disabled=True),
    "MoS %":           st.column_config.NumberColumn(format="%.0f%%", disabled=True,
                                                       help="Margin of safety: (DCF value - price) / DCF value"),
    "Score":           st.column_config.NumberColumn(format="%d", disabled=True),
    "Action":          st.column_config.TextColumn(disabled=True),
    "SI":              st.column_config.NumberColumn(format="%d", disabled=True,
                                                       help="How many of 82 tracked superinvestors hold this"),
    "Watch Portfolio": st.column_config.CheckboxColumn(help="Tag into the paper Watch Portfolio below"),
    "Remove?":         st.column_config.CheckboxColumn(help="Check, then confirm below, to remove from the Watchlist"),
}

_wl_editor_gen = st.session_state.get("wl_full_editor_gen", 0)
wl_edited = st.data_editor(
    wl_table_df, column_config=wl_column_config, hide_index=True,
    use_container_width=True, key=f"wl_full_table_editor_{_wl_editor_gen}",
)
if _foreign_currency_tickers:
    _fc_note = ", ".join(f"{t} ({c})" for t, c in sorted(_foreign_currency_tickers.items()))
    st.caption(f"💱 FX-converted from home-currency EDGAR filings (#11): {_fc_note}")

# ── Apply Watch Portfolio toggles (one write per detected change) ──
for _, _row in wl_edited.iterrows():
    _t = _row["Ticker"]
    _orig = items[_t].get("in_watch_portfolio", False)
    if bool(_row["Watch Portfolio"]) != _orig:
        wl.set_in_watch_portfolio(_t, bool(_row["Watch Portfolio"]))
        st.rerun()

# ── Remove flow — requires an explicit confirm click, separate from the
#    checkbox click itself, so ticking "Remove?" can't by itself delete a
#    tracked position's transaction history. ──
_to_remove = wl_edited[wl_edited["Remove?"] == True]["Ticker"].tolist()  # noqa: E712
if _to_remove:
    _has_txs = any(len(items[t].get("transactions", [])) > 0 for t in _to_remove)
    st.warning(
        f"Remove {', '.join(_to_remove)} from the Watchlist"
        + (" — this also deletes their transaction history" if _has_txs else "") + "?"
    )
    _rc1, _rc2 = st.columns(2)
    with _rc1:
        if st.button("✅ Confirm removal", type="primary", key="wl_confirm_remove_bulk"):
            for _t in _to_remove:
                wl.remove_from_watchlist(_t)
            st.session_state["wl_full_editor_gen"] = _wl_editor_gen + 1
            st.rerun()
    with _rc2:
        if st.button("Cancel", key="wl_cancel_remove_bulk"):
            st.session_state["wl_full_editor_gen"] = _wl_editor_gen + 1
            st.rerun()

with st.expander("📝 Edit notes"):
    _note_ticker = st.selectbox("Ticker", sorted(items.keys()), key="wl_note_ticker")
    _existing_notes = items[_note_ticker].get("notes", "")
    _new_notes = st.text_area("Notes", value=_existing_notes, key=f"wl_notes_{_note_ticker}")
    if _new_notes != _existing_notes and st.button("Save notes", key="wl_notes_save"):
        wl.update_notes(_note_ticker, _new_notes)
        st.rerun()

# ─────────────────────────────────────────────
# SECTION 2 — Watch Portfolio (position table + inline Buy/Sell)
# ─────────────────────────────────────────────
st.markdown("## 💰 Watch Portfolio")
portfolio_items = {t: i for t, i in items.items() if i.get("in_watch_portfolio")}

if not portfolio_items:
    st.caption("No tickers tagged into the Watch Portfolio yet — check the box above on any watchlisted ticker.")
else:
    total_mv, total_cost, total_realized = 0.0, 0.0, 0.0
    pf_rows = []
    for ticker, item in sorted(portfolio_items.items()):
        price = current_prices.get(ticker)
        summ = wl.position_summary(item, price)
        total_mv += summ["market_value"] or 0.0
        total_cost += summ["cost_basis"] or 0.0
        total_realized += summ["realized_gain"] or 0.0
        pf_rows.append({
            "Ticker": ticker,
            "Shares": round(summ["shares_held"], 4),
            "Avg Cost": summ["avg_cost"],
            "Price": price,
            "Market Value": summ["market_value"],
            "Unrealized G/L": summ["unrealized_gain"],
            "Realized G/L": summ["realized_gain"],
            "Buy $": 0.0,
            "Sell $": 0.0,
        })
    pf_df = pd.DataFrame(pf_rows)

    pf_column_config = {
        "Ticker":         st.column_config.TextColumn(disabled=True),
        "Shares":         st.column_config.NumberColumn(format="%.4f", disabled=True),
        "Avg Cost":       st.column_config.NumberColumn(format="$%.2f", disabled=True),
        "Price":          st.column_config.NumberColumn(format="$%.2f", disabled=True),
        "Market Value":   st.column_config.NumberColumn(format="$%.2f", disabled=True),
        "Unrealized G/L": st.column_config.NumberColumn(format="$%.2f", disabled=True),
        "Realized G/L":   st.column_config.NumberColumn(format="$%.2f", disabled=True),
        "Buy $":          st.column_config.NumberColumn(format="$%.2f", min_value=0.0, step=100.0,
                                                          help="Enter an amount and click Execute Trades below"),
        "Sell $":         st.column_config.NumberColumn(format="$%.2f", min_value=0.0, step=100.0,
                                                          help="Enter an amount and click Execute Trades below"),
    }
    _pf_editor_gen = st.session_state.get("wl_portfolio_editor_gen", 0)
    pf_edited = st.data_editor(
        pf_df, column_config=pf_column_config, hide_index=True,
        use_container_width=True, key=f"wl_portfolio_editor_{_pf_editor_gen}",
    )

    m1, m2, m3 = st.columns(3)
    with m1:
        st.metric("Total Market Value", f"${total_mv:,.2f}")
    with m2:
        st.metric("Total Open Cost Basis", f"${total_cost:,.2f}")
    with m3:
        st.metric("Total Unrealized + Realized G/L", f"${(total_mv - total_cost + total_realized):,.2f}")

    if st.button("▶ Execute Trades", type="primary"):
        _executed, _errors = [], []
        for _, _row in pf_edited.iterrows():
            _t = _row["Ticker"]
            _buy_amt = float(_row["Buy $"] or 0)
            _sell_amt = float(_row["Sell $"] or 0)
            if _buy_amt <= 0 and _sell_amt <= 0:
                continue
            if _buy_amt > 0 and _sell_amt > 0:
                _errors.append(f"{_t}: enter Buy OR Sell, not both — skipped.")
                continue
            _price = current_prices.get(_t)
            if not _price or _price <= 0:
                _errors.append(f"{_t}: no live price available — skipped.")
                continue
            if _buy_amt > 0:
                _shares = _buy_amt / _price
                _ok, _msg = wl.record_transaction(_t, "buy", _shares, _price)
                (_executed if _ok else _errors).append(
                    f"Bought {_shares:.4g} {_t} @ ${_price:,.2f}" if _ok else f"{_t}: {_msg}"
                )
            else:
                _shares = _sell_amt / _price
                _ok, _msg = wl.record_transaction(_t, "sell", _shares, _price)
                (_executed if _ok else _errors).append(
                    f"Sold {_shares:.4g} {_t} @ ${_price:,.2f}" if _ok else f"{_t}: {_msg}"
                )
        for _e in _errors:
            st.error(_e)
        if _executed:
            st.success(" · ".join(_executed))
            st.session_state["wl_portfolio_editor_gen"] = _pf_editor_gen + 1
            st.rerun()

    with st.expander("Transaction history"):
        any_tx = False
        for ticker, item in sorted(portfolio_items.items()):
            txs = sorted(item.get("transactions", []), key=lambda t: t["date"])
            if not txs:
                continue
            any_tx = True
            st.markdown(f"**{ticker}**")
            for t in txs:
                trc1, trc2, trc3, trc4, trc5, trc6 = st.columns([1.4, 1, 1.2, 1.2, 1.4, 0.8])
                trc1.write(t["date"])
                trc2.write(t["action"].title())
                trc3.write(round(t["shares"], 4))
                trc4.write(f"${t['price']:,.2f}")
                trc5.write(f"${t['amount']:,.2f}")
                if trc6.button("🗑️", key=f"wl_deltx_{ticker}_{t['id']}"):
                    wl.delete_transaction(ticker, t["id"])
                    st.rerun()
                if t.get("note"):
                    st.caption(f"🌱 {t['note']} — delete + record a new Buy above to change the amount")
        if not any_tx:
            st.caption("No transactions recorded yet.")

# ─────────────────────────────────────────────
# SECTION 3 — Performance vs. Real Holdings
# ─────────────────────────────────────────────
st.markdown("## 📈 Performance vs. Holdings")

if not portfolio_items:
    st.caption("Tag at least one ticker into the Watch Portfolio and record a Buy to see performance here.")
else:
    dc1, dc2 = st.columns(2)
    with dc1:
        range_start = st.date_input(
            "Start date", value=date.today() - timedelta(days=365), max_value=date.today(), key="wl_range_start"
        )
    with dc2:
        range_end = st.date_input("End date", value=date.today(), max_value=date.today(), key="wl_range_end")

    st.caption(
        "Both baskets are priced with the same yfinance daily-close methodology, and both returns use "
        "money-weighted (XIRR) math anchored on the exact date of every Buy/Sell — same calculation, "
        "applied to two different baskets, so the percentages below are genuinely comparable."
    )

    if range_start >= range_end:
        st.error("Start date must be before end date.")
    else:
        wp_result = wl.watch_portfolio_period_return(wl_data, range_start, range_end, current_prices)

        with st.spinner("Reconstructing holdings basket for comparison..."):
            holdings_df, trans_df, load_err = wl.load_ms_holdings_and_transactions()
        if load_err:
            st.warning(f"Couldn't load MS holdings for comparison: {load_err}")
            hold_result, hold_note = None, None
        else:
            hold_result, hold_note = wl.holdings_basket_period_return(holdings_df, trans_df, range_start, range_end)

        pc1, pc2 = st.columns(2)
        with pc1:
            st.markdown("#### 🧪 Watch Portfolio")
            st.metric("Annualized Return (XIRR)",
                      f"{wp_result['xirr']:.1%}" if wp_result["xirr"] is not None else "N/A")
            st.metric("Total Return (period)",
                      f"{wp_result['simple_return']:.1%}" if wp_result["simple_return"] is not None else "N/A")
            st.caption(
                f"Begin value {range_start}: ${wp_result['begin_value']:,.2f} · "
                f"End value {range_end}: ${wp_result['end_value']:,.2f} · "
                f"Net contributions in window: ${wp_result['net_contributions']:,.2f}"
            )
        with pc2:
            st.markdown("#### 🛡️ Real Holdings")
            if hold_result is None:
                st.info("No holdings data available for comparison.")
            else:
                st.metric("Annualized Return (XIRR)",
                          f"{hold_result['xirr']:.1%}" if hold_result["xirr"] is not None else "N/A")
                st.metric("Total Return (period)",
                          f"{hold_result['simple_return']:.1%}" if hold_result["simple_return"] is not None else "N/A")
                st.caption(
                    f"Begin value {range_start}: ${hold_result['begin_value']:,.2f} · "
                    f"End value {range_end}: ${hold_result['end_value']:,.2f}"
                )
                st.caption(f"⚠️ {hold_note}")

        # ── Combined value-trend chart — both baskets, indexed to 100 ──
        st.markdown("#### Value Trend: Watch Portfolio vs. Real Holdings")
        st.caption(
            "Indexed to 100 at the start date, not raw dollars — the two portfolios are very "
            "different sizes, so this shows relative performance, not a dollar-for-dollar comparison."
        )
        chart_end = min(range_end, date.today())

        wp_frames = []
        for ticker, item in portfolio_items.items():
            series = wl.fetch_price_series(ticker, range_start.isoformat(), chart_end.isoformat())
            if series.empty:
                continue
            txs_sorted = sorted(item.get("transactions", []), key=lambda t: t["date"])

            def _shares_on(d, _txs=txs_sorted):
                s = 0.0
                for t in _txs:
                    if date.fromisoformat(t["date"]) > d:
                        break
                    s += t["shares"] if t["action"] == "buy" else -t["shares"]
                return max(0.0, s)

            series = series.copy()
            series["shares"] = series["date"].apply(_shares_on)
            series["value"] = series["shares"] * series["close"]
            wp_frames.append(series[["date", "value"]])

        wp_totals = None
        if wp_frames:
            wp_totals = pd.concat(wp_frames).groupby("date")["value"].sum().reset_index()

        hold_totals, hold_chart_note = pd.DataFrame(), None
        if not load_err:
            with st.spinner("Building real-holdings value trend (can take a bit the first time)..."):
                hold_totals, hold_chart_note = wl.holdings_basket_value_series(
                    holdings_df, trans_df, range_start, chart_end
                )

        fig = go.Figure()
        plotted_any = False
        if wp_totals is not None and not wp_totals.empty and wp_totals["value"].iloc[0] and wp_totals["value"].iloc[0] > 0:
            idx = wp_totals["value"] / wp_totals["value"].iloc[0] * 100
            fig.add_trace(go.Scatter(x=wp_totals["date"], y=idx, mode="lines", name="Watch Portfolio"))
            plotted_any = True
        if hold_totals is not None and not hold_totals.empty and hold_totals["value"].iloc[0] and hold_totals["value"].iloc[0] > 0:
            idx2 = hold_totals["value"] / hold_totals["value"].iloc[0] * 100
            fig.add_trace(go.Scatter(x=hold_totals["date"], y=idx2, mode="lines", name="Real Holdings"))
            plotted_any = True

        if plotted_any:
            fig.update_layout(yaxis_title="Indexed Value (start = 100)", xaxis_title="Date", height=350,
                               margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)
            if hold_chart_note:
                st.caption(f"Real Holdings coverage: {hold_chart_note}")
        else:
            st.caption("No price history available to chart for the selected range yet.")
