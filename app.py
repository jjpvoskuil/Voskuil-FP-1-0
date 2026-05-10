import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import yfinance as yf
import time

# ─────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(page_title="Voskuil FP 1.0", layout="wide")
st.title("🛡️ Voskuil FP 1.0: Sovereign Wealth Dashboard")

# ─────────────────────────────────────────────
# GLOBAL FILENAMES
# ─────────────────────────────────────────────
HOLDINGS_FILE = 'Current MS holdings - 042526.csv'
TAX_FILE      = 'Realized GL 042626.csv'
TRANS_FILE    = 'Transaction History 042626.csv'

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
    "poe_bargain":              15.0,
    "poe_fair":                 25.0,
    "poe_stretched":            35.0,
}

# ─────────────────────────────────────────────
# DEFAULT WEIGHTS
# ─────────────────────────────────────────────
DEFAULT_WEIGHTS = {
    "FCF Yield":              30,
    "ROIC":                   10,
    "Debt / FCF":             20,
    "Gross Margin":           15,
    "Interest Coverage":      10,
    "Price / Owner Earnings": 15,
}

# ─────────────────────────────────────────────
# SEC XREF
# ─────────────────────────────────────────────
@st.cache_data
def fetch_sec_tickers():
    try:
        url      = "https://www.sec.gov/files/company_tickers.json"
        headers  = {'User-Agent': 'Voskuil Wealth Engine (voskuil@example.com)'}
        response = requests.get(url, headers=headers)
        data     = response.json()
        return {item['ticker']: str(item['cik_str']).zfill(10) for item in data.values()}
    except:
        return {}

cik_map = fetch_sec_tickers()

# ─────────────────────────────────────────────
# MASTER INGESTION
# ─────────────────────────────────────────────
def get_clean_df(filename, anchor_text):
    try:
        with open(filename, 'r') as f:
            lines = f.readlines()
        header_idx = next(i for i, line in enumerate(lines) if anchor_text in line)
        return pd.read_csv(filename, skiprows=header_idx)
    except:
        return None

# ─────────────────────────────────────────────
# SCORING ENGINE
# ─────────────────────────────────────────────
def fetch_score_data(ticker: str) -> dict | None:
    try:
        stock      = yf.Ticker(ticker)
        info       = stock.info
        cashflow   = stock.cashflow
        financials = stock.financials
        balance    = stock.balance_sheet

        market_cap = info.get('marketCap')
        if not market_cap:
            return None

        try:
            op_cf = cashflow.loc['Operating Cash Flow'].iloc[0]
            capex = cashflow.loc['Capital Expenditure'].iloc[0]
            fcf   = op_cf + capex
        except:
            fcf = None

        fcf_yield = (fcf / market_cap) if (fcf and market_cap) else None

        try:
            net_income   = financials.loc['Net Income'].iloc[0]
            total_assets = balance.loc['Total Assets'].iloc[0]
            current_liab = balance.loc['Current Liabilities'].iloc[0]
            invested_cap = total_assets - current_liab
            roic         = net_income / invested_cap if invested_cap != 0 else None
        except:
            net_income, roic = None, None

        try:
            total_debt  = balance.loc['Total Debt'].iloc[0]
            debt_to_fcf = (total_debt / fcf) if (fcf and fcf > 0) else None
        except:
            debt_to_fcf = None

        try:
            ebit              = financials.loc['EBIT'].iloc[0]
            interest_expense  = abs(financials.loc['Interest Expense'].iloc[0])
            interest_coverage = ebit / interest_expense if interest_expense != 0 else None
        except:
            interest_coverage = None

        gross_margin = info.get('grossMargins')

        try:
            dna        = cashflow.loc['Depreciation And Amortization'].iloc[0]
            capex_val  = abs(cashflow.loc['Capital Expenditure'].iloc[0])
            owner_earn = net_income + dna - capex_val if net_income is not None else None
            shares     = info.get('sharesOutstanding')
            price      = info.get('currentPrice') or info.get('regularMarketPrice')
            poe        = (price / (owner_earn / shares)) if (owner_earn and owner_earn > 0 and shares and price) else None
        except:
            poe = None

        return {
            "fcf_yield":         fcf_yield,
            "roic":              roic,
            "debt_to_fcf":       debt_to_fcf,
            "interest_coverage": interest_coverage,
            "gross_margin":      gross_margin,
            "price_owner_earn":  poe,
        }
    except:
        return None


def score_stock(data: dict, weights: dict) -> int:
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
    ic       = data.get('interest_coverage') or 0
    if debt_fcf is not None:
        if debt_fcf < THRESHOLDS['debt_fcf_safe']:       pts += weights["Debt / FCF"]
        elif debt_fcf < THRESHOLDS['debt_fcf_warning']:  pts += round(weights["Debt / FCF"] * 0.50)
        elif ic >= THRESHOLDS['interest_coverage_safe']: pts += round(weights["Debt / FCF"] * 0.50)

    gm = data.get('gross_margin')
    if gm:
        if gm >= THRESHOLDS['gross_margin_great']:   pts += weights["Gross Margin"]
        elif gm >= THRESHOLDS['gross_margin_good']:  pts += round(weights["Gross Margin"] * 0.67)
        else:                                        pts += round(weights["Gross Margin"] * 0.20)

    ic_val = data.get('interest_coverage')
    if ic_val:
        if ic_val >= THRESHOLDS['interest_coverage_safe']: pts += weights["Interest Coverage"]
        elif ic_val >= 2.5:                                pts += round(weights["Interest Coverage"] * 0.50)
        elif ic_val > 0:                                   pts += round(weights["Interest Coverage"] * 0.15)

    poe = data.get('price_owner_earn')
    if poe:
        if poe <= THRESHOLDS['poe_bargain']:    pts += weights["Price / Owner Earnings"]
        elif poe <= THRESHOLDS['poe_fair']:     pts += round(weights["Price / Owner Earnings"] * 0.67)
        elif poe <= THRESHOLDS['poe_stretched']:pts += round(weights["Price / Owner Earnings"] * 0.25)

    return pts


def score_to_badge(score) -> str:
    try:
        if score is None or (isinstance(score, float) and pd.isna(score)):
            return "—"
        score = int(score)
        if score >= 80:   return f"🟢 {score}"
        elif score >= 65: return f"🟡 {score}"
        elif score >= 45: return f"🟠 {score}"
        else:             return f"🔴 {score}"
    except:
        return "—"


# ─────────────────────────────────────────────
# DATA PROCESSING
# ─────────────────────────────────────────────
total_val, total_income            = 0.0, 0.0
ira_gain_total, taxable_gain_total = 0.0, 0.0
ytd_dividends, ytd_interest        = 0.0, 0.0
product_mix                        = pd.DataFrame()
df_holdings_raw                    = None

# A. HOLDINGS
df_holdings_raw = get_clean_df(HOLDINGS_FILE, "Account Number")
if df_holdings_raw is not None:
    df_holdings_raw.columns = [c.strip() for c in df_holdings_raw.columns]
    df_holdings_raw = df_holdings_raw[
        ~df_holdings_raw.iloc[:, 0].astype(str).str.contains('Total', case=False, na=False)
    ]
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
    color_palette= px.colors.qualitative.Prism
    product_mix['color'] = [color_palette[i % len(color_palette)] for i in range(len(product_mix))]
    df_holdings_raw = df_holdings_raw.dropna(subset=['Symbol'])

# B. REALIZED GAINS
df_tax = get_clean_df(TAX_FILE, "Account Number")
if df_tax is not None:
    df_tax.columns = [c.strip() for c in df_tax.columns]
    df_tax_clean   = df_tax[~df_tax.iloc[:, 0].astype(str).str.contains('Total', case=False, na=False)]
    df_tax_clean['Numeric Gain'] = pd.to_numeric(
        df_tax_clean.iloc[:, 13].astype(str).str.replace(',', '').str.replace('"', ''),
        errors='coerce'
    )
    ira_mask           = df_tax_clean.iloc[:, 0].astype(str).str.contains('IRA', case=False, na=False)
    ira_gain_total     = df_tax_clean[ira_mask]['Numeric Gain'].sum()
    taxable_gain_total = df_tax_clean[~ira_mask]['Numeric Gain'].sum()

# C. DIVIDENDS & INTEREST
df_trans = get_clean_df(TRANS_FILE, "Activity Date")
if df_trans is not None:
    df_trans.columns = [c.strip() for c in df_trans.columns]
    df_trans['Amount($)'] = pd.to_numeric(
        df_trans['Amount($)'].astype(str).str.replace(',', '').str.replace('"', ''),
        errors='coerce'
    )
    ytd_dividends = df_trans[df_trans['Activity'].str.contains('Dividend', na=False, case=False)]['Amount($)'].sum()
    ytd_interest  = df_trans[df_trans['Activity'].str.contains('Interest', na=False, case=False)]['Amount($)'].sum()

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
        fig = px.pie(
            product_mix, values='Market Value ($)', names='Product Type',
            hole=0.4, color='Product Type',
            color_discrete_map=dict(zip(product_mix['Product Type'], product_mix['color']))
        )
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
/* Style dataframe link columns to look like buttons */
.stDataFrame a {
    background-color: #1f6feb;
    color: white !important;
    padding: 3px 10px;
    border-radius: 12px;
    text-decoration: none !important;
    font-size: 0.78em;
    font-weight: 500;
    white-space: nowrap;
}
.stDataFrame a:hover {
    background-color: #388bfd;
    color: white !important;
}
</style>
""", unsafe_allow_html=True)

if df_holdings_raw is not None:

    # ── Consolidate by Symbol ─────────────────────────────────────────
    consolidated = (
        df_holdings_raw
        .groupby('Symbol')
        .agg(
            Name          = ('Name',            'first'),
            Product_Type  = ('Product Type',    'first'),
            Total_Value   = ('Market Value ($)', 'sum'),
            Accounts      = ('Account Number',   lambda x: ', '.join(x.astype(str).unique())),
            Account_Count = ('Account Number',   'nunique'),
        )
        .reset_index()
        .sort_values('Total_Value', ascending=False)
    )

    # SEC / Yahoo links
    def get_sec_link(symbol):
        cik = cik_map.get(symbol)
        return f"https://www.sec.gov/edgar/browse/?CIK={cik}&owner=exclude" if cik else f"https://www.sec.gov/cgi-bin/browse-edgar?CIK={symbol}"

    consolidated['SEC Filings'] = consolidated['Symbol'].apply(get_sec_link)
    consolidated['Market Data'] = consolidated['Symbol'].apply(lambda x: f"https://finance.yahoo.com/quote/{x}")

    # Restore cached scores if available
    if 'holding_scores' not in st.session_state:
        st.session_state.holding_scores = {}
    if 'holding_weights' not in st.session_state:
        st.session_state.holding_weights = DEFAULT_WEIGHTS.copy()

    # ── Weight Customizer ─────────────────────────────────────────────
    st.subheader("⚙️ Scoring Weights")
    st.caption("Set the weights used to score your holdings. Must add up to 100.")

    w_col1, w_col2 = st.columns(2)
    with w_col1:
        w_fcf  = st.slider("FCF Yield",              0, 60, st.session_state.holding_weights["FCF Yield"],              step=5, key="w_fcf")
        w_roic = st.slider("ROIC",                   0, 40, st.session_state.holding_weights["ROIC"],                   step=5, key="w_roic")
        w_debt = st.slider("Debt / FCF",             0, 40, st.session_state.holding_weights["Debt / FCF"],             step=5, key="w_debt")
    with w_col2:
        w_gm   = st.slider("Gross Margin",           0, 40, st.session_state.holding_weights["Gross Margin"],           step=5, key="w_gm")
        w_ic   = st.slider("Interest Coverage",      0, 40, st.session_state.holding_weights["Interest Coverage"],      step=5, key="w_ic")
        w_poe  = st.slider("Price / Owner Earnings", 0, 40, st.session_state.holding_weights["Price / Owner Earnings"], step=5, key="w_poe")

    active_weights = {
        "FCF Yield":              w_fcf,
        "ROIC":                   w_roic,
        "Debt / FCF":             w_debt,
        "Gross Margin":           w_gm,
        "Interest Coverage":      w_ic,
        "Price / Owner Earnings": w_poe,
    }

    # Save weights to session state so Score All uses them
    st.session_state.holding_weights = active_weights

    total_weight = sum(active_weights.values())
    if total_weight == 100:
        st.success(f"✅ Total: {total_weight} / 100")
    elif total_weight < 100:
        st.warning(f"⚠️ Total: {total_weight} / 100 — {100 - total_weight} pts unallocated")
    else:
        st.error(f"❌ Total: {total_weight} / 100 — over by {total_weight - 100} pts.")

    st.divider()

    # ── Score All Button ──────────────────────────────────────────────
    unique_symbols = consolidated['Symbol'].tolist()
    n_symbols      = len(unique_symbols)

    score_col, info_col = st.columns([2, 5])
    with score_col:
        run_scoring = st.button(
            f"⚡ Score All {n_symbols} Holdings",
            type="primary",
            disabled=(total_weight != 100),
            help="Weights must add up to 100 before scoring." if total_weight != 100 else "Fetch fundamentals and score all holdings."
        )
    with info_col:
        scored_count = len(st.session_state.holding_scores)
        if scored_count > 0:
            st.success(f"✅ {scored_count} holdings scored — sort by Score column to find your best opportunities.")
        else:
            st.caption("Scores not yet loaded. Click the button above to score all holdings.")

    if run_scoring:
        progress_bar = st.progress(0)
        status_text  = st.empty()
        scores       = {}

        for i, symbol in enumerate(unique_symbols):
            pct = (i + 1) / n_symbols
            progress_bar.progress(pct)
            status_text.markdown(f"⏳ Scoring **{symbol}** — {i+1} of {n_symbols}")

            data = fetch_score_data(symbol)
            scores[symbol] = score_stock(data, active_weights) if data else None
            time.sleep(0.15)

        st.session_state.holding_scores = scores
        progress_bar.progress(1.0)
        status_text.markdown(f"✅ Done — {len([s for s in scores.values() if s is not None])} holdings scored.")
        st.write("Sample scores:", dict(list(scores.items())[:5]))
        st.rerun()

    st.divider()

    # ── Build Sortable Display DataFrame ─────────────────────────────
    # We use st.dataframe with column_config for a clean sortable table.
    # The Analyze link is a separate clickable column.

    display_df = consolidated.copy()
    display_df['Score']        = display_df['Symbol'].apply(
        lambda s: st.session_state.holding_scores.get(s, None)
    )
    display_df['Score_Numeric']= display_df['Score'].apply(
        lambda s: int(s) if s is not None and not (isinstance(s, float) and pd.isna(s)) else None
    )
    display_df['Verdict'] = display_df['Score_Numeric'].apply(
        lambda s: ("🟢 Strong Buy" if s >= 80 else "🟡 Watch" if s >= 65 else "🟠 Caution" if s >= 45 else "🔴 Avoid") if s is not None else "—"
    )
    display_df['Accounts_Label'] = display_df['Account_Count'].apply(
        lambda n: f"{n} account{'s' if n > 1 else ''}"
    )
    display_df['Analyze'] = display_df['Symbol'].apply(
        lambda s: f"https://voskuil-fp-1-0-k85bd7afbw8dnqeftzxwbu.streamlit.app/equity_scout?ticker={s}&auto=1"
    )

    # Columns to show in the sortable table
    table_df = display_df[[
        'Symbol', 'Name', 'Product_Type',
        'Total_Value', 'Accounts_Label',
        'Score_Numeric', 'Verdict',
        'SEC Filings', 'Market Data', 'Analyze'
    ]].copy()

    table_df.columns = [
        'Symbol', 'Name', 'Product Type',
        'Total Value ($)', 'Accounts',
        'Score', 'Verdict',
        'SEC Filings', 'Market Data', 'Deep Dive'
    ]

    st.subheader(f"{n_symbols} Unique Holdings — Consolidated Across All Accounts")
    st.caption("Click any column header to sort. Use Deep Dive to open the full analysis.")

    st.dataframe(
        table_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Symbol":         st.column_config.TextColumn("Symbol",       width="small"),
            "Name":           st.column_config.TextColumn("Name",         width="medium"),
            "Product Type":   st.column_config.TextColumn("Product Type", width="medium"),
            "Total Value ($)":st.column_config.NumberColumn("Total Value ($)", format="$%,.0f", width="medium"),
            "Accounts":       st.column_config.TextColumn("Accounts",     width="small"),
            "Score":          st.column_config.NumberColumn("Score",      format="%d",          width="small"),
            "Verdict":        st.column_config.TextColumn("Verdict",      width="medium"),
            "SEC Filings":    st.column_config.LinkColumn("SEC Filings",  width="small"),
            "Market Data":    st.column_config.LinkColumn("Market Data",  width="small"),
            "Deep Dive":      st.column_config.LinkColumn("Deep Dive 🔍", width="small"),
        }
    )

    st.divider()

    # ── Account Breakdown ─────────────────────────────────────────────
    st.subheader("🏦 Account Breakdown")
    st.caption("Select a holding to see how its value is distributed across your accounts.")

    selected_symbol = st.selectbox(
        "Select a holding",
        options=[""] + unique_symbols,
        format_func=lambda x: x if x else "— choose a symbol —"
    )

    if selected_symbol:
        account_detail = (
            df_holdings_raw[df_holdings_raw['Symbol'] == selected_symbol]
            [['Account Number', 'Name', 'Market Value ($)', 'Est. Annual Income ($)']]
            .copy()
            .sort_values('Market Value ($)', ascending=False)
        )

        total_holding_val = account_detail['Market Value ($)'].sum()

        st.markdown(f"**{selected_symbol}** — Total Value: **${total_holding_val:,.2f}**")

        score = st.session_state.holding_scores.get(selected_symbol)
        if score is not None:
            badge = score_to_badge(score)
            st.markdown(f"Conviction Score: {badge}")

        account_detail['% of Position'] = (
            account_detail['Market Value ($)'] / total_holding_val * 100
        ).round(1).astype(str) + '%'

        st.dataframe(account_detail, hide_index=True, use_container_width=True)

        st.markdown(
            f"[🔍 Open Full Analysis in Equity Scout](https://voskuil-fp-1-0-k85bd7afbw8dnqeftzxwbu.streamlit.app/equity_scout?ticker={selected_symbol}&auto=1)",
            unsafe_allow_html=True
        )
