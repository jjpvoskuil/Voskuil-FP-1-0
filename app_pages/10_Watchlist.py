"""
10_Watchlist.py — dedicated Watchlist + paper "Watch Portfolio" page (#68).

Tickers are added here (add-only) from Dashboard, Equity Scout, Market
Screener, and Compare Stocks via a shared ☆ Watchlist checkbox — see
watchlist_utils.py's module docstring for the full design rationale
(persistence, why removal only happens on this page, and why the
watch-portfolio-vs-holdings comparison reuses one return-calc function for
both baskets).
"""

import sys, os
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from sec_utils import fetch_price_and_market_cap, safe_float
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
# SECTION 1 — Full Watchlist
# ─────────────────────────────────────────────
st.markdown("## 📋 Full Watchlist")
st.caption(f"{len(items)} ticker(s)")

for ticker, item in sorted(items.items()):
    price = current_prices.get(ticker)
    with st.container():
        c1, c2, c3, c4, c5 = st.columns([1.2, 3, 1.5, 1.8, 1.5])
        with c1:
            st.markdown(f"### {ticker}")
        with c2:
            st.caption(item.get("name", ticker))
            st.caption(f"Added {item.get('added_date', '?')} · via {item.get('source') or 'manual'}")
        with c3:
            st.metric("Price", f"${price:,.2f}" if price else "N/A")
        with c4:
            in_portfolio = st.checkbox(
                "Watch Portfolio",
                value=item.get("in_watch_portfolio", False),
                key=f"wl_tag_{ticker}",
                help="Include this ticker in the paper Watch Portfolio below so you can Buy/Sell it.",
            )
            if in_portfolio != item.get("in_watch_portfolio", False):
                wl.set_in_watch_portfolio(ticker, in_portfolio)
                st.rerun()
        with c5:
            confirm_key = f"wl_confirm_remove_{ticker}"
            has_txs = len(item.get("transactions", [])) > 0
            if st.session_state.get(confirm_key):
                st.warning("Remove + delete history?")
                yc, nc = st.columns(2)
                with yc:
                    if st.button("Yes", key=f"wl_remove_yes_{ticker}", type="primary", use_container_width=True):
                        wl.remove_from_watchlist(ticker)
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
                with nc:
                    if st.button("Cancel", key=f"wl_remove_no_{ticker}", use_container_width=True):
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
            else:
                if st.button("🗑️ Remove", key=f"wl_remove_{ticker}", use_container_width=True):
                    if has_txs:
                        st.session_state[confirm_key] = True
                        st.rerun()
                    else:
                        wl.remove_from_watchlist(ticker)
                        st.rerun()

        with st.expander("Notes"):
            existing_notes = item.get("notes", "")
            new_notes = st.text_area("Notes", value=existing_notes, key=f"wl_notes_{ticker}",
                                      label_visibility="collapsed")
            if new_notes != existing_notes and st.button("Save notes", key=f"wl_notes_save_{ticker}"):
                wl.update_notes(ticker, new_notes)
                st.rerun()
        st.divider()

# ─────────────────────────────────────────────
# SECTION 2 — Watch Portfolio (Buy/Sell paper ledger)
# ─────────────────────────────────────────────
st.markdown("## 💰 Watch Portfolio")
portfolio_items = {t: i for t, i in items.items() if i.get("in_watch_portfolio")}

if not portfolio_items:
    st.caption("No tickers tagged into the Watch Portfolio yet — check the box above on any watchlisted ticker.")
else:
    total_mv, total_cost, total_realized = 0.0, 0.0, 0.0
    rows = []
    for ticker, item in sorted(portfolio_items.items()):
        price = current_prices.get(ticker)
        summ = wl.position_summary(item, price)
        total_mv += summ["market_value"] or 0.0
        total_cost += summ["cost_basis"] or 0.0
        total_realized += summ["realized_gain"] or 0.0
        rows.append({
            "Ticker": ticker,
            "Shares": round(summ["shares_held"], 4),
            "Avg Cost": f"${summ['avg_cost']:,.2f}" if summ["avg_cost"] else "—",
            "Price": f"${price:,.2f}" if price else "N/A",
            "Market Value": f"${summ['market_value']:,.2f}" if summ["market_value"] is not None else "N/A",
            "Unrealized G/L": f"${summ['unrealized_gain']:,.2f}" if summ["unrealized_gain"] is not None else "N/A",
            "Realized G/L": f"${summ['realized_gain']:,.2f}",
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    m1, m2, m3 = st.columns(3)
    with m1:
        st.metric("Total Market Value", f"${total_mv:,.2f}")
    with m2:
        st.metric("Total Open Cost Basis", f"${total_cost:,.2f}")
    with m3:
        st.metric("Total Unrealized + Realized G/L", f"${(total_mv - total_cost + total_realized):,.2f}")

    st.markdown("### Buy / Sell")
    bs_ticker = st.selectbox("Ticker", sorted(portfolio_items.keys()), key="wl_bs_ticker")
    bs_item = portfolio_items[bs_ticker]
    bs_price_live = current_prices.get(bs_ticker)
    bs_summary = wl.position_summary(bs_item, bs_price_live)

    bc1, bc2, bc3, bc4 = st.columns([1.2, 2, 2, 1.6])
    with bc1:
        action = st.radio("Action", ["Buy", "Sell"], key="wl_bs_action", horizontal=True)
    with bc2:
        amount = st.number_input("Amount ($)", min_value=0.0, step=100.0, key="wl_bs_amount")
    with bc3:
        use_price = st.number_input(
            "Price ($/share)", min_value=0.0, step=0.01,
            value=float(bs_price_live) if bs_price_live else 0.0,
            key="wl_bs_price",
            help="Defaults to the current live price — override for a historical/hypothetical entry date.",
        )
    with bc4:
        tx_date = st.date_input("Date", value=date.today(), max_value=date.today(), key="wl_bs_date")

    if action == "Sell":
        st.caption(f"Currently holding {bs_summary['shares_held']:.4g} shares of {bs_ticker}.")

    if st.button(f"{'🟢 Execute Buy' if action == 'Buy' else '🔴 Execute Sell'}", key="wl_bs_submit"):
        if amount <= 0 or use_price <= 0:
            st.error("Enter a positive amount and price.")
        else:
            shares = amount / use_price
            ok, msg = wl.record_transaction(bs_ticker, action.lower(), shares, use_price, tx_date.isoformat())
            if ok:
                st.success(f"{action} recorded: {shares:.4g} shares of {bs_ticker} @ ${use_price:,.2f}")
                st.rerun()
            else:
                st.error(msg)

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

        # ── Watch Portfolio value-over-time chart ──
        st.markdown("#### Watch Portfolio value over time")
        chart_frames = []
        chart_end = min(range_end, date.today())
        for ticker, item in portfolio_items.items():
            series = wl.fetch_price_series(ticker, range_start.isoformat(), chart_end.isoformat())
            if series.empty:
                continue
            txs_sorted = sorted(item.get("transactions", []), key=lambda t: t["date"])

            def shares_on(d, _txs=txs_sorted):
                s = 0.0
                for t in _txs:
                    if date.fromisoformat(t["date"]) > d:
                        break
                    s += t["shares"] if t["action"] == "buy" else -t["shares"]
                return max(0.0, s)

            series = series.copy()
            series["shares"] = series["date"].apply(shares_on)
            series["value"] = series["shares"] * series["close"]
            chart_frames.append(series[["date", "value"]])

        if chart_frames:
            combined = pd.concat(chart_frames)
            totals = combined.groupby("date")["value"].sum().reset_index()
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=totals["date"], y=totals["value"], mode="lines", name="Watch Portfolio"))
            fig.update_layout(yaxis_title="Value ($)", xaxis_title="Date", height=350,
                               margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No price history available to chart for the selected range yet.")
