"""
ui_utils.py — small, reusable Streamlit UI workarounds.

Scroll behavior (#76 / #75 follow-up): two distinct helpers for two
distinct situations, which used to be conflated into a single
force_scroll_to_top() called unconditionally at the end of every page.
That meant literally any interaction on a page -- a chat message, a sort
click, a slider drag -- re-forced the viewport back to the absolute page
top, fighting the user's own scrolling. Replaced with:

  - force_scroll_to_top(): now called centrally from app.py, only when the
    user has actually just navigated to a different page (not on every
    rerun within a page).
  - scroll_to_element(anchor_id): called by an individual page immediately
    after rendering the results of a specific action (Analyze, Score All,
    Run Screen, etc.), and only on the run where that action was actually
    just triggered -- scrolls that results section into view instead of
    the page top, and doesn't fire again on unrelated reruns (e.g.
    interacting with a chat box further down), so the user stays free to
    scroll wherever they want after that.

Real scroll container (found by inspecting the live app's DOM via Claude
in Chrome, since our first pass at this -- a plain window.scrollTo(0,0) --
turned out to silently do nothing): Streamlit renders the whole app inside
a same-origin iframe, and on any page that includes st.chat_input, wraps
the main content in a special section with
data-testid="stAppScrollToBottomContainer" that has its OWN overflow-y:
auto (the document/window itself doesn't scroll at all in this layout).
That container is a chat-app-style "stick to bottom" widget -- Streamlit's
own frontend forces it to auto-scroll to the bottom on mount and
re-asserts that any time content resizes while it thinks the user is "at
bottom", via an internal polling loop that keeps running for a second or
two after the page settles. A single corrective scrollTop=0 fired once
loses that race. Pages without st.chat_input get a plain
data-testid="stMain" section instead, with no such behavior -- included
below as a fallback selector so this keeps working if a page's chat_input
is ever removed.
"""

import streamlit.components.v1 as components

# Shared JS: locate the real scrolling container inside the app's own
# window (one level up from the iframe this script itself runs in).
_GET_SCROLL_CONTAINER_JS = """
function _getScrollContainer() {
    var doc = window.parent.document;
    return doc.querySelector('[data-testid="stAppScrollToBottomContainer"]')
        || doc.querySelector('[data-testid="stMain"]');
}
"""


def force_scroll_to_top():
    """
    Resets the browser viewport to the top of the page. Intended to be
    called once per genuine page navigation (see app.py, which tracks the
    previously-rendered page in session_state and only calls this when
    that page has changed) -- NOT unconditionally on every rerun, which
    would fight the user's own scrolling on every later interaction.

    Mechanism: st.components.v1.html() renders in a same-origin iframe, so
    a tiny script inside it can reach window.parent.document and reset
    scroll position on the real scrolling container (see module docstring
    -- it's a specific inner <section>, not the window/document itself).
    Streamlit's own "scroll chat_input into view on mount" behavior keeps
    re-asserting itself for a second or two via its own polling loop, so
    this re-applies scrollTop=0 on an interval for a few seconds rather
    than firing once, then stops -- after that the user is free to scroll
    wherever they want. height=0 keeps the iframe invisible and out of
    the page's layout.
    """
    components.html(
        f"""<script>
        {_GET_SCROLL_CONTAINER_JS}
        window.parent.scrollTo(0, 0);
        var _start = Date.now();
        var _iv = setInterval(function() {{
            var c = _getScrollContainer();
            if (c && c.scrollTop !== 0) {{ c.scrollTop = 0; }}
            if (Date.now() - _start > 3000) {{ clearInterval(_iv); }}
        }}, 50);
        </script>""",
        height=0,
    )


def scroll_to_element(anchor_id: str):
    """
    Scrolls a specific element into view instead of resetting to the
    absolute page top. Pair with a marker element rendered just before a
    results section, e.g.:

        st.markdown(f'<div id="{anchor_id}"></div>', unsafe_allow_html=True)
        # ... render the actual results ...
        if just_ran_the_action:
            scroll_to_element(anchor_id)

    Only call this on the specific rerun where the triggering action (a
    button click, typically) actually just fired -- not on every rerun
    that happens to redisplay cached results, or every interaction with
    this scrolls the user back to the same spot regardless of what
    they're doing elsewhere on the page.

    Same re-assertion approach as force_scroll_to_top(): scrollIntoView()
    correctly walks up to the real scrolling container on its own, but a
    single call can still lose the race against Streamlit's own
    scroll-to-bottom-on-mount behavior on chat_input pages, so this
    repeats the call for a couple seconds before giving up control.
    """
    components.html(
        f"""<script>
        var _start = Date.now();
        var _iv = setInterval(function() {{
            var el = window.parent.document.getElementById("{anchor_id}");
            if (el) {{ el.scrollIntoView({{behavior: "smooth", block: "start"}}); }}
            if (Date.now() - _start > 2000) {{ clearInterval(_iv); }}
        }}, 200);
        </script>""",
        height=0,
    )
