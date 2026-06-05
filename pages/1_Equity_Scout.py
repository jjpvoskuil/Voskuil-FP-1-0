import streamlit as st
import requests
import plotly.graph_objects as go

st.set_page_config(page_title="Equity Scout", layout="wide")

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
    "monthly_income_target":    8000,
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

def fval(obj, key):
    try:
        return float(obj[key]["value"])
    except (KeyError, TypeError, ValueError):
        return None

def calc_interest_coverage(inc):
    """
    Returns (coverage_value_or_None, is_net_creditor bool).
    Net creditor = positive nonoperating income with no interest expense = full points.
    """
    op_income    = fval(inc, "operating_income_loss")
    interest_exp = fval(inc, "interest_expense_operating")
    if interest_exp and interest_exp > 0 and op_income is not None:
        return op_income / interest_exp, False
    nonop = fval(inc, "nonoperating_income_loss")
    if nonop is not None and nonop > 0:
        return None, True
    return None, False

@st.cache_data(ttl=3600)
def fetch_fundamentals(ticker):
    try:
        det_data = poly_get(f"/v3/reference/tickers/{ticker}")
        det = det_data.get("results", {}) if det_data else {}
        market_cap  = safe_float(det.get("market_cap"))
        shares      = safe_float(det.get("weighted_shares_outstanding"))
        name        = det.get("name", ticker)
        sector      = det.get("sic_description", "N/A")
        description = (det.get("description", "")[:400] + "...") if det.get("description") else ""

        price_data = poly_get(f"/v2/aggs/ticker/{ticker}/prev")
        price = None
        try:
            price = float(price_data["results"][0]["c"])
        except (KeyError, TypeError, IndexError):
            pass

        fin_data = poly_get("/vX/reference/financials", {
            "ticker": ticker, "timeframe": "annual", "limit": 2,
            "order": "desc", "sort": "period_of_report_date",
        })
        if not fin_data or not fin_data.get("results"):
            return {}

        results = fin_data["results"]
        f  = results[0]["financials"]
        f2 = results[1]["financials"] if len(results) > 1 else {}

        inc = f.get("income_statement",    {})
        cf  = f.get("cash_flow_statement", {})
        bs  = f.get("balance_sheet",       {})
        cf2 = f2.get("cash_flow_statement", {}) if f2 else {}

        op_cf  = fval(cf, "net_cash_flow_from_operating_activities")
        inv_cf = fval(cf, "net_cash_flow_from_investing_activities")
        fcf    = (op_cf + inv_cf) if (op_cf is not None and inv_cf is not None) else None

        op_cf2  = fval(cf2, "net_cash_flow_from_operating_activities")
        inv_cf2 = fval(cf2, "net_cash_flow_from_investing_activities")
        fcf2    = (op_cf2 + inv_cf2) if (op_cf2 is not None and inv_cf2 is not None) else None
        fcf_growth = ((fcf / fcf2) - 1) if (fcf and fcf2 and fcf2 != 0) else None

        fcf_yield    = (fcf / market_cap) if (fcf and market_cap and market_cap > 0) else None
        gross_profit = fval(inc, "gross_profit")
        revenues     = fval(inc, "revenues")
        gross_margin = (gross_profit / revenues) if (gross_profit and revenues and revenues > 0) else None
        net_income   = fval(inc, "net_income_loss")
        total_assets = fval(bs,  "assets")
        current_liab = fval(bs,  "current_liabilities")
        invested_cap = (total_assets - current_liab) if (total_assets and current_liab) else None
        roic         = (net_income / invested_cap) if (net_income and invested_cap and invested_cap != 0) else None

        long_term_debt = fval(bs, "long_term_debt") or fval(bs, "noncurrent_liabilities")
        debt_to_fcf    = (long_term_debt / fcf) if (long_term_debt is not None and fcf and fcf > 0) else None

        interest_cov, is_net_creditor = calc_interest_coverage(inc)

        dna_proxy  = (op_cf - net_income) if (op_cf and net_income) else None
        capex_abs  = abs(inv_cf) if inv_cf else 0
        owner_earn = (net_income + (dna_proxy or 0) - capex_abs) if net_income is not None else None
        poe        = (price / (owner_earn / shares)) if (owner_earn and owner_earn > 0 and shares and price) else None

        div_ps    = fval(inc, "common_stock_dividends")
        div_yield = (div_ps / price) if (div_ps and price and price > 0) else None

        return {
            "name":              name,
            "sector":            sector,
            "description":       description,
            "market_cap":        market_cap,
            "price":             price,
            "fcf":               fcf,
            "fcf_yield":         fcf_yield,
            "fcf_growth":        fcf_growth,
            "gross_margin":      gross_margin,
            "roic":              roic,
            "long_term_debt":    long_term_debt,
            "debt_to_fcf":       debt_to_fcf,
            "interest_coverage": interest_cov,
            "is_net_creditor":   is_net_creditor,
            "owner_earnings":    owner_earn,
            "price_owner_earn":  poe,
            "dividend_yield":    div_yield,
            "net_income":        net_income,
            "revenues":          revenues,
        }
    except Exception as e:
        st.error(f"Could not fetch data for **{ticker}**: {e}")
        return {}

def score_stock(data, weights):
    criteria = []

    # ── FCF Yield ─────────────────────────────────────────────────────
    max_pts   = weights["FCF Yield"]
    fcf_yield = data.get('fcf_yield')
    if fcf_yield is not None:
        if fcf_yield >= THRESHOLDS['fcf_yield_great']:   pts, verdict = max_pts, "Excellent"
        elif fcf_yield >= THRESHOLDS['fcf_yield_good']:  pts, verdict = round(max_pts * 0.60), "Good"
        elif fcf_yield > 0:                              pts, verdict = round(max_pts * 0.15), "Weak"
        else:                                            pts, verdict = 0, "Negative FCF"
    else:
        pts, verdict = 0, "No Data"
    criteria.append({"name": "Free Cash Flow Yield",
                      "value": f"{fcf_yield:.1%}" if fcf_yield is not None else "N/A",
                      "points_earned": pts, "points_max": max_pts, "verdict": verdict,
                      "note": "What you earn as an owner relative to price. Buffett wants real cash, not accounting earnings.",
                      "missing": fcf_yield is None})

    # ── ROIC ──────────────────────────────────────────────────────────
    max_pts = weights["ROIC"]
    roic    = data.get('roic')
    if roic is not None:
        if roic >= THRESHOLDS['roic_great']:   pts, verdict = max_pts, "Exceptional"
        elif roic >= THRESHOLDS['roic_good']:  pts, verdict = round(max_pts * 0.60), "Strong"
        elif roic > 0:                         pts, verdict = round(max_pts * 0.20), "Below Average"
        else:                                  pts, verdict = 0, "Destroying Capital"
    else:
        pts, verdict = 0, "No Data"
    criteria.append({"name": "Return on Invested Capital (ROIC)",
                      "value": f"{roic:.1%}" if roic is not None else "N/A",
                      "points_earned": pts, "points_max": max_pts, "verdict": verdict,
                      "note": "Munger: 'Show me the incentives and I'll show you the outcome.' ROIC shows if management deploys capital wisely.",
                      "missing": roic is None})

    # ── Debt / FCF ────────────────────────────────────────────────────
    max_pts  = weights["Debt / FCF"]
    debt_fcf = data.get('debt_to_fcf')
    ic       = data.get('interest_coverage') or 0
    is_nc    = data.get('is_net_creditor', False)
    if debt_fcf is not None:
        if debt_fcf < THRESHOLDS['debt_fcf_safe']:        pts, verdict = max_pts, "Fortress"
        elif debt_fcf < THRESHOLDS['debt_fcf_warning']:   pts, verdict = round(max_pts * 0.50), "Manageable"
        elif ic >= THRESHOLDS['interest_coverage_safe'] or is_nc:
                                                          pts, verdict = round(max_pts * 0.50), "High Debt, Well Covered"
        else:                                             pts, verdict = 0, "Overleveraged"
    else:
        pts, verdict = 0, "No Data"
    criteria.append({"name": "Debt / Free Cash Flow",
                      "value": f"{debt_fcf:.1f}x" if debt_fcf is not None else "N/A",
                      "points_earned": pts, "points_max": max_pts, "verdict": verdict,
                      "note": "Years of FCF needed to pay off long-term debt. In a credit crunch, this is the survival metric.",
                      "missing": debt_fcf is None})

    # ── Gross Margin ──────────────────────────────────────────────────
    max_pts = weights["Gross Margin"]
    gm      = data.get('gross_margin')
    if gm is not None:
        if gm >= THRESHOLDS['gross_margin_great']:   pts, verdict = max_pts, "Wide Moat"
        elif gm >= THRESHOLDS['gross_margin_good']:  pts, verdict = round(max_pts * 0.67), "Solid Moat"
        else:                                        pts, verdict = round(max_pts * 0.20), "Commodity Risk"
    else:
        pts, verdict = 0, "No Data"
    criteria.append({"name": "Gross Margin (Pricing Power)",
                      "value": f"{gm:.1%}" if gm is not None else "N/A",
                      "points_earned": pts, "points_max": max_pts, "verdict": verdict,
                      "note": "Buffett's favorite moat signal. Can the company raise prices without losing customers?",
                      "missing": gm is None})

    # ── Interest Coverage ─────────────────────────────────────────────
    max_pts = weights["Interest Coverage"]
    ic_val  = data.get('interest_coverage')
    is_nc   = data.get('is_net_creditor', False)
    if is_nc:
        pts, verdict = max_pts, "Net Creditor ✨"
    elif ic_val is not None:
        if ic_val >= THRESHOLDS['interest_coverage_safe']: pts, verdict = max_pts, "Safe"
        elif ic_val >= 2.5:                                pts, verdict = round(max_pts * 0.50), "Adequate"
        elif ic_val > 0:                                   pts, verdict = round(max_pts * 0.15), "Tight"
        else:                                              pts, verdict = 0, "Danger"
    else:
        pts, verdict = 0, "No Data"
    display_val = "Net Creditor" if is_nc else (f"{ic_val:.1f}x" if ic_val is not None else "N/A")
    criteria.append({"name": "Interest Coverage Ratio",
                      "value": display_val,
                      "points_earned": pts, "points_max": max_pts, "verdict": verdict,
                      "note": "How many times can earnings cover interest payments? 'Net Creditor' means the company earns more interest than it pays.",
                      "missing": (not is_nc and ic_val is None)})

    # ── Price / Owner Earnings ────────────────────────────────────────
    max_pts = weights["Price / Owner Earnings"]
    poe     = data.get('price_owner_earn')
    if poe is not None:
        if poe <= THRESHOLDS['poe_bargain']:    pts, verdict = max_pts, "Bargain"
        elif poe <= THRESHOLDS['poe_fair']:     pts, verdict = round(max_pts * 0.67), "Fair Value"
        elif poe <= THRESHOLDS['poe_stretched']:pts, verdict = round(max_pts * 0.25), "Stretched"
        else:                                   pts, verdict = 0, "Expensive"
    else:
        pts, verdict = 0, "No Data"
    criteria.append({"name": "Price / Owner Earnings",
                      "value": f"{poe:.1f}x" if poe is not None else "N/A",
                      "points_earned": pts, "points_max": max_pts, "verdict": verdict,
                      "note": "Buffett's valuation test. What are you paying per dollar of real owner earnings? Under 15x is a bargain.",
                      "missing": poe is None})

    # ── Totals ────────────────────────────────────────────────────────
    raw_score      = sum(c['points_earned'] for c in criteria)
    missing_pts    = sum(c['points_max'] for c in criteria if c.get('missing'))
    missing_names  = [c['name'] for c in criteria if c.get('missing')]

    # Rebalanced score: redistribute missing points proportionally
    available_pts  = 100 - missing_pts
    rebalanced     = round(raw_score / available_pts * 100) if available_pts > 0 else raw_score

    return raw_score, rebalanced, missing_names, criteria

def score_to_verdict(score):
    if score >= 80:   return "Strong Buy", "#2ecc71"
    elif score >= 65: return "Watch Closely", "#f39c12"
    elif score >= 45: return "Proceed with Caution", "#e67e22"
    else:             return "Avoid", "#e74c3c"

# ── Query params ──────────────────────────────────────────────────────
params       = st.query_params
url_ticker   = params.get("ticker", "").upper().strip()
auto_analyze = params.get("auto", "0") == "1"

st.title("🔍 Equity Scout")
st.caption("Concentrated, Buffett-style fundamental analysis. One business at a time.")
st.markdown("> *\"Price is what you pay. Value is what you get.\"* — Warren Buffett")

if url_ticker:
    st.info(f"📌 Analyzing **{url_ticker}** — arrived from Holdings Explorer. [← Back to Dashboard]({APP_URL})")

st.divider()

# ── Weight reset handler — runs BEFORE any widget with these keys renders ──
_weight_map = [("w_fcf","FCF Yield"),("w_roic","ROIC"),("w_debt","Debt / FCF"),
               ("w_gm","Gross Margin"),("w_ic","Interest Coverage"),("w_poe","Price / Owner Earnings")]
for _wkey, _mkey in _weight_map:
    if st.session_state.pop(f"pending_reset_{_wkey}", False):
        st.session_state[_wkey] = DEFAULT_WEIGHTS[_mkey]
        st.session_state.scoring_weights[_mkey] = DEFAULT_WEIGHTS[_mkey]

with st.expander("⚙️ Customize Scoring Weights", expanded=False):
    st.caption("Weights shared across all pages. Set them on the dashboard and they carry through here automatically.")

    # Read from shared session state — falls back to DEFAULT_WEIGHTS if dashboard not visited yet
    if "scoring_weights"   not in st.session_state:
        st.session_state.scoring_weights   = DEFAULT_WEIGHTS.copy()
    if "committed_weights" not in st.session_state:
        st.session_state.committed_weights = DEFAULT_WEIGHTS.copy()

    sw = st.session_state.scoring_weights

    rc1, rc2, rc3 = st.columns([1.2, 1.2, 4])
    if rc1.button("↺ Reset to Defaults", key="es_reset_weights"):
        st.session_state.scoring_weights   = DEFAULT_WEIGHTS.copy()
        st.session_state.committed_weights = DEFAULT_WEIGHTS.copy()
        for _wkey, _mkey in _weight_map:
            st.session_state[_wkey] = DEFAULT_WEIGHTS[_mkey]
        st.rerun()

    draft_weights = {
        "FCF Yield":              st.session_state.get("w_fcf",  sw["FCF Yield"]),
        "ROIC":                   st.session_state.get("w_roic", sw["ROIC"]),
        "Debt / FCF":             st.session_state.get("w_debt", sw["Debt / FCF"]),
        "Gross Margin":           st.session_state.get("w_gm",   sw["Gross Margin"]),
        "Interest Coverage":      st.session_state.get("w_ic",   sw["Interest Coverage"]),
        "Price / Owner Earnings": st.session_state.get("w_poe",  sw["Price / Owner Earnings"]),
    }
    draft_total = sum(draft_weights.values())
    apply_ok    = draft_total == 100
    if rc2.button("✅ Apply Weights", key="es_apply_weights", type="primary", disabled=not apply_ok,
                  help="Activates weights for scoring." if apply_ok else f"Total must equal 100 (currently {draft_total})."):
        st.session_state.committed_weights = draft_weights.copy()
        st.session_state.scoring_weights   = draft_weights.copy()
        st.rerun()

    cw = st.session_state.committed_weights
    rc3.caption(
        f"**Active:** FCF {cw['FCF Yield']} · ROIC {cw['ROIC']} · Debt {cw['Debt / FCF']} · "
        f"GM {cw['Gross Margin']} · IC {cw['Interest Coverage']} · P/OE {cw['Price / Owner Earnings']}"
    )

    w_col1, w_col2 = st.columns(2)
    with w_col1:
        _sc_w_fcf, _sb_w_fcf = st.columns([4, 1])
        with _sc_w_fcf:
            w_fcf = st.slider("FCF Yield", 0, 60, sw["FCF Yield"], step=5, key="w_fcf")
        with _sb_w_fcf:
            st.write("")
            if st.button(f"↺ {DEFAULT_WEIGHTS['FCF Yield']}", key="reset_w_fcf", help="Reset FCF Yield to default", use_container_width=True):
                st.session_state["pending_reset_w_fcf"] = True
                st.rerun()
        _sc_w_roic, _sb_w_roic = st.columns([4, 1])
        with _sc_w_roic:
            w_roic = st.slider("ROIC", 0, 40, sw["ROIC"], step=5, key="w_roic")
        with _sb_w_roic:
            st.write("")
            if st.button(f"↺ {DEFAULT_WEIGHTS['ROIC']}", key="reset_w_roic", help="Reset ROIC to default", use_container_width=True):
                st.session_state["pending_reset_w_roic"] = True
                st.rerun()
        _sc_w_debt, _sb_w_debt = st.columns([4, 1])
        with _sc_w_debt:
            w_debt = st.slider("Debt / FCF", 0, 40, sw["Debt / FCF"], step=5, key="w_debt")
        with _sb_w_debt:
            st.write("")
            if st.button(f"↺ {DEFAULT_WEIGHTS['Debt / FCF']}", key="reset_w_debt", help="Reset Debt / FCF to default", use_container_width=True):
                st.session_state["pending_reset_w_debt"] = True
                st.rerun()
    with w_col2:
        _sc_w_gm, _sb_w_gm = st.columns([4, 1])
        with _sc_w_gm:
            w_gm = st.slider("Gross Margin", 0, 40, sw["Gross Margin"], step=5, key="w_gm")
        with _sb_w_gm:
            st.write("")
            if st.button(f"↺ {DEFAULT_WEIGHTS['Gross Margin']}", key="reset_w_gm", help="Reset Gross Margin to default", use_container_width=True):
                st.session_state["pending_reset_w_gm"] = True
                st.rerun()
        _sc_w_ic, _sb_w_ic = st.columns([4, 1])
        with _sc_w_ic:
            w_ic = st.slider("Interest Coverage", 0, 40, sw["Interest Coverage"], step=5, key="w_ic")
        with _sb_w_ic:
            st.write("")
            if st.button(f"↺ {DEFAULT_WEIGHTS['Interest Coverage']}", key="reset_w_ic", help="Reset Interest Coverage to default", use_container_width=True):
                st.session_state["pending_reset_w_ic"] = True
                st.rerun()
        _sc_w_poe, _sb_w_poe = st.columns([4, 1])
        with _sc_w_poe:
            w_poe = st.slider("Price / Owner Earnings", 0, 60, sw["Price / Owner Earnings"], step=5, key="w_poe")
        with _sb_w_poe:
            st.write("")
            if st.button(f"↺ {DEFAULT_WEIGHTS['Price / Owner Earnings']}", key="reset_w_poe", help="Reset Price / Owner Earnings to default", use_container_width=True):
                st.session_state["pending_reset_w_poe"] = True
                st.rerun()

    active_weights = {
        "FCF Yield": w_fcf, "ROIC": w_roic, "Debt / FCF": w_debt,
        "Gross Margin": w_gm, "Interest Coverage": w_ic, "Price / Owner Earnings": w_poe,
    }
    st.session_state.scoring_weights = active_weights
    total_weight = sum(active_weights.values())
    if total_weight == 100:
        st.success(f"✅ Total: {total_weight} / 100 — click Apply Weights to activate")
    elif total_weight < 100:
        st.warning(f"⚠️ Total: {total_weight} / 100 — {100 - total_weight} pts unallocated")
    else:
        st.error(f"❌ Total: {total_weight} / 100 — over by {total_weight - 100} pts")

# Scoring uses committed weights
weights = st.session_state.get("committed_weights", DEFAULT_WEIGHTS.copy())

col_input, col_btn = st.columns([3, 1])
with col_input:
    ticker_input = st.text_input("Enter a stock ticker", value=url_ticker,
                                  placeholder="e.g. ABBV, MSFT, KO, NVDA",
                                  label_visibility="collapsed").strip().upper()
with col_btn:
    analyze = st.button("🔎 Analyze", use_container_width=True, type="primary")

with st.expander("💼 Position Sizing Context (optional)"):
    position_size = st.number_input("How much are you considering investing? ($)",
                                     min_value=0, value=100000, step=10000, format="%d")

if auto_analyze and url_ticker and not analyze:
    analyze      = True
    ticker_input = url_ticker

if analyze and ticker_input:
    if total_weight != 100:
        st.warning(f"Weights add up to {total_weight}, not 100. Adjust sliders for accurate scores.")

    with st.spinner(f"Fetching fundamentals for **{ticker_input}** via Polygon..."):
        data = fetch_fundamentals(ticker_input)

    if not data:
        st.error(f"No data returned for {ticker_input}. Check the ticker symbol and try again.")
        st.stop()

    raw_score, rebalanced_score, missing_names, criteria = score_stock(data, weights)
    verdict_label, verdict_color = score_to_verdict(rebalanced_score)

    st.markdown(f"## {data.get('name', ticker_input)}")
    st.caption(f"{data.get('sector','')}  ·  ${data.get('price',0) or 0:,.2f} per share  ·  Market Cap: ${(data.get('market_cap') or 0)/1e9:.1f}B")
    if data.get('description'):
        st.markdown(f"*{data['description']}*")
    st.divider()

    left, right = st.columns([1, 2])
    with left:
        fig = go.Figure(go.Indicator(
            mode="gauge+number", value=rebalanced_score,
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
                'threshold': {'line': {'color': verdict_color, 'width': 4}, 'thickness': 0.75, 'value': rebalanced_score}
            }
        ))
        fig.update_layout(height=260, margin=dict(t=30, b=0, l=20, r=20))
        st.plotly_chart(fig, use_container_width=True)
        st.markdown(f"<div style='text-align:center; font-size:1.4em; font-weight:bold; color:{verdict_color}'>{verdict_label}</div>", unsafe_allow_html=True)

        # Show both scores if any data is missing
        if missing_names:
            st.markdown(f"**Rebalanced Score:** {rebalanced_score}/100")
            st.markdown(f"**Raw Score:** {raw_score}/100")
            st.warning(f"⚠️ Missing data: {', '.join(missing_names)}. Score rebalanced across available metrics.")
        else:
            st.markdown(f"**Score:** {rebalanced_score}/100")

        st.markdown("**Active Weights**")
        for k, v in weights.items():
            st.caption(f"{k}: {v} pts")

    with right:
        st.markdown("### Owner's Scorecard")
        for c in criteria:
            earned  = c['points_earned']
            maximum = c['points_max']
            pct     = earned / maximum if maximum > 0 else 0
            if c.get('missing'):
                bar_color, icon = "#888888", "⬜"
            elif pct >= 0.8:   bar_color, icon = "#2ecc71", "✅"
            elif pct >= 0.5:   bar_color, icon = "#f39c12", "⚠️"
            else:              bar_color, icon = "#e74c3c", "❌"
            st.markdown(
                f"{icon} **{c['name']}** — `{c['value']}` &nbsp;&nbsp;<span style='color:{bar_color}'>{c['verdict']}</span> &nbsp;·&nbsp; {earned}/{maximum} pts",
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
        st.metric("Free Cash Flow",       fmt_val(data.get('fcf')))
        st.metric("Owner Earnings",       fmt_val(data.get('owner_earnings')))
    with m2:
        st.metric("FCF Yield",            fmt_val(data.get('fcf_yield'), "pct"))
        st.metric("FCF Growth (1yr)",     fmt_val(data.get('fcf_growth'), "pct"))
    with m3:
        st.metric("ROIC",                 fmt_val(data.get('roic'), "pct"))
        st.metric("Gross Margin",         fmt_val(data.get('gross_margin'), "pct"))
    with m4:
        st.metric("Long-Term Debt/FCF",   fmt_val(data.get('debt_to_fcf'), "ratio"))
        st.metric("Price/Owner Earnings", fmt_val(data.get('price_owner_earn'), "ratio"))

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
        with ic3: st.metric("Est. Monthly Income", f"${monthly_income:,.0f}", delta=f"{pct_of_target:.0%} of your $8K/mo target")
        st.progress(min(pct_of_target, 1.0))
    else:
        st.info("No dividend yield data available. This may be a pure growth compounder.")

    st.divider()
    st.markdown("### 📝 The Verdict")
    strengths  = [c['name'] for c in criteria if not c.get('missing') and c['points_max'] > 0 and c['points_earned'] / c['points_max'] >= 0.8]
    weaknesses = [c['name'] for c in criteria if not c.get('missing') and c['points_max'] > 0 and c['points_earned'] / c['points_max'] < 0.5 and c['value'] != 'N/A']
    verdict_text = f"**{data.get('name', ticker_input)}** scores **{rebalanced_score}/100** on the Voskuil Owner's Framework. "
    if missing_names:
        verdict_text += f"Note: {', '.join(missing_names)} had no data and were excluded from scoring. "
    if strengths:  verdict_text += f"Its strongest qualities are {', '.join(strengths)}. "
    if weaknesses: verdict_text += f"Areas of concern: {', '.join(weaknesses)}. "
    if rebalanced_score >= 80:   verdict_text += "This business passes the 'Would Buffett hold it for 10 years?' test. Consider a concentrated position."
    elif rebalanced_score >= 65: verdict_text += "Worth watching closely. Strong in some areas but not a slam dunk. Look for a better entry price."
    elif rebalanced_score >= 45: verdict_text += "Real weaknesses in the fundamentals. Not a fortress business. Proceed only with a significant margin of safety."
    else:                        verdict_text += "Does not meet the criteria for a concentrated bet. Risk of permanent capital loss outweighs the upside."
    st.markdown(verdict_text)
    st.info("⚠️ **Macro Overlay Reminder:** In a 'Long Squeeze' environment, prioritize companies with low debt, strong FCF, and pricing power. Your $8K/month withdrawal target requires this portfolio to be recession-resistant, not just return-maximizing.")

elif analyze and not ticker_input:
    st.warning("Please enter a ticker symbol to analyze.")
else:
    st.markdown("""
    ### How this works
    Enter any stock ticker above and get an **Owner's Report** scored on six Buffett fundamentals.

    | Metric | Default Weight | What it measures |
    |--------|---------------|-----------------|
    | Free Cash Flow Yield | 20 pts | Real owner earnings relative to price |
    | ROIC | 10 pts | How wisely management deploys your capital |
    | Debt / FCF | 20 pts | Survival capacity in a credit crunch |
    | Gross Margin | 15 pts | Pricing power and moat durability |
    | Interest Coverage | 10 pts | Ability to service debt in a Long Squeeze |
    | Price / Owner Earnings | 25 pts | What you're paying per dollar of real earnings |

    **Score guide:** 80-100 = Strong Buy · 65-79 = Watch · 45-64 = Caution · <45 = Avoid

    When data is missing for a metric, the score is **rebalanced** across available metrics so companies
    aren't unfairly penalized for data gaps. Missing metrics are highlighted in grey.

    *Data sourced from Polygon.io SEC filings.*
    """)
