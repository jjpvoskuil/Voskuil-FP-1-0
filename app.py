import streamlit as st

pg = st.navigation([
    st.Page("pages/0_Dashboard.py",          title="Dashboard",            icon="🛡️"),
    st.Page("pages/1_Equity_Scout.py",        title="Equity Scout",         icon="🔍"),
    st.Page("pages/2_Market_Screener.py",     title="Market Screener",      icon="📡"),
    st.Page("pages/3_Financial_Modeler.py",   title="Financial Modeler",    icon="🏔️"),
    st.Page("pages/4_MS_Financial_Modeler.py",title="MS Financial Modeler", icon="🏦"),
    st.Page("pages/5_Downloads.py",           title="Downloads",            icon="⬇️"),
    st.Page("pages/6_Punch_List.py",          title="Punch List",           icon="✅"),
])
pg.run()
