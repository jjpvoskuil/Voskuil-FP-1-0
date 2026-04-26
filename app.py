import streamlit as st
import pandas as pd

# 1. Setup and Project Identity
st.set_page_config(page_title="Voskuil FP 1.0", layout="wide")
st.title("🛡️ Voskuil FP 1.0: Sovereign Wealth Dashboard")

# 2. Data Ingestion Engine (The "Compustat" Layer)
# We skip the first few header rows of the MS CSV to find the actual data [1, 3]
try:
    df = pd.read_csv('Current MS holdings - 042526.csv', skiprows=6)
    # Clean the column names and remove empty rows/columns common in MS exports [1]
    df = df.dropna(subset=['Symbol'])
except:
    st.error("Data Ingestion Error: Please ensure 'Current MS holdings - 042526.csv' is uploaded to GitHub.")
    st.stop()

# 3. Institutional KPIs (Power Bar) [3, 4]
total_value = 3790586.51
unrealized_gain = 1369802.57
est_income = 58613.01
withdrawal_goal = 96000.00 # $8,000/mo [5]

col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Total Market Value", f"${total_value:,.2f}")
with col2:
    st.metric("Total Unrealized Gain", f"${unrealized_gain:,.2f}")
with col3:
    st.metric("Est. Annual Income", f"${est_income:,.2f}")
with col4:
    st.metric("Income Gap", f"-${(withdrawal_goal - est_income):,.2f}", delta_color="inverse")

st.divider()

# 4. Strategic Philosophy Input Page [2]
with st.sidebar:
    st.header("Philosophy Engine")
    st.info("Currently retired at age 57. Strategy: 'Final Expedition' [2].")
    
    strategy = st.text_area("Investment Strategy Definition:", 
                             value="Pettis-Dalio Framework: 15% Tech Cap, Pricing Power focus, 5-7.5% Real Assets [6-8].")
    
    st.markdown("### Legacy Machine Goals")
    st.write("- Avoid 'Reverse Compounding' [5]")
    st.write("- Hedge for 'Ugly Deleveraging' [7]")

# 5. The Holdings Explorer with SEC Drill-Down [2]
st.header("📋 Holdings Explorer & SEC Drill-Down")
st.write("This table replicates your Morgan Stanley view but adds one-click access to SEC filings.")

# Create the SEC Link column [2]
df['SEC Link'] = df['Symbol'].apply(lambda x: f"https://www.sec.gov/edgar/browse/?CIK={x}")

# Select and display the core columns you care about [2, 9]
display_df = df[['Symbol', 'Name', 'Quantity', 'Market Value ($)', 'Unrealized Gain/Loss ($)', 'SEC Link']]

# Display the data as an interactive table
st.dataframe(
    display_df,
    column_config={
        "SEC Link": st.column_config.Link_Column("SEC Filing Data", help="Institutional Drill-Down [2]")
    },
    hide_index=True,
    use_container_width=True
)

# 6. Concentration Analysis [6]
st.header("🎯 Concentration Risk: The 'Lost Decade' Hedge")
big_tech_symbols = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META']
current_tech_value = df[df['Symbol'].isin(big_tech_symbols)]['Market Value ($)'].str.replace(',', '').astype(float).sum()
tech_pct = (current_tech_value / total_value) * 100

col_a, col_b = st.columns(2)
with col_a:
    st.write(f"### Big Tech Exposure: {tech_pct:.1f}%")
    st.progress(tech_pct / 100)
    st.caption("Strategic Target: Trim from ~24% to 15% to increase Pricing Power [6, 8].")

with col_b:
    st.write("### Recommended Reallocation")
    st.write("- 9% into **Dividend Aristocrats** (e.g., PG, ABBV) [6]")
    st.write("- 5-7.5% into **Real Assets** (Gold/GSG) [7]")
