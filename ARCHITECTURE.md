# Voskuil FP 1.0 — Architecture

*Living document. The repo copy is the source of truth — if this and Claude's Project Knowledge
ever disagree, trust this file. See "Keeping This Current" at the bottom.*

*Last updated: July 6, 2026*

---

## What This Is

A personal financial operating system built in Python/Streamlit, hosted on Streamlit Community
Cloud, version-controlled on GitHub (`jjpvoskuil/Voskuil-FP-1-0`). Two parallel tracks:

1. **Personal use** — dashboard tracking a Morgan Stanley portfolio, stock scoring/screening,
   financial planning and retirement modeling, tax monitoring.
2. **Commercial product** (later) — the same concentrated-value scoring engine packaged for
   middle-class investors priced out of institutional research. Target: 2027, after proving the
   approach on the personal portfolio.

## Owner Profile & Investment Philosophy

- Age 57. Buffett/Munger **concentrated value** philosophy — not diversification theater.
- **No macro/market-prediction overlay.** An earlier "Long Squeeze" macro thesis (financial
  repression, passive index bubble risk) was baked into Claude's system prompt and some UI text.
  Retired in July 2026 — deliberate design principle now: the app evaluates each business on its
  own fundamentals (moat, balance sheet, management) under a generic downside-survival stress
  test, not a specific predicted economic scenario. Don't reintroduce market-timing or
  macro-thesis assumptions into scoring or Claude prompts.
- Primary home fully paid off — no mortgage obligations.
- Goal: identify a handful of high-conviction concentrated positions using owner-earnings-based
  analysis, not P/E-multiple comparison shopping.

## Tech Stack

| Component | Choice | Notes |
|---|---|---|
| Language | Python | |
| UI Framework | Streamlit | Streamlit Community Cloud hosting |
| Version Control | GitHub | `jjpvoskuil/Voskuil-FP-1-0`, `main` branch |
| Primary financial data | **SEC EDGAR** Company Facts API | Free, no rate-limit risk at this scale, direct from source (no third-party normalization) |
| Pricing data | **yfinance** | Live price, market cap, sector, dividend yield — EDGAR has no pricing data at all |
| Portfolio data | Manual Morgan Stanley CSV export | `rename_files.py` → `push_files.py` via a desktop shortcut, since MS Online blocks automated/headless downloads |
| ~~Polygon.io~~ | **Fully retired** | Was the original primary data source; do not reintroduce references to it as current |

## App Structure — Page Map

Registered via `st.navigation()` in `app.py`. The folder is named `app_pages/`, deliberately
**not** `pages/` — Streamlit special-cases a folder literally named `pages/` for legacy
auto-discovery, which runs in parallel with `st.navigation()` and causes exactly the kind of
mixed, icon-less, stale-page sidebar bug we hit and fixed in July 2026.

| Page | File | Purpose |
|---|---|---|
| 🛡️ Dashboard | `0_Dashboard.py` | Portfolio overview, holdings scoring, hold/add/trim signals, Claude agent |
| 🔍 Equity Scout | `7_Equity_Scout_EDGAR.py` | Single-ticker deep dive — full scoring, DCF, historical trends, 10-K-aware Claude agent |
| 📡 Market Screener | `8_Market_Screener_EDGAR.py` | Broad-universe scan (S&P 500 or ~7,000 US common stocks), persistent scan cache, quant-only Claude agent |
| ⚖️ Compare Stocks | `9_Compare_Stocks_EDGAR.py` | Side-by-side comparison (2-5 tickers), score breakdown, combined trend charts, 10-K-aware Claude agent |
| 🏔️ Financial Modeler | `3_Financial_Modeler.py` | Retirement/cash-flow modeling |
| 🏦 MS Financial Modeler | `4_MS_Financial_Modeler.py` | MS-holdings-specific modeling |
| ⬇️ Downloads | `5_Downloads.py` | Data export |
| ✅ Punch List | `6_Punch_List.py` | Dev roadmap tracker — this page's new **Architecture** tab is this document, rendered |

**Retired, still on disk, not registered in navigation:** `1_Equity_Scout.py`,
`2_Market_Screener.py` — the original Polygon-based versions. Kept for reference/rollback, not
reachable from the sidebar.

## Data Flow

```
                     ┌─────────────────────┐
                     │   SEC EDGAR API      │  Financial statements
                     │  (data.sec.gov)      │  (income, cash flow,
                     └──────────┬───────────┘   balance sheet — all
                                │                history + latest)
                                ▼
                     ┌─────────────────────┐
                     │   sec_utils.py       │  fetch_fundamentals_edgar()
                     │  (shared utility)     │  score_stock_breakdown()
                     └──────────┬───────────┘  compute_dcf_value()
                                │
              ┌─────────────────┼─────────────────┬──────────────────┐
              ▼                 ▼                 ▼                  ▼
      Equity Scout      Market Screener    Compare Stocks       Dashboard
        EDGAR              EDGAR              EDGAR          (holdings score)

                     ┌─────────────────────┐
                     │     yfinance         │  Live price, market cap,
                     │                      │  sector, dividend yield
                     └──────────┬───────────┘
                                │
                    (same 4 consumers as above)

                     ┌─────────────────────┐
                     │  Morgan Stanley CSV  │  Manual export → rename →
                     │  (manual export)     │  push_files.py → GitHub
                     └──────────┬───────────┘
                                ▼
                            Dashboard
```

## Scoring Engine

**Canonical location: `sec_utils.py`** — `DEFAULT_WEIGHTS`, `THRESHOLDS`, `score_stock_breakdown()`,
`score_stock()`. Every page that scores a stock (Dashboard, Equity Scout, Market Screener, Compare
Stocks) imports from here. This was consolidated in July 2026 after Dashboard was found running an
entirely separate, stale scoring pipeline with a wrong ROIC formula — see "Architectural Debt Paid
Down" below.

**5 criteria, rebalanced to 100 points across whatever has data available** (a missing metric
doesn't just lose points — remaining criteria are rescaled proportionally):

| Criterion | Default Weight | What it measures |
|---|---|---|
| FCF Yield | 30 | Real owner earnings relative to price |
| ROIC | 20 | Total Equity + Total Debt as invested capital (corrected from the old, wrong Total Assets − Current Liabilities formula) |
| Debt / FCF | 25 | Balance sheet strength; Net Creditor detection gives full points to companies earning more interest than they pay |
| Gross Margin | 15 | Pricing power / moat durability |
| Interest Coverage | 10 | Ability to service debt, cash-basis preferred |

**Price / Owner Earnings is intentionally excluded from scoring** — shown as a reference valuation
metric on result cards, but not a weighted criterion. (Also used independently in Dashboard's
hold/add/trim threshold logic, which is separate from the scoring engine.)

**DCF Intrinsic Value** (`compute_dcf_value()`, also in `sec_utils.py`) — two-stage discounted cash
flow, growth rate derived from each company's own historical FCF trend (clipped to a sane range),
Gordon Growth terminal value, shown under live price on Equity Scout and Compare Stocks with an
adjustable assumptions expander. FCF Yield and Price/Owner Earnings can't be trended over time
(would need historical share price, which isn't fetched anywhere in the app today) — flagged
clearly in the UI rather than silently failing.

## Persistence & Caching

Streamlit Community Cloud wipes the container's filesystem and memory on every reboot/redeploy.
Two things need to survive that, so both are backed by GitHub instead of local disk:

- **`github_store.py`** — generic, reusable GitHub Contents API read/write (SHA-checked to avoid
  clobbering concurrent writes). Used by:
  - **Punch list** (`pages/6_Punch_List.py` — actually has its own dedicated, slightly more
    specialized implementation predating `github_store.py`; not yet migrated to share it)
  - **Market Screener's scan cache** (`market_screener_scan_cache.json`) — the Stage 1
    quality-floor survivor pool, so a multi-minute full-universe scan doesn't have to be re-run on
    every reboot. Shows last-scan date/universe next to the re-run buttons.

## Claude Agent Integrations

Four separate chat panels, each scoped to what that page actually knows:

| Page | Scope | Fetches 10-K filings? |
|---|---|---|
| Dashboard | Portfolio-wide questions | No |
| Equity Scout | Single ticker | Yes |
| Market Screener | Full screen results (quant only) — narrows candidates *before* a shortlist | No |
| Compare Stocks | The 2-5 tickers being compared | Yes, lazily on first question |

All use `claude_utils.ask_claude_about_equity()`. Market Screener deliberately does **not** fetch
filings — that capability lives on Compare Stocks now, scoped to an actual shortlist rather than
the whole screen.

## Development Workflow

Claude has direct git push access via a dedicated fine-grained GitHub PAT (separate from the
Streamlit app's own `GITHUB_TOKEN` secret, which only handles punch-list/scan-cache persistence).
Session-start: paste the token, Claude configures the credentialed remote, verifies with a no-op
fetch, then edits/commits/pushes directly — no copy-paste into the GitHub web editor.

**Session-end: Claude updates `SESSION_NOTES.md`** with a short summary of what shipped this
session, key decisions and why, any gotchas hit, and open follow-ups — not a transcript, just
what's worth carrying into the next chat (added July 2026, after repeatedly re-explaining the
same context at the start of new chats). This is *why* Claude re-reads this file every session:
baking the instruction here — rather than depending on cross-chat memory, which doesn't reliably
exist — is what makes it actually happen. `SESSION_NOTES.md` keeps roughly the last 8-10 sessions;
older entries get folded into this file or the punch list before being trimmed, not just deleted.

## Architectural Debt Paid Down (July 2026)

Worth keeping visible so it doesn't silently recur:

- **Dashboard's holdings scoring** was running an entirely separate Polygon/yfinance pipeline
  with a wrong ROIC formula and Price/Owner Earnings still actively scored — migrated to the
  shared EDGAR engine.
- **`score_stock_breakdown()`** was duplicated across Market Screener and Compare Stocks (each
  with a comment explaining why, at the time) — consolidated into `sec_utils.py`.
- **`pages/` → `app_pages/`** folder rename — see App Structure above.
- **`.streamlit/pages.toml`** — deleted; stale config fighting `st.navigation()`.
- **Market Screener's random-sample scan bias** — was silently scanning the same
  alphabetically-first slice every time; now either scans everything or takes a seeded random
  sample.
- **"Long Squeeze" macro overlay removed.** Was baked into Claude's core system prompt
  (`claude_utils.py`) as an entire "pessimistic scenario" narrative — credit tightening, passive
  index concentration risk — with the return-assumption variable literally named `ls_return`.
  Also present in a few suggested-question strings and punch list seed text. Removed everywhere
  found; system prompt now explicitly instructs Claude not to layer in speculative macro/market-
  timing predictions. The retirement modeler's base/pessimistic/bear return scenarios for
  withdrawal-sustainability stress testing were left alone — that's standard financial planning,
  not a market thesis, though the variable was renamed away from the Long Squeeze association.

## Known Gaps / Still Stale

- `pages/6_Punch_List.py`'s GitHub persistence predates `github_store.py` and isn't migrated to
  share it yet — two implementations of the same pattern.
- Item #12 (ETF dist_yield) still references replacing yfinance "with Polygon" — stale wording,
  low priority, not yet corrected.
- Some in-app help text and code comments elsewhere may still reference Polygon or the old
  6-metric framework — corrected opportunistically as encountered, not yet swept exhaustively.

## Future Development Roadmap

Pulled from the live punch list (`punch_list_data.json`) — **34 open items** as of this writing.
This section is a snapshot; the Architecture tab on the Punch List page renders it live so it
can't drift out of sync the way a hand-maintained roadmap would.

### Architecture (1)
- #62 Develop architecture visual *(this document + in-app page/tab)*

### Equity Scoring (8) — mostly High priority
- #31 Demote/remove Gross Margin from composite scoring
- #32 Restructure Debt/FCF — outstanding principal + cash available for debt service
- #33 Demote FCF Yield and Price/Owner Earnings to secondary screens
- #34 Overhaul ROIC — 10-year cash-accounting basis
- #35 Fix interest coverage — cash-basis, interest paid
- #36 Financial firm detection — alternative scoring flag
- #37 Cyclical firm detection — full-cycle caveat flag
- #39 Operating metrics panel on Equity Scout

### Data Quality (12)
- #63 Redo stock scoring metrics *(High — kept alongside the Equity Scoring items above deliberately: scope isn't finalized yet, this is a placeholder for "the metrics need work" so the thinking isn't lost before it's fully formed)*
- #18/#19/#20 Bank of America dashboard/transactions/API integration (High)
- #21 Quicken-like transaction categorization page
- #7 Separate maintenance vs. growth capex
- #9 Historical score trending for a ticker
- #41 Total allocation view (direct + fund exposure combined)
- #53–56 Smart ingestion phases 1-4 (manual entry → fuzzy match → confidence UI → pattern library)

### Fund Deep Dive (2)
- #5 Strategy-matched discovery scan (High)
- #42 Separate deep-dive format for ETFs/mutual funds

### Modeling (3)
- #6 Real-time tax monitoring (replace MS Parametric)
- #44 Retirement modeler — cash/treasuries/equities allocator
- #45 Retirement modeler — historical crisis scenario overlays

### API / Infrastructure (4)
- #52 EDGAR historical full ingestion/normalization layer
- #11 Morningstar via RapidAPI for foreign ADR scoring
- #28 Yodlee/Finicity research for MS API connectivity
- #12 yfinance ETF dist_yield replacement *(stale — see Known Gaps above)*

### Commercial Product Track (3) — all Low, post-personal-validation
- #14 Watchlist/portfolio tracker persistence
- #15 Multi-user support infrastructure
- #16 Flat-fee subscription + onboarding

### Aesthetics and UX (1)
- #1 Watchlist — save top screener candidates

---

## Keeping This Current

**PowerPoint version:** `Voskuil_FP_Architecture.pptx` — a real, editable slide deck (native
shapes/connectors/tables, not a picture) generated from this same content, for anyone who prefers
dragging boxes around PowerPoint over reading markdown. Regenerate anytime by asking Claude, or
run `node tools/build_architecture_deck.js` directly (see that file's header for the full
command). Edit the .pptx freely in PowerPoint — if you want those edits folded back into this
file, re-upload the edited deck in a chat with Claude and it'll reconcile the content.

This file lives in the repo, not just Claude's Project Knowledge — same principle as the punch
list. Project Knowledge uploads are manual and lag behind reality (the old
`voskuil_fp_project_summary.md` is a good example of exactly this problem: still describes 3
pages, Polygon as primary, and the old 6-metric framework, months out of date).

**Recommendation:** upload this file to Project Knowledge once as a baseline, but treat the repo
copy as authoritative going forward. Claude should re-read this file from the repo each session
rather than relying on what's in Project Knowledge, the same way it already does for the punch
list.
