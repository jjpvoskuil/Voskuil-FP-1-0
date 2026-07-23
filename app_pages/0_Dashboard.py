import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import time
import sys, os
from datetime import datetime, timezone
from urllib.parse import quote
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from claude_utils import ask_claude_about_equity, get_user_profile, build_context
from ui_utils import scroll_to_element
from superinvestor_utils import get_conviction_data, get_superinvestor_conviction
from sec_utils import fetch_fundamentals_edgar, DEFAULT_WEIGHTS, THRESHOLDS, score_stock, score_financial_firm_breakdown, FINANCIAL_THRESHOLDS
from github_store import github_get_json, github_put_json
from watchlist_utils import add_to_watchlist, is_watchlisted

st.set_page_config(page_title="Voskuil FP 1.0", layout="wide")
st.title("🛡️ Voskuil FP 1.0: Sovereign Wealth Dashboard")

# ─────────────────────────────────────────────
# MS DATA REFRESH (#74/#75) — moved to the top of the main page, per owner
# request, instead of the sidebar. Deliberately just the button now: no
# instructional caption, no manual-fallback expander (run_push.command is
# still in the repo for anyone reading the code, just not surfaced in the
# UI anymore).
# ─────────────────────────────────────────────
# One-click flow: the prompt itself scripts the login handoff -- Claude
# opens the MS Online login page as its very first action (no need to log
# in before clicking), waits for a one-word confirmation once logged in,
# then runs the rest of the macro end-to-end without further check-ins.
# This matters because MS Online's session times out quickly, so we don't
# want to burn time on setup/cloning before the login tab is even open.
#
# Deep-links into Claude Desktop — claude://cowork/new opens a new Cowork
# session with a prefilled prompt. Support doc:
# https://support.claude.com/en/articles/14729294-open-claude-desktop-with-a-link
_ms_refresh_prompt = (
    "Refresh Morgan Stanley data for Voskuil FP 1.0. "
    "Step 1 (do this immediately, don't wait to ask): open "
    "https://www.morganstanleyclientserv.com in a new Chrome tab via "
    "Claude in Chrome, tell me it's open, then wait for me to reply "
    "that I've logged in -- my MS Online session times out fast, so "
    "open the tab right away rather than checking in first. "
    "Step 2 (once I confirm I'm logged in, run this whole step "
    "autonomously -- don't check in again until it's done or something "
    "goes wrong): clone jjpvoskuil/Voskuil-FP-1-0 if you don't already "
    "have local access (ask me for a fresh GitHub PAT), then navigate "
    "Accounts > Holdings, Accounts > Activity (Current Year, then "
    "Prior Year), and Accounts > Realized Gain/Loss > Details (Current "
    "Year, then Previous Year), and download all 5 files -- you have "
    "my permission to download all 5 without asking again. Convert "
    "them to CSV matching the existing ms_*.csv files in the repo, "
    "validate by running app_pages/0_Dashboard.py through "
    "streamlit.testing.v1.AppTest (no exceptions, sane totals), then "
    "commit and push. Report back when done. See SESSION_NOTES.md in "
    "the repo for the exact workflow and gotchas from last time -- "
    "e.g. the Realized G/L year dropdown is a native <select> that "
    "needs a JS value change, not clicks. You'll need access to my "
    "Downloads folder (~/Downloads) at some point to pick up the "
    "downloaded files -- request it whenever it's convenient, no need "
    "to do that before opening the MS Online tab."
)
# Deliberately NOT passing folder=/Users/JohnV/Downloads here. Claude
# Desktop's "Another app attached '<folder>'" confirmation dialog for
# the folder param clears the prefilled composer text when you click
# Continue -- confirmed live: text was visible behind the dialog, then
# gone after confirming. The prompt itself now asks Claude to request
# Downloads access mid-conversation instead, which doesn't have this bug.
_ms_refresh_url = "claude://cowork/new?q=" + quote(_ms_refresh_prompt)
# A raw <a href="claude://..."> click, rather than st.link_button (which
# opens links via window.open()), turned out to matter -- window.open()'s
# handoff to the OS for non-http(s) schemes is inconsistent across
# browsers and appears to drop the query string in at least Chrome. A
# direct anchor click is a real top-level navigation attempt, which
# browsers hand off to the OS protocol handler intact, query string
# included.
_ms_refresh_html = (
    '<a href="' + _ms_refresh_url + '" target="_self" style="'
    'display:inline-block; text-decoration:none; '
    'background-color:#FF4B4B; color:white; font-weight:600; '
    'padding:0.5rem 1.25rem; border-radius:0.5rem; margin-bottom:1rem;'
    '">🔄 Refresh MS Data via Claude</a>'
)
st.markdown(_ms_refresh_html, unsafe_allow_html=True)

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────
HOLDINGS_FILE  = 'ms_holdings.csv'
TAX_FILE       = 'ms_realized_gl_current.csv'
TAX_FILE_PRIOR = 'ms_realized_gl_prior.csv'
TRANS_FILE_YTD   = 'ms_transactions_ytd.csv'
TRANS_FILE_PRIOR = 'ms_transactions_prior.csv'
APP_URL   = "https://voskuil-fp-1-0-k85bd7afbw8dnqeftzxwbu.streamlit.app"

# Persistent holdings-score cache (#72) — Streamlit Cloud's filesystem and
# in-memory session_state both reset on every reboot/redeploy (which a git
# push triggers), so "Score All Holdings" results would otherwise vanish
# the moment anything gets pushed, or whenever the app recycles from
# inactivity. Same GitHub-Contents-API persistence pattern already used by
# the Market Screener's scan cache (github_store.py).
HOLDINGS_SCORE_CACHE_PATH = "dashboard_holdings_score_cache.json"

DEFAULT_HOLD_THRESHOLDS = {
    "min_roic":      0.12,
    "max_debt_fcf":  5.0,
    "max_poe":       25.0,
    "min_fcf_yield": 0.03,
}

# ─────────────────────────────────────────────
# EDGAR DATA FETCH (holdings scoring)
# ─────────────────────────────────────────────
# Migrated off the old Polygon-primary/yfinance-fallback pipeline, which had
# drifted from the rest of the app: it used the superseded ROIC formula
# (Total Assets - Current Liabilities instead of Total Equity + Total Debt),
# still scored Price/Owner Earnings as a weighted criterion (demoted to
# reference-only everywhere else), and had no rebalancing for missing data.
# This now reuses the exact same fetch_fundamentals_edgar() + score_stock()
# used by Equity Scout, Market Screener, and Compare Stocks, so a holding's
# score here matches its score everywhere else in the app.
def fetch_score_data(ticker):
    data = fetch_fundamentals_edgar(ticker)
    if data.get("error"):
        return None
    data["source"] = "edgar"
    return data

def score_to_badge(score):
    try:
        if score is None or (isinstance(score, float) and pd.isna(score)):
            return "—"
        score = int(score)
        if score >= 80:   return f"🟢 {score}"
        elif score >= 65: return f"🟡 {score}"
        elif score >= 45: return f"🟠 {score}"
        else:             return f"🔴 {score}"
    except Exception:
        return "—"

def hold_verdict(data, thresholds):
    """Returns (verdict, color, icon) based on Buffett hold/add/trim logic.

    Banks/insurers (#70): ROIC and Debt/FCF are meaningless for a leveraged
    balance-sheet business (deposits/policy liabilities ARE the raw material,
    not optional leverage), so the quality leg substitutes ROE for ROIC and
    Equity/Assets for Debt/FCF -- the same swap score_financial_firm_breakdown()
    makes for the Score column, so Signal and Score don't contradict each other.

    Bug fixed here (found via ASML, July 2026): every individual check below
    is written as "metric is None OR metric clears the bar" -- deliberately,
    so a stock missing ONE metric doesn't get unfairly Trimmed on that gap
    alone (same rebalancing philosophy as the Score column). But that means
    if EVERY metric is missing, every check vacuously passes and this used
    to fall through to "Add" -- a false buy signal on a holding the app
    actually knows nothing about, which is worse than useless. Guarded
    below: if there's literally nothing to evaluate, show unrated ("—"),
    matching what the Score column already does for the same holding.
    """
    if data is None:
        return "—", "#888888", ""
    poe       = data.get("price_owner_earn")
    fcf_yield = data.get("fcf_yield")
    subtype   = data.get("financial_subtype")
    if subtype in ("bank", "insurance"):
        roe = data.get("roe")
        eqa = data.get("equity_to_assets")
        eqa_good = (FINANCIAL_THRESHOLDS["equity_assets_good_insurance"] if subtype == "insurance"
                    else FINANCIAL_THRESHOLDS["equity_assets_good"])
        quality_ok = (
            (roe is None or roe >= FINANCIAL_THRESHOLDS["roe_good"]) and
            (eqa is None or eqa >= eqa_good)
        )
        quality_inputs = (roe, eqa)
    else:
        roic      = data.get("roic_10yr_avg")  # (#34) 10-yr avg, cash basis
        debt_fcf  = data.get("debt_to_fcf")
        debt_cads = data.get("debt_to_cads")
        debt_candidates = [d for d in (debt_fcf, debt_cads) if d is not None]
        debt_multiple   = min(debt_candidates) if debt_candidates else None
        quality_ok = (
            (roic         is None or roic         >= thresholds["min_roic"]) and
            (debt_multiple is None or debt_multiple <= thresholds["max_debt_fcf"])
        )
        quality_inputs = (roic, debt_multiple)
    value_ok = (
        (poe       is None or poe       <= thresholds["max_poe"]) and
        (fcf_yield is None or fcf_yield >= thresholds["min_fcf_yield"])
    )
    if all(v is None for v in (*quality_inputs, poe, fcf_yield)):
        return "—", "#888888", ""
    if not quality_ok:
        return "Trim", "#e74c3c", "🔴"
    elif quality_ok and value_ok:
        return "Add", "#2ecc71", "🟢"
    else:
        return "Hold", "#f39c12", "🟡"

@st.cache_data
def fetch_sec_tickers():
    try:
        url = "https://www.sec.gov/files/company_tickers.json"
        headers = {'User-Agent': 'Voskuil Wealth Engine (voskuil@example.com)'}
        response = requests.get(url, headers=headers)
        data = response.json()
        return {item['ticker']: str(item['cik_str']).zfill(10) for item in data.values()}
    except Exception:
        return {}

cik_map = fetch_sec_tickers()

def get_clean_df(filename, anchor_text):
    try:
        with open(filename, 'r') as f:
            lines = f.readlines()
        header_idx = next(i for i, line in enumerate(lines) if anchor_text in line)
        return pd.read_csv(filename, skiprows=header_idx)
    except Exception:
        return None

# ─────────────────────────────────────────────
# DATA PROCESSING
# ─────────────────────────────────────────────
total_val          = 0.0
total_income       = 0.0
ira_gain_total     = 0.0
taxable_gain_total = 0.0
ytd_dividends      = 0.0
ytd_interest       = 0.0
py_dividends       = 0.0
py_interest        = 0.0
py_ira_gain_total     = 0.0
py_taxable_gain_total = 0.0
product_mix        = pd.DataFrame()
df_holdings_raw    = None

# Holdings
df_holdings_raw = get_clean_df(HOLDINGS_FILE, "Account Number")
if df_holdings_raw is not None:
    df_holdings_raw.columns = [c.strip() for c in df_holdings_raw.columns]
    df_holdings_raw = df_holdings_raw[~df_holdings_raw.iloc[:, 0].astype(str).str.contains('Total', case=False, na=False)]
    for col in ['Market Value ($)', 'Est. Annual Income ($)', 'Quantity']:
        if col in df_holdings_raw.columns:
            df_holdings_raw[col] = pd.to_numeric(
                df_holdings_raw[col].astype(str).str.replace(',', '').str.replace('"', ''),
                errors='coerce'
            )
    total_val    = df_holdings_raw['Market Value ($)'].sum()
    total_income = df_holdings_raw['Est. Annual Income ($)'].sum()
    product_mix  = df_holdings_raw.groupby('Product Type')['Market Value ($)'].sum().reset_index()
    product_mix  = product_mix.sort_values(by='Market Value ($)', ascending=False)
    color_palette = px.colors.qualitative.Prism
    product_mix['color'] = [color_palette[i % len(color_palette)] for i in range(len(product_mix))]
    df_holdings_raw = df_holdings_raw.dropna(subset=['Symbol'])

# Current Year G/L
df_tax = get_clean_df(TAX_FILE, "Account Number")
if df_tax is not None:
    df_tax.columns = [c.strip() for c in df_tax.columns]
    df_tax_clean = df_tax[~df_tax.iloc[:, 0].astype(str).str.contains('Total', case=False, na=False)]
    df_tax_clean['Numeric Gain'] = pd.to_numeric(
        df_tax_clean.iloc[:, 13].astype(str).str.replace(',', '').str.replace('"', ''),
        errors='coerce'
    )
    ira_mask           = df_tax_clean.iloc[:, 0].astype(str).str.contains('IRA', case=False, na=False)
    ira_gain_total     = df_tax_clean[ira_mask]['Numeric Gain'].sum()
    taxable_gain_total = df_tax_clean[~ira_mask]['Numeric Gain'].sum()

# YTD Transactions
df_trans = get_clean_df(TRANS_FILE_YTD, "Activity Date")
if df_trans is not None:
    df_trans.columns = [c.strip() for c in df_trans.columns]
    df_trans['Amount($)'] = pd.to_numeric(
        df_trans['Amount($)'].astype(str).str.replace(',', '').str.replace('"', ''),
        errors='coerce'
    )
    df_trans['Activity Date'] = pd.to_datetime(df_trans['Activity Date'], errors='coerce')
    today    = pd.Timestamp.today()
    ytd_mask = df_trans['Activity Date'].dt.year == today.year
    df_trans_ytd  = df_trans[ytd_mask]
    ytd_dividends = df_trans_ytd[
        df_trans_ytd['Activity'].str.contains('Dividend', na=False, case=False)
    ]['Amount($)'].sum()
    ytd_interest = df_trans_ytd[
        df_trans_ytd['Activity'].str.contains('Interest', na=False, case=False)
    ]['Amount($)'].sum()

# Prior Year Transactions
df_trans_prior = get_clean_df(TRANS_FILE_PRIOR, "Activity Date")
if df_trans_prior is not None:
    df_trans_prior.columns = [c.strip() for c in df_trans_prior.columns]
    df_trans_prior['Amount($)'] = pd.to_numeric(
        df_trans_prior['Amount($)'].astype(str).str.replace(',', '').str.replace('"', ''),
        errors='coerce'
    )
    df_trans_prior['Activity Date'] = pd.to_datetime(df_trans_prior['Activity Date'], errors='coerce')
    py_dividends = df_trans_prior[
        df_trans_prior['Activity'].str.contains('Dividend', na=False, case=False)
    ]['Amount($)'].sum()
    py_interest = df_trans_prior[
        df_trans_prior['Activity'].str.contains('Interest', na=False, case=False)
    ]['Amount($)'].sum()

# Prior Year G/L
df_tax_prior = get_clean_df(TAX_FILE_PRIOR, "Account Number")
if df_tax_prior is not None:
    df_tax_prior.columns = [c.strip() for c in df_tax_prior.columns]
    df_tax_prior = df_tax_prior[~df_tax_prior.iloc[:, 0].astype(str).str.contains('Total', case=False, na=False)]
    gain_col = next((c for c in df_tax_prior.columns if 'Realized Gain' in c), None)
    if gain_col:
        df_tax_prior['Numeric Gain'] = pd.to_numeric(
            df_tax_prior[gain_col].astype(str).str.replace(',', '').str.replace('"', '').str.replace('$', ''),
            errors='coerce'
        )
        ira_mask_prior        = df_tax_prior.iloc[:, 0].astype(str).str.contains('IRA', case=False, na=False)
        py_ira_gain_total     = df_tax_prior[ira_mask_prior]['Numeric Gain'].sum()
        py_taxable_gain_total = df_tax_prior[~ira_mask_prior]['Numeric Gain'].sum()

# ─────────────────────────────────────────────
# POWER BAR
# ─────────────────────────────────────────────
def power_metric(col, label, current, prior, help=None):
    delta    = current - prior if prior != 0 else None
    arrow    = "▲" if delta and delta > 0 else "▼" if delta and delta < 0 else ""
    color    = "green" if delta and delta > 0 else "red" if delta and delta < 0 else "gray"
    py_label = label.replace("YTD", "PY").replace("(YTD)", "(PY)")
    with col:
        if help:
            st.metric(label, f"${current:,.2f}", help=help)
        else:
            st.metric(label, f"${current:,.2f}")
        if prior != 0:
            st.markdown(
                f"<p style='font-size:0.875rem;color:rgb(120,120,120);margin:0.5rem 0 0 0'>{py_label}</p>"
                f"<p style='font-size:2.25rem;font-weight:400;color:rgb(49,51,63);margin:0;padding:0'>${prior:,.2f}</p>"
                f"<p style='font-size:0.875rem;margin:0;padding:0;color:{color}'>{arrow} ${abs(delta):,.0f} vs PY</p>",
                unsafe_allow_html=True
            )

col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric("Total Market Value", f"${total_val:,.2f}")
power_metric(col2, "Taxable G/L (YTD)", taxable_gain_total, py_taxable_gain_total,
             help="Gains from non-IRA accounts.")
power_metric(col3, "IRA G/L (YTD)", ira_gain_total, py_ira_gain_total,
             help="Tax-deferred growth in IRA buckets.")
power_metric(col4, "YTD Dividends", ytd_dividends, py_dividends)
power_metric(col5, "YTD Interest", ytd_interest, py_interest)
st.divider()

# ─────────────────────────────────────────────
# ASSET ALLOCATION
# ─────────────────────────────────────────────
st.subheader("Institutional Asset Allocation")
c1, c2, c3 = st.columns([3, 4, 5])
with c1:
    if not product_mix.empty:
        fig = px.pie(product_mix, values='Market Value ($)', names='Product Type', hole=0.4,
                     color='Product Type',
                     color_discrete_map=dict(zip(product_mix['Product Type'], product_mix['color'])))
        fig.update_traces(textinfo='percent', textposition='inside')
        fig.update_layout(showlegend=False, margin=dict(t=0, b=0, l=0, r=0), height=300)
        st.plotly_chart(fig, use_container_width=True)
with c2:
    st.markdown("**Product Type**")
    for _, row in product_mix.iterrows():
        st.markdown(f"<span style='color:{row['color']};'>●</span> {row['Product Type']}", unsafe_allow_html=True)
with c3:
    st.markdown("**Value ($)**")
    for _, row in product_mix.iterrows():
        st.markdown(f"<span style='color:{row['color']};'>●</span> ${row['Market Value ($)']:,.0f}", unsafe_allow_html=True)
st.divider()

# ─────────────────────────────────────────────
# CASH FLOW MONITOR
# ─────────────────────────────────────────────
st.subheader("Retirement Cash Flow Monitor")
total_ytd_cash = ytd_dividends + ytd_interest
st.write(f"Passive Cash Flow YTD: **${total_ytd_cash:,.2f}**")
st.progress(min(total_ytd_cash / 96000.0, 1.0))
st.info("Targeting progress toward your **$37,386 income gap** toward legacy preservation.")
st.divider()

# ─────────────────────────────────────────────
# HOLDINGS EXPLORER
# ─────────────────────────────────────────────
st.header("📋 Holdings Explorer")

if df_holdings_raw is not None:
    consolidated = (
        df_holdings_raw.groupby('Symbol')
        .agg(
            Name=('Name', 'first'),
            Product_Type=('Product Type', 'first'),
            Total_Value=('Market Value ($)', 'sum'),
            Total_Shares=('Quantity', 'sum'),
            Accounts=('Account Number', lambda x: ', '.join(x.astype(str).unique())),
            Account_Count=('Account Number', 'nunique'),
        )
        .reset_index()
        .sort_values('Total_Value', ascending=False)
    )

    # ── Session state init ─────────────────────────────────────────────
    if 'holding_scores'     not in st.session_state: st.session_state.holding_scores     = {}
    if 'holding_sources'    not in st.session_state: st.session_state.holding_sources    = {}
    if 'holding_raw_data'   not in st.session_state: st.session_state.holding_raw_data   = {}
    if 'scoring_weights'    not in st.session_state: st.session_state.scoring_weights    = DEFAULT_WEIGHTS.copy()
    if 'committed_weights'  not in st.session_state: st.session_state.committed_weights  = DEFAULT_WEIGHTS.copy()

    # ── Load persisted holdings scores (#72) ────────────────────────────
    # Runs once per browser session. If this session doesn't already have
    # scores in memory (fresh session, or session_state got reset by a
    # redeploy), pull the last-saved scoring run from GitHub instead of
    # showing everything as unscored until the owner re-clicks "Score All."
    if 'holdings_cache_load_attempted' not in st.session_state:
        st.session_state['holdings_cache_load_attempted'] = True
        if not st.session_state.holding_scores:
            _cached, _sha, _err = github_get_json(HOLDINGS_SCORE_CACHE_PATH)
            if _cached and not _err:
                st.session_state.holding_scores            = _cached.get('scores', {})
                st.session_state.holding_sources           = _cached.get('sources', {})
                st.session_state.holding_raw_data          = _cached.get('raw_data', {})
                st.session_state.holdings_cache_timestamp  = _cached.get('scored_timestamp')
            elif _err:
                st.session_state.holdings_cache_load_error = _err
    if 'hold_thresholds'    not in st.session_state: st.session_state.hold_thresholds    = DEFAULT_HOLD_THRESHOLDS.copy()
    st.session_state.holding_weights = st.session_state.committed_weights

    # ── Weight reset handler ───────────────────────────────────────────
    _weight_map = [("w_fcf","FCF Yield"),("w_roic","ROIC"),("w_debt","Debt / FCF"),
                   ("w_gm","Gross Margin"),("w_ic","Interest Coverage")]
    for _wkey, _mkey in _weight_map:
        if st.session_state.pop(f"pending_reset_{_wkey}", False):
            st.session_state[_wkey] = DEFAULT_WEIGHTS[_mkey]
            st.session_state.scoring_weights[_mkey] = DEFAULT_WEIGHTS[_mkey]

    # ── Uncommitted weights banner ─────────────────────────────────────
    _live_total    = sum(st.session_state.scoring_weights.values())
    _weights_dirty = st.session_state.scoring_weights != st.session_state.committed_weights
    if _weights_dirty and _live_total != 100:
        st.info("⚙️ Weights have unsaved changes. Adjust sliders to reach 100 pts, then click **Apply Weights**.")
    elif _weights_dirty and _live_total == 100:
        st.warning("⚙️ Weights ready to apply — click **Apply Weights** to activate.")

    # ── Scoring Weights Expander ───────────────────────────────────────
    with st.expander("⚙️ Scoring Weights", expanded=False):
        st.caption(
            "Adjust freely — scoring uses the last **Applied** set. Shared with Equity Scout, "
            "Market Screener, and Compare Stocks, so a holding's score here matches its score "
            "everywhere else in the app. Price/Owner Earnings is shown as a reference valuation "
            "metric on holding detail cards but isn't scored — same as the rest of the app."
        )
        rc1, rc2, rc3 = st.columns([1.2, 1.2, 4])
        if rc1.button("↺ Reset to Defaults", key="reset_stock_weights"):
            st.session_state.scoring_weights  = DEFAULT_WEIGHTS.copy()
            st.session_state.committed_weights = DEFAULT_WEIGHTS.copy()
            for _wkey, _mkey in _weight_map:
                st.session_state[_wkey] = DEFAULT_WEIGHTS[_mkey]
            st.rerun()

        sw = st.session_state.scoring_weights
        draft_weights = {
            "FCF Yield":              st.session_state.get("w_fcf",  sw["FCF Yield"]),
            "ROIC":                   st.session_state.get("w_roic", sw["ROIC"]),
            "Debt / FCF":             st.session_state.get("w_debt", sw["Debt / FCF"]),
            "Gross Margin":           st.session_state.get("w_gm",   sw["Gross Margin"]),
            "Interest Coverage":      st.session_state.get("w_ic",   sw["Interest Coverage"]),
        }
        draft_total = sum(draft_weights.values())
        apply_ok    = draft_total == 100

        if rc2.button("✅ Apply Weights", key="apply_weights", type="primary", disabled=not apply_ok,
                      help="Activates these weights for scoring." if apply_ok else f"Total must equal 100 (currently {draft_total})."):
            st.session_state.committed_weights = draft_weights.copy()
            st.session_state.scoring_weights   = draft_weights.copy()
            st.session_state.holding_weights   = draft_weights.copy()
            st.success("✅ Weights applied.")
            st.rerun()

        cw = st.session_state.committed_weights
        rc3.caption(
            f"**Active:** FCF {cw['FCF Yield']} · ROIC {cw['ROIC']} · Debt {cw['Debt / FCF']} · "
            f"GM {cw['Gross Margin']} · IC {cw['Interest Coverage']}"
        )

        w_col1, w_col2 = st.columns(2)
        with w_col1:
            _sc, _sb = st.columns([4, 1])
            with _sc: w_fcf = st.slider("FCF Yield", 0, 60, sw["FCF Yield"], step=5, key="w_fcf")
            with _sb:
                st.write("")
                if st.button(f"↺ {DEFAULT_WEIGHTS['FCF Yield']}", key="reset_w_fcf", use_container_width=True):
                    st.session_state["pending_reset_w_fcf"] = True; st.rerun()
            _sc, _sb = st.columns([4, 1])
            with _sc: w_roic = st.slider("ROIC", 0, 60, sw["ROIC"], step=5, key="w_roic")  # (#34) raised from 40 -- new default (40) needs headroom
            with _sb:
                st.write("")
                if st.button(f"↺ {DEFAULT_WEIGHTS['ROIC']}", key="reset_w_roic", use_container_width=True):
                    st.session_state["pending_reset_w_roic"] = True; st.rerun()
            _sc, _sb = st.columns([4, 1])
            with _sc: w_debt = st.slider("Debt / FCF", 0, 40, sw["Debt / FCF"], step=5, key="w_debt")
            with _sb:
                st.write("")
                if st.button(f"↺ {DEFAULT_WEIGHTS['Debt / FCF']}", key="reset_w_debt", use_container_width=True):
                    st.session_state["pending_reset_w_debt"] = True; st.rerun()
        with w_col2:
            _sc, _sb = st.columns([4, 1])
            with _sc: w_gm = st.slider("Gross Margin", 0, 40, sw["Gross Margin"], step=5, key="w_gm")
            with _sb:
                st.write("")
                if st.button(f"↺ {DEFAULT_WEIGHTS['Gross Margin']}", key="reset_w_gm", use_container_width=True):
                    st.session_state["pending_reset_w_gm"] = True; st.rerun()
            _sc, _sb = st.columns([4, 1])
            with _sc: w_ic = st.slider("Interest Coverage", 0, 40, sw["Interest Coverage"], step=5, key="w_ic")
            with _sb:
                st.write("")
                if st.button(f"↺ {DEFAULT_WEIGHTS['Interest Coverage']}", key="reset_w_ic", use_container_width=True):
                    st.session_state["pending_reset_w_ic"] = True; st.rerun()

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

    # ── Hold/Add/Trim Thresholds Expander ─────────────────────────────
    with st.expander("📊 Hold / Add / Trim Thresholds", expanded=False):
        st.caption("Adjust quality and value thresholds that drive the Add / Hold / Trim signal on each holding.")
        ht = st.session_state.hold_thresholds
        tc1, tc2 = st.columns(2)
        with tc1:
            min_roic = st.slider("Min ROIC to Hold (%)", 0, 30,
                                  int(ht["min_roic"] * 100), step=1,
                                  help="Below this ROIC → Trim") / 100
            max_debt = st.slider("Max Debt/FCF to Hold (x)", 0.0, 15.0,
                                  float(ht["max_debt_fcf"]), step=0.5,
                                  help="Above this leverage → Trim")
        with tc2:
            max_poe  = st.slider("Max P/Owner Earnings to Add (x)", 0.0, 60.0,
                                  float(ht["max_poe"]), step=1.0,
                                  help="Above this valuation → Hold")
            min_fcfy = st.slider("Min FCF Yield to Add (%)", 0, 10,
                                  int(ht["min_fcf_yield"] * 100), step=1,
                                  help="Below this yield → Hold") / 100
        tb1, tb2, _ = st.columns([1.2, 1.2, 4])
        if tb1.button("✅ Apply Thresholds", type="primary", key="apply_thresholds"):
            st.session_state.hold_thresholds = {
                "min_roic":      min_roic,
                "max_debt_fcf":  max_debt,
                "max_poe":       max_poe,
                "min_fcf_yield": min_fcfy,
            }
            st.success("✅ Thresholds applied.")
            st.rerun()
        if tb2.button("↺ Reset Thresholds", key="reset_thresholds"):
            st.session_state.hold_thresholds = DEFAULT_HOLD_THRESHOLDS.copy()
            st.rerun()

    # Scoring uses committed weights
    active_weights = st.session_state.committed_weights
    total_weight   = sum(active_weights.values())
    unique_symbols = consolidated['Symbol'].tolist()
    n_symbols      = len(unique_symbols)

    # ── Score All Button ───────────────────────────────────────────────
    score_col, info_col = st.columns([2, 5])
    with score_col:
        run_scoring = st.button(
            f"⚡ Score All {n_symbols} Holdings", type="primary",
            disabled=(total_weight != 100),
            help="Weights must add up to 100." if total_weight != 100 else "Score using SEC EDGAR fundamentals."
        )
    with info_col:
        _all_scores = st.session_state.holding_scores
        scored_count  = sum(1 for s in _all_scores.values() if s is not None)
        failed_count  = sum(1 for s in _all_scores.values() if s is None)
        if _all_scores:
            msg = f"✅ {scored_count} holdings scored via SEC EDGAR"
            if failed_count > 0:
                msg += f" ({failed_count} unavailable — foreign ADRs or no EDGAR filings)"
            _ts = st.session_state.get('holdings_cache_timestamp')
            if _ts:
                try:
                    _ts_str = datetime.fromisoformat(_ts).strftime("%b %d, %Y %I:%M %p UTC")
                    msg += f" — saved {_ts_str}, persists across reloads"
                except Exception:
                    pass
            st.success(msg)
        else:
            st.caption("Scores not yet loaded. Click the button above.")
        if st.session_state.get('holdings_cache_load_error'):
            st.caption(f"⚠️ Couldn't load saved scores: {st.session_state['holdings_cache_load_error']}")

    if run_scoring:
        progress_bar = st.progress(0)
        status_text  = st.empty()
        scores  = {}
        sources = {}
        raw_data_cache = {}
        for i, symbol in enumerate(unique_symbols):
            pct = (i + 1) / n_symbols
            progress_bar.progress(pct)
            status_text.markdown(f"⏳ Scoring **{symbol}** — {i+1} of {n_symbols}")
            data = fetch_score_data(symbol)
            if data is not None:
                if data.get("financial_subtype") in ("bank", "insurance"):
                    scores[symbol], _ = score_financial_firm_breakdown(data, data["financial_subtype"])
                else:
                    scores[symbol]      = score_stock(data, active_weights)
                sources[symbol]         = data.get("source", "edgar")
                raw_data_cache[symbol]  = data
            else:
                scores[symbol]  = None
                sources[symbol] = None
            time.sleep(0.1)
        st.session_state.holding_scores   = scores
        st.session_state.holding_sources  = sources
        st.session_state.holding_raw_data = raw_data_cache
        progress_bar.progress(1.0)
        scored_ok = len([s for s in scores.values() if s is not None])
        status_text.markdown(f"✅ Done — {scored_ok} of {n_symbols} scored via SEC EDGAR.")

        # ── Persist to GitHub (#72) — survives the next redeploy/reboot ──
        # instead of living only in this browser tab's in-memory
        # session_state. _history/_latest (bulky per-year EDGAR series,
        # only used for trend charts on other pages) are dropped before
        # saving since Dashboard itself never reads them.
        _cache_timestamp = datetime.now(timezone.utc).isoformat()
        _cache_payload = {
            "scored_timestamp": _cache_timestamp,
            "scores":  scores,
            "sources": sources,
            "raw_data": {
                sym: {k: v for k, v in d.items() if k not in ("_history", "_latest")}
                for sym, d in raw_data_cache.items() if d is not None
            },
        }
        _ok, _msg = github_put_json(
            HOLDINGS_SCORE_CACHE_PATH, _cache_payload,
            commit_message=f"Dashboard holdings score cache — {scored_ok} of {n_symbols} scored",
        )
        if _ok:
            st.session_state.holdings_cache_timestamp = _cache_timestamp
        else:
            st.warning(f"Scored successfully, but couldn't save for next time: {_msg}")

    st.divider()

    # Results anchor (#76): scrolled into view below, only on the run
    # where "Score All Holdings" was actually just clicked -- not on
    # every later rerun (sorting, chat, etc.), so the user stays free to
    # scroll wherever they want after that.
    st.markdown('<div id="ms-scoring-results"></div>', unsafe_allow_html=True)

    # ── Build display dataframe ────────────────────────────────────────
    display_df = consolidated.copy()
    display_df['Score_Num'] = display_df['Symbol'].apply(
        lambda s: st.session_state.holding_scores.get(s, None)
    )
    display_df['Score_Num'] = display_df['Score_Num'].apply(
        lambda s: int(s) if s is not None and not (isinstance(s, float) and pd.isna(s)) else None
    )
    display_df['Badge']  = display_df['Score_Num'].apply(score_to_badge)
    display_df['Source'] = display_df['Symbol'].apply(
        lambda s: st.session_state.holding_sources.get(s, None)
    )
    display_df['Accounts_Label'] = display_df['Account_Count'].apply(
        lambda n: f"{n} acct{'s' if n > 1 else ''}"
    )

    # ── Signal (Hold/Add/Trim verdict) — materialized as a real column
    # (not just computed at render time) so it can be sorted on (#71).
    # Sorting the plain verdict string ascending naturally yields
    # Add < Hold < Trim < "—" (em dash sorts after letters), which is
    # exactly the "alphabetical, unscored last" order used as the default.
    ht = st.session_state.hold_thresholds
    def _signal_for(symbol):
        return hold_verdict(st.session_state.holding_raw_data.get(symbol), ht)
    _signals = display_df['Symbol'].apply(_signal_for)
    display_df['Signal']       = _signals.apply(lambda t: t[0])
    display_df['Signal_Color'] = _signals.apply(lambda t: t[1])
    display_df['Signal_Icon']  = _signals.apply(lambda t: t[2])

    st.subheader(f"{n_symbols} Unique Holdings — Consolidated Across All Accounts")

    # ── Superinvestor data load button (only if not already cached) ────
    if "_si_full_map" not in st.session_state:
        si_load_col1, si_load_col2 = st.columns([2, 5])
        with si_load_col1:
            if st.button("🦁 Load Superinvestor Conviction", use_container_width=True,
                         help="Fetches all 82 superinvestor portfolios from Dataroma (~30-60s, one-time per session)"):
                st.session_state["_si_full_map"] = get_conviction_data()
                st.rerun()
        with si_load_col2:
            st.caption("Optional — adds a Superinvestor Conviction column showing how many of 82 tracked value investors hold each position.")
        st.markdown("")

    # SI_Count materialized the same way as Signal, only once superinvestor
    # data is loaded, so it can be sorted on like any other column.
    si_data = st.session_state.get("_si_full_map", {})
    if si_data and si_data.get("ticker_map"):
        display_df['SI_Count'] = display_df['Symbol'].apply(
            lambda s: get_superinvestor_conviction(s).get("holder_count", 0)
        )
    else:
        display_df['SI_Count'] = None

    # ── Click-to-sort column headers (#71) — replaces the old "Sort by"
    # dropdown. Clicking a header sorts by that column; clicking the
    # active column again reverses direction, like a spreadsheet.
    SORT_COLUMNS = {
        "Symbol": {"field": "Symbol",        "label": "Symbol",  "default_asc": True},
        "Name":   {"field": "Name",          "label": "Name",    "default_asc": True},
        "Type":   {"field": "Product_Type",  "label": "Type",    "default_asc": True},
        "Value":  {"field": "Total_Value",   "label": "Value",   "default_asc": False},
        "Accts":  {"field": "Account_Count", "label": "Accts",   "default_asc": False},
        "Score":  {"field": "Score_Num",     "label": "Score",   "default_asc": False},
        "Signal": {"field": "Signal",        "label": "Signal",  "default_asc": True},
        "SI":     {"field": "SI_Count",      "label": "🦁 SI",   "default_asc": False},
    }
    if "holdings_sort_col" not in st.session_state:
        st.session_state.holdings_sort_col = "Signal"
        st.session_state.holdings_sort_asc = True

    st.caption("Click a column header to sort by it — click again to reverse the order.")

    def _sort_header(container, col_key):
        cfg    = SORT_COLUMNS[col_key]
        active = st.session_state.holdings_sort_col == col_key
        arrow  = ("▲" if st.session_state.holdings_sort_asc else "▼") if active else ""
        with container:
            if st.button(f"{cfg['label']} {arrow}".strip(), key=f"sort_hdr_{col_key}",
                         use_container_width=True, type="primary" if active else "secondary"):
                if active:
                    st.session_state.holdings_sort_asc = not st.session_state.holdings_sort_asc
                else:
                    st.session_state.holdings_sort_col = col_key
                    st.session_state.holdings_sort_asc = cfg["default_asc"]

    # ── Column Headers ─────────────────────────────────────────────────
    h1, h2, h3, h4, h5, h6, h7, h8, h9, h10 = st.columns([1.2, 2.6, 1.8, 1.4, 1.1, 1.3, 1.3, 1.3, 1.8, 0.9])
    _sort_header(h1, "Symbol")
    _sort_header(h2, "Name")
    _sort_header(h3, "Type")
    _sort_header(h4, "Value")
    _sort_header(h5, "Accts")
    _sort_header(h6, "Score")
    _sort_header(h7, "Signal")
    _sort_header(h8, "SI")
    with h9: st.markdown("**Analysis**")
    with h10: st.markdown("**Watch**")
    st.markdown("<hr style='margin:4px 0 8px 0'>", unsafe_allow_html=True)

    # ── Apply sort (after header buttons, so a click this run is reflected
    # immediately without needing a second rerun) ───────────────────────
    _active_cfg   = SORT_COLUMNS[st.session_state.holdings_sort_col]
    display_df = display_df.sort_values(
        _active_cfg["field"], ascending=st.session_state.holdings_sort_asc, na_position='last'
    )

    # ── Rows ───────────────────────────────────────────────────────────
    for _, row in display_df.iterrows():
        c1, c2, c3, c4, c5, c6, c7, c8, c9, c10 = st.columns([1.2, 2.6, 1.8, 1.4, 1.1, 1.3, 1.3, 1.3, 1.8, 0.9])
        with c1:
            st.markdown(f"**{row['Symbol']}**")
        with c2:
            st.caption(row['Name'])
        with c3:
            st.caption(row['Product_Type'])
        with c4:
            st.markdown(f"${row['Total_Value']:,.0f}")
        with c5:
            st.caption(row['Accounts_Label'])
        with c6:
            badge = row['Badge']
            if badge != "—":
                color = "#2ecc71" if badge.startswith("🟢") else "#f39c12" if badge.startswith("🟡") else "#e67e22" if badge.startswith("🟠") else "#e74c3c"
                st.markdown(f"<span style='font-weight:bold; color:{color}'>{badge}</span>", unsafe_allow_html=True)
                _row_data = st.session_state.holding_raw_data.get(row['Symbol'])
                if _row_data and _row_data.get("financial_subtype") in ("bank", "insurance"):
                    st.caption(f"🏦 {_row_data['financial_subtype'].title()} scoring")
            else:
                st.caption("—")
        with c7:
            # Signal/Signal_Color/Signal_Icon were materialized on
            # display_df above (same hold_verdict() call) so sorting and
            # rendering always agree -- no need to recompute here.
            if row['Signal'] != "—":
                st.markdown(
                    f"<span style='font-weight:bold; color:{row['Signal_Color']}'>{row['Signal_Icon']} {row['Signal']}</span>",
                    unsafe_allow_html=True
                )
            else:
                st.caption("—")
        with c8:
            si_data = st.session_state.get("_si_full_map", {})
            if si_data and si_data.get("ticker_map"):
                si_result   = get_superinvestor_conviction(row['Symbol'])
                si_n        = si_result.get("holder_count", 0)
                si_score    = si_result.get("conviction_score", 0)
                if si_n > 0:
                    si_color = "#2ecc71" if si_n >= 5 else "#f39c12" if si_n >= 2 else "#888"
                    st.markdown(
                        f"<span style='font-weight:bold; color:{si_color}'>🦁 {si_n}</span>",
                        unsafe_allow_html=True
                    )
                    st.caption(f"{si_score}/100")
                else:
                    st.caption("🦁 0")
            else:
                st.caption("—")
        with c9:
            if st.button("🔍 Deep Dive", key=f"dive_{row['Symbol']}", use_container_width=True, type="primary"):
                st.session_state["dive_ticker"] = row['Symbol']
                st.switch_page("app_pages/7_Equity_Scout_EDGAR.py")
        with c10:
            # Add-only control (#68) -- removal only happens on the
            # Watchlist page itself, deliberately, so an accidental
            # uncheck here can't silently wipe a tracked position's
            # buy/sell history. Checking it writes once (idempotent);
            # unchecking here does nothing.
            _already_watched = is_watchlisted(row['Symbol'])
            _seed_value  = row.get('Total_Value')
            _seed_shares = row.get('Total_Shares')
            _watch_checked = st.checkbox(
                "⭐", value=_already_watched, key=f"dash_watch_{row['Symbol']}",
                disabled=_already_watched,
                help=("On Watchlist" if _already_watched else
                      f"Add to Watchlist — seeds the Watch Portfolio with your current "
                      f"${_seed_value:,.0f} position as its editable starting point"),
            )
            if _watch_checked and not _already_watched:
                add_to_watchlist(
                    row['Symbol'], name=row.get('Name', row['Symbol']), source="Dashboard",
                    starting_shares=_seed_shares, starting_value=_seed_value,
                )
                st.rerun()

    st.caption("Foreign ADRs and companies without SEC EDGAR filings will show as unscored — see the summary message above for a count.")
    if run_scoring:
        scroll_to_element("ms-scoring-results")
    st.divider()

    # ── Ask Claude — Portfolio Analysis ───────────────────────────────
    st.markdown("### 🤖 Ask Claude — Portfolio Analysis")
    st.caption(
        "Claude reasons across your entire holdings using Buffett + Munger philosophy. "
        "Ask about concentration risk, sector exposure, portfolio resilience, or any specific holding."
    )

    # Build portfolio context from scored holdings
    def build_portfolio_context() -> str:
        lines = ["CURRENT HOLDINGS PORTFOLIO\n"]
        profile  = get_user_profile()
        _age     = profile.get('age', 57)
        _sage    = profile.get('spouse_age', '')
        _wd      = profile.get('monthly_withdrawal', 8000)
        _pv      = profile.get('portfolio_val', 3_790_000)
        _age_str = f"{_age}-year-old" + (f" and spouse age {_sage}" if _sage else "")
        lines.append(
            f"Household: {_age_str} | Portfolio: ${_pv/1e6:.1f}M | "
            f"Monthly target: ${_wd:,.0f} | Annual passive: ${profile.get('annual_passive', 0):,.0f}\n"
        )
        lines.append("Holdings (scored via Owner's Framework):")
        _df = display_df  # consolidated holdings dataframe
        for sym in unique_symbols:
            score  = st.session_state.holding_scores.get(sym)
            cached = st.session_state.holding_raw_data.get(sym)
            row    = _df[_df['Symbol'] == sym].iloc[0] if sym in _df['Symbol'].values else None
            if row is not None:
                val    = row['Total_Value'] if 'Total_Value' in row.index else 0
                badge  = row['Badge'] if 'Badge' in row.index else '—'
                pct    = val / profile.get('portfolio_val', 1) * 100 if profile.get('portfolio_val') else 0
                verdict, _, _ = hold_verdict(cached, ht) if cached else ('—', '', '')
                score_str = f"{score}/100" if score else "unscored"
                lines.append(
                    f"{sym} | Score: {score_str} | Value: ${val:,.0f} ({pct:.1f}%) | "
                    f"Signal: {verdict} | Badge: {badge}"
                )
                if cached:
                    def fv(v, t='pct'):
                        if v is None: return 'N/A'
                        return f"{v:.1%}" if t=='pct' else f"{v:.1f}x" if t=='ratio' else str(v)
                    lines.append(
                        f"  FCF Yield: {fv(cached.get('fcf_yield'))} | "
                        f"ROIC (10yr avg, cash basis): {fv(cached.get('roic_10yr_avg'))} | "
                        f"Debt/FCF: {fv(cached.get('debt_to_fcf'),'ratio')} | "
                        f"Gross Margin: {fv(cached.get('gross_margin'))} | "
                        f"P/OE: {fv(cached.get('price_owner_earn'),'ratio')} | "
                        f"Sector: {cached.get('sector','N/A')}"
                    )
        return "\n".join(lines)

    dash_convo_key   = "dash_claude_convo"
    dash_context_key = "dash_claude_context_sent"
    if dash_convo_key not in st.session_state:
        st.session_state[dash_convo_key]   = []
        st.session_state[dash_context_key] = False

    # Display conversation history
    for msg in st.session_state[dash_convo_key]:
        role    = msg["role"]
        display = msg["content"]
        if role == "user" and "\n---\nQUESTION: " in display:
            display = display.split("\n---\nQUESTION: ", 1)[-1]
        with st.chat_message(role, avatar="🤖" if role == "assistant" else None):
            st.markdown(display)

    # Starter questions
    if not st.session_state[dash_convo_key]:
        st.markdown("**Suggested questions:**")
        dq_cols = st.columns(2)
        dash_starters = [
            "Which holding is most vulnerable if rates stay elevated and growth slows?",
            "Apply Munger's inversion — what could permanently destroy value here?",
            "Where is my biggest concentration risk and what should I do about it?",
            "Which holding has the strongest Buffett/Munger moat and why?",
        ]
        for i, q in enumerate(dash_starters):
            with dq_cols[i % 2]:
                if st.button(q, key=f"dash_starter_{i}", use_container_width=True):
                    st.session_state["dash_pending_q"] = q
                    st.rerun()

    dash_pending_q = st.session_state.pop("dash_pending_q", None)
    dash_user_q    = st.chat_input("Ask Claude about your portfolio...", key="dash_claude_input")
    dash_active_q  = dash_pending_q or dash_user_q

    if dash_active_q:
        portfolio_ctx = build_portfolio_context()
        full_q        = f"{portfolio_ctx}\n\n---\nQUESTION: {dash_active_q}"

        with st.chat_message("user"):
            st.markdown(dash_active_q)

        with st.chat_message("assistant", avatar="🤖"):
            with st.spinner("Analyzing your portfolio..."):
                if not st.session_state[dash_context_key]:
                    response = ask_claude_about_equity(
                        ticker="PORTFOLIO", data={}, scores={}, sections={},
                        user_question=full_q,
                        conversation_history=None,
                    )
                    st.session_state[dash_convo_key].append({"role": "user",    "content": full_q})
                    st.session_state[dash_context_key] = True
                else:
                    response = ask_claude_about_equity(
                        ticker="PORTFOLIO", data={}, scores={}, sections={},
                        user_question=dash_active_q,
                        conversation_history=st.session_state[dash_convo_key],
                    )
                    st.session_state[dash_convo_key].append({"role": "user", "content": dash_active_q})

                st.session_state[dash_convo_key].append({"role": "assistant", "content": response})
                st.markdown(response)

    if st.session_state[dash_convo_key]:
        if st.button("🗑️ Clear conversation", key="dash_clear_convo"):
            st.session_state[dash_convo_key]   = []
            st.session_state[dash_context_key] = False
            st.rerun()

    st.divider()

    # ── Account Breakdown ──────────────────────────────────────────────
    st.subheader("🏦 Account Breakdown")
    st.caption("Select a holding to see how its value is distributed across your accounts.")
    selected_symbol = st.selectbox(
        "Select a holding", options=[""] + unique_symbols,
        format_func=lambda x: x if x else "— choose a symbol —"
    )
    if selected_symbol:
        account_detail = (
            df_holdings_raw[df_holdings_raw['Symbol'] == selected_symbol]
            [['Account Number', 'Name', 'Market Value ($)', 'Est. Annual Income ($)']]
            .copy().sort_values('Market Value ($)', ascending=False)
        )
        total_holding_val = account_detail['Market Value ($)'].sum()
        st.markdown(f"**{selected_symbol}** — Total Value: **${total_holding_val:,.2f}**")
        score  = st.session_state.holding_scores.get(selected_symbol)
        if score is not None:
            st.markdown(f"Conviction Score: {score_to_badge(score)} (via SEC EDGAR)")
        account_detail['% of Position'] = (
            account_detail['Market Value ($)'] / total_holding_val * 100
        ).round(1).astype(str) + '%'
        st.dataframe(account_detail, hide_index=True, use_container_width=True)
        if st.button(
            "🔍 Open Full Analysis in Equity Scout",
            key=f"account_dive_{selected_symbol}",
            type="primary"
        ):
            st.session_state["dive_ticker"] = selected_symbol
            st.switch_page("app_pages/7_Equity_Scout_EDGAR.py")

