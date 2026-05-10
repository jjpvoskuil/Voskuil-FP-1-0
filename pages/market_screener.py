import streamlit as st
import yfinance as yf
import pandas as pd
import time

# ─────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(page_title="Market Screener | Voskuil FP", layout="wide")

# ─────────────────────────────────────────────
# S&P 500 TICKER LIST
# Pulled from Wikipedia via pandas - cached so
# it only fetches once per session.
# ─────────────────────────────────────────────
@st.cache_data(ttl=86400)  # Refresh once per day
def get_sp500_tickers() -> list[str]:
    try:
        table   = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        df      = table[0]
        tickers = df['Symbol'].str.replace('.', '-', regex=False).tolist()
        return tickers
    except Exception as e:
        st.error(f"Could not fetch S&P 500 list: {e}")
        return []

# ─────────────────────────────────────────────
# DEFAULT WEIGHTS
# Keep in sync with equity_scout.py
# ─────────────────────────────────────────────
DEFAULT_WEIGHTS = {
    "FCF Yield":              30,
    "ROIC":                   10,
    "Debt / FCF":             20,
    "Gross Margin":           15,
    "Interest Coverage":      10,
    "Price / Owner Earnings": 15,
}

# ─────────────────────────────────────────────
# SCORING THRESHOLDS
# Keep in sync with equity_scout.py
# ─────────────────────────────────────────────
THRESHOLDS = {
    "fcf_yield_good":           0.04,
    "fcf_yield_great":          0.06,
    "roic_good":                0.12,
    "roic_great":               0.20,
    "debt_fcf_safe":            3.0,
    "debt_fcf_warning":         5.0,
    "interest_coverage_safe":   5.0,
    "gross_margin_good":        0.40,
    "gross_margin_great":       0.60,
    "poe_bargain":              15.0,
    "poe_fair":                 25.0,
    "poe_stretched":            35.0,
}

# ─────────────────────────────────────────────
# FAST DATA FETCHER
# Lightweight version - only pulls what the
# scoring engine needs. Skips slow API calls.
# ─────────────────────────────────────────────
def fetch_score_data(ticker: str) -> dict | None:
    """
    Fetches the minimum data needed to score a stock.
    Returns None if data is missing or the company
    has negative FCF (automatic disqualifier).
    """
    try:
        stock      = yf.Ticker(ticker)
        info       = stock.info
        cashflow   = stock.cashflow
        financials = stock.financials
        balance    = stock.balance_sheet

        # Must have market cap data or skip
        market_cap = info.get('marketCap')
        if not market_cap or market_cap < 1_000_000_000:
            return None  # Skip sub-$1B companies

        # Free Cash Flow — negative FCF is auto-disqualified
        try:
            op_cf = cashflow.loc['Operating Cash Flow'].iloc[0]
            capex = cashflow.loc['Capital Expenditure'].iloc[0]
            fcf   = op_cf + capex
            if fcf <= 0:
                return None  # No negative FCF companies
        except Exception:
            return None

        # FCF Yield
        fcf_yield = fcf / market_cap

        # ROIC
        try:
            net_income   = financials.loc['Net Income'].iloc[0]
            total_assets = balance.loc['Total Assets'].iloc[0]
            current_liab = balance.loc['Current Liabilities'].iloc[0]
            invested_cap = total_assets - current_liab
            roic         = net_income / invested_cap if invested_cap != 0 else None
        except Exception:
            net_income, roic = None, None

        # Debt / FCF
        try:
            total_debt  = balance.loc['Total Debt'].iloc[0]
            debt_to_fcf = total_debt / fcf if fcf > 0 else None
        except Exception:
            debt_to_fcf = None

        # Interest Coverage
        try:
            ebit             = financials.loc['EBIT'].iloc[0]
            interest_expense = abs(financials.loc['Interest Expense'].iloc[0])
            interest_coverage= ebit / interest_expense if interest_expense != 0 else None
        except Exception:
            interest_coverage = None

        # Gross Margin
        gross_margin = info.get('grossMargins')

        # Price / Owner Earnings
        try:
            dna       = cashflow.loc['Depreciation And Amortization'].iloc[0]
            capex_val = abs(cashflow.loc['Capital Expenditure'].iloc[0])
            owner_earn= net_income + dna - capex_val if net_income is not None else None
            shares    = info.get('sharesOutstanding')
            price     = info.get('currentPrice') or info.get('regularMarketPrice')
            poe       = (price / (owner_earn / shares)) if (owner_earn and owner_earn > 0 and shares and price) else None
        except Exception:
            owner_earn, poe = None, None

        return {
            "ticker":           ticker,
            "name":             info.get('longName', ticker),
            "sector":           info.get('sector', 'N/A'),
            "price":            info.get('currentPrice') or info.get('regularMarketPrice'),
            "market_cap":       market_cap,
            "fcf_yield":        fcf_yield,
            "roic":             roic,
            "debt_to_fcf":      debt_to_fcf,
            "interest_coverage":interest_coverage,
            "gross_margin":     gross_margin,
            "price_owner_earn": poe,
            "dividend_yield":   info.get('dividendYield'),
        }

    except Exception:
        return None  # Silently skip any ticker that errors


# ─────────────────────────────────────────────
# SCORING ENGINE (same logic as equity_scout.py)
# ─────────────────────────────────────────────
def score_stock(data: dict, weights: dict) -> int:
    pts_total = 0

    # FCF Yield
    fcf_yield = data.get('fcf_yield')
    max_pts   = weights["FCF Yield"]
    if fcf_yield:
        if fcf_yield >= THRESHOLDS['fcf_yield_great']:  pts_total += max_pts
        elif fcf_yield >= THRESHOLDS['fcf_yield_good']: pts_total += round(max_pts * 0.60)
        elif fcf_yield > 0:                             pts_total += round(max_pts * 0.15)

    # ROIC
    roic    = data.get('roic')
    max_pts = weights["ROIC"]
    if roic:
        if roic >= THRESHOLDS['roic_great']:  pts_total += max_pts
        elif roic >= THRESHOLDS['roic_good']: pts_total += round(max_pts * 0.60)
        elif roic > 0:                        pts_total += round(max_pts * 0.20)

    # Debt / FCF
    debt_fcf = data.get('debt_to_fcf')
    ic       = data.get('interest_coverage') or 0
    max_pts  = weights["Debt / FCF"]
    if debt_fcf is not None:
        if debt_fcf < THRESHOLDS['debt_fcf_safe']:      pts_total += max_pts
        elif debt_fcf < THRESHOLDS['debt_fcf_warning']: pts_total += round(max_pts * 0.50)
        elif ic >= THRESHOLDS['interest_coverage_safe']:pts_total += round(max_pts * 0.50)

    # Gross Margin
    gm      = data.get('gross_margin')
    max_pts = weights["Gross Margin"]
    if gm:
        if gm >= THRESHOLDS['gross_margin_great']:  pts_total += max_pts
        elif gm >= THRESHOLDS['gross_margin_good']: pts_total += round(max_pts * 0.67)
        else:                                       pts_total += round(max_pts * 0.20)

    # Interest Coverage
    ic_val  = data.get('interest_coverage')
    max_pts = weights["Interest Coverage"]
    if ic_val:
        if ic_val >= THRESHOLDS['interest_coverage_safe']: pts_total += max_pts
        elif ic_val >= 2.5:                                pts_total += round(max_pts * 0.50)
        elif ic_val > 0:                                   pts_total += round(max_pts * 0.15)

    # Price / Owner Earnings
    poe     = data.get('price_owner_earn')
    max_pts = weights["Price / Owner Earnings"]
    if poe:
        if poe <= THRESHOLDS['poe_bargain']:    pts_total += max_pts
        elif poe <= THRESHOLDS['poe_fair']:     pts_total += round(max_pts * 0.67)
        elif poe <= THRESHOLDS['poe_stretched']:pts_total += round(max_pts * 0.25)

    return pts_total


def score_to_label(score: int) -> tuple[str, str]:
    if score >= 80:   return "Strong Buy", "🟢"
    elif score >= 65: return "Watch",      "🟡"
    elif score >= 45: return "Caution",    "🟠"
    else:             return "Avoid",      "🔴"


# ─────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────
st.title("📡 Market Screener")
st.caption("Scans the S&P 500 through the Voskuil Owner's Framework. Surfaces the top concentrated opportunities.")

st.info(
    "**How this works:** The screener fetches fundamentals for each S&P 500 company, "
    "scores them using the same 6-metric engine as Equity Scout, and returns the top results. "
    "Stocks with negative Free Cash Flow are automatically eliminated. "
    "⏱️ A full scan takes 10–20 minutes — start it and check back."
)

st.divider()

# ── Weight Controls ───────────────────────────────────────────────────────
with st.expander("⚙️ Customize Scoring Weights", expanded=False):
    st.caption("These should match your Equity Scout weights for consistent results.")
    w_col1, w_col2 = st.columns(2)
    with w_col1:
        w_fcf  = st.slider("FCF Yield",              0, 60, DEFAULT_WEIGHTS["FCF Yield"],              step=5)
        w_roic = st.slider("ROIC",                   0, 40, DEFAULT_WEIGHTS["ROIC"],                   step=5)
        w_debt = st.slider("Debt / FCF",             0, 40, DEFAULT_WEIGHTS["Debt / FCF"],             step=5)
    with w_col2:
        w_gm   = st.slider("Gross Margin",           0, 40, DEFAULT_WEIGHTS["Gross Margin"],           step=5)
        w_ic   = st.slider("Interest Coverage",      0, 40, DEFAULT_WEIGHTS["Interest Coverage"],      step=5)
        w_poe  = st.slider("Price / Owner Earnings", 0, 40, DEFAULT_WEIGHTS["Price / Owner Earnings"], step=5)

    weights = {
        "FCF Yield":              w_fcf,
        "ROIC":                   w_roic,
        "Debt / FCF":             w_debt,
        "Gross Margin":           w_gm,
        "Interest Coverage":      w_ic,
        "Price / Owner Earnings": w_poe,
    }

    total_weight = sum(weights.values())
    if total_weight == 100:
        st.success(f"✅ Total: {total_weight} / 100")
    elif total_weight < 100:
        st.warning(f"⚠️ Total: {total_weight} / 100 — {100 - total_weight} pts unallocated")
    else:
        st.error(f"❌ Total: {total_weight} / 100 — over by {total_weight - 100} pts.")

# ── Screener Controls ─────────────────────────────────────────────────────
col1, col2, col3 = st.columns(3)
with col1:
    top_n = st.number_input("How many top results to show?", min_value=5, max_value=50, value=15, step=5)
with col2:
    sector_filter = st.selectbox("Filter by sector (optional)", [
        "All Sectors",
        "Technology", "Healthcare", "Financials", "Consumer Staples",
        "Consumer Discretionary", "Industrials", "Energy",
        "Utilities", "Real Estate", "Materials", "Communication Services"
    ])
with col3:
    min_div = st.checkbox("Dividend payers only", value=False,
                           help="Only show companies that currently pay a dividend")

st.divider()

run_screen = st.button("🚀 Run Screen", type="primary", use_container_width=True)

# ── Screener Execution ────────────────────────────────────────────────────
if run_screen:

    if total_weight != 100:
        st.error(f"Weights must add up to 100 before running. Currently at {total_weight}.")
        st.stop()

    tickers = get_sp500_tickers()
    if not tickers:
        st.error("Could not load S&P 500 ticker list. Check your internet connection.")
        st.stop()

    # Apply sector pre-filter if selected
    total_tickers = len(tickers)

    st.markdown(f"### Scanning {total_tickers} companies...")
    progress_bar  = st.progress(0)
    status_text   = st.empty()
    results       = []

    for i, ticker in enumerate(tickers):

        # Update progress
        pct = (i + 1) / total_tickers
        progress_bar.progress(pct)
        status_text.markdown(
            f"⏳ Analyzing **{ticker}** — {i+1} of {total_tickers} "
            f"({int(pct*100)}%) — {len(results)} candidates found so far"
        )

        # Fetch & score
        data = fetch_score_data(ticker)
        if data is None:
            continue

        # Sector filter
        if sector_filter != "All Sectors" and data.get('sector') != sector_filter:
            continue

        # Dividend filter
        if min_div and not data.get('dividend_yield'):
            continue

        score = score_stock(data, weights)
        data['score'] = score
        results.append(data)

        # Small delay to be polite to Yahoo Finance's servers
        time.sleep(0.1)

    progress_bar.progress(1.0)
    status_text.markdown(f"✅ Scan complete — {len(results)} companies passed the FCF filter.")

    if not results:
        st.warning("No results found. Try removing the sector or dividend filters.")
        st.stop()

    # Sort by score, take top N
    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('score', ascending=False).head(top_n).reset_index(drop=True)

    st.divider()
    st.markdown(f"## 🏆 Top {min(top_n, len(results_df))} Concentrated Opportunities")
    st.caption("Ranked by Voskuil Owner's Framework score. Click any row to deep-dive in Equity Scout.")

    # ── Results Table ──────────────────────────────────────────────────
    def fmt(val, fmt_type):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return "N/A"
        if fmt_type == "pct":   return f"{val:.1%}"
        if fmt_type == "ratio": return f"{val:.1f}x"
        if fmt_type == "price": return f"${val:,.2f}"
        if fmt_type == "mcap":  return f"${val/1e9:.1f}B"
        return str(val)

    for rank, row in results_df.iterrows():
        score       = int(row['score'])
        label, icon = score_to_label(score)

        with st.container():
            c1, c2, c3, c4, c5, c6, c7, c8 = st.columns([1, 3, 2, 2, 2, 2, 2, 2])

            with c1:
                st.markdown(f"### {icon}")
                st.markdown(f"**#{rank+1}**")
            with c2:
                st.markdown(f"**{row['ticker']}**")
                st.caption(row.get('name', ''))
                st.caption(row.get('sector', ''))
            with c3:
                st.metric("Score", f"{score}/100")
            with c4:
                st.metric("FCF Yield", fmt(row.get('fcf_yield'), "pct"))
            with c5:
                st.metric("ROIC", fmt(row.get('roic'), "pct"))
            with c6:
                st.metric("Gross Margin", fmt(row.get('gross_margin'), "pct"))
            with c7:
                st.metric("Debt/FCF", fmt(row.get('debt_to_fcf'), "ratio"))
            with c8:
                st.metric("P/OE", fmt(row.get('price_owner_earn'), "ratio"))

            # Dividend yield if available
            div = row.get('dividend_yield')
            if div:
                st.caption(f"💰 Dividend Yield: {div:.2%}")

            st.divider()

    # ── Summary Stats ──────────────────────────────────────────────────
    st.markdown("### 📊 Screen Summary")
    s1, s2, s3, s4 = st.columns(4)
    with s1: st.metric("Companies Scanned",   total_tickers)
    with s2: st.metric("Passed FCF Filter",   len(results))
    with s3: st.metric("Avg Score (Top List)", f"{results_df['score'].mean():.0f}")
    with s4: st.metric("Strong Buys (80+)",   len(results_df[results_df['score'] >= 80]))

    # ── Export ────────────────────────────────────────────────────────
    st.markdown("### 💾 Export Results")
    export_df = results_df[[
        'ticker', 'name', 'sector', 'score',
        'fcf_yield', 'roic', 'gross_margin',
        'debt_to_fcf', 'interest_coverage',
        'price_owner_earn', 'dividend_yield', 'price', 'market_cap'
    ]].copy()

    # Format for export
    export_df.columns = [
        'Ticker', 'Name', 'Sector', 'Score',
        'FCF Yield', 'ROIC', 'Gross Margin',
        'Debt/FCF', 'Interest Coverage',
        'Price/Owner Earnings', 'Dividend Yield', 'Price', 'Market Cap'
    ]

    csv = export_df.to_csv(index=False)
    st.download_button(
        label="⬇️ Download Top Results as CSV",
        data=csv,
        file_name="voskuil_screen_results.csv",
        mime="text/csv"
    )

    st.caption(
        "💡 **Next step:** Take the top tickers into Equity Scout for a full deep-dive "
        "including the ownership report and income potential at your position size."
    )

else:
    # Landing state
    st.markdown("""
    ### What this screener does

    1. **Loads all ~500 S&P 500 companies** from a live Wikipedia table
    2. **Eliminates** any company with negative Free Cash Flow — they fail the first test
    3. **Scores** every remaining company on the same 6-metric Owner's Framework as Equity Scout
    4. **Returns the top results** ranked by conviction score

    ### Filters available
    - **Sector** — focus on industries you understand or want exposure to
    - **Dividend payers only** — useful if income is your priority

    ### After the screen
    Take your top 15 into **Equity Scout** for the full deep-dive report.
    Then narrow to your 5 highest-conviction names for concentrated positions.

    ---
    **Score guide:** 🟢 80+ Strong Buy · 🟡 65-79 Watch · 🟠 45-64 Caution · 🔴 <45 Avoid

    *Data sourced from Yahoo Finance via yfinance. For proof-of-concept use only.*
    """)
