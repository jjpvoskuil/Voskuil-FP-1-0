import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(
    page_title="MS Financial Modeler | Voskuil FP",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Hide Streamlit chrome to give the iframe maximum space
st.markdown("""
<style>
    header[data-testid="stHeader"] { display: none; }
    .block-container { padding: 0 !important; margin: 0 !important; max-width: 100% !important; }
    section[data-testid="stSidebar"] { display: none; }
</style>
""", unsafe_allow_html=True)

MGP_URL = "https://ms.moneyguidepro.com/morganstanley-darst/Guests.aspx"

# Thin top bar with nav back to dashboard
col1, col2, col3 = st.columns([1, 6, 1])
with col1:
    st.page_link("app.py", label="← Dashboard", icon="🛡️")
with col2:
    st.markdown(
        "<div style='text-align:center; padding:6px 0; font-size:0.9em; color:#888;'>"
        "Morgan Stanley · MoneyGuidePro Financial Plan"
        "</div>",
        unsafe_allow_html=True,
    )
with col3:
    st.link_button("↗ Open full page", MGP_URL)

# Full-height iframe — fills the remaining viewport
# Use viewport height minus ~50px for the nav bar above
components.html(
    f"""
    <iframe
        src="{MGP_URL}"
        style="
            width: 100%;
            height: calc(100vh - 52px);
            border: none;
            display: block;
        "
        allow="fullscreen"
        id="mgp-frame"
    ></iframe>
    """,
    height=900,
    scrolling=False,
)

