"""
ui_utils.py — small, reusable Streamlit UI workarounds.

Scroll behavior (#76): two distinct helpers for two distinct situations,
which used to be conflated into a single force_scroll_to_top() called
unconditionally at the end of every page. That meant literally any
interaction on a page -- a chat message, a sort click, a slider drag --
re-forced the viewport back to the absolute page top, fighting the user's
own scrolling and, on slower-rendering pages, sometimes losing a timing
race against Streamlit's native "scroll chat_input into view" behavior and
landing at the bottom instead. Replaced with:

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
"""

import streamlit.components.v1 as components


def force_scroll_to_top():
    """
    Resets the browser viewport to the top of the page. Intended to be
    called once per genuine page navigation (see app.py, which tracks the
    previously-rendered page in session_state and only calls this when
    that page has changed) -- NOT unconditionally on every rerun, which
    would fight the user's own scrolling on every later interaction.

    Mechanism: st.components.v1.html() renders in a same-origin iframe, so
    a tiny script inside it can reach into window.parent (the actual
    browser tab) and reset scroll position after Streamlit's own scroll
    behavior (e.g. auto-revealing a bottom-pinned st.chat_input) has
    already fired. height=0 keeps it invisible and out of the page's
    layout.
    """
    components.html(
        "<script>window.parent.scrollTo(0, 0);</script>",
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
    """
    components.html(
        f"""<script>
        var el = window.parent.document.getElementById("{anchor_id}");
        if (el) {{ el.scrollIntoView({{behavior: "smooth", block: "start"}}); }}
        </script>""",
        height=0,
    )
