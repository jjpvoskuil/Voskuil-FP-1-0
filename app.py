import streamlit as st
import pandas as pd

# 1. Setup the Page
st.set_page_config(page_title="Voskuil FP 1.0", layout="wide")
st.title("🛡️ Voskuil FP 1.0: Sovereign Wealth Dashboard")
st.subheader("The 'Final Expedition' Wealth Management Engine")

# 2. Key Portfolio Data (From Source [1, 2])
TOTAL_VALUE = 3790586.51
TOTAL_GAIN = 1369802.57
ANNUAL_INCOME = 58613.01
MONTHLY_WITHDRAWAL_GOAL = 8000.00
ANNUAL_GAP = 37386.99 # ($96k goal - $58.6k actual)

# 3. The Power Bar: Institutional KPIs
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Total Market Value", f"${TOTAL_VALUE:,.2f}")
with col2:
    st.metric("Unrealized Gain", f"${TOTAL_GAIN:,.2f}", delta="Total Alpha")
with col3:
    st.metric("Est. Annual Income", f"${ANNUAL_INCOME:,.2f}")
with col4:
    st.metric("Current Yield", "1.54%")

st.divider()

# 4. Strategy Analysis Sidebar (Pettis-Dalio Frameworks)
with st.sidebar:
    st.header("Strategic Philosophy")
    philosophy = st.text_area("Investment Thesis Input:", 
                                value="Prioritize Pricing Power. Hedge for the 'Lost Decade'. Trim Big Tech to 15% cap.")
    
    st.info(f"**Withdrawal Status:** Your monthly goal is ${MONTHLY_WITHDRAWAL_GOAL:,.0f}. "
            f"Current income leaves a **${ANNUAL_GAP:,.0f} annual gap**.")
    
    if st.button("Run Monte Carlo Analysis"):
        st.warning("Monte Carlo Engine: Simulation logic pending Phase 5...")

# 5. Asset Concentration View (Source [3, 4])
st.header("🎯 Strategic Alignment & Concentration")
col_a, col_b = st.columns(2)

with col_a:
    st.write("### Sector Concentration (Big Tech)")
    # Visualizing the 23.8% Tech tilt discussed in sources
    tech_data = pd.DataFrame({'Sector': ['Big Tech', 'Other'], 'Weight': [23.8, 76.2]})
    st.bar_chart(tech_data.set_index('Sector'))
    st.caption("Target: Reduce Big Tech from 23.8% to 15% for Pricing Power defense.")

with col_b:
    st.write("### Income Gap Analysis")
    st.progress(ANNUAL_INCOME / (MONTHLY_WITHDRAWAL_GOAL * 12))
    st.write(f"Your portfolio currently generates **{ (ANNUAL_INCOME/96000)*100 :.1f}%** of your target retirement income.")

# 6. Holdings Explorer (Placeholder for SEC Drill-Down [5])
st.header("📋 Holdings Explorer")
st.info("SEC Filing Integration: Click any symbol in the next phase to 'drill down' into corporate balance sheets.")
# Placeholder for the data table
st.write("Reading 'Current MS holdings - 042526.csv'...")
# (Note: In a live app, we would use pd.read_csv here to display the table)
