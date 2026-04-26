import streamlit as st
import pandas as pd
import requests

# 1. Page Config
st.set_page_config(page_title="Voskuil FP 1.0", layout="wide")
st.title("🛡️ Voskuil FP 1.0: Sovereign Wealth Dashboard")

# File Definitions
HOLDINGS_FILE = 'Current MS holdings - 042526.csv'
TAX_FILE = 'Realized GL 042626.csv'
TRANS_FILE = 'Tranasaction History 042626.csv'

# 2. DATA INGESTION: Realized Gains (Targeting Column N)
realized_gain_total = 0.0
try:
    # Read the file and specifically look for the 14th column (Column N)
    df_tax_raw = pd.read_csv(TAX_FILE, skiprows=6) # Skipping header junk
    # We clean Column N (Realized Gain/Loss) by removing commas and converting to numbers
    df_tax_raw.iloc[:, 13] = pd.to_numeric(df_tax_raw.iloc[:, 13].astype(str).str.replace(',', '').str.replace('"', ''), errors='coerce')
    realized_gain_total = df_tax_raw.iloc[:, 13].sum()
    tax_ready = True
except Exception as e:
    tax_ready = False

# 3. DATA INGESTION: Dividends & Interest (Transaction History)
ytd_dividends = 0.0
ytd_interest = 0.0
try:
    df_trans = pd.read_csv(TRANS_FILE, skiprows=4)
    # Standardize column names for processing
    df_trans.columns = [c.strip() for c in df_trans.columns]
    
    # Filter and sum Qualified Dividends and standard Dividends
    div_mask = df_trans['Activity'].isin(['Qualified Dividend', 'Dividend'])
    ytd_dividends = df_trans[div_mask]['Amount($)'].sum()
    
    # Filter and sum Interest Income
    int_mask = df_trans['Activity'].isin(['Interest Income', 'Interest'])
    ytd_interest = df_trans[int_mask]['Amount($)'].sum()
    flow_ready = True
except:
    flow_ready = False

# 4. DATA INGESTION: Holdings (Original Logic)
try:
    df_holdings = pd.read_csv(HOLDINGS_FILE, skiprows=6).dropna(subset=['Symbol'])
    total_val = 3790586.51 # Metric from Source 2
except:
    st.stop()

# 5. THE POWER BAR (Tax & Cash Flow Focus)
col1, col2, col3, col4 = st.columns(4)
with col1: 
    st.metric("Total Market Value", f"${total_val:,.2f}")
with col2: 
    st.metric("Realized G/L (YTD)", f"${realized_gain_total:,.2f}", help="Pulled from Column N of GL file")
with col3: 
    st.metric("YTD Dividends", f"${ytd_dividends:,.2f}")
with col4: 
    st.metric("YTD Interest", f"${ytd_interest:,.2f}")

st.divider()

# 6. TAX IMPLICATIONS MODULE
st.header("📊 Tax Implications & Optimization")
t1, t2 = st.columns(2)
with t1:
    st.subheader("YTD Income Summary")
    tax_data = {
        "Category": ["Realized Gains", "Dividends", "Interest Earned"],
        "Amount": [realized_gain_total, ytd_dividends, ytd_interest]
    }
    st.bar_chart(pd.DataFrame(tax_data).set_index("Category"))

with t2:
    st.subheader("Sovereign Tax Strategy")
    st.info(f"**OBBBA Hedge:** Your $96k withdrawal goal is optimized for the **$6,000 senior deduction** [5].")
    st.warning(f"**Unrealized Pool:** You are sitting on **$1,369,802.57** in gains. Manage the 23.8% tech tilt to avoid tax spikes [6, 7].")

# 7. HOLDINGS EXPLORER (With SEC Drill-Down)
st.header("📋 Holdings Explorer & SEC Drill-Down")
st.dataframe(df_holdings[['Symbol', 'Name', 'Market Value ($)']], use_container_width=True)
