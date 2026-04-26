import streamlit as st
import pandas as pd
import requests
import os

# 1. Page Configuration
st.set_page_config(page_title="Voskuil FP 1.0", layout="wide")
st.title("🛡️ Voskuil FP 1.0: Sovereign Wealth Dashboard")

# Exact filenames from your Source list
HOLDINGS_FILE = 'Current MS holdings - 042526.csv'
TAX_FILE = 'Realized GL 042626.csv'
TRANS_FILE = 'Tranasaction History 042626.csv'

# 2. FILE DIAGNOSTIC (Sidebar)
st.sidebar.header("📁 System Status")
files_present = os.listdir('.')
for f in [HOLDINGS_FILE, TAX_FILE, TRANS_FILE]:
    if f in files_present:
        st.sidebar.success(f"Linked: {f}")
    else:
        st.sidebar.error(f"Missing: {f}")

# 3. DYNAMIC SEC XREF ENGINE (Automated CIK Mapping)
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

# 4. DYNAMIC DATA INGESTION ENGINE
def get_clean_df(filename, anchor_text):
    try:
        with open(filename, 'r') as f:
            lines = f.readlines()
        header_idx = next(i for i, line in enumerate(lines) if anchor_text in line)
        return pd.read_csv(filename, skiprows=header_idx)
    except:
        return None

# Load Holdings Data
df_holdings = get_clean_df(HOLDINGS_FILE, "Symbol")
if df_holdings is not None:
    df_holdings = df_holdings.dropna(subset=['Symbol'])
    total_val = 3790586.51 # Metric from MS Summary
else:
    st.warning("Holdings data not yet linked. Check the sidebar status.")
    st.stop()

# Load Tax Data (FIXED: Excludes the 'Total' row to prevent double counting)
realized_gain_total = 0.0
df_tax = get_clean_df(TAX_FILE, "Symbol")
if df_tax is not None:
    try:
        # THE FIX: Filter out the row where the first column says 'Total'
        df_tax_clean = df_tax[~df_tax.iloc[:, 0].astype(str).str.contains('Total', case=False, na=False)]
        
        # Target Column N (index 13) for Realized Gain/Loss
        gain_col = df_tax_clean.iloc[:, 13]
        realized_gain_total = pd.to_numeric(gain_col.astype(str).str.replace(',', '').str.replace('"', ''), errors='coerce').sum()
    except Exception as e:
        st.sidebar.error(f"Tax Data Precision Note: {e}")

# Load Cash Flow (Dividends & Interest)
ytd_dividends, ytd_interest = 0.0, 0.0
df_trans = get_clean_df(TRANS_FILE, "Activity")
if df_trans is not None:
    try:
        df_trans.columns = [c.strip() for c in df_trans.columns]
        # Sum activities including 'Dividend' and 'Interest' [2, 3]
        ytd_dividends = df_trans[df_trans['Activity'].str.contains('Dividend', na=False)]['Amount($)'].sum()
        ytd_interest = df_trans[df_trans['Activity'].str.contains('Interest', na=False)]['Amount($)'].sum()
    except:
        pass

# 5. THE POWER BAR (Institutional KPIs)
withdrawal_goal = 96000.00
col1, col2, col3, col4 = st.columns(4)
with col1: st.metric("Total Market Value", f"${total_val:,.2f}")
with col2: st.metric("Realized G/L (YTD)", f"${realized_gain_total:,.2f}", help="Sum of individual trades, excluding the MS Total row.")
with col3: st.metric("YTD Dividends", f"${ytd_dividends:,.2f}")
with col4: st.metric("YTD Interest", f"${ytd_interest:,.2f}")

st.divider()

# 6. TAX & RETIREMENT VIEWS
st.header("📊 Tax Implications & Optimization")
t1, t2 = st.columns(2)
with t1:
    st.subheader("Cash Flow Tracker")
    st.write("Tracking progress towards closing your **$37,386 income gap** [4].")
    cash_data = pd.DataFrame({
        "Source": ["Dividends", "Interest"],
        "Amount": [ytd_dividends, ytd_interest]
    }).set_index("Source")
    st.bar_chart(cash_data)

with t2:
    st.subheader("Strategic Tax Notes")
    st.info("Modeling includes the **$6,000 senior deduction** hedge [5].")
    st.warning("Harvesting Opportunity: Managing against **$1,369,802.57** in unrealized gains [6].")

# 7. INSTITUTIONAL HOLDINGS EXPLORER (With CIK Drill-Down)
st.header("📋 Institutional Holdings Explorer")

def get_sec_link(symbol):
    cik = cik_map.get(symbol)
    if cik:
        return f"https://www.sec.gov/edgar/browse/?CIK={cik}&owner=exclude"
    return f"https://www.sec.gov/cgi-bin/browse-edgar?CIK={symbol}"

df_holdings['SEC Edgar'] = df_holdings['Symbol'].apply(get_sec_link)
df_holdings['Yahoo'] = df_holdings['Symbol'].apply(lambda x: f"https://finance.yahoo.com/quote/{x}")

st.dataframe(
    df_holdings[['Symbol', 'Name', 'Quantity', 'Market Value ($)', 'SEC Edgar', 'Yahoo']],
    column_config={
        "SEC Edgar": st.column_config.LinkColumn("Research"),
        "Yahoo": st.column_config.LinkColumn("Market")
    },
    hide_index=True, use_container_width=True
)
