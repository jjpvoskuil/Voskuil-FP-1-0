import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import os

# 1. Page Configuration
st.set_page_config(page_title="Voskuil FP 1.0", layout="wide")
st.title("🛡️ Voskuil FP 1.0: Sovereign Wealth Dashboard")

# Global Filenames
HOLDINGS_FILE = 'Current MS holdings - 042526.csv'
TAX_FILE = 'Realized GL 042626.csv'
TRANS_FILE = 'Transaction History 042626.csv'

# 2. DYNAMIC SEC XREF ENGINE
@st.cache_data 
def fetch_sec_tickers():
    try:
        url = "https://www.sec.gov/files/company_tickers.json"
        headers = {'User-Agent': 'Voskuil Wealth Engine (voskuil@example.com)'}
        response = requests.get(url, headers=headers)
        data = response.json()
        return {item['ticker']: str(item['cik_str']).zfill(10) for item in data.values()}
    except:
        return {}

cik_map = fetch_sec_tickers()

# 3. MASTER INGESTION FUNCTION
def get_clean_df(filename, anchor_text):
    try:
        with open(filename, 'r') as f:
            lines = f.readlines()
        header_idx = next(i for i, line in enumerate(lines) if anchor_text in line)
        return pd.read_csv(filename, skiprows=header_idx)
    except:
        return None

# --- DATA PROCESSING ---

# A. HOLDINGS: Raw Summation & Product Mix [Source 2, 3, 93, 117]
total_val, total_income = 0.0, 0.0
df_holdings = get_clean_df(HOLDINGS_FILE, "Symbol")
product_mix = pd.DataFrame()

if df_holdings is not None:
    df_holdings.columns = [c.strip() for c in df_holdings.columns]
    # Filter 'Total' row to prevent double-counting [Source 93, 158]
    df_holdings = df_holdings[~df_holdings.iloc[:, 0].astype(str).str.contains('Total', case=False, na=False)]
    
    # Numeric conversion for summation
    for col in ['Market Value ($)', 'Est. Annual Income ($)']:
        if col in df_holdings.columns:
            df_holdings[col] = pd.to_numeric(df_holdings[col].astype(str).str.replace(',', '').str.replace('"', ''), errors='coerce')
    
    # Calculated metrics from detail rows as requested
    total_val = df_holdings['Market Value ($)'].sum()
    total_income = df_holdings['Est. Annual Income ($)'].sum()
    
    # Grouping for the Pie Chart
    product_mix = df_holdings.groupby('Product Type')['Market Value ($)'].sum().reset_index()
    df_holdings = df_holdings.dropna(subset=['Symbol'])

# B. REALIZED GAINS (Column N) [Source 158]
realized_gain_total = 0.0
df_tax = get_clean_df(TAX_FILE, "Symbol")
if df_tax is not None:
    df_tax_clean = df_tax[~df_tax.iloc[:, 0].astype(str).str.contains('Total', case=False, na=False)]
    gain_col = df_tax_clean.iloc[:, 13] 
    realized_gain_total = pd.to_numeric(gain_col.astype(str).str.replace(',', '').str.replace('"', ''), errors='coerce').sum()

# C. DIVIDENDS & INTEREST [Source 167, 168]
ytd_dividends, ytd_interest = 0.0, 0.0
df_trans = get_clean_df(TRANS_FILE, "Activity Date")
if df_trans is not None:
    df_trans.columns = [c.strip() for c in df_trans.columns]
    df_trans['Amount($)'] = pd.to_numeric(df_trans['Amount($)'].astype(str).str.replace(',', '').str.replace('"', ''), errors='coerce')
    ytd_dividends = df_trans[df_trans['Activity'].str.contains('Dividend', na=False, case=False)]['Amount($)'].sum()
    ytd_interest = df_trans[df_trans['Activity'].str.contains('Interest', na=False, case=False)]['Amount($)'].sum()

# 4. THE POWER BAR (Institutional KPIs)
withdrawal_goal = 96000.00 
col1, col2, col3, col4 = st.columns(4)
with col1: st.metric("Total Market Value", f"${total_val:,.2f}")
with col2: st.metric("Realized G/L (YTD)", f"${realized_gain_total:,.2f}")
with col3: st.metric("YTD Dividends", f"${ytd_dividends:,.2f}")
with col4: st.metric("YTD Interest", f"${ytd_interest:,.2f}")

st.divider()

# 5. PRODUCT BREAKDOWN (With $ Values & Side Labels) & RETIREMENT PROGRESS
c1, c2 = st.columns(2)

with c1:
    st.subheader("Asset Allocation by Product Type")
    if not product_mix.empty:
        # Create the Pie Chart [Source 124]
        fig = px.pie(product_mix, values='Market Value ($)', names='Product Type', 
                     hole=0.4, color_discrete_sequence=px.colors.qualitative.Prism)
        
        # FIXED: Moves labels outside and adds formatted dollar values
        fig.update_traces(
            textposition='outside', 
            textinfo='percent+label',
            texttemplate='%{label}<br>%{percent}<br>$%{value:,.0f}'
        )
        
        fig.update_layout(
            margin=dict(t=30, b=30, l=10, r=10),
            showlegend=False # Legend hidden as labels are now outside
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Allocation data currently unavailable.")

with c2:
    st.subheader("Passive Cash Flow Progress")
    total_ytd_cash = ytd_dividends + ytd_interest
    st.write(f"Total YTD Cash Flow: **${total_ytd_cash:,.2f}**")
    st.progress(min(total_ytd_cash / withdrawal_goal, 1.0))
    st.info(f"Targeting a **$37,386 income gap** [Source 127].")
    st.write(f"**Organic Yield Income:** ${total_income:,.2f}")

# 6. HOLDINGS EXPLORER
st.header("📋 Institutional Holdings Explorer")
if df_holdings is not None:
    def get_sec_link(symbol):
        cik = cik_map.get(symbol)
        return f"https://www.sec.gov/edgar/browse/?CIK={cik}&owner=exclude" if cik else f"https://www.sec.gov/cgi-bin/browse-edgar?CIK={symbol}"

    df_holdings['SEC Edgar'] = df_holdings['Symbol'].apply(get_sec_link)
    df_holdings['Yahoo Finance'] = df_holdings['Symbol'].apply(lambda x: f"https://finance.yahoo.com/quote/{x}")

    st.dataframe(
        df_holdings[['Symbol', 'Name', 'Product Type', 'Market Value ($)', 'Est. Annual Income ($)', 'SEC Edgar', 'Yahoo Finance']],
        column_config={
            "SEC Edgar": st.column_config.LinkColumn("SEC Filings"),
            "Yahoo Finance": st.column_config.LinkColumn("Market Data")
        },
        hide_index=True, use_container_width=True
    )
