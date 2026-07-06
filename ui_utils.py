"""
ui_utils.py — small, reusable Streamlit UI workarounds.

Currently just one: force_scroll_to_top(), a fix for a known st.chat_input
quirk (see function docstring). Kept separate from claude_utils.py since
this is a UI/browser concern, not an Anthropic API concern.
"""

import streamlit.components.v1 as components


def force_scroll_to_top():
    """
    Workaround for a known st.chat_input behavior: any page with a chat
    input pinned to the bottom of the viewport tends to auto-scroll the
    whole page down to reveal it on load/rerun, rather than staying at
    the top where the user actually landed. Call this once, near the end
    of any page that uses st.chat_input.

    Mechanism: st.components.v1.html() renders in a same-origin iframe,
    so a tiny script inside it can reach into window.parent (the actual
    browser tab) and reset scroll position after Streamlit's own scroll
    behavior has already fired. height=0 keeps it invisible and out of
    the page's layout.
    """
    components.html(
        "<script>window.parent.scrollTo(0, 0);</script>",
        height=0,
    )
