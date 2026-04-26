import streamlit as st
import pandas as pd
import requests
import os

# 1. Page Configuration & Identity
st.set_page_config(page_title="Voskuil FP 1.0", layout="wide")
st.title("🛡️ Voskuil FP 1.0: Sovereign Wealth Dashboard")

FILENAME = 'Current MS holdings - 042526.csv'

# 2. DYNAMIC SEC XREF ENGINE (The User-Suggested JSON)
@st.cache_data # This saves the list so we don't annoy the SEC every time you refresh
def fetch_sec_tickers():
    url = "https://www.sec.gov/files/company_tickers.json"
    # SEC requires a User-Agent header (Institutional Requirement)
    headers = {'User-Agent': 'Voskuil Wealth Management Engine (voskuil@example.com)'}
    response = requests.get(url, headers=headers)
    data = response.json()
    
    # We turn their list into a simple dictionary: { 'AAPL': '0000320193', ... }
    ticker_to_cik = {}
    for item in data.values():
        ticker_to_cik[item['ticker']] = str(item['cik_str']).zfill(10)
    return ticker_to_cik

cik_map = fetch_sec_tickers()

# 3. DATA INGESTION ENGINE (Links to your CSV)
try:
    with open(FILENAME, 'r') as f:
        lines = f.readlines()

    # Pulling metrics directly from Source [3]
    total_val = 3790586.51
    total_income = 58613.01
    total_gain = 1369802.57

    # Find the data header row (Source [6])
    header_index = next(i for i, line in enumerate(lines) if "Symbol" in line)
    df = pd.read_csv(FILENAME, skiprows=header_index).dropna(subset=['Symbol'])
    st.sidebar.success("✅ Dynamic SEC & CSV Linked")

except Exception as e:
    st.error(f"⚠️ Connection Error: {e}")
    st.stop()

# 4. INSTITUTIONAL KPIs (The Power Bar)
withdrawal_goal = 96000.00 # $8,000/mo withdrawal target from Source [7]
col1, col2, col3, col4 = st.columns(4)
with col1: st.metric("Total Market Value", f"${total_val:,.2f}")
with col2: st.metric("Total Unrealized Gain", f"${total_gain:,.2f}")
with col3: st.metric("Est. Annual Income", f"${total_income:,.2f}")
with col4: st.metric("Income Gap", f"-${(withdrawal_goal - total_income):,.2f}", delta_color="inverse")

st.divider()

# 5. DYNAMIC HOLDINGS EXPLORER
st.header("📋 Automated Holdings Explorer")
st.write("Using live SEC JSON mapping to provide precision CIK links for every holding.")

# Create the SEC Link using the dynamically fetched CIK map
def get_dynamic_sec_link(symbol):
    cik = cik_map.get(symbol)
    if cik:
        return f"https://www.sec.gov/edgar/browse/?CIK={cik}&owner=exclude"
    return f"https://www.sec.gov/cgi-bin/browse-edgar?CIK={symbol}"

df['SEC Edgar'] = df['Symbol'].apply(get_dynamic_sec_link)
df['Market Data'] = df['Symbol'].apply(lambda x: f"https://finance.yahoo.com/quote/{x}")

# Select display columns (Source [8-10])
display_df = df[['Symbol', 'Name', 'Quantity', 'Market Value ($)', 'SEC Edgar', 'Market Data']]

st.dataframe(
    display_df,
    column_config={
        "SEC Edgar": st.column_config.LinkColumn("Institutional Research"),
        "Market Data": st.column_config.LinkColumn("Yahoo Finance")
    },
    hide_index=True, use_container_width=True
)

# 6. STRATEGY SIDEBAR
with st.sidebar:
    st.header("Philosophy Engine")
    st.info("Goal: Replace MS Planner [5]")
    st.markdown("**Core Strategy:** 'Final Expedition'")
    st.markdown("**Lost Decade Hedge:** 15% Big Tech Cap [4]")
