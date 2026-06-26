import streamlit as st
import requests
import plotly.graph_objects as go
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from sec_utils import fetch_10k_sections
from claude_utils import ask_claude_about_equity
from superinvestor_utils import get_superinvestor_conviction, clear_superinvestor_cache

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
                      "note": "Buffett: 'The most important thing for me is figuring out how big a moat there is around the business and the cash it generates.' FCF yield is what you actually earn as an owner — not accounting profits.",
                      "missing": fcf_yield is None})

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
                      "note": "Munger's capital allocation test: management that consistently earns 20%+ ROIC is compounding your wealth. Below 12% means they're destroying value with every reinvestment dollar.",
                      "missing": roic is None})

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
                      "note": "Munger's inversion: 'What kills a great business?' Excessive debt when capital becomes scarce. A fortress balance sheet means never being a forced seller. Under 3x Debt/FCF = structural survivor.",
                      "missing": debt_fcf is None})

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
                      "note": "Buffett: 'The single most important decision in evaluating a business is pricing power.' Gross margin above 60% signals a structural moat — brand, switching costs, or network effects at work.",
                      "missing": gm is None})

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
                      "note": "Munger's survival lens: can this business service its debt through elevated rates, suppressed growth, and tightening credit? Net Creditor status is the ultimate fortress signal.",
                      "missing": (not is_nc and ic_val is None)})

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
                      "note": "Buffett: 'Price is what you pay. Value is what you get.' Owner Earnings = net income + D&A − maintenance capex. Under 15x is a bargain; over 35x you're paying for perfection.",
                      "missing": poe is None})

    raw_score      = sum(c['points_earned'] for c in criteria)
    missing_pts    = sum(c['points_max'] for c in criteria if c.get('missing'))
    missing_names  = [c['name'] for c in criteria if c.get('missing')]
    available_pts  = 100 - missing_pts
    rebalanced     = round(raw_score / available_pts * 100) if available_pts > 0 else raw_score

    return raw_score, rebalanced, missing_names, criteria

def score_to_verdict(score):
    if score >= 80:   return "Strong Buy", "#2ecc71"
    elif score >= 65: return "Watch Closely", "#f39c12"
    elif score >= 45: return "Proceed with Caution", "#e67e22"
    else:             return "Avoid", "#e74c3c"

def fmt_val(val, fmt="money"):
    if val is None: return "N/A"
    if fmt == "money":  return f"${val/1e9:.2f}B" if abs(val) >= 1e9 else f"${val/1e6:.1f}M"
    if fmt == "pct":    return f"{val:.1%}"
    if fmt == "ratio":  return f"{val:.1f}x"
    return str(val)

# ── Query params ──────────────────────────────────────────────────────
params     = st.query_params
url_ticker = params.get("ticker", "").upper().strip()
if not url_ticker and "dive_ticker" in st.session_state:
    url_ticker = st.session_state.pop("dive_ticker", "").upper().strip()
auto_analyze = bool(url_ticker)

st.title("🔍 Equity Scout")
st.caption("Concentrated, Buffett-style fundamental analysis. One business at a time.")
st.markdown("> *\"Price is what you pay. Value is what you get.\"* — Warren Buffett")

if url_ticker:
    col_back, _ = st.columns([1, 4])
    with col_back:
        if st.button("← Back to Dashboard"):
            st.switch_page("pages/0_Dashboard.py")
    st.info(f"📌 Analyzing **{url_ticker}** — arrived from Holdings Explorer.")

st.divider()

# ── Weight reset handler ──────────────────────────────────────────────
_weight_map = [("w_fcf","FCF Yield"),("w_roic","ROIC"),("w_debt","Debt / FCF"),
               ("w_gm","Gross Margin"),("w_ic","Interest Coverage"),("w_poe","Price / Owner Earnings")]
for _wkey, _mkey in _weight_map:
    if st.session_state.pop(f"pending_reset_{_wkey}", False):
        st.session_state[_wkey] = DEFAULT_WEIGHTS[_mkey]
        st.session_state.scoring_weights[_mkey] = DEFAULT_WEIGHTS[_mkey]

with st.expander("⚙️ Customize Scoring Weights", expanded=False):
    st.caption("Weights shared across all pages. Set them on the dashboard and they carry through here automatically.")
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
            if st.button(f"↺ {DEFAULT_WEIGHTS['FCF Yield']}", key="reset_w_fcf", use_container_width=True):
                st.session_state["pending_reset_w_fcf"] = True
                st.rerun()
        _sc_w_roic, _sb_w_roic = st.columns([4, 1])
        with _sc_w_roic:
            w_roic = st.slider("ROIC", 0, 40, sw["ROIC"], step=5, key="w_roic")
        with _sb_w_roic:
            st.write("")
            if st.button(f"↺ {DEFAULT_WEIGHTS['ROIC']}", key="reset_w_roic", use_container_width=True):
                st.session_state["pending_reset_w_roic"] = True
                st.rerun()
        _sc_w_debt, _sb_w_debt = st.columns([4, 1])
        with _sc_w_debt:
            w_debt = st.slider("Debt / FCF", 0, 40, sw["Debt / FCF"], step=5, key="w_debt")
        with _sb_w_debt:
            st.write("")
            if st.button(f"↺ {DEFAULT_WEIGHTS['Debt / FCF']}", key="reset_w_debt", use_container_width=True):
                st.session_state["pending_reset_w_debt"] = True
                st.rerun()
    with w_col2:
        _sc_w_gm, _sb_w_gm = st.columns([4, 1])
        with _sc_w_gm:
            w_gm = st.slider("Gross Margin", 0, 40, sw["Gross Margin"], step=5, key="w_gm")
        with _sb_w_gm:
            st.write("")
            if st.button(f"↺ {DEFAULT_WEIGHTS['Gross Margin']}", key="reset_w_gm", use_container_width=True):
                st.session_state["pending_reset_w_gm"] = True
                st.rerun()
        _sc_w_ic, _sb_w_ic = st.columns([4, 1])
        with _sc_w_ic:
            w_ic = st.slider("Interest Coverage", 0, 40, sw["Interest Coverage"], step=5, key="w_ic")
        with _sb_w_ic:
            st.write("")
            if st.button(f"↺ {DEFAULT_WEIGHTS['Interest Coverage']}", key="reset_w_ic", use_container_width=True):
                st.session_state["pending_reset_w_ic"] = True
                st.rerun()
        _sc_w_poe, _sb_w_poe = st.columns([4, 1])
        with _sc_w_poe:
            w_poe = st.slider("Price / Owner Earnings", 0, 60, sw["Price / Owner Earnings"], step=5, key="w_poe")
        with _sb_w_poe:
            st.write("")
            if st.button(f"↺ {DEFAULT_WEIGHTS['Price / Owner Earnings']}", key="reset_w_poe", use_container_width=True):
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

# ── Results cache key — persists across chat reruns ───────────────────
_cache_key = f"es_results_{ticker_input}" if ticker_input else None

# ── Run analysis and cache, OR restore from cache ────────────────────
if analyze and ticker_input:
    total_weight = sum(st.session_state.get("committed_weights", DEFAULT_WEIGHTS).values())
    if total_weight != 100:
        st.warning(f"Weights add up to {total_weight}, not 100. Adjust sliders for accurate scores.")

    with st.spinner(f"Fetching fundamentals for **{ticker_input}** via Polygon..."):
        data = fetch_fundamentals(ticker_input)

    if not data:
        st.error(f"No data returned for {ticker_input}. Check the ticker symbol and try again.")
        st.stop()

    raw_score, rebalanced_score, missing_names, criteria = score_stock(data, weights)
    verdict_label, verdict_color = score_to_verdict(rebalanced_score)

    # Cache so page stays rendered when chat input triggers reruns
    st.session_state[_cache_key] = {
        "data": data, "raw_score": raw_score, "rebalanced_score": rebalanced_score,
        "missing_names": missing_names, "criteria": criteria,
        "verdict_label": verdict_label, "verdict_color": verdict_color,
    }

elif _cache_key and _cache_key in st.session_state:
    # Restore from cache on any rerun (chat input, button clicks, etc.)
    _c = st.session_state[_cache_key]
    data             = _c["data"]
    raw_score        = _c["raw_score"]
    rebalanced_score = _c["rebalanced_score"]
    missing_names    = _c["missing_names"]
    criteria         = _c["criteria"]
    verdict_label    = _c["verdict_label"]
    verdict_color    = _c["verdict_color"]

# ── Render results if we have them (fresh or cached) ─────────────────
if _cache_key and _cache_key in st.session_state:

    st.markdown(f"## {data.get('name', ticker_input)}")
    st.caption(f"{data.get('sector','')}  ·  ${data.get('price',0) or 0:,.2f} per share  ·  Market Cap: ${(data.get('market_cap') or 0)/1e9:.1f}B")
    if data.get('description'):
        st.markdown(f"*{data['description']}*")

    sec_link   = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker_input}&type=10-K&dateb=&owner=include&count=10"
    yahoo_link = f"https://finance.yahoo.com/quote/{ticker_input}"
    rl1, rl2, rl3 = st.columns([1, 1, 6])
    with rl1:
        st.link_button("📋 SEC Filings", sec_link)
    with rl2:
        st.link_button("📈 Yahoo Finance", yahoo_link)
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

    # ── Superinvestor Conviction ──────────────────────────────────────
    st.markdown("### 🦁 Superinvestor Conviction")
    st.caption(
        "How many of 13 tracked value superinvestors (Buffett, Ackman, Klarman, etc.) "
        "hold this stock — sourced directly from SEC EDGAR 13F filings."
    )

    si_key = f"si_conviction_{ticker_input}"
    si_refresh = st.button("🔄 Refresh", key=f"si_refresh_{ticker_input}",
                           help="Clear cache and re-fetch 13F data")
    if si_refresh:
        clear_superinvestor_cache()
        st.session_state.pop(si_key, None)
    if si_key not in st.session_state:
        with st.spinner("Checking superinvestor 13F filings..."):
            st.session_state[si_key] = get_superinvestor_conviction(ticker_input)

    si = st.session_state[si_key]
    n_holders      = si.get("holder_count", 0)
    si_score       = si.get("conviction_score", 0)
    si_holders     = si.get("holders", [])
    si_period      = si.get("period", "")

    si_c1, si_c2, si_c3 = st.columns(3)
    with si_c1:
        color = "#2ecc71" if n_holders >= 3 else "#f39c12" if n_holders >= 1 else "#888"
        st.markdown(
            f"<div style='font-size:2em; font-weight:bold; color:{color}'>{n_holders}/13</div>",
            unsafe_allow_html=True
        )
        st.caption("Superinvestors holding")
    with si_c2:
        st.markdown(
            f"<div style='font-size:2em; font-weight:bold'>{si_score}/100</div>",
            unsafe_allow_html=True
        )
        st.caption("Conviction score")
    with si_c3:
        if si_period:
            st.caption(f"📅 Data as of {si_period}")
        st.caption("Source: SEC EDGAR 13F filings")

    if si_holders:
        st.markdown("**Holders:**")
        holder_cols = st.columns(min(len(si_holders), 3))
        for i, h in enumerate(si_holders):
            with holder_cols[i % 3]:
                pct_str = f"{h['pct']:.1f}% of portfolio" if h['pct'] > 0 else "< 0.1%"
                val_str = f"${h['value']/1e9:.2f}B" if h['value'] >= 1e9 else f"${h['value']/1e6:.0f}M"
                st.markdown(f"**{h['investor'].split('(')[0].strip()}**")
                st.caption(f"{pct_str} · {val_str}")
    elif n_holders == 0:
        st.info("No tracked superinvestors currently hold this stock — or it may be too small/foreign for 13F reporting.")

    with st.expander("🔍 Debug info"):
        st.caption(f"Info cols: {si.get('_debug_cols', [])}")
        st.caption(f"Sub cols: {si.get('_debug_sub_cols', [])}")
        st.caption(f"TVT col: {si.get('_debug_tvt_col')} | Date col: {si.get('_debug_date_col')}")
        st.caption(f"Li Lu accessions: {si.get('_debug_li_lu_accs', [])}")
        st.caption(f"Matched ({si.get('_debug_acc_count', 0)}): {si.get('_debug_acc_investors', [])}")
        st.caption(f"Missing: {si.get('_debug_missing', [])}")
        st.caption(f"Berkshire match: {si.get('_debug_brk_match', [])}")
        st.caption(f"In dataset but no {ticker_input} holding: {si.get('_debug_no_match', [])}")
        st.caption(f"Berkshire sample names: {si.get('_debug_brk_sample', [])}")
        if si.get("error"):
            st.text(si["error"][:600])

    st.divider()
    st.markdown("### 📊 Key Metrics at a Glance")
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("Free Cash Flow",   fmt_val(data.get('fcf')))
        st.metric("Owner Earnings",   fmt_val(data.get('owner_earnings')))
    with m2:
        st.metric("FCF Yield",        fmt_val(data.get('fcf_yield'), "pct"))
        st.metric("FCF Growth (1yr)", fmt_val(data.get('fcf_growth'), "pct"))
    with m3:
        st.metric("ROIC",             fmt_val(data.get('roic'), "pct"))
        st.metric("Gross Margin",     fmt_val(data.get('gross_margin'), "pct"))
    with m4:
        st.metric("Long-Term Debt/FCF",   fmt_val(data.get('debt_to_fcf'), "ratio"))
        st.metric("Price/Owner Earnings", fmt_val(data.get('price_owner_earn'), "ratio"))

    st.divider()
    st.markdown("### 💰 Income Potential at Your Position Size")
    div_yield = data.get('dividend_yield')
    if div_yield and position_size > 0:
        annual_income  = position_size * div_yield
        monthly_income = annual_income / 12
        from claude_utils import get_user_profile as _gup2
        _prof2         = _gup2()
        monthly_withdrawal = _prof2.get('monthly_withdrawal', THRESHOLDS['monthly_income_target'])
        target         = monthly_withdrawal
        pct_of_target  = monthly_income / target
        ic1, ic2, ic3  = st.columns(3)
        with ic1: st.metric("Dividend Yield",      f"{div_yield:.2%}")
        with ic2: st.metric("Est. Annual Income",  f"${annual_income:,.0f}")
        with ic3: st.metric("Est. Monthly Income", f"${monthly_income:,.0f}", delta=f"{pct_of_target:.0%} of your ${monthly_withdrawal:,.0f}/mo target")
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
    from claude_utils import get_user_profile as _gup
    _prof = _gup()
    _wd   = _prof.get('monthly_withdrawal', 8000)
    _age  = _prof.get('age', 57)
    _inf  = _prof.get('inflation', 4.0)
    st.info(
        f"⚠️ **Portfolio Reminder:** Prioritize companies with low debt, strong FCF, and "
        f"pricing power. At age {_age} with a ${_wd:,.0f}/month withdrawal target, "
        f"avoid permanent capital loss — recession-resilience matters more than maximum returns. "
        f"Inflation assumption: {_inf:.1f}%."
    )

    # ── Ask Claude Panel ──────────────────────────────────────────────
    st.divider()
    st.markdown("### 🤖 Ask Claude — SEC Filing Analysis")
    st.caption(
        "Claude reads the actual 10-K filing alongside the quantitative scores above — applying Buffett + Munger philosophy to your specific financial situation. "
        "Ask anything: red flags, management tone, moat durability, macro resilience. "
        "Conversation is multi-turn — follow up freely."
    )

    filing_key = f"sec_filing_{ticker_input}"
    if filing_key not in st.session_state:
        with st.spinner(f"📄 Fetching {ticker_input} 10-K from SEC EDGAR..."):
            st.session_state[filing_key] = fetch_10k_sections(ticker_input)

    filing_result = st.session_state[filing_key]
    sections      = filing_result.get("sections", {})
    filing_error  = filing_result.get("error")
    filing_url    = filing_result.get("filing_url")

    if filing_error:
        st.warning(f"⚠️ SEC filing issue: {filing_error}")
        if filing_url:
            st.markdown(f"[📋 View filings manually on EDGAR]({filing_url})")
    else:
        found_sections = [k for k, v in sections.items() if v]
        st.success(f"✅ 10-K loaded — sections found: {', '.join(found_sections) if found_sections else 'none'}.")
        if filing_url:
            st.markdown(f"[📋 View full 10-K on EDGAR]({filing_url})")

    convo_key   = f"claude_convo_{ticker_input}"
    context_key = f"claude_context_sent_{ticker_input}"
    if convo_key not in st.session_state:
        st.session_state[convo_key]   = []
        st.session_state[context_key] = False

    for msg in st.session_state[convo_key]:
        if msg["role"] == "user":
            display_content = msg["content"]
            if "\n---\nQUESTION: " in display_content:
                display_content = display_content.split("\n---\nQUESTION: ", 1)[-1]
            with st.chat_message("user"):
                st.markdown(display_content)
        else:
            with st.chat_message("assistant", avatar="🤖"):
                st.markdown(msg["content"])

    if not st.session_state[convo_key]:
        st.markdown("**Suggested questions:**")
        sq_cols = st.columns(2)
        starters = [
            "What are the biggest qualitative red flags in this filing?",
            "Does management's tone in the MD&A match the numbers?",
            "How resilient is this business in a credit crunch / financial repression environment?",
            "What does the filing say about competitive moat and pricing power?",
        ]
        for i, q in enumerate(starters):
            with sq_cols[i % 2]:
                if st.button(q, key=f"starter_{i}_{ticker_input}", use_container_width=True):
                    st.session_state[f"pending_claude_q_{ticker_input}"] = q
                    st.rerun()

    pending_q = st.session_state.pop(f"pending_claude_q_{ticker_input}", None)

    user_q   = st.chat_input(f"Ask Claude about {ticker_input}'s 10-K filing...",
                              key=f"claude_input_{ticker_input}")
    active_q = pending_q or user_q

    if active_q:
        scores_dict = {"rebalanced": rebalanced_score, "raw": raw_score, "verdict": verdict_label}

        with st.chat_message("user"):
            st.markdown(active_q)

        with st.chat_message("assistant", avatar="🤖"):
            with st.spinner("Reading the 10-K and thinking..."):
                if not st.session_state[context_key]:
                    # First turn: build full context and pass it WITH the question
                    from claude_utils import build_context, get_user_profile
                    profile     = get_user_profile()
                    context_str = build_context(ticker_input, data, scores_dict, sections, profile)
                    full_q      = f"{context_str}\n\n---\nQUESTION: {active_q}"
                    response = ask_claude_about_equity(
                        ticker=ticker_input, data=data, scores=scores_dict,
                        sections=sections, user_question=full_q,
                        conversation_history=None,
                        profile=profile,
                    )
                    st.session_state[convo_key].append({"role": "user", "content": full_q})
                    st.session_state[context_key] = True
                else:
                    # Subsequent turns: context already in history
                    response = ask_claude_about_equity(
                        ticker=ticker_input, data=data, scores=scores_dict,
                        sections=sections, user_question=active_q,
                        conversation_history=st.session_state[convo_key],
                    )
                    st.session_state[convo_key].append({"role": "user", "content": active_q})

                st.session_state[convo_key].append({"role": "assistant", "content": response})
                st.markdown(response)

    if st.session_state[convo_key]:
        if st.button("🗑️ Clear conversation", key=f"clear_convo_{ticker_input}"):
            st.session_state[convo_key]   = []
            st.session_state[context_key] = False
            st.rerun()

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
    | Debt / FCF | 20 pts | Balance sheet strength — can it survive a downturn? |
    | Gross Margin | 15 pts | Pricing power and moat durability |
    | Interest Coverage | 10 pts | Ability to service debt in a tightening credit environment |
    | Price / Owner Earnings | 25 pts | What you're paying per dollar of real earnings |

    **Score guide:** 80-100 = Strong Buy · 65-79 = Watch · 45-64 = Caution · <45 = Avoid

    When data is missing for a metric, the score is **rebalanced** across available metrics so companies
    aren't unfairly penalized for data gaps. Missing metrics are highlighted in grey.

    *Data sourced from Polygon.io SEC filings.*
    """)
