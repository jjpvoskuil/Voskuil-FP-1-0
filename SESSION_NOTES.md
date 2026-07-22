# Session Notes — Voskuil FP 1.0

Working memory for continuity between chat sessions. **Not a transcript** — just what's worth
carrying forward: decisions and why, what's mid-flight, things that surprised us, operational
gotchas. Punch list (`punch_list_data.json`) = what's left to build. `ARCHITECTURE.md` = current
state of the system. This file = what happened and why, for recent sessions only.

**Retention:** keep roughly the last 8-10 sessions below. When trimming an old entry, fold
anything still relevant into `ARCHITECTURE.md` or the punch list first — don't just delete
decisions that still matter. Newest entries at the top.

---

## 2026-07-20/21 — Bank/insurer alternative scoring (#36)

Punch list #36 asked for a way to score banks/insurers instead of hard-excluding them from
Market Screener. Scoped with the owner to **banks + insurers only** (Buffett/Berkshire's own
playbook), not the full financial SIC universe — brokers/REITs/real estate stay excluded for now.

**What shipped:** new EDGAR concept map fields for bank/insurer raw data (interest income/expense,
noninterest income/expense, loan loss provision, premiums earned, policyholder benefits,
underwriting expense) — every tag verified against real JPMorgan Chase and Progressive Corp
filings via EDGAR's `companyconcept` API before being trusted, not guessed from taxonomy docs
alone. New derived metrics in `sec_utils.py` (ROE, Equity/Assets, Net Interest Margin proxy,
Efficiency Ratio, Provision/NI, Combined Ratio), computed for both latest period and 10-yr
history. New `evaluate_financial_firm_funnel()` (pass/fail gate) and
`score_financial_firm_breakdown()` (weighted 0-100 score), separate weight/threshold sets for
banks vs. insurers. Wired into **Market Screener only** (`fetch_quality_edgar`, the background
scan worker's waterfall counters, the single-ticker debug tool, result card badges, CSV export) —
**not yet** wired into Equity Scout / Compare Stocks / Dashboard (tracked as #70).

Validated against a synthetic 6-year dataset run through the actual code path before the first
push (mocked EDGAR responses via a fake `_sec_get`, not reimplemented logic) — didn't catch
anything wrong, but worth doing before trusting new financial-calculation code.

**Real-scan validation caught a real bug (do this after any future scoring change):** once live,
pulled the actual 145-survivor scan cache and checked bank/insurer scores against known real
company profiles. Found the Equity/Assets "great" threshold (10%, copied from the bank weights)
was insurer-blind — insurers structurally run 3-4x a bank's equity/assets ratio (they aren't
deposit-funded), so 20 of 33 real insurer survivors (61%) scored a flat 100/100 regardless of
actual capitalization. Fixed with separate insurance-specific thresholds (good 15%/great 25%, vs.
bank's 8%/10%) and tightened Combined Ratio (great 85%/good 95%, was 95%/100%). The synthetic test
alone never would have caught this — it only had one company per subtype, not a real peer spread.
**Lesson: for scoring/threshold changes specifically, a real-data spot-check after deploy is not
optional, even when synthetic tests pass.**

**Known gap, deliberately not solved by recalibration:** monoline mortgage/credit/financial-
guaranty insurers (MGIC, Radian, Essent, NMI Holdings, Enact — SIC 6351, "Surety Insurance") run a
structurally different balance sheet (58-78% equity/assets, single-digit combined ratios by
business-model design, not because they're unusually strong) that scores near-perfect under
*any* P&C-calibrated threshold — an apples-to-oranges problem, not a tuning issue. Per owner's
request, moved SIC 6351 out of `INSURANCE_SIC_CODES` entirely (`edgar_concept_map.py`); they now
classify as `"other_financial"` and are excluded by the Market Screener's skip toggle by default,
same as brokers/REITs, until #70 gives them a proper third subtype (loss ratio vs.
risk-in-force, PMIERs-style capital adequacy — not combined ratio).

**Operational gotcha — Streamlit Cloud stale deploy:** right after pushing #36, the app threw
`ImportError: cannot import name 'evaluate_financial_firm_funnel'` even though GitHub's `main`
branch had the function defined correctly (double-checked via `git show origin/main:sec_utils.py`
directly from this session's clone). This was a stale/cached container, not a real bug — the
redacted Streamlit Cloud error page even showed an outdated version of the import block in its
code-context preview, which was the tell. A manual reboot (Manage app → the "⋮" menu → Reboot)
fixed it immediately. **If a fresh push causes an ImportError that doesn't match what's actually
on GitHub, try a reboot before assuming the code is wrong.**

Files touched: `edgar_concept_map.py`, `sec_utils.py`, `app_pages/8_Market_Screener_EDGAR.py`,
`punch_list_data.json`, `ARCHITECTURE.md` (this workflow note + this file).

Also set up this session: added the "Session-end: update SESSION_NOTES.md" convention to
`ARCHITECTURE.md`'s Development Workflow section — this file didn't exist before this session.

---

## Session: #32 + #70 — bank/insurer scoring parity across Dashboard, Equity Scout, Compare Stocks

Follow-up to the #36 session above. Owner asked to bring Dashboard, Equity Scout, and Compare
Stocks up to the same bar as Market Screener on two fronts: #32 (CADS-based dual-hurdle debt
metric) and #70 (bank/insurer alt scoring). Scoped via two explicit choices up front: **score-only
update** (keep these 3 pages' continuous 0-100 score UX, do NOT add Market Screener's pass/fail
funnel checklist to them), and **all three pages in this session**, not one at a time.

**#32 (CADS dual-hurdle):** `sec_utils.score_stock_breakdown()`'s Debt/FCF criterion now takes
`min(debt_to_fcf, debt_to_cads)` (whichever is available/lower) instead of only looking at
`debt_to_fcf` — same "pass on either hurdle" philosophy already used by the Market Screener funnel.
Dashboard and Compare Stocks import this function directly, so they inherited the fix for free.
Equity Scout has its own local duplicate `score_stock()` (a richer version with per-criterion
value/verdict/note strings, not just points) — ported the identical fix there by hand.

**#70 (bank/insurer alt scoring, 3 pages):** `fetch_fundamentals_edgar()` (the shared EDGAR fetch
used by all 3 pages) now surfaces `financial_subtype`, `debt_to_cads`, and the 6 bank/insurer
derived metrics (`roe`, `equity_to_assets`, `nim_proxy`, `efficiency_ratio`, `provision_to_ni`,
`combined_ratio`) that were already being computed internally but never returned. Added a new
`sec_utils.score_financial_firm_display()` — wraps `score_financial_firm_breakdown()` and adds
`value`/`verdict`/`note` to each criterion (percentages/x-multiples formatted, qualitative labels
like "Excellent"/"Good"/"Weak"), so all 3 pages get Equity Scout-quality display richness from one
shared function instead of 3 copies of formatting logic.

Per page:
- **Equity Scout**: branches to the alt scorer when `financial_subtype` is bank/insurance; the old
  blanket "Score shown for reference only, see #36" warning is now an info banner that only fires
  for genuinely un-scored subtypes (brokers/REITs/other_financial) — bank/insurer tickers get a
  real, tailored score instead of an apology.
- **Compare Stocks**: branches per-ticker in the scoring loop (so a bank + an industrial can be
  compared side by side, each scored on its own framework). Fixed a latent bug while in there: the
  "Score Breakdown" table only pulled criterion names from `active_tickers[0]` — now unions names
  across *all* compared tickers, so a mixed comparison doesn't silently drop rows just because the
  first ticker happens to use a different framework.
- **Dashboard**: "Score All Holdings" branches to the alt scorer for bank/insurance holdings, with
  a small "🏦 Bank/Insurance scoring" caption under the badge so it's clear which framework produced
  a given number. Also made `hold_verdict()` (the Hold/Add/Trim "Signal" column) subtype-aware —
  it was still judging bank/insurer holdings against ROIC/Debt-FCF thresholds, which would have
  produced a Signal that contradicted a properly-computed alt Score sitting right next to it. Now
  substitutes ROE for ROIC and Equity/Assets for Debt/FCF, same swap the score itself makes.

**Not in scope, left as-is on purpose:** `other_financial` subtypes (brokers/REITs/monoline
mortgage insurers) still score under the standard framework on these 3 pages — only Market
Screener has the skip-checkbox exclusion UX for them, and giving them a proper third framework is
still #70's still-open remainder (loss ratio/PMIERs-style metrics for monoline insurers, AUM/fee
metrics for brokers, FFO for REITs).

**Testing:** synthetic tests (`/tmp/test/test_page_parity.py` — not committed, scratch only)
covering: dual-hurdle takes the better of the two debt multiples, legacy single-metric path
unaffected, missing-both case still flags as missing, `score_financial_firm_display()` adds
value/verdict/note correctly for both bank and insurance criteria sets, non-bank/insurance subtype
still returns `(None, [])`. All passing. No live real-data spot-check this time (no scan running to
compare against) — worth a quick spot-check on a known bank/insurer holding (e.g. JPM, PGR) next
session once deployed, same as the #36 real-data pass caught a real calibration bug last time.

Files touched: `sec_utils.py`, `app_pages/0_Dashboard.py`, `app_pages/7_Equity_Scout_EDGAR.py`,
`app_pages/9_Compare_Stocks_EDGAR.py`, `punch_list_data.json` (#32 and #70 both marked done).

---

## Session (cont'd): #71 — Dashboard holdings sort UX (click-to-sort headers, default Signal)

Small follow-on ask right after #32/#70 shipped: change the Dashboard holdings table's default
sort to Signal, and replace the "Sort by" dropdown with click-to-sort column headers (confirmed
via clarifying questions: header click = sort, not a per-column value filter; default Signal
order = alphabetical, i.e. Add, Hold, Trim, with unscored "—" last).

Implementation: `Signal`/`Signal_Color`/`Signal_Icon` are now materialized as real `display_df`
columns (one `hold_verdict()` call per row, done once) instead of being recomputed at render time
inside the row loop — this is what makes Signal sortable at all. `SI_Count` (superinvestor holder
count) is materialized the same way once superinvestor data is loaded. The old dropdown is gone;
each column header (Symbol, Name, Type, Value, Accts, Score, Signal, 🦁 SI) is now an `st.button` —
clicking an inactive column sorts by it (using a sensible per-column default direction, e.g. Value
defaults high→low, Symbol defaults A→Z), clicking the already-active column reverses direction.
State lives in `st.session_state.holdings_sort_col`/`holdings_sort_asc`, defaulting to
`("Signal", ascending=True)` on first load.

Neat detail worth remembering: sorting the plain `Signal` verdict string ("Add"/"Hold"/"Trim"/"—")
ascending *already* produces the exact order requested (alphabetical, unscored last) with zero
custom rank-mapping, because the em dash (U+2014) sorts after all uppercase Latin letters in
Unicode. Verified this with a throwaway `pd.DataFrame.sort_values()` check before committing to
the approach, rather than assuming it.

Files touched: `app_pages/0_Dashboard.py`, `punch_list_data.json` (#71 added and closed).

---

## Session (cont'd): #72 — Dashboard holdings scores now persist across redeploys

Owner reported: "sorting gets rid of all the scoring." Before assuming the new #71 sort-header
code was the culprit, diagnosed with `streamlit.testing.v1.AppTest` — ran the ACTUAL
`0_Dashboard.py` script headlessly (not a reimplementation), seeded `session_state` to simulate a
completed "Score All Holdings" run, scripted a click on a sort header, and confirmed
`holding_scores`/`holding_raw_data` are untouched by the click. So the sort code itself was never
the bug.

Real cause: `st.session_state` is in-memory only and gets wiped on every Streamlit Cloud
reboot/redeploy — and a git push triggers exactly that (this is the same behavior already
documented in this project re: pushing while a Market Screener scan is running). The #71 sort-UX
push itself was almost certainly what wiped the owner's in-progress scoring; clicking a sort
header right after was just the first interaction that surfaced the now-empty state, not the
cause of it.

Fix: reused the exact persistence pattern Market Screener already has for its scan cache
(`github_store.py`'s `github_get_json`/`github_put_json`, GitHub Contents API). New
`dashboard_holdings_score_cache.json`: written right after "Score All Holdings" finishes (with
each holding's bulky `_history`/`_latest` EDGAR series stripped first — Dashboard never reads
those, only Compare Stocks/Equity Scout do, so no reason to pay for them here), and loaded once per
fresh session if `session_state` doesn't already have scores in memory. Added a "saved
<timestamp>, persists across reloads" note next to the scored-count message so it's visible that
this is now durable.

Verified end-to-end against the real page code with `AppTest`: faked `fetch_fundamentals_edgar`
(fast, no real network) plus faked `github_get_json`/`github_put_json` (captured the actual
payload instead of hitting GitHub), scored a fresh session, then spun up a completely separate
second `AppTest` session pointed at that captured payload to simulate a post-redeploy reload —
confirmed scores repopulate with no button click, and survive a subsequent sort-header click too.

**Note for next session:** `AppTest` (from `streamlit.testing.v1`) turned out to be a genuinely
good tool for this kind of "does clicking X actually cause Y" question on this app — it runs the
real page script headlessly and lets you script clicks/reruns and inspect `session_state`
directly, which is more convincing than static code reading for anything involving Streamlit's
rerun/session-state model. Worth reaching for again for future UI-behavior bug reports rather than
reasoning from the code alone.

Files touched: `app_pages/0_Dashboard.py`, `punch_list_data.json` (#72 added and closed).

---

## Session (cont'd): #73 — MS data refresh, rebuilt for Mac (with a working automated macro)

Owner switched from Windows to Mac, breaking the old `run_push.bat` flow entirely (hardcoded
`C:\Users\John Voskuil\Downloads`, Windows desktop shortcut). Asked to try automating the whole
refresh via Claude driving the browser, with a Mac-compatible manual process as the fallback if
that didn't pan out.

**The automated macro worked.** Using the Claude in Chrome extension (already connected on the
owner's Mac) against the owner's own already-logged-in MS Online session: navigated to Accounts >
Holdings, clicked Download; Accounts > Activity, set the year filter to Current Year then Prior
Year, downloading both; Accounts > Realized Gain/Loss > Details, same for Current/Previous Year.
Picked up all 5 resulting .xlsx files from `~/Downloads` via a Cowork-connected folder (mounted
into the sandbox bash environment too, so the same session could read them, convert, and push).

One real snag, resolved: the Realized Gain/Loss year picker is a genuine native `<select>` whose
OS-rendered dropdown doesn't show up in extension screenshots and doesn't respond to coordinate
clicks or keyboard arrows while focused (unlike the Activity page's year picker, which is a
custom-rendered dropdown that worked fine with plain clicks). Fixed by using the Chrome
extension's JS execution tool to set `select.value` directly and dispatch `input`/`change` events
— confirmed this genuinely changed the app's state (not just a visual no-op) via the resulting
URL query param (`period=2`) and the page's data actually updating to Previous Year figures.
**Worth remembering for future MS Online automation: if a dropdown looks like a plain click
should work but nothing happens, check whether it's a real native `<select>` (via `read_page`)
before assuming the click coordinates are wrong — the fix is JS, not more clicking.**

Converted all 5 xlsx exports to CSV matching the exact structure `get_clean_df()` expects
(verified column layout against the anchor row of each: "Account Number" for Holdings/Realized
G&L, "Activity Date" for Activity). Validated the ENTIRE pipeline end-to-end by running the real
`app_pages/0_Dashboard.py` through `streamlit.testing.v1.AppTest` against the new files — no
exceptions, and the computed Total Market Value ($3,926,287.92) matched MS Online's live total
exactly, which is about as strong a correctness signal as this kind of change can get.

Also rebuilt the manual fallback for Mac, since the automated path may not always be available or
preferred: `rename_files.py`/`push_files.py` now use `Path.home()/"Downloads"` instead of a
hardcoded Windows path. Also fixed a latent fragility in the original `rename_files.py`: it used to
guess Current vs. Prior year purely from download order/mtime ("assume Current Year was
downloaded first") — now it reads each file's own report header text (MS Online prints "...from
Current Year" / "Previous Year Realized Gain/Loss..." right in the export) so it's correct
regardless of download order. Added `run_push.command` (Mac double-clickable, `chmod +x`) next to
the now-legacy `run_push.bat` (kept, marked legacy, not deleted, path hardcoding fixed too in case
it's ever run on Windows again).

Updated the Dashboard sidebar's refresh instructions (both options: ask Claude, or the manual
script) and corrected an inaccurate ARCHITECTURE.md claim that MS Online "blocks automated/
headless downloads" — it blocks headless/server-side scraping, not a real logged-in browser
session under a human's own authentication, which is what actually happened here.

Files touched: `ms_holdings.csv`, `ms_transactions_ytd.csv`, `ms_transactions_prior.csv`,
`ms_realized_gl_current.csv`, `ms_realized_gl_prior.csv` (all refreshed with live data),
`rename_files.py`, `push_files.py`, `run_push.command` (new), `run_push.bat`,
`app_pages/0_Dashboard.py` (sidebar instructions), `ARCHITECTURE.md`, `punch_list_data.json`.

---

## Session (cont'd): #74 — "Refresh MS Data via Claude" button on the Dashboard

Owner asked for a button on the Dashboard that runs the #73 macro directly, wanting it to feel
like part of the app rather than a separate manual step of opening a chat. Worth recording the
reasoning trail here since it corrects a mental-model gap that could easily recur:

The deployed Streamlit Cloud app is a sandboxed server process. It cannot open a browser, cannot
see the owner's screen, and has no access to the Claude in Chrome extension — that tool only
exists inside an interactive Claude Desktop/Cowork session running on the owner's own machine. An
"embedded Claude agent" on the page doesn't sidestep this: it would still need either a separate
hosted-browser service (real new infrastructure, ongoing cost, and still needs the owner present
for MS Online's login/MFA) or Plaid (already tried — see #13, Morgan Stanley refused the
connection, and even working Plaid only covers balances/transactions, never Holdings or Realized
G/L, so the CSV macro would stay necessary regardless).

The actual answer: Claude Desktop supports a `claude://` URL scheme
(support.claude.com/en/articles/14729294-open-claude-desktop-with-a-link).
`claude://cowork/new?q=<prompt>&folder=<path>` opens a new Cowork session with the prompt
pre-filled and a folder pre-attached. Added an `st.link_button` to the Dashboard sidebar that
builds exactly this URL, with the prompt containing the full refresh instructions (clone the
repo, drive MS Online via Chrome, convert/validate/push, pointing to SESSION_NOTES.md for the
detailed gotchas from #73) and `/Users/JohnV/Downloads` pre-attached as the folder.

Caught one real bug before it shipped: initially built the folder path with `Path.home()`, which
would resolve to the *Streamlit Cloud server's* home directory (since that Python code runs
server-side), not the owner's Mac — even though the whole point of the `folder` param is to name
a path on whatever machine actually opens the link. Fixed by hardcoding `/Users/JohnV/Downloads`
directly, consistent with how this single-user app already hardcodes other owner-specific values.

Verified with `AppTest` that the button renders with the correct `claude://cowork/new?q=...`
URL and that the prompt/folder survive URL-encoding round-trip intact. Did NOT get a fully live
click-through verification in this session — this session's own sandboxed browser tooling
mangles custom-protocol URLs (the navigate tool silently prepends `https://`) and a manual
JS-click workaround tripped an unrelated safety guardrail — so the very first real click from the
owner's own Dashboard should be treated as the actual first test. Expected behavior: the browser
will ask permission to open Claude Desktop the first time; that's normal.

Files touched: `app_pages/0_Dashboard.py`, `punch_list_data.json` (#74 added and closed).

---

## Session (cont'd): #74 fix — deep-link button didn't prefill; redesigned to a login-first flow

Owner clicked the new "Refresh MS Data via Claude" button: Claude Desktop opened, but the prompt
was blank. Root cause: `st.link_button` opens URLs via `window.open()`, and browsers' handoff of
`window.open()` calls to the OS for non-http(s) schemes is inconsistent — the query string
(`q=`/`folder=`) was getting dropped before Claude Desktop ever saw it. Fixed by switching to a
raw `<a href="claude://...">` anchor rendered via `st.markdown(unsafe_allow_html=True)` — a direct
anchor click is treated as a real top-level navigation attempt, which browsers hand off to the OS
protocol handler (and its confirmation dialog) with the query string intact.

While fixing that, the owner also pushed back usefully on the earlier scheduled-task offer: their
MS Online session times out quickly, so a recurring unattended scheduled task wouldn't reliably
work anyway (it'd frequently hit an expired session with nobody there to re-authenticate). Instead
asked for: click the button, have it open the MS Online login page automatically, then run
everything else on its own once they confirm they're logged in — essentially "background" within
the constraint that a human still has to clear MS's own login/MFA gate.

Redesigned the prefilled prompt to script exactly that: Step 1 (done immediately, before cloning
the repo or anything else) is opening `https://www.morganstanleyclientserv.com` in a new Chrome
tab and waiting for a one-word "logged in" confirmation. Step 2 (once confirmed) runs the entire
rest of the macro — clone, navigate all 3 report pages, download all 5 files, convert, validate
via AppTest, commit, push — autonomously, with an explicit instruction not to check in again until
done or something breaks. This also front-loads the time-sensitive part (getting the login tab
open) ahead of anything slower (like waiting on a fresh GitHub PAT), which matters given the
timeout concern. Updated the button's caption to describe this actual flow instead of the old
"log in yourself first, then click" framing.

Files touched: `app_pages/0_Dashboard.py`, `punch_list_data.json` (#74 note updated).

---

## Session (cont'd): #74 — second live test, turned out to be working as designed

Owner tested again (Claude Desktop and Chrome tabs fully closed first): button opened Claude
Desktop, but "nothing ran or downloaded." Rather than guessing at another code fix, asked a direct
diagnostic question first: was there unsent text in the message box? Answer: yes — there was a
permission pop-up (the browser asking to open Claude Desktop) in front of it, and after allowing
that, it landed on a new chat.

This confirmed the deep link is actually working correctly — `claude://cowork/new?q=...`
deliberately only prefills the composer, it does not auto-send (same pattern as a `mailto:` link
drafting an email without sending it — a sensible safety default for a link that can kick off an
agent). The owner just hadn't clicked Send after the permission dialog stole their attention.

**Lesson worth keeping:** when a user reports "nothing happened," ask what they actually saw
before touching code again — this could easily have turned into an unnecessary second round of
"fixes" to something that wasn't broken. Fixed for real this time by rewriting the button's
caption to spell out all 3 steps explicitly, including "you still have to press Enter/click Send
yourself, it won't run on its own" in bold.

Files touched: `app_pages/0_Dashboard.py`, `punch_list_data.json` (#74 note updated again).

---

## Session (cont'd): #74 — real bug found via screenshot: folder= param clears the composer

Third live test, and this time the owner sent a screenshot, which made the actual bug obvious
immediately instead of requiring more guessing: a "Another app attached 'Downloads'" confirmation
dialog (Claude Desktop's built-in confirmation for the `folder=` deep-link parameter) appeared
directly on top of the correctly-prefilled composer. Clicking **Continue** to approve the folder
attach cleared the composer text entirely — nothing left to send. The prefill itself had been
working the whole time (visible behind the dialog in the screenshot); the folder-attach
confirmation flow just has a side effect of wiping it when confirmed.

Fix: dropped `folder=` from the deep link entirely. The URL is now just
`claude://cowork/new?q=<prompt>` — no folder attach, no confirmation dialog, nothing to clear the
composer. The prompt text itself now tells Claude to request Downloads folder access
mid-conversation instead (the same `request_cowork_directory` flow already used elsewhere this
session) — that happens *after* the message is already sent, so it can't clobber anything.

**Pattern worth remembering:** a screenshot cut straight through two rounds of "sounds
plausible" theorizing (cold-start deep-link bugs, composer-not-submitted, etc.) that were
reasonable guesses but wrong. When a user reports "it doesn't work" and text is involved, ask to
see it rather than reasoning from a text description alone if there's any ambiguity — this is
the second time this exact "just ask/look before touching code again" instinct paid off in this
session (the first being the "nothing ran" report that turned out to be an unsent message).

Files touched: `app_pages/0_Dashboard.py`, `punch_list_data.json` (#74 note updated again).

---

## Session (cont'd): #74 — simplified to just the button, moved to top of page

Owner confirmed the deep-link flow works, called it "a bit clunky but better than manual" (the
clunkiness being the browser permission dialog + manual Send click, both inherent to how
`claude://` deep links work — not something further code changes here can smooth over). Asked for
a cleanup: drop the instructional caption and the "Manual fallback" expander, keep just the
button, and move it from the sidebar to the top of the main Dashboard page.

Done. Also removed `get_ms_data_freshness()` and its "Last updated" caption since they only
existed to support the sidebar section being removed — didn't leave it as dead code. The manual
fallback scripts (`run_push.command`, `rename_files.py`, `push_files.py`) are untouched in the
repo in case they're ever needed again, just no longer surfaced anywhere in the UI.

Also: mid-session, the automated macro (run via the button in an earlier test) needed a fresh
GitHub PAT, which invalidated the PAT this session had been using for git push access (fetch
still worked with the old one, push didn't — "Invalid username or token"). Owner supplied a new
PAT; verified it with the same push+delete-throwaway-branch check used at session start before
trusting it for further work.

Files touched: `app_pages/0_Dashboard.py`, `punch_list_data.json` (#74 note updated again).

---

## Session (cont'd): #75 — fixed disruptive auto-scroll (nav-only top, action-only results-scroll)

Owner reported pages sometimes auto-scrolling to the bottom, and asked for two things instead:
navigating to a page should land at the top; clicking an analyze/update-type button should show
the top of that action's fresh results, with free scrolling afterward (no forced repositioning
on later interactions).

Root cause: `ui_utils.force_scroll_to_top()` was called unconditionally at the end of every
script run on 4 pages (Dashboard, Equity Scout EDGAR, Market Screener EDGAR, Compare Stocks
EDGAR) — so *any* rerun (button click, chat message, sort click, slider drag) yanked the
viewport back to (0,0), racing against Streamlit's own scroll behavior (e.g. chat input reveal)
and sometimes losing that race, leaving the user scrolled to the bottom instead. Same root cause
behind both complaints.

Fix — split into two purpose-built mechanisms in `ui_utils.py`:
- `force_scroll_to_top()` is now called centrally from `app.py` only, gated on comparing
  `st.navigation()`'s current page `.url_path` against the last-rendered `url_path` stored in
  `session_state["_last_page_key"]` — fires once per genuine navigation, never on an in-page
  rerun. This covers all 8 live pages automatically since it lives in app.py, not per-page.
- New `scroll_to_element(anchor_id)` scrolls a specific marker element into view instead of the
  absolute top. Wired into:
  - **Dashboard**: anchor placed right before the holdings results table; triggered only when
    `run_scoring` (the "Score All Holdings" button) was truthy *this run*.
  - **Equity Scout EDGAR**: anchor placed right before the analysis results header; triggered
    only via a new `_just_analyzed` flag, set `True` only inside the `if analyze and
    ticker_input:` branch that computes *fresh* results — deliberately distinct from "`_cache_key`
    in `session_state`" (true on almost every subsequent rerun, which would have wrongly
    re-triggered the scroll on unrelated interactions like the chat).
- **Market Screener EDGAR** and **Compare Stocks EDGAR**: removed the `force_scroll_to_top`
  import and trailing call (stops the disruptive behavior) but did *not* get a dedicated
  results-anchor in this pass. Market Screener's scan runs via a background-thread/live-polling
  rerun loop that would need more careful investigation before safely adding a mid-scan anchor
  trigger. Compare Stocks has no in-page action button (entered via `switch_page` from Market
  Screener with results computed on load), so the centralized app.py nav-scroll-to-top already
  covers it correctly as-is.

Verification: `python3 -m py_compile` on all 6 touched files; `streamlit.testing.v1.AppTest`
confirmed app.py's page-key comparison stays stable across in-page reruns (i.e.
`force_scroll_to_top` would NOT re-fire on a non-navigation rerun) and that both Dashboard and
Equity Scout EDGAR load cleanly with their action buttons present and no exceptions.

Scoping note for the owner: Market Screener and Compare Stocks only got the "stop forcing
scroll" half of the fix, not a dedicated jump-to-results anchor — worth a follow-up pass if
those pages' results also deserve the auto-scroll-into-view treatment.

Files touched: `ui_utils.py`, `app.py`, `app_pages/0_Dashboard.py`,
`app_pages/7_Equity_Scout_EDGAR.py`, `app_pages/8_Market_Screener_EDGAR.py`,
`app_pages/9_Compare_Stocks_EDGAR.py`, `punch_list_data.json` (#75 added).

---

## Session (cont'd): #75 follow-up — Market Screener EDGAR results-anchor closed

Owner confirmed Compare Stocks needed nothing further and asked to close the loop on Market
Screener EDGAR, the one page intentionally left without a dedicated results-anchor in the first
pass (it was the riskiest one to touch blind — background-thread scan + a `run_every=2`
polling fragment).

Traced the two places fresh results actually land:
- **Re-apply Filters (no rescan)** — synchronous, same-run, same shape as Dashboard's Score All.
- **Run Two-Stage Screen** — kicks off a background thread; an `@st.fragment(run_every=2)` polls
  it, redrawing only itself while active so the rest of the page stays interactive; once the
  scan finishes the fragment fires one `st.rerun()`, and the main script ingests the result via
  an already-existing `_just_ingested` flag (guarded to fire exactly once per completed scan).

Combined both into one `_scroll_to_ms_results` flag, added an anchor before the "🏆 N Checklist
Survivors" heading, and called `scroll_to_element()` gated on that flag — same pattern as
Dashboard/Equity Scout. Left the 2s polling fragment itself untouched; it only needed a hook at
the moment results actually become available, which both trigger paths already made easy to
find.

Verified: py_compile clean; AppTest confirms the page loads without exception and both action
buttons render.

Files touched: `app_pages/8_Market_Screener_EDGAR.py`, `punch_list_data.json` (#75 note updated).

---

## Session (cont'd): #75 real bug found — force_scroll_to_top() was targeting the wrong DOM node

Owner reported Market Screener EDGAR still opening at the top and then auto-scrolling to the
BOTTOM a second or two later, on every open — meaning the earlier fix hadn't actually fixed the
underlying problem in production, despite clean py_compile and AppTest runs (those can't catch
this class of bug — it's real-browser DOM behavior, invisible to a headless Python test).

Investigated live via Claude in Chrome against the deployed app instead of theorizing further.
Found via direct DOM inspection + reading Streamlit's own minified frontend bundle:
`window.scrollTo(0, 0)` was scrolling the *outer document*, which has zero scroll range in this
Streamlit layout (the whole app renders inside a same-origin iframe, and body/documentElement
scrollHeight always equals the viewport height — there's nothing to scroll there). The actual
scrolling container is a specific inner `<section data-testid="stAppScrollToBottomContainer">`
with its own `overflow-y: auto`. That data-testid name is not a coincidence — it's Streamlit's
own built-in chat-style "stick to bottom" widget, automatically wrapped around the main content
on any page containing `st.chat_input` (which is all four live pages with an "Ask Claude"
section: Dashboard, Equity Scout, Market Screener, Compare Stocks). It force-scrolls to bottom
on mount and re-asserts that via an internal ~17ms polling loop for a second or two while content
settles — exactly the delay the owner described. Pages without `chat_input` get a plain
`data-testid="stMain"` section with none of this behavior.

Fix: rewrote `force_scroll_to_top()` and `scroll_to_element()` in `ui_utils.py` to target
`[data-testid="stAppScrollToBottomContainer"]` directly (falling back to `[data-testid="stMain"]`
for chat_input-less pages) instead of `window.scrollTo`, and to keep re-asserting scroll position
on an interval for 2-3 seconds after firing instead of a single one-shot call — long enough to
outlast Streamlit's own settle window, after which the user is free to scroll normally.

**Pattern worth remembering:** py_compile and AppTest both passed cleanly on the original
"fix" — neither can catch a bug like this, since it's about which real DOM element a piece of
injected JS happens to hit in an actual browser, not anything expressible in Python/Streamlit's
own component tree. When a user reports a *visual/behavioral* bug that passed all our usual
checks, the fastest path to ground truth is looking at the live app directly (Claude in Chrome +
reading the actual frontend bundle) rather than iterating on more Python-side guesses.

Files touched: `ui_utils.py`, `punch_list_data.json` (#75 note updated again).

---

## Session (cont'd): #75 — flat timeout wasn't long enough, switched to stabilization-based

Deployed the stAppScrollToBottomContainer fix and re-checked live via Claude in Chrome — the new
code WAS running (verified by reading the injected component's srcdoc for the new marker
strings), but Dashboard still settled scrolled to the bottom on a fresh load.

Root cause of the remaining gap: the first version used a flat 3-second correction window, but
Dashboard specifically keeps growing in height for longer than that while it fetches/scores
holdings and appends elements sequentially — Streamlit's native "stick to bottom" behavior keeps
re-triggering for as long as content keeps growing, not just for a fixed couple of seconds. A
timeout that's shorter than the page's actual render time hands control back before the page is
done.

Replaced the fixed timeout in both `force_scroll_to_top()` and `scroll_to_element()` with a
content-stabilization loop: keep forcing the scroll position on every 100ms tick until the
container's `scrollHeight` hasn't changed for ~1 second (10 consecutive stable ticks), then stop
— with a 15s hard cap as a safety net. This adapts to however long each page actually takes to
render instead of guessing a fixed number.

Files touched: `ui_utils.py`, `punch_list_data.json` (#75 note updated again). Re-verifying live
after this deploy.

---

## Session (cont'd): #75 — stabilization heuristic replaced with cancel-on-user-scroll hold

The height-stabilization approach wasn't fully reliable either — Dashboard has multiple
sequential async loading phases with brief pauses between them, so `scrollHeight` could look
"settled" for a full second and release control, only for another phase of content to grow
afterward and let Streamlit's native scroll-to-bottom win again with nothing left correcting it.

Replaced with something simpler and more robust: hold the scroll position unconditionally for a
fixed 12-second window (comfortably longer than any page's observed render time), but cancel
immediately the moment a wheel/touchstart/mousedown event fires on the scroll container — so a
deliberate user scroll during that window is never fought, while Streamlit's own automatic
re-scrolling has nothing left to win against once our hold is active.

Files touched: `ui_utils.py`, `punch_list_data.json` (#75 note updated again). Deploying and
re-verifying live next.

---

## Session (cont'd): #75 — fixed hold also proved insufficient, now holds indefinitely

A longer live soak test (Claude in Chrome, polling every 1s) told a clearer story: scrollTop
correctly stayed at 0 continuously from load through 40+ seconds of polling — well past the
12-second hold — then drifted back to the bottom sometime shortly after polling stopped, with
scrollHeight already constant the whole time (no content growth to explain it). Streamlit's
native re-snap-to-bottom appears tied to some internal event (focus, a delayed resize, or
similar) firing at an unpredictable time, not a fixed content-settle window — so picking a longer
fixed number would just delay the same failure, not fix it.

Removed the fixed hold entirely. `force_scroll_to_top()` and `scroll_to_element()` now correct
the scroll position indefinitely, bounded only by a 5-minute safety-net cap not meant to be
reached in practice, backing off immediately and only when the user actually
scrolls/touches/drags the container. This is safe to run indefinitely because each
`components.html()` iframe (and its correction interval) gets destroyed on Streamlit's next
rerun anyway — it only ever lives as long as the current page render does.

Files touched: `ui_utils.py`, `punch_list_data.json` (#75 note updated again). Deploying and
soak-testing live next.

---

## Session (cont'd): #75 — second real bug found on Market Screener: stale-scan scroll trigger

With the DOM-container scroll fix deployed and confirmed via a 33+ second soak test (Dashboard
correctly stayed pinned to the top the whole time, no interaction needed to hold it there),
re-checked Market Screener specifically — the page originally reported — and found it landing
mid-page on the "15 Checklist Survivors" results heading instead of the true top, on a plain
sidebar-click navigation with zero button clicks.

Root cause: Market Screener's background scan state is shared globally across every
session/tab (it's a module-level dict + lock, not per-user session_state), so `_just_ingested`
(added in the earlier #75 follow-up specifically to trigger `scroll_to_element` when a scan
completes) was true not only when a session actually watches a scan run to completion, but also
the very first time *any* fresh session opens the page and discovers an already-finished scan
from earlier — a different visit, a different tab, even leftover from this session's own
testing — that it hasn't "seen" yet. That's indistinguishable from "I was just watching this
finish" under the original logic, so it fired the results-scroll on a plain page open — the
exact disruptive behavior #75 was trying to eliminate in the first place, just via a different
code path than the DOM-container bug.

Fixed by tracking `st.session_state['ms_edgar_watched_active_scan']`, set `True` only while THIS
session actually observes `_snap['active'] == True`, and consumed (popped) when deciding whether
to scroll on ingestion — so scroll-to-results now only fires for a session that genuinely watched
the scan run, not one that just walked in on a stale finished result from elsewhere.

**Pattern worth remembering (second time this session):** the reported symptom ("opens at top,
scrolls to bottom after a second or two") had two independent causes layered on top of each
other — the DOM scroll-container bug (fixed first, confirmed via live soak test) and this
stale-shared-state bug (only visible once the first was fixed and Market Screener was re-checked
specifically). Fixing the first symptom-shaped bug isn't the same as fixing the report; worth
re-testing the exact page/scenario the user named after each fix, not just declaring victory once
*a* plausible cause is addressed.

Files touched: `app_pages/8_Market_Screener_EDGAR.py`, `punch_list_data.json` (#75 note updated
again). Deploying and verifying live next.

---

## Session (cont'd): #75 — actual root cause found: our own two scroll fixes fighting each other

Re-tested Market Screener after the "watched_active_scan" fix and it still oscillated between top
and bottom instead of settling — a fresh JS poll showed `scrollTop` correctly at 0 with the page
title visible one instant, then a screenshot moments later showed results-section content
instead, over and over.

Root cause, finally isolated: on any run where a genuine navigation happens AND the page's own
script triggers `scroll_to_element()` for freshly-ingested results in that same run (e.g.
arriving at Market Screener right as a long-running background scan finishes), `app.py`'s
`force_scroll_to_top()` and the page's `scroll_to_element()` both fire — each starting its own
indefinite hold-until-user-scrolls correction loop, targeting two different positions (absolute
top vs. the results anchor). Neither loop knew about the other, so they fought forever, each
overwriting the other's correction on every tick. This was a bug entirely of our own making —
the "hold indefinitely" design from the last two fixes, stacked on top of each other, not any
further Streamlit platform quirk.

Fixed with simple Python-side coordination: `scroll_to_element()` now also sets
`st.session_state['_scroll_to_element_fired']`; `app.py` reads and clears that flag right after
`pg.run()` and skips its own `force_scroll_to_top()` call if it's set — so results-scroll always
wins over navigation-top-scroll when both would otherwise apply on the same run, matching what
the user actually wants (see the results, not the literal page top, right after an action
produces them).

Files touched: `ui_utils.py`, `app.py`, `punch_list_data.json` (#75 note updated again).
Deploying and doing a final live verification pass next.
