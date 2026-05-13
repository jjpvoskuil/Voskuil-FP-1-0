import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import time

st.set_page_config(page_title="Voskuil FP 1.0", layout="wide")
st.title("🛡️ Voskuil FP 1.0: Sovereign Wealth Dashboard")

HOLDINGS_FILE = 'Current MS holdings - 042526.csv'
TAX_FILE      = 'Realized GL 042626.csv'
TRANS_FILE    = 'Transaction History 042626.csv'
APP_URL       = "https://voskuil-fp-1-0-k85bd7afbw8dnqeftzxwbu.streamlit.app"
POLY_URL      = "https://api.polygon.io"

DEFAULT_WEIGHTS = {
    "FCF Yield":              20,
    "ROIC":                   10,
    "Debt / FCF":             20,
    "Gross Margin":           15,
    "Interest Coverage":      10,
    "Price / Owner Earnings": 25,
}

# Fund scoring uses different metrics — funds are wrappers, not businesses.
# Philosophy: cost discipline, income generation, compounding, drawdown safety, concentration risk.
DEFAULT_FUND_WEIGHTS = {
    "Expense Ratio":      25,   # The one guaranteed drag — fees compound against you in a Long Squeeze
    "Distribution Yield": 25,   # Income toward the $8K/month goal — same thresholds as stock FCF Yield
    "3-Year Return":      20,   # Does this fund actually compound?
    "Volatility (Beta)":  15,   # Retirement drawdown risk — forced selling kills a withdrawal portfolio
    "Concentration Risk": 15,   # Passive Ponzi flag — top-10 holdings > 50% = index bubble exposure
}

FUND_THRESHOLDS = {
    "expense_ratio_great":    0.0010,   # ≤ 0.10% — elite (Vanguard/Fidelity index tier)
    "expense_ratio_good":     0.0050,   # ≤ 0.50% — acceptable
    "expense_ratio_warn":     0.0100,   # ≤ 1.00% — expensive
    "dist_yield_great":       0.06,     # ≥ 6% — strong income (mirrors stock FCF Yield great)
    "dist_yield_good":        0.04,     # ≥ 4% — solid income
    "return_3yr_great":       0.12,     # ≥ 12% annualized — strong compounder
    "return_3yr_good":        0.07,     # ≥ 7% annualized — decent
    "beta_safe":              0.80,     # ≤ 0.80 — low drawdown risk
    "beta_moderate":          1.10,     # ≤ 1.10 — moderate risk
    "concentration_safe":     0.35,     # top-10 weight ≤ 35% — well diversified
    "concentration_warn":     0.50,     # top-10 weight ≤ 50% — getting concentrated
}

# Product Type strings from Morgan Stanley CSV that identify funds vs individual securities
FUND_PRODUCT_TYPES = {
    "mutual fund", "etf", "exchange traded fund", "exchange-traded fund",
    "money market", "money market fund", "closed-end fund", "unit investment trust",
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
# POLYGON FETCHER
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
# YFINANCE FALLBACK (for foreign ADRs)
# ─────────────────────────────────────────────
def fetch_score_data_yfinance(ticker):
    """Fallback for foreign ADRs not in Polygon SEC database."""
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
        is_net_creditor = False

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
            "is_net_creditor":   is_net_creditor,
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
    """Try Polygon first; fall back to yfinance for foreign ADRs."""
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

        # No SEC filings found — fall back to yfinance
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

# ─────────────────────────────────────────────
# FUND DETECTION
# ─────────────────────────────────────────────
# Exact matches (lowercase) for known MS product type strings
FUND_PRODUCT_TYPES = {
    "mutual fund", "mutual funds",
    "etf", "etfs",
    "exchange traded fund", "exchange traded funds",
    "exchange-traded fund", "exchange-traded funds",
    "money market", "money market fund", "money market funds",
    "closed-end fund", "closed-end funds",
    "unit investment trust",
    "529", "529 plan",
    "annuity",
}

# Keyword fragments — if any of these appear anywhere in the product type string it's a fund
FUND_KEYWORDS = ("fund", "etf", "money market", "annuity", "trust", "529")

def is_fund(product_type: str) -> bool:
    """True if the Product Type from the MS CSV identifies this holding as a fund/ETF.
    Uses exact-match set first, then keyword fallback for unexpected MS label variants."""
    pt = str(product_type).strip().lower()
    if pt in FUND_PRODUCT_TYPES:
        return True
    return any(kw in pt for kw in FUND_KEYWORDS)


# ─────────────────────────────────────────────
# FUND DATA FETCHER (yfinance)
# ─────────────────────────────────────────────
def fetch_score_data_fund(ticker):
    """Fetch fund-specific metrics via yfinance for ETFs and mutual funds.
    Returns a dict in all cases (never bare None) so the scoring loop can
    surface debug info when data is missing rather than silently showing '—'.
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info

        # ── Expense Ratio ────────────────────────────────────────────────
        expense_ratio = safe_float(info.get('expenseRatio'))

        # ── Distribution / Dividend Yield ────────────────────────────────
        # yfinance uses different keys by fund type and library version.
        raw_yield = (
            info.get('yield')
            or info.get('dividendYield')
            or info.get('trailingAnnualDividendYield')
        )
        dist_yield = safe_float(raw_yield)
        # Normalise: sometimes returned as decimal (0.04), sometimes percent (4.0)
        if dist_yield is not None and dist_yield > 1.0:
            dist_yield = dist_yield / 100.0

        # ── 3-Year / 5-Year Return ───────────────────────────────────────
        return_3yr = safe_float(info.get('threeYearAverageReturn'))
        if return_3yr is None:
            return_3yr = safe_float(info.get('fiveYearAverageReturn'))   # acceptable proxy
        if return_3yr is not None and return_3yr > 1.0:
            return_3yr = return_3yr / 100.0

        # ── Beta ─────────────────────────────────────────────────────────
        beta = safe_float(info.get('beta3Year') or info.get('beta'))

        # ── Concentration ─────────────────────────────────────────────────
        concentration = None   # yfinance doesn't expose holdings weights yet

        # ── Debug payload — always captured ─────────────────────────────
        yield_key_used = (
            "yield" if info.get('yield') else
            "dividendYield" if info.get('dividendYield') else
            "trailingAnnualDividendYield" if info.get('trailingAnnualDividendYield') else
            "none"
        )
        debug = {
            "quoteType":     info.get('quoteType', '?'),
            "expense_ratio": expense_ratio,
            "dist_yield":    dist_yield,
            "return_3yr":    return_3yr,
            "beta":          beta,
            "yield_key":     yield_key_used,
        }

        if all(v is None for v in [expense_ratio, dist_yield, return_3yr, beta]):
            return {"source": "fund_no_data", "debug": debug}

        return {
            "expense_ratio":  expense_ratio,
            "dist_yield":     dist_yield,
            "return_3yr":     return_3yr,
            "beta":           beta,
            "concentration":  concentration,
            "source":         "fund",
            "debug":          debug,
        }
    except Exception as e:
        return {"source": "fund_error", "error": str(e)}


# ─────────────────────────────────────────────
# FUND SCORING ENGINE
# ─────────────────────────────────────────────
def score_fund(data, fund_weights):
    """
    Score a fund 0-100 using retirement-focused metrics.
    Missing metrics are rebalanced proportionally (same pattern as stock scorer).
    Returns (raw_score, rebalanced_score).
    """
    pts          = 0
    missing_pts  = 0

    # ── Expense Ratio ──────────────────────────────────────────────────────
    w   = fund_weights["Expense Ratio"]
    exp = data.get('expense_ratio')
    if exp is not None:
        if exp <= FUND_THRESHOLDS['expense_ratio_great']:   pts += w
        elif exp <= FUND_THRESHOLDS['expense_ratio_good']:  pts += round(w * 0.65)
        elif exp <= FUND_THRESHOLDS['expense_ratio_warn']:  pts += round(w * 0.25)
        # else: > 1% → 0 pts
    else:
        missing_pts += w

    # ── Distribution Yield ────────────────────────────────────────────────
    w   = fund_weights["Distribution Yield"]
    dy  = data.get('dist_yield')
    if dy is not None:
        if dy >= FUND_THRESHOLDS['dist_yield_great']:   pts += w
        elif dy >= FUND_THRESHOLDS['dist_yield_good']:  pts += round(w * 0.60)
        elif dy > 0:                                    pts += round(w * 0.20)
        # else: 0 yield → 0 pts
    else:
        missing_pts += w

    # ── 3-Year Trailing Return ────────────────────────────────────────────
    w   = fund_weights["3-Year Return"]
    r3  = data.get('return_3yr')
    if r3 is not None:
        if r3 >= FUND_THRESHOLDS['return_3yr_great']:   pts += w
        elif r3 >= FUND_THRESHOLDS['return_3yr_good']:  pts += round(w * 0.60)
        elif r3 > 0:                                    pts += round(w * 0.20)
        # else: negative → 0 pts
    else:
        missing_pts += w

    # ── Volatility (Beta) ─────────────────────────────────────────────────
    w    = fund_weights["Volatility (Beta)"]
    beta = data.get('beta')
    if beta is not None:
        if beta <= FUND_THRESHOLDS['beta_safe']:      pts += w
        elif beta <= FUND_THRESHOLDS['beta_moderate']: pts += round(w * 0.55)
        else:                                          pts += round(w * 0.15)
    else:
        missing_pts += w

    # ── Concentration Risk ────────────────────────────────────────────────
    w    = fund_weights["Concentration Risk"]
    conc = data.get('concentration')
    if conc is not None:
        if conc <= FUND_THRESHOLDS['concentration_safe']:   pts += w
        elif conc <= FUND_THRESHOLDS['concentration_warn']: pts += round(w * 0.50)
        # else: > 50% → 0 pts (Passive Ponzi flag)
    else:
        missing_pts += w   # yfinance doesn't expose this yet — always missing for now

    raw_score      = pts
    available_pts  = 100 - missing_pts
    rebalanced     = round(raw_score / available_pts * 100) if available_pts > 0 else raw_score
    return raw_score, rebalanced


def fund_score_to_badge(rebalanced_score):
    """Same colour bands as stocks but with 📊 icon so funds are visually distinct."""
    try:
        if rebalanced_score is None or (isinstance(rebalanced_score, float) and pd.isna(rebalanced_score)):
            return "—"
        s = int(rebalanced_score)
        if s >= 80:   return f"📊🟢 {s}"
        elif s >= 65: return f"📊🟡 {s}"
        elif s >= 45: return f"📊🟠 {s}"
        else:         return f"📊🔴 {s}"
    except Exception:
        return "—"


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
total_val = 0.0
total_income = 0.0
ira_gain_total = 0.0
taxable_gain_total = 0.0
ytd_dividends = 0.0
ytd_interest = 0.0
product_mix = pd.DataFrame()
df_holdings_raw = None

df_holdings_raw = get_clean_df(HOLDINGS_FILE, "Account Number")
if df_holdings_raw is not None:
    df_holdings_raw.columns = [c.strip() for c in df_holdings_raw.columns]
    df_holdings_raw = df_holdings_raw[~df_holdings_raw.iloc[:, 0].astype(str).str.contains('Total', case=False, na=False)]
    for col in ['Market Value ($)', 'Est. Annual Income ($)']:
        if col in df_holdings_raw.columns:
            df_holdings_raw[col] = pd.to_numeric(df_holdings_raw[col].astype(str).str.replace(',', '').str.replace('"', ''), errors='coerce')
    total_val = df_holdings_raw['Market Value ($)'].sum()
    total_income = df_holdings_raw['Est. Annual Income ($)'].sum()
    product_mix = df_holdings_raw.groupby('Product Type')['Market Value ($)'].sum().reset_index()
    product_mix = product_mix.sort_values(by='Market Value ($)', ascending=False)
    color_palette = px.colors.qualitative.Prism
    product_mix['color'] = [color_palette[i % len(color_palette)] for i in range(len(product_mix))]
    df_holdings_raw = df_holdings_raw.dropna(subset=['Symbol'])

df_tax = get_clean_df(TAX_FILE, "Account Number")
if df_tax is not None:
    df_tax.columns = [c.strip() for c in df_tax.columns]
    df_tax_clean = df_tax[~df_tax.iloc[:, 0].astype(str).str.contains('Total', case=False, na=False)]
    df_tax_clean['Numeric Gain'] = pd.to_numeric(df_tax_clean.iloc[:, 13].astype(str).str.replace(',', '').str.replace('"', ''), errors='coerce')
    ira_mask = df_tax_clean.iloc[:, 0].astype(str).str.contains('IRA', case=False, na=False)
    ira_gain_total = df_tax_clean[ira_mask]['Numeric Gain'].sum()
    taxable_gain_total = df_tax_clean[~ira_mask]['Numeric Gain'].sum()

df_trans = get_clean_df(TRANS_FILE, "Activity Date")
if df_trans is not None:
    df_trans.columns = [c.strip() for c in df_trans.columns]
    df_trans['Amount($)'] = pd.to_numeric(df_trans['Amount($)'].astype(str).str.replace(',', '').str.replace('"', ''), errors='coerce')
    ytd_dividends = df_trans[df_trans['Activity'].str.contains('Dividend', na=False, case=False)]['Amount($)'].sum()
    ytd_interest  = df_trans[df_trans['Activity'].str.contains('Interest',  na=False, case=False)]['Amount($)'].sum()

# ─────────────────────────────────────────────
# POWER BAR
# ─────────────────────────────────────────────
col1, col2, col3, col4, col5 = st.columns(5)
with col1: st.metric("Total Market Value", f"${total_val:,.2f}")
with col2: st.metric("Taxable G/L (YTD)",  f"${taxable_gain_total:,.2f}", help="Gains from non-IRA accounts.")
with col3: st.metric("IRA G/L (YTD)",      f"${ira_gain_total:,.2f}",     help="Tax-deferred growth in IRA buckets.")
with col4: st.metric("YTD Dividends",      f"${ytd_dividends:,.2f}")
with col5: st.metric("YTD Interest",       f"${ytd_interest:,.2f}")
st.divider()

# ─────────────────────────────────────────────
# ASSET ALLOCATION
# ─────────────────────────────────────────────
st.subheader("Institutional Asset Allocation")
c1, c2, c3 = st.columns([3, 4, 5])
with c1:
    if not product_mix.empty:
        fig = px.pie(product_mix, values='Market Value ($)', names='Product Type', hole=0.4, color='Product Type',
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

st.markdown("""
<style>
div[data-testid="stLinkButton"] a[href*="sec.gov"] {
    background-color: #27ae60 !important;
    color: white !important;
    border-color: #27ae60 !important;
}
div[data-testid="stLinkButton"] a[href*="yahoo.com"] {
    background-color: #8e44ad !important;
    color: white !important;
    border-color: #8e44ad !important;
}
div[data-testid="stLinkButton"] a[href*="equity_scout"] {
    background-color: #1f6feb !important;
    color: white !important;
    border-color: #1f6feb !important;
}
</style>
""", unsafe_allow_html=True)

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

    def get_sec_link(symbol):
        cik = cik_map.get(symbol)
        return f"https://www.sec.gov/edgar/browse/?CIK={cik}&owner=exclude" if cik else f"https://www.sec.gov/cgi-bin/browse-edgar?CIK={symbol}"

    consolidated['SEC Link']   = consolidated['Symbol'].apply(get_sec_link)
    consolidated['Yahoo Link'] = consolidated['Symbol'].apply(lambda x: f"https://finance.yahoo.com/quote/{x}")
    consolidated['Dive Link']  = consolidated['Symbol'].apply(lambda s: f"{APP_URL}/equity_scout?ticker={s}&auto=1")

    if 'holding_scores'       not in st.session_state: st.session_state.holding_scores       = {}
    if 'holding_weights'      not in st.session_state: st.session_state.holding_weights      = DEFAULT_WEIGHTS.copy()
    if 'holding_sources'      not in st.session_state: st.session_state.holding_sources      = {}
    if 'holding_fund_weights' not in st.session_state: st.session_state.holding_fund_weights = DEFAULT_FUND_WEIGHTS.copy()
    if 'holding_fund_scores'  not in st.session_state: st.session_state.holding_fund_scores  = {}
    if 'holding_fund_debug'   not in st.session_state: st.session_state.holding_fund_debug   = {}

    # ── Weight Customizer ──────────────────────────────────────────────────
    with st.expander("⚙️ Scoring Weights", expanded=False):
        stock_tab, fund_tab = st.tabs(["📈 Stock Weights", "📊 Fund / ETF Weights"])

        with stock_tab:
            st.caption("Weights for individual stocks scored via the Owner's Framework. Must add up to 100.")
            w_col1, w_col2 = st.columns(2)
            with w_col1:
                w_fcf  = st.slider("FCF Yield",              0, 60, st.session_state.holding_weights["FCF Yield"],              step=5, key="w_fcf")
                w_roic = st.slider("ROIC",                   0, 40, st.session_state.holding_weights["ROIC"],                   step=5, key="w_roic")
                w_debt = st.slider("Debt / FCF",             0, 40, st.session_state.holding_weights["Debt / FCF"],             step=5, key="w_debt")
            with w_col2:
                w_gm   = st.slider("Gross Margin",           0, 40, st.session_state.holding_weights["Gross Margin"],           step=5, key="w_gm")
                w_ic   = st.slider("Interest Coverage",      0, 40, st.session_state.holding_weights["Interest Coverage"],      step=5, key="w_ic")
                w_poe  = st.slider("Price / Owner Earnings", 0, 60, st.session_state.holding_weights["Price / Owner Earnings"], step=5, key="w_poe")
            active_weights = {
                "FCF Yield": w_fcf, "ROIC": w_roic, "Debt / FCF": w_debt,
                "Gross Margin": w_gm, "Interest Coverage": w_ic, "Price / Owner Earnings": w_poe,
            }
            st.session_state.holding_weights = active_weights
            stock_total = sum(active_weights.values())
            if stock_total == 100:  st.success(f"✅ Total: {stock_total} / 100")
            elif stock_total < 100: st.warning(f"⚠️ Total: {stock_total} / 100 — {100 - stock_total} pts unallocated")
            else:                   st.error(f"❌ Total: {stock_total} / 100 — over by {stock_total - 100} pts.")

        with fund_tab:
            st.caption("Weights for ETFs and mutual funds scored via the Fund Health Framework. Must add up to 100.")
            st.caption("**Metric guide:** Expense Ratio = cost drag · Distribution Yield = income toward $8K/mo · 3-Year Return = compounding quality · Volatility = drawdown safety · Concentration = Passive Ponzi risk")
            fw_col1, fw_col2 = st.columns(2)
            with fw_col1:
                fw_exp  = st.slider("Expense Ratio",      0, 50, st.session_state.holding_fund_weights["Expense Ratio"],      step=5, key="fw_exp",
                                    help="Low fees compound in your favour. ≤0.10% = full pts, ≤0.50% = partial, >1% = zero.")
                fw_dy   = st.slider("Distribution Yield", 0, 50, st.session_state.holding_fund_weights["Distribution Yield"], step=5, key="fw_dy",
                                    help="Income generation toward your $8K/month goal. ≥6% = full pts, ≥4% = partial.")
                fw_ret  = st.slider("3-Year Return",      0, 40, st.session_state.holding_fund_weights["3-Year Return"],      step=5, key="fw_ret",
                                    help="Annualized 3-year trailing return. ≥12% = full pts, ≥7% = partial.")
            with fw_col2:
                fw_beta = st.slider("Volatility (Beta)",  0, 40, st.session_state.holding_fund_weights["Volatility (Beta)"], step=5, key="fw_beta",
                                    help="Beta ≤0.80 = safe for retirement drawdown, ≤1.10 = moderate, >1.10 = high risk.")
                fw_conc = st.slider("Concentration Risk", 0, 40, st.session_state.holding_fund_weights["Concentration Risk"], step=5, key="fw_conc",
                                    help="Top-10 holdings weight. ≤35% = diversified, ≤50% = concentrated, >50% = Passive Ponzi flag. (Data pending — rebalanced out currently.)")
            active_fund_weights = {
                "Expense Ratio": fw_exp, "Distribution Yield": fw_dy, "3-Year Return": fw_ret,
                "Volatility (Beta)": fw_beta, "Concentration Risk": fw_conc,
            }
            st.session_state.holding_fund_weights = active_fund_weights
            fund_total = sum(active_fund_weights.values())
            if fund_total == 100:  st.success(f"✅ Total: {fund_total} / 100")
            elif fund_total < 100: st.warning(f"⚠️ Total: {fund_total} / 100 — {100 - fund_total} pts unallocated")
            else:                   st.error(f"❌ Total: {fund_total} / 100 — over by {fund_total - 100} pts.")

    active_weights      = st.session_state.holding_weights
    active_fund_weights = st.session_state.holding_fund_weights
    stock_total         = sum(active_weights.values())
    fund_total          = sum(active_fund_weights.values())
    total_weight        = stock_total   # used for Score All button gate
    unique_symbols = consolidated['Symbol'].tolist()
    n_symbols      = len(unique_symbols)

    # ── Score All Button ───────────────────────────────────────────────────
    score_col, info_col = st.columns([2, 5])
    with score_col:
        weights_ok = (stock_total == 100 and fund_total == 100)
        run_scoring = st.button(
            f"⚡ Score All {n_symbols} Holdings", type="primary",
            disabled=(not weights_ok),
            help="Both Stock and Fund weights must add up to 100." if not weights_ok else "Score stocks via Polygon + yfinance; funds via yfinance Fund Health Framework."
        )
    with info_col:
        scored_count = len(st.session_state.holding_scores) + len(st.session_state.holding_fund_scores)
        if scored_count > 0:
            poly_count = sum(1 for s in st.session_state.holding_sources.values() if s == "polygon")
            yf_count   = sum(1 for s in st.session_state.holding_sources.values() if s == "yfinance")
            fund_count = sum(1 for s in st.session_state.holding_sources.values() if s == "fund")
            msg = f"✅ {scored_count} holdings scored"
            parts = []
            if poly_count: parts.append(f"{poly_count} stocks via Polygon")
            if yf_count:   parts.append(f"{yf_count} ADRs via yfinance")
            if fund_count: parts.append(f"{fund_count} funds via Fund Health")
            if parts: msg += " — " + ", ".join(parts)
            st.success(msg)
        else:
            st.caption("Scores not yet loaded. Click the button above.")

    if run_scoring:
        progress_bar = st.progress(0)
        status_text  = st.empty()
        scores       = {}
        sources      = {}
        fund_scores  = {}
        fund_debug   = {}   # symbol → debug payload for the diagnostics expander
        # Build a symbol→product_type lookup for routing
        sym_to_type = dict(zip(consolidated['Symbol'], consolidated['Product_Type']))
        for i, symbol in enumerate(unique_symbols):
            pct = (i + 1) / n_symbols
            progress_bar.progress(pct)
            product_type = sym_to_type.get(symbol, "")
            if is_fund(product_type):
                status_text.markdown(f"⏳ Scoring fund **{symbol}** ({product_type}) — {i+1} of {n_symbols}")
                data = fetch_score_data_fund(symbol)
                src  = data.get("source", "fund_error") if data else "fund_error"
                if src == "fund":
                    _, rebalanced    = score_fund(data, active_fund_weights)
                    fund_scores[symbol] = rebalanced
                    scores[symbol]      = rebalanced
                    sources[symbol]     = "fund"
                else:
                    scores[symbol]  = None
                    sources[symbol] = src   # "fund_no_data" or "fund_error"
                fund_debug[symbol] = {
                    "product_type": product_type,
                    "is_fund_detected": True,
                    **(data or {}),
                }
            else:
                status_text.markdown(f"⏳ Scoring **{symbol}** — {i+1} of {n_symbols}")
                data = fetch_score_data(symbol)
                if data is not None:
                    scores[symbol]  = score_stock(data, active_weights)
                    sources[symbol] = data.get("source", "polygon")
                else:
                    scores[symbol]  = None
                    sources[symbol] = None
            time.sleep(0.1)
        st.session_state.holding_scores      = scores
        st.session_state.holding_sources     = sources
        st.session_state.holding_fund_scores = fund_scores
        st.session_state.holding_fund_debug  = fund_debug
        progress_bar.progress(1.0)
        scored_ok      = len([s for s in scores.values() if s is not None])
        fund_ok        = len(fund_scores)
        fund_no_data   = sum(1 for s in sources.values() if s == "fund_no_data")
        fund_err       = sum(1 for s in sources.values() if s == "fund_error")
        yf_ok          = sum(1 for s in sources.values() if s == "yfinance")
        summary = f"✅ Done — {scored_ok} of {n_symbols} scored ({fund_ok} funds via Fund Health, {yf_ok} ADRs via yfinance fallback)"
        if fund_no_data or fund_err:
            summary += f" · ⚠️ {fund_no_data + fund_err} fund(s) returned no data — see diagnostics below"
        status_text.markdown(summary)

    # ── Fund Diagnostics expander (only shown after a scoring run with fund debug data) ──
    if 'holding_fund_debug' in st.session_state and st.session_state.holding_fund_debug:
        with st.expander("🔬 Fund Scoring Diagnostics", expanded=False):
            st.caption("Shows exactly what yfinance returned for each fund ticker. Useful for debugging missing scores.")
            for sym, dbg in st.session_state.holding_fund_debug.items():
                src = st.session_state.holding_sources.get(sym, "?")
                score_val = st.session_state.holding_fund_scores.get(sym, None)
                inner = dbg.get("debug", dbg)
                st.markdown(
                    f"**{sym}** ({dbg.get('product_type','?')}) — "
                    f"source: `{src}` — "
                    f"score: `{score_val if score_val is not None else 'unscored'}`"
                )
                cols = st.columns(5)
                labels = ["Expense Ratio", "Dist Yield", "3-Yr Return", "Beta", "yield_key"]
                keys   = ["expense_ratio", "dist_yield", "return_3yr", "beta", "yield_key"]
                for col, lbl, key in zip(cols, labels, keys):
                    val = inner.get(key)
                    col.metric(lbl, f"{val:.4f}" if isinstance(val, float) else str(val) if val is not None else "None")
                if dbg.get("error"):
                    st.error(f"Exception: {dbg['error']}")

    st.divider()

    # ── Build display dataframe ────────────────────────────────────────────
    display_df = consolidated.copy()
    display_df['Score_Num'] = display_df['Symbol'].apply(
        lambda s: st.session_state.holding_scores.get(s, None)
    )
    display_df['Score_Num'] = display_df['Score_Num'].apply(
        lambda s: int(s) if s is not None and not (isinstance(s, float) and pd.isna(s)) else None
    )
    display_df['Source'] = display_df['Symbol'].apply(
        lambda s: st.session_state.holding_sources.get(s, None)
    )
    # Route badge generation: fund_score_to_badge for funds, score_to_badge for stocks
    def make_badge(row):
        if row['Source'] == 'fund':
            return fund_score_to_badge(row['Score_Num'])
        return score_to_badge(row['Score_Num'])
    display_df['Badge']   = display_df.apply(make_badge, axis=1)
    display_df['Accounts_Label'] = display_df['Account_Count'].apply(
        lambda n: f"{n} acct{'s' if n > 1 else ''}"
    )

    # ── Sort Controls ──────────────────────────────────────────────────────
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

    # ── Column Headers ─────────────────────────────────────────────────────
    h1, h2, h3, h4, h5, h6, h7, h8 = st.columns([1.2, 3, 2, 1.5, 1.2, 1.5, 1.5, 1.5])
    with h1: st.markdown("**Symbol**")
    with h2: st.markdown("**Name**")
    with h3: st.markdown("**Type**")
    with h4: st.markdown("**Value**")
    with h5: st.markdown("**Accts**")
    with h6: st.markdown("**Score**")
    with h7: st.markdown("**Research**")
    with h8: st.markdown("**Analysis**")
    st.markdown("<hr style='margin:4px 0 8px 0'>", unsafe_allow_html=True)

    # ── Rows ───────────────────────────────────────────────────────────────
    for _, row in display_df.iterrows():
        c1, c2, c3, c4, c5, c6, c7, c8 = st.columns([1.2, 3, 2, 1.5, 1.2, 1.5, 1.5, 1.5])
        with c1:
            src = row.get('Source')
            sym_label = f"**{row['Symbol']}**"
            if src == "yfinance":
                sym_label += " 🌐"   # Globe = foreign ADR scored via yfinance
            elif src == "fund":
                sym_label += " 📊"   # Chart = fund scored via Fund Health Framework
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
            badge  = row['Badge']
            source = row.get('Source')
            if badge != "—":
                if source == "fund":
                    # Fund Health Score — use teal to distinguish from stock colour bands
                    num_part = badge.split(" ")[-1]  # e.g. "📊🟢 72" → "72"
                    if "🟢" in badge:   fc = "#1abc9c"
                    elif "🟡" in badge: fc = "#f39c12"
                    elif "🟠" in badge: fc = "#e67e22"
                    else:               fc = "#e74c3c"
                    st.markdown(
                        f"<span style='font-weight:bold; color:{fc}'>{badge}</span>"
                        f"<br><span style='font-size:0.7em; color:#888'>Fund Health</span>",
                        unsafe_allow_html=True
                    )
                else:
                    color = "#2ecc71" if badge.startswith("🟢") else "#f39c12" if badge.startswith("🟡") else "#e67e22" if badge.startswith("🟠") else "#e74c3c"
                    st.markdown(f"<span style='font-weight:bold; color:{color}'>{badge}</span>", unsafe_allow_html=True)
            else:
                st.caption("—")
        with c7:
            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                st.link_button("SEC", row['SEC Link'], use_container_width=True)
            with btn_col2:
                st.link_button("Yahoo", row['Yahoo Link'], use_container_width=True)
        with c8:
            st.link_button("🔍 Deep Dive", row['Dive Link'], use_container_width=True, type="primary")

    st.caption("🌐 = scored via yfinance fallback (foreign ADR — not in SEC database) · 📊 = Fund Health Score (expense ratio, yield, return, beta)")
    st.divider()

    # ── Account Breakdown ──────────────────────────────────────────────────
    st.subheader("🏦 Account Breakdown")
    st.caption("Select a holding to see how its value is distributed across your accounts.")
    selected_symbol = st.selectbox(
        "Select a holding", options=[""] + unique_symbols,
        format_func=lambda x: x if x else "— choose a symbol —"
    )
    if selected_symbol:
        account_detail = (
            df_holdings_raw[df_holdings_raw['Symbol'] == selected_symbol]
            [['Account Number','Name','Market Value ($)','Est. Annual Income ($)']]
            .copy().sort_values('Market Value ($)', ascending=False)
        )
        total_holding_val = account_detail['Market Value ($)'].sum()
        st.markdown(f"**{selected_symbol}** — Total Value: **${total_holding_val:,.2f}**")
        score  = st.session_state.holding_scores.get(selected_symbol)
        source = st.session_state.holding_sources.get(selected_symbol)
        if score is not None:
            if source == "fund":
                src_label = " (Fund Health Score — expense ratio, yield, return, beta)"
                st.markdown(f"Fund Health Score: {fund_score_to_badge(score)}{src_label}")
            elif source == "yfinance":
                st.markdown(f"Conviction Score: {score_to_badge(score)} (via yfinance — foreign ADR)")
            else:
                st.markdown(f"Conviction Score: {score_to_badge(score)} (via Polygon)")
        account_detail['% of Position'] = (
            account_detail['Market Value ($)'] / total_holding_val * 100
        ).round(1).astype(str) + '%'
        st.dataframe(account_detail, hide_index=True, use_container_width=True)
        st.link_button(
            "🔍 Open Full Analysis in Equity Scout",
            f"{APP_URL}/equity_scout?ticker={selected_symbol}&auto=1",
            type="primary"
        )
