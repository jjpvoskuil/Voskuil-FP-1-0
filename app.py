import streamlit as st

# ── Initialize user profile defaults at app startup ──────────────────────
# These are overwritten when the Financial Modeler page is visited.
# Setting them here ensures Claude has sensible values on any page visited first.
_fp_defaults = {
    "fp_age":                57,
    "fp_plan_to_age":        90,
    "fp_spouse_age":         54,
    "fp_portfolio_val":      3_790_000,
    "fp_monthly_withdrawal": 8_000,
    "fp_annual_passive":     96_000,
    "fp_cash_buffer":        96_000,
    "fp_ss_monthly":         3_200,
    "fp_ss_start_age":       67,
    "fp_spouse_ss":          2_200,
    "fp_inflation":          4.0,
    "fp_base_return":        6.0,
    "fp_pessimistic_return": 3.5,
    "fp_bear_return":        1.0,
    "fp_survivor_monthly":   5_500,
}
for _k, _v in _fp_defaults.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

pg = st.navigation([
    st.Page("pages/0_Dashboard.py",          title="Dashboard",            icon="🛡️"),
    st.Page("pages/7_Equity_Scout_EDGAR.py",  title="Equity Scout",         icon="🔍"),
    st.Page("pages/8_Market_Screener_EDGAR.py", title="Market Screener",    icon="📡"),
    st.Page("pages/9_Compare_Stocks_EDGAR.py",  title="Compare Stocks",     icon="⚖️"),
    st.Page("pages/3_Financial_Modeler.py",   title="Financial Modeler",    icon="🏔️"),
    st.Page("pages/4_MS_Financial_Modeler.py",title="MS Financial Modeler", icon="🏦"),
    st.Page("pages/5_Downloads.py",           title="Downloads",            icon="⬇️"),
    st.Page("pages/6_Punch_List.py",          title="Punch List",           icon="✅"),
    # st.Page("pages/1_Equity_Scout.py",      title="Equity Scout",         icon="🔍"),   # retired — code kept for reference
    # st.Page("pages/2_Market_Screener.py",   title="Market Screener",      icon="📡"),   # retired — code kept for reference
])
pg.run()
