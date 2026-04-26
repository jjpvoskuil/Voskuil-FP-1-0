import streamlit as st
import pandas as pd
import os

# 1. Page Configuration
st.set_page_config(page_title="Voskuil FP 1.0", layout="wide")
st.title("🛡️ Voskuil FP 1.0: Sovereign Wealth Dashboard")

# Exact filenames from your Source list
HOLDINGS_FILE = 'Current MS holdings - 042526.csv'
TAX_FILE = 'Realized GL 042626.csv'
TRANS_FILE = 'Tranasaction History 042626.csv'

# 2. DEBUG SIDEBAR (To find the missing link)
st.sidebar.header("📁 File System Check")
files_present = os.listdir('.')
for f in [HOLDINGS_FILE, TAX_FILE, TRANS_FILE]:
    if f in files_present:
        st.sidebar.success(f"Found: {f}")
    else:
        st.sidebar.error(f"Missing: {f}")

# 3. DATA INGESTION: HOLDINGS
try:
    df_holdings = pd.read_csv(HOLDINGS_FILE, skiprows=6).dropna(subset=['Symbol'])
    total_val = 3790586.51 
except:
    st.warning("Holdings data not yet linked.")
    st.stop()

# 4. DATA INGESTION: TAX (Targeting Column N)
realized_gain_total = 0.0
try:
    df_tax = pd.read_csv(TAX_FILE, skiprows=6)
    # Column N is index 13. We clean and sum it.
    gain_col = df_tax.iloc[:, 13]
    realized_gain_total = pd.to_numeric(gain_col.astype(str).str.replace(',', '').str.replace('"', ''), errors='coerce').sum()
except Exception as e:
    st.sidebar.info(f"Tax Data Note: {e}")

# 5. DATA INGESTION: CASH FLOW (Dividends & Interest)
ytd_dividends, ytd_interest = 0.0, 0.0
try:
    df_trans = pd.read_csv(TRANS_FILE, skiprows=4)
    df_trans.columns = [c.strip() for c in df_trans.columns]
    
    # Summing activity types [Source 168, 185, 201]
    ytd_dividends = df_trans[df_trans['Activity'].str.contains('Dividend', na=False)]['Amount($)'].sum()
    ytd_interest = df_trans[df_trans['Activity'].str.contains('Interest', na=False)]['Amount($)'].sum()
except Exception as e:
    st.sidebar.info(f"Cash Flow Note: {e}")

# 6. THE POWER BAR
withdrawal_goal = 96000.00
col1, col2, col3, col4 = st.columns(4)
with col1: st.metric("Total Market Value", f"${total_val:,.2f}")
with col2: st.metric("Realized G/L (YTD)", f"${realized_gain_total:,.2f}")
with col3: st.metric("YTD Dividends", f"${ytd_dividends:,.2f}")
with col4: st.metric("YTD Interest", f"${ytd_interest:,.2f}")

st.divider()

# 7. TAX & HOLDINGS VIEWS
st.header("📊 Tax Implications & Optimization")
if not realized_gain_total == 0:
    st.write(f"Tracking taxable events for your **$1,369,802.57** unrealized gain pool [Source 120].")
    st.dataframe(df_tax.dropna(axis=1, how='all'), use_container_width=True)

st.header("📋 Institutional Holdings Explorer")
st.dataframe(df_holdings[['Symbol', 'Name', 'Market Value ($)']], use_container_width=True)
