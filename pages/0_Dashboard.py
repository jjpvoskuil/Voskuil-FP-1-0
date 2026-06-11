import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import time
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from claude_utils import ask_claude_about_equity, get_user_profile, build_context

st.set_page_config(page_title="Voskuil FP 1.0", layout="wide")
st.title("🛡️ Voskuil FP 1.0: Sovereign Wealth Dashboard")

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────
HOLDINGS_FILE  = 'ms_holdings.csv'
TAX_FILE       = 'ms_realized_gl_current.csv'
TAX_FILE_PRIOR = 'ms_realized_gl_prior.csv'
TRANS_FILE_YTD   = 'ms_transactions_ytd.csv'
TRANS_FILE_PRIOR = 'ms_transactions_prior.csv'
APP_URL   = "https://voskuil-fp-1-0-k85bd7afbw8dnqeftzxwbu.streamlit.app"
POLY_URL  = "https://api.polygon.io"

DEFAULT_WEIGHTS = {
    "FCF Yield":              20,
    "ROIC":                   10,
    "Debt / FCF":             20,
    "Gross Margin":           15,
    "Interest Coverage":      10,
    "Price / Owner Earnings": 25,
}

DEFAULT_HOLD_THRESHOLDS = {
    "min_roic":      0.12,
    "max_debt_fcf":  5.0,
    "max_poe":       25.0,
    "min_fcf_yield": 0.03,
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
# MS DATA FRESHNESS
# ─────────────────────────────────────────────
def get_ms_data_freshness() -> str:
    try:
        token = st.secrets.get("GITHUB_TOKEN", "")
        repo  = st.secrets.get("GITHUB_REPO",  "jjpvoskuil/Voskuil-FP-1-0")
        if not token:
            return "unknown"
        url = f"https://api.github.com/repos/{repo}/commits?path=ms_holdings.csv&per_page=1"
        headers = {
            "Authorization": f"token {token}",
            "Accept":        "application/vnd.github+json",
        }
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            commits = r.json()
            if commits:
                return commits[0]["commit"]["committer"]["date"][:10]
        return "never"
    except Exception:
        return "unknown"

# ─────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 📂 MS Data")
    last_updated = get_ms_data_freshness()
    if last_updated not in ("unknown", "never"):
        st.caption(f"📅 Last updated: **{last_updated}**")
    elif last_updated == "never":
        st.caption("📅 No data uploaded yet")
    else:
        st.caption("📅 Last updated: unknown")
    st.info(
        "**To refresh MS data:**\n\n"
        "1. Log into MS Online\n"
        "2. Download these 5 files:\n"
        "   - Holdings\n"
        "   - Activity → Current Year\n"
        "   - Activity → Prior Year\n"
        "   - Realized G/L → Current Year\n"
        "   - Realized G/L → Prior Year\n"
        "3. Double-click **run_push.bat** on your Desktop\n"
        "4. Reload this page",
        icon="💡"
    )
    st.divider()

# ─────────────────────────────────────────────
# POLYGON HELPERS
# ─────────────────────────────────────────────
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

# ─────────────────────────────────────────────
# YFINANCE FALLBACK
# ─────────────────────────────────────────────
def fetch_score_data_yfinance(ticker):
    try:
        import yfinance as yf
        stock = yf.Ticker(ticker)
        info  = stock.info
        market_cap = safe_float(info.get('marketCap'))
        price      = safe_float(info.get('currentPrice') or info.get('regularMarketPrice'))
        cashflow   = stock.cashflow
        financials = stock.financials
        balance    = stock.balance_sheet
        op_cf  = safe_float(cashflow.loc['Operating Cash Flow'].iloc[0]) if 'Operating Cash Flow' in cashflow.index else None
        capex  = safe_float(cashflow.loc['Capital Expenditure'].iloc[0]) if 'Capital Expenditure' in cashflow.index else None
        fcf    = (op_cf + capex) if (op_cf is not None and capex is not None) else None
        if fcf is None or fcf <= 0:
            return None
        fcf_yield    = (fcf / market_cap) if (market_cap and market_cap > 0) else None
        gross_margin = safe_float(info.get('grossMargins'))
        net_income   = safe_float(financials.loc['Net Income'].iloc[0]) if 'Net Income' in financials.index else None
        total_assets = safe_float(balance.loc['Total Assets'].iloc[0]) if 'Total Assets' in balance.index else None
        current_liab = safe_float(balance.loc['Current Liabilities'].iloc[0]) if 'Current Liabilities' in balance.index else None
        invested_cap = (total_assets - current_liab) if (total_assets and current_liab) else None
        roic         = (net_income / invested_cap) if (net_income and invested_cap and invested_cap != 0) else None
        total_debt   = safe_float(balance.loc['Total Debt'].iloc[0]) if 'Total Debt' in balance.index else None
        debt_to_fcf  = (total_debt / fcf) if (total_debt is not None and fcf > 0) else None
        ebit         = safe_float(financials.loc['EBIT'].iloc[0]) if 'EBIT' in financials.index else None
        int_exp      = abs(safe_float(financials.loc['Interest Expense'].iloc[0])) if 'Interest Expense' in financials.index else None
        interest_cov = (ebit / int_exp) if (ebit and int_exp and int_exp > 0) else None
        shares       = safe_float(info.get('sharesOutstanding'))
        dna          = safe_float(cashflow.loc['Depreciation And Amortization'].iloc[0]) if 'Depreciation And Amortization' in cashflow.index else None
        capex_abs    = abs(capex) if capex else 0
        owner_earn   = (net_income + (dna or 0) - capex_abs) if net_income is not None else None
        poe          = (price / (owner_earn / shares)) if (owner_earn and owner_earn > 0 and shares and price) else None
        div_yield    = safe_float(info.get('dividendYield'))
        return {
            "fcf_yield":         fcf_yield,
            "roic":              roic,
            "debt_to_fcf":       debt_to_fcf,
            "interest_coverage": interest_cov,
            "is_net_creditor":   False,
            "gross_margin":      gross_margin,
            "price_owner_earn":  poe,
            "dividend_yield":    div_yield,
            "source":            "yfinance",
        }
    except Exception:
        return None

# ─────────────────────────────────────────────
# PRIMARY POLYGON FETCHER
# ─────────────────────────────────────────────
def fetch_score_data(ticker):
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
        fin_data = poly_get("/vX/reference/financials", {
            "ticker": ticker, "timeframe": "annual", "limit": 1,
            "order": "desc", "sort": "period_of_report_date",
        })
        if not fin_data or not fin_data.get("results"):
            return fetch_score_data_yfinance(ticker)
        f   = fin_data["results"][0]["financials"]
        inc = f.get("income_statement",    {})
        cf  = f.get("cash_flow_statement", {})
        bs  = f.get("balance_sheet",       {})
        op_cf  = fval(cf, "net_cash_flow_from_operating_activities")
        inv_cf = fval(cf, "net_cash_flow_from_investing_activities")
        fcf    = (op_cf + inv_cf) if (op_cf is not None and inv_cf is not None) else None
        if fcf is None or fcf <= 0:
            return fetch_score_data_yfinance(ticker)
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
            "fcf_yield":         fcf_yield,
            "roic":              roic,
            "debt_to_fcf":       debt_to_fcf,
            "interest_coverage": interest_cov,
            "is_net_creditor":   is_net_creditor,
            "gross_margin":      gross_margin,
            "price_owner_earn":  poe,
            "dividend_yield":    div_yield,
            "source":            "polygon",
        }
    except Exception:
        return fetch_score_data_yfinance(ticker)

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
    """Returns (verdict, color, icon) based on Buffett hold/add/trim logic."""
    if data is None:
        return "—", "#888888", ""
    roic      = data.get("roic")
    debt_fcf  = data.get("debt_to_fcf")
    poe       = data.get("price_owner_earn")
    fcf_yield = data.get("fcf_yield")
    quality_ok = (
        (roic     is None or roic     >= thresholds["min_roic"]) and
        (debt_fcf is None or debt_fcf <= thresholds["max_debt_fcf"])
    )
    value_ok = (
        (poe       is None or poe       <= thresholds["max_poe"]) and
        (fcf_yield is None or fcf_yield >= thresholds["min_fcf_yield"])
    )
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
    for col in ['Market Value ($)', 'Est. Annual Income ($)']:
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
    if 'hold_thresholds'    not in st.session_state: st.session_state.hold_thresholds    = DEFAULT_HOLD_THRESHOLDS.copy()
    st.session_state.holding_weights = st.session_state.committed_weights

    # ── Weight reset handler ───────────────────────────────────────────
    _weight_map = [("w_fcf","FCF Yield"),("w_roic","ROIC"),("w_debt","Debt / FCF"),
                   ("w_gm","Gross Margin"),("w_ic","Interest Coverage"),("w_poe","Price / Owner Earnings")]
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
        st.caption("Adjust freely — scoring uses the last **Applied** set.")
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
            "Price / Owner Earnings": st.session_state.get("w_poe",  sw["Price / Owner Earnings"]),
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
            f"GM {cw['Gross Margin']} · IC {cw['Interest Coverage']} · P/OE {cw['Price / Owner Earnings']}"
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
            with _sc: w_roic = st.slider("ROIC", 0, 40, sw["ROIC"], step=5, key="w_roic")
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
            _sc, _sb = st.columns([4, 1])
            with _sc: w_poe = st.slider("Price / Owner Earnings", 0, 60, sw["Price / Owner Earnings"], step=5, key="w_poe")
            with _sb:
                st.write("")
                if st.button(f"↺ {DEFAULT_WEIGHTS['Price / Owner Earnings']}", key="reset_w_poe", use_container_width=True):
                    st.session_state["pending_reset_w_poe"] = True; st.rerun()

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
            help="Weights must add up to 100." if total_weight != 100 else "Score using Polygon (with yfinance fallback)."
        )
    with info_col:
        scored_count = len(st.session_state.holding_scores)
        if scored_count > 0:
            poly_count = sum(1 for s in st.session_state.holding_sources.values() if s == "polygon")
            yf_count   = sum(1 for s in st.session_state.holding_sources.values() if s == "yfinance")
            msg = f"✅ {scored_count} holdings scored"
            if yf_count > 0:
                msg += f" — {poly_count} via Polygon, {yf_count} via yfinance"
            st.success(msg)
        else:
            st.caption("Scores not yet loaded. Click the button above.")

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
                scores[symbol]          = score_stock(data, active_weights)
                sources[symbol]         = data.get("source", "polygon")
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
        yf_ok     = sum(1 for s in sources.values() if s == "yfinance")
        status_text.markdown(
            f"✅ Done — {scored_ok} of {n_symbols} scored "
            f"({yf_ok} via yfinance fallback)."
        )

    st.divider()

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

    # ── Sort Controls ──────────────────────────────────────────────────
    st.subheader(f"{n_symbols} Unique Holdings — Consolidated Across All Accounts")
    sort_col, _ = st.columns([3, 1])
    with sort_col:
        sort_by = st.selectbox(
            "Sort by",
            options=["Total Value (High→Low)", "Total Value (Low→High)",
                     "Score (High→Low)", "Score (Low→High)",
                     "Symbol (A→Z)", "Symbol (Z→A)"],
            label_visibility="collapsed"
        )
    if sort_by == "Total Value (High→Low)":
        display_df = display_df.sort_values('Total_Value', ascending=False)
    elif sort_by == "Total Value (Low→High)":
        display_df = display_df.sort_values('Total_Value', ascending=True)
    elif sort_by == "Score (High→Low)":
        display_df = display_df.sort_values('Score_Num', ascending=False, na_position='last')
    elif sort_by == "Score (Low→High)":
        display_df = display_df.sort_values('Score_Num', ascending=True, na_position='last')
    elif sort_by == "Symbol (A→Z)":
        display_df = display_df.sort_values('Symbol', ascending=True)
    elif sort_by == "Symbol (Z→A)":
        display_df = display_df.sort_values('Symbol', ascending=False)

    # ── Column Headers ─────────────────────────────────────────────────
    h1, h2, h3, h4, h5, h6, h7, h8 = st.columns([1.2, 3, 2, 1.5, 1.2, 1.5, 1.5, 2])
    with h1: st.markdown("**Symbol**")
    with h2: st.markdown("**Name**")
    with h3: st.markdown("**Type**")
    with h4: st.markdown("**Value**")
    with h5: st.markdown("**Accts**")
    with h6: st.markdown("**Score**")
    with h7: st.markdown("**Signal**")
    with h8: st.markdown("**Analysis**")
    st.markdown("<hr style='margin:4px 0 8px 0'>", unsafe_allow_html=True)

    # ── Rows ───────────────────────────────────────────────────────────
    ht = st.session_state.hold_thresholds
    for _, row in display_df.iterrows():
        c1, c2, c3, c4, c5, c6, c7, c8 = st.columns([1.2, 3, 2, 1.5, 1.2, 1.5, 1.5, 2])
        with c1:
            src = row.get('Source')
            sym_label = f"**{row['Symbol']}**"
            if src == "yfinance":
                sym_label += " 🌐"
            st.markdown(sym_label)
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
            else:
                st.caption("—")
        with c7:
            cached = st.session_state.holding_raw_data.get(row['Symbol'])
            verdict, vcolor, vicon = hold_verdict(cached, ht)
            if verdict != "—":
                st.markdown(
                    f"<span style='font-weight:bold; color:{vcolor}'>{vicon} {verdict}</span>",
                    unsafe_allow_html=True
                )
            else:
                st.caption("—")
        with c8:
            if st.button("🔍 Deep Dive", key=f"dive_{row['Symbol']}", use_container_width=True, type="primary"):
                st.session_state["dive_ticker"] = row['Symbol']
                st.switch_page("pages/1_Equity_Scout.py")

    st.caption("🌐 = scored via yfinance fallback (foreign ADR — not in SEC database)")
    st.divider()

    # ── Ask Claude — Portfolio Analysis ───────────────────────────────
    st.markdown("### 🤖 Ask Claude — Portfolio Analysis")
    st.caption(
        "Claude reasons across your entire holdings using Buffett + Munger philosophy. "
        "Ask about concentration risk, sector exposure, Long Squeeze resilience, or any specific holding."
    )

    # Build portfolio context from scored holdings
    def build_portfolio_context() -> str:
        lines = ["CURRENT HOLDINGS PORTFOLIO\n"]
        profile = get_user_profile()
        lines.append(
            f"Portfolio value: ${profile.get('portfolio_val', 0):,.0f} | "
            f"Monthly target: ${profile.get('monthly_withdrawal', 0):,.0f} | "
            f"Annual passive: ${profile.get('annual_passive', 0):,.0f}\n"
        )
        lines.append("Holdings (scored via Owner's Framework):")
        for sym in unique_symbols:
            score  = st.session_state.holding_scores.get(sym)
            cached = st.session_state.holding_raw_data.get(sym)
            row    = df_display[df_display['Symbol'] == sym].iloc[0] if sym in df_display['Symbol'].values else None
            if row is not None:
                val    = row.get('Total_Value', 0)
                badge  = row.get('Badge', '—')
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
                        f"ROIC: {fv(cached.get('roic'))} | "
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
            "Which holding is most vulnerable in a Long Squeeze environment?",
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
        source = st.session_state.holding_sources.get(selected_symbol)
        if score is not None:
            src_label = " (via yfinance — foreign ADR)" if source == "yfinance" else " (via Polygon)"
            st.markdown(f"Conviction Score: {score_to_badge(score)}{src_label}")
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
            st.switch_page("pages/1_Equity_Scout.py")
