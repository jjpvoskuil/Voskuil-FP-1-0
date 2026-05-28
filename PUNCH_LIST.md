# Voskuil FP 1.0 — Punch List
*Persists across sessions · check off as you ship · add or delete anytime*
*Last updated: May 2026*

---

## IMMEDIATE / NEAR-TERM

- [ ] **Watchlist — save top screener candidates** `Urgent`
  Save tickers from Market Screener for later review. Needs session-state persistence or a lightweight store.

- [ ] **Button styling refinements in Holdings Explorer** `High`
  SEC (green), Yahoo (purple), Deep Dive (blue) — CSS targeting via st.markdown is fragile. Revisit approach.

- [ ] **Sync scoring weights across all three pages** `High`
  Each page (app.py, equity_scout.py, market_screener.py) has independent weight sliders. Changes don't propagate — same ticker can score differently on each page. Consider storing active weights in session state and reading on all pages.

---

## PHASE 4 — DEEPER ANALYSIS

- [ ] **SEC filing deep-dive links in Equity Scout** `High`
  Add direct click-through to actual SEC EDGAR filings from the Equity Scout analysis page.

- [ ] **Strategy-matched discovery scan** `High`
  Scan for stocks matching specific criteria: Dividend Aristocrats, commodity ETFs, Long Squeeze survivors.

---

## PHASE 5 — RETIREMENT MODELING

- [x] **Monte Carlo retirement modeling** `High` ✅ DONE
  Built in retirement_modeler.py — household model, 3 scenarios, spaghetti chart, percentile tester, goals/events table.

- [x] **Cash flow ladder visualization** `High` ✅ DONE
  Built into retirement modeler — income gap timeline, sequence of returns risk, cash buffer months metric.

- [ ] **Real-time tax monitoring (replace MS Parametric)** `Medium`
  Daily cap gains / tax-loss harvesting scanner to replace what Morgan Stanley Parametric currently does.

---

## DATA QUALITY IMPROVEMENTS

- [ ] **Separate maintenance vs growth capex** `Medium`
  Currently using total investing CF as proxy — conservative but imprecise. Investigate Polygon fields.

- [ ] **D&A direct from financials (not proxy)** `Medium`
  D&A proxy = Op CF - Net Income currently. New Massive v1 API has `depreciation_depletion_and_amortization` directly from CF statement — already wired in fetch_score_data but not yet in equity_scout fetch_fundamentals.

- [ ] **Historical score trending for a ticker** `Medium`
  How has conviction score changed over 3-5 years? Requires pulling multiple annual filings from Polygon.

---

## COMMERCIAL PRODUCT TRACK

- [ ] **Replace static CSVs with live MS account connection** `Medium`
  Connect directly to Morgan Stanley account data API to replace manual CSV uploads.
  Plaid infrastructure is BUILT (connect.py, plaid_data.py, app.py updated) but MS is blocking
  third-party data sharing despite setting being enabled. Financial planner contacted.
  Next steps: (1) wait 24-48hrs, (2) call MS 1-800-869-3326 to force-enable,
  (3) try toggling from mobile app. Plaid sandbox works fine (user_good/pass_good).
  Switch PLAID_ENV="sandbox" in secrets to test while waiting for MS production access.

- [ ] **Replace yfinance foreign ADR scoring with Morningstar via Rapid API** `Medium`
  Foreign companies (ASML, ARGX, others) have no SEC filings — Polygon returns nothing.
  Current fix: Pass 2 of scoring loop calls fetch_score_data_yfinance() for any stock
  Polygon can't score. Works but yfinance is unreliable. Morningstar via Rapid API ~$10/month
  covers full international fundamentals reliably.
  Implementation: replace fetch_score_data_yfinance() internals with Morningstar API call,
  keeping same output dict structure. Pass 2 routing and sleep timing already correct.
  Deferred: prove tool on personal portfolio before adding paid services beyond Massive.

- [ ] **Watchlist / portfolio tracker persistence across sessions** `Low`
  Saved tickers and notes persist between Streamlit sessions.
  Options: SQLite, Supabase, secrets-backed JSON.

- [ ] **Multi-user support infrastructure** `Low`
  Authentication, per-user portfolios, isolated sessions for commercial product launch.

- [ ] **Flat-fee subscription + onboarding flow** `Low`
  Payment infrastructure and new-user onboarding. Flat fee not AUM%. Target: middle-class investors.

---

## API / INFRASTRUCTURE

- [ ] **Massive API migration — June 22, 2026 DEADLINE** `Urgent`
  vX/reference/financials endpoint sunsets June 22, 2026.
  Migration to new v1 endpoints (income-statements, cash-flow-statements, balance-sheets) is BUILT
  in app.py, equity_scout.py, market_screener.py with automatic vX fallback.
  Blocker: new endpoints require Financials & Ratios Expansion add-on ($29/mo) on top of
  Stocks Starter ($29/mo). Add the add-on at massive.com dashboard before June 22.
  Once added, v1 path activates automatically — no code changes needed.
  After confirming v1 works, vX fallback blocks can be removed.

- [ ] **Replace yfinance with Polygon for ETF distribution yield** `Low`
  Polygon's distribution_yield field for ETFs is sparsely populated — dist_yield often None
  and rebalanced out of fund scores. Score based on expense ratio + 3yr return + beta only.
  When Polygon improves field coverage it picks up automatically.

---

## RECENTLY COMPLETED ✅

- [x] Fund / ETF scoring — Fund Health Framework (5 metrics, separate weight sliders, two-pass architecture)
- [x] Buffett Action Signal (BUY/HOLD/SELL) on all three pages with adjustable threshold sliders
- [x] Polygon v1 API migration with vX fallback
- [x] Plaid integration infrastructure (connect.py, plaid_data.py)
- [x] MoneyGuidePro iframe embed (financial_plan.py)
- [x] Retirement Modeler — household model, Monte Carlo, sequence of returns, Long Squeeze overlay
- [x] SEC + Yahoo buttons moved to Equity Scout page (removed from holdings table)
- [x] Foreign ADR scoring via yfinance Pass 2 (ASML, ARGX)
- [x] Mutual fund static expense ratio lookup (GIBIX, CMNIX, SPAXX, ABYIX, PDBZX)
- [x] Two-pass scoring architecture eliminating yfinance rate limits
- [x] Spaghetti chart with hover (percentile rank + ending balance per line)
- [x] Percentile pressure tester slider
- [x] Goals & Events table in retirement modeler (cars, travel, inheritance, gifts)
- [x] Spouse / household inputs in retirement modeler


