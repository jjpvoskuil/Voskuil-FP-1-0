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

A first attempt at "correct, then wait for N consecutive clean polls
before revealing" (checking every 100ms) still let a heavier page like
Dashboard bounce visibly a few times after reveal. Root cause: Streamlit's
own re-snap runs on its own ~17ms internal loop, much faster than a
100ms poll -- it can flip the position to bottom and get corrected back
again multiple times *between* two of our checks, so a handful of
100ms-spaced "yep, still 0" reads can look falsely stable while a fight
is still actively happening, revealing too early into an unsettled fight
that then continues visibly after reveal.

Fixed by switching from polling to a scroll *event* listener on the
container: every actual scroll (native re-snap, our own correction,
anything) fires a real 'scroll' event synchronously, and correcting the
position inside that same handler, in the same task, happens before the
browser ever paints the intermediate frame -- so no fight, however fast,
can be visually observed. "Settled enough to reveal" is now judged by
elapsed wall-clock time since the last correction was actually needed
(not by a fixed number of polls), so it naturally adapts to how long a
given page's fight actually takes. A hard cap on total hidden time is a
safety net in case something never truly stops fighting, so the page
can't get stuck invisible.

A content-height-stabilization heuristic and a couple of fixed-timeout
approaches were tried before this for the long-run hold (after reveal)
and weren't reliable -- Streamlit's own re-snap-to-bottom has been
observed firing anywhere from a few seconds up to 30+ seconds after
load, not on any fixed schedule. The long-run correction below (after
reveal) is the same event-driven approach, held indefinitely: react to
every scroll event for as long as the page is up, with NO fixed expiry,
and rely entirely on cancelling the instant the user manually
scrolls/touches/drags the container. This is safe because the injected
iframe (and its listeners) is destroyed on Streamlit's next rerun anyway
(components.html() re-creates it fresh each script run), so there's no
risk of it running forever in the background -- it only lives as long as
the current render of the page does, and backs off immediately the
moment the user actually wants to scroll.

Even with the event-driven fix above, Dashboard specifically kept
bouncing visibly in the real browser (confirmed via a user-supplied
screen recording -- automated MutationObserver traces kept showing a
clean settle and couldn't reproduce it). The recording showed the
"quiet since last correction" reveal test itself was the bug: Dashboard
sends its content as several separate deltas several seconds apart (the
metrics row, then the donut chart, then the holdings table, then the
Ask Claude/chat section) -- each one capable of re-triggering
Streamlit's own auto-scroll-to-bottom on arrival. The gap between two
deltas can easily exceed the quiet window even though the page is
nowhere near done, so the old logic revealed partway through the
stream -- the recording plainly shows the holdings table and Ask Claude
section becoming visible mid-transition, followed by more visible
scrolling as later deltas arrived, exactly matching the user's "scrolls
down and up a couple times" report.

Fixed with two additions: (1) mark_render_complete() renders an
invisible marker element, called from app.py right after pg.run()
returns -- since Streamlit delivers deltas to the browser in script
order, that marker can only exist in the real DOM once every element the
page produced has actually arrived, which a fixed timeout can never
guarantee. Reveal now refuses to even start its quiet-countdown until
this marker is seen (the hard safety cap still applies regardless, so a
page can never get stuck invisible if the marker somehow never shows
up). (2) The quiet clock now also resets on any change to the
container's scrollHeight, not just on scroll corrections -- a late
Plotly chart layout pass can keep resizing the container for a bit after
its DOM node lands without necessarily provoking a scroll event in
between, and that's still "not actually settled yet."

STILL not fixed for real for this specific user (confirmed by a second,
independent recording after the above shipped -- the bounce got
*longer*, not shorter). Root-caused for real this time with a
millisecond-resolution trace captured directly in their own browser
console (see dashboard_scroll_diagnostic_v2.js, a one-off diagnostic
script, not part of the app): the hide <style> tag inserted by
hide_main_for_scroll_fix() -- sent as the very first delta of the run,
before pg.run() even starts -- was not actually taking visible effect
in their browser until ~800ms AFTER the new page's content had already
started rendering and growing on screen (container height climbing from
877 -> 7066 while still measured "visible"). Being first in Python
script order guarantees nothing about when the browser actually APPLIES
a given delta relative to later ones -- delivery/paint timing over a
real connection is not the same thing as script order, and every prior
round of this fix assumed it was.

Fixed by no longer waiting on the server for the hide trigger at all.
install_instant_nav_hide() attaches a capture-phase click listener
directly on the persistent parent document (not torn down between
Streamlit reruns, unlike this function's own iframe) that fires the
instant a sidebar nav link is clicked -- before Streamlit's own routing
even begins, with zero server round-trip. The reveal side is unchanged
(still the marker + quiet + height-stability gate above), so the only
thing that moved is WHEN hiding starts, from "whenever the server's CSS
delta happens to get applied" to "synchronously, in the same click
event". hide_main_for_scroll_fix() is kept as a secondary fallback for
non-click navigations (browser back/forward, deep links) where there's
no click to hook.
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

# id on the marker element mark_render_complete() injects -- see that
# function's docstring. Reveal logic below polls for this before it will
# ever start counting down to a reveal.
_MARKER_ID = "_ui_scroll_fix_marker"

_MARKER_CHECK_JS = """
function _renderComplete() {
    return !!window.parent.document.getElementById("%s");
}
""" % _MARKER_ID

# Safety-net cap on how long the corrective listener can run, in case a
# user session sits on a freshly-navigated page for a very long time
# without ever touching it. Not meant to be reached in normal use -- the
# real stop condition is the user manually scrolling (see _cancel below).
_SAFETY_CAP_MS = 5 * 60 * 1000

# How long the position must go without needing a correction AND without
# the container's own height changing before we reveal the content --
# judged by elapsed time since the last actual correction/resize, not by
# a fixed number of polls (see module docstring for why polling missed
# fast back-and-forth fights, and why height changes matter too).
_QUIET_MS_BEFORE_REVEAL = 500

# How often to check "has it been quiet long enough to reveal yet" while
# hidden. This is just the reveal-timing check, NOT the correction
# mechanism -- corrections themselves happen instantly in the scroll
# event handler, not on this interval.
_REVEAL_CHECK_INTERVAL_MS = 50

# Hard cap on how long we'll stay hidden waiting for things to settle,
# in case something never truly stabilizes -- a safety net, not the
# normal path, so the page can never get stuck invisible. Measured live
# via a MutationObserver trace against the deployed app: Dashboard's
# holdings can still be actively growing/resizing (still-hidden fight,
# scrollHeight climbing continuously) more than 2.3 seconds in with no
# sign of stopping -- a short cap here was forcing a reveal WHILE still
# mid-fight, which is exactly the visible bounce this whole mechanism
# exists to prevent. Set generously above what heavy pages have actually
# been observed to need. This is the one reveal path that does NOT wait
# for the render-complete marker -- it's the last-resort escape hatch if
# something goes wrong upstream (marker never renders, JS error, etc.).
_MAX_HIDE_MS = 12000

# id on the <style> tag hide_main_for_scroll_fix() injects, so the
# corrective scripts below can find and remove it once they've actually
# applied the right scroll position.
_HIDE_STYLE_ID = "_ui_scroll_fix_hide"

# Shared class on every hide <style> tag, whichever of the two paths
# inserted it (server-side hide_main_for_scroll_fix() or the client-side
# instant-hide click listener below) -- reveal removes ALL elements with
# this class rather than relying on getElementById(), which only ever
# finds the first match, in case both paths happen to fire for the same
# navigation and leave two tags with the same id.
_HIDE_STYLE_CLASS = "_ui_scroll_fix_hide_el"


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
        f'<style id="{_HIDE_STYLE_ID}" class="{_HIDE_STYLE_CLASS}">'
        '[data-testid="stAppScrollToBottomContainer"], [data-testid="stMain"]'
        " { visibility: hidden !important; } </style>",
        unsafe_allow_html=True,
    )


# Selector for the sidebar's own page links, found by inspecting the
# live app's DOM: <a data-testid="stSidebarNavLink" href="...">.
_NAV_LINK_SELECTOR = 'a[data-testid="stSidebarNavLink"]'


def install_instant_nav_hide():
    """
    Primary hide trigger (see module docstring for why the server-sent
    CSS approach alone wasn't enough): attaches a capture-phase click
    listener directly on the persistent parent document -- NOT on
    anything inside this function's own components.html() iframe, which
    gets destroyed on every single rerun -- so it fires the instant a
    sidebar nav link is clicked, before Streamlit's own routing even
    starts and with zero server round-trip. That's the only way to
    guarantee hiding happens before the new page's content can be seen
    rendering, since script order on the Python side says nothing about
    when the browser actually applies a given delta relative to later
    ones over a real connection (confirmed directly: a millisecond trace
    from the affected user's own browser console showed the server-sent
    hide style not visibly taking effect until ~800ms after the new
    page's content had already started growing on screen).

    Call this unconditionally, every rerun, from app.py, before pg.run()
    -- cheap (a few dozen bytes of JS), and idempotent by design: it
    checks a flag on the parent window so the listener only ever gets
    attached once per browser session no matter how many times this
    function itself gets called (once per rerun, since the iframe it
    runs in is destroyed and recreated each time).

    Skips hiding entirely if the clicked link's path matches the current
    page (i.e. clicking the already-active page isn't a real navigation)
    -- mirrors the _navigated check in app.py, just evaluated client-side
    since there's no time to round-trip to the server first.

    Includes its own independent safety-net timeout that force-removes
    the hide style after a generous delay regardless of what happens
    next, in case the page that was about to load never ends up calling
    force_scroll_to_top()/scroll_to_element() to clean up after itself
    (e.g. a script error) -- the page can never get stuck invisible.
    """
    components.html(
        f"""<script>
        (function() {{
            var doc = window.parent.document;
            var HIDE_ID = "{_HIDE_STYLE_ID}";
            var HIDE_CLASS = "{_HIDE_STYLE_CLASS}";

            function insertHide() {{
                if (doc.querySelector("." + HIDE_CLASS)) return;
                var style = doc.createElement("style");
                style.id = HIDE_ID;
                style.className = HIDE_CLASS;
                style.textContent =
                    '[data-testid="stAppScrollToBottomContainer"], '
                    + '[data-testid="stMain"] {{ visibility: hidden !important; }}';
                doc.head.appendChild(style);
                setTimeout(function() {{
                    doc.querySelectorAll("." + HIDE_CLASS).forEach(function(el) {{ el.remove(); }});
                }}, {_MAX_HIDE_MS} + 3000);
            }}

            function onNavClick(e) {{
                var a = e.target.closest ? e.target.closest('{_NAV_LINK_SELECTOR}') : null;
                if (!a) return;
                var url;
                try {{ url = new URL(a.getAttribute("href"), window.parent.location.href); }}
                catch (err) {{ return; }}
                if (url.pathname === window.parent.location.pathname) return;
                insertHide();
            }}

            if (!window.parent.__scrollFixNavHideInstalled) {{
                window.parent.__scrollFixNavHideInstalled = true;
                doc.addEventListener("click", onNavClick, true);
            }}
        }})();
        </script>""",
        height=0,
    )


def mark_render_complete():
    """
    Renders a single invisible marker element -- call this from app.py
    immediately after pg.run() returns, on every run (cheap: one empty
    hidden div). Streamlit delivers each element to the browser as a
    delta in the order the script creates it, so this marker can only
    exist in the real page DOM once every element the just-finished page
    script produced has actually arrived there -- a far stronger "is the
    page actually done" signal than any fixed timeout.

    force_scroll_to_top() and scroll_to_element() below refuse to start
    their "gone quiet, safe to reveal" countdown until they observe this
    marker present, so a heavy page whose content streams in over several
    seconds as separate deltas (a chart, then a table, then a chat
    section -- each capable of re-triggering Streamlit's own
    auto-scroll-to-bottom on arrival) can't get revealed early into the
    middle of that stream, which was the actual cause of Dashboard's
    visible bounce (see module docstring). The hard safety cap in both
    functions still applies regardless of this marker, so a page can
    never get stuck invisible if something upstream goes wrong.
    """
    st.markdown(
        f'<div id="{_MARKER_ID}" style="display:none"></div>',
        unsafe_allow_html=True,
    )


# Shared JS: remove every hiding rule currently installed, whichever path
# put it there (server-side hide_main_for_scroll_fix() or the client-side
# instant-hide click listener in install_instant_nav_hide()). Removes ALL
# matches by class rather than a single getElementById() lookup, in case
# both paths happened to fire for the same navigation. Safe to call even
# if nothing was ever inserted (e.g. a page calls scroll_to_element() on
# a run that wasn't a navigation).
_REVEAL_JS = """
function _revealMain() {
    var doc = window.parent.document;
    doc.querySelectorAll(".%s").forEach(function(el) { el.remove(); });
}
""" % _HIDE_STYLE_CLASS


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
    Listens for the container's own 'scroll' event and corrects
    synchronously inside that handler -- catching every fight instantly,
    including ones Streamlit's own ~17ms internal loop wins/loses faster
    than any fixed-interval poll could reliably observe -- while hidden
    behind the rule installed by hide_main_for_scroll_fix(). Also tracks
    the container's own scrollHeight, resetting the quiet-clock on any
    change -- a late chart layout pass can keep resizing the container
    without necessarily firing a scroll event in between. Reveals only
    once BOTH the render-complete marker (see mark_render_complete) has
    been observed AND the position/height have gone quiet for a short
    stretch of real time, so the container only ever becomes visible
    already settled, with no bounce. A hard cap reveals regardless if
    something upstream goes wrong (marker never appears, etc.), so the
    page can never get stuck invisible. After revealing, keeps reacting
    to scroll events indefinitely (Streamlit's own behavior can still
    re-assert itself much later on a heavy page), backing off immediately
    the instant it detects the user manually scrolling. height=0 keeps
    the iframe invisible and out of the page's layout.
    """
    components.html(
        f"""<script>
        {_GET_SCROLL_CONTAINER_JS}
        {_MARKER_CHECK_JS}
        {_REVEAL_JS}
        window.parent.scrollTo(0, 0);
        var _stop = false;
        var _revealed = false;
        var _hideStart = Date.now();
        var _lastFixAt = Date.now();
        var _lastHeight = null;
        var _attachedTo = null;
        var _cancel = function() {{ _stop = true; }};

        function _correct() {{
            var c = _getScrollContainer();
            if (c && c.scrollTop !== 0) {{
                c.scrollTop = 0;
                _lastFixAt = Date.now();
            }}
        }}
        function _checkHeight() {{
            var c = _getScrollContainer();
            if (!c) return;
            var h = c.scrollHeight;
            if (h !== _lastHeight) {{
                _lastHeight = h;
                _lastFixAt = Date.now();
            }}
        }}
        function _ensureAttached() {{
            var c = _getScrollContainer();
            if (!c || c === _attachedTo) return;
            _attachedTo = c;
            c.addEventListener('scroll', function() {{
                if (_stop) return;
                _correct();
            }}, {{passive: true}});
            ['wheel', 'touchstart', 'mousedown'].forEach(function(evt) {{
                c.addEventListener(evt, _cancel, {{once: true, passive: true}});
            }});
        }}
        _correct();
        _ensureAttached();
        _checkHeight();
        var _iv = setInterval(function() {{
            if (_stop) {{ clearInterval(_iv); return; }}
            _correct();
            _ensureAttached();
            _checkHeight();
            if (!_revealed && (Date.now() - _hideStart > {_MAX_HIDE_MS}
                                || (_renderComplete()
                                    && Date.now() - _lastFixAt > {_QUIET_MS_BEFORE_REVEAL}))) {{
                _revealMain();
                _revealed = true;
            }}
        }}, {_REVEAL_CHECK_INTERVAL_MS});
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

    Same event-driven, settle-before-reveal approach as
    force_scroll_to_top() (see its docstring and the module docstring for
    why a fixed-interval poll wasn't reliable, and why the quiet-clock
    also tracks container height and gates on the render-complete
    marker): reacts to the container's own 'scroll' event to re-apply
    scrollIntoView() the instant the anchor drifts out of position, and
    only reveals once that's gone quiet for a short stretch AFTER the
    page has confirmed it's actually done rendering. Keeps holding
    indefinitely afterward, backing off immediately if the user manually
    scrolls.

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
        {_MARKER_CHECK_JS}
        {_REVEAL_JS}
        var _stop = false;
        var _revealed = false;
        var _hideStart = Date.now();
        var _lastFixAt = Date.now();
        var _lastHeight = null;
        var _attachedTo = null;
        var _cancel = function() {{ _stop = true; }};

        function _correct() {{
            var el = window.parent.document.getElementById("{anchor_id}");
            if (!el) return;
            var top = el.getBoundingClientRect().top;
            if (Math.abs(top) > 2) {{
                el.scrollIntoView({{block: "start", behavior: _revealed ? "smooth" : "auto"}});
                _lastFixAt = Date.now();
            }}
        }}
        function _checkHeight() {{
            var c = _getScrollContainer();
            if (!c) return;
            var h = c.scrollHeight;
            if (h !== _lastHeight) {{
                _lastHeight = h;
                _lastFixAt = Date.now();
            }}
        }}
        function _ensureAttached() {{
            var c = _getScrollContainer();
            if (!c || c === _attachedTo) return;
            _attachedTo = c;
            c.addEventListener('scroll', function() {{
                if (_stop) return;
                _correct();
            }}, {{passive: true}});
            ['wheel', 'touchstart', 'mousedown'].forEach(function(evt) {{
                c.addEventListener(evt, _cancel, {{once: true, passive: true}});
            }});
        }}
        _correct();
        _ensureAttached();
        _checkHeight();
        var _iv = setInterval(function() {{
            if (_stop) {{ clearInterval(_iv); return; }}
            _correct();
            _ensureAttached();
            _checkHeight();
            if (!_revealed && (Date.now() - _hideStart > {_MAX_HIDE_MS}
                                || (_renderComplete()
                                    && Date.now() - _lastFixAt > {_QUIET_MS_BEFORE_REVEAL}))) {{
                _revealMain();
                _revealed = true;
            }}
        }}, {_REVEAL_CHECK_INTERVAL_MS});
        setTimeout(function() {{ clearInterval(_iv); }}, {_SAFETY_CAP_MS});
        </script>""",
        height=0,
    )
