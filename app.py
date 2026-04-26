import streamlit as st
import pandas as pd
import io

# 1. Page Identity
st.set_page_config(page_title="Voskuil FP 1.0", layout="wide")
st.title("🛡️ Voskuil FP 1.0: Sovereign Wealth Dashboard")

FILENAME = 'Current MS holdings - 042526.csv'

# 2. THE HUNTER ENGINE (Data Ingestion)
try:
    # Read the whole file as text first to find our data
    with open(FILENAME, 'r') as f:
        lines = f.readlines()

    # Hunt for the "Power Bar" numbers (Total Value and Income)
    total_val, total_income = 0.0, 0.0
    for line in lines:
        if "Total Market Value:" in line:
            # Extracts the $3.79M value directly from your MS file [2]
            parts = line.split('"')
            total_val = float(parts[1].replace(',', ''))
        if "Est. Annual Income:" in line:
            # Extracts the $58k income directly [2]
            parts = line.split('"')
            total_income = float(parts[-2].replace(',', ''))

    # Hunt for the row where your stocks actually start
    header_index = 0
    for i, line in enumerate(lines):
        if "Symbol" in line:
            header_index = i
            break
    
    # Load the table starting from that row
    df = pd.read_csv(FILENAME, skiprows=header_index)
    df = df.dropna(subset=['Symbol'])
    
    st.sidebar.success("✅ Compustat Layer Active")

except Exception as e:
    st.error(f"⚠️ Technical Error: {e}")
    st.stop()

# 3. THE POWER BAR (Live from your file)
withdrawal_goal = 96000.00 
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Total Market Value", f"${total_val:,.2f}")
with col2:
    st.metric("Est. Annual Income", f"${total_income:,.2f}")
with col3:
    st.metric("Current Portfolio Yield", f"{(total_income/total_val)*100:.2f}%")
with col4:
    st.metric("Income Gap", f"-${(withdrawal_goal - total_income):,.2f}", delta_color="inverse")

st.divider()

# 4. UNIVERSAL DRILL-DOWN LOGIC
# This function creates a search link for ANY ticker symbol automatically
def make_dynamic_sec_link(symbol):
    # This URL tells the SEC to search for the ticker directly
    return f"https://www.sec.gov/cgi-bin/browse-edgar?CIK={symbol}&action=getcompany"

def make_market_research_link(symbol):
    # This provides a dynamic link to external market data and charts
    return f"https://finance.yahoo.com/quote/{symbol}"

# 5. APPLYING LINKS TO THE FULL HOLDINGS LIST
# This now applies to every symbol found in your MS CSV (e.g., ABBV, MSFT, NVDA, etc.)
df['SEC Link'] = df['Symbol'].apply(make_dynamic_sec_link)
df['Market Link'] = df['Symbol'].apply(make_market_research_link)

# 6. THE DYNAMIC HOLDINGS EXPLORER
st.header("📋 Dynamic Holdings Explorer")
st.write("Every holding below now has live, dynamic links for institutional-grade research.")

# Columns to display from your holdings [1, 2, 4-91]
display_cols = ['Symbol', 'Name', 'Market Value ($)', 'SEC Link', 'Market Link']

st.dataframe(
    df[display_cols],
    column_config={
        "SEC Link": st.column_config.LinkColumn("SEC Filings", help="Raw Corporate Filings"),
        "Market Link": st.column_config.LinkColumn("Market Analysis", help="Live Charts & News")
    },
    hide_index=True,
    use_container_width=True
)

# 7. DISCOVERY ENGINE (Future Goal)
st.divider()
st.subheader("🔎 Strategy-Matched Discovery")
search_ticker = st.text_input("Enter any other Public Company Ticker to analyze against your strategy:")
if search_ticker:
    col_x, col_y = st.columns(2)
    with col_x:
        st.link_button(f"View {search_ticker} SEC Filings", make_dynamic_sec_link(search_ticker))
    with col_y:
        st.link_button(f"View {search_ticker} Market Data", make_market_research_link(search_ticker))

# 8. STRATEGY SIDEBAR
with st.sidebar:
    st.header("Philosophy Engine")
    st.info("Current Phase: Final Expedition")
    st.markdown("**Core Strategy:** Replace MS Planner with AI logic [3].")
    st.markdown("**Structural Hedge:** Target 15% Big Tech cap [5].")
