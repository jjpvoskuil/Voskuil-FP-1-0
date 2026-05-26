import streamlit as st
import requests
import pandas as pd
from io import StringIO
import time

st.set_page_config(page_title="Market Screener | Voskuil FP", layout="wide")

APP_URL  = "https://voskuil-fp-1-0-k85bd7afbw8dnqeftzxwbu.streamlit.app"
POLY_URL = "https://api.polygon.io"

DEFAULT_WEIGHTS = {
    "FCF Yield":              20,
    "ROIC":                   10,
    "Debt / FCF":             20,
    "Gross Margin":           15,
    "Interest Coverage":      10,
    "Price / Owner Earnings": 25,
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

def poly_get(endpoint, params={}):
    try:
        key = st.secrets["POLYGON_KEY"]
        url = f"{POLY_URL}{endpoint}"
        all_params = {**params, "apiKey": key}
        response = requests.get(url, params=all_params, timeout=10)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception:
        return None

def safe_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None

def sfval(obj, key):
    """Safe float from flat dict — new Fundamentals API returns raw numbers."""
    try:
        return float(obj[key])
    except (KeyError, TypeError, ValueError):
        return None

def fval(obj, key):
    """Legacy vX API helper — fields wrapped in {value: X} dicts."""
    try:
        return float(obj[key]["value"])
    except (KeyError, TypeError, ValueError):
        return None

def calc_interest_coverage_new(inc, cf):
    """Interest coverage from new flat-field API."""
    op_income    = sfval(inc, "operating_income")
    interest_exp = sfval(inc, "interest_expense")
    interest_inc = sfval(inc, "interest_income")
    if interest_exp and interest_exp > 0 and op_income is not None:
        return op_income / interest_exp, False
    if interest_inc is not None and interest_inc > 0 and (interest_exp is None or interest_exp == 0):
        return None, True
    other = sfval(inc, "other_income_expense")
    if other is not None and other > 0 and (interest_exp is None or interest_exp == 0):
        return None, True
    return None, False

def calc_interest_coverage(inc):
    """Legacy vX interest coverage."""
    op_income    = fval(inc, "operating_income_loss")
    interest_exp = fval(inc, "interest_expense_operating")
    if interest_exp and interest_exp > 0 and op_income is not None:
        return op_income / interest_exp, False
    nonop = fval(inc, "nonoperating_income_loss")
    if nonop is not None and nonop > 0:
        return None, True
    return None, False

@st.cache_data(ttl=86400)
def get_sp500_tickers():
    try:
        headers  = {"User-Agent": "Mozilla/5.0 (compatible; VoskuilFP/1.0)"}
        response = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers=headers, timeout=10)
        tables   = pd.read_html(StringIO(response.text))
        tickers  = tables[0]['Symbol'].str.replace('.', '-', regex=False).tolist()
        return tickers
    except Exception as e:
        st.error(f"Could not fetch S&P 500 list: {e}")
        return []

def fetch_score_data(ticker):
    """New v1 endpoints with vX fallback for Stocks Starter plan."""
    try:
        det_data   = poly_get(f"/v3/reference/tickers/{ticker}")
        det        = det_data.get("results", {}) if det_data else {}
        market_cap = safe_float(det.get("market_cap"))
        shares     = safe_float(det.get("weighted_shares_outstanding"))

        price_data = poly_get(f"/v2/aggs/ticker/{ticker}/prev")
        price = None
        try:
            price = float(price_data["results"][0]["c"])
        except (KeyError, TypeError, IndexError):
            pass

        fin_params = {
            "tickers": ticker, "timeframe": "annual",
            "limit": 1, "sort": "period_end.desc",
        }
        NEW_BASE = "/stocks/financials/v1"

        inc_data = poly_get(f"{NEW_BASE}/income-statements",    fin_params)
        cf_data  = poly_get(f"{NEW_BASE}/cash-flow-statements", fin_params)
        bs_data  = poly_get(f"{NEW_BASE}/balance-sheets",       fin_params)

        use_new_api = (
            inc_data and inc_data.get("results") and
            cf_data  and cf_data.get("results")  and
            bs_data  and bs_data.get("results")
        )

        if use_new_api:
            inc = inc_data["results"][0]
            cf  = cf_data["results"][0]
            bs  = bs_data["results"][0]

            op_cf  = sfval(cf, "net_cash_from_operating_activities")
            inv_cf = sfval(cf, "net_cash_from_investing_activities")
            fcf    = (op_cf + inv_cf) if (op_cf is not None and inv_cf is not None) else None

            if fcf is None or fcf <= 0:
                return None

            fcf_yield    = (fcf / market_cap) if (market_cap and market_cap > 0) else None
            gross_profit = sfval(inc, "gross_profit")
            revenue      = sfval(inc, "revenue")
            gross_margin = (gross_profit / revenue) if (gross_profit and revenue and revenue > 0) else None
            net_income   = sfval(inc, "net_income_loss_attributable_common_shareholders") or sfval(cf, "net_income")
            total_assets = sfval(bs, "total_assets")
            current_liab = sfval(bs, "total_current_liabilities")
            invested_cap = (total_assets - current_liab) if (total_assets and current_liab) else None
            roic         = (net_income / invested_cap) if (net_income and invested_cap and invested_cap != 0) else None
            long_term_debt = sfval(bs, "long_term_debt") or sfval(bs, "long_term_debt_and_capital_lease_obligations") or sfval(bs, "total_noncurrent_liabilities")
            debt_to_fcf  = (long_term_debt / fcf) if (long_term_debt is not None and fcf > 0) else None
            interest_cov, is_net_creditor = calc_interest_coverage_new(inc, cf)
            dna          = sfval(cf, "depreciation_depletion_and_amortization") or (op_cf - net_income if op_cf and net_income else 0)
            capex_abs    = abs(inv_cf) if inv_cf else 0
            owner_earn   = (net_income + (dna or 0) - capex_abs) if net_income is not None else None
            poe          = (price / (owner_earn / shares)) if (owner_earn and owner_earn > 0 and shares and price) else None
            div_raw      = sfval(cf, "dividends")
            div_yield    = (abs(div_raw) / market_cap) if (div_raw and market_cap and market_cap > 0) else None

        else:
            # Legacy vX fallback
            fin_data = poly_get("/vX/reference/financials", {
                "ticker": ticker, "timeframe": "annual", "limit": 1,
                "order": "desc", "sort": "period_of_report_date",
            })
            if not fin_data or not fin_data.get("results"):
                return None

            f   = fin_data["results"][0]["financials"]
            inc_vx = f.get("income_statement",    {})
            cf_vx  = f.get("cash_flow_statement", {})
            bs_vx  = f.get("balance_sheet",       {})

            op_cf  = fval(cf_vx, "net_cash_flow_from_operating_activities")
            inv_cf = fval(cf_vx, "net_cash_flow_from_investing_activities")
            fcf    = (op_cf + inv_cf) if (op_cf is not None and inv_cf is not None) else None
            if fcf is None or fcf <= 0:
                return None

            fcf_yield    = (fcf / market_cap) if (market_cap and market_cap > 0) else None
            gross_profit = fval(inc_vx, "gross_profit")
            revenues     = fval(inc_vx, "revenues")
            gross_margin = (gross_profit / revenues) if (gross_profit and revenues and revenues > 0) else None
            net_income   = fval(inc_vx, "net_income_loss")
            total_assets = fval(bs_vx, "assets")
            current_liab = fval(bs_vx, "current_liabilities")
            invested_cap = (total_assets - current_liab) if (total_assets and current_liab) else None
            roic         = (net_income / invested_cap) if (net_income and invested_cap and invested_cap != 0) else None
            long_term_debt = fval(bs_vx, "long_term_debt") or fval(bs_vx, "noncurrent_liabilities")
            debt_to_fcf  = (long_term_debt / fcf) if (long_term_debt is not None and fcf > 0) else None
            interest_cov, is_net_creditor = calc_interest_coverage(inc_vx)
            dna_proxy    = (op_cf - net_income) if (op_cf and net_income) else None
            capex_abs    = abs(inv_cf) if inv_cf else 0
            owner_earn   = (net_income + (dna_proxy or 0) - capex_abs) if net_income is not None else None
            poe          = (price / (owner_earn / shares)) if (owner_earn and owner_earn > 0 and shares and price) else None
            div_ps       = fval(inc_vx, "common_stock_dividends")
            div_yield    = (div_ps / price) if (div_ps and price and price > 0) else None

        return {
            "ticker": ticker, "name": det.get("name", ticker),
            "sector": det.get("sic_description", "N/A"), "price": price,
            "market_cap": market_cap, "fcf_yield": fcf_yield, "roic": roic,
            "debt_to_fcf": debt_to_fcf, "interest_coverage": interest_cov,
            "is_net_creditor": is_net_creditor, "gross_margin": gross_margin,
            "price_owner_earn": poe, "dividend_yield": div_yield,
        }
    except Exception:
        return None

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
    ic = data.get('interest_coverage') or 0
    is_nc = data.get('is_net_creditor', False)
    if debt_fcf is not None:
        if debt_fcf < THRESHOLDS['debt_fcf_safe']:        pts += weights["Debt / FCF"]
        elif debt_fcf < THRESHOLDS['debt_fcf_warning']:   pts += round(weights["Debt / FCF"] * 0.50)
        elif ic >= THRESHOLDS['interest_coverage_safe'] or is_nc:
                                                          pts += round(weights["Debt / FCF"] * 0.50)
    gm = data.get('gross_margin')
    if gm:
        if gm >= THRESHOLDS['gross_margin_great']:   pts += weights["Gross Margin"]
        elif gm >= THRESHOLDS['gross_margin_good']:  pts += round(weights["Gross Margin"] * 0.67)
        else:                                        pts += round(weights["Gross Margin"] * 0.20)
    ic_val = data.get('interest_coverage')
    if is_nc:
        pts += weights["Interest Coverage"]
    elif ic_val:
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

DEFAULT_ACTION_THRESHOLDS = {
    "sell_score_floor":  45,
    "buy_score_floor":   65,
    "buy_poe_max":       25.0,
    "bargain_poe":       15.0,
    "sell_debt_max":      5.0,
    "sell_ic_min":        2.5,
}

def buffett_action(score, data):
    """Buffett-style Buy / Hold / Sell signal.
    Reads thresholds from session state (set on dashboard) with fallback to defaults.
    """
    if score is None or data is None:
        return "—", "#888888", ""

    t = st.session_state.get("action_thresholds", DEFAULT_ACTION_THRESHOLDS)
    sell_floor  = t["sell_score_floor"]
    buy_floor   = t["buy_score_floor"]
    buy_poe_max = t["buy_poe_max"]
    bargain_poe = t["bargain_poe"]
    sell_debt   = t["sell_debt_max"]
    sell_ic     = t["sell_ic_min"]

    poe      = data.get("price_owner_earn")
    debt_fcf = data.get("debt_to_fcf")
    ic       = data.get("interest_coverage") or 0
    is_nc    = data.get("is_net_creditor", False)
    roic     = data.get("roic")

    if score < sell_floor:
        return "SELL", "#e74c3c", f"Fundamentals below conviction threshold (score < {sell_floor})"
    if debt_fcf is not None and debt_fcf > sell_debt and not is_nc and ic < sell_ic:
        return "SELL", "#e74c3c", f"Debt trap: {debt_fcf:.1f}x Debt/FCF, {ic:.1f}x coverage"
    if roic is not None and roic < 0:
        return "SELL", "#e74c3c", "Negative ROIC — destroying capital"
    if score >= buy_floor:
        price_ok = poe is not None and poe <= buy_poe_max
        debt_ok  = debt_fcf is None or debt_fcf < sell_debt or is_nc
        if price_ok and debt_ok:
            label = "bargain" if (poe is not None and poe <= bargain_poe) else "fair"
            return "BUY", "#2ecc71", f"Quality business at {label} price ({poe:.1f}x P/OE)"
    if score >= buy_floor:
        if poe is not None and poe > buy_poe_max:
            return "HOLD", "#3498db", f"Quality but stretched ({poe:.1f}x P/OE)"
        if debt_fcf is not None and debt_fcf >= sell_debt and not is_nc:
            return "HOLD", "#3498db", f"Quality but elevated debt ({debt_fcf:.1f}x)"
        return "HOLD", "#3498db", "Quality business — monitor for entry"
    return "PASS", "#888888", "Adequate fundamentals — not a concentrated bet candidate"

st.title("📡 Market Screener")
st.caption("Scans the S&P 500 through the Voskuil Owner's Framework. Surfaces the top concentrated opportunities.")
st.info("**How this works:** Fetches fundamentals from Polygon.io SEC filings, scores each company on the 6-metric Owner's Framework, and surfaces the top results. Negative FCF companies are automatically eliminated.")
st.divider()

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
        w_poe  = st.slider("Price / Owner Earnings", 0, 60, DEFAULT_WEIGHTS["Price / Owner Earnings"], step=5)
    weights = {
        "FCF Yield": w_fcf, "ROIC": w_roic, "Debt / FCF": w_debt,
        "Gross Margin": w_gm, "Interest Coverage": w_ic, "Price / Owner Earnings": w_poe,
    }
    total_weight = sum(weights.values())
    if total_weight == 100:   st.success(f"✅ Total: {total_weight} / 100")
    elif total_weight < 100:  st.warning(f"⚠️ Total: {total_weight} / 100 — {100 - total_weight} pts unallocated")
    else:                     st.error(f"❌ Total: {total_weight} / 100 — over by {total_weight - 100} pts.")

col1, col2, col3 = st.columns(3)
with col1:
    top_n = st.number_input("Top results to show", min_value=5, max_value=50, value=15, step=5)
with col2:
    sector_filter = st.selectbox("Filter by sector", [
        "All Sectors", "Technology", "Healthcare", "Financials",
        "Consumer Staples", "Consumer Discretionary", "Industrials",
        "Energy", "Utilities", "Real Estate", "Materials", "Communication Services"
    ])
with col3:
    max_scan = st.number_input("Max stocks to scan", min_value=10, max_value=500, value=100, step=10)
    min_div  = st.checkbox("Dividend payers only", value=False)

st.divider()
run_screen = st.button("🚀 Run Screen", type="primary", use_container_width=True)

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
    progress_bar = st.progress(0)
    status_text  = st.empty()
    results      = []

    for i, ticker in enumerate(tickers_to_scan):
        pct = (i + 1) / total_tickers
        progress_bar.progress(pct)
        status_text.markdown(f"⏳ Analyzing **{ticker}** — {i+1} of {total_tickers} ({int(pct*100)}%) — {len(results)} candidates found")
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
        time.sleep(0.1)

    progress_bar.progress(1.0)
    status_text.markdown(f"✅ Scan complete — {len(results)} companies passed the FCF filter.")

    if not results:
        st.warning("No results found. Try removing filters or increasing max stocks to scan.")
        st.stop()

    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('score', ascending=False).head(top_n).reset_index(drop=True)

    st.divider()
    st.markdown(f"## 🏆 Top {min(top_n, len(results_df))} Concentrated Opportunities")
    st.caption("Ranked by Voskuil Owner's Framework score.")

    def fmt(val, fmt_type):
        if val is None or (isinstance(val, float) and pd.isna(val)): return "N/A"
        if fmt_type == "pct":   return f"{val:.1%}"
        if fmt_type == "ratio": return f"{val:.1f}x"
        return str(val)

    for rank, row in results_df.iterrows():
        score       = int(row['score'])
        label, icon = score_to_label(score)
        signal, sig_color, sig_reason = buffett_action(score, row.to_dict())
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
                if signal != "—":
                    st.markdown(
                        f"<span style='font-weight:bold; color:{sig_color}'>{signal}</span>",
                        unsafe_allow_html=True
                    )
                    st.caption(sig_reason)
            with c4: st.metric("FCF Yield",    fmt(row.get('fcf_yield'),       "pct"))
            with c5: st.metric("ROIC",         fmt(row.get('roic'),            "pct"))
            with c6: st.metric("Gross Margin", fmt(row.get('gross_margin'),    "pct"))
            with c7: st.metric("Debt/FCF",     fmt(row.get('debt_to_fcf'),     "ratio"))
            with c8: st.metric("P/OE",         fmt(row.get('price_owner_earn'),"ratio"))
            div = row.get('dividend_yield')
            if div: st.caption(f"💰 Dividend Yield: {div:.2%}")
            ic_note = "Net Creditor" if row.get('is_net_creditor') else ""
            if ic_note: st.caption(f"✨ {ic_note}")
            st.markdown(f"[🔍 Deep Dive in Equity Scout]({APP_URL}/equity_scout?ticker={row['ticker']}&auto=1)")
            st.divider()

    st.markdown("### 📊 Screen Summary")
    s1, s2, s3, s4 = st.columns(4)
    with s1: st.metric("Scanned",           total_tickers)
    with s2: st.metric("Passed FCF Filter", len(results))
    with s3: st.metric("Avg Score",         f"{results_df['score'].mean():.0f}")
    with s4: st.metric("Strong Buys (80+)", len(results_df[results_df['score'] >= 80]))

    st.markdown("### 💾 Export Results")
    export_df = results_df[['ticker','name','sector','score','fcf_yield','roic','gross_margin',
                              'debt_to_fcf','interest_coverage','price_owner_earn','dividend_yield','price','market_cap']].copy()
    export_df.columns = ['Ticker','Name','Sector','Score','FCF Yield','ROIC','Gross Margin',
                          'Debt/FCF','Interest Coverage','Price/Owner Earnings','Dividend Yield','Price','Market Cap']
    st.download_button(label="⬇️ Download Results as CSV", data=export_df.to_csv(index=False),
                        file_name="voskuil_screen_results.csv", mime="text/csv")

else:
    st.markdown("""
    ### What this screener does

    1. **Loads S&P 500 companies** from Wikipedia
    2. **Eliminates** companies with negative Free Cash Flow
    3. **Scores** remaining companies on the 6-metric Owner's Framework
    4. **Returns top results** ranked by conviction score

    ### What's new
    - **Net Creditor detection** — companies that earn more interest than they pay score full points on Interest Coverage
    - **Long-term debt** used instead of total noncurrent liabilities for more accurate Debt/FCF
    - **Updated weights** — Price/Owner Earnings now 25 pts (was 15), FCF Yield 20 pts (was 30)

    ---
    **Score guide:** 🟢 80+ Strong Buy · 🟡 65-79 Watch · 🟠 45-64 Caution · 🔴 <45 Avoid

    *Data sourced from Polygon.io SEC filings.*
    """)
