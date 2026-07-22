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
