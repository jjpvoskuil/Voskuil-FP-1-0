import streamlit as st
import pandas as pd
import requests
import os

# 1. Page Configuration
st.set_page_config(page_title="Voskuil FP 1.0", layout="wide")
st.title("🛡️ Voskuil FP 1.0: Sovereign Wealth Dashboard")

# Verified Filenames
HOLDINGS_FILE = 'Current MS holdings - 042526.csv'
TAX_FILE = 'Realized GL 042626.csv'
TRANS_FILE = 'Transaction History 042626.csv'

# 2. DYNAMIC METRIC SCRAPER (Scans CSV headers for summary numbers)
def scrape_ms_summary(filename, search_term):
    try:
        with open(filename, 'r') as f:
            for line in f:
                if search_term in line:
                    # Extracts the number between quotes (e.g., "3,790,586.51") [Source 2]
                    parts = line.split(',"')
                    for p in parts:
                        if search_term not in p and '"' in p:
                            val = p.split('"').replace(',', '')
                            return float(val)
    except:
        return 0.0
    return 0.0

# 3. MASTER INGESTION FUNCTION
def get_clean_df(filename, anchor_text):
    try:
        with open(filename, 'r') as f:
            lines = f.readlines()
        header_idx = next(i for i, line in enumerate(lines) if anchor_text in line)
        return pd.read_csv(filename, skiprows=header_idx)
    except:
        return None

# --- DYNAMIC DATA PROCESSING ---

# A. Holdings & Dynamic Market Value [Source 2, 93]
total_val = scrape_ms_summary(HOLDINGS_FILE, "Total Market Value:")
total_income = scrape_ms_summary(HOLDINGS_FILE, "Est. Annual Income:")
df_holdings = get_clean_df(HOLDINGS_FILE, "Symbol")

# B. Realized Gains (Excluding 'Total' row) [Source 158]
realized_gain_total = 0.0
df_tax = get_clean_df(TAX_FILE, "Symbol")
if df_tax is not None:
    df_tax_clean = df_tax[~df_tax.iloc[:, 0].astype(str).str.contains('Total', case=False, na=False)]
    gain_col = df_tax_clean.iloc[:, 13] 
    realized_gain_total = pd.to_numeric(gain_col.astype(str).str.replace(',', '').str.replace('"', ''), errors='coerce').sum()

# C. Dividends & Interest [Source 167, 168, 213]
ytd_dividends, ytd_interest = 0.0, 0.0
df_trans = get_clean_df(TRANS_FILE, "Activity Date")
if df_trans is not None:
    df_trans.columns = [c.strip() for c in df_trans.columns]
    df_trans['Amount($)'] = pd.to_numeric(df_trans['Amount($)'].astype(str).str.replace(',', '').str.replace('"', ''), errors='coerce')
    ytd_dividends = df_trans[df_trans['Activity'].str.contains('Dividend', na=False, case=False)]['Amount($)'].sum()
    ytd_interest = df_trans[df_trans['Activity'].str.contains('Interest', na=False, case=False)]['Amount($)'].sum()

# 4. THE POWER BAR
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
    st.subheader("Passive Cash Flow Progress")
    total_ytd_cash = ytd_dividends + ytd_interest
    st.write(f"Total YTD Cash Flow: **${total_ytd_cash:,.2f}**")
    st.progress(min(total_ytd_cash / withdrawal_goal, 1.0))
    st.info(f"Targeting the **$37,386 income gap** [Source 127].")

with t2:
    st.subheader("Strategic Context")
    st.write(f"Est. Annual Income: **${total_income:,.2f}**")
    st.warning("Strategy: Managing against Pettis-style inflation risk.")

# 6. HOLDINGS EXPLORER
if df_holdings is not None:
    st.header("📋 Institutional Holdings Explorer")
    st.dataframe(df_holdings[['Symbol', 'Name', 'Market Value ($)']], hide_index=True, use_container_width=True)
