import streamlit as st
import requests
import plotly.graph_objects as go
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from sec_utils import fetch_10k_sections, fetch_company_facts
from claude_utils import ask_claude_about_equity
from superinvestor_utils import get_superinvestor_conviction, clear_superinvestor_cache

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

APP_URL = "https://voskuil-fp-1-0-k85bd7afbw8dnqeftzxwbu.streamlit.app"

DEFAULT_WEIGHTS = {
    "FCF Yield":              30,
    "ROIC":                   20,
    "Debt / FCF":             25,
    "Gross Margin":           15,
    "Interest Coverage":      10,
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

def safe_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None

def fmt_val(val, fmt="money"):
    if val is None: return "N/A"
    if fmt == "money":  return f"${val/1e9:.2f}B" if abs(val) >= 1e9 else f"${val/1e6:.1f}M"
    if fmt == "pct":    return f"{val:.1%}"
    if fmt == "ratio":  return f"{val:.1f}x"
    return str(val)

# ── Price fetch via yfinance (EDGAR has no live pricing) ─────────────────────
@st.cache_data(ttl=900)
def fetch_price_and_market_cap(ticker):
    """
    Fetch current price and market cap from yfinance.
    EDGAR provides shares outstanding; we use yfinance for live price only.
    Returns dict with price, market_cap, shares, dividend_yield.
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        price      = info.get("currentPrice") or info.get("regularMarketPrice")
        market_cap = info.get("marketCap")
        shares     = info.get("sharesOutstanding")
        div_yield  = info.get("dividendYield")
        name       = info.get("longName") or info.get("shortName") or ticker
        sector     = info.get("sector", "N/A")
        description = (info.get("longBusinessSummary", "")[:400] + "...") if info.get("longBusinessSummary") else ""
        return {
            "price":         safe_float(price),
            "market_cap":    safe_float(market_cap),
            "shares":        safe_float(shares),
            "dividend_yield": safe_float(div_yield),
            "name":          name,
            "sector":        sector,
            "description":   description,
        }
    except Exception as e:
        return {"price": None, "market_cap": None, "shares": None,
                "dividend_yield": None, "name": ticker, "sector": "N/A",
                "description": "", "error": str(e)}

@st.cache_data(ttl=3600)
def fetch_fundamentals_edgar(ticker):
    """
    Primary data fetch using SEC EDGAR Company Facts API.
    Falls back gracefully when concepts are missing.
    Returns a data dict compatible with the existing score_stock() function.
    """
    # 1. Fetch EDGAR company facts (fundamentals + history)
    facts = fetch_company_facts(ticker)
    if facts.get("error"):
        return {"error": facts["error"]}

    latest = facts["latest"]
    meta   = facts["meta"]
    missing = facts.get("missing", [])

    # 2. Fetch live price + market cap from yfinance
    price_data = fetch_price_and_market_cap(ticker)
    price      = price_data.get("price")
    market_cap = price_data.get("market_cap")
    shares     = price_data.get("shares") or latest.get("diluted_shares")
    div_yield  = price_data.get("dividend_yield")

    # Use yfinance name/sector/description as primary (richer than EDGAR entity name)
    name        = price_data.get("name") or meta.get("company_name", ticker)
    sector      = price_data.get("sector") or meta.get("sic", "N/A")
    description = price_data.get("description", "")

    # 3. Pull pre-computed scoring fields from EDGAR latest
    op_cf        = latest.get("op_cf")
    inv_cf       = latest.get("inv_cf")
    fcf          = latest.get("fcf")
    net_income   = latest.get("net_income")
    revenues     = latest.get("revenue")
    gross_profit = latest.get("gross_profit")
    gross_margin = latest.get("gross_margin")
    roic         = latest.get("roic")
    long_term_debt = latest.get("long_term_debt", 0) or 0
    short_term_debt = latest.get("short_term_debt", 0) or 0
    total_debt   = long_term_debt + short_term_debt
    debt_to_fcf  = latest.get("debt_to_fcf")
    int_coverage = latest.get("int_coverage")
    owner_earn   = latest.get("owner_earnings")
    dna          = latest.get("dna")

    # 4. Valuation metrics (need price)
    fcf_yield   = (fcf / market_cap) if (fcf and market_cap and market_cap > 0) else None
    poe         = None
    if owner_earn and owner_earn > 0 and shares and price:
        poe = price / (owner_earn / shares)

    # 5. FCF growth (compare latest vs prior year from history)
    fcf_growth = None
    history = facts.get("history", {})
    op_cf_hist = history.get("op_cf", [])
    inv_cf_hist = history.get("inv_cf", [])
    if len(op_cf_hist) >= 2 and len(inv_cf_hist) >= 2:
        try:
            fcf_prior = op_cf_hist[-2]["value"] + inv_cf_hist[-2]["value"]
            if fcf_prior and fcf_prior != 0 and fcf:
                fcf_growth = (fcf / fcf_prior) - 1
        except Exception:
            pass

    # 6. Interest coverage — prefer cash-basis interest paid
    is_net_creditor = False
    int_exp = latest.get("interest_paid") or latest.get("interest_expense")
    op_inc  = latest.get("op_income")
    if int_exp and int_exp > 0 and op_inc is not None:
        int_coverage = op_inc / int_exp
    elif int_exp is None or int_exp == 0:
        # No interest expense — likely net creditor
        cash     = latest.get("cash", 0) or 0
        if cash > total_debt:
            is_net_creditor = True

    return {
        # Identity
        "name":             name,
        "sector":           sector,
        "description":      description,
        "ticker":           ticker.upper(),
        "cik":              meta.get("cik"),
        "is_financial":     meta.get("is_financial", False),
        "is_cyclical":      meta.get("is_cyclical", False),
        "fiscal_year":      meta.get("last_annual_period"),
        "sic":              meta.get("sic"),
        "data_source":      "SEC EDGAR Company Facts",
        "missing_concepts": missing,

        # Pricing (yfinance)
        "price":            price,
        "market_cap":       market_cap,
        "shares":           shares,

        # Cash flow
        "op_cf":            op_cf,
        "inv_cf":           inv_cf,
        "fcf":              fcf,
        "fcf_yield":        fcf_yield,
        "fcf_growth":       fcf_growth,

        # Income
        "revenues":         revenues,
        "gross_profit":     gross_profit,
        "gross_margin":     gross_margin,
        "net_income":       net_income,

        # Quality metrics
        "roic":             roic,
        "long_term_debt":   long_term_debt,
        "short_term_debt":  short_term_debt,
        "total_debt":       total_debt,
        "debt_to_fcf":      debt_to_fcf,
        "interest_coverage": int_coverage,
        "is_net_creditor":  is_net_creditor,

        # Owner earnings
        "owner_earnings":   owner_earn,
        "price_owner_earn": poe,
        "dna":              dna,

        # Income
        "dividend_yield":   div_yield,

        # Raw EDGAR history (for ROIC trending etc.)
        "_history":         history,
        "_latest":          latest,
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
    roic    = data.get("roic")
    if roic is not None:
        if roic >= THRESHOLDS["roic_great"]:   pts, verdict = max_pts, "Exceptional"
        elif roic >= THRESHOLDS["roic_good"]:  pts, verdict = round(max_pts * 0.60), "Strong"
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
    debt_fcf = data.get("debt_to_fcf")
    ic       = data.get("interest_coverage") or 0
    is_nc    = data.get("is_net_creditor", False)
    if debt_fcf is not None:
        if debt_fcf < THRESHOLDS["debt_fcf_safe"]:      pts, verdict = max_pts, "Fortress"
        elif debt_fcf < THRESHOLDS["debt_fcf_warning"]: pts, verdict = round(max_pts * 0.50), "Manageable"
        elif ic >= THRESHOLDS["interest_coverage_safe"] or is_nc:
                                                         pts, verdict = round(max_pts * 0.50), "High Debt, Well Covered"
        else:                                            pts, verdict = 0, "Overleveraged"
    else:
        pts, verdict = 0, "No Data"
    criteria.append({"name": "Debt / Free Cash Flow",
                     "value": f"{debt_fcf:.1f}x" if debt_fcf is not None else "N/A",
                     "points_earned": pts, "points_max": max_pts, "verdict": verdict,
                     "note": "Munger's inversion: 'What kills a great business?' Excessive debt when capital becomes scarce. A fortress balance sheet means never being a forced seller. Under 3x Debt/FCF = structural survivor.",
                     "missing": debt_fcf is None})

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


def score_to_verdict(score):
    if score >= 80:   return "Strong Buy", "#2ecc71"
    elif score >= 65: return "Watch Closely", "#f39c12"
    elif score >= 45: return "Proceed with Caution", "#e67e22"
    else:             return "Avoid", "#e74c3c"


# ── Query params ─────────────────────────────────────────────────────────────
params       = st.query_params
url_ticker   = params.get("ticker", "").upper().strip()
if not url_ticker and "dive_ticker" in st.session_state:
    url_ticker = st.session_state.pop("dive_ticker", "").upper().strip()
auto_analyze = bool(url_ticker)

st.title("🔍 Equity Scout — EDGAR")
st.caption("Concentrated, Buffett-style fundamental analysis. Primary data: SEC EDGAR Company Facts API.")

st.info(
    "🧪 **Validation page** — One input runs both EDGAR and Polygon simultaneously. "
    "Results appear side-by-side in the comparison panel below the score. "
    "Once validated against your core holdings, EDGAR replaces Polygon. See punch list #57.",
    icon="🔬"
)

st.markdown("> *\"Price is what you pay. Value is what you get.\"* — Warren Buffett")

if url_ticker:
    col_back, _ = st.columns([1, 4])
    with col_back:
        if st.button("← Back to Dashboard"):
            st.switch_page("pages/0_Dashboard.py")
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
        with _sc: w_fcf = st.slider("FCF Yield", 0, 60, sw["FCF Yield"], step=5, key="w_fcf_e")
        with _sb:
            st.write("")
            if st.button(f"↺ {DEFAULT_WEIGHTS['FCF Yield']}", key="reset_w_fcf_e", use_container_width=True):
                st.session_state["pending_reset_w_fcf_e"] = True; st.rerun()
        _sc, _sb = st.columns([4, 1])
        with _sc: w_roic = st.slider("ROIC", 0, 40, sw["ROIC"], step=5, key="w_roic_e")
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
        with _sc: w_gm = st.slider("Gross Margin", 0, 40, sw["Gross Margin"], step=5, key="w_gm_e")
        with _sb:
            st.write("")
            if st.button(f"↺ {DEFAULT_WEIGHTS['Gross Margin']}", key="reset_w_gm_e", use_container_width=True):
                st.session_state["pending_reset_w_gm_e"] = True; st.rerun()
        _sc, _sb = st.columns([4, 1])
        with _sc: w_ic = st.slider("Interest Coverage", 0, 40, sw["Interest Coverage"], step=5, key="w_ic_e")
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
if analyze and ticker_input:
    total_weight = sum(st.session_state.get("committed_weights", DEFAULT_WEIGHTS).values())
    if total_weight != 100:
        st.warning(f"Weights add up to {total_weight}, not 100. Adjust sliders for accurate scores.")

    col_p, col_e = st.columns(2)

    # Fetch both sources in parallel columns so spinners show simultaneously
    with col_e:
        with st.spinner(f"🏛️ Fetching **{ticker_input}** from SEC EDGAR..."):
            data = fetch_fundamentals_edgar(ticker_input)

    with col_p:
        with st.spinner(f"📡 Fetching **{ticker_input}** from Polygon..."):
            poly_data = fetch_fundamentals_polygon(ticker_input)

    # EDGAR errors are blocking — can't show the page without it
    if data.get("error"):
        st.error(f"EDGAR fetch failed for {ticker_input}: {data['error']}")
        st.stop()

    # Polygon errors are non-blocking — show warning, comparison panel will degrade gracefully
    poly_cache_key = f"es_results_{ticker_input}"
    if poly_data.get("error"):
        st.warning(f"⚠️ Polygon fetch failed: {poly_data['error']} — comparison will be unavailable.")
    else:
        # Store in same cache key format as 1_Equity_Scout.py so comparison panel works
        poly_raw, poly_rebalanced, poly_missing, poly_criteria = score_stock(poly_data, weights)
        poly_verdict_label, poly_verdict_color = score_to_verdict(poly_rebalanced)
        st.session_state[poly_cache_key] = {
            "data": poly_data, "raw_score": poly_raw,
            "rebalanced_score": poly_rebalanced, "missing_names": poly_missing,
            "criteria": poly_criteria, "verdict_label": poly_verdict_label,
            "verdict_color": poly_verdict_color,
        }

    # Financial/cyclical firm warnings
    if data.get("is_financial"):
        st.warning(
            f"⚠️ **Financial firm detected** (SIC {data.get('sic')}) — "
            "Standard FCF/debt/margin scoring is unreliable for banks, insurers, and asset managers. "
            "Score shown for reference only. See punch list #36."
        )
    if data.get("is_cyclical"):
        st.warning(
            f"⚠️ **Cyclical firm detected** (SIC {data.get('sic')}) — "
            "Single-period scoring reflects where this company is in the cycle, not intrinsic value. "
            "Full-cycle analysis recommended. See punch list #37."
        )

    raw_score, rebalanced_score, missing_names, criteria = score_stock(data, weights)
    verdict_label, verdict_color = score_to_verdict(rebalanced_score)

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

    st.markdown(f"## {data.get('name', ticker_input)}")
    price_str  = f"${data.get('price', 0) or 0:,.2f} per share" if data.get("price") else "Price unavailable"
    mktcap_str = f"Market Cap: ${(data.get('market_cap') or 0)/1e9:.1f}B" if data.get("market_cap") else ""
    fy_str     = f"FY{data.get('fiscal_year', '')}" if data.get("fiscal_year") else ""
    st.caption(f"{data.get('sector', '')}  ·  {price_str}  ·  {mktcap_str}  ·  {fy_str}")
    if data.get("description"):
        st.markdown(f"*{data['description']}*")

    # Source banner
    cik = data.get("cik", "")
    sec_link   = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker_input}&type=10-K&dateb=&owner=include&count=10"
    yahoo_link = f"https://finance.yahoo.com/quote/{ticker_input}"
    edgar_link = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json" if cik else None

    rl1, rl2, rl3, rl4 = st.columns([1, 1, 1, 5])
    with rl1: st.link_button("📋 SEC Filings", sec_link)
    with rl2: st.link_button("📈 Yahoo Finance", yahoo_link)
    if edgar_link:
        with rl3: st.link_button("🏛️ EDGAR Facts", edgar_link)

    # Data source badge
    missing_concepts = data.get("missing_concepts", [])
    if missing_concepts:
        st.caption(f"📡 Data: SEC EDGAR Company Facts  ·  Pricing: yfinance  ·  Missing XBRL concepts: {len(missing_concepts)}")
    else:
        st.caption("📡 Data: SEC EDGAR Company Facts (primary)  ·  Pricing: yfinance")

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

    # ── Full Statement Comparison — EDGAR vs Polygon ──────────────────────────
    poly_cache_key_stmt = f"es_results_{ticker_input}"
    poly_stmt = (st.session_state.get(poly_cache_key_stmt) or {}).get("data", {})

    with st.expander("📋 Full Statement Comparison — EDGAR vs Polygon (line by line)", expanded=False):
        st.caption(
            "Raw financial statement line items from both sources side by side. "
            "🟡 = values differ by more than 5%.  🔴 = one source is missing the value entirely. "
            "Use this to identify exactly where and why the two sources diverge."
        )

        def pct_diff(a, b):
            """Return % difference between two values, or None if can't compute."""
            try:
                if a and b and b != 0:
                    return abs(a - b) / abs(b)
            except Exception:
                pass
            return None

        def diff_color(a, b):
            if a is None and b is None: return "⬜"
            if a is None or b is None:  return "🔴"
            d = pct_diff(a, b)
            if d is None:               return "⬜"
            if d > 0.05:                return "🟡"
            return "✅"

        def fmt_b(val):
            """Format as $B or $M."""
            if val is None: return "—"
            if abs(val) >= 1e9:  return f"${val/1e9:.2f}B"
            if abs(val) >= 1e6:  return f"${val/1e6:.1f}M"
            return f"${val:,.0f}"

        # Pull EDGAR latest raw values
        el = data.get("_latest", {})

        # ── Income Statement ─────────────────────────────────────────────────
        st.markdown("#### 📊 Income Statement")
        inc_rows = [
            ("Revenue",              el.get("revenue"),         poly_stmt.get("revenues")),
            ("Gross Profit",         el.get("gross_profit"),    poly_stmt.get("gross_profit")),
            ("Operating Income",     el.get("op_income"),       None),   # Polygon doesn't expose directly
            ("Interest Expense",     el.get("interest_expense"),None),
            ("Interest Paid (cash)", el.get("interest_paid"),   None),
            ("Net Income",           el.get("net_income"),      poly_stmt.get("net_income")),
            ("EPS (diluted)",        el.get("eps_diluted"),     None),
            ("Income Tax",           el.get("income_tax"),      None),
        ]

        hc1, hc2, hc3, hc4 = st.columns([3, 2, 2, 1])
        hc1.markdown("**Line Item**")
        hc2.markdown("**🏛️ EDGAR**")
        hc3.markdown("**📡 Polygon**")
        hc4.markdown("**Match**")

        for label, edgar_val, poly_val in inc_rows:
            flag = diff_color(edgar_val, poly_val)
            c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
            c1.markdown(label)
            c2.markdown(f"`{fmt_b(edgar_val)}`")
            c3.markdown(f"`{fmt_b(poly_val)}`")
            c4.markdown(flag)

        # ── Cash Flow Statement ──────────────────────────────────────────────
        st.markdown("#### 💵 Cash Flow Statement")

        # Polygon FCF is already op+inv combined — show what we have
        poly_op_cf  = poly_stmt.get("op_cf")
        poly_inv_cf = poly_stmt.get("inv_cf")
        poly_fcf    = poly_stmt.get("fcf")

        cf_rows = [
            ("Operating Cash Flow",  el.get("op_cf"),    poly_op_cf),
            ("Investing Cash Flow",  el.get("inv_cf"),   poly_inv_cf),
            ("Free Cash Flow",       el.get("fcf"),      poly_fcf),
            ("CapEx",                el.get("capex"),    None),
            ("D&A",                  el.get("dna"),      None),
        ]

        hc1, hc2, hc3, hc4 = st.columns([3, 2, 2, 1])
        hc1.markdown("**Line Item**")
        hc2.markdown("**🏛️ EDGAR**")
        hc3.markdown("**📡 Polygon**")
        hc4.markdown("**Match**")

        for label, edgar_val, poly_val in cf_rows:
            flag = diff_color(edgar_val, poly_val)
            c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
            c1.markdown(label)
            c2.markdown(f"`{fmt_b(edgar_val)}`")
            c3.markdown(f"`{fmt_b(poly_val)}`")
            c4.markdown(flag)

        # ── Balance Sheet ────────────────────────────────────────────────────
        st.markdown("#### 🏦 Balance Sheet")

        poly_ltd = poly_stmt.get("long_term_debt")

        bs_rows = [
            ("Total Assets",         el.get("total_assets"),    None),
            ("Current Assets",       el.get("current_assets"),  None),
            ("Cash & Equivalents",   el.get("cash"),            None),
            ("Inventory",            el.get("inventory"),       None),
            ("Accounts Receivable",  el.get("accounts_receivable"), None),
            ("PP&E (net)",           el.get("ppe_net"),         None),
            ("Goodwill",             el.get("goodwill"),        None),
            ("Total Liabilities",    el.get("total_liabilities"), None),
            ("Current Liabilities",  el.get("current_liabilities"), None),
            ("Long-Term Debt",       el.get("long_term_debt"),  poly_ltd),
            ("Short-Term Debt",      el.get("short_term_debt"), None),
            ("Total Debt",           el.get("total_debt"),      None),
            ("Total Equity",         el.get("total_equity"),    None),
            ("Retained Earnings",    el.get("retained_earnings"), None),
        ]

        hc1, hc2, hc3, hc4 = st.columns([3, 2, 2, 1])
        hc1.markdown("**Line Item**")
        hc2.markdown("**🏛️ EDGAR**")
        hc3.markdown("**📡 Polygon**")
        hc4.markdown("**Match**")

        for label, edgar_val, poly_val in bs_rows:
            flag = diff_color(edgar_val, poly_val)
            c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
            c1.markdown(label)
            c2.markdown(f"`{fmt_b(edgar_val)}`")
            c3.markdown(f"`{fmt_b(poly_val)}`")
            c4.markdown(flag)

        # ── Derived Scoring Metrics ──────────────────────────────────────────
        st.markdown("#### 🎯 Derived Scoring Metrics")
        st.caption("These are the values that actually feed the scoring engine — computed from the raw fields above.")

        drv_rows = [
            ("FCF Yield",            data.get("fcf_yield"),         poly_stmt.get("fcf_yield"),       "pct"),
            ("ROIC",                 data.get("roic"),              poly_stmt.get("roic"),             "pct"),
            ("Gross Margin",         data.get("gross_margin"),      poly_stmt.get("gross_margin"),     "pct"),
            ("Debt / FCF",           data.get("debt_to_fcf"),       poly_stmt.get("debt_to_fcf"),      "ratio"),
            ("Interest Coverage",    data.get("interest_coverage"), poly_stmt.get("interest_coverage"),"ratio"),
            ("Owner Earnings",       data.get("owner_earnings"),    poly_stmt.get("owner_earnings"),   "money"),
            ("Price / Owner Earn",   data.get("price_owner_earn"),  poly_stmt.get("price_owner_earn"), "ratio"),
        ]

        def fmt_metric(val, fmt):
            if val is None: return "—"
            if fmt == "pct":   return f"{val:.2%}"
            if fmt == "ratio": return f"{val:.2f}x"
            return fmt_b(val)

        hc1, hc2, hc3, hc4 = st.columns([3, 2, 2, 1])
        hc1.markdown("**Metric**")
        hc2.markdown("**🏛️ EDGAR**")
        hc3.markdown("**📡 Polygon**")
        hc4.markdown("**Match**")

        for label, edgar_val, poly_val, fmt in drv_rows:
            flag = diff_color(edgar_val, poly_val)
            c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
            c1.markdown(label)
            c2.markdown(f"`{fmt_metric(edgar_val, fmt)}`")
            c3.markdown(f"`{fmt_metric(poly_val, fmt)}`")
            c4.markdown(flag)

        # ── XBRL Concept used for each field ─────────────────────────────────
        with st.expander("🔎 EDGAR XBRL concepts used (debug)", expanded=False):
            st.caption("Which XBRL concept tag was resolved for each field in this company's filing.")
            from edgar_concept_map import CONCEPT_MAP
            edgar_raw = data.get("_latest", {})
            for field, concepts in CONCEPT_MAP.items():
                if field in edgar_raw:
                    st.caption(f"`{field}` → resolved ✅  (candidates: {', '.join(concepts[:2])}{'...' if len(concepts)>2 else ''})")
                else:
                    st.caption(f"`{field}` → **not found** ❌  (tried: {', '.join(concepts[:2])}{'...' if len(concepts)>2 else ''})")

    # ── Historical ROIC Chart (new — foundation for punch list #34/#40) ───────
    history = data.get("_history", {})
    op_cf_hist  = history.get("op_cf", [])
    inv_cf_hist = history.get("inv_cf", [])
    ni_hist     = history.get("net_income", [])
    eq_hist     = history.get("total_equity", [])
    ltd_hist    = history.get("long_term_debt", [])

    if len(ni_hist) >= 3 and len(eq_hist) >= 3:
        with st.expander("📈 Historical ROIC Trend (from EDGAR)", expanded=False):
            st.caption("10+ years of ROIC derived directly from SEC filings. Consistency = durable competitive advantage.")

            # Build aligned year → ROIC series
            ni_by_year  = {h["period"]: h["value"] for h in ni_hist if h.get("value") is not None}
            eq_by_year  = {h["period"]: h["value"] for h in eq_hist if h.get("value") is not None}
            ltd_by_year = {h["period"]: h["value"] for h in ltd_hist if h.get("value") is not None}

            years = sorted(set(ni_by_year) & set(eq_by_year))
            roic_series = []
            for yr in years:
                ni  = ni_by_year.get(yr, 0)
                eq  = eq_by_year.get(yr, 0)
                ltd = ltd_by_year.get(yr, 0)
                inv_cap = eq + ltd
                if inv_cap and inv_cap > 0:
                    roic_series.append({"year": yr, "roic": ni / inv_cap})

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
                    title=f"{ticker_input} — Historical ROIC ({years[0]}–{years[-1]})",
                    yaxis_title="ROIC %",
                    height=300,
                    margin=dict(t=40, b=20, l=20, r=80),
                )
                st.plotly_chart(fig2, use_container_width=True)

    # ── FCF History Chart ─────────────────────────────────────────────────────
    if len(op_cf_hist) >= 3 and len(inv_cf_hist) >= 3:
        with st.expander("💰 Historical Free Cash Flow (from EDGAR)", expanded=False):
            op_by_yr  = {h["period"]: h["value"] for h in op_cf_hist if h.get("value") is not None}
            inv_by_yr = {h["period"]: h["value"] for h in inv_cf_hist if h.get("value") is not None}
            years_fcf = sorted(set(op_by_yr) & set(inv_by_yr))
            fcf_series = [
                {"year": yr, "fcf": op_by_yr[yr] + inv_by_yr[yr]}
                for yr in years_fcf
            ]
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
        st.metric("ROIC",             fmt_val(data.get("roic"), "pct"))
        st.metric("Gross Margin",     fmt_val(data.get("gross_margin"), "pct"))
    with m4:
        st.metric("Total Debt/FCF",       fmt_val(data.get("debt_to_fcf"), "ratio"))
        st.metric("Price/Owner Earnings", fmt_val(data.get("price_owner_earn"), "ratio"))

    st.divider()

    # ── Polygon vs EDGAR Comparison ───────────────────────────────────────────
    st.markdown("### 🔬 Source Comparison — EDGAR vs Polygon")
    st.caption("Side-by-side view of how SEC EDGAR and Polygon report the same metrics. Differences may reflect normalization choices, concept tag selection, or filing period alignment.")

    poly_cache_key = f"es_results_{ticker_input}"
    poly_cached    = st.session_state.get(poly_cache_key)

    if poly_cached:
        poly_data  = poly_cached.get("data", {})
        poly_score = poly_cached.get("rebalanced_score")
        poly_verdict = poly_cached.get("verdict_label", "")

        # Build comparison rows
        compare_rows = [
            ("Conviction Score",    f"{rebalanced_score}/100  ({verdict_label})",
                                    f"{poly_score}/100  ({poly_verdict})"
                                    if poly_score is not None else "N/A"),
            ("Free Cash Flow",      fmt_val(data.get("fcf")),
                                    fmt_val(poly_data.get("fcf"))),
            ("Operating CF",        fmt_val(data.get("op_cf")),
                                    fmt_val(poly_data.get("fcf"))),  # Polygon doesn't expose op_cf directly
            ("FCF Yield",           fmt_val(data.get("fcf_yield"), "pct"),
                                    fmt_val(poly_data.get("fcf_yield"), "pct")),
            ("ROIC",                fmt_val(data.get("roic"), "pct"),
                                    fmt_val(poly_data.get("roic"), "pct")),
            ("Gross Margin",        fmt_val(data.get("gross_margin"), "pct"),
                                    fmt_val(poly_data.get("gross_margin"), "pct")),
            ("Debt / FCF",          fmt_val(data.get("debt_to_fcf"), "ratio"),
                                    fmt_val(poly_data.get("debt_to_fcf"), "ratio")),
            ("Interest Coverage",   fmt_val(data.get("interest_coverage"), "ratio"),
                                    fmt_val(poly_data.get("interest_coverage"), "ratio")),
            ("Owner Earnings",      fmt_val(data.get("owner_earnings")),
                                    fmt_val(poly_data.get("owner_earnings"))),
            ("Price / Owner Earn",  fmt_val(data.get("price_owner_earn"), "ratio"),
                                    fmt_val(poly_data.get("price_owner_earn"), "ratio")),
            ("Net Income",          fmt_val(data.get("net_income")),
                                    fmt_val(poly_data.get("net_income"))),
            ("Revenue",             fmt_val(data.get("revenues")),
                                    fmt_val(poly_data.get("revenues"))),
            ("Long-Term Debt",      fmt_val(data.get("long_term_debt")),
                                    fmt_val(poly_data.get("long_term_debt"))),
            ("Market Cap",          fmt_val(data.get("market_cap")),
                                    fmt_val(poly_data.get("market_cap"))),
            ("Price",               f"${data.get('price'):,.2f}" if data.get("price") else "N/A",
                                    f"${poly_data.get('price'):,.2f}" if poly_data.get("price") else "N/A"),
        ]

        # Header row
        hc1, hc2, hc3 = st.columns([2, 1.5, 1.5])
        hc1.markdown("**Metric**")
        hc2.markdown("**🏛️ EDGAR** *(primary)*")
        hc3.markdown("**📡 Polygon** *(reference)*")
        st.markdown("---")

        for label, edgar_val, poly_val in compare_rows:
            c1, c2, c3 = st.columns([2, 1.5, 1.5])
            c1.markdown(f"{label}")

            # Highlight differences
            match = edgar_val == poly_val or edgar_val == "N/A" or poly_val == "N/A"
            if match:
                c2.markdown(f"`{edgar_val}`")
                c3.markdown(f"`{poly_val}`")
            else:
                c2.markdown(f"**`{edgar_val}`**")
                c3.markdown(f"<span style='color:#f39c12'>**`{poly_val}`**</span>",
                            unsafe_allow_html=True)

        st.caption(
            "🟡 Orange values differ between sources. "
            "Differences in scoring metrics usually trace to: debt definition (total vs long-term only), "
            "interest expense (cash paid vs accrual), or fiscal year alignment."
        )

    else:
        st.info(
            f"📋 Enter a ticker above and click **Analyze** — both EDGAR and Polygon will run "
            f"simultaneously and the comparison will appear here.",
            icon="💡"
        )

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
            "How resilient is this business in a credit crunch / financial repression environment?",
            "What does the filing say about competitive moat and pricing power?",
        ]
        for i, q in enumerate(starters):
            with sq_cols[i % 2]:
                if st.button(q, key=f"edgar_starter_{i}_{ticker_input}", use_container_width=True):
                    st.session_state[f"pending_edgar_claude_q_{ticker_input}"] = q
                    st.rerun()

    pending_q = st.session_state.pop(f"pending_edgar_claude_q_{ticker_input}", None)
    user_q    = st.chat_input(f"Ask Claude about {ticker_input}'s 10-K filing...",
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

    *Run the same ticker on Equity Scout (Polygon) to compare scores — see punch list #57.*
    """)
