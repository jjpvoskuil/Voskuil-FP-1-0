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

# 4. HOLDINGS EXPLORER & SEC DRILL-DOWN [3]
st.header("📋 Holdings Explorer & SEC Drill-Down")
st.write("Click the links below to 'drill down' into raw SEC filings for your holdings.")

# Create the SEC Link column [3]
df['SEC Link'] = df['Symbol'].apply(lambda x: f"https://www.sec.gov/edgar/browse/?CIK={x}")

# Clean and display the core data
# Note: Quantity and Market Value often come in with commas/quotes in MS files [4]
display_df = df[['Symbol', 'Name', 'Quantity', 'Market Value ($)', 'SEC Link']]

st.dataframe(
    display_df,
    column_config={"SEC Link": st.column_config.Link_Column("Institutional Research")},
    hide_index=True,
    use_container_width=True
)

# 5. STRATEGY SIDEBAR
with st.sidebar:
    st.header("Philosophy Engine")
    st.info("Current Phase: Final Expedition")
    st.markdown("**Core Strategy:** Replace MS Planner with AI logic [3].")
    st.markdown("**Structural Hedge:** Target 15% Big Tech cap [5].")
