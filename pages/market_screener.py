import streamlit as st
import requests
import pandas as pd
import time

# ─────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(page_title="Market Screener | Voskuil FP", layout="wide")

APP_URL = "https://voskuil-fp-1-0-k85bd7afbw8dnqeftzxwbu.streamlit.app"
AV_URL  = "https://www.alphavantage.co/query"

# ─────────────────────────────────────────────
# DEFAULT WEIGHTS & THRESHOLDS
# ─────────────────────────────────────────────
DEFAULT_WEIGHTS = {
    "FCF Yield":              30,
    "ROIC":                   10,
    "Debt / FCF":             20,
    "Gross Margin":           15,
    "Interest Coverage":      10,
    "Price / Owner Earnings": 15,
}

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
# ALPHA VANTAGE FETCHER
# ─────────────────────────────────────────────
def safe_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def av_get_overview(ticker):
    try:
        key = st.secrets["ALPHA_VANTAGE_KEY"]
        params = {"function": "OVERVIEW", "symbol": ticker, "apikey": key}
        response = requests.get(AV_URL, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if "Symbol" in data:
                return data
        return None
    except Exception:
        return None


def av_get_cashflow(ticker):
    try:
        key = st.secrets["ALPHA_VANTAGE_KEY"]
        params = {"function": "CASH_FLOW", "symbol": ticker, "apikey": key}
        response = requests.get(AV_URL, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            reports = data.get("annualReports", [])
            if reports:
                return reports[0]
        return None
    except Exception:
        return None


def fetch_score_data(ticker):
    try:
        overview = av_get_overview(ticker)
        if not overview:
            return None

        time.sleep(12)
        cf = av_get_cashflow(ticker)

        market_cap   = safe_float(overview.get("MarketCapitalization"))
        price        = safe_float(overview.get("50DayMovingAverage"))
        gross_profit = safe_float(overview.get("GrossProfitTTM"))
        revenue      = safe_float(overview.get("RevenueTTM"))
        gross_margin = (gross_profit / revenue) if (gross_profit and revenue and revenue > 0) else None
        roic         = safe_float(overview.get("ReturnOnEquityTTM"))
        ebitda       = safe_float(overview.get("EBITDA"))
        total_debt   = safe_float(overview.get("TotalDebt")) or safe_float(overview.get("LongTermDebtNoncurrent"))
        div_yield    = safe_float(overview.get("DividendYield"))
        shares       = safe_float(overview.get("SharesOutstanding"))

        fcf = None
        fcf_yield = None
        debt_to_fcf = None
        interest_coverage = None
        poe = None

        if cf:
            op_cf   = safe_float(cf.get("operatingCashflow"))
            capex   = safe_float(cf.get("capitalExpenditures"))
            net_inc = safe_float(cf.get("netIncome"))
            dna     = safe_float(cf.get("depreciationDepletionAndAmortization"))
            int_exp = safe_float(cf.get("interestExpense")) or safe_float(cf.get("paymentsForInterest"))

            if op_cf is not None and capex is not None:
                fcf = op_cf - capex

            if fcf and market_cap and market_cap > 0:
                fcf_yield = fcf / market_cap

            if fcf and total_debt and fcf > 0:
                debt_to_fcf = total_debt / fcf

            if ebitda and int_exp and int_exp > 0:
                interest_coverage = ebitda / int_exp

            if net_inc is not None and dna is not None and capex is not None:
                owner_earn = net_inc + dna - capex
                if owner_earn > 0 and shares and price:
                    poe = price / (owner_earn / shares)

        if not fcf or fcf <= 0:
            return None

        return {
            "ticker":            ticker,
            "name":              overview.get("Name", ticker),
            "sector":            overview.get("Sector", "N/A"),
            "price":             price,
            "market_cap":        market_cap,
            "fcf_yield":         fcf_yield,
            "roic":              roic,
            "debt_to_fcf":       debt_to_fcf,
            "interest_coverage": interest_coverage,
            "gross_margin":      gross_margin,
            "price_owner_earn":  poe,
            "dividend_yield":    div_yield,
        }

    except Exception:
        return None


# ─────────────────────────────────────────────
# SCORING ENGINE
# ─────────────────────────────────────────────
def score_stock(data, weights):
    pts = 0

    fcf_yield = data.get('fcf_yield')
    if fcf_yield:
        if fcf_yield >= THRESHOLDS['fcf_yield_great']:   pts += weights["FCF Yield"]
        elif fcf_yield >= THRESHOLDS['fcf_yield_good']:  pts += round(weights["FCF Yield"] * 0.60)
        elif fcf_yield > 0:                              pts += round(weights["FCF Yield"] * 0.15)

    roic = data.get('roic')
    if roic:
        if roic >= THRESHOLDS['roic_great']:   pts += weights["ROIC"]
        elif roic >= THRESHOLDS['roic_good']:  pts += round(weights["ROIC"] * 0.60)
        elif roic > 0:                         pts += round(weights["ROIC"] * 0.20)

    debt_fcf = data.get('debt_to_fcf')
    ic       = data.get('interest_coverage') or 0
    if debt_fcf is not None:
        if debt_fcf < THRESHOLDS['debt_fcf_safe']:       pts += weights["Debt / FCF"]
        elif debt_fcf < THRESHOLDS['debt_fcf_warning']:  pts += round(weights["Debt / FCF"] * 0.50)
        elif ic >= THRESHOLDS['interest_coverage_safe']: pts += round(weights["Debt / FCF"] * 0.50)

    gm = data.get('gross_margin')
    if gm:
        if gm >= THRESHOLDS['gross_margin_great']:   pts += weights["Gross Margin"]
        elif gm >= THRESHOLDS['gross_margin_good']:  pts += round(weights["Gross Margin"] * 0.67)
        else:                                        pts += round(weights["Gross Margin"] * 0.20)

    ic_val = data.get('interest_coverage')
    if ic_val:
        if ic_val >= THRESHOLDS['interest_coverage_safe']: pts += weights["Interest Coverage"]
        elif ic_val >= 2.5:                                pts += round(weights["Interest Coverage"] * 0.50)
        elif ic_val > 0:                                   pts += round(weights["Interest Coverage"] * 0.15)

    poe = data.get('price_owner_earn')
    if poe:
        if poe <= THRESHOLDS['poe_bargain']:     pts += weights["Price / Owner Earnings"]
        elif poe <= THRESHOLDS['poe_fair']:      pts += round(weights["Price / Owner Earnings"] * 0.67)
        elif poe <= THRESHOLDS['poe_stretched']: pts += round(weights["Price / Owner Earnings"] * 0.25)

    return pts


def score_to_label(score):
    if score >= 80:   return "Strong Buy", "🟢"
    elif score >= 65: return "Watch",      "🟡"
    elif score >= 45: return "Caution",    "🟠"
    else:             return "Avoid",      "🔴"


# ─────────────────────────────────────────────
# S&P 500 TICKER LIST
# ─────────────────────────────────────────────
@st.cache_data(ttl=86400)
def get_sp500_tickers():
    try:
        from io import StringIO
        headers = {"User-Agent": "Mozilla/5.0 (compatible; VoskuilFP/1.0)"}
        response = requests.get(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers=headers,
            timeout=10
        )
        tables = pd.read_html(StringIO(response.text))
        df = tables[0]
        tickers = df['Symbol'].str.replace('.', '-', regex=False).tolist()
        return tickers
    except Exception as e:
        st.error(f"Could not fetch S&P 500 list: {e}")
        return []

# ─────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────
st.title("📡 Market Screener")
st.caption("Scans the S&P 500 through the Voskuil Owner's Framework.")

st.warning(
    "⚠️ **Alpha Vantage Free Tier Limits:** 25 API calls/day, 5 calls/minute. "
    "Each stock uses 2 calls with a 12-second pause between them. "
    "**Max recommended scan: 10-12 stocks per day** to leave calls for Equity Scout. "
    "Use sector filters to focus on your highest-conviction areas."
)

st.divider()

# Weights
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
    if total_weight == 100:   st.success(f"✅ Total: {total_weight} / 100")
    elif total_weight < 100:  st.warning(f"⚠️ Total: {total_weight} / 100 — {100 - total_weight} pts unallocated")
    else:                     st.error(f"❌ Total: {total_weight} / 100 — over by {total_weight - 100} pts.")

# Screener controls
col1, col2, col3 = st.columns(3)
with col1:
    top_n = st.number_input("Top results to show", min_value=5, max_value=50, value=10, step=5)
with col2:
    sector_filter = st.selectbox("Filter by sector", [
        "All Sectors", "Technology", "Healthcare", "Financials",
        "Consumer Staples", "Consumer Discretionary", "Industrials",
        "Energy", "Utilities", "Real Estate", "Materials", "Communication Services"
    ])
with col3:
    max_scan = st.number_input(
        "Max stocks to scan",
        min_value=5, max_value=500, value=10, step=5,
        help="Keep at 10-12 on the free Alpha Vantage tier."
    )
    min_div = st.checkbox("Dividend payers only", value=False)

st.divider()
run_screen = st.button("🚀 Run Screen", type="primary", use_container_width=True)

# ─────────────────────────────────────────────
# SCREENER EXECUTION
# ─────────────────────────────────────────────
if run_screen:

    if total_weight != 100:
        st.error(f"Weights must add up to 100. Currently at {total_weight}.")
        st.stop()

    tickers = get_sp500_tickers()
    if not tickers:
        st.error("Could not load S&P 500 ticker list.")
        st.stop()

    tickers_to_scan = tickers[:max_scan]
    total_tickers   = len(tickers_to_scan)

    st.markdown(f"### Scanning {total_tickers} companies...")
    st.caption("Each stock takes ~25 seconds due to API rate limiting. Please be patient.")
    progress_bar = st.progress(0)
    status_text  = st.empty()
    results      = []

    for i, ticker in enumerate(tickers_to_scan):
        pct = (i + 1) / total_tickers
        progress_bar.progress(pct)
        status_text.markdown(
            f"⏳ Analyzing **{ticker}** — {i+1} of {total_tickers} "
            f"({int(pct*100)}%) — {len(results)} candidates found"
        )

        data = fetch_score_data(ticker)
        if data is None:
            continue

        if sector_filter != "All Sectors" and data.get('sector') != sector_filter:
            continue

        if min_div and not data.get('dividend_yield'):
            continue

        score = score_stock(data, weights)
        data['score'] = score
        results.append(data)

    progress_bar.progress(1.0)
    status_text.markdown(f"✅ Scan complete — {len(results)} companies passed the FCF filter.")

    if not results:
        st.warning("No results found. Try removing filters or increasing max stocks to scan.")
        st.stop()

    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('score', ascending=False).head(top_n).reset_index(drop=True)

    st.divider()
    st.markdown(f"## 🏆 Top {min(top_n, len(results_df))} Concentrated Opportunities")

    def fmt(val, fmt_type):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return "N/A"
        if fmt_type == "pct":   return f"{val:.1%}"
        if fmt_type == "ratio": return f"{val:.1f}x"
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

            div = row.get('dividend_yield')
            if div:
                st.caption(f"💰 Dividend Yield: {div:.2%}")

            st.markdown(
                f"[🔍 Deep Dive in Equity Scout]({APP_URL}/equity_scout?ticker={row['ticker']}&auto=1)"
            )
            st.divider()

    # Summary
    st.markdown("### 📊 Screen Summary")
    s1, s2, s3, s4 = st.columns(4)
    with s1: st.metric("Scanned",           total_tickers)
    with s2: st.metric("Passed FCF Filter", len(results))
    with s3: st.metric("Avg Score",         f"{results_df['score'].mean():.0f}")
    with s4: st.metric("Strong Buys (80+)", len(results_df[results_df['score'] >= 80]))

    # Export
    st.markdown("### 💾 Export Results")
    export_df = results_df[[
        'ticker', 'name', 'sector', 'score',
        'fcf_yield', 'roic', 'gross_margin',
        'debt_to_fcf', 'interest_coverage',
        'price_owner_earn', 'dividend_yield', 'price', 'market_cap'
    ]].copy()
    export_df.columns = [
        'Ticker', 'Name', 'Sector', 'Score',
        'FCF Yield', 'ROIC', 'Gross Margin',
        'Debt/FCF', 'Interest Coverage',
        'Price/Owner Earnings', 'Dividend Yield', 'Price', 'Market Cap'
    ]
    csv = export_df.to_csv(index=False)
    st.download_button(
        label="⬇️ Download Results as CSV",
        data=csv,
        file_name="voskuil_screen_results.csv",
        mime="text/csv"
    )

else:
    st.markdown("""
    ### What this screener does

    1. **Loads S&P 500 companies** from Wikipedia
    2. **Eliminates** companies with negative Free Cash Flow
    3. **Scores** remaining companies on the 6-metric Owner's Framework
    4. **Returns top results** ranked by conviction score

    ### ⚠️ Free tier API limits
    Alpha Vantage free tier: **25 calls/day, 5 calls/minute**.
    Each stock = 2 calls + 12 second pause.
    **Recommended: scan 10-12 stocks per session** and use sector filters to focus.

    ---
    **Score guide:** 🟢 80+ Strong Buy · 🟡 65-79 Watch · 🟠 45-64 Caution · 🔴 <45 Avoid

    *Data sourced from Alpha Vantage.*
    """)
