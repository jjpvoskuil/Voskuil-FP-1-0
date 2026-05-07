import streamlit as st
import pandas as pd
import requests
import os

# 1. Page Configuration
st.set_page_config(page_title="Voskuil FP 1.0", layout="wide")
st.title("🛡️ Voskuil FP 1.0: Sovereign Wealth Dashboard")

# Updated Filenames
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

# 3. MASTER INGESTION FUNCTION (Hunter Logic)
def get_clean_df(filename, anchor_text):
    try:
        with open(filename, 'r') as f:
            lines = f.readlines()
        # Searches for the row where actual data starts
        header_idx = next(i for i, line in enumerate(lines) if anchor_text in line)
        return pd.read_csv(filename, skiprows=header_idx)
    except:
        return None

# --- DATA PROCESSING ---

# A. Holdings (Source 93)
df_holdings = get_clean_df(HOLDINGS_FILE, "Symbol")
total_val = 3790586.51 

# B. Realized Gains (Column N, preventing double-count) [Source 158]
realized_gain_total = 0.0
df_tax = get_clean_df(TAX_FILE, "Symbol")
if df_tax is not None:
    # Exclude MS 'Total' row [Source 158]
    df_tax_clean = df_tax[~df_tax.iloc[:, 0].astype(str).str.contains('Total', case=False, na=False)]
    gain_col = df_tax_clean.iloc[:, 13] # Column N
    realized_gain_total = pd.to_numeric(gain_col.astype(str).str.replace(',', '').str.replace('"', ''), errors='coerce').sum()

# C. Dividends & Interest (The New Feature) [Source 167, 168, 186, 213]
ytd_dividends, ytd_interest = 0.0, 0.0
df_trans = get_clean_df(TRANS_FILE, "Activity")
if df_trans is not None:
    # Ensure column names are clean and Amount is numeric
    df_trans.columns = [c.strip() for c in df_trans.columns]
    df_trans['Amount($)'] = pd.to_numeric(df_trans['Amount($)'].astype(str).str.replace(',', '').str.replace('"', ''), errors='coerce')
    
    # Sum all activities containing 'Dividend' (Qualified or standard) [Source 201, 202]
    ytd_dividends = df_trans[df_trans['Activity'].str.contains('Dividend', na=False, case=False)]['Amount($)'].sum()
    
    # Sum all activities containing 'Interest' [Source 186, 213]
    ytd_interest = df_trans[df_trans['Activity'].str.contains('Interest', na=False, case=False)]['Amount($)'].sum()

# 4. THE POWER BAR (Institutional KPIs)
withdrawal_goal = 96000.00 
col1, col2, col3, col4 = st.columns(4)
with col1: st.metric("Total Market Value", f"${total_val:,.2f}")
with col2: st.metric("Realized G/L (YTD)", f"${realized_gain_total:,.2f}")
with col3: st.metric("YTD Dividends", f"${ytd_dividends:,.2f}")
with col4: st.metric("YTD Interest", f"${ytd_interest:,.2f}")

st.divider()

# 5. DASHBOARD VIEWS
t1, t2 = st.columns(2)
with t1:
    st.subheader("Passive Cash Flow vs. Goal")
    total_ytd_cash = ytd_dividends + ytd_interest
    st.write(f"Total Cash Flow: **${total_ytd_cash:,.2f}**")
    st.progress(min(total_ytd_cash / withdrawal_goal, 1.0))
    st.info(f"Closing the **$37,386 income gap** via dividends and interest.")

with t2:
    st.subheader("Strategic Tax Context")
    st.warning(f"Unrealized Gain Pool: **$1,369,802.57** [Source 93]")
    st.info("Strategy: Targeting Pricing Power to offset structural inflation.")

# 6. HOLDINGS EXPLORER
st.header("📋 Institutional Holdings Explorer")
def get_sec_link(symbol):
    cik = cik_map.get(symbol)
    return f"https://www.sec.gov/edgar/browse/?CIK={cik}&owner=exclude" if cik else f"https://www.sec.gov/cgi-bin/browse-edgar?CIK={symbol}"

df_holdings['SEC Edgar'] = df_holdings['Symbol'].apply(get_sec_link)
st.dataframe(df_holdings[['Symbol', 'Name', 'Market Value ($)', 'SEC Edgar']], 
             column_config={"SEC Edgar": st.column_config.LinkColumn("Research")}, 
             hide_index=True, use_container_width=True)
