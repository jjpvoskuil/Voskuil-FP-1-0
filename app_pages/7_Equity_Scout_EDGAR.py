import streamlit as st
import requests
import plotly.graph_objects as go
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from sec_utils import fetch_10k_sections, fetch_company_facts, safe_float, fmt_val, fetch_price_and_market_cap, fetch_fundamentals_edgar_cached, compute_dcf_value, DCF_DEFAULTS, compute_residual_income_value, RESIDUAL_INCOME_DEFAULTS, score_financial_firm_display, investment_verdict
from claude_utils import ask_claude_about_equity
from ui_utils import scroll_to_element, render_sidebar_refresh_controls
from superinvestor_utils import get_superinvestor_conviction, clear_superinvestor_cache
from watchlist_utils import add_to_watchlist, is_watchlisted

POLY_URL = "https://api.polygon.io"

def poly_get(endpoint, params={}):
    try:
        key = st.secrets["POLYGON_KEY"]
        r   = requests.get(f"{POLY_URL}{endpoint}", params={**params, "apiKey": key}, timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None

def fval(obj, key):
    try:    return float(obj[key]["value"])
    except: return None

@st.cache_data(ttl=3600)
def fetch_fundamentals_polygon(ticker):
    """Slim Polygon fetch — same logic as 1_Equity_Scout.py fetch_fundamentals."""
    try:
        det   = (poly_get(f"/v3/reference/tickers/{ticker}") or {}).get("results", {})
        market_cap = safe_float(det.get("market_cap"))
        shares     = safe_float(det.get("weighted_shares_outstanding"))
        name       = det.get("name", ticker)
        sector     = det.get("sic_description", "N/A")

        price_data = poly_get(f"/v2/aggs/ticker/{ticker}/prev")
        price = None
        try:    price = float(price_data["results"][0]["c"])
        except: pass

        fin = poly_get("/vX/reference/financials", {
            "ticker": ticker, "timeframe": "annual", "limit": 2,
            "order": "desc", "sort": "period_of_report_date",
        })
        if not fin or not fin.get("results"):
            return {"error": "No Polygon financials returned"}

        results = fin["results"]
        f  = results[0]["financials"]
        f2 = results[1]["financials"] if len(results) > 1 else {}

        inc  = f.get("income_statement",    {})
        cf   = f.get("cash_flow_statement", {})
        bs   = f.get("balance_sheet",       {})
        cf2  = (f2 or {}).get("cash_flow_statement", {})

        op_cf  = fval(cf,  "net_cash_flow_from_operating_activities")
        inv_cf = fval(cf,  "net_cash_flow_from_investing_activities")
        fcf    = (op_cf + inv_cf) if (op_cf is not None and inv_cf is not None) else None

        op_cf2  = fval(cf2, "net_cash_flow_from_operating_activities")
        inv_cf2 = fval(cf2, "net_cash_flow_from_investing_activities")
        fcf2    = (op_cf2 + inv_cf2) if (op_cf2 and inv_cf2) else None
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

        op_income    = fval(inc, "operating_income_loss")
        interest_exp = fval(inc, "interest_expense_operating")
        int_cov      = (op_income / interest_exp) if (interest_exp and interest_exp > 0 and op_income is not None) else None
        is_nc        = (int_cov is None and interest_exp is None)

        dna_proxy  = (op_cf - net_income) if (op_cf and net_income) else None
        capex_abs  = abs(inv_cf) if inv_cf else 0
        owner_earn = (net_income + (dna_proxy or 0) - capex_abs) if net_income is not None else None
        poe        = (price / (owner_earn / shares)) if (owner_earn and owner_earn > 0 and shares and price) else None

        return {
            "name": name, "sector": sector, "market_cap": market_cap,
            "price": price, "shares": shares,
            "fcf": fcf, "fcf_yield": fcf_yield, "fcf_growth": fcf_growth,
            "gross_margin": gross_margin, "gross_profit": gross_profit, "revenues": revenues,
            "roic": roic, "net_income": net_income,
            "long_term_debt": long_term_debt, "debt_to_fcf": debt_to_fcf,
            "interest_coverage": int_cov, "is_net_creditor": is_nc,
            "owner_earnings": owner_earn, "price_owner_earn": poe,
            "op_cf": op_cf, "inv_cf": inv_cf,
            "data_source": "Polygon",
        }
    except Exception as e:
        return {"error": str(e)}

st.set_page_config(page_title="Equity Scout — EDGAR", layout="wide")
render_sidebar_refresh_controls()

APP_URL = "https://voskuil-fp-1-0-k85bd7afbw8dnqeftzxwbu.streamlit.app"

# (#34) kept in sync with sec_utils.DEFAULT_WEIGHTS — ROIC upweighted
# 20 -> 40 (10-yr avg, cash basis); other four rescaled x0.75 so the
# total still sums to 100 (enforced by the weight editor below).
DEFAULT_WEIGHTS = {
    "FCF Yield":         22,
    "ROIC":              40,
    "Debt / FCF":        19,
    "Gross Margin":      11,
    "Interest Coverage":  8,
}

# ── Migrate stale session state from old 6-criteria weights ──────────
# If scoring_weights still contains the removed "Price / Owner Earnings"
# key, or if any weight value exceeds the old caps (max was 40/60 before,
# now it's 100), the session is stale — reset to current defaults so the
# sliders render correctly. This one-time migration fires whenever the
# page loads with stale session state from a pre-update session.
_needs_reset = (
    "Price / Owner Earnings" in st.session_state.get("scoring_weights", {})
    or "Price / Owner Earnings" in st.session_state.get("committed_weights", {})
    or st.session_state.get("scoring_weights", {}) == {}
)
if _needs_reset:
    st.session_state["scoring_weights"]   = DEFAULT_WEIGHTS.copy()
    st.session_state["committed_weights"] = DEFAULT_WEIGHTS.copy()
    for _k in ["w_fcf_e", "w_roic_e", "w_debt_e", "w_gm_e", "w_ic_e", "w_poe_e"]:
        st.session_state.pop(_k, None)

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


def score_stock(data, weights):
    criteria = []

    max_pts   = weights["FCF Yield"]
    fcf_yield = data.get("fcf_yield")
    if fcf_yield is not None:
        if fcf_yield >= THRESHOLDS["fcf_yield_great"]:  pts, verdict = max_pts, "Excellent"
        elif fcf_yield >= THRESHOLDS["fcf_yield_good"]: pts, verdict = round(max_pts * 0.60), "Good"
        elif fcf_yield > 0:                             pts, verdict = round(max_pts * 0.15), "Weak"
        else:                                           pts, verdict = 0, "Negative FCF"
    else:
        pts, verdict = 0, "No Data"
    criteria.append({"name": "Free Cash Flow Yield",
                     "value": f"{fcf_yield:.1%}" if fcf_yield is not None else "N/A",
                     "points_earned": pts, "points_max": max_pts, "verdict": verdict,
                     "note": "Buffett: 'The most important thing for me is figuring out how big a moat there is around the business and the cash it generates.' FCF yield is what you actually earn as an owner — not accounting profits.",
                     "missing": fcf_yield is None})

    max_pts = weights["ROIC"]
    # (#34) 10-yr average, cash-accounting basis — not the single latest
    # year. Fewer than 5 reliable years of history -> roic_10yr_avg is
    # None (see sec_utils._fetch_company_facts_for_cik), same "missing"
    # path as any other unavailable metric.
    roic    = data.get("roic_10yr_avg")
    if roic is not None:
        if roic >= THRESHOLDS["roic_great"]:   pts, verdict = max_pts, "Exceptional"
        elif roic >= THRESHOLDS["roic_good"]:  pts, verdict = round(max_pts * 0.60), "Strong"
        elif roic > 0:                         pts, verdict = round(max_pts * 0.20), "Below Average"
        else:                                  pts, verdict = 0, "Destroying Capital"
    else:
        pts, verdict = 0, "No Data"
    criteria.append({"name": "Return on Invested Capital (ROIC)",
                     "value": f"{roic:.1%} (10yr avg, cash basis)" if roic is not None else "N/A",
                     "points_earned": pts, "points_max": max_pts, "verdict": verdict,
                     "note": "Munger's capital allocation test: management that consistently earns 20%+ ROIC (10-yr average, cash accounting basis) is compounding your wealth. Below 12% means they're destroying value with every reinvestment dollar. 12%+ sustained for 10+ years is a strong signal of durable competitive advantage.",
                     "missing": roic is None})

    # Debt gate (#32): dual-hurdle, mirroring sec_utils.score_stock_breakdown()
    # — pass if EITHER Debt/FCF OR Debt/CADS clears the bar, so structural
    # float-users with thin/negative FCF but healthy operating cash
    # generation aren't penalized by a naive FCF-based multiple alone.
    max_pts   = weights["Debt / FCF"]
    debt_fcf  = data.get("debt_to_fcf")
    debt_cads = data.get("debt_to_cads")
    ic        = data.get("interest_coverage") or 0
    is_nc     = data.get("is_net_creditor", False)
    candidates    = [d for d in (debt_fcf, debt_cads) if d is not None]
    debt_multiple = min(candidates) if candidates else None
    if debt_multiple is not None:
        if debt_multiple < THRESHOLDS["debt_fcf_safe"]:      pts, verdict = max_pts, "Fortress"
        elif debt_multiple < THRESHOLDS["debt_fcf_warning"]: pts, verdict = round(max_pts * 0.50), "Manageable"
        elif ic >= THRESHOLDS["interest_coverage_safe"] or is_nc:
                                                         pts, verdict = round(max_pts * 0.50), "High Debt, Well Covered"
        else:                                            pts, verdict = 0, "Overleveraged"
    else:
        pts, verdict = 0, "No Data"
    debt_label = "Debt / CADS" if (debt_multiple is not None and debt_multiple == debt_cads and debt_fcf != debt_cads) else "Debt / Free Cash Flow"
    criteria.append({"name": "Debt / Free Cash Flow",
                     "value": f"{debt_multiple:.1f}x ({debt_label})" if debt_multiple is not None else "N/A",
                     "points_earned": pts, "points_max": max_pts, "verdict": verdict,
                     "note": "Munger's inversion: 'What kills a great business?' Excessive debt when capital becomes scarce. A fortress balance sheet means never being a forced seller. Under 3x Debt/FCF (or Debt/CADS) = structural survivor.",
                     "missing": debt_multiple is None})

    max_pts = weights["Gross Margin"]
    gm      = data.get("gross_margin")
    if gm is not None:
        if gm >= THRESHOLDS["gross_margin_great"]:  pts, verdict = max_pts, "Wide Moat"
        elif gm >= THRESHOLDS["gross_margin_good"]: pts, verdict = round(max_pts * 0.67), "Solid Moat"
        else:                                       pts, verdict = round(max_pts * 0.20), "Commodity Risk"
    else:
        pts, verdict = 0, "No Data"
    criteria.append({"name": "Gross Margin (Pricing Power)",
                     "value": f"{gm:.1%}" if gm is not None else "N/A",
                     "points_earned": pts, "points_max": max_pts, "verdict": verdict,
                     "note": "Buffett: 'The single most important decision in evaluating a business is pricing power.' Gross margin above 60% signals a structural moat — brand, switching costs, or network effects at work.",
                     "missing": gm is None})

    max_pts = weights["Interest Coverage"]
    ic_val  = data.get("interest_coverage")
    is_nc   = data.get("is_net_creditor", False)
    if is_nc:
        pts, verdict = max_pts, "Net Creditor ✨"
    elif ic_val is not None:
        if ic_val >= THRESHOLDS["interest_coverage_safe"]: pts, verdict = max_pts, "Safe"
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


    raw_score     = sum(c["points_earned"] for c in criteria)
    missing_pts   = sum(c["points_max"] for c in criteria if c.get("missing"))
    missing_names = [c["name"] for c in criteria if c.get("missing")]
    available_pts = 100 - missing_pts
    rebalanced    = round(raw_score / available_pts * 100) if available_pts > 0 else raw_score

    return raw_score, rebalanced, missing_names, criteria


# (verdict overhaul) Thin wrapper around sec_utils.investment_verdict() --
# same quality+value gate Dashboard uses for its Add/Hold/Trim Signal, so
# a name doesn't score "Strong Buy" here while showing "Hold" on
# Dashboard for the same underlying reason (see investment_verdict()'s
# docstring for the NVDA case this fixes: excellent quality but priced
# above the value gate's P/Owner-Earnings and FCF-yield bars). Score
# alone no longer drives the headline verdict -- it's quality+value,
# same as everywhere else the verdict is shown; the numeric Conviction
# Score below still reflects quality alone.
_TIER_TO_RESEARCH_LABEL = {"buy": "Strong Buy", "hold": "Hold", "avoid": "Avoid", "unrated": "—"}

def score_to_verdict(data):
    v = investment_verdict(data)
    return _TIER_TO_RESEARCH_LABEL[v["tier"]], v["color"]


# ── Query params ─────────────────────────────────────────────────────────────
params       = st.query_params
url_ticker   = params.get("ticker", "").upper().strip()
if not url_ticker and "dive_ticker" in st.session_state:
    url_ticker = st.session_state.pop("dive_ticker", "").upper().strip()
auto_analyze = bool(url_ticker)

st.title("🔍 Equity Scout — EDGAR")
st.caption("Concentrated, Buffett-style fundamental analysis. Primary data: SEC EDGAR Company Facts API.")

st.info(
"🏛️ Fundamentals sourced directly from **SEC EDGAR** Company Facts API. No Polygon dependency.",
    icon="🔬"
)

st.markdown("> *\"Price is what you pay. Value is what you get.\"* — Warren Buffett")

if url_ticker:
    col_back, _ = st.columns([1, 4])
    with col_back:
        if st.button("← Back to Dashboard"):
            st.switch_page("app_pages/0_Dashboard.py")
    st.info(f"📌 Analyzing **{url_ticker}** — arrived from Holdings Explorer.")

st.divider()

# ── Weight reset handler ─────────────────────────────────────────────────────
_weight_map = [
    ("w_fcf_e",  "FCF Yield"),
    ("w_roic_e", "ROIC"),
    ("w_debt_e", "Debt / FCF"),
    ("w_gm_e",   "Gross Margin"),
    ("w_ic_e",   "Interest Coverage"),

]
for _wkey, _mkey in _weight_map:
    if st.session_state.pop(f"pending_reset_{_wkey}", False):
        st.session_state[_wkey] = DEFAULT_WEIGHTS[_mkey]
        st.session_state.scoring_weights[_mkey] = DEFAULT_WEIGHTS[_mkey]

with st.expander("⚙️ Customize Scoring Weights", expanded=False):
    st.caption("Weights shared across all pages. Set them here and they carry through.")
    if "scoring_weights"   not in st.session_state:
        st.session_state.scoring_weights   = DEFAULT_WEIGHTS.copy()
    if "committed_weights" not in st.session_state:
        st.session_state.committed_weights = DEFAULT_WEIGHTS.copy()
    sw = st.session_state.scoring_weights
    rc1, rc2, rc3 = st.columns([1.2, 1.2, 4])
    if rc1.button("↺ Reset to Defaults", key="es_e_reset_weights"):
        st.session_state.scoring_weights   = DEFAULT_WEIGHTS.copy()
        st.session_state.committed_weights = DEFAULT_WEIGHTS.copy()
        for _wkey, _mkey in _weight_map:
            st.session_state[_wkey] = DEFAULT_WEIGHTS[_mkey]
        st.rerun()
    draft_weights = {
        "FCF Yield":              st.session_state.get("w_fcf_e",  sw["FCF Yield"]),
        "ROIC":                   st.session_state.get("w_roic_e", sw["ROIC"]),
        "Debt / FCF":             st.session_state.get("w_debt_e", sw["Debt / FCF"]),
        "Gross Margin":           st.session_state.get("w_gm_e",   sw["Gross Margin"]),
        "Interest Coverage":      st.session_state.get("w_ic_e",   sw["Interest Coverage"]),

    }
    draft_total = sum(draft_weights.values())
    apply_ok    = draft_total == 100
    if rc2.button("✅ Apply Weights", key="es_e_apply_weights", type="primary", disabled=not apply_ok,
                  help="Activates weights for scoring." if apply_ok else f"Total must equal 100 (currently {draft_total})."):
        st.session_state.committed_weights = draft_weights.copy()
        st.session_state.scoring_weights   = draft_weights.copy()
        st.rerun()
    cw = st.session_state.committed_weights
    rc3.caption(
        f"**Active:** FCF {cw['FCF Yield']} · ROIC {cw['ROIC']} · Debt {cw['Debt / FCF']} · "
        f"GM {cw['Gross Margin']} · IC {cw['Interest Coverage']}"
    )
    w_col1, w_col2 = st.columns(2)
    with w_col1:
        _sc, _sb = st.columns([4, 1])
        with _sc: w_fcf = st.slider("FCF Yield",  0, 100, sw["FCF Yield"], step=5, key="w_fcf_e")
        with _sb:
            st.write("")
            if st.button(f"↺ {DEFAULT_WEIGHTS['FCF Yield']}", key="reset_w_fcf_e", use_container_width=True):
                st.session_state["pending_reset_w_fcf_e"] = True; st.rerun()
        _sc, _sb = st.columns([4, 1])
        with _sc: w_roic = st.slider("ROIC",       0, 100, sw["ROIC"], step=5, key="w_roic_e")
        with _sb:
            st.write("")
            if st.button(f"↺ {DEFAULT_WEIGHTS['ROIC']}", key="reset_w_roic_e", use_container_width=True):
                st.session_state["pending_reset_w_roic_e"] = True; st.rerun()
        _sc, _sb = st.columns([4, 1])
        with _sc: w_debt = st.slider("Debt / FCF", 0, 100, sw["Debt / FCF"], step=5, key="w_debt_e")
        with _sb:
            st.write("")
            if st.button(f"↺ {DEFAULT_WEIGHTS['Debt / FCF']}", key="reset_w_debt_e", use_container_width=True):
                st.session_state["pending_reset_w_debt_e"] = True; st.rerun()
    with w_col2:
        _sc, _sb = st.columns([4, 1])
        with _sc: w_gm   = st.slider("Gross Margin",      0, 100, sw["Gross Margin"], step=5, key="w_gm_e")
        with _sb:
            st.write("")
            if st.button(f"↺ {DEFAULT_WEIGHTS['Gross Margin']}", key="reset_w_gm_e", use_container_width=True):
                st.session_state["pending_reset_w_gm_e"] = True; st.rerun()
        _sc, _sb = st.columns([4, 1])
        with _sc: w_ic   = st.slider("Interest Coverage", 0, 100, sw["Interest Coverage"], step=5, key="w_ic_e")
        with _sb:
            st.write("")
            if st.button(f"↺ {DEFAULT_WEIGHTS['Interest Coverage']}", key="reset_w_ic_e", use_container_width=True):
                st.session_state["pending_reset_w_ic_e"] = True; st.rerun()

    active_weights = {
        "FCF Yield": w_fcf, "ROIC": w_roic, "Debt / FCF": w_debt,
        "Gross Margin": w_gm, "Interest Coverage": w_ic,
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
    ticker_input = st.text_input(
        "Enter a stock ticker", value=url_ticker,
        placeholder="e.g. COST, MSFT, KO, V",
        label_visibility="collapsed"
    ).strip().upper()
with col_btn:
    analyze = st.button("🔎 Analyze", use_container_width=True, type="primary")

with st.expander("💼 Position Sizing Context (optional)"):
    position_size = st.number_input(
        "How much are you considering investing? ($)",
        min_value=0, value=100000, step=10000, format="%d"
    )

if auto_analyze and url_ticker and not analyze:
    analyze      = True
    ticker_input = url_ticker

_cache_key = f"es_edgar_results_{ticker_input}" if ticker_input else None

# ── Run analysis — both sources fire on single button click ───────────────────
# _just_analyzed (#76) tracks whether THIS run is the one that actually
# computed fresh results, vs. the elif branch below just redisplaying
# cached results on an unrelated rerun (chat, etc.) -- only the former
# should scroll the results into view.
_just_analyzed = False
if analyze and ticker_input:
    _just_analyzed = True
    total_weight = sum(st.session_state.get("committed_weights", DEFAULT_WEIGHTS).values())
    if total_weight != 100:
        st.warning(f"Weights add up to {total_weight}, not 100. Adjust sliders for accurate scores.")

    with st.spinner(f"🏛️ Loading **{ticker_input}** from the EDGAR cache..."):
        data = fetch_fundamentals_edgar_cached(ticker_input)

    # (punch list #76) Cache errors are blocking, same as a live EDGAR
    # error used to be -- can't show the page without fundamentals. A
    # cache_miss gets a pointer to the sidebar refresh control instead of
    # a raw EDGAR error string, since there's nothing EDGAR-specific to
    # show the user in that case.
    if data.get("error"):
        if data.get("cache_miss"):
            st.error(f"{data['error']}")
        else:
            st.error(f"EDGAR fetch failed for {ticker_input}: {data['error']}")
        st.stop()
    if data.get("cache_stale"):
        st.caption(
            f"ℹ️ Cached EDGAR data as of {data.get('cache_fetched_at', 'unknown')} "
            "— use 'Refresh EDGAR data' in the sidebar for the latest filing."
        )

    # Financial/cyclical firm warnings
    financial_subtype = data.get("financial_subtype")
    if financial_subtype in ("bank", "insurance"):
        st.info(
            f"🏦 **{financial_subtype.title()} detected** (SIC {data.get('sic')}) — "
            "using the alternative bank/insurer scoring framework (ROE, Equity/Assets, "
            + ("Efficiency Ratio, Net Interest Margin, Provision/NI" if financial_subtype == "bank" else "Combined Ratio")
            + ") instead of standard FCF/ROIC/Gross Margin. See punch list #36/#70."
        )
    elif data.get("is_financial"):
        st.warning(
            f"⚠️ **Financial firm detected** (SIC {data.get('sic')}) — "
            "Standard FCF/debt/margin scoring is unreliable for brokers, REITs, and other financials. "
            "Score shown for reference only. See punch list #70."
        )
    if data.get("is_cyclical"):
        st.warning(
            f"⚠️ **Cyclical firm detected** (SIC {data.get('sic')}) — "
            "Single-period scoring reflects where this company is in the cycle, not intrinsic value. "
            "Full-cycle analysis recommended. See punch list #37."
        )

    if financial_subtype in ("bank", "insurance"):
        rebalanced_score, criteria = score_financial_firm_display(data, financial_subtype)
        raw_score     = sum(c["points_earned"] for c in criteria)
        missing_names = [c["name"] for c in criteria if c.get("missing")]
    else:
        raw_score, rebalanced_score, missing_names, criteria = score_stock(data, weights)
    verdict_label, verdict_color = score_to_verdict(data)

    st.session_state[_cache_key] = {
        "data": data, "raw_score": raw_score, "rebalanced_score": rebalanced_score,
        "missing_names": missing_names, "criteria": criteria,
        "verdict_label": verdict_label, "verdict_color": verdict_color,
    }

elif _cache_key and _cache_key in st.session_state:
    _c               = st.session_state[_cache_key]
    data             = _c["data"]
    raw_score        = _c["raw_score"]
    rebalanced_score = _c["rebalanced_score"]
    missing_names    = _c["missing_names"]
    criteria         = _c["criteria"]
    verdict_label    = _c["verdict_label"]
    verdict_color    = _c["verdict_color"]

# ── Render results ────────────────────────────────────────────────────────────
if _cache_key and _cache_key in st.session_state:

    st.markdown('<div id="es-analysis-results"></div>', unsafe_allow_html=True)
    if _just_analyzed:
        scroll_to_element("es-analysis-results")

    st.markdown(f"## {data.get('name', ticker_input)}")
    price_str  = f"${data.get('price', 0) or 0:,.2f} per share" if data.get("price") else "Price unavailable"
    mktcap_str = f"Market Cap: ${(data.get('market_cap') or 0)/1e9:.1f}B" if data.get("market_cap") else ""
    fy_str     = f"FY{data.get('fiscal_year', '')}" if data.get("fiscal_year") else ""
    st.caption(f"{data.get('sector', '')}  ·  {price_str}  ·  {mktcap_str}  ·  {fy_str}")

    # (2026-07-23) Banks/insurers don't run through FCF-DCF at all --
    # compute_dcf_value() itself refuses (see its financial_subtype
    # guard) since "fcf" isn't economically meaningful for a financial
    # firm. This is the same split Market Screener's debug tool uses:
    # Residual Income assumptions (cost of equity / terminal growth /
    # projection years) for banks & insurers, DCF assumptions otherwise.
    _is_financial = financial_subtype in ("bank", "insurance")
    ri = None
    if _is_financial:
        with st.expander("⚙️ Residual Income Assumptions", expanded=False):
            st.caption(
                "Residual income (excess return) model, shown single-stage AND multi-stage: "
                "Intrinsic Value = Book Value + PV of (ROE − Cost of Equity) × Book Value each year. "
                "Multi-stage fades current ROE toward this company's own normalized (10-yr average) "
                "ROE over the projection window — a wide single-vs-multi gap means current ROE is "
                "running well outside its own historical normal range, worth a closer look."
            )
            _rc1, _rc2, _rc3 = st.columns(3)
            with _rc1:
                _coe = st.number_input("Cost of equity (%)", min_value=4.0, max_value=20.0,
                                        value=RESIDUAL_INCOME_DEFAULTS["cost_of_equity"] * 100, step=0.5,
                                        key=f"ri_coe_{ticker_input}") / 100
            with _rc2:
                _ritg = st.number_input("Terminal growth (%)", min_value=0.0, max_value=5.0,
                                         value=RESIDUAL_INCOME_DEFAULTS["terminal_growth"] * 100, step=0.25,
                                         key=f"ri_tg_{ticker_input}") / 100
            with _rc3:
                _riyrs = st.number_input("Projection years", min_value=5, max_value=20,
                                          value=RESIDUAL_INCOME_DEFAULTS["projection_years"], step=1,
                                          key=f"ri_yrs_{ticker_input}")
        ri = compute_residual_income_value(
            data, {"cost_of_equity": _coe, "terminal_growth": _ritg, "projection_years": _riyrs})
        dcf = None
    else:
        with st.expander("⚙️ DCF Assumptions", expanded=False):
            st.caption(
                "Two-stage discounted cash flow: FCF is projected forward using a growth rate derived "
                "from this company's own historical FCF trend (capped to keep extrapolation sane), then "
                "a Gordon Growth terminal value. Simplification: FCF here already reflects post-interest "
                "cash flow, so it's treated as cash flow to equity directly — no separate net-debt adjustment."
            )
            _dc1, _dc2, _dc3 = st.columns(3)
            with _dc1:
                _dr = st.number_input("Discount rate (%)", min_value=4.0, max_value=20.0,
                                       value=DCF_DEFAULTS["discount_rate"] * 100, step=0.5,
                                       key=f"dcf_dr_{ticker_input}") / 100
            with _dc2:
                _tg = st.number_input("Terminal growth (%)", min_value=0.0, max_value=5.0,
                                       value=DCF_DEFAULTS["terminal_growth"] * 100, step=0.25,
                                       key=f"dcf_tg_{ticker_input}") / 100
            with _dc3:
                _yrs = st.number_input("Projection years", min_value=5, max_value=20,
                                        value=DCF_DEFAULTS["projection_years"], step=1,
                                        key=f"dcf_yrs_{ticker_input}")
        # (result moved next to the Score gauge below, per owner feedback --
        # was previously shown here, disconnected from the score) -- just
        # compute it here, where the assumption inputs are.
        dcf = compute_dcf_value(data, {"discount_rate": _dr, "terminal_growth": _tg, "projection_years": _yrs})

    if data.get("description"):
        st.markdown(f"*{data['description']}*")

    # Source banner
    cik = data.get("cik", "")
    sec_link   = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker_input}&type=10-K&dateb=&owner=include&count=10"
    yahoo_link = f"https://finance.yahoo.com/quote/{ticker_input}"
    edgar_link = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json" if cik else None

    rl1, rl2, rl3, rl4, rl5 = st.columns([1, 1, 1, 3.5, 1.3])
    with rl1: st.link_button("📋 SEC Filings", sec_link)
    with rl2: st.link_button("📈 Yahoo Finance", yahoo_link)
    if edgar_link:
        with rl3: st.link_button("🏛️ EDGAR Facts", edgar_link)
    with rl5:
        # Add-only control (#68) -- removal only happens on the Watchlist
        # page itself. See Dashboard's matching control for the rationale.
        _already_watched = is_watchlisted(ticker_input)
        _watch_checked = st.checkbox(
            "⭐ Watchlist", value=_already_watched, key=f"scout_watch_{ticker_input}",
            disabled=_already_watched,
            help="On Watchlist" if _already_watched else "Add to Watchlist",
        )
        if _watch_checked and not _already_watched:
            add_to_watchlist(ticker_input, name=data.get('name', ticker_input), source="Equity Scout")
            st.rerun()

    # Data source badge
    missing_concepts = data.get("missing_concepts", [])
    if missing_concepts:
        st.caption(f"📡 Data: SEC EDGAR Company Facts  ·  Pricing: yfinance  ·  Missing XBRL concepts: {len(missing_concepts)}")
    else:
        st.caption("📡 Data: SEC EDGAR Company Facts (primary)  ·  Pricing: yfinance")
    _foreign_ccy = data.get("foreign_currency")
    if _foreign_ccy:
        st.caption(f"💱 Financials reported in {_foreign_ccy} — converted to USD using historical FX rates, period by period (#11)")

    st.divider()

    left, right = st.columns([1, 2])
    with left:
        fig = go.Figure(go.Indicator(
            mode="gauge+number", value=rebalanced_score,
            domain={"x": [0, 1], "y": [0, 1]},
            title={"text": "Conviction Score", "font": {"size": 16}},
            gauge={
                "axis": {"range": [0, 100], "tickwidth": 1},
                "bar":  {"color": verdict_color},
                "steps": [
                    {"range": [0, 45],   "color": "#fadbd8"},
                    {"range": [45, 65],  "color": "#fdebd0"},
                    {"range": [65, 80],  "color": "#fef9e7"},
                    {"range": [80, 100], "color": "#eafaf1"},
                ],
                "threshold": {"line": {"color": verdict_color, "width": 4},
                              "thickness": 0.75, "value": rebalanced_score}
            }
        ))
        fig.update_layout(height=260, margin=dict(t=30, b=0, l=20, r=20))
        st.plotly_chart(fig, use_container_width=True)
        st.markdown(
            f"<div style='text-align:center; font-size:1.4em; font-weight:bold; color:{verdict_color}'>"
            f"{verdict_label}</div>", unsafe_allow_html=True
        )

        # Intrinsic Value, right next to the score (#owner feedback --
        # previously shown up near the header, disconnected from the score;
        # this is the "does the price support the recommendation" check,
        # so it belongs right where the recommendation itself is).
        #
        # (2026-07-23) Banks/insurers show the Residual Income comparison
        # (single-stage + multi-stage side by side, with a divergence
        # flag) instead of the DCF block -- same convention as Market
        # Screener's debug tool, since compute_dcf_value() now refuses to
        # run for these tickers at all.
        _current_price = data.get("price")
        if _is_financial:
            if ri["error"]:
                st.caption(f"💰 **Intrinsic Value:** — _{ri['error']}_")
            else:
                st.metric("Current Price", f"${_current_price:.2f}" if _current_price else "N/A")
                st.caption(
                    f"Book Value: ${ri['book_value_per_share']:.2f}/sh  ·  "
                    f"Current ROE: {ri['current_roe']:.1%}  ·  Normalized ROE: {ri['normalized_roe']:.1%}"
                    if ri["book_value_per_share"] is not None and ri["current_roe"] is not None and ri["normalized_roe"] is not None
                    else ""
                )
                riv1, riv2 = st.columns(2)
                with riv1:
                    st.markdown("**Single-Stage**")
                    _ss = ri["single_stage"]
                    if _ss["error"]:
                        st.caption(f"— _{_ss['error']}_")
                    else:
                        st.metric("Intrinsic Value", f"${_ss['intrinsic_value_per_share']:.2f}/sh")
                        _ss_mos = _ss["margin_of_safety"]
                        if _ss_mos is not None:
                            _c = "#2ecc71" if _ss_mos > 0 else "#e74c3c"
                            st.markdown(f"<span style='color:{_c}; font-weight:bold'>{_ss_mos:+.0%} MoS</span>",
                                        unsafe_allow_html=True)
                with riv2:
                    st.markdown("**Multi-Stage**")
                    _ms = ri["multi_stage"]
                    if _ms["error"]:
                        st.caption(f"— _{_ms['error']}_")
                    else:
                        st.metric("Intrinsic Value", f"${_ms['intrinsic_value_per_share']:.2f}/sh")
                        _ms_mos = _ms["margin_of_safety"]
                        if _ms_mos is not None:
                            _c = "#2ecc71" if _ms_mos > 0 else "#e74c3c"
                            st.markdown(f"<span style='color:{_c}; font-weight:bold'>{_ms_mos:+.0%} MoS</span>",
                                        unsafe_allow_html=True)
                _div = ri["divergence"]
                if _div is not None:
                    if _div >= 0.30:
                        st.warning(f"⚠️ {_div:.0%} divergence between single- and multi-stage — "
                                   f"current ROE is running well outside this company's own normal range.")
                    elif _div >= 0.15:
                        st.info(f"ℹ️ {_div:.0%} divergence between single- and multi-stage.")
                    else:
                        st.caption(f"✓ Single- and multi-stage agree within {_div:.0%}.")
                st.caption(
                    f"_Residual income: {ri['cost_of_equity']:.1%} cost of equity, "
                    f"{ri['terminal_growth']:.1%} terminal growth, {ri['projection_years']}-yr multi-stage window "
                    f"— see ⚙️ Residual Income Assumptions above to adjust_"
                )
        elif dcf["error"]:
            st.caption(f"💰 **Intrinsic Value:** — _{dcf['error']}_")
        else:
            _mos = dcf["margin_of_safety"]
            _mos_color = "#2ecc71" if (_mos or 0) > 0 else "#e74c3c"
            iv1, iv2 = st.columns(2)
            with iv1:
                st.metric("Intrinsic Value", f"${dcf['intrinsic_value_per_share']:.2f}/sh")
            with iv2:
                st.metric("Current Price", f"${_current_price:.2f}" if _current_price else "N/A")
            if _mos is not None:
                st.markdown(
                    f"<div style='text-align:center; font-size:1.1em; font-weight:bold; color:{_mos_color}'>"
                    f"{_mos:+.0%} Margin of Safety</div>", unsafe_allow_html=True
                )
            st.caption(
                f"_10-yr DCF: {dcf['growth_rate']:.1%} FCF growth, {dcf['discount_rate']:.1%} discount, "
                f"{dcf['terminal_growth']:.1%} terminal growth — see ⚙️ DCF Assumptions above to adjust_"
            )

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
            earned  = c["points_earned"]
            maximum = c["points_max"]
            pct     = earned / maximum if maximum > 0 else 0
            if c.get("missing"):
                bar_color, icon = "#888888", "⬜"
            elif pct >= 0.8:   bar_color, icon = "#2ecc71", "✅"
            elif pct >= 0.5:   bar_color, icon = "#f39c12", "⚠️"
            else:              bar_color, icon = "#e74c3c", "❌"
            st.markdown(
                f"{icon} **{c['name']}** — `{c['value']}` &nbsp;&nbsp;"
                f"<span style='color:{bar_color}'>{c['verdict']}</span> &nbsp;·&nbsp; {earned}/{maximum} pts",
                unsafe_allow_html=True
            )
            st.progress(pct)
            st.caption(c["note"])

    st.divider()

    # ── EDGAR Data Transparency Panel (new — not in Polygon version) ──────────
    with st.expander("🏛️ EDGAR Raw Data — What's Driving This Score", expanded=False):
        st.caption(
            "Full transparency on the underlying SEC-filed numbers feeding each metric. "
            "This is the primary source data — no Polygon normalization layer between you and the filing."
        )
        latest = data.get("_latest", {})
        d1, d2, d3 = st.columns(3)
        with d1:
            st.markdown("**Cash Flow**")
            st.metric("Operating CF",  fmt_val(data.get("op_cf")))
            st.metric("Investing CF",  fmt_val(data.get("inv_cf")))
            st.metric("Free CF",       fmt_val(data.get("fcf")))
            st.metric("D&A",           fmt_val(data.get("dna")))
        with d2:
            st.markdown("**Income Statement**")
            st.metric("Revenue",       fmt_val(data.get("revenues")))
            st.metric("Gross Profit",  fmt_val(data.get("gross_profit")))
            st.metric("Net Income",    fmt_val(data.get("net_income")))
            st.metric("Owner Earnings",fmt_val(data.get("owner_earnings")))
        with d3:
            st.markdown("**Balance Sheet**")
            st.metric("Long-Term Debt", fmt_val(data.get("long_term_debt")))
            st.metric("Short-Term Debt",fmt_val(data.get("short_term_debt")))
            st.metric("Total Debt",     fmt_val(data.get("total_debt")))
            st.metric("Shares Out.",    f"{(data.get('shares') or 0)/1e6:.1f}M" if data.get("shares") else "N/A")

        if missing_concepts:
            st.warning(f"XBRL concepts not found in this company's filings: {', '.join(missing_concepts[:10])}")

    # ── Full Financial Statement Panel (EDGAR) ────────────────────────────────
    with st.expander("📋 Full Financial Statements — EDGAR", expanded=False):
        st.caption(
            "Raw financial statement values sourced directly from SEC EDGAR Company Facts API — "
            "no Polygon normalization layer. Values in $B or $M."
        )

        def fmt_b(val):
            if val is None: return "—"
            if abs(val) >= 1e9:  return f"${val/1e9:.2f}B"
            if abs(val) >= 1e6:  return f"${val/1e6:.1f}M"
            return f"${val:,.0f}"

        def fmt_pct(val):
            if val is None: return "—"
            return f"{val:.1%}"

        def fmt_x(val):
            if val is None: return "—"
            return f"{val:.1f}x"

        el = data.get("_latest", {})

        st.markdown("#### 📊 Income Statement")
        inc_rows = [
            ("Revenue",          fmt_b(el.get("revenue"))),
            ("Gross Profit",     fmt_b(el.get("gross_profit"))),
            ("Gross Margin",     fmt_pct(el.get("gross_margin"))),
            ("Operating Income", fmt_b(el.get("op_income"))),
            ("Interest Expense", fmt_b(el.get("interest_expense"))),
            ("Interest Paid",    fmt_b(el.get("interest_paid"))),
            ("Net Income",       fmt_b(el.get("net_income"))),
        ]
        hc1, hc2 = st.columns([2, 2])
        hc1.markdown("**Field**")
        hc2.markdown("**EDGAR**")
        for label, edgar_val in inc_rows:
            c1, c2 = st.columns([2, 2])
            c1.caption(label)
            c2.caption(edgar_val)

        st.markdown("#### 💵 Cash Flow Statement")
        cf_rows = [
            ("Operating Cash Flow",    fmt_b(el.get("op_cf"))),
            ("Investing Cash Flow",    fmt_b(el.get("inv_cf"))),
            ("Free Cash Flow",         fmt_b(el.get("fcf"))),
            ("CapEx",                  fmt_b(el.get("capex"))),
            ("D&A",                    fmt_b(el.get("da"))),
            ("Owner Earnings (approx)", fmt_b(el.get("owner_earnings"))),
        ]
        hc1, hc2 = st.columns([2, 2])
        hc1.markdown("**Field**")
        hc2.markdown("**EDGAR**")
        for label, edgar_val in cf_rows:
            c1, c2 = st.columns([2, 2])
            c1.caption(label)
            c2.caption(edgar_val)

        st.markdown("#### 🏦 Balance Sheet")
        bs_rows = [
            ("Total Assets",           fmt_b(el.get("total_assets"))),
            ("Current Liabilities",    fmt_b(el.get("current_liabilities"))),
            ("Long-Term Debt",         fmt_b(el.get("long_term_debt"))),
            ("Short-Term Debt",        fmt_b(el.get("short_term_debt"))),
            ("Total Equity",           fmt_b(el.get("total_equity"))),
            ("Cash",                   fmt_b(el.get("cash"))),
        ]
        hc1, hc2 = st.columns([2, 2])
        hc1.markdown("**Field**")
        hc2.markdown("**EDGAR**")
        for label, edgar_val in bs_rows:
            c1, c2 = st.columns([2, 2])
            c1.caption(label)
            c2.caption(edgar_val)

        st.markdown("#### 🎯 Derived Scoring Metrics")
        dm_rows = [
            ("FCF Yield",                    fmt_pct(data.get("fcf_yield"))),
            ("ROIC (10yr avg, cash basis)",  fmt_pct(el.get("roic_10yr_avg"))),  # (#34) what actually feeds the score
            ("ROIC (latest yr, cash basis)", fmt_pct(el.get("roic"))),
            ("Debt / FCF",                    fmt_x(el.get("debt_to_fcf"))),
            ("Interest Coverage",             fmt_x(el.get("int_coverage"))),
            ("Price / Owner Earn.",           fmt_x(data.get("price_owner_earn"))),
        ]
        hc1, hc2 = st.columns([2, 2])
        hc1.markdown("**Metric**")
        hc2.markdown("**Value**")
        for label, val in dm_rows:
            c1, c2 = st.columns([2, 2])
            c1.caption(label)
            c2.caption(val)

        with st.expander("🔎 XBRL concepts resolved (debug)", expanded=False):
            from edgar_concept_map import CONCEPT_MAP
            edgar_raw = data.get("_latest", {})
            for field, concepts in CONCEPT_MAP.items():
                if field in edgar_raw:
                    st.caption(f"`{field}` → ✅  (candidates: {', '.join(concepts[:2])}{'...' if len(concepts)>2 else ''})")
                else:
                    st.caption(f"`{field}` → ❌ not found  (tried: {', '.join(concepts[:2])}{'...' if len(concepts)>2 else ''})")

    # ── Historical ROIC Chart (#34 — cash-accounting basis, 10-yr avg) ────────
    # Plots sec_utils' canonical history["roic"] series directly rather than
    # recomputing inline. That canonical series (a) uses owner earnings, not
    # net income, as the numerator (#34's cash-accounting-basis ask), (b)
    # includes short-term debt in invested capital (this chart's old inline
    # formula omitted it), and (c) applies _roic_denominator_reliable() to
    # guard against near-zero-invested-capital years distorting the trend
    # (see sec_utils.py for the VeriSign case that guard was built for).
    history   = data.get("_history", {})
    roic_hist = history.get("roic", [])

    if len(roic_hist) >= 3:
        with st.expander("📈 Historical ROIC Trend (from EDGAR)", expanded=False):
            st.caption("10 years of ROIC, cash-accounting basis (owner earnings / invested capital) — 12%+ sustained for 10+ years signals a durable competitive advantage (#34).")

            roic_10yr_avg = el.get("roic_10yr_avg")
            roic_yrs_used = el.get("roic_years_used")
            if roic_10yr_avg is not None:
                st.metric("10-yr Average ROIC", f"{roic_10yr_avg:.1%}", help=f"{roic_yrs_used} reliable year(s) used")
            elif roic_yrs_used:
                st.caption(f"⚠️ Only {roic_yrs_used} reliable year(s) of history — need 5+ for a scored average.")

            roic_series = [{"year": h["period"], "roic": h["value"]} for h in roic_hist]
            roic_yr_start = roic_series[0]["year"] if roic_series else None
            roic_yr_end   = roic_series[-1]["year"] if roic_series else None

            if roic_series:
                fig2 = go.Figure()
                fig2.add_trace(go.Scatter(
                    x=[r["year"] for r in roic_series],
                    y=[r["roic"] * 100 for r in roic_series],
                    mode="lines+markers",
                    name="ROIC %",
                    line=dict(color="#2ecc71", width=2),
                    marker=dict(size=6),
                ))
                fig2.add_hline(y=12, line_dash="dash", line_color="#f39c12",
                               annotation_text="12% threshold", annotation_position="right")
                fig2.add_hline(y=20, line_dash="dash", line_color="#2ecc71",
                               annotation_text="20% exceptional", annotation_position="right")
                fig2.update_layout(
                    title=f"{ticker_input} — Historical ROIC ({roic_yr_start}–{roic_yr_end})",
                    yaxis_title="ROIC %",
                    height=300,
                    margin=dict(t=40, b=20, l=20, r=80),
                )
                st.plotly_chart(fig2, use_container_width=True)

    # ── FCF History Chart ─────────────────────────────────────────────────────
    # Plots sec_utils' canonical history["fcf"] series (capex-based, i.e.
    # op_cf - capex) rather than recomputing op_cf + inv_cf inline. That
    # inline formula is what let this exact chart show FCF going negative
    # for cash-rich filers in years they were clearly hugely profitable
    # (confirmed on NVDA FY2021: -$13.9B) -- inv_cf is dominated by
    # marketable-securities purchases/maturities for a company sitting on
    # a large investment portfolio, not capex. See sec_utils.py's FCF
    # comment for the full writeup.
    fcf_hist_chart = data.get("_history", {}).get("fcf", [])
    if len(fcf_hist_chart) >= 3:
        with st.expander("💰 Historical Free Cash Flow (from EDGAR)", expanded=False):
            fcf_series = [{"year": h["period"], "fcf": h["value"]} for h in fcf_hist_chart]
            if fcf_series:
                fig3 = go.Figure()
                fig3.add_trace(go.Bar(
                    x=[r["year"] for r in fcf_series],
                    y=[r["fcf"] / 1e9 for r in fcf_series],
                    name="FCF ($B)",
                    marker_color=["#2ecc71" if r["fcf"] > 0 else "#e74c3c" for r in fcf_series],
                ))
                fig3.update_layout(
                    title=f"{ticker_input} — Historical Free Cash Flow",
                    yaxis_title="FCF ($B)",
                    height=280,
                    margin=dict(t=40, b=20, l=20, r=20),
                )
                st.plotly_chart(fig3, use_container_width=True)

    st.divider()

    # ── Superinvestor Conviction ──────────────────────────────────────
    st.markdown("### 🦁 Superinvestor Conviction")
    st.caption(
        "How many of 82 tracked superinvestors hold this stock — "
        "via Dataroma.com (aggregates SEC 13F filings). "
        "Shows each holder's portfolio weight and recent activity."
    )

    si_refresh = st.button("🔄 Refresh", key=f"si_edgar_refresh_{ticker_input}",
                           help="Clear cache and re-fetch from Dataroma")
    if si_refresh:
        clear_superinvestor_cache()
        st.rerun()

    si           = get_superinvestor_conviction(ticker_input)
    n_holders    = si.get("holder_count", 0)
    si_score     = si.get("conviction_score", 0)
    si_holders   = si.get("holders", [])
    total_mgrs   = si.get("total_managers", 82)

    si_c1, si_c2, si_c3 = st.columns(3)
    with si_c1:
        color = "#2ecc71" if n_holders >= 5 else "#f39c12" if n_holders >= 2 else "#888"
        st.markdown(
            f"<div style='font-size:2em; font-weight:bold; color:{color}'>{n_holders}</div>",
            unsafe_allow_html=True
        )
        st.caption(f"Superinvestors holding (of {total_mgrs} tracked)")
    with si_c2:
        st.markdown(
            f"<div style='font-size:2em; font-weight:bold'>{si_score}/100</div>",
            unsafe_allow_html=True
        )
        st.caption("Conviction score")
    with si_c3:
        st.caption("Source: Dataroma.com")
        st.caption("Complete portfolio data from all 82 managers")

    if si_holders:
        st.markdown(f"**Holders** (avg position: {si.get('avg_pct', 0):.1f}% of portfolio):")
        holder_cols = st.columns(min(len(si_holders), 3))
        for i, h in enumerate(si_holders):
            with holder_cols[i % 3]:
                activity  = h.get('activity', '').strip()
                pct_str = f"{h['pct']:.1f}% of portfolio" if h['pct'] > 0.05 else "< 0.1% of portfolio"
                st.markdown(f"**{h['investor']}**")
                st.caption(pct_str)
                display_activity = activity if activity else "Held"
                act_color = ("#2ecc71" if any(w in display_activity for w in ["Add", "New", "Buy"])
                             else "#e74c3c" if any(w in display_activity for w in ["Reduce", "Sold", "Sell"])
                             else "#888")
                st.markdown(
                    f"<span style='color:{act_color}; font-size:0.8em'>{display_activity}</span>",
                    unsafe_allow_html=True
                )
    elif n_holders == 0 and not si.get("error"):
        st.info(f"No superinvestors currently hold {ticker_input}.")

    if si.get("error"):
        st.warning(f"⚠️ {si['error'][:300]}")

    st.caption(
        f"Source: Dataroma.com · {si.get('total_managers', 82)} managers · "
        f"{si.get('total_holdings', 0):,} total holdings tracked"
    )

    st.divider()

    # ── Key Metrics ───────────────────────────────────────────────────────────
    st.markdown("### 📊 Key Metrics at a Glance")
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("Free Cash Flow",   fmt_val(data.get("fcf")))
        st.metric("Owner Earnings",   fmt_val(data.get("owner_earnings")))
    with m2:
        st.metric("FCF Yield",        fmt_val(data.get("fcf_yield"), "pct"))
        st.metric("FCF Growth (1yr)", fmt_val(data.get("fcf_growth"), "pct"))
    with m3:
        st.metric("ROIC (10yr avg)",  fmt_val(data.get("roic_10yr_avg"), "pct"),
                   help="Cash-accounting basis (owner earnings / invested capital), 10-yr average (#34). This is what feeds the score, not a single year's figure.")
        st.metric("Gross Margin",     fmt_val(data.get("gross_margin"), "pct"))
    with m4:
        st.metric("Total Debt/FCF",       fmt_val(data.get("debt_to_fcf"), "ratio"))
        st.metric("Price/Owner Earnings", fmt_val(data.get("price_owner_earn"), "ratio"))

    st.divider()

    # ── Key Metrics Summary (EDGAR) ────────────────────────────────────────────
    st.markdown("### 📊 Key Metrics — EDGAR")
    st.caption("Core scoring metrics sourced directly from SEC EDGAR Company Facts API.")

    metric_rows = [
        ("Conviction Score",   f"{rebalanced_score}/100  ({verdict_label})", None),
        ("Free Cash Flow",      fmt_val(data.get("fcf")),          None),
        ("FCF Yield",           fmt_val(data.get("fcf_yield"), "pct"),  None),
        ("ROIC (10yr avg, cash basis)", fmt_val(data.get("roic_10yr_avg"), "pct"), "#34: owner earnings / invested capital, averaged over the trailing 10 years (min. 5 reliable years required)"),
        ("Gross Margin",        fmt_val(data.get("gross_margin"), "pct"), None),
        ("Debt / FCF",          fmt_val(data.get("debt_to_fcf"), "ratio"), None),
        ("Interest Coverage",   fmt_val(data.get("interest_coverage"), "ratio"), None),
        ("Owner Earnings",      fmt_val(data.get("owner_earnings")),    None),
        ("Price / Owner Earn.", fmt_val(data.get("price_owner_earn"), "ratio"), "Reference only — not scored"),
        ("Net Income",          fmt_val(data.get("net_income")),        None),
        ("Revenue",             fmt_val(data.get("revenues")),          None),
        ("Long-Term Debt",      fmt_val(data.get("long_term_debt")),    None),
        ("Market Cap",          fmt_val(data.get("market_cap")),        None),
        ("Price",               f"${data.get('price'):,.2f}" if data.get("price") else "N/A", None),
    ]

    hc1, hc2, hc3 = st.columns([2, 2, 2])
    hc1.markdown("**Metric**")
    hc2.markdown("**Value**")
    hc3.markdown("**Note**")
    st.markdown("---")

    for label, value, note in metric_rows:
        c1, c2, c3 = st.columns([2, 2, 2])
        c1.markdown(f"{label}")
        c2.markdown(f"`{value}`")
        if note:
            c3.caption(note)

    st.divider()

    # ── Income Potential ──────────────────────────────────────────────────────
    st.markdown("### 💰 Income Potential at Your Position Size")
    div_yield = data.get("dividend_yield")
    if div_yield and position_size > 0:
        annual_income  = position_size * div_yield
        monthly_income = annual_income / 12
        from claude_utils import get_user_profile as _gup2
        _prof2 = _gup2()
        target = _prof2.get("monthly_withdrawal", THRESHOLDS["monthly_income_target"])
        pct_of_target = monthly_income / target
        ic1, ic2, ic3 = st.columns(3)
        with ic1: st.metric("Dividend Yield",      f"{div_yield:.2%}")
        with ic2: st.metric("Est. Annual Income",  f"${annual_income:,.0f}")
        with ic3: st.metric("Est. Monthly Income", f"${monthly_income:,.0f}",
                            delta=f"{pct_of_target:.0%} of your ${target:,.0f}/mo target")
        st.progress(min(pct_of_target, 1.0))
    else:
        st.info("No dividend yield data available. This may be a pure growth compounder.")

    st.divider()

    # ── Verdict ───────────────────────────────────────────────────────────────
    st.markdown("### 📝 The Verdict")
    strengths  = [c["name"] for c in criteria if not c.get("missing") and c["points_max"] > 0
                  and c["points_earned"] / c["points_max"] >= 0.8]
    weaknesses = [c["name"] for c in criteria if not c.get("missing") and c["points_max"] > 0
                  and c["points_earned"] / c["points_max"] < 0.5 and c["value"] != "N/A"]
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
    _wd   = _prof.get("monthly_withdrawal", 8000)
    _age  = _prof.get("age", 57)
    _inf  = _prof.get("inflation", 4.0)
    st.info(
        f"⚠️ **Portfolio Reminder:** Prioritize companies with low debt, strong FCF, and "
        f"pricing power. At age {_age} with a ${_wd:,.0f}/month withdrawal target, "
        f"avoid permanent capital loss — recession-resilience matters more than maximum returns. "
        f"Inflation assumption: {_inf:.1f}%."
    )

    # ── Ask Claude Panel ──────────────────────────────────────────────────────
    st.divider()
    st.markdown("### 🤖 Ask Claude — SEC Filing Analysis")
    st.caption(
        "Claude reads the actual 10-K filing alongside the quantitative scores above — applying Buffett + Munger philosophy. "
        "Ask anything: red flags, management tone, moat durability, macro resilience."
    )

    filing_key = f"sec_filing_edgar_{ticker_input}"
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
        st.success(f"✅ 10-K loaded — sections: {', '.join(found_sections) if found_sections else 'none'}.")
        if filing_url:
            st.markdown(f"[📋 View full 10-K on EDGAR]({filing_url})")

    convo_key   = f"claude_edgar_convo_{ticker_input}"
    context_key = f"claude_edgar_context_sent_{ticker_input}"
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
            "How resilient is this business's balance sheet and cash flow to an extended economic downturn?",
            "What does the filing say about competitive moat and pricing power?",
        ]
        for i, q in enumerate(starters):
            with sq_cols[i % 2]:
                if st.button(q, key=f"edgar_starter_{i}_{ticker_input}", use_container_width=True):
                    st.session_state[f"pending_edgar_claude_q_{ticker_input}"] = q
                    st.rerun()

    # ── Deferred chat_input mount (cold-load scroll fix, same as
    # Dashboard's) ─────────────────────────────────────────────────────
    # st.chat_input's mere presence makes Streamlit wrap the page in its
    # own auto-scroll-to-bottom chat container -- see ui_utils.py's
    # scroll-fix docstring and 0_Dashboard.py's matching gate for the
    # full story. Deferring the widget itself behind a click (starter
    # question or an explicit enable button) means nothing creates that
    # container on a fresh/auto-analyzed load of this page.
    chat_enabled_key = f"edgar_chat_enabled_{ticker_input}"
    if chat_enabled_key not in st.session_state:
        st.session_state[chat_enabled_key] = bool(st.session_state[convo_key])

    pending_q = st.session_state.pop(f"pending_edgar_claude_q_{ticker_input}", None)
    if pending_q:
        st.session_state[chat_enabled_key] = True

    if not st.session_state[chat_enabled_key]:
        if st.button(f"💬 Ask Claude about {ticker_input}'s 10-K filing", key=f"edgar_enable_chat_{ticker_input}"):
            st.session_state[chat_enabled_key] = True
            st.rerun()
        user_q = None
    else:
        user_q = st.chat_input(f"Ask Claude about {ticker_input}'s 10-K filing...",
                                key=f"edgar_claude_input_{ticker_input}")
    active_q  = pending_q or user_q

    if active_q:
        scores_dict = {"rebalanced": rebalanced_score, "raw": raw_score, "verdict": verdict_label}
        with st.chat_message("user"):
            st.markdown(active_q)
        with st.chat_message("assistant", avatar="🤖"):
            with st.spinner("Reading the 10-K and thinking..."):
                if not st.session_state[context_key]:
                    from claude_utils import build_context, get_user_profile
                    profile     = get_user_profile()
                    context_str = build_context(ticker_input, data, scores_dict, sections, profile)
                    full_q      = f"{context_str}\n\n---\nQUESTION: {active_q}"
                    response = ask_claude_about_equity(
                        ticker=ticker_input, data=data, scores=scores_dict,
                        sections=sections, user_question=full_q,
                        conversation_history=None, profile=profile,
                    )
                    st.session_state[convo_key].append({"role": "user", "content": full_q})
                    st.session_state[context_key] = True
                else:
                    response = ask_claude_about_equity(
                        ticker=ticker_input, data=data, scores=scores_dict,
                        sections=sections, user_question=active_q,
                        conversation_history=st.session_state[convo_key],
                    )
                    st.session_state[convo_key].append({"role": "user", "content": active_q})
                st.session_state[convo_key].append({"role": "assistant", "content": response})
                st.markdown(response)

    if st.session_state[convo_key]:
        if st.button("🗑️ Clear conversation", key=f"edgar_clear_convo_{ticker_input}"):
            st.session_state[convo_key]   = []
            st.session_state[context_key] = False
            st.rerun()

elif analyze and not ticker_input:
    st.warning("Please enter a ticker symbol to analyze.")
else:
    st.markdown("""
    ### How this works
    Same scoring framework as Equity Scout — but data flows directly from **SEC EDGAR Company Facts API**,
    the primary source that Polygon itself pulls from. No normalization layer between you and the filing.

    **What's new vs. the Polygon version:**
    - 🏛️ Primary source: SEC EDGAR (undisputed truth from the filing itself)
    - 📈 Historical ROIC chart — 10+ years directly from SEC filings
    - 💰 Historical FCF chart — full history available
    - 🔍 Raw data transparency panel — see exactly what numbers feed each metric
    - ⚠️ Financial firm + cyclical firm detection flags
    - 💲 Live pricing only via yfinance (EDGAR has no price data)

    | Metric | Default Weight | What it measures |
    |--------|---------------|-----------------|
    | Free Cash Flow Yield | 20 pts | Real owner earnings relative to price |
    | ROIC | 10 pts | How wisely management deploys your capital |
    | Debt / FCF | 20 pts | Balance sheet strength |
    | Gross Margin | 15 pts | Pricing power and moat durability |
    | Interest Coverage | 10 pts | Ability to service debt |
    | Price / Owner Earnings | — | Shown as reference only (not scored) |

    **Score guide:** 80-100 = Strong Buy · 65-79 = Watch · 45-64 = Caution · <45 = Avoid

    
    """)

