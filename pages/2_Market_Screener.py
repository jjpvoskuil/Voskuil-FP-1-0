import streamlit as st
import requests
import pandas as pd
from io import StringIO
import time
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from claude_utils import ask_claude_about_equity


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
    try:
        det_data = poly_get(f"/v3/reference/tickers/{ticker}")
        det = det_data.get("results", {}) if det_data else {}
        market_cap = safe_float(det.get("market_cap"))
        shares     = safe_float(det.get("weighted_shares_outstanding"))

        price_data = poly_get(f"/v2/aggs/ticker/{ticker}/prev")
        price = None
        try:
            price = float(price_data["results"][0]["c"])
        except (KeyError, TypeError, IndexError):
            pass

        fin_data = poly_get("/vX/reference/financials", {
            "ticker": ticker, "timeframe": "annual", "limit": 1,
            "order": "desc", "sort": "period_of_report_date",
        })
        if not fin_data or not fin_data.get("results"):
            return None

        f   = fin_data["results"][0]["financials"]
        inc = f.get("income_statement",    {})
        cf  = f.get("cash_flow_statement", {})
        bs  = f.get("balance_sheet",       {})

        op_cf  = fval(cf, "net_cash_flow_from_operating_activities")
        inv_cf = fval(cf, "net_cash_flow_from_investing_activities")
        fcf    = (op_cf + inv_cf) if (op_cf is not None and inv_cf is not None) else None

        if fcf is None or fcf <= 0:
            return None

        fcf_yield    = (fcf / market_cap) if (market_cap and market_cap > 0) else None
        gross_profit = fval(inc, "gross_profit")
        revenues     = fval(inc, "revenues")
        gross_margin = (gross_profit / revenues) if (gross_profit and revenues and revenues > 0) else None
        net_income   = fval(inc, "net_income_loss")
        total_assets = fval(bs,  "assets")
        current_liab = fval(bs,  "current_liabilities")
        invested_cap = (total_assets - current_liab) if (total_assets and current_liab) else None
        roic         = (net_income / invested_cap) if (net_income and invested_cap and invested_cap != 0) else None
        long_term_debt = fval(bs, "long_term_debt") or fval(bs, "noncurrent_liabilities")
        debt_to_fcf    = (long_term_debt / fcf) if (long_term_debt is not None and fcf > 0) else None
        interest_cov, is_net_creditor = calc_interest_coverage(inc)
        dna_proxy  = (op_cf - net_income) if (op_cf and net_income) else None
        capex_abs  = abs(inv_cf) if inv_cf else 0
        owner_earn = (net_income + (dna_proxy or 0) - capex_abs) if net_income is not None else None
        poe        = (price / (owner_earn / shares)) if (owner_earn and owner_earn > 0 and shares and price) else None
        div_ps     = fval(inc, "common_stock_dividends")
        div_yield  = (div_ps / price) if (div_ps and price and price > 0) else None

        return {
            "ticker":            ticker,
            "name":              det.get("name", ticker),
            "sector":            det.get("sic_description", "N/A"),
            "price":             price,
            "market_cap":        market_cap,
            "fcf_yield":         fcf_yield,
            "roic":              roic,
            "debt_to_fcf":       debt_to_fcf,
            "interest_coverage": interest_cov,
            "is_net_creditor":   is_net_creditor,
            "gross_margin":      gross_margin,
            "price_owner_earn":  poe,
            "dividend_yield":    div_yield,
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

import concurrent.futures
from sec_utils import fetch_10k_sections

# ── Helper: build context string from results dataframe ──────────────
def build_ms_context(df):
    from claude_utils import get_user_profile as _gup
    _prof = _gup()
    _age  = _prof.get('age', 57)
    _sage = _prof.get('spouse_age', '')
    _wd   = _prof.get('monthly_withdrawal', 8000)
    _pv   = _prof.get('portfolio_val', 3_790_000)
    _inf  = _prof.get('inflation', 4.0)
    _age_str = f"{_age}-year-old" + (f" and spouse age {_sage}" if _sage else "")
    lines = [
        "MARKET SCREEN RESULTS — Voskuil Owner's Framework\n",
        f"Investment context: Buffett + Munger concentrated value philosophy.",
        f"Investor: {_age_str} | Portfolio: ${_pv/1e6:.1f}M | Monthly target: ${_wd:,.0f} | Inflation assumption: {_inf:.1f}%. Hold horizon 5-10 years.\n",
        f"Top {len(df)} results from S&P 500 screen:\n",
    ]
    for _, row in df.iterrows():
        def f(v, t="pct"):
            if v is None or (isinstance(v, float) and pd.isna(v)): return "N/A"
            if t == "pct":   return f"{v:.1%}"
            if t == "ratio": return f"{v:.1f}x"
            return str(v)
        lines.append(
            f"{row['ticker']} ({row.get('name','')}) | Score: {int(row['score'])}/100 | "
            f"FCF Yield: {f(row.get('fcf_yield'))} | ROIC: {f(row.get('roic'))} | "
            f"Debt/FCF: {f(row.get('debt_to_fcf'),'ratio')} | Gross Margin: {f(row.get('gross_margin'))} | "
            f"P/OE: {f(row.get('price_owner_earn'),'ratio')} | Div: {f(row.get('dividend_yield'))} | "
            f"Sector: {row.get('sector','N/A')}"
        )
    return "\n".join(lines)


# ── Helper: fetch 10-K sections for a list of tickers in parallel ─────
def fetch_filings_parallel(tickers: list) -> dict:
    """Returns {ticker: filing_result} fetched concurrently."""
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_map = {executor.submit(fetch_10k_sections, t): t for t in tickers}
        for future in concurrent.futures.as_completed(future_map):
            ticker = future_map[future]
            try:
                results[ticker] = future.result()
            except Exception as e:
                results[ticker] = {"sections": {}, "error": str(e)}
    return results


# ── Helper: build deep-dive context with filing sections ──────────────
def build_deep_dive_context(df, filings: dict, question: str) -> str:
    lines = [build_ms_context(df), "\n\n=== SEC 10-K FILING EXCERPTS ===\n"]
    for ticker, filing in filings.items():
        sections = filing.get("sections", {})
        err      = filing.get("error")
        lines.append(f"\n--- {ticker} ---")
        if err:
            lines.append(f"[Filing unavailable: {err}]")
            continue
        for key, label in [
            ("business",     "BUSINESS"),
            ("risk_factors", "RISK FACTORS"),
            ("mda",          "MD&A"),
        ]:
            text = sections.get(key, "")
            if text:
                lines.append(f"[{label}]: {text[:3000]}")
    lines.append(f"\n\nQUESTION: {question}")
    return "\n".join(lines)


# ── Helper: extract ticker mentions from a message ────────────────────
def extract_tickers_from_text(text: str, valid_tickers: list) -> list:
    """Find uppercase 1-5 letter words in text that match valid tickers."""
    words   = re.findall(r'\b[A-Z]{1,5}\b', text)
    matches = [w for w in words if w in valid_tickers]
    return list(dict.fromkeys(matches))  # deduplicate preserving order


import re

# ─────────────────────────────────────────────────────────────────────
# PAGE UI
# ─────────────────────────────────────────────────────────────────────
st.title("📡 Market Screener")
st.caption("Scans the S&P 500 through the Voskuil Owner's Framework. Surfaces the top concentrated opportunities.")
st.info("**How this works:** Fetches fundamentals from Polygon.io SEC filings, scores each company on the 6-metric Owner's Framework, and surfaces the top results. Negative FCF companies are automatically eliminated.")
st.divider()

# ── Weight reset handler ──────────────────────────────────────────────
_weight_map = [("w_fcf","FCF Yield"),("w_roic","ROIC"),("w_debt","Debt / FCF"),
               ("w_gm","Gross Margin"),("w_ic","Interest Coverage"),("w_poe","Price / Owner Earnings")]
for _wkey, _mkey in _weight_map:
    if st.session_state.pop(f"pending_reset_{_wkey}", False):
        st.session_state[_wkey] = DEFAULT_WEIGHTS[_mkey]
        st.session_state.scoring_weights[_mkey] = DEFAULT_WEIGHTS[_mkey]

with st.expander("⚙️ Customize Scoring Weights", expanded=False):
    st.caption("Adjust freely — scoring uses the last Applied set. Click Apply Weights when total hits 100.")
    if "scoring_weights"   not in st.session_state:
        st.session_state.scoring_weights   = DEFAULT_WEIGHTS.copy()
    if "committed_weights" not in st.session_state:
        st.session_state.committed_weights = DEFAULT_WEIGHTS.copy()
    sw = st.session_state.scoring_weights
    rc1, rc2, rc3 = st.columns([1.2, 1.2, 4])
    if rc1.button("↺ Reset to Defaults", key="ms_reset_weights"):
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
    if rc2.button("✅ Apply Weights", key="ms_apply_weights", type="primary", disabled=not apply_ok,
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

# ── Run screen ────────────────────────────────────────────────────────
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

    # Cache screen results so Claude panel persists across reruns
    st.session_state['ms_results_df']    = results_df
    st.session_state['ms_total_tickers'] = total_tickers
    st.session_state['ms_results_count'] = len(results)
    # Clear previous Claude conversation and selections when a new screen runs
    st.session_state['ms_claude_convo']        = []
    st.session_state['ms_claude_context_sent'] = False
    st.session_state['ms_selected_tickers']    = []
    st.session_state.pop('ms_filings', None)

# ── Render results (fresh or cached) ─────────────────────────────────
if 'ms_results_df' in st.session_state:
    results_df    = st.session_state['ms_results_df']
    total_tickers = st.session_state.get('ms_total_tickers', 0)

    if not run_screen:
        st.info("💡 Showing results from last screen run. Click **Run Screen** to refresh.")

    st.divider()
    st.markdown(f"## 🏆 Top {len(results_df)} Concentrated Opportunities")
    st.caption("Ranked by Voskuil Owner's Framework score.")

    def fmt(val, fmt_type):
        if val is None or (isinstance(val, float) and pd.isna(val)): return "N/A"
        if fmt_type == "pct":   return f"{val:.1%}"
        if fmt_type == "ratio": return f"{val:.1f}x"
        return str(val)

    # ── Init checkbox selection state ───────────────────────────────
    if 'ms_selected_tickers' not in st.session_state:
        st.session_state['ms_selected_tickers'] = []

    # Clear selections when a new screen runs
    _selected = st.session_state.get('ms_selected_tickers', [])

    for rank, row in results_df.iterrows():
        score       = int(row['score'])
        label, icon = score_to_label(score)
        ticker      = row['ticker']
        is_checked  = ticker in _selected

        with st.container():
            c1, c2, c3, c4, c5, c6, c7, c8, c9 = st.columns([1, 3, 2, 2, 2, 2, 2, 2, 1.5])
            with c1:
                st.markdown(f"### {icon}")
                st.markdown(f"**#{rank+1}**")
            with c2:
                st.markdown(f"**{ticker}**")
                st.caption(row.get('name', ''))
                st.caption(row.get('sector', ''))
            with c3: st.metric("Score",        f"{score}/100")
            with c4: st.metric("FCF Yield",    fmt(row.get('fcf_yield'),        "pct"))
            with c5: st.metric("ROIC",         fmt(row.get('roic'),             "pct"))
            with c6: st.metric("Gross Margin", fmt(row.get('gross_margin'),     "pct"))
            with c7: st.metric("Debt/FCF",     fmt(row.get('debt_to_fcf'),      "ratio"))
            with c8: st.metric("P/OE",         fmt(row.get('price_owner_earn'), "ratio"))
            with c9:
                # Checkbox — limit selection to 5
                _at_limit = len(_selected) >= 5 and ticker not in _selected
                st.caption("Deep Dive")
                checked = st.checkbox(
                    "☑ Select",
                    value=is_checked,
                    key=f"ms_chk_{ticker}_{rank}",
                    disabled=_at_limit,
                    help="Max 5 selected" if _at_limit else f"Add {ticker} to deep dive",
                )
                if checked and ticker not in _selected:
                    _selected.append(ticker)
                    st.session_state['ms_selected_tickers'] = _selected
                elif not checked and ticker in _selected:
                    _selected.remove(ticker)
                    st.session_state['ms_selected_tickers'] = _selected

            div = row.get('dividend_yield')
            if div: st.caption(f"💰 Dividend Yield: {div:.2%}")
            if row.get('is_net_creditor'): st.caption("✨ Net Creditor")
            st.markdown(f"[🔍 Deep Dive in Equity Scout]({APP_URL}/equity_scout?ticker={ticker}&auto=1)")
            st.divider()

    st.markdown("### 📊 Screen Summary")
    s1, s2, s3, s4 = st.columns(4)
    with s1: st.metric("Scanned",           total_tickers)
    with s2: st.metric("Passed FCF Filter", st.session_state.get('ms_results_count', len(results_df)))
    with s3: st.metric("Avg Score",         f"{results_df['score'].mean():.0f}")
    with s4: st.metric("Strong Buys (80+)", len(results_df[results_df['score'] >= 80]))

    st.markdown("### 💾 Export Results")
    export_df = results_df[['ticker','name','sector','score','fcf_yield','roic','gross_margin',
                              'debt_to_fcf','interest_coverage','price_owner_earn','dividend_yield','price','market_cap']].copy()
    export_df.columns = ['Ticker','Name','Sector','Score','FCF Yield','ROIC','Gross Margin',
                          'Debt/FCF','Interest Coverage','Price/Owner Earnings','Dividend Yield','Price','Market Cap']
    st.download_button(label="⬇️ Download Results as CSV", data=export_df.to_csv(index=False),
                        file_name="voskuil_screen_results.csv", mime="text/csv")

    # ── Ask Claude Panel ──────────────────────────────────────────────
    st.divider()
    st.markdown("### 🤖 Ask Claude — Analyze These Results")
    st.caption(
        "Claude reasons over the full screen results. Ask it to compare, rank by thesis fit, "
        "or flag risks. Use **Deep Dive Top 3** to pull the actual 10-K filings for the top scorers."
    )

    # ── Deep Dive buttons ────────────────────────────────────────────
    top3_tickers     = results_df['ticker'].head(3).tolist()
    selected_tickers = st.session_state.get('ms_selected_tickers', [])

    dd_col1, dd_col2, dd_col3 = st.columns([2, 2, 3])
    with dd_col1:
        if st.button("🔬 Deep Dive Top 3", type="primary", use_container_width=True,
                     help="Fetch SEC 10-K filings for the top 3 scored tickers"):
            st.session_state['ms_pending_deep_dive'] = top3_tickers
            st.session_state['ms_selected_tickers']  = []
            st.rerun()
    with dd_col2:
        n_sel = len(selected_tickers)
        if st.button(
            f"🔬 Deep Dive Selected ({n_sel})",
            type="primary" if n_sel > 0 else "secondary",
            use_container_width=True,
            disabled=n_sel == 0,
            help=f"Fetch SEC filings for: {', '.join(selected_tickers)}" if selected_tickers else "Check boxes next to results to select",
        ):
            st.session_state['ms_pending_deep_dive'] = selected_tickers.copy()
            st.session_state['ms_selected_tickers']  = []
            st.rerun()
    with dd_col3:
        if selected_tickers:
            st.caption(f"✅ Selected: {', '.join(selected_tickers)}")
        else:
            st.caption("☑️ Check boxes next to any result to select for deep dive (max 5)")

    # Show which tickers are loaded
    loaded_filings = st.session_state.get('ms_filings', {})
    if loaded_filings:
        loaded_str = ", ".join(
            f"{'✅' if not v.get('error') else '⚠️'} {k}"
            for k, v in loaded_filings.items()
        )
        with dd_col2:
            st.caption(f"Filings loaded: {loaded_str}")

    # Handle deep dive trigger
    if st.session_state.pop('ms_pending_deep_dive', None):
        with st.spinner(f"📄 Fetching 10-K filings for {', '.join(top3_tickers)} in parallel..."):
            st.session_state['ms_filings'] = fetch_filings_parallel(top3_tickers)
        # Inject a question into the conversation
        from claude_utils import get_user_profile
        _p  = get_user_profile()
        _age = _p.get('age', 57)
        _wd  = _p.get('monthly_withdrawal', 8000)
        _pv  = _p.get('portfolio_val', 3_790_000)
        _sage = _p.get('spouse_age', '')
        _age_str = f"{_age}-year-old" + (f" and spouse age {_sage}" if _sage else "")
        st.session_state['ms_pending_claude_q'] = (
            f"I've now loaded the SEC 10-K filings for {', '.join(top3_tickers)}. "
            f"Please do a full qualitative comparison of these three companies using both "
            f"the quantitative scores and the actual filing text. Apply both Buffett and "
            f"Munger lenses — use Munger's inversion first (what could permanently destroy "
            f"value?), then assess moat durability, management quality, and pricing power. "
            f"Rank them for a {_age_str} household with a ${_pv/1e6:.1f}M portfolio "
            f"targeting ${_wd:,.0f}/month in retirement income. "
            f"Which one would you concentrate in and why?"
        )
        st.rerun()

    # ── Conversation state ────────────────────────────────────────────
    ms_convo_key   = "ms_claude_convo"
    ms_context_key = "ms_claude_context_sent"
    if ms_convo_key not in st.session_state:
        st.session_state[ms_convo_key]   = []
        st.session_state[ms_context_key] = False

    # Display history
    for msg in st.session_state[ms_convo_key]:
        role = msg["role"]
        content = msg["content"]
        if role == "user":
            if "\n---\nQUESTION: " in content:
                content = content.split("\n---\nQUESTION: ", 1)[-1]
            with st.chat_message("user"):
                st.markdown(content)
        else:
            with st.chat_message("assistant", avatar="🤖"):
                st.markdown(content)

    # Suggested starters (only before first message)
    if not st.session_state[ms_convo_key]:
        st.markdown("**Suggested questions:**")
        sq_cols = st.columns(2)
        from claude_utils import get_user_profile
        _sp  = get_user_profile()
        _wd2 = _sp.get('monthly_withdrawal', 8000)
        ms_starters = [
            f"Which fits best for our ${_wd2:,.0f}/month retirement income target?",
            "Apply Munger's inversion — what could permanently destroy value in each?",
            "Compare the top 3 on moat durability using Buffett + Munger criteria.",
            "Which would Buffett most likely hold for 10 years and why?",
        ]
        for i, q in enumerate(ms_starters):
            with sq_cols[i % 2]:
                if st.button(q, key=f"ms_starter_{i}", use_container_width=True):
                    st.session_state["ms_pending_claude_q"] = q
                    st.rerun()

    ms_pending_q = st.session_state.pop("ms_pending_claude_q", None)
    ms_user_q    = st.chat_input("Ask Claude about these screen results...", key="ms_claude_input")
    ms_active_q  = ms_pending_q or ms_user_q

    if ms_active_q:
        # Check if user is requesting a filing for a specific ticker
        all_tickers   = results_df['ticker'].tolist()
        filings_cache = st.session_state.get('ms_filings', {})
        mentioned     = extract_tickers_from_text(ms_active_q, all_tickers)
        new_tickers   = [t for t in mentioned if t not in filings_cache]

        if new_tickers:
            with st.spinner(f"📄 Fetching 10-K filings for {', '.join(new_tickers)}..."):
                new_filings = fetch_filings_parallel(new_tickers)
                filings_cache.update(new_filings)
                st.session_state['ms_filings'] = filings_cache

        with st.chat_message("user"):
            st.markdown(ms_active_q)

        with st.chat_message("assistant", avatar="🤖"):
            with st.spinner("Analyzing..."):
                # Build context — include filing sections if available
                if filings_cache:
                    context_str = build_deep_dive_context(results_df, filings_cache, ms_active_q)
                else:
                    context_str = build_ms_context(results_df) + f"\n\n---\nQUESTION: {ms_active_q}"

                if not st.session_state[ms_context_key]:
                    response = ask_claude_about_equity(
                        ticker="SCREEN", data={}, scores={}, sections={},
                        user_question=context_str,
                        conversation_history=None,
                    )
                    st.session_state[ms_convo_key].append({"role": "user", "content": context_str})
                    st.session_state[ms_context_key] = True
                else:
                    response = ask_claude_about_equity(
                        ticker="SCREEN", data={}, scores={}, sections={},
                        user_question=ms_active_q,
                        conversation_history=st.session_state[ms_convo_key],
                    )
                    st.session_state[ms_convo_key].append({"role": "user", "content": ms_active_q})

                st.session_state[ms_convo_key].append({"role": "assistant", "content": response})
                st.markdown(response)

    if st.session_state.get(ms_convo_key):
        if st.button("🗑️ Clear conversation", key="ms_clear_convo"):
            st.session_state[ms_convo_key]   = []
            st.session_state[ms_context_key] = False
            st.session_state.pop('ms_filings', None)
            st.rerun()

else:
    st.markdown("""
    ### What this screener does

    1. **Loads S&P 500 companies** from Wikipedia
    2. **Eliminates** companies with negative Free Cash Flow
    3. **Scores** remaining companies on the 6-metric Owner's Framework
    4. **Returns top results** ranked by conviction score

    ### What's new
    - 🤖 **Ask Claude** — compare results, rank by thesis fit, or pull SEC 10-K filings for any ticker
    - 🔬 **Deep Dive Top 3** — fetches actual 10-K filings for the top 3 scorers in parallel
    - **Net Creditor detection** — companies earning more interest than they pay score full points
    - **Long-term debt** used for more accurate Debt/FCF

    ---
    **Score guide:** 🟢 80+ Strong Buy · 🟡 65-79 Watch · 🟠 45-64 Caution · 🔴 <45 Avoid

    *Data sourced from Polygon.io SEC filings.*
    """)
