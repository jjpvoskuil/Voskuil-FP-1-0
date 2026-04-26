import streamlit as st
import pandas as pd
import os

st.set_page_config(page_title="Voskuil FP 1.0", layout="wide")
st.title("🛡️ Voskuil FP 1.0: Sovereign Wealth Dashboard")

# 1. THE FILE SCANNER
# This part looks at your GitHub folder to see exactly what files are there.
st.sidebar.header("System Status")
files_in_folder = os.listdir('.')
st.sidebar.write("Files detected in cloud:", files_in_folder)

FILENAME = 'Current MS holdings - 042526.csv'

# 2. THE SMART INGESTION ENGINE
# Instead of guessing, we search for the row that contains your actual stock data.
try:
    # First, we read the file to find the header row
    raw_data = pd.read_csv(FILENAME, header=None)
    
    # We look for the row that contains the word 'Symbol' [3]
    header_row_index = raw_data[raw_data.apply(lambda r: r.astype(str).str.contains('Symbol').any(), axis=1)].index
    
    # Now we read it correctly starting from that row
    df = pd.read_csv(FILENAME, skiprows=header_row_index)
    df = df.dropna(subset=['Symbol'])
    
    st.sidebar.success("✅ Data Connection Established")

except Exception as e:
    st.error(f"⚠️ Technical Error: {e}")
    st.info("Check the sidebar to see if the filename in GitHub matches 'Current MS holdings - 042526.csv' exactly.")
    st.stop()

# 3. INSTITUTIONAL KPIs (Using actual data from your MS Export [2, 4])
total_value = 3790586.51
unrealized_gain = 1369802.57
est_income = 58613.01
withdrawal_goal = 96000.00 

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

# 4. THE HOLDINGS EXPLORER (Drill-Down [5])
st.header("📋 Holdings Explorer & SEC Drill-Down")

# Create the SEC Link column for institutional research
df['SEC Link'] = df['Symbol'].apply(lambda x: f"https://www.sec.gov/edgar/browse/?CIK={x}")

# Select the columns from your MS file to display [3, 6]
display_cols = ['Symbol', 'Name', 'Quantity', 'Market Value ($)', 'Unrealized Gain/Loss ($)', 'SEC Link']
st.dataframe(
    df[display_cols],
    column_config={"SEC Link": st.column_config.Link_Column("SEC Filing Data")},
    hide_index=True,
    use_container_width=True
)

st.sidebar.markdown(f"**Retirement Age:** 57")
st.sidebar.markdown(f"**Strategy:** Final Expedition")
