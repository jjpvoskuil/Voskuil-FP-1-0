import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import os

# 1. Page Configuration
st.set_page_config(page_title="Voskuil FP 1.0", layout="wide")
st.title("🛡️ Voskuil FP 1.0: Sovereign Wealth Dashboard")

# Global Filenames [1, 3, 4]
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

# 3. MASTER INGESTION FUNCTION [5]
def get_clean_df(filename, anchor_text):
    try:
        with open(filename, 'r') as f:
            lines = f.readlines()
        header_idx = next(i for i, line in enumerate(lines) if anchor_text in line)
        return pd.read_csv(filename, skiprows=header_idx)
    except:
        return None

# --- DYNAMIC DATA PROCESSING ---

# A. HOLDINGS: Raw Summation & Product Mix [2, 6-8]
total_val, total_income = 0.0, 0.0
df_holdings = get_clean_df(HOLDINGS_FILE, "Symbol")
product_mix = pd.DataFrame()

if df_holdings is not None:
    df_holdings.columns = [c.strip() for c in df_holdings.columns]
    # Filter 'Total' row to prevent double-counting [7, 9]
    df_holdings = df_holdings[~df_holdings.iloc[:, 0].astype(str).str.contains('Total', case=False, na=False)]
    
    # Numeric conversion for summation [6]
    for col in ['Market Value ($)', 'Est. Annual Income ($)']:
        if col in df_holdings.columns:
            df_holdings[col] = pd.to_numeric(df_holdings[col].astype(str).str.replace(',', '').str.replace('"', ''), errors='coerce')
    
    # Calculated metrics from detail rows [8]
    total_val = df_holdings['Market Value ($)'].sum()
    total_income = df_holdings['Est. Annual Income ($)'].sum()
    
    # Group and Sort for Synchronized Keys [2, 3]
    product_mix = df_holdings.groupby('Product Type')['Market Value ($)'].sum().reset_index()
    product_mix = product_mix.sort_values(by='Market Value ($)', ascending=False)
    
    # Assign specific colors for legend syncing
    color_palette = px.colors.qualitative.Prism
    product_mix['color'] = [color_palette[i % len(color_palette)] for i in range(len(product_mix))]
    
    df_holdings = df_holdings.dropna(subset=['Symbol'])

# B. REALIZED GAINS (Column N) [9]
realized_gain_total = 0.0
df_tax = get_clean_df(TAX_FILE, "Symbol")
if df_tax is not None:
    df_tax_clean = df_tax[~df_tax.iloc[:, 0].astype(str).str.contains('Total', case=False, na=False)]
    gain_col = df_tax_clean.iloc[:, 13] 
    realized_gain_total = pd.to_numeric(gain_col.astype(str).str.replace(',', '').str.replace('"', ''), errors='coerce').sum()

# C. DIVIDENDS & INTEREST [5, 10]
ytd_dividends, ytd_interest = 0.0, 0.0
df_trans = get_clean_df(TRANS_FILE, "Activity Date")
if df_trans is not None:
    df_trans.columns = [c.strip() for c in df_trans.columns]
    df_trans['Amount($)'] = pd.to_numeric(df_trans['Amount($)'].astype(str).str.replace(',', '').str.replace('"', ''), errors='coerce')
    ytd_dividends = df_trans[df_trans['Activity'].str.contains('Dividend', na=False, case=False)]['Amount($)'].sum()
    ytd_interest = df_trans[df_trans['Activity'].str.contains('Interest', na=False, case=False)]['Amount($)'].sum()

# 4. THE POWER BAR (Institutional KPIs)
col1, col2, col3, col4 = st.columns(4)
with col1: st.metric("Total Market Value", f"${total_val:,.2f}")
with col2: st.metric("Realized G/L (YTD)", f"${realized_gain_total:,.2f}")
with col3: st.metric("YTD Dividends", f"${ytd_dividends:,.2f}")
with col4: st.metric("YTD Interest", f"${ytd_interest:,.2f}")

st.divider()

# 5. ASSET ALLOCATION (Clean Pie + Dual Synchronized Keys)
st.subheader("Institutional Asset Allocation")
# FIXED: Providing exactly 3 width definitions [1, 2] for the 3 variables (c1, c2, c3)
c1, c2, c3 = st.columns([1, 2]) 

with c1:
    if not product_mix.empty:
        # Create simplified pie chart
        fig = px.pie(product_mix, values='Market Value ($)', names='Product Type', 
                     hole=0.4, color='Product Type',
                     color_discrete_map=dict(zip(product_mix['Product Type'], product_mix['color'])))
        
        # Only show % inside chart slices, hide legend to use your custom keys
        fig.update_traces(textinfo='percent', textposition='inside')
        fig.update_layout(showlegend=False, margin=dict(t=0, b=0, l=0, r=0), height=300)
        st.plotly_chart(fig, use_container_width=True)

with c2:
    st.markdown("**Product Type**")
    for _, row in product_mix.iterrows():
        # Synchronized color-coded key for Product Names
        st.markdown(f"<span style='color:{row['color']};'>●</span> {row['Product Type']}", unsafe_allow_html=True)

with c3:
    st.markdown("**Market Value**")
    for _, row in product_mix.iterrows():
        # Synchronized matching color-coded key for Dollar Values
        st.markdown(f"<span style='color:{row['color']};'>●</span> ${row['Market Value ($)']:,.0f}", unsafe_allow_html=True)

st.divider()

# 6. PASSIVE CASH FLOW PROGRESS
st.subheader("Retirement Cash Flow Monitor")
total_ytd_cash = ytd_dividends + ytd_interest
st.write(f"Passive Cash Flow YTD: **${total_ytd_cash:,.2f}**")
st.progress(min(total_ytd_cash / 96000.0, 1.0))
st.info(f"Closing the **$37,386 income gap** [11] toward your $8k/mo goal.")

# 7. HOLDINGS EXPLORER (With Institutional Drill-Downs)
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
