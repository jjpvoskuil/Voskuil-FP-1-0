import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
 
# ─────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(page_title="Equity Scout | Voskuil FP", layout="wide")
 
# ─────────────────────────────────────────────
# DEFAULT WEIGHTS
# Must add up to 100.
# ─────────────────────────────────────────────
DEFAULT_WEIGHTS = {
    "FCF Yield":            30,
    "ROIC":                 10,
    "Debt / FCF":           20,
    "Gross Margin":         15,
    "Interest Coverage":    10,
    "Price / Owner Earnings": 15,
}
 
# ─────────────────────────────────────────────
# SCORING THRESHOLDS
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
    "poe_bargain":              15.0,   # Price/Owner Earnings under 15x = bargain
    "poe_fair":                 25.0,   # Under 25x = fair value
    "poe_stretched":            35.0,   # Under 35x = stretched
    "monthly_income_target":    8000,
}
 
# ─────────────────────────────────────────────
# DATA FETCHER
# ─────────────────────────────────────────────
@st.cache_data(ttl=3600)
def fetch_fundamentals(ticker: str) -> dict:
    try:
        stock      = yf.Ticker(ticker)
        info       = stock.info
        cashflow   = stock.cashflow
        financials = stock.financials
        balance    = stock.balance_sheet
 
        # Free Cash Flow
        try:
            op_cf      = cashflow.loc['Operating Cash Flow'].iloc[0]
            capex      = cashflow.loc['Capital Expenditure'].iloc[0]
            fcf        = op_cf + capex
            fcf_1yr    = op_cf + cashflow.loc['Capital Expenditure'].iloc[1] if len(cashflow.columns) > 1 else None
            fcf_growth = ((fcf / fcf_1yr) - 1) if fcf_1yr and fcf_1yr != 0 else None
        except Exception:
            fcf, fcf_growth = None, None
 
        # Market Cap & FCF Yield
        market_cap = info.get('marketCap')
        fcf_yield  = (fcf / market_cap) if (fcf and market_cap) else None
 
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
            debt_to_fcf = (total_debt / fcf) if (fcf and fcf > 0) else None
        except Exception:
            total_debt, debt_to_fcf = None, None
 
        # Interest Coverage
        try:
            ebit              = financials.loc['EBIT'].iloc[0]
            interest_expense  = abs(financials.loc['Interest Expense'].iloc[0])
            interest_coverage = ebit / interest_expense if interest_expense != 0 else None
        except Exception:
            interest_coverage = None
 
        # Gross Margin
        gross_margin = info.get('grossMargins')
 
        # Owner Earnings & Price/Owner Earnings
        try:
            dna        = cashflow.loc['Depreciation And Amortization'].iloc[0]
            capex_val  = abs(cashflow.loc['Capital Expenditure'].iloc[0])
            owner_earn = net_income + dna - capex_val
            shares     = info.get('sharesOutstanding')
            price      = info.get('currentPrice') or info.get('regularMarketPrice')
            poe        = (price / (owner_earn / shares)) if (owner_earn and shares and owner_earn > 0 and price) else None
        except Exception:
            owner_earn, poe = None, None
 
        return {
            "name":              info.get('longName', ticker),
            "sector":            info.get('sector', 'N/A'),
            "market_cap":        market_cap,
            "price":             info.get('currentPrice') or info.get('regularMarketPrice'),
            "pe_ratio":          info.get('trailingPE'),
            "fcf":               fcf,
            "fcf_yield":         fcf_yield,
            "fcf_growth":        fcf_growth,
            "roic":              roic,
            "debt_to_fcf":       debt_to_fcf,
            "total_debt":        total_debt,
            "interest_coverage": interest_coverage,
            "gross_margin":      gross_margin,
            "owner_earnings":    owner_earn,
            "price_owner_earn":  poe,
            "dividend_yield":    info.get('dividendYield'),
            "description":       info.get('longBusinessSummary', '')[:400] + '...' if info.get('longBusinessSummary') else '',
        }
 
    except Exception as e:
        st.error(f"Could not fetch data for **{ticker}**: {e}")
        return {}
 
 
# ─────────────────────────────────────────────
# SCORING ENGINE
# ─────────────────────────────────────────────
def score_stock(data: dict, weights: dict) -> tuple[int, list[dict]]:
    criteria = []
 
    # ── 1. FCF Yield ────────────────────────────────────────────────────
    max_pts   = weights["FCF Yield"]
    fcf_yield = data.get('fcf_yield')
    if fcf_yield is not None:
        if fcf_yield >= THRESHOLDS['fcf_yield_great']:
            pts, verdict = max_pts, "Excellent"
        elif fcf_yield >= THRESHOLDS['fcf_yield_good']:
            pts, verdict = round(max_pts * 0.60), "Good"
        elif fcf_yield > 0:
            pts, verdict = round(max_pts * 0.15), "Weak"
        else:
            pts, verdict = 0, "Negative FCF"
    else:
        pts, verdict = 0, "No Data"
    criteria.append({
        "name": "Free Cash Flow Yield",
        "value": f"{fcf_yield:.1%}" if fcf_yield is not None else "N/A",
        "points_earned": pts, "points_max": max_pts, "verdict": verdict,
        "note": "What you earn as an owner relative to price. Buffett wants real cash, not accounting earnings."
    })
 
    # ── 2. ROIC ─────────────────────────────────────────────────────────
    max_pts = weights["ROIC"]
    roic    = data.get('roic')
    if roic is not None:
        if roic >= THRESHOLDS['roic_great']:
            pts, verdict = max_pts, "Exceptional"
        elif roic >= THRESHOLDS['roic_good']:
            pts, verdict = round(max_pts * 0.60), "Strong"
        elif roic > 0:
            pts, verdict = round(max_pts * 0.20), "Below Average"
        else:
            pts, verdict = 0, "Destroying Capital"
    else:
        pts, verdict = 0, "No Data"
    criteria.append({
        "name": "Return on Invested Capital (ROIC)",
        "value": f"{roic:.1%}" if roic is not None else "N/A",
        "points_earned": pts, "points_max": max_pts, "verdict": verdict,
        "note": "Munger: 'Show me the incentives and I'll show you the outcome.' ROIC shows if management deploys capital wisely."
    })
 
    # ── 3. Debt / FCF ────────────────────────────────────────────────────
    max_pts  = weights["Debt / FCF"]
    debt_fcf = data.get('debt_to_fcf')
    ic       = data.get('interest_coverage') or 0
    if debt_fcf is not None:
        if debt_fcf < THRESHOLDS['debt_fcf_safe']:
            pts, verdict = max_pts, "Fortress"
        elif debt_fcf < THRESHOLDS['debt_fcf_warning']:
            pts, verdict = round(max_pts * 0.50), "Manageable"
        elif ic >= THRESHOLDS['interest_coverage_safe']:
            pts, verdict = round(max_pts * 0.50), "High Debt, Well Covered"
        else:
            pts, verdict = 0, "Overleveraged"
    else:
        pts, verdict = 0, "No Data"
    criteria.append({
        "name": "Debt / Free Cash Flow",
        "value": f"{debt_fcf:.1f}x" if debt_fcf is not None else "N/A",
        "points_earned": pts, "points_max": max_pts, "verdict": verdict,
        "note": "Years of FCF needed to pay off all debt. In a credit crunch, this is the survival metric."
    })
 
    # ── 4. Gross Margin ──────────────────────────────────────────────────
    max_pts = weights["Gross Margin"]
    gm      = data.get('gross_margin')
    if gm is not None:
        if gm >= THRESHOLDS['gross_margin_great']:
            pts, verdict = max_pts, "Wide Moat"
        elif gm >= THRESHOLDS['gross_margin_good']:
            pts, verdict = round(max_pts * 0.67), "Solid Moat"
        else:
            pts, verdict = round(max_pts * 0.20), "Commodity Risk"
    else:
        pts, verdict = 0, "No Data"
    criteria.append({
        "name": "Gross Margin (Pricing Power)",
        "value": f"{gm:.1%}" if gm is not None else "N/A",
        "points_earned": pts, "points_max": max_pts, "verdict": verdict,
        "note": "Buffett's favorite moat signal. Can the company raise prices without losing customers?"
    })
 
    # ── 5. Interest Coverage ─────────────────────────────────────────────
    max_pts = weights["Interest Coverage"]
    ic_val  = data.get('interest_coverage')
    if ic_val is not None:
        if ic_val >= THRESHOLDS['interest_coverage_safe']:
            pts, verdict = max_pts, "Safe"
        elif ic_val >= 2.5:
            pts, verdict = round(max_pts * 0.50), "Adequate"
        elif ic_val > 0:
            pts, verdict = round(max_pts * 0.15), "Tight"
        else:
            pts, verdict = 0, "Danger"
    else:
        pts, verdict = 0, "No Data"
    criteria.append({
        "name": "Interest Coverage Ratio",
        "value": f"{ic_val:.1f}x" if ic_val is not None else "N/A",
        "points_earned": pts, "points_max": max_pts, "verdict": verdict,
        "note": "How many times can earnings cover interest payments? Critical in a rising-rate Long Squeeze environment."
    })
 
    # ── 6. Price / Owner Earnings ────────────────────────────────────────
    max_pts = weights["Price / Owner Earnings"]
    poe     = data.get('price_owner_earn')
    if poe is not None:
        if poe <= THRESHOLDS['poe_bargain']:
            pts, verdict = max_pts, "Bargain"
        elif poe <= THRESHOLDS['poe_fair']:
            pts, verdict = round(max_pts * 0.67), "Fair Value"
        elif poe <= THRESHOLDS['poe_stretched']:
            pts, verdict = round(max_pts * 0.25), "Stretched"
        else:
            pts, verdict = 0, "Expensive"
    else:
        pts, verdict = 0, "No Data"
    criteria.append({
        "name": "Price / Owner Earnings",
        "value": f"{poe:.1f}x" if poe is not None else "N/A",
        "points_earned": pts, "points_max": max_pts, "verdict": verdict,
        "note": "Buffett's valuation test. What are you paying per dollar of real owner earnings? Under 15x is a bargain."
    })
 
    total = sum(c['points_earned'] for c in criteria)
    return total, criteria
 
 
def score_to_verdict(score: int) -> tuple[str, str]:
    if score >= 80:
        return "Strong Buy", "#2ecc71"
    elif score >= 65:
        return "Watch Closely", "#f39c12"
    elif score >= 45:
        return "Proceed with Caution", "#e67e22"
    else:
        return "Avoid", "#e74c3c"
 
 
# ─────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────
st.title("🔍 Equity Scout")
st.caption("Concentrated, Buffett-style fundamental analysis. One business at a time.")
st.markdown("""
> *"Price is what you pay. Value is what you get."* — Warren Buffett
""")
st.divider()
 
# ── Scoring Weights ───────────────────────────────────────────────────────
with st.expander("⚙️ Customize Scoring Weights", expanded=False):
    st.caption("Adjust how much each metric contributes to the total score. Must add up to 100.")
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
 
# ── Ticker Input ──────────────────────────────────────────────────────────
col_input, col_btn = st.columns([3, 1])
with col_input:
    ticker_input = st.text_input(
        "Enter a stock ticker",
        placeholder="e.g. ABBV, MSFT, KO, NVDA",
        label_visibility="collapsed"
    ).strip().upper()
with col_btn:
    analyze = st.button("🔎 Analyze", use_container_width=True, type="primary")
 
with st.expander("💼 Position Sizing Context (optional)"):
    position_size = st.number_input(
        "How much are you considering investing? ($)",
        min_value=0, value=100000, step=10000, format="%d"
    )
 
# ── Analysis ──────────────────────────────────────────────────────────────
if analyze and ticker_input:
 
    if total_weight != 100:
        st.warning(f"Weights add up to {total_weight}, not 100. Adjust sliders for accurate scores.")
 
    with st.spinner(f"Fetching fundamentals for **{ticker_input}**..."):
        data = fetch_fundamentals(ticker_input)
 
    if not data:
        st.stop()
 
    score, criteria      = score_stock(data, weights)
    verdict_label, verdict_color = score_to_verdict(score)
 
    st.markdown(f"## {data.get('name', ticker_input)}")
    st.caption(
        f"{data.get('sector', '')}  ·  "
        f"${data.get('price', 0):,.2f} per share  ·  "
        f"Market Cap: ${data.get('market_cap', 0)/1e9:.1f}B"
    )
    if data.get('description'):
        st.markdown(f"*{data['description']}*")
 
    st.divider()
 
    left, right = st.columns([1, 2])
 
    with left:
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=score,
            domain={'x': [0, 1], 'y': [0, 1]},
            title={'text': "Conviction Score", 'font': {'size': 16}},
            gauge={
                'axis': {'range': [0, 100], 'tickwidth': 1},
                'bar': {'color': verdict_color},
                'steps': [
                    {'range': [0, 45],   'color': "#fadbd8"},
                    {'range': [45, 65],  'color': "#fdebd0"},
                    {'range': [65, 80],  'color': "#fef9e7"},
                    {'range': [80, 100], 'color': "#eafaf1"},
                ],
                'threshold': {
                    'line': {'color': verdict_color, 'width': 4},
                    'thickness': 0.75, 'value': score
                }
            }
        ))
        fig.update_layout(height=260, margin=dict(t=30, b=0, l=20, r=20))
        st.plotly_chart(fig, use_container_width=True)
 
        st.markdown(
            f"<div style='text-align:center; font-size:1.4em; font-weight:bold; color:{verdict_color}'>"
            f"{verdict_label}</div>",
            unsafe_allow_html=True
        )
        st.markdown("**Active Weights**")
        for k, v in weights.items():
            st.caption(f"{k}: {v} pts")
 
    with right:
        st.markdown("### Owner's Scorecard")
        for c in criteria:
            earned  = c['points_earned']
            maximum = c['points_max']
            pct     = earned / maximum if maximum > 0 else 0
            if pct >= 0.8:
                bar_color, icon = "#2ecc71", "✅"
            elif pct >= 0.5:
                bar_color, icon = "#f39c12", "⚠️"
            else:
                bar_color, icon = "#e74c3c", "❌"
            st.markdown(
                f"{icon} **{c['name']}** — `{c['value']}` "
                f"&nbsp;&nbsp;<span style='color:{bar_color}'>{c['verdict']}</span> "
                f"&nbsp;·&nbsp; {earned}/{maximum} pts",
                unsafe_allow_html=True
            )
            st.progress(pct)
            st.caption(c['note'])
 
    st.divider()
 
    st.markdown("### 📊 Key Metrics at a Glance")
    m1, m2, m3, m4 = st.columns(4)
 
    def fmt_val(val, fmt="money"):
        if val is None: return "N/A"
        if fmt == "money":  return f"${val/1e9:.2f}B" if abs(val) >= 1e9 else f"${val/1e6:.1f}M"
        if fmt == "pct":    return f"{val:.1%}"
        if fmt == "ratio":  return f"{val:.1f}x"
        return str(val)
 
    with m1:
        st.metric("Free Cash Flow",      fmt_val(data.get('fcf')))
        st.metric("Owner Earnings",      fmt_val(data.get('owner_earnings')))
    with m2:
        st.metric("FCF Yield",           fmt_val(data.get('fcf_yield'), "pct"))
        st.metric("FCF Growth (1yr)",    fmt_val(data.get('fcf_growth'), "pct"))
    with m3:
        st.metric("ROIC",                fmt_val(data.get('roic'), "pct"))
        st.metric("Gross Margin",        fmt_val(data.get('gross_margin'), "pct"))
    with m4:
        st.metric("Debt / FCF",          fmt_val(data.get('debt_to_fcf'), "ratio"))
        st.metric("Price/Owner Earnings",fmt_val(data.get('price_owner_earn'), "ratio"))
 
    st.divider()
 
    st.markdown("### 💰 Income Potential at Your Position Size")
    div_yield = data.get('dividend_yield')
    if div_yield and position_size > 0:
        annual_income  = position_size * div_yield
        monthly_income = annual_income / 12
        target         = THRESHOLDS['monthly_income_target']
        pct_of_target  = monthly_income / target
        ic1, ic2, ic3  = st.columns(3)
        with ic1: st.metric("Dividend Yield",      f"{div_yield:.2%}")
        with ic2: st.metric("Est. Annual Income",  f"${annual_income:,.0f}")
        with ic3: st.metric("Est. Monthly Income", f"${monthly_income:,.0f}",
                             delta=f"{pct_of_target:.0%} of your $8K/mo target")
        st.progress(min(pct_of_target, 1.0))
    else:
        st.info("No dividend yield data available. This may be a pure growth compounder.")
 
    st.divider()
 
    st.markdown("### 📝 The Verdict")
    strengths  = [c['name'] for c in criteria if c['points_max'] > 0 and c['points_earned'] / c['points_max'] >= 0.8]
    weaknesses = [c['name'] for c in criteria if c['points_max'] > 0 and c['points_earned'] / c['points_max'] < 0.5 and c['value'] != 'N/A']
 
    verdict_text = f"**{data.get('name', ticker_input)}** scores **{score}/100** on the Voskuil Owner's Framework. "
    if strengths:
        verdict_text += f"Its strongest qualities are {', '.join(strengths)}. "
    if weaknesses:
        verdict_text += f"Areas of concern: {', '.join(weaknesses)}. "
 
    if score >= 80:
        verdict_text += "This business passes the 'Would Buffett hold it for 10 years?' test. Consider a concentrated position."
    elif score >= 65:
        verdict_text += "Worth watching closely. Strong in some areas but not a slam dunk. Look for a better entry price."
    elif score >= 45:
        verdict_text += "Real weaknesses in the fundamentals. Not a fortress business. Proceed only with a significant margin of safety."
    else:
        verdict_text += "Does not meet the criteria for a concentrated bet. Risk of permanent capital loss outweighs the upside."
 
    st.markdown(verdict_text)
    st.info(
        "⚠️ **Macro Overlay Reminder:** In a 'Long Squeeze' environment, prioritize companies with low debt, "
        "strong FCF, and pricing power. Your $8K/month withdrawal target requires this portfolio to be "
        "recession-resistant, not just return-maximizing."
    )
 
elif analyze and not ticker_input:
    st.warning("Please enter a ticker symbol to analyze.")
 
else:
    st.markdown("""
    ### How this works
 
    Enter any stock ticker above and get an **Owner's Report** scored on six Buffett fundamentals.
    Use the **Customize Scoring Weights** expander to adjust how much each metric matters to you.
 
    | Metric | Default Weight | What it measures |
    |--------|---------------|-----------------|
    | Free Cash Flow Yield | 30 pts | Real owner earnings relative to price |
    | ROIC | 10 pts | How wisely management deploys your capital |
    | Debt / FCF | 20 pts | Survival capacity in a credit crunch |
    | Gross Margin | 15 pts | Pricing power and moat durability |
    | Interest Coverage | 10 pts | Ability to service debt in a Long Squeeze |
    | Price / Owner Earnings | 15 pts | What you're paying per dollar of real earnings |
 
    **Score guide:** 80-100 = Strong Buy · 65-79 = Watch · 45-64 = Caution · <45 = Avoid
 
    ---
    *Data sourced from Yahoo Finance via yfinance. For proof-of-concept use.*
    """)
