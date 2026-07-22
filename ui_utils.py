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
own frontend forces it to auto-scroll to the bottom on mount, which paints
BEFORE our own corrective JS gets a chance to run (our script lives inside
a components.html() iframe, which loads slightly after the main content
does) -- so simply correcting the position after the fact, no matter how
fast, produces a visible "lands at bottom, then jumps to top" flash. Pages
without st.chat_input get a plain data-testid="stMain" section instead,
with no such behavior -- included below as a fallback selector so this
keeps working if a page's chat_input is ever removed.

To eliminate that flash: hide_main_for_scroll_fix() (called from app.py
BEFORE the page script runs, via a plain st.markdown <style> tag -- CSS
takes effect immediately on paint, no JS load delay, unlike a <script>
tag inserted the same way, which browsers refuse to execute) hides the
scroll container the instant it exists, before Streamlit's own
auto-scroll-to-bottom can ever be seen. force_scroll_to_top() and
scroll_to_element() then correct the scroll position FIRST and only
reveal the container after that correction has been applied -- so the
user only ever sees the page already sitting at the right position, never
the wrong one flashing past first.

A content-height-stabilization heuristic (stop once scrollHeight hasn't
changed for ~1s) was tried first and wasn't reliable -- some pages have a
brief pause between loading phases that looks like "settled" but isn't,
letting Streamlit's native behavior win once real growth resumes after
our loop had already given up. A fixed hold window (e.g. 12s) wasn't
reliable either -- live testing against the deployed app showed
Streamlit's own re-snap-to-bottom firing anywhere from a few seconds up
to 30+ seconds after load, seemingly tied to some internal event (focus,
a delayed resize, etc.) rather than a fixed content-settle time. Rather
than keep guessing at a number, this just holds the position
indefinitely: keep forcing the position on every tick, with NO fixed
expiry, and rely entirely on cancelling the instant the user manually
scrolls/touches/drags the container. This is safe because the injected
iframe (and its interval) is destroyed on Streamlit's next rerun anyway
(components.html() re-creates it fresh each script run), so there's no
risk of it running forever in the background -- it only lives as long as
the current render of the page does, and backs off immediately the
moment the user actually wants to scroll.
"""

import streamlit as st
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

# Safety-net cap on how long the corrective interval can run, in case a
# user session sits on a freshly-navigated page for a very long time
# without ever touching it. Not meant to be reached in normal use -- the
# real stop condition is the user manually scrolling (see _cancel below).
_SAFETY_CAP_MS = 5 * 60 * 1000

# id on the <style> tag hide_main_for_scroll_fix() injects, so the
# corrective scripts below can find and remove it once they've actually
# applied the right scroll position.
_HIDE_STYLE_ID = "_ui_scroll_fix_hide"


def hide_main_for_scroll_fix():
    """
    Hides the main content area via a plain CSS rule -- call this from
    app.py BEFORE running the page script, whenever the run is about to
    end in a call to force_scroll_to_top() or scroll_to_element() (i.e.
    whenever a genuine navigation just happened). A <style> tag inserted
    via st.markdown(unsafe_allow_html=True) takes effect immediately on
    paint (unlike a <script> tag inserted the same way, which browsers
    silently refuse to execute), so this closes the gap between "page
    starts rendering" and "our corrective JS in its components.html()
    iframe gets a chance to run" with something the browser applies for
    free, before Streamlit's own auto-scroll-to-bottom behavior can ever
    be visually seen.

    Whichever correction function ends up running (force_scroll_to_top or
    scroll_to_element) is responsible for removing this rule once it's
    applied the right position -- see both docstrings below. Because
    app.py only calls this when it already knows one of those two will
    run before the script finishes, the content never stays hidden.
    """
    st.markdown(
        f'<style id="{_HIDE_STYLE_ID}">'
        '[data-testid="stAppScrollToBottomContainer"], [data-testid="stMain"]'
        " { visibility: hidden !important; } </style>",
        unsafe_allow_html=True,
    )


# Shared JS: remove the hiding rule installed by hide_main_for_scroll_fix()
# above, if present. Safe to call even if it was never inserted (e.g. a
# page calls scroll_to_element() on a run that wasn't a navigation, so
# app.py never called hide_main_for_scroll_fix()).
_REVEAL_JS = """
function _revealMain() {
    var doc = window.parent.document;
    var style = doc.getElementById("%s");
    if (style) { style.remove(); }
}
""" % _HIDE_STYLE_ID


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
    Corrects the position FIRST, then removes the hiding rule installed by
    hide_main_for_scroll_fix() -- so the container only ever becomes
    visible already sitting at the top, no flash of the wrong position.
    Streamlit's own "scroll chat_input into view on mount" behavior keeps
    re-asserting itself for as long as page content keeps growing, so
    this keeps forcing scrollTop=0 indefinitely after that, but backs off
    the instant it detects the user manually scrolling -- so a deliberate
    scroll is never overridden. height=0 keeps the iframe invisible and
    out of the page's layout.
    """
    components.html(
        f"""<script>
        {_GET_SCROLL_CONTAINER_JS}
        {_REVEAL_JS}
        window.parent.scrollTo(0, 0);
        var _c0 = _getScrollContainer();
        if (_c0) {{ _c0.scrollTop = 0; }}
        _revealMain();
        var _stop = false;
        var _cancel = function() {{ _stop = true; }};
        if (_c0) {{
            ['wheel', 'touchstart', 'mousedown'].forEach(function(evt) {{
                _c0.addEventListener(evt, _cancel, {{once: true, passive: true}});
            }});
        }}
        var _iv = setInterval(function() {{
            if (_stop) {{ clearInterval(_iv); return; }}
            var c = _getScrollContainer();
            if (c && c.scrollTop !== 0) {{ c.scrollTop = 0; }}
        }}, 100);
        setTimeout(function() {{ clearInterval(_iv); }}, {_SAFETY_CAP_MS});
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

    Corrects the position FIRST, then removes any hiding rule installed by
    hide_main_for_scroll_fix() (safe no-op if it was never inserted --
    e.g. this run wasn't a navigation) -- same flash-free approach as
    force_scroll_to_top(). Keeps re-applying scrollIntoView() indefinitely
    afterward (in case content above the anchor is still growing and
    shifting its position), backing off immediately if the user manually
    scrolls.

    Also sets st.session_state['_scroll_to_element_fired'] -- app.py reads
    (and clears) this after the page script finishes to decide whether to
    ALSO call force_scroll_to_top() for a navigation that happened on the
    same run. Both functions hold their target indefinitely once fired
    (see module docstring), so if a genuine navigation and a "results just
    landed" event coincide on the same run -- e.g. arriving at Market
    Screener while a background scan happens to finish ingesting on that
    exact run -- calling both would mean two independent corrective loops
    permanently fighting each other over two different scroll targets,
    producing a visible flicker between them forever. The results scroll
    wins in that case (matching what the user actually asked for: see the
    top of the results, not the literal top of the page), so app.py skips
    its own top-scroll whenever this fired first.
    """
    st.session_state["_scroll_to_element_fired"] = True
    components.html(
        f"""<script>
        {_GET_SCROLL_CONTAINER_JS}
        {_REVEAL_JS}
        function _apply() {{
            var el = window.parent.document.getElementById("{anchor_id}");
            if (el) {{ el.scrollIntoView({{block: "start"}}); }}
            return el;
        }}
        _apply();
        _revealMain();
        var _stop = false;
        var _cancel = function() {{ _stop = true; }};
        var _c0 = _getScrollContainer();
        if (_c0) {{
            ['wheel', 'touchstart', 'mousedown'].forEach(function(evt) {{
                _c0.addEventListener(evt, _cancel, {{once: true, passive: true}});
            }});
        }}
        var _iv = setInterval(function() {{
            if (_stop) {{ clearInterval(_iv); return; }}
            var el = window.parent.document.getElementById("{anchor_id}");
            if (el) {{ el.scrollIntoView({{behavior: "smooth", block: "start"}}); }}
        }}, 150);
        setTimeout(function() {{ clearInterval(_iv); }}, {_SAFETY_CAP_MS});
        </script>""",
        height=0,
    )
