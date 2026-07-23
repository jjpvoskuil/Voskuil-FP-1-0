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

STILL bouncing after all of the above -- and this time the user caught
something decisive: "when you tested the 3x navigation back and forth,
it was also doing it then." Reproduced it directly in an automated test
browser too (not just the user's machine), which finally ruled out
every timing/environment theory from the rounds above. Built an
on-screen debug overlay (burned directly into a recording, showing live
hidden/visible + scrollTop state) and, critically, an elementFromPoint
probe logging the FULL testid ancestor chain of whatever was actually
painting on screen at a fixed point, every ~20ms, whenever it disagreed
with what our own hide check believed.

That probe caught it immediately and conclusively: while
stAppScrollToBottomContainer's own computed visibility genuinely was
"hidden" (confirmed directly via getComputedStyle, not just "the style
tag exists" -- everything above assumed that check was sufficient and
it never was), a specific descendant a few levels down --
[data-testid="stMarkdownContainer"] -- had its OWN explicit
`visibility: visible` from Streamlit's base stylesheet. CSS visibility
is an inherited property, but any element can re-declare its own value
and break the inheritance chain for itself and everything below it --
completely legal CSS, and exactly what was happening. Since
stMarkdownContainer wraps most of Dashboard's actual text and metrics,
this one rule alone was enough to make the majority of the "hidden"
content paint anyway, no matter how early or how reliably our hide
style got applied -- explaining why every previous fix in this saga
kept "working" by every check we had (style tag present, computed
visibility hidden on the container itself, scrollTop pinned at 0) while
the user kept seeing the exact same bounce regardless.

Fixed by switching the hide mechanism from `visibility: hidden` to
`display: none`. Unlike visibility, display has no inheritance-override
escape hatch -- no descendant of a display:none ancestor can be made to
render no matter what CSS targets it specifically, full stop. This is a
strictly stronger guarantee than anything tried in this whole saga, and
it's the first fix here that closes off the ACTUAL mechanism that was
demonstrated to be leaking, rather than another plausible-sounding
theory about timing.

STILL not fixed -- and the user reported it happens on a cold app open
too, not just in-app navigation, which an instant-hide-on-click can
never cover (there's no click on a fresh page load). Asked for one more
console capture, this time logging container scrollTop on every single
change alongside what's rendered at a fixed point, over a full 90-second
window covering several navigations with no time pressure.

That capture finally showed the actual mechanism driving the bounce,
and it was hiding in plain sight the whole time: right after a clean
reveal (container genuinely visible, display:flex), scrollTop climbs in
a smooth, steady ramp -- 0, 312, 616, 1057, 1341, ... all the way to
~6200 (the bottom) over roughly 750ms, then snaps back to 0. Twice in a
row in the same capture. This is a SMOOTH, ANIMATED scroll, not an
instant jump -- and that's exactly why the event-driven correction (see
above: "listen for 'scroll' and correct in the same handler") never
caught it. Setting `container.scrollTop = 0` synchronously in a scroll
handler stops an instantaneous jump dead, but it does not reliably
cancel an in-progress compositor-driven smooth-scroll animation --
Streamlit's own animation just continues toward its original target on
the next frame, on its own schedule, ignoring the intervening write.
The fight was never close: our correction runs once per JS-visible
scroll event; the animation's next frame runs on the compositor thread
regardless.

Fixed at the root instead of trying to out-fight the animation:
disable_smooth_scroll() forces `scroll-behavior: auto !important` on
the container, permanently (not tied to the hide/reveal cycle at all --
there's no reason this container should ever animate a scroll). With no
CSS-level smooth scrolling available, Streamlit's own scrollTo() calls
resolve instantly instead of animating, so there is no multi-frame
animation left for anything to fight in the first place.

CRASH (2026-07-22): reported as a page-wide "NotFoundError: Failed to
execute 'removeChild' on 'Node': The node to be removed is not a child
of this node" thrown from inside react-dom itself, surfacing after
editing a Punch List item (several in-page reruns, no navigation) and
then navigating away. Root cause: the hide <style> tag inserted by
hide_main_for_scroll_fix() goes through
st.markdown(unsafe_allow_html=True), which makes it a React-owned DOM
node -- React's own fiber tree tracks it like any other element it
rendered. Every cleanup path that reveals the page again
(_revealIfPresent() here, install_instant_nav_hide()'s safety-net
timeout, and the shared _revealMain() used by force_scroll_to_top()/
scroll_to_element()) used to call el.remove() on every element matching
the shared hide class -- including that React-owned one. Detaching it
directly desyncs React's fiber tree from the real DOM: React still
believes the node is attached, and the next time it tries to reconcile
that same position (a later same-page rerun that doesn't call
hide_main_for_scroll_fix() again since it's not a navigation, or a
subsequent page's cleanup pass), its own removeChild() call fails
because we already pulled the node out from under it -- exactly the
observed crash, and why it took multiple in-page reruns (Punch List's
edit form) before a navigation actually triggered the failure. Fixed by
switching every one of those cleanup paths from el.remove() to
el.disabled = true: same instant visual effect (the stylesheet stops
applying immediately) without ever detaching the node from the DOM, so
React's own bookkeeping stays consistent and its eventual real removal
of the node -- whenever it decides to -- always succeeds normally.
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
    position has actually settled -- see both docstrings below.

    ALSO installs its own independent client-side cleanup, entirely
    separate from force_scroll_to_top()/scroll_to_element() -- confirmed
    live that those two are NOT a reliable enough safety net on their
    own. Root cause: st.stop() (used by several pages -- e.g. Compare
    Stocks EDGAR, which calls it when navigated to directly without
    tickers already selected) raises an exception that Streamlit's own
    ScriptRunner re-raises again on the very next `st.foo()` call, for
    the rest of that run, no matter where that call is made from --
    including a try/finally in app.py wrapped around pg.run(). That was
    tried first and confirmed NOT to work: the finally block's own
    mark_render_complete()/force_scroll_to_top() calls are themselves
    `st.foo()` calls, so they re-trigger the exact same stop-check and
    get cut off before rendering anything, every time. There is no way
    to run additional Streamlit code after pg.run() on a run that called
    st.stop() -- confirmed against Streamlit's own source
    (streamlit/runtime/scriptrunner/script_runner.py,
    _maybe_handle_execution_control_request(): every enqueued ForwardMsg
    re-checks the pending stop request and raises again while it's set).
    So the cleanup for THIS hide has to be self-contained, scheduled here
    and only here, before pg.run() -- and specifically before anything
    downstream has a chance to abort the rest of the script.

    First version of this used a single flat timer at {_MAX_HIDE_MS} +
    3000ms (matching force_scroll_to_top()'s own hard cap) -- functionally
    correct (Compare Stocks stopped being permanently blank) but the
    owner immediately flagged the UX cost: a plain st.stop() page (e.g.
    the "no tickers selected" message, three small elements) still took
    a flat 15 real seconds to appear every single time, long enough that
    on first encountering it they assumed the page hadn't loaded at all
    and navigated away. Replaced with the same quiet-then-reveal polling
    force_scroll_to_top() uses (track the container's own scrollHeight,
    reset a quiet-clock on any change, reveal once nothing has changed
    for {_QUIET_MS_BEFORE_REVEAL}ms) -- just without that function's gate
    on the render-complete marker, since a page that calls st.stop() can
    never produce that marker at all (mark_render_complete() is itself
    an `st.foo()` call, blocked the same way). Safe to drop that gate
    specifically here because this fallback only ever ends up being the
    one that actually reveals anything on a page that stopped very early
    with a handful of static elements and nothing left to stream in --
    any page that completes pg.run() normally (e.g. Compare Stocks with
    valid tickers actually loaded) still gets revealed by
    force_scroll_to_top()'s own marker-gated path first, well before this
    fallback's quiet timer would fire, making this a no-op cleanup for
    that case (querySelectorAll simply finds nothing left to remove).

    One risk specific to dropping the marker gate: this polling starts
    immediately, before pg.run() has sent a single delta for the page
    being navigated TO -- at that instant the container still holds the
    PREVIOUS page's content/height, so if the server takes a moment to
    even start streaming the new page, scrollHeight could look
    artificially "unchanged" during that gap and reveal on stale content
    before the real page has arrived. Guarded with a minimum floor
    ({_QUIET_MS_BEFORE_REVEAL * 2}ms since this script started) before
    the quiet-based reveal is allowed to fire at all, giving the
    server round-trip a chance to start before any "quiet" reading counts
    -- doesn't fully eliminate the race in theory, but combined with the
    fact that this path only matters in practice for st.stop() pages
    (whose entire output arrives in one small, synchronous burst, not a
    slow stream), it's a solid safety margin. The {_MAX_HIDE_MS} + 3000ms
    timer remains as the absolute last-resort cap if scrollHeight polling
    somehow never settles.
    """
    st.markdown(
        f'<style id="{_HIDE_STYLE_ID}" class="{_HIDE_STYLE_CLASS}">'
        '[data-testid="stAppScrollToBottomContainer"], [data-testid="stMain"]'
        " { display: none !important; } </style>",
        unsafe_allow_html=True,
    )
    components.html(
        f"""<script>
        {_GET_SCROLL_CONTAINER_JS}
        var _lastHeight = null;
        var _lastChangeAt = Date.now();
        var _start = Date.now();
        var _done = false;

        function _revealIfPresent() {{
            // Disable rather than remove: this style tag may have been
            // inserted server-side via st.markdown(unsafe_allow_html=True),
            // which makes it a React-owned DOM node. Detaching it directly
            // (el.remove()) desyncs React's fiber tree from the real DOM --
            // React still believes the node is attached, and the next time
            // it tries to reconcile that position (e.g. a later rerun that
            // doesn't re-emit this markdown call, or a subsequent page
            // navigation), its own removeChild() call fails with
            // "NotFoundError: the node to be removed is not a child of this
            // node" because we already pulled it out from under it.
            // Setting .disabled = true turns off the CSS rule instantly
            // (same visible effect as removal) without ever detaching the
            // node, so React's own bookkeeping stays consistent and its
            // eventual real removal of the node (when it decides to) always
            // succeeds normally.
            window.parent.document.querySelectorAll(".{_HIDE_STYLE_CLASS}")
                .forEach(function(el) {{ el.disabled = true; }});
        }}

        // Scheduled via window.parent (the persistent app window), not a
        // bare setInterval/setTimeout -- this iframe itself gets torn
        // down almost immediately (the very next rerun, e.g. pg.run()
        // executing the navigated-to page), well before a timer scoped
        // to it could ever fire.
        var _iv = window.parent.setInterval(function() {{
            if (_done) {{ window.parent.clearInterval(_iv); return; }}
            var c = _getScrollContainer();
            var h = c ? c.scrollHeight : null;
            if (h !== _lastHeight) {{
                _lastHeight = h;
                _lastChangeAt = Date.now();
            }}
            var pastFloor = Date.now() - _start > {_QUIET_MS_BEFORE_REVEAL * 2};
            var quiet = pastFloor && (Date.now() - _lastChangeAt > {_QUIET_MS_BEFORE_REVEAL});
            var capped = Date.now() - _start > {_MAX_HIDE_MS} + 3000;
            if (quiet || capped) {{
                _revealIfPresent();
                _done = true;
                window.parent.clearInterval(_iv);
            }}
        }}, {_REVEAL_CHECK_INTERVAL_MS});
        </script>""",
        height=0,
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
    -- cheap (a few dozen bytes of JS). Deliberately re-attaches a FRESH
    listener on every single call rather than attaching once and never
    again: this function's own iframe (like any components.html() call)
    is destroyed on the very next rerun, and a listener whose defining
    realm has been torn down stops firing even though it's technically
    still registered -- so "install once per session" would silently go
    dead after the first navigation. The previous run's listener (if
    any) is removed first via a reference stashed on the parent window,
    so this stays idempotent without ever leaving a dead listener as the
    only one attached.

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
                    + '[data-testid="stMain"] {{ display: none !important; }}';
                doc.head.appendChild(style);
                // Scheduled against window.parent (the persistent app
                // window), NOT a bare setTimeout -- a bare setTimeout here
                // belongs to THIS iframe, which is destroyed by the very
                // navigation this timer is meant to guard, well before it
                // could ever fire. window.parent.setTimeout survives that
                // teardown since it's scheduled on a realm that isn't
                // going anywhere. Confirmed live: a page that calls
                // st.stop() partway through its own script (e.g. Compare
                // Stocks EDGAR with no tickers selected) short-circuits
                // app.py's own mark_render_complete()/force_scroll_to_top()
                // cleanup entirely, and this was the only other thing
                // that could have removed the hide style -- except it
                // never fired, leaving the page permanently blank.
                window.parent.setTimeout(function() {{
                    // Disable, don't remove -- see _revealIfPresent()'s
                    // comment in hide_main_for_scroll_fix() above. This
                    // style tag may be the OTHER hide path's (React-owned,
                    // via st.markdown), not just this function's own
                    // client-created one, since both share this class and
                    // this cleanup runs indiscriminately over all matches.
                    doc.querySelectorAll("." + HIDE_CLASS).forEach(function(el) {{ el.disabled = true; }});
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

            // Re-attach fresh on every single call rather than "only once ever"
            // -- this function's OWN iframe (and everything defined inside it,
            // including this very listener function) gets torn down on the
            // NEXT Streamlit rerun, at which point the listener silently stops
            // firing even though it's technically still attached to `doc` (a
            // dead V8 realm, not a live one). An earlier "install once per
            // session" guard here caused exactly that: it fired correctly for
            // the first navigation (whose iframe was still alive) and then
            // silently did nothing for every navigation after that, falling
            // back to the slower server-side path with no visible error.
            // Removing the previous listener before adding this run's fresh
            // one keeps this idempotent (no stacking duplicates) without ever
            // leaving a dead one as the only one attached.
            if (window.parent.__scrollFixNavHideListener) {{
                doc.removeEventListener("click", window.parent.__scrollFixNavHideListener, true);
            }}
            window.parent.__scrollFixNavHideListener = onNavClick;
            doc.addEventListener("click", onNavClick, true);
        }})();
        </script>""",
        height=0,
    )


def disable_smooth_scroll():
    """
    Forces `scroll-behavior: auto !important` on the real scroll
    container -- call this unconditionally, every run, from app.py. Not
    tied to the hide/reveal cycle at all: this rule should just always
    be there, permanently, for the lifetime of the page.

    Root fix for the actual mechanism behind Dashboard's bounce (see
    module docstring): Streamlit's own auto-scroll-to-bottom, when it
    fires, animates smoothly over several hundred ms rather than jumping
    instantly. A live capture from the affected user's browser showed
    scrollTop climbing in a steady ramp all the way to the bottom after
    a clean reveal, then snapping back -- twice in the same session. The
    existing scroll-event-driven correction (see force_scroll_to_top())
    cannot reliably win that fight: writing container.scrollTop = 0
    inside a 'scroll' handler stops an instantaneous jump dead, but does
    not reliably cancel an in-progress CSS/compositor-driven smooth
    scroll animation -- the animation's next frame runs on the
    compositor's own schedule and simply continues toward its original
    target regardless of the intervening write.

    Removing the ability to animate at all removes the fight entirely:
    with scroll-behavior forced to auto, any scrollTo()/scrollIntoView()
    call against this container (Streamlit's own included) resolves
    instantly instead of animating, so there's no multi-frame animation
    left for our correction -- or anything else -- to lose to.
    """
    st.markdown(
        '<style id="_ui_scroll_fix_no_smooth">'
        '[data-testid="stAppScrollToBottomContainer"], [data-testid="stMain"]'
        " { scroll-behavior: auto !important; } </style>",
        unsafe_allow_html=True,
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
    // Disable, don't remove -- one of the matched elements may be the
    // hide <style> tag inserted server-side via
    // st.markdown(unsafe_allow_html=True) in hide_main_for_scroll_fix(),
    // which makes it a React-owned DOM node. Directly detaching a
    // React-owned node from outside React (el.remove()) leaves React's
    // fiber tree pointing at a node that's no longer actually attached --
    // the next time React itself tries to reconcile/remove that same
    // position (e.g. the very next rerun that doesn't re-issue this
    // markdown call, or a later page navigation's cleanup pass), its
    // internal removeChild() call throws "NotFoundError: the node to be
    // removed is not a child of this node", crashing the whole page.
    // Setting .disabled = true switches off the CSS rule immediately
    // (identical visible effect to removal) while leaving the node
    // exactly where React put it, so React's own eventual cleanup of it
    // always succeeds normally.
    doc.querySelectorAll(".%s").forEach(function(el) { el.disabled = true; });
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

    A console-level trace (dashboard_scroll_diagnostic_v5, run directly
    against the live app) finally showed why the earlier "listen for
    'scroll' and correct in the same handler" approach, and later the
    scroll-behavior:auto CSS override, both failed to actually stop the
    visible bounce even though each looked completely clean in its own
    isolated verification: Streamlit's own bundle isn't calling
    scrollTo()/scrollIntoView() at all for this, and isn't relying on CSS
    smooth-scroll either -- it's a plain JS animation loop that just
    assigns container.scrollTop = <climbing value> directly, once per
    animation frame (~16-17ms apart, 0 -> ~235 -> ~541 -> ... -> the
    container's full scrollHeight), completely oblivious to
    scroll-behavior (which only governs the browser's OWN smooth-scroll
    implementation, never manual per-frame property writes) and
    oblivious to whatever value a 'scroll' event handler had just written
    back, because it computes each frame's target from its own internal
    animation state, not by reading the DOM. Reacting after the fact --
    via a scroll event, however synchronous -- can only ever win the
    fight one frame late at best, since 'scroll' events are dispatched
    asynchronously (coalesced to the next paint) rather than inline with
    the write that triggered them, leaving a real window for the browser
    to paint Streamlit's climbing value before any correction lands.

    Fixed properly this time by intercepting the write itself instead of
    reacting to it: installs an own-property override of `scrollTop` on
    the actual container element (Object.defineProperty, instance-level,
    not prototype-level) that silently discards any attempt to set it to
    a non-zero value while the guard is active, and lets the real setter
    run for anything else. Because this runs synchronously as part of the
    assignment expression itself, Streamlit's frame-by-frame writes never
    actually reach the DOM in the first place -- there is no intermediate
    value left for the browser to ever paint, regardless of how the scroll
    event queue is timed. The original 'scroll'-listener correction is
    kept alongside it as a second, redundant layer (harmless -- it only
    ever fires for a scrollTop that already isn't 0, which the guard
    should prevent from happening at all) and to catch anything that
    moves scroll position through some other API entirely.

    Also tracks the container's own scrollHeight, resetting the
    quiet-clock on any change -- a late chart layout pass can keep
    resizing the container without necessarily firing a scroll event in
    between. Reveals only once BOTH the render-complete marker (see
    mark_render_complete) has been observed AND the position/height have
    gone quiet for a short stretch of real time, so the container only
    ever becomes visible already settled, with no bounce. A hard cap
    reveals regardless if something upstream goes wrong (marker never
    appears, etc.), so the page can never get stuck invisible. After
    revealing, keeps guarding indefinitely (Streamlit's own behavior can
    still re-assert itself much later on a heavy page), backing off
    immediately -- releasing the real scrollTop setter back to normal --
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
        var _guard = null;
        var _cancel = function() {{
            _stop = true;
            if (_guard) {{ _guard.restore(); _guard = null; }}
        }};

        // Instance-level override of scrollTop on the live container --
        // NOT the prototype -- so only this one element's writes are
        // intercepted. Any write of a non-zero value while `active` is
        // discarded outright (the underlying native setter is simply
        // never called with that value), which stops Streamlit's own
        // per-frame animation writes from ever reaching the DOM instead
        // of merely reacting after they already have.
        function _installGuard(c) {{
            if (c.__scrollFixGuard) return c.__scrollFixGuard;
            var proto = window.parent.Element.prototype;
            var desc = Object.getOwnPropertyDescriptor(proto, 'scrollTop');
            if (!desc || !desc.set) return null;
            var nativeSet = desc.set;
            var nativeGet = desc.get;
            var guard = {{ active: true }};
            guard.restore = function() {{
                guard.active = false;
                try {{ Object.defineProperty(c, 'scrollTop', desc); }} catch (e) {{}}
                delete c.__scrollFixGuard;
            }};
            Object.defineProperty(c, 'scrollTop', {{
                configurable: true,
                get: function() {{ return nativeGet.call(this); }},
                set: function(v) {{
                    if (guard.active && v !== 0) {{
                        nativeSet.call(this, 0);
                        _lastFixAt = Date.now();
                        return;
                    }}
                    nativeSet.call(this, v);
                }},
            }});
            c.__scrollFixGuard = guard;
            return guard;
        }}

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
            if (!c) return;
            if (c !== _attachedTo) {{
                _attachedTo = c;
                c.addEventListener('scroll', function() {{
                    if (_stop) return;
                    _correct();
                }}, {{passive: true}});
                ['wheel', 'touchstart', 'mousedown'].forEach(function(evt) {{
                    c.addEventListener(evt, _cancel, {{once: true, passive: true}});
                }});
            }}
            if (!_stop && (!_guard || !_guard.active)) {{
                _guard = _installGuard(c);
            }}
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
        setTimeout(function() {{ clearInterval(_iv); _cancel(); }}, {_SAFETY_CAP_MS});
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

    BUG (2026-07-23): owner reported Equity Scout "scrolls around" the
    first time they analyze a ticker right after navigating to the page,
    but never on a second analysis in the same visit. Root cause: the
    navigation's own force_scroll_to_top() call installs a scrollTop
    guard directly on the real container (see its _installGuard()) that
    silently discards any non-zero scrollTop write until the user
    manually scrolls/touches/drags it -- and that release is wired to
    'wheel'/'touchstart'/'mousedown' only. Submitting a ticker with Enter
    right after landing on the page (the natural first move) never fires
    any of those, so the guard is still armed when this function's own
    scrollIntoView() calls run -- each one gets silently reset to 0,
    which fires a 'scroll' event, which re-triggers this function's own
    correction, which gets reset again, and so on: an invisible tug-of-
    war between two corrective mechanisms with different targets (0 vs.
    the results anchor) that reads to the user as the page scrolling
    around on its own. Never happens on a second analysis because
    nothing re-installs a guard between analyses on the same page visit.
    Fixed by releasing any leftover guard (c.__scrollFixGuard) up front
    and on every poll tick, before this function's own corrections run --
    see _releaseLeftoverGuard() below.
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

        // A PRIOR force_scroll_to_top() call (from the navigation that
        // landed on this page) may still have its scrollTop guard
        // actively installed on the real container -- see
        // force_scroll_to_top()'s _installGuard(): it discards any
        // attempt to set scrollTop to a non-zero value until the user
        // manually scrolls/touches/drags, and that release only fires on
        // a 'wheel'/'touchstart'/'mousedown' event reaching the
        // container. Submitting a text_input with Enter (the natural way
        // to run an analysis right after navigating -- type a ticker,
        // hit Enter) is a keyboard interaction, not a mouse/touch one, so
        // it never releases that guard. Without this, THIS function's
        // own scrollIntoView() calls below get silently discarded back to
        // 0 by the leftover guard, which keeps generating 'scroll'
        // events that re-trigger _correct() here, which tries to scroll
        // again, which the guard discards again -- an invisible-looking
        // fight that reads to the user as the page "scrolling around"
        // right after their first analysis on a freshly-navigated page.
        // Confirmed as the mechanism: it only ever happens on the first
        // analysis after navigating (the only time a force_scroll_to_top()
        // guard could still be un-released), never the second (nothing
        // installs a new guard between analyses on the same page visit).
        function _releaseLeftoverGuard() {{
            var c = _getScrollContainer();
            if (c && c.__scrollFixGuard && c.__scrollFixGuard.active) {{
                c.__scrollFixGuard.restore();
            }}
        }}

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
        _releaseLeftoverGuard();
        _correct();
        _ensureAttached();
        _checkHeight();
        var _iv = setInterval(function() {{
            if (_stop) {{ clearInterval(_iv); return; }}
            _releaseLeftoverGuard();
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
