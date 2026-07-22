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
own frontend forces it to auto-scroll to the bottom on mount, and keeps
re-asserting that any time the container resizes while it still thinks
the user is "at the bottom". Pages without st.chat_input get a plain
data-testid="stMain" section instead, with no such behavior -- included
below as a fallback selector so this keeps working if a page's chat_input
is ever removed.

To avoid a visible flash of the wrong position: hide_main_for_scroll_fix()
(called from app.py BEFORE the page script runs, via a plain st.markdown
<style> tag -- CSS takes effect immediately on paint, no JS load delay,
unlike a <script> tag inserted the same way, which browsers refuse to
execute) hides the scroll container the instant it exists, before
Streamlit's own auto-scroll-to-bottom can ever be seen.

Heavier pages (Dashboard in particular -- holdings scoring, EDGAR calls,
a Plotly chart that does its own async layout pass after mount) can keep
triggering Streamlit's native re-snap-to-bottom several times in the
moments right after our first correction, each one a brief, visible
bounce if we reveal too early. So force_scroll_to_top() and
scroll_to_element() don't reveal on the first successful correction --
they keep correcting while hidden until the position has actually held
steady on its own (without needing to be re-forced) for several
consecutive checks, and only reveal then. A short hard cap on how long
they'll stay hidden is a safety net in case something never truly
settles, so the page can't get stuck invisible.

A content-height-stabilization heuristic (stop once scrollHeight hasn't
changed for ~1s) was tried first for the long-run hold and wasn't
reliable on its own -- some pages have a brief pause between loading
phases that looks like "settled" but isn't. A fixed hold window (e.g.
12s) wasn't reliable either -- live testing showed Streamlit's own
re-snap-to-bottom firing anywhere from a few seconds up to 30+ seconds
after load. Rather than keep guessing at a number, the long-run
correction below holds the position indefinitely: keep forcing it on
every tick, with NO fixed expiry, and rely entirely on cancelling the
instant the user manually scrolls/touches/drags the container. This is
safe because the injected iframe (and its interval) is destroyed on
Streamlit's next rerun anyway (components.html() re-creates it fresh
each script run), so there's no risk of it running forever in the
background -- it only lives as long as the current render of the page
does, and backs off immediately the moment the user actually wants to
scroll.
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

# How many consecutive 100ms ticks the position must hold on its own
# (i.e. nothing fought us that tick) before we reveal the content. 6
# ticks ~= 600ms of quiet -- long enough to absorb the handful of
# re-snap bounces a heavy page like Dashboard can trigger right after
# our first correction.
_STABLE_TICKS_BEFORE_REVEAL = 6

# Hard cap on how long we'll stay hidden waiting for things to settle,
# in case something never truly stabilizes -- a safety net, not the
# normal path, so the page can never get stuck invisible.
_MAX_HIDE_MS = 4000

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
    scroll_to_element) is responsible for removing this rule once the
    position has actually settled -- see both docstrings below. Because
    app.py only calls this when it already knows one of those two will
    run before the script finishes, the content never stays hidden for
    longer than each function's own hard cap.
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
    Keeps forcing scrollTop=0 while hidden (see module docstring) until
    the position has held on its own for several consecutive checks, THEN
    removes the hiding rule installed by hide_main_for_scroll_fix() -- so
    the container only ever becomes visible already sitting at the top,
    settled, with no visible bounce. After revealing, keeps holding the
    position indefinitely (Streamlit's own behavior can still re-assert
    itself much later on a heavy page), backing off immediately the
    instant it detects the user manually scrolling. height=0 keeps the
    iframe invisible and out of the page's layout.
    """
    components.html(
        f"""<script>
        {_GET_SCROLL_CONTAINER_JS}
        {_REVEAL_JS}
        window.parent.scrollTo(0, 0);
        var _stop = false;
        var _revealed = false;
        var _stableTicks = 0;
        var _hideStart = Date.now();
        var _cancel = function() {{ _stop = true; }};
        var _c0 = _getScrollContainer();
        if (_c0) {{
            ['wheel', 'touchstart', 'mousedown'].forEach(function(evt) {{
                _c0.addEventListener(evt, _cancel, {{once: true, passive: true}});
            }});
        }}
        function _tick() {{
            if (_stop) {{ clearInterval(_iv); return; }}
            var c = _getScrollContainer();
            if (c && c.scrollTop !== 0) {{
                c.scrollTop = 0;
                _stableTicks = 0;
            }} else {{
                _stableTicks++;
            }}
            if (!_revealed && (_stableTicks >= {_STABLE_TICKS_BEFORE_REVEAL}
                                || Date.now() - _hideStart > {_MAX_HIDE_MS})) {{
                _revealMain();
                _revealed = true;
            }}
        }}
        _tick();
        var _iv = setInterval(_tick, 100);
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

    Same settle-before-reveal approach as force_scroll_to_top(): keeps
    re-applying scrollIntoView() while hidden until the anchor's position
    has held steady (its top edge stays within a couple pixels of target
    across consecutive checks) before removing the hiding rule, then keeps
    holding indefinitely afterward, backing off immediately if the user
    manually scrolls.

    Also sets st.session_state['_scroll_to_element_fired'] -- app.py reads
    (and clears) this after the page script finishes to decide whether to
    ALSO call force_scroll_to_top() for a navigation that happened on the
    same run. Both functions hold their target indefinitely once revealed
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
        var _stop = false;
        var _revealed = false;
        var _stableTicks = 0;
        var _hideStart = Date.now();
        var _lastTop = null;
        var _cancel = function() {{ _stop = true; }};
        var _c0 = _getScrollContainer();
        if (_c0) {{
            ['wheel', 'touchstart', 'mousedown'].forEach(function(evt) {{
                _c0.addEventListener(evt, _cancel, {{once: true, passive: true}});
            }});
        }}
        function _tick() {{
            if (_stop) {{ clearInterval(_iv); return; }}
            var el = window.parent.document.getElementById("{anchor_id}");
            if (!el) return;
            var top = el.getBoundingClientRect().top;
            if (Math.abs(top) > 2) {{
                el.scrollIntoView({{block: "start", behavior: _revealed ? "smooth" : "auto"}});
                _stableTicks = 0;
            }} else {{
                _stableTicks++;
            }}
            if (!_revealed && (_stableTicks >= {_STABLE_TICKS_BEFORE_REVEAL}
                                || Date.now() - _hideStart > {_MAX_HIDE_MS})) {{
                _revealMain();
                _revealed = true;
            }}
        }}
        _tick();
        var _iv = setInterval(_tick, 100);
        setTimeout(function() {{ clearInterval(_iv); }}, {_SAFETY_CAP_MS});
        </script>""",
        height=0,
    )
