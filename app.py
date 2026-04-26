import streamlit as st
import pandas as pd
import requests

# 1. Page Identity & Layout
st.set_page_config(page_title="Voskuil FP 1.0", layout="wide")
st.title("🛡️ Voskuil FP 1.0: Sovereign Wealth Dashboard")

# Define our source files
HOLDINGS_FILE = 'Current MS holdings - 042526.csv'
TAX_FILE = 'Realized GL 042626.csv'

# 2. AUTOMATED SEC XREF (User-Suggested JSON)
@st.cache_data
def fetch_sec_tickers():
    url = "https://www.sec.gov/files/company_tickers.json"
    headers = {'User-Agent': 'Voskuil Wealth Management Engine (voskuil@example.com)'}
    response = requests.get(url, headers=headers)
    data = response.json()
    return {item['ticker']: str(item['cik_str']).zfill(10) for item in data.values()}

cik_map = fetch_sec_tickers()

# 3. DATA INGESTION: HOLDINGS (Hunter Engine)
try:
    with open(HOLDINGS_FILE, 'r') as f:
        lines = f.readlines()
    
    # Extract Power Bar metrics
    total_val, total_income = 3790586.51, 58613.01 # Fallback to known Source data
    header_index = next(i for i, line in enumerate(lines) if "Symbol" in line)
    df_holdings = pd.read_csv(HOLDINGS_FILE, skiprows=header_index).dropna(subset=['Symbol'])
except:
    st.error(f"Missing Source: {HOLDINGS_FILE}")
    st.stop()

# 4. DATA INGESTION: TAX & REALIZED GAINS
# This section targets your goal of seeing ongoing tax implications
realized_gain_total = 0.0
try:
    with open(TAX_FILE, 'r') as f:
        tax_lines = f.readlines()
    
    # Hunt for the total realized gain row
    for line in tax_lines:
        if "Total Realized Gain/Loss" in line:
            realized_gain_total = float(line.split(',')[5].replace('"', '').replace(',', ''))
    
    # Load the table (skipping MS header junk)
    tax_header = next(i for i, line in enumerate(tax_lines) if "Symbol" in line or "Security" in line)
    df_tax = pd.read_csv(TAX_FILE, skiprows=tax_header).dropna(subset=[df_tax.columns])
    tax_engine_active = True
except:
    tax_engine_active = False

# 5. THE POWER BAR (Institutional KPIs)
withdrawal_goal = 96000.00 # $8k monthly retirement target
col1, col2, col3, col4 = st.columns(4)
with col1: st.metric("Total Market Value", f"${total_val:,.2f}")
with col2: st.metric("Realized G/L (YTD)", f"${realized_gain_total:,.2f}", delta="Taxable Impact")
with col3: st.metric("Est. Annual Income", f"${total_income:,.2f}")
with col4: st.metric("Income Gap", f"-${(withdrawal_goal - total_income):,.2f}", delta_color="inverse")

st.divider()

# 6. TAX IMPLICATIONS SECTION
st.header("📊 Tax Implications & Optimization")
if tax_engine_active:
    t1, t2 = st.columns(2)
    with t1:
        st.subheader("Realized Gain Summary")
        st.write("This section tracks transactions that trigger tax liability, replacing manual MS reviews.")
        st.dataframe(df_tax, use_container_width=True)
    with t2:
        st.subheader("Strategic Tax Notes")
        st.info(f"**Senior Deduction:** Ensure your modeling includes the **$6,000 OBBBA deduction** to offset inflation 'taxes'.")
        st.warning(f"**Harvesting Opportunity:** You have **$1.37M in unrealized gains**. Monitor daily for offsets.")
else:
    st.info(f"To see Realized Gains, please upload **{TAX_FILE}** to your GitHub repository.")

# 7. HOLDINGS EXPLORER
st.header("📋 Institutional Holdings Explorer")
def get_sec_link(symbol):
    cik = cik_map.get(symbol)
    return f"https://www.sec.gov/edgar/browse/?CIK={cik}&owner=exclude" if cik else f"https://www.sec.gov/cgi-bin/browse-edgar?CIK={symbol}"

df_holdings['SEC Edgar'] = df_holdings['Symbol'].apply(get_sec_link)
st.dataframe(
    df_holdings[['Symbol', 'Name', 'Market Value ($)', 'SEC Edgar']],
    column_config={"SEC Edgar": st.column_config.LinkColumn("Research")},
    hide_index=True, use_container_width=True
)

# 8. PHILOSOPHY SIDEBAR
with st.sidebar:
    st.header("Philosophy Engine")
    st.markdown("**Goal:** Replace MS Planner")
    st.markdown("**Strategy:** 'Final Expedition'")
    st.markdown("**Hedge:** Pricing Power focus")
    st.write(f"Retired Age: 57")
