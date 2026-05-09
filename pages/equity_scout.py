import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go

# ─────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(page_title="Equity Scout | Voskuil FP", layout="wide")

# ─────────────────────────────────────────────
# SCORING THRESHOLDS
# Adjust these to tune the philosophy.
# All thresholds reflect Buffett/Munger owner-earnings logic.
# ─────────────────────────────────────────────
THRESHOLDS = {
    "fcf_yield_good":        0.04,   # FCF Yield >= 4% is attractive
    "fcf_yield_great":       0.06,   # FCF Yield >= 6% is excellent
    "roic_good":             0.12,   # ROIC >= 12% = durable competitive advantage
    "roic_great":            0.20,   # ROIC >= 20% = exceptional compounder
    "debt_fcf_safe":         3.0,    # Debt payable in < 3 years of FCF = safe
    "debt_fcf_warning":      5.0,    # Debt payable in 3-5 years = caution
    "interest_coverage_safe":5.0,    # EBIT covers interest 5x = safe
    "gross_margin_good":     0.40,   # 40%+ gross margin = pricing power
    "gross_margin_great":    0.60,   # 60%+ = exceptional moat
    "monthly_income_target": 8000,   # Your $8,000/month withdrawal target
}

# ─────────────────────────────────────────────
# DATA FETCHER
# ─────────────────────────────────────────────
@st.cache_data(ttl=3600)  # Cache for 1 hour so repeated runs don't re-fetch
def fetch_fundamentals(ticker: str) -> dict:
    """
    Pull all raw fundamental data from yfinance.
    Returns a flat dict of raw values. None = data not available.
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        cashflow = stock.cashflow        # Annual cash flow statement
        financials = stock.financials    # Annual income statement
        balance = stock.balance_sheet    # Annual balance sheet

        # ── Free Cash Flow ──────────────────────────────────────────────
        # Operating Cash Flow minus Capital Expenditures = true owner earnings
        try:
            op_cf   = cashflow.loc['Operating Cash Flow'].iloc[0]
            capex   = cashflow.loc['Capital Expenditure'].iloc[0]  # Usually negative
            fcf     = op_cf + capex   # capex is negative so this subtracts it
            fcf_1yr = op_cf + cashflow.loc['Capital Expenditure'].iloc[1] if len(cashflow.columns) > 1 else None
            fcf_growth = ((fcf / fcf_1yr) - 1) if fcf_1yr and fcf_1yr != 0 else None
        except Exception:
            fcf, fcf_growth = None, None

        # ── Market Cap & FCF Yield ───────────────────────────────────────
        market_cap = info.get('marketCap')
        fcf_yield  = (fcf / market_cap) if (fcf and market_cap) else None

        # ── ROIC (Return on Invested Capital) ───────────────────────────
        # ROIC = EBIT(1-tax rate) / Invested Capital
        # Simplified: Net Income / (Total Assets - Current Liabilities)
        try:
            net_income      = financials.loc['Net Income'].iloc[0]
            total_assets    = balance.loc['Total Assets'].iloc[0]
            current_liab    = balance.loc['Current Liabilities'].iloc[0]
            invested_cap    = total_assets - current_liab
            roic            = net_income / invested_cap if invested_cap != 0 else None
        except Exception:
            roic = None

        # ── Debt / FCF Ratio ────────────────────────────────────────────
        # How many years of free cash flow to pay off all debt?
        try:
            total_debt = balance.loc['Total Debt'].iloc[0]
            debt_to_fcf = (total_debt / fcf) if (fcf and fcf > 0) else None
        except Exception:
            total_debt, debt_to_fcf = None, None

        # ── Interest Coverage ───────────────────────────────────────────
        # EBIT / Interest Expense. Higher = safer debt load.
        try:
            ebit              = financials.loc['EBIT'].iloc[0]
            interest_expense  = abs(financials.loc['Interest Expense'].iloc[0])
            interest_coverage = ebit / interest_expense if interest_expense != 0 else None
        except Exception:
            interest_coverage = None

        # ── Gross Margin ────────────────────────────────────────────────
        gross_margin = info.get('grossMargins')

        # ── Owner Earnings (Buffett's metric) ───────────────────────────
        # Net Income + D&A - CapEx (maintenance capex proxy)
        try:
            dna          = cashflow.loc['Depreciation And Amortization'].iloc[0]
            capex_val    = abs(cashflow.loc['Capital Expenditure'].iloc[0])
            owner_earn   = net_income + dna - capex_val
        except Exception:
            owner_earn = None

        # ── Monthly Income Potential ─────────────────────────────────────
        # If you invested $X, what monthly passive income does this yield?
        div_yield      = info.get('dividendYield')

        return {
            "name":             info.get('longName', ticker),
            "sector":           info.get('sector', 'N/A'),
            "market_cap":       market_cap,
            "price":            info.get('currentPrice') or info.get('regularMarketPrice'),
            "pe_ratio":         info.get('trailingPE'),
            "fcf":              fcf,
            "fcf_yield":        fcf_yield,
            "fcf_growth":       fcf_growth,
            "roic":             roic,
            "debt_to_fcf":      debt_to_fcf,
            "total_debt":       total_debt,
            "interest_coverage":interest_coverage,
            "gross_margin":     gross_margin,
            "owner_earnings":   owner_earn,
            "dividend_yield":   div_yield,
            "description":      info.get('longBusinessSummary', '')[:400] + '...' if info.get('longBusinessSummary') else '',
        }

    except Exception as e:
        st.error(f"Could not fetch data for **{ticker}**: {e}")
        return {}


# ─────────────────────────────────────────────
# SCORING ENGINE
# ─────────────────────────────────────────────
def score_stock(data: dict) -> tuple[int, list[dict]]:
    """
    Score a stock 0-100 on Buffett fundamentals.
    Returns (total_score, list of criterion dicts for display).
    Each criterion: {name, value, points_earned, points_max, verdict, note}
    """
    criteria = []

    # ── 1. FCF Yield (25 pts) ────────────────────────────────────────────
    fcf_yield = data.get('fcf_yield')
    if fcf_yield is not None:
        if fcf_yield >= THRESHOLDS['fcf_yield_great']:
            pts, verdict = 25, "Excellent"
        elif fcf_yield >= THRESHOLDS['fcf_yield_good']:
            pts, verdict = 15, "Good"
        elif fcf_yield > 0:
            pts, verdict = 5, "Weak"
        else:
            pts, verdict = 0, "Negative FCF"
        criteria.append({
            "name": "Free Cash Flow Yield",
            "value": f"{fcf_yield:.1%}",
            "points_earned": pts,
            "points_max": 25,
            "verdict": verdict,
            "note": "What you earn as an owner relative to price. Buffett wants to see real cash, not accounting earnings."
        })
    else:
        criteria.append({"name": "Free Cash Flow Yield", "value": "N/A", "points_earned": 0, "points_max": 25,
                          "verdict": "No Data", "note": "FCF data unavailable from yfinance."})

    # ── 2. ROIC (25 pts) ─────────────────────────────────────────────────
    roic = data.get('roic')
    if roic is not None:
        if roic >= THRESHOLDS['roic_great']:
            pts, verdict = 25, "Exceptional"
        elif roic >= THRESHOLDS['roic_good']:
            pts, verdict = 15, "Strong"
        elif roic > 0:
            pts, verdict = 5, "Below Average"
        else:
            pts, verdict = 0, "Destroying Capital"
        criteria.append({
            "name": "Return on Invested Capital (ROIC)",
            "value": f"{roic:.1%}",
            "points_earned": pts,
            "points_max": 25,
            "verdict": verdict,
            "note": "Munger: 'Show me the incentives and I'll show you the outcome.' ROIC shows if management deploys capital wisely."
        })
    else:
        criteria.append({"name": "Return on Invested Capital (ROIC)", "value": "N/A", "points_earned": 0,
                          "points_max": 25, "verdict": "No Data", "note": "ROIC data unavailable."})

    # ── 3. Debt Safety (20 pts) ───────────────────────────────────────────
    debt_fcf = data.get('debt_to_fcf')
    if debt_fcf is not None:
        if debt_fcf < THRESHOLDS['debt_fcf_safe']:
            pts, verdict = 20, "Fortress"
        elif debt_fcf < THRESHOLDS['debt_fcf_warning']:
            pts, verdict = 10, "Manageable"
        else:
            pts, verdict = 0, "Overleveraged"
        criteria.append({
            "name": "Debt / Free Cash Flow",
            "value": f"{debt_fcf:.1f}x",
            "points_earned": pts,
            "points_max": 20,
            "verdict": verdict,
            "note": "Years of FCF needed to pay off all debt. In a credit crunch, this is the survival metric."
        })
    else:
        criteria.append({"name": "Debt / Free Cash Flow", "value": "N/A", "points_earned": 0,
                          "points_max": 20, "verdict": "No Data", "note": "Debt data unavailable."})

    # ── 4. Gross Margin / Pricing Power (15 pts) ─────────────────────────
    gm = data.get('gross_margin')
    if gm is not None:
        if gm >= THRESHOLDS['gross_margin_great']:
            pts, verdict = 15, "Wide Moat"
        elif gm >= THRESHOLDS['gross_margin_good']:
            pts, verdict = 10, "Solid Moat"
        else:
            pts, verdict = 3, "Commodity Risk"
        criteria.append({
            "name": "Gross Margin (Pricing Power)",
            "value": f"{gm:.1%}",
            "points_earned": pts,
            "points_max": 15,
            "verdict": verdict,
            "note": "Buffett's favorite moat signal. Can the company raise prices without losing customers?"
        })
    else:
        criteria.append({"name": "Gross Margin (Pricing Power)", "value": "N/A", "points_earned": 0,
                          "points_max": 15, "verdict": "No Data", "note": "Margin data unavailable."})

    # ── 5. Interest Coverage (15 pts) ─────────────────────────────────────
    ic = data.get('interest_coverage')
    if ic is not None:
        if ic >= THRESHOLDS['interest_coverage_safe']:
            pts, verdict = 15, "Safe"
        elif ic >= 2.5:
            pts, verdict = 7, "Adequate"
        elif ic > 0:
            pts, verdict = 2, "Tight"
        else:
            pts, verdict = 0, "Danger"
        criteria.append({
            "name": "Interest Coverage Ratio",
            "value": f"{ic:.1f}x",
            "points_earned": pts,
            "points_max": 15,
            "verdict": verdict,
            "note": "How many times can earnings cover interest payments? Critical in a rising-rate 'Long Squeeze' environment."
        })
    else:
        criteria.append({"name": "Interest Coverage Ratio", "value": "N/A", "points_earned": 0,
                          "points_max": 15, "verdict": "No Data", "note": "Interest data unavailable."})

    total = sum(c['points_earned'] for c in criteria)
    return total, criteria


def score_to_verdict(score: int) -> tuple[str, str]:
    """Convert numeric score to label and color."""
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
> *"I don't look to jump over 7-foot bars: I look around for 1-foot bars that I can step over."*
> — Warren Buffett
""")

st.divider()

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

# ── Optional: position sizing context ─────────────────────────────────────
with st.expander("⚙️ Position Sizing Context (optional)"):
    position_size = st.number_input(
        "How much are you considering investing? ($)",
        min_value=0,
        value=100000,
        step=10000,
        format="%d"
    )
    st.caption("Used to estimate monthly income potential from dividends at your position size.")

# ── Analysis ──────────────────────────────────────────────────────────────
if analyze and ticker_input:
    with st.spinner(f"Fetching fundamentals for **{ticker_input}**..."):
        data = fetch_fundamentals(ticker_input)

    if not data:
        st.stop()

    score, criteria = score_stock(data)
    verdict_label, verdict_color = score_to_verdict(score)

    # ── Company Header ─────────────────────────────────────────────────
    st.markdown(f"## {data.get('name', ticker_input)}")
    st.caption(f"{data.get('sector', '')}  ·  ${data.get('price', 0):,.2f} per share  ·  "
               f"Market Cap: ${data.get('market_cap', 0)/1e9:.1f}B")

    if data.get('description'):
        st.markdown(f"*{data['description']}*")

    st.divider()

    # ── Conviction Score ───────────────────────────────────────────────
    left, right = st.columns([1, 2])

    with left:
        # Gauge chart for the score
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=score,
            domain={'x': [0, 1], 'y': [0, 1]},
            title={'text': "Conviction Score", 'font': {'size': 16}},
            gauge={
                'axis': {'range': [0, 100], 'tickwidth': 1},
                'bar': {'color': verdict_color},
                'steps': [
                    {'range': [0, 45],  'color': "#fadbd8"},
                    {'range': [45, 65], 'color': "#fdebd0"},
                    {'range': [65, 80], 'color': "#fef9e7"},
                    {'range': [80, 100],'color': "#eafaf1"},
                ],
                'threshold': {
                    'line': {'color': verdict_color, 'width': 4},
                    'thickness': 0.75,
                    'value': score
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

    with right:
        # ── Scorecard breakdown ────────────────────────────────────────
        st.markdown("### Owner's Scorecard")
        for c in criteria:
            earned = c['points_earned']
            maximum = c['points_max']
            pct = earned / maximum if maximum > 0 else 0

            # Color code by performance
            if pct >= 0.8:
                bar_color = "#2ecc71"
                icon = "✅"
            elif pct >= 0.5:
                bar_color = "#f39c12"
                icon = "⚠️"
            else:
                bar_color = "#e74c3c"
                icon = "❌"

            st.markdown(
                f"{icon} **{c['name']}** — `{c['value']}` "
                f"&nbsp;&nbsp;<span style='color:{bar_color}'>{c['verdict']}</span> "
                f"&nbsp;·&nbsp; {earned}/{maximum} pts",
                unsafe_allow_html=True
            )
            st.progress(pct)
            st.caption(c['note'])

    st.divider()

    # ── Key Metrics Summary ────────────────────────────────────────────
    st.markdown("### 📊 Key Metrics at a Glance")
    m1, m2, m3, m4 = st.columns(4)

    def fmt_val(val, fmt="money"):
        if val is None:
            return "N/A"
        if fmt == "money":
            return f"${val/1e9:.2f}B" if abs(val) >= 1e9 else f"${val/1e6:.1f}M"
        if fmt == "pct":
            return f"{val:.1%}"
        if fmt == "ratio":
            return f"{val:.1f}x"
        return str(val)

    with m1:
        st.metric("Free Cash Flow",     fmt_val(data.get('fcf')))
        st.metric("Owner Earnings",     fmt_val(data.get('owner_earnings')))
    with m2:
        st.metric("FCF Yield",          fmt_val(data.get('fcf_yield'), "pct"))
        st.metric("FCF Growth (1yr)",   fmt_val(data.get('fcf_growth'), "pct"))
    with m3:
        st.metric("ROIC",               fmt_val(data.get('roic'), "pct"))
        st.metric("Gross Margin",       fmt_val(data.get('gross_margin'), "pct"))
    with m4:
        st.metric("Debt / FCF",         fmt_val(data.get('debt_to_fcf'), "ratio"))
        st.metric("Interest Coverage",  fmt_val(data.get('interest_coverage'), "ratio"))

    st.divider()

    # ── Income Potential ──────────────────────────────────────────────
    st.markdown("### 💰 Income Potential at Your Position Size")
    div_yield = data.get('dividend_yield')
    if div_yield and position_size > 0:
        annual_income  = position_size * div_yield
        monthly_income = annual_income / 12
        target         = THRESHOLDS['monthly_income_target']
        pct_of_target  = monthly_income / target

        ic1, ic2, ic3 = st.columns(3)
        with ic1:
            st.metric("Dividend Yield",       f"{div_yield:.2%}")
        with ic2:
            st.metric("Est. Annual Income",   f"${annual_income:,.0f}")
        with ic3:
            st.metric("Est. Monthly Income",  f"${monthly_income:,.0f}",
                      delta=f"{pct_of_target:.0%} of your $8K/mo target")

        st.progress(min(pct_of_target, 1.0))
    else:
        st.info("No dividend yield data available, or position size is $0. "
                "This may be a pure growth / reinvestment compounder.")

    st.divider()

    # ── Plain English Verdict ─────────────────────────────────────────
    st.markdown("### 📝 The Verdict")

    # Build a plain-English summary from the data
    fcf_yield = data.get('fcf_yield')
    roic = data.get('roic')
    debt_fcf = data.get('debt_to_fcf')

    strengths = [c['name'] for c in criteria if c['points_earned'] / c['points_max'] >= 0.8]
    weaknesses = [c['name'] for c in criteria if c['points_earned'] / c['points_max'] < 0.5 and c['value'] != 'N/A']

    verdict_text = f"**{data.get('name', ticker_input)}** scores **{score}/100** on the Voskuil Owner's Framework. "

    if strengths:
        verdict_text += f"Its strongest qualities are {', '.join(strengths)}. "
    if weaknesses:
        verdict_text += f"Areas of concern: {', '.join(weaknesses)}. "

    if score >= 80:
        verdict_text += ("This business passes the 'Would Buffett hold it for 10 years?' test. "
                         "Consider a concentrated position.")
    elif score >= 65:
        verdict_text += ("Worth watching closely. Strong in some areas but not a slam dunk. "
                         "Look for a better entry price or a catalyst.")
    elif score >= 45:
        verdict_text += ("The fundamentals have real weaknesses. This is not a 'fortress' business. "
                         "Proceed only with a significant margin of safety.")
    else:
        verdict_text += ("This business does not meet the criteria for a concentrated bet. "
                         "The risk of permanent capital loss outweighs the upside.")

    st.markdown(verdict_text)

    # ── Macro Context reminder ─────────────────────────────────────────
    st.info(
        "⚠️ **Macro Overlay Reminder:** In a 'Long Squeeze' environment (high debt, financial repression), "
        "prioritize companies with low debt, strong FCF, and pricing power. Avoid businesses that need "
        "cheap credit to survive. Your $8K/month withdrawal target requires this portfolio to be "
        "recession-resistant, not just return-maximizing."
    )

elif analyze and not ticker_input:
    st.warning("Please enter a ticker symbol to analyze.")

else:
    # ── Landing state ─────────────────────────────────────────────────
    st.markdown("""
    ### How this works

    Enter any stock ticker above and get an **Owner's Report** scored on five Buffett fundamentals:

    | Metric | What it measures | Max Points |
    |--------|-----------------|------------|
    | Free Cash Flow Yield | Real owner earnings relative to price | 25 |
    | ROIC | How wisely management deploys your capital | 25 |
    | Debt / FCF | Survival capacity in a credit crunch | 20 |
    | Gross Margin | Pricing power and moat durability | 15 |
    | Interest Coverage | Ability to service debt in a 'Long Squeeze' | 15 |

    **Score guide:** 80-100 = Strong Buy · 65-79 = Watch · 45-64 = Caution · <45 = Avoid

    ---
    *Data sourced from Yahoo Finance via yfinance. For proof-of-concept use.*
    """)
