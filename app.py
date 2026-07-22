import streamlit as st
from ui_utils import (
    disable_smooth_scroll,
    force_scroll_to_top,
    hide_main_for_scroll_fix,
    install_instant_nav_hide,
    mark_render_complete,
)

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
    st.Page("app_pages/0_Dashboard.py",          title="Dashboard",            icon="🛡️"),
    st.Page("app_pages/7_Equity_Scout_EDGAR.py",  title="Equity Scout",         icon="🔍"),
    st.Page("app_pages/8_Market_Screener_EDGAR.py", title="Market Screener",    icon="📡"),
    st.Page("app_pages/9_Compare_Stocks_EDGAR.py",  title="Compare Stocks",     icon="⚖️"),
    st.Page("app_pages/3_Financial_Modeler.py",   title="Financial Modeler",    icon="🏔️"),
    st.Page("app_pages/4_MS_Financial_Modeler.py",title="MS Financial Modeler", icon="🏦"),
    st.Page("app_pages/5_Downloads.py",           title="Downloads",            icon="⬇️"),
    st.Page("app_pages/6_Punch_List.py",          title="Punch List",           icon="✅"),
    # st.Page("app_pages/1_Equity_Scout.py",      title="Equity Scout",         icon="🔍"),   # retired — code kept for reference
    # st.Page("app_pages/2_Market_Screener.py",   title="Market Screener",      icon="📡"),   # retired — code kept for reference
])

# ── Scroll to top on page navigation only (#76) ───────────────────────────
# Streamlit's st.navigation is a single-page-app model -- switching pages
# does NOT reset browser scroll position on its own the way a traditional
# multi-page site's full page load would. Previously each page called a
# scroll-to-top fix unconditionally at the end of its own script, which
# fired on every single rerun (any button click, chat message, sort
# click...), not just navigation -- fighting the user's own scrolling.
# Centralized here instead: compare the page actually being rendered this
# run against the last one recorded in session_state, and only scroll to
# top when they differ, i.e. a genuine navigation just happened.
_current_page_key = pg.url_path
_navigated = st.session_state.get("_last_page_key") != _current_page_key
st.session_state["_last_page_key"] = _current_page_key

# Permanently disables smooth-scroll animation on the real scroll
# container -- see disable_smooth_scroll() docstring in ui_utils.py for
# why this turned out to be necessary: Streamlit's own auto-scroll-to-
# bottom animates over several hundred ms rather than jumping instantly,
# and no amount of "catch the scroll event and correct" JS can reliably
# cancel an already-in-progress compositor-driven animation. Removing
# the ability to animate at all removes the fight. Called every run,
# unconditionally, independent of the hide/reveal cycle below.
disable_smooth_scroll()

# Primary hide trigger: a client-side click listener that hides the
# instant a sidebar nav link is clicked, before Streamlit's own routing
# starts and with no server round-trip -- see install_instant_nav_hide()
# docstring in ui_utils.py for why the alternative (a server-sent CSS
# delta, however early in script order) turned out not to be reliably
# fast enough on a real connection. Called every run, unconditionally --
# cheap, and it only actually attaches its listener once per session.
install_instant_nav_hide()

# Secondary/fallback hide trigger for navigations that don't go through
# a sidebar click (browser back/forward, a deep link) -- same latency
# caveat as before applies here, but it's a much rarer path than a
# direct nav-link click. Whichever of force_scroll_to_top()/
# scroll_to_element() ends up firing below is responsible for revealing
# it again once corrected, regardless of which of the two hide triggers
# (if either) actually fired for this run.
if _navigated:
    hide_main_for_scroll_fix()

pg.run()

# Mark that every element this run's page script produced has actually
# been sent to the browser -- Streamlit delivers deltas in script order,
# so this marker can only land in the real DOM after everything before
# it already has. force_scroll_to_top()/scroll_to_element() gate their
# reveal on seeing this (see ui_utils.py module docstring): without it, a
# heavy page whose content streams in over several seconds as separate
# deltas could get revealed mid-stream, which was the actual cause of
# Dashboard's visible bounce -- a quiet gap *between* two deltas looked
# "settled" even though the page was nowhere near done.
mark_render_complete()

# If the page itself just triggered a results-anchor scroll on this same
# run (e.g. arriving here via navigation right as a background scan
# finishes ingesting), let that win instead of also forcing the literal
# page top -- see scroll_to_element()'s docstring in ui_utils.py for why
# running both at once would fight forever instead of settling anywhere.
_page_scrolled_to_results = st.session_state.pop("_scroll_to_element_fired", False)
if _navigated and not _page_scrolled_to_results:
    force_scroll_to_top()
