"""
sec_utils.py — SEC EDGAR filing fetcher for Voskuil FP 1.0

Two data paths:
1. fetch_10k_sections()    — Qualitative: pulls 10-K narrative text for Claude analysis.
2. fetch_company_facts()   — Quantitative: pulls XBRL Company Facts for scoring engine.

The Company Facts API (data.sec.gov/api/xbrl/companyfacts/) returns every
XBRL-tagged value from every filing ever submitted — the authoritative primary
source, free and permanent. Concept → field mapping is in edgar_concept_map.py.

Data model design: all historical annual periods are retained, not just the
latest. This is the foundation for 10-year ROIC trending (#34/#40), full-cycle
analysis (#37), and the historical normalization layer (#52).
"""

import re
import time
import threading
import requests
import concurrent.futures
import streamlit as st
from edgar_concept_map import (
    CONCEPT_MAP, FINANCIAL_SIC_CODES, CYCLICAL_SIC_CODES,
    BANK_SIC_CODES, INSURANCE_SIC_CODES, classify_financial_subtype,
)

EDGAR_BASE    = "https://data.sec.gov"
SEC_BASE      = "https://www.sec.gov"
HEADERS       = {"User-Agent": "VoskuilFP/1.0 jvoskuil@foxdenholdings.com"}
SECTION_LIMIT = 8_000

# ── Shared SEC rate limiter ─────────────────────────────────────────────
# SEC's fair-access policy caps requests at ~10/sec per source. The Market
# Screener's Stage 1 scan fires up to 8 concurrent worker threads, each
# making 2 requests per ticker (companyfacts + submissions) across
# thousands of tickers — with no shared throttle, that blows well past
# the limit and SEC starts returning 429s, which then also blocks
# unrelated pages (Compare Stocks, Equity Scout) hitting the same IP
# until the cooldown passes. This lock + timestamp pacing keeps the
# EFFECTIVE combined request rate across all threads under the limit,
# and _sec_get() retries a 429 (honoring Retry-After if SEC sends one)
# a few times before giving up, rather than failing immediately.
_rate_lock          = threading.Lock()
_last_request_time  = [0.0]
_MIN_REQUEST_INTERVAL = 0.12   # ~8 req/sec ceiling, safely under SEC's ~10/sec guidance


def _sec_get(url: str, timeout: int = 30, max_retries: int = 3):
    """
    Shared GET wrapper for every SEC EDGAR request in this module.
    Paces requests across ALL threads to stay under SEC's fair-access
    rate limit, and retries 429s (rate-limited) with backoff instead of
    surfacing an immediate failure.
    """
    for attempt in range(max_retries + 1):
        with _rate_lock:
            elapsed = time.monotonic() - _last_request_time[0]
            if elapsed < _MIN_REQUEST_INTERVAL:
                time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
            _last_request_time[0] = time.monotonic()

        resp = requests.get(url, headers=HEADERS, timeout=timeout)

        if resp.status_code == 429 and attempt < max_retries:
            retry_after = resp.headers.get("Retry-After")
            try:
                wait = float(retry_after) if retry_after else (2 ** attempt) * 1.0
            except ValueError:
                wait = (2 ** attempt) * 1.0
            time.sleep(min(wait, 15.0))
            continue

        return resp

    return resp  # exhausted retries — caller checks status_code


def get_cik(ticker: str):
    """
    Single-ticker CIK lookup. For bulk lookups (Market Screener scanning
    hundreds of tickers), use get_ticker_cik_map() instead and look up
    from the returned dict — this avoids re-downloading the full
    company_tickers.json file (10,000+ entries) on every call.
    """
    try:
        resp = _sec_get(f"{SEC_BASE}/files/company_tickers.json", timeout=10)
        if resp.status_code != 200:
            return None, f"company_tickers.json returned {resp.status_code}"
        for entry in resp.json().values():
            if entry.get("ticker", "").upper() == ticker.upper():
                return str(entry["cik_str"]).zfill(10), None
        return None, f"Ticker {ticker} not found in EDGAR tickers list"
    except Exception as e:
        return None, str(e)


def get_ticker_cik_map() -> dict:
    """
    Fetch the full EDGAR ticker -> CIK mapping ONCE.
    Returns {"AAPL": "0000320193", "MSFT": "0000789019", ...}

    This is the key optimization for bulk scanning (Market Screener):
    one ~1MB download instead of one redundant download per ticker.
    Cache this in st.session_state at the call site for the duration
    of a screen run.
    """
    try:
        resp = _sec_get(f"{SEC_BASE}/files/company_tickers.json", timeout=15)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        return {
            entry.get("ticker", "").upper(): str(entry["cik_str"]).zfill(10)
            for entry in data.values()
            if entry.get("ticker")
        }
    except Exception:
        return {}


def fetch_company_facts_with_cik(ticker: str, cik: str) -> dict:
    """
    Same as fetch_company_facts() but accepts a pre-resolved CIK,
    skipping the CIK lookup step entirely. Used by bulk scanners
    (Market Screener) that already built a ticker_cik_map once via
    get_ticker_cik_map().
    """
    if not cik:
        return {"latest": {}, "history": {}, "meta": {}, "missing": [],
                "error": f"No CIK provided for {ticker}"}
    return _fetch_company_facts_for_cik(ticker, cik)



def fetch_company_facts(ticker: str) -> dict:
    """
    Fetch XBRL Company Facts from SEC EDGAR for a given ticker.

    Returns a dict with two top-level keys:

    "latest"  → dict of scoring fields → most recent annual value
                 e.g. {"op_cf": 13335000000, "net_income": 8099000000, ...}
                 This is what the scoring engine consumes directly.

    "history" → dict of scoring fields → list of annual observations,
                 sorted oldest → newest:
                 e.g. {"op_cf": [
                     {"period": "2015", "end": "2015-08-30", "value": 4285000000},
                     {"period": "2016", "end": "2016-08-28", "value": 4601000000},
                     ...
                 ]}
                 This powers 10-year ROIC trending (#34/#40), full-cycle
                 analysis (#37), and the historical normalization layer (#52).

    Also returns:
    "meta"    → {"ticker", "cik", "company_name", "sic", "is_financial",
                  "is_cyclical", "fiscal_year_end", "last_annual_period"}
    "error"   → None on success, error string on failure
    "missing" → list of scoring fields not found in this company's XBRL data
    """
    cik, err = get_cik(ticker)
    if not cik:
        return {"latest": {}, "history": {}, "meta": {}, "missing": [],
                "error": f"CIK lookup failed: {err}"}
    return _fetch_company_facts_for_cik(ticker, cik)


def _roic_denominator_reliable(net_income, invested_cap) -> bool:
    """
    True if invested_cap is a large enough, positive capital base for
    net_income / invested_cap to be an economically meaningful return
    figure — not just an equation.

    Guards against companies with negative or near-zero book equity, a
    common pattern for aggressive-buyback compounders (VeriSign,
    Domino's, AutoZone, etc.). Invested capital (equity + debt) for
    these businesses can swing through zero year to year — and when it
    does, net_income / invested_cap produces triple- or quadruple-digit
    swings that are purely a denominator artifact, not a real return.
    Confirmed empirically: VeriSign's raw 10-yr average ROIC computed as
    +273% against a latest-year figure of -87% for the SAME business,
    because one or more years in the window had invested capital near
    zero.

    Requires invested_cap to be positive AND at least half the
    magnitude of that year's net income, so the ratio itself can't
    exceed roughly 200% purely from a thin capital base. This is a
    materiality floor relative to earnings, not company size, so it
    doesn't suppress genuinely high ROIC from real capital-light
    compounders — only denominators too small to be trustworthy.
    """
    if invested_cap is None or net_income is None:
        return False
    if invested_cap <= 0:
        return False
    return invested_cap >= 0.5 * abs(net_income)


def _fetch_company_facts_for_cik(ticker: str, cik: str) -> dict:
    """Internal: does the actual Company Facts fetch + parse once CIK is known."""
    # Fetch Company Facts JSON
    # This returns ALL XBRL concepts ever filed — typically 2-8MB for large caps
    url = f"{EDGAR_BASE}/api/xbrl/companyfacts/CIK{cik}.json"
    try:

        resp = _sec_get(url, timeout=30)
        if resp.status_code != 200:
            return {"latest": {}, "history": {}, "meta": {}, "missing": [],
                    "error": f"Company Facts API returned {resp.status_code} for CIK {cik}",
                    "status_code": resp.status_code}
        data = resp.json()
    except requests.Timeout:
        return {"latest": {}, "history": {}, "meta": {}, "missing": [],
                "error": "Timeout fetching Company Facts (>30s)", "status_code": None}
    except Exception as e:
        return {"latest": {}, "history": {}, "meta": {}, "missing": [],
                "status_code": None,
                "error": f"Error fetching Company Facts: {e}"}

    # 3. Extract company metadata
    entity_name = data.get("entityName", ticker)
    facts       = data.get("facts", {})
    us_gaap     = facts.get("us-gaap", {})

    # Get SIC from submissions API (lightweight call, cached by EDGAR)
    sic = None
    try:
        sub_resp = _sec_get(f"{EDGAR_BASE}/submissions/CIK{cik}.json", timeout=10)
        if sub_resp.status_code == 200:
            sic = str(sub_resp.json().get("sic", ""))
    except Exception:
        pass

    meta = {
        "ticker":            ticker.upper(),
        "cik":               cik,
        "company_name":      entity_name,
        "sic":               sic,
        "is_financial":      sic in FINANCIAL_SIC_CODES if sic else False,
        "is_cyclical":       sic in CYCLICAL_SIC_CODES  if sic else False,
        # "bank" | "insurance" | "other_financial" | None — see #36. Only
        # "bank"/"insurance" get an alternative scoring path today;
        # "other_financial" (brokers/REITs/real estate/investment offices)
        # still respects the existing skip/exclude behavior.
        "financial_subtype": classify_financial_subtype(sic),
        "last_annual_period": None,
        "fiscal_year_end":   None,
    }

    # 4. For each scoring field, merge observations across ALL matching
    # concept candidates — not just the first one with any data.
    #
    # Bug this fixes: companies routinely change which XBRL tag they use
    # for the same line item over time (e.g. ASC 606 forced most filers
    # to switch revenue tags from "Revenues" to
    # "RevenueFromContractWithCustomerExcludingAssessedTax" around 2018;
    # equity/debt tags get similarly deprecated/renamed across taxonomy
    # updates). The old logic tried concepts in priority order and
    # STOPPED at the first one with any data — so a large, long-tenured
    # filer whose preferred tag only covers a handful of recent (or
    # early) fiscal years would get its history silently truncated to
    # just that span, even though a full multi-decade history exists
    # across two or three tag generations combined. This is what was
    # producing false "limited history" flags on mega-caps that
    # obviously have plenty of EDGAR history.
    #
    # Fix: merge every concept's annual observations into one history
    # keyed by fiscal period-end date. Priority order is preserved only
    # as a tie-breaker — if two tags both report the SAME period end
    # (companies sometimes double-tag during a transition year), the
    # higher-priority (earlier-listed) tag's value wins.
    latest  = {}   # field → most recent annual value (float or None)
    history = {}   # field → sorted list of annual observations

    all_annual_ends = []  # track all period end dates to find fiscal year

    for field, concepts in CONCEPT_MAP.items():
        merged_by_end = {}  # end date -> observation dict

        for concept in concepts:
            if concept not in us_gaap:
                continue

            concept_data = us_gaap[concept]
            units        = concept_data.get("units", {})

            # Most financial concepts use USD; shares use "shares"
            unit_key = "USD"
            if field in ("diluted_shares",):
                unit_key = "shares"
                if unit_key not in units:
                    unit_key = "USD"  # some filers tag shares in USD units

            observations = units.get(unit_key, [])
            if not observations:
                # Try the other unit key as fallback
                alt = "shares" if unit_key == "USD" else "USD"
                observations = units.get(alt, [])

            # Filter to annual (10-K) filings only
            # EDGAR uses "form" field: "10-K", "10-K/A", "20-F" (foreign filers)
            # Also validate period duration — annual periods span ~340-370 days.
            # Some concepts include quarterly sub-period values even inside 10-K filings
            # (segment data, interim comparatives). Filter those out by duration.
            def is_annual_duration(obs):
                start = obs.get("start")
                end   = obs.get("end")
                if not start or not end:
                    return True  # no dates to check — allow through
                try:
                    from datetime import date
                    d0 = date.fromisoformat(start)
                    d1 = date.fromisoformat(end)
                    days = (d1 - d0).days
                    return 340 <= days <= 400  # full fiscal year window
                except Exception:
                    return True

            annual_obs = [
                o for o in observations
                if o.get("form") in ("10-K", "10-K/A", "20-F", "20-F/A")
                and o.get("end")
                and is_annual_duration(o)
            ]

            if not annual_obs:
                continue

            # Within THIS concept, if multiple entries share the same end
            # date (e.g. original + amended), prefer the latest filed
            seen_ends = {}
            for o in sorted(annual_obs, key=lambda x: x.get("filed", "")):
                seen_ends[o["end"]] = o

            # Merge into the field's combined history. Since concepts are
            # processed in priority order, only add an end date if a
            # higher-priority concept hasn't already claimed it.
            for end, o in seen_ends.items():
                if o.get("val") is None:
                    continue
                if end not in merged_by_end:
                    merged_by_end[end] = {
                        "period": o["end"][:4],          # fiscal year as string e.g. "2024"
                        "end":    o["end"],               # exact period end date
                        "value":  o.get("val"),           # raw value in USD or shares
                        "filed":  o.get("filed", ""),     # filing date
                        "form":   o.get("form", ""),
                    }

        field_history = sorted(merged_by_end.values(), key=lambda x: x["end"])

        if field_history:
            all_annual_ends.extend([h["end"] for h in field_history])
            history[field] = field_history
            latest[field]  = field_history[-1]["value"]  # most recent annual

    # 5. Identify missing fields
    missing = [f for f in CONCEPT_MAP if f not in latest]

    # 6. Determine last annual period and fiscal year end
    if all_annual_ends:
        last_end = max(all_annual_ends)
        meta["last_annual_period"] = last_end[:4]
        meta["fiscal_year_end"]    = last_end

    # 7. Compute derived fields on the latest period
    # These are stored in latest[] so the scoring engine can use them directly
    op_cf   = latest.get("op_cf")
    inv_cf  = latest.get("inv_cf")
    capex   = latest.get("capex")
    net_inc = latest.get("net_income")
    dna     = latest.get("dna")
    eq      = latest.get("total_equity")
    ltd     = latest.get("long_term_debt", 0) or 0
    std     = latest.get("short_term_debt", 0) or 0
    cash    = latest.get("cash", 0) or 0
    op_inc  = latest.get("op_income")
    int_pd  = latest.get("interest_paid") or latest.get("interest_expense")

    # FCF: operating CF + investing CF (investing is negative, so this subtracts capex proxy)
    if op_cf is not None and inv_cf is not None:
        latest["fcf"] = op_cf + inv_cf
    elif op_cf is not None and capex is not None:
        latest["fcf"] = op_cf - abs(capex)

    # Invested capital
    if eq is not None:
        latest["invested_cap"] = eq + ltd + std

    # Total debt
    latest["total_debt"] = ltd + std

    # Net debt
    latest["net_debt"] = ltd + std - cash

    # ROIC — guarded against near-zero/negative invested capital; see
    # _roic_denominator_reliable(). Shows N/A rather than a technically-
    # computed but economically meaningless ratio for negative-equity
    # buyback compounders.
    inv_cap = latest.get("invested_cap")
    if net_inc is not None and _roic_denominator_reliable(net_inc, inv_cap):
        latest["roic"] = net_inc / inv_cap

    # Negative equity flag — surfaced independently of whether it happens
    # to distort ROIC in the CURRENT window, because it's meaningful
    # information in its own right (debt/float-funded capital structure).
    if eq is not None:
        latest["is_negative_equity"] = eq < 0

    # Debt / FCF
    fcf = latest.get("fcf")
    if fcf and fcf > 0 and (ltd + std) > 0:
        latest["debt_to_fcf"] = (ltd + std) / fcf

    # Gross margin — with sanity check and COGS fallback
    # Some companies (e.g. NVDA) don't tag GrossProfit cleanly in XBRL,
    # causing multi-period rollup values that make GM appear > 100%.
    # Fix: validate GM is in (0, 1], fall back to Revenue - COGS if not.
    rev = latest.get("revenue")
    gp  = latest.get("gross_profit")
    cor = latest.get("cost_of_revenue")

    if gp is not None and rev is not None and rev > 0:
        gm_check = gp / rev
        if gm_check > 1.0 or gm_check < -0.5:
            # Bad XBRL value — try COGS fallback
            if cor is not None and rev is not None:
                gp = rev - cor
                latest["gross_profit"] = gp
            else:
                gp = None
                latest["gross_profit"] = None
    elif gp is None and rev is not None and cor is not None:
        # GrossProfit concept not tagged — derive from COGS
        gp = rev - cor
        latest["gross_profit"] = gp

    if rev and rev > 0 and gp is not None:
        gm = gp / rev
        # Final sanity gate — if still nonsensical after COGS fallback, null it
        if 0.0 <= gm <= 1.0:
            latest["gross_margin"] = gm

    # Interest coverage
    if op_inc is not None and int_pd and int_pd > 0:
        latest["int_coverage"] = op_inc / int_pd

    # Owner earnings (Buffett: net income + D&A - maintenance capex)
    capex_val = capex if capex is not None else (inv_cf if inv_cf is not None else None)
    if net_inc is not None and dna is not None and capex_val is not None:
        latest["owner_earnings"] = net_inc + dna - abs(capex_val)
    elif net_inc is not None and op_cf is not None:
        # Proxy: use op_cf - net_income as D&A proxy when D&A not available
        dna_proxy = op_cf - net_inc
        if capex_val is not None:
            latest["owner_earnings"] = net_inc + dna_proxy - abs(capex_val)

    # FCF Margin — funnel gate metric (#63): quality-of-revenue check,
    # independent of Gross Margin (#31 removed GM as a universal moat proxy).
    rev_for_margin = latest.get("revenue")
    fcf_for_margin = latest.get("fcf")
    if fcf_for_margin is not None and rev_for_margin:
        latest["fcf_margin"] = fcf_for_margin / rev_for_margin

    # Cash Available for Debt Service (CADS) — unlevered cash proxy used by
    # the refined debt gate (#32) and the interest-margin check (#35):
    # operating income + D&A - capex, i.e. cash generated by the business
    # before financing costs. Deliberately pre-interest so debt capacity
    # isn't assessed circularly against cash that's already net of debt
    # service.
    if op_inc is not None and dna is not None:
        latest["cash_available_debt_service"] = op_inc + dna - (abs(capex) if capex is not None else 0)

    # Debt / Net Income — simple funnel-gate debt check (#63). Deliberately
    # crude (accrual-basis, ignores capital structure nuance) so it's cheap
    # to compute for every ticker in a full-market scan; the refined
    # CADS-based multiple below is the more faithful #32 check.
    total_debt_latest = ltd + std
    if net_inc and net_inc > 0 and total_debt_latest > 0:
        latest["debt_to_ni"] = total_debt_latest / net_inc

    # Debt / CADS — refined funnel-gate debt check (#32): outstanding
    # principal against unlevered cash generation rather than carrying-
    # value debt against standard FCF. Explicitly does NOT penalize
    # negative-working-capital float users (insurers, etc.) the way a
    # naive FCF-based multiple would, since CADS is built from operating
    # income rather than reported FCF.
    cads_latest = latest.get("cash_available_debt_service")
    if cads_latest and cads_latest > 0 and total_debt_latest > 0:
        latest["debt_to_cads"] = total_debt_latest / cads_latest

    # Interest paid as a % of CADS — cash-basis interest margin (#35),
    # shown alongside the principal multiple rather than replacing it.
    if cads_latest and cads_latest > 0 and int_pd:
        latest["interest_margin_cads"] = int_pd / cads_latest

    # ── Financial firm derived metrics (latest period) — #36 ──────────────
    # Computed whenever the underlying raw fields exist, which in practice
    # means only for filers that actually tag them (banks and insurers).
    # A non-financial company simply won't have interest_income/
    # noninterest_income/premiums_earned/etc. tagged at all, so these stay
    # absent for it rather than producing nonsense numbers — no is_financial
    # gate needed here, the data availability does the gating.
    int_inc      = latest.get("interest_income")
    noninc       = latest.get("noninterest_income")
    nonexp       = latest.get("noninterest_expense")
    provision    = latest.get("provision_credit_losses")
    total_assets = latest.get("total_assets")
    premiums     = latest.get("premiums_earned")
    ph_benefits  = latest.get("policyholder_benefits")
    uw_exp       = latest.get("underwriting_expenses")

    # ROE — return on equity, the standard profitability yardstick for a
    # leveraged balance-sheet business, where ROIC (which treats leverage
    # as a cost rather than the business model itself) doesn't apply the
    # same way it does to an industrial or tech company.
    if net_inc is not None and eq is not None and eq > 0:
        latest["roe"] = net_inc / eq

    # Equity / Assets — capital cushion / leverage proxy, substituting for
    # Debt/FCF as the solvency signal. A debt-multiple gate is meaningless
    # for a bank (deposits/borrowings ARE the raw material of the
    # business), so this measures how much of the balance sheet is
    # loss-absorbing equity instead.
    if eq is not None and total_assets:
        latest["equity_to_assets"] = eq / total_assets

    # Net interest income + NIM proxy. True net interest margin divides by
    # AVERAGE EARNING ASSETS, which EDGAR doesn't tag as a standalone
    # concept anywhere — total assets is used as a workable stand-in here,
    # clearly labeled "proxy" everywhere it's surfaced. It understates true
    # NIM (total assets includes non-earning assets like goodwill and
    # premises) but is consistent enough year-over-year and company-to-
    # company to rank on.
    if int_inc is not None and int_pd is not None:
        nii = int_inc - int_pd
        latest["net_interest_income"] = nii
        if total_assets:
            latest["nim_proxy"] = nii / total_assets

        # Efficiency ratio — noninterest expense as a share of total
        # revenue (net interest income + noninterest income). Lower is
        # better; bank-land's version of a cost-discipline metric, since
        # Gross Margin (excluded from scoring generally, per #31) doesn't
        # exist as a concept for a bank at all.
        if nonexp is not None:
            total_rev = nii + (noninc or 0)
            if total_rev > 0:
                latest["efficiency_ratio"] = nonexp / total_rev

    # Provision / Net Income — credit cost as a share of earnings. High or
    # rising provisions relative to earnings are an early credit-quality
    # warning sign.
    if provision is not None and net_inc and net_inc > 0:
        latest["provision_to_ni"] = provision / net_inc

    # Combined ratio (insurers) — (losses incurred + underwriting expense)
    # / premiums earned. Under 100% means the insurer made an underwriting
    # profit; over 100% means it's relying on investment income from the
    # float to be profitable overall — the classic Buffett/Berkshire
    # distinction between a good insurer and a mediocre one.
    if premiums and ph_benefits is not None:
        combined_num = ph_benefits + (uw_exp or 0)
        latest["combined_ratio"] = combined_num / premiums

    # 8. Compute the SAME derived metrics for every historical year, not just
    # the latest — this is what powers Compare Stocks' historical trend
    # charts (#60) for derived fields. Without this, history[] only ever
    # contains raw XBRL line items and the trend chart has nothing to plot
    # for FCF, ROIC, Gross Margin, Debt/FCF, Interest Coverage, or Owner
    # Earnings. Mirrors the latest-only logic above exactly, year by year,
    # matched by period end date across the underlying raw series.
    #
    # Not included here: FCF Yield and Price/Owner Earnings. Both need a
    # historical share price/market cap per fiscal year end, which EDGAR
    # doesn't provide (yfinance only gives current price in this app today).
    # Trending those would require a separate historical price fetch — worth
    # a future punch list item if wanted.
    def _hist_map(field):
        return {h["end"]: h["value"] for h in history.get(field, []) if h.get("value") is not None}

    op_cf_h  = _hist_map("op_cf")
    inv_cf_h = _hist_map("inv_cf")
    capex_h  = _hist_map("capex")
    net_inc_h = _hist_map("net_income")
    dna_h    = _hist_map("dna")
    eq_h     = _hist_map("total_equity")
    ltd_h    = _hist_map("long_term_debt")
    std_h    = _hist_map("short_term_debt")
    op_inc_h = _hist_map("op_income")
    int_pd_h  = _hist_map("interest_paid")
    int_exp_h = _hist_map("interest_expense")
    rev_h    = _hist_map("revenue")
    gp_h     = _hist_map("gross_profit")
    cor_h    = _hist_map("cost_of_revenue")

    # Financial firm raw fields (#36) — same merge-by-end-date approach
    int_inc_h     = _hist_map("interest_income")
    noninc_h      = _hist_map("noninterest_income")
    nonexp_h      = _hist_map("noninterest_expense")
    provision_h   = _hist_map("provision_credit_losses")
    total_assets_h = _hist_map("total_assets")
    premiums_h    = _hist_map("premiums_earned")
    ph_benefits_h = _hist_map("policyholder_benefits")
    uw_exp_h      = _hist_map("underwriting_expenses")

    all_ends = sorted(set(op_cf_h) | set(net_inc_h) | set(rev_h) | set(int_inc_h) | set(premiums_h))

    fcf_hist, gm_hist, roic_hist, dtf_hist, ic_hist, oe_hist = [], [], [], [], [], []
    fcfm_hist, cads_hist, dni_hist, dcads_hist = [], [], [], []
    roe_hist, eq_assets_hist, nim_hist, eff_ratio_hist = [], [], [], []
    prov_ni_hist, combined_ratio_hist = [], []

    for end in all_ends:
        period = end[:4]
        ocf = op_cf_h.get(end)
        icf = inv_cf_h.get(end)
        cpx = capex_h.get(end)

        # FCF
        fcf_val = None
        if ocf is not None and icf is not None:
            fcf_val = ocf + icf
        elif ocf is not None and cpx is not None:
            fcf_val = ocf - abs(cpx)
        if fcf_val is not None:
            fcf_hist.append({"period": period, "end": end, "value": fcf_val})

        # Gross margin (same sanity-check + COGS fallback as latest-period)
        rev = rev_h.get(end)
        gp  = gp_h.get(end)
        cor = cor_h.get(end)
        if gp is not None and rev and rev > 0:
            gm_check = gp / rev
            if gm_check > 1.0 or gm_check < -0.5:
                gp = (rev - cor) if cor is not None else None
        elif gp is None and rev is not None and cor is not None:
            gp = rev - cor
        if rev and rev > 0 and gp is not None:
            gm = gp / rev
            if 0.0 <= gm <= 1.0:
                gm_hist.append({"period": period, "end": end, "value": gm})

        # ROIC — guarded against near-zero/negative invested capital; see
        # _roic_denominator_reliable(). This is THE critical guard: an
        # unguarded historical average is what let VeriSign's 10-yr avg
        # ROIC come out to +273% (vs. a latest-year figure of -87% for
        # the same business) — one bad-denominator year distorts the
        # whole average, not just that year's figure.
        ni  = net_inc_h.get(end)
        eq  = eq_h.get(end)
        ltd = ltd_h.get(end, 0) or 0
        std = std_h.get(end, 0) or 0
        if eq is not None:
            inv_cap = eq + ltd + std
            if ni is not None and _roic_denominator_reliable(ni, inv_cap):
                roic_hist.append({"period": period, "end": end, "value": ni / inv_cap})

        # Debt / FCF
        if fcf_val and fcf_val > 0 and (ltd + std) > 0:
            dtf_hist.append({"period": period, "end": end, "value": (ltd + std) / fcf_val})

        # Interest coverage — cash-basis preferred, same as latest-period
        op_inc = op_inc_h.get(end)
        int_pd = int_pd_h.get(end) or int_exp_h.get(end)
        if op_inc is not None and int_pd and int_pd > 0:
            ic_hist.append({"period": period, "end": end, "value": op_inc / int_pd})

        # Owner earnings
        capex_val_y = cpx if cpx is not None else icf
        if ni is not None and dna_h.get(end) is not None and capex_val_y is not None:
            oe_hist.append({"period": period, "end": end,
                             "value": ni + dna_h.get(end) - abs(capex_val_y)})
        elif ni is not None and ocf is not None and capex_val_y is not None:
            dna_proxy_y = ocf - ni
            oe_hist.append({"period": period, "end": end,
                             "value": ni + dna_proxy_y - abs(capex_val_y)})

        # FCF Margin — funnel gate metric (#63)
        if fcf_val is not None and rev and rev > 0:
            fcfm_hist.append({"period": period, "end": end, "value": fcf_val / rev})

        # Cash Available for Debt Service (CADS) — funnel gate metrics (#32/#35)
        dna_y = dna_h.get(end)
        cads_val = None
        if op_inc is not None and dna_y is not None:
            cads_val = op_inc + dna_y - (abs(cpx) if cpx is not None else 0)
            cads_hist.append({"period": period, "end": end, "value": cads_val})

        total_debt_y = ltd + std

        # Debt / Net Income — simple funnel gate (#63)
        if ni and ni > 0 and total_debt_y > 0:
            dni_hist.append({"period": period, "end": end, "value": total_debt_y / ni})

        # Debt / CADS — refined funnel gate (#32)
        if cads_val and cads_val > 0 and total_debt_y > 0:
            dcads_hist.append({"period": period, "end": end, "value": total_debt_y / cads_val})

        # ── Financial firm derived metrics (#36) — same logic as latest-period
        ta_y   = total_assets_h.get(end)
        ii_y   = int_inc_h.get(end)
        noninc_y = noninc_h.get(end)
        nonexp_y = nonexp_h.get(end)
        prov_y   = provision_h.get(end)
        prem_y   = premiums_h.get(end)
        phb_y    = ph_benefits_h.get(end)
        uwe_y    = uw_exp_h.get(end)

        if ni is not None and eq is not None and eq > 0:
            roe_hist.append({"period": period, "end": end, "value": ni / eq})

        if eq is not None and ta_y:
            eq_assets_hist.append({"period": period, "end": end, "value": eq / ta_y})

        if ii_y is not None and int_pd is not None:
            nii_y = ii_y - int_pd
            if ta_y:
                nim_hist.append({"period": period, "end": end, "value": nii_y / ta_y})
            if nonexp_y is not None:
                total_rev_y = nii_y + (noninc_y or 0)
                if total_rev_y > 0:
                    eff_ratio_hist.append({"period": period, "end": end, "value": nonexp_y / total_rev_y})

        if prov_y is not None and ni and ni > 0:
            prov_ni_hist.append({"period": period, "end": end, "value": prov_y / ni})

        if prem_y and phb_y is not None:
            combined_ratio_hist.append({"period": period, "end": end,
                                         "value": (phb_y + (uwe_y or 0)) / prem_y})

    if fcf_hist:   history["fcf"]                        = fcf_hist
    if gm_hist:    history["gross_margin"]                = gm_hist
    if roic_hist:  history["roic"]                        = roic_hist
    if dtf_hist:   history["debt_to_fcf"]                 = dtf_hist
    if ic_hist:    history["interest_coverage"]           = ic_hist
    if oe_hist:    history["owner_earnings"]               = oe_hist
    if fcfm_hist:  history["fcf_margin"]                  = fcfm_hist
    if cads_hist:  history["cash_available_debt_service"] = cads_hist
    if dni_hist:   history["debt_to_ni"]                  = dni_hist
    if dcads_hist: history["debt_to_cads"]                = dcads_hist
    if roe_hist:          history["roe"]                  = roe_hist
    if eq_assets_hist:    history["equity_to_assets"]      = eq_assets_hist
    if nim_hist:          history["nim_proxy"]             = nim_hist
    if eff_ratio_hist:    history["efficiency_ratio"]      = eff_ratio_hist
    if prov_ni_hist:      history["provision_to_ni"]       = prov_ni_hist
    if combined_ratio_hist: history["combined_ratio"]      = combined_ratio_hist

    return {
        "latest":  latest,
        "history": history,
        "meta":    meta,
        "missing": missing,
        "error":   None,
    }



def get_latest_10k_accession(cik: str):
    """
    Returns (accession_dashed, filing_date, error).
    Skips 10-K/A amendments — we want the original filing.
    """
    try:
        resp = _sec_get(f"{EDGAR_BASE}/submissions/CIK{cik}.json", timeout=10)
        if resp.status_code != 200:
            return None, None, f"submissions API returned {resp.status_code}"
        data   = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        forms  = recent.get("form", [])
        accnos = recent.get("accessionNumber", [])
        dates  = recent.get("filingDate", [])

        # Prefer original 10-K over 10-K/A amendment
        for i, form in enumerate(forms):
            if form == "10-K":
                return accnos[i], dates[i], None

        # Fall back to 10-K/A if no original found
        for i, form in enumerate(forms):
            if form == "10-K/A":
                return accnos[i], dates[i], None

        # Check older filing pages
        for file_entry in data.get("filings", {}).get("files", []):
            fname    = file_entry.get("name", "")
            sub_resp = _sec_get(f"{EDGAR_BASE}/submissions/{fname}", timeout=10)
            if sub_resp.status_code == 200:
                sub = sub_resp.json()
                for i, form in enumerate(sub.get("form", [])):
                    if form == "10-K":
                        return sub["accessionNumber"][i], sub["filingDate"][i], None

        return None, None, "No 10-K found in submissions"
    except Exception as e:
        return None, None, str(e)


def get_complete_submission_url(cik: str, accession_dashed: str) -> str:
    """
    Build the URL for the complete submission .txt file.
    Format: /Archives/edgar/data/{cik_int}/{accession_nodash}/{accession_dashed}.txt
    """
    cik_int          = str(int(cik))
    accession_nodash = accession_dashed.replace("-", "")
    return (
        f"{SEC_BASE}/Archives/edgar/data/{cik_int}/"
        f"{accession_nodash}/{accession_dashed}.txt"
    )


def extract_10k_body(submission_text: str) -> str:
    """
    Parse the complete submission .txt file and extract the 10-K body document.

    The .txt format wraps each document like:
        <DOCUMENT>
        <TYPE>10-K
        <SEQUENCE>1
        <FILENAME>mo-20241231.htm
        <DESCRIPTION>10-K
        <TEXT>
        ...actual filing content...
        </TEXT>
        </DOCUMENT>

    We find the first DOCUMENT block with TYPE=10-K and extract its TEXT content.
    """
    # Find all DOCUMENT blocks
    doc_blocks = re.split(r'<DOCUMENT>', submission_text, flags=re.IGNORECASE)

    for block in doc_blocks[1:]:  # skip content before first <DOCUMENT>
        # Get the TYPE for this block
        type_match = re.search(r'<TYPE>\s*(\S+)', block, re.IGNORECASE)
        if not type_match:
            continue
        doc_type = type_match.group(1).strip().upper()

        if doc_type != "10-K":
            continue

        # Extract text between <TEXT> and </TEXT>
        text_match = re.search(r'<TEXT>(.*?)(?:</TEXT>|</DOCUMENT>)', block, re.IGNORECASE | re.DOTALL)
        if text_match:
            return text_match.group(1).strip()

    return ""


def clean_filing_text(raw: str) -> str:
    """
    Strip HTML/SGML tags and clean the filing body text.
    Handles both plain text and HTML-wrapped filings.
    """
    # Remove SGML/HTML tags
    clean = re.sub(r'<[^>]+>', ' ', raw)
    # Decode common HTML entities
    clean = clean.replace('&nbsp;', ' ')
    clean = clean.replace('&amp;',  '&')
    clean = clean.replace('&lt;',   '<')
    clean = clean.replace('&gt;',   '>')
    clean = re.sub(r'&[a-zA-Z#0-9]+;', ' ', clean)
    # Collapse whitespace
    clean = re.sub(r'[ \t]+', ' ', clean)
    clean = re.sub(r'\n{3,}', '\n\n', clean)
    clean = clean.strip()
    return clean


def extract_sections(clean_text: str) -> dict:
    """
    Extract key 10-K sections from cleaned plain text.
    Uses multiple pattern strategies to handle different filing formats.
    """
    # Primary patterns — match "Item N." or "Item N " in any capitalisation
    item_patterns = {
        "business":     r'Item\s+1(?:\.|\s)(?!A\b).{0,80}?Business\b',
        "risk_factors": r'Item\s+1A(?:\.|\s).{0,80}?Risk\s+Factor',
        "mda":          r'Item\s+7(?:\.|\s)(?!A\b).{0,100}?(?:Management|MD&A).{0,60}?(?:Discussion|Analysis)',
        "quantitative": r'Item\s+7A(?:\.|\s).{0,80}?Quantitative',
    }

    positions = {}
    for key, pattern in item_patterns.items():
        matches = list(re.finditer(pattern, clean_text, re.IGNORECASE))
        # Skip table of contents — use second occurrence if available
        if len(matches) >= 2:
            positions[key] = matches[1].start()
        elif len(matches) == 1:
            positions[key] = matches[0].start()

    # Fallback: simpler numeric patterns
    if len(positions) < 2:
        fallback = {
            "business":     r'(?:^|\n)\s*1\.\s{1,10}Business\b',
            "risk_factors": r'(?:^|\n)\s*1A\.\s{1,10}Risk',
            "mda":          r'(?:^|\n)\s*7\.\s{1,10}(?:Management|MD&A)',
            "quantitative": r'(?:^|\n)\s*7A\.\s{1,10}Quantitative',
        }
        for key, pattern in fallback.items():
            if key not in positions:
                matches = list(re.finditer(pattern, clean_text, re.IGNORECASE | re.MULTILINE))
                if len(matches) >= 2:
                    positions[key] = matches[1].start()
                elif len(matches) == 1:
                    positions[key] = matches[0].start()

    # Last resort: return a large body chunk
    if not positions:
        mid = clean_text[5_000:29_000]
        return {"business": mid} if mid else {}

    sections    = {}
    sorted_keys = sorted(positions.keys(), key=lambda k: positions[k])
    for i, key in enumerate(sorted_keys):
        start = positions[key]
        end   = positions[sorted_keys[i + 1]] if i + 1 < len(sorted_keys) else start + SECTION_LIMIT
        end   = min(end, start + SECTION_LIMIT)
        sections[key] = clean_text[start:end].strip()

    return sections


def fetch_10k_sections(ticker: str) -> dict:
    """
    Main entry point. Fetches the complete submission .txt file from EDGAR
    and extracts 10-K narrative sections for qualitative analysis.

    Returns dict: {sections, filing_url, doc_url, filing_date, error}
    """
    # 1. Resolve ticker -> CIK
    cik, err = get_cik(ticker)
    if not cik:
        return {"sections": {}, "filing_url": None,
                "error": f"CIK lookup failed: {err}"}

    # 2. Find most recent 10-K accession number
    accession, filing_date, err = get_latest_10k_accession(cik)
    if not accession:
        return {"sections": {}, "filing_url": None,
                "error": f"10-K accession lookup failed: {err}"}

    # 3. Build filing index URL (for display)
    cik_int          = str(int(cik))
    accession_nodash = accession.replace("-", "")
    index_url = (
        f"{SEC_BASE}/Archives/edgar/data/{cik_int}/"
        f"{accession_nodash}/{accession}-index.htm"
    )

    # 4. Fetch the complete submission .txt file
    # These files can be 20MB+. We stream and stop after capturing the 10-K body
    # to avoid loading the entire file (exhibits can be huge).
    txt_url = get_complete_submission_url(cik, accession)
    try:
        resp = requests.get(txt_url, headers=HEADERS, timeout=60, stream=True)
        if resp.status_code != 200:
            return {"sections": {}, "filing_url": index_url,
                    "error": f"Complete submission file returned HTTP {resp.status_code}. URL: {txt_url}"}

        # Read in chunks, stop once we've found and closed the 10-K DOCUMENT block
        MAX_BYTES      = 15 * 1024 * 1024  # 15MB cap
        chunks         = []
        total          = 0
        found_10k_end  = False

        for chunk in resp.iter_content(chunk_size=65536):
            if chunk:
                chunks.append(chunk.decode('utf-8', errors='replace'))
                total += len(chunk)
                partial = ''.join(chunks)
                # Stop once we've passed the first 10-K </DOCUMENT> block
                if re.search(r'<TYPE>10-K', partial, re.IGNORECASE):
                    end_pos = partial.find('</DOCUMENT>', partial.find('<TYPE>10-K'))
                    if end_pos > -1:
                        chunks = [partial[:end_pos + 11]]
                        found_10k_end = True
                        break
                if total >= MAX_BYTES:
                    break

        submission_text = ''.join(chunks)

    except requests.Timeout:
        return {"sections": {}, "filing_url": index_url,
                "error": "Timeout fetching complete submission file (>60s)."}
    except Exception as e:
        return {"sections": {}, "filing_url": index_url,
                "error": f"Error fetching submission file: {e}"}

    # 5. Extract the 10-K body from the submission
    body = extract_10k_body(submission_text)
    if not body:
        return {"sections": {}, "filing_url": index_url,
                "error": f"Could not find 10-K body in complete submission file ({len(submission_text):,} chars). "
                         f"File may use an unexpected format."}

    # 6. Clean the text
    clean_text = clean_filing_text(body)
    if len(clean_text) < 5_000:
        return {"sections": {}, "filing_url": index_url,
                "error": f"10-K body cleaned to only {len(clean_text):,} chars — likely corrupt or empty."}

    # 7. Extract sections
    sections = extract_sections(clean_text)
    if not sections:
        return {"sections": {}, "filing_url": index_url,
                "error": f"Extracted 10-K body ({len(clean_text):,} chars) but could not locate Item sections."}

    return {
        "sections":    sections,
        "filing_url":  index_url,
        "doc_url":     txt_url,
        "filing_date": filing_date,
        "error":       None,
    }


# ── Shared helpers: value formatting + fundamentals fetch ────────────────────
# Moved here from app_pages/7_Equity_Scout_EDGAR.py (originally page-local) so that
# any page needing full fundamentals — Equity Scout, Market Screener deep-dive,
# and the Compare Stocks page (#60) — can call one canonical implementation
# instead of drifting copies.

def safe_float(val):
    """Coerce to float, returning None instead of raising on bad input."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def fmt_val(val, fmt="money"):
    """Format a numeric value for display: money ($B/$M), pct, or ratio (x)."""
    if val is None:
        return "N/A"
    if fmt == "money":
        return f"${val/1e9:.2f}B" if abs(val) >= 1e9 else f"${val/1e6:.1f}M"
    if fmt == "pct":
        return f"{val:.1%}"
    if fmt == "ratio":
        return f"{val:.1f}x"
    return str(val)


@st.cache_data(ttl=900)
def fetch_price_and_market_cap(ticker):
    """
    Fetch current price and market cap from yfinance.
    EDGAR provides shares outstanding; we use yfinance for live price only.
    Returns dict with price, market_cap, shares, dividend_yield.
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        price      = info.get("currentPrice") or info.get("regularMarketPrice")
        market_cap = info.get("marketCap")
        shares     = info.get("sharesOutstanding")
        div_yield  = info.get("dividendYield")
        name       = info.get("longName") or info.get("shortName") or ticker
        sector     = info.get("sector", "N/A")
        description = (info.get("longBusinessSummary", "")[:400] + "...") if info.get("longBusinessSummary") else ""
        return {
            "price":         safe_float(price),
            "market_cap":    safe_float(market_cap),
            "shares":        safe_float(shares),
            "dividend_yield": safe_float(div_yield),
            "name":          name,
            "sector":        sector,
            "description":   description,
        }
    except Exception as e:
        return {"price": None, "market_cap": None, "shares": None,
                "dividend_yield": None, "name": ticker, "sector": "N/A",
                "description": "", "error": str(e)}


@st.cache_data(ttl=3600)
def fetch_fundamentals_edgar(ticker):
    """
    Primary data fetch using SEC EDGAR Company Facts API.
    Falls back gracefully when concepts are missing.
    Returns a data dict compatible with score_stock() in Equity Scout EDGAR
    and Market Screener EDGAR, plus raw "_history"/"_latest" for trend charts
    (Compare Stocks page, #60).
    """
    # 1. Fetch EDGAR company facts (fundamentals + history)
    facts = fetch_company_facts(ticker)
    if facts.get("error"):
        return {"error": facts["error"]}

    latest = facts["latest"]
    meta   = facts["meta"]
    missing = facts.get("missing", [])

    # 2. Fetch live price + market cap from yfinance
    price_data = fetch_price_and_market_cap(ticker)
    price      = price_data.get("price")
    market_cap = price_data.get("market_cap")
    shares     = price_data.get("shares") or latest.get("diluted_shares")
    div_yield  = price_data.get("dividend_yield")

    # Use yfinance name/sector/description as primary (richer than EDGAR entity name)
    name        = price_data.get("name") or meta.get("company_name", ticker)
    sector      = price_data.get("sector") or meta.get("sic", "N/A")
    description = price_data.get("description", "")

    # 3. Pull pre-computed scoring fields from EDGAR latest
    op_cf        = latest.get("op_cf")
    inv_cf       = latest.get("inv_cf")
    fcf          = latest.get("fcf")
    net_income   = latest.get("net_income")
    revenues     = latest.get("revenue")
    gross_profit = latest.get("gross_profit")
    gross_margin = latest.get("gross_margin")
    roic         = latest.get("roic")
    long_term_debt = latest.get("long_term_debt", 0) or 0
    short_term_debt = latest.get("short_term_debt", 0) or 0
    total_debt   = long_term_debt + short_term_debt
    debt_to_fcf  = latest.get("debt_to_fcf")
    int_coverage = latest.get("int_coverage")
    owner_earn   = latest.get("owner_earnings")
    dna          = latest.get("dna")

    # 4. Valuation metrics (need price)
    fcf_yield   = (fcf / market_cap) if (fcf and market_cap and market_cap > 0) else None
    poe         = None
    if owner_earn and owner_earn > 0 and shares and price:
        poe = price / (owner_earn / shares)

    # 5. FCF growth (compare latest vs prior year from history)
    fcf_growth = None
    history = facts.get("history", {})
    op_cf_hist = history.get("op_cf", [])
    inv_cf_hist = history.get("inv_cf", [])
    if len(op_cf_hist) >= 2 and len(inv_cf_hist) >= 2:
        try:
            fcf_prior = op_cf_hist[-2]["value"] + inv_cf_hist[-2]["value"]
            if fcf_prior and fcf_prior != 0 and fcf:
                fcf_growth = (fcf / fcf_prior) - 1
        except Exception:
            pass

    # 6. Interest coverage — prefer cash-basis interest paid
    is_net_creditor = False
    int_exp = latest.get("interest_paid") or latest.get("interest_expense")
    op_inc  = latest.get("op_income")
    if int_exp and int_exp > 0 and op_inc is not None:
        int_coverage = op_inc / int_exp
    elif int_exp is None or int_exp == 0:
        # No interest expense — likely net creditor
        cash     = latest.get("cash", 0) or 0
        if cash > total_debt:
            is_net_creditor = True

    return {
        # Identity
        "name":             name,
        "sector":           sector,
        "description":      description,
        "ticker":           ticker.upper(),
        "cik":              meta.get("cik"),
        "is_financial":     meta.get("is_financial", False),
        "is_cyclical":      meta.get("is_cyclical", False),
        "fiscal_year":      meta.get("last_annual_period"),
        "sic":              meta.get("sic"),
        "data_source":      "SEC EDGAR Company Facts",
        "missing_concepts": missing,

        # Pricing (yfinance)
        "price":            price,
        "market_cap":       market_cap,
        "shares":           shares,

        # Cash flow
        "op_cf":            op_cf,
        "inv_cf":           inv_cf,
        "fcf":              fcf,
        "fcf_yield":        fcf_yield,
        "fcf_growth":       fcf_growth,

        # Income
        "revenues":         revenues,
        "gross_profit":     gross_profit,
        "gross_margin":     gross_margin,
        "net_income":       net_income,

        # Quality metrics
        "roic":             roic,
        "long_term_debt":   long_term_debt,
        "short_term_debt":  short_term_debt,
        "total_debt":       total_debt,
        "debt_to_fcf":      debt_to_fcf,
        "interest_coverage": int_coverage,
        "is_net_creditor":  is_net_creditor,

        # Owner earnings
        "owner_earnings":   owner_earn,
        "price_owner_earn": poe,
        "dna":              dna,

        # Income
        "dividend_yield":   div_yield,

        # Raw EDGAR history (for ROIC trending, Compare Stocks trend charts)
        "_history":         history,
        "_latest":          latest,
    }


# ── Shared helpers: parallel filing fetch + ticker mention detection ────────
# Moved here from app_pages/8_Market_Screener_EDGAR.py so the Compare Stocks page
# (#60/#61) can reuse the same filings-fetch mechanism for its own Claude
# agent instead of duplicating it.

def fetch_filings_parallel(tickers: list, max_workers: int = 3) -> dict:
    """Fetch 10-K filing sections for multiple tickers concurrently.
    Returns {ticker: filing_result} — see fetch_10k_sections() for the
    per-ticker return shape ({"sections", "filing_url", "doc_url",
    "filing_date", "error"})."""
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(fetch_10k_sections, t): t for t in tickers}
        for future in concurrent.futures.as_completed(future_map):
            ticker = future_map[future]
            try:
                results[ticker] = future.result()
            except Exception as e:
                results[ticker] = {"sections": {}, "error": str(e)}
    return results


def extract_tickers_from_text(text: str, valid_tickers: list) -> list:
    """Find uppercase 1-5 letter words in text that match valid tickers.
    Used so a Claude chat can detect when a user mentions a specific ticker
    by name and fetch its filing on demand."""
    words   = re.findall(r'\b[A-Z]{1,5}\b', text)
    matches = [w for w in words if w in valid_tickers]
    return list(dict.fromkeys(matches))  # deduplicate preserving order


# ── DCF intrinsic value ──────────────────────────────────────────────────────
# Simple two-stage discounted cash flow: explicit projection of Free Cash Flow
# for N years using a growth rate derived from the company's own historical
# FCF trend (from fetch_fundamentals_edgar()'s "_history"), then a Gordon
# Growth terminal value, both discounted to present and divided by diluted
# shares outstanding. Shared so both Equity Scout EDGAR and Compare Stocks
# EDGAR can show "DCF Intrinsic Value" directly under the live price.
#
# Simplifying assumption: FCF here (op_cf + inv_cf) already reflects
# post-interest cash flow under GAAP's indirect method, so it's treated as
# cash flow to equity — no separate net-debt adjustment is applied. This is
# a standard simplification for a per-share DCF, not a rigorous FCFF/FCFE
# build, and is disclosed in the UI caption wherever this is shown.

DCF_DEFAULTS = {
    "discount_rate":    0.09,   # ~9% hurdle rate, typical concentrated-value threshold
    "terminal_growth":  0.025,  # roughly long-run GDP/inflation — never above discount_rate
    "projection_years": 10,
    "growth_cap":       0.15,   # cap extrapolated growth — avoid absurd hyper-growth projections
    "growth_floor":     -0.05,  # floor — avoid a single bad year cratering the whole model
    "default_growth":   0.04,   # fallback when too little FCF history to estimate a trend
}


def _estimate_fcf_growth_rate(fcf_history: list, cap: float, floor: float, default: float) -> float:
    """Average year-over-year FCF growth from historical annual observations,
    clipped to [floor, cap]. Falls back to `default` if there's insufficient
    or unusable history (e.g., negative FCF in an early year breaks a clean
    growth-rate calculation)."""
    vals = [h["value"] for h in fcf_history if h.get("value") is not None]
    if len(vals) < 3:
        return default
    # Use at most the last 6 years of data — recent trend, not ancient history
    vals = vals[-7:]
    yoy = []
    for i in range(1, len(vals)):
        prev, cur = vals[i - 1], vals[i]
        if prev and prev > 0:
            yoy.append((cur / prev) - 1)
    if not yoy:
        return default
    g = sum(yoy) / len(yoy)
    return max(floor, min(cap, g))


def compute_dcf_value(data: dict, assumptions: dict = None) -> dict:
    """
    Two-stage DCF intrinsic value per share.

    `data` is a fetch_fundamentals_edgar()-shaped dict (needs "fcf", "shares",
    "price", and "_history").

    Returns:
    {
        "intrinsic_value_per_share": float | None,
        "margin_of_safety":          float | None,  # (intrinsic - price) / intrinsic
        "base_fcf":                  float | None,
        "growth_rate":               float,
        "discount_rate":             float,
        "terminal_growth":           float,
        "projection_years":          int,
        "error":                     str | None,
    }
    """
    a = {**DCF_DEFAULTS, **(assumptions or {})}
    base_fcf = data.get("fcf")
    shares   = data.get("shares")
    price    = data.get("price")

    if base_fcf is None or base_fcf <= 0:
        return {"intrinsic_value_per_share": None, "margin_of_safety": None,
                "base_fcf": base_fcf, "growth_rate": None,
                "discount_rate": a["discount_rate"], "terminal_growth": a["terminal_growth"],
                "projection_years": a["projection_years"],
                "error": "FCF is negative or unavailable — DCF not meaningful for this company."}

    if not shares or shares <= 0:
        return {"intrinsic_value_per_share": None, "margin_of_safety": None,
                "base_fcf": base_fcf, "growth_rate": None,
                "discount_rate": a["discount_rate"], "terminal_growth": a["terminal_growth"],
                "projection_years": a["projection_years"],
                "error": "Shares outstanding unavailable — cannot compute per-share value."}

    r  = a["discount_rate"]
    tg = a["terminal_growth"]
    n  = a["projection_years"]
    if tg >= r:
        tg = r - 0.01  # guard against a nonsensical negative-denominator terminal value

    fcf_history = data.get("_history", {}).get("fcf", [])
    g = _estimate_fcf_growth_rate(fcf_history, a["growth_cap"], a["growth_floor"], a["default_growth"])

    pv_sum   = 0.0
    fcf_year = base_fcf
    for year in range(1, n + 1):
        fcf_year = fcf_year * (1 + g)
        pv_sum  += fcf_year / ((1 + r) ** year)

    terminal_value    = fcf_year * (1 + tg) / (r - tg)
    pv_terminal_value = terminal_value / ((1 + r) ** n)

    total_intrinsic_value = pv_sum + pv_terminal_value
    intrinsic_per_share   = total_intrinsic_value / shares

    margin_of_safety = None
    if price and price > 0:
        margin_of_safety = (intrinsic_per_share - price) / intrinsic_per_share

    return {
        "intrinsic_value_per_share": intrinsic_per_share,
        "margin_of_safety":          margin_of_safety,
        "base_fcf":                  base_fcf,
        "growth_rate":               g,
        "discount_rate":             r,
        "terminal_growth":           tg,
        "projection_years":          n,
        "error":                     None,
    }


# ── Canonical 5-criteria scoring engine ──────────────────────────────────────
# Single source of truth for the Voskuil Owner's Framework score, used by
# Equity Scout EDGAR, Market Screener EDGAR, Compare Stocks EDGAR, and
# Dashboard's holdings scoring. Price/Owner Earnings is intentionally
# excluded from scoring (shown as a reference metric only on pages that
# display it) — this was a deliberate punch-list decision, not an oversight.

DEFAULT_WEIGHTS = {
    "FCF Yield":              30,
    "ROIC":                   20,
    "Debt / FCF":             25,
    "Gross Margin":           15,
    "Interest Coverage":      10,
}

THRESHOLDS = {
    "fcf_yield_good":           0.04,
    "fcf_yield_great":          0.06,
    "roic_good":                0.12,
    "roic_great":               0.20,
    "debt_fcf_safe":            3.0,
    "debt_fcf_warning":         5.0,
    "interest_coverage_safe":   5.0,
    "gross_margin_good":        0.40,
    "gross_margin_great":       0.60,
    "poe_bargain":              15.0,
    "poe_fair":                 25.0,
    "poe_stretched":            35.0,
}


# ═════════════════════════════════════════════════════════════════════
# Buffett/Munger Screening Funnel (#63, #31-#37)
#
# This is a PASS/FAIL CHECKLIST, not a weighted composite score. It
# answers "does this business clear the bar," as a distinct question
# from ranking or valuation. Built here (shared) rather than in the
# Market Screener page file so Dashboard/Equity Scout can adopt the
# same 10-year cash-basis logic later (#34) without duplicating it —
# even though the Market Screener funnel is the first consumer.
#
# Deliberately excluded from the gate itself, per punch list notes:
#   - Gross Margin (#31) — too context-dependent to be a universal
#     moat proxy (Groupon's 90% GM vs. Costco's 13% GM both mislead).
#   - FCF Yield / Price-Owner-Earnings (#33) — valuation metrics;
#     belong in a secondary screen once price is known (Stage 2),
#     not in a quality-only gate.
#   - Interest Coverage as a standalone hard gate (#35) — the CADS-
#     based debt multiple below already captures solvency; interest
#     margin (interest_paid / CADS) is still computed and shown as a
#     reference column.
# ═════════════════════════════════════════════════════════════════════

FUNNEL_THRESHOLDS = {
    "lookback_years":          10,    # look back at most this many annual periods
    "min_history_years":       5,     # fewer annual observations than this = insufficient data, gate fails
    "roic_avg_min":            0.15,  # 10-yr avg ROIC > 15%          (#34)
    "fcf_margin_avg_min":      0.10,  # 10-yr avg FCF margin > 10%    (#63)
    "debt_to_ni_max":          3.0,   # simple gate: total debt / net income          (#63)
    "debt_to_cads_max":        3.0,   # refined gate: total debt / cash avail. for debt service (#32)
    "dilution_lookback_years": 5,     # shares (t) vs. shares (t - this many years)   (#63)
    "roic_stale_years_max":    2,     # flag if the most recent RELIABLE ROIC year is
                                       # this many years or more behind the company's
                                       # actual latest filing (see _roic_denominator_reliable)
}


def _historical_average(history_list: list, lookback_years: int = 10, min_years: int = 5) -> dict:
    """
    Averages the most recent `lookback_years` annual observations from a
    field's history list (already sorted oldest -> newest by
    fetch_company_facts()). Returns years actually used so callers can
    flag limited-history companies with an honest count, rather than
    either silently excluding young companies or silently blending a
    4-year average in next to a true 10-year track record.

    Returns: {"avg": float | None, "years_used": int, "sufficient": bool}
    """
    values = [h["value"] for h in (history_list or []) if h.get("value") is not None]
    recent = values[-lookback_years:] if lookback_years else values
    years_used = len(recent)
    if years_used == 0:
        return {"avg": None, "years_used": 0, "sufficient": False}
    return {
        "avg":        sum(recent) / years_used,
        "years_used": years_used,
        "sufficient": years_used >= min_years,
    }


def _dilution_check(shares_history: list, lookback_years: int = 5) -> dict:
    """
    Compares the latest annual shares-outstanding observation against
    the observation closest to `lookback_years` prior. Passes if shares
    did not grow (buybacks or a flat share count) — a simple Buffett-
    style signal that management isn't funding itself through dilution.

    Returns: {"passed": bool | None, "shares_start", "shares_end",
              "pct_change", "years_compared"}
    "passed" is None (not False) when there isn't enough share-count
    history to make the comparison at all, so callers can distinguish
    "failed the check" from "couldn't run the check."
    """
    obs = [h for h in (shares_history or []) if h.get("value") is not None]
    if len(obs) < 2:
        return {"passed": None, "shares_start": None, "shares_end": None,
                "pct_change": None, "years_compared": 0}

    end_obs    = obs[-1]
    target_idx = max(0, len(obs) - 1 - lookback_years)
    start_obs  = obs[target_idx]

    shares_start = start_obs["value"]
    shares_end   = end_obs["value"]
    try:
        years_compared = int(end_obs["period"]) - int(start_obs["period"])
    except (TypeError, ValueError):
        years_compared = None

    if not shares_start:
        return {"passed": None, "shares_start": shares_start, "shares_end": shares_end,
                "pct_change": None, "years_compared": years_compared}

    pct_change = (shares_end - shares_start) / shares_start
    return {
        "passed":         shares_end <= shares_start,
        "shares_start":   shares_start,
        "shares_end":     shares_end,
        "pct_change":     pct_change,
        "years_compared": years_compared,
    }


def evaluate_buffett_funnel(facts: dict, thresholds: dict = None) -> dict:
    """
    Runs the #63 pass/fail checklist against a fetch_company_facts()-style
    result (dict with "latest"/"history"/"meta" keys). This is the shared
    gate for the Market Screener funnel's Stage 1.

    Both debt hurdles (simple Debt/NI and refined Debt/CADS) are checked
    in parallel — a company passes the debt leg if EITHER clears, and
    "debt_hurdle_cleared" reports which ("simple", "refined", "both", or
    "none") so a clean pass reads differently from a narrow one.

    Returns a full breakdown dict (not just a bool) so the UI can show
    which hurdle was cleared and how much history backs each average.
    """
    t       = thresholds or FUNNEL_THRESHOLDS
    history = facts.get("history", {})
    latest  = facts.get("latest", {})

    roic_r = _historical_average(history.get("roic", []),       t["lookback_years"], t["min_history_years"])
    fcfm_r = _historical_average(history.get("fcf_margin", []), t["lookback_years"], t["min_history_years"])
    dil_r  = _dilution_check(history.get("diluted_shares", []), t["dilution_lookback_years"])

    roic_pass = bool(roic_r["sufficient"] and roic_r["avg"] is not None and roic_r["avg"] > t["roic_avg_min"])
    fcfm_pass = bool(fcfm_r["sufficient"] and fcfm_r["avg"] is not None and fcfm_r["avg"] > t["fcf_margin_avg_min"])

    debt_to_ni   = latest.get("debt_to_ni")
    debt_to_cads = latest.get("debt_to_cads")
    simple_pass  = debt_to_ni   is not None and debt_to_ni   < t["debt_to_ni_max"]
    refined_pass = debt_to_cads is not None and debt_to_cads < t["debt_to_cads_max"]

    if simple_pass and refined_pass:
        cleared = "both"
    elif simple_pass:
        cleared = "simple"
    elif refined_pass:
        cleared = "refined"
    else:
        cleared = "none"

    debt_pass     = simple_pass or refined_pass
    dilution_pass = dil_r["passed"] is True  # None (insufficient data) does not pass

    years_used_candidates = [v for v in (roic_r["years_used"], fcfm_r["years_used"]) if v]
    years_used            = min(years_used_candidates) if years_used_candidates else 0
    limited_history        = 0 < years_used < t["lookback_years"]

    # ROIC staleness — distinct from limited_history. limited_history
    # means "fewer than 10 years total"; staleness means "the RELIABLE
    # years available skip over recent history entirely." This can
    # happen even with a full 10 years counted, if the reliability guard
    # (see _roic_denominator_reliable) excludes several of the most
    # recent years — e.g. a company whose invested capital only recently
    # went negative/unreliable would show a strong-looking multi-year
    # average that's actually silent on anything since. Confirmed real
    # case: without this check, a company could show "10 years, 64% avg"
    # while its most reliable year is actually several years old.
    roic_hist        = history.get("roic", [])
    roic_last_period = roic_hist[-1]["period"] if roic_hist else None
    company_last_period = facts.get("meta", {}).get("last_annual_period")
    roic_stale_years = None
    if roic_last_period and company_last_period:
        try:
            roic_stale_years = int(company_last_period) - int(roic_last_period)
        except (TypeError, ValueError):
            roic_stale_years = None
    roic_stale = roic_stale_years is not None and roic_stale_years >= t.get("roic_stale_years_max", 2)

    overall_passed = bool(roic_pass and fcfm_pass and debt_pass and dilution_pass)

    return {
        "overall_passed":       overall_passed,
        "roic_avg":             roic_r,
        "roic_pass":            roic_pass,
        "roic_stale":           roic_stale,
        "roic_stale_years":     roic_stale_years,
        "roic_last_reliable_period": roic_last_period,
        "fcf_margin_avg":       fcfm_r,
        "fcf_margin_pass":      fcfm_pass,
        "debt_to_ni":           debt_to_ni,
        "debt_to_cads":         debt_to_cads,
        "debt_pass":            debt_pass,
        "debt_hurdle_cleared":  cleared,   # "simple" | "refined" | "both" | "none"
        "dilution":             dil_r,
        "dilution_pass":        dilution_pass,
        "limited_history":      limited_history,
        "years_used":           years_used,
        "is_financial":         facts.get("meta", {}).get("is_financial", False),
        "is_cyclical":          facts.get("meta", {}).get("is_cyclical", False),
        "is_negative_equity":   facts.get("latest", {}).get("is_negative_equity", False),
    }


# ═════════════════════════════════════════════════════════════════════
# Bank / Insurer Alternative Scoring (#36)
#
# Standard ROIC/FCF-margin/Debt-FCF/Gross-Margin metrics above describe a
# business that turns capital into products. A bank or insurer turns
# capital into MORE capital via leverage/underwriting — the same metrics
# either don't exist for them (no "gross margin" on a loan book) or
# actively penalize the thing that makes them a bank/insurer in the first
# place (leverage). This is a parallel funnel + scorer, not a patch on
# the existing ones, scoped to the two subtypes Buffett/Berkshire's own
# playbook actually covers — banks and insurers. Other financial SIC
# codes (brokers, REITs, real estate, investment offices) are still
# flagged is_financial=True and still respect the existing skip/exclude
# toggle; they don't get an alt framework yet (see classify_financial_
# subtype() in edgar_concept_map.py for why).
# ═════════════════════════════════════════════════════════════════════

FINANCIAL_WEIGHTS_BANK = {
    "ROE":                  35,
    "Efficiency Ratio":     25,
    "Net Interest Margin":  20,
    "Equity / Assets":      15,
    "Provision / NI":        5,
}

FINANCIAL_WEIGHTS_INSURANCE = {
    "ROE":              30,
    "Combined Ratio":   40,
    "Equity / Assets":  30,
}

FINANCIAL_THRESHOLDS = {
    "roe_good":              0.10,
    "roe_great":             0.15,
    "nim_good":              0.020,   # NIM is a PROXY (NII / total assets, not average earning assets) — see nim_proxy in edgar_concept_map.py
    "nim_great":             0.030,
    "efficiency_good":       0.65,    # lower is better
    "efficiency_great":      0.55,
    # Equity/Assets — BANK thresholds. A well-capitalized bank runs roughly
    # 8-12% equity/assets; these are NOT reused for insurers (see below) —
    # confirmed via a real scan (#36 validation pass, July 2026): with a
    # single shared 8%/10% threshold, 20 of 33 real insurer survivors
    # (61%) scored a perfect 100/100 because EVERY insurer in the sample
    # cleared "great" trivially — insurers structurally run 3-4x a bank's
    # equity/assets ratio (they don't take deposits), so a bank-calibrated
    # floor provides zero differentiation among them.
    "equity_assets_good":    0.08,
    "equity_assets_great":   0.10,
    # Equity/Assets — INSURANCE thresholds, calibrated against the real
    # July 2026 scan's classic P&C/life survivor spread (~7%-44%, median
    # ~22%): CNO Financial's 6.8% (the weakest survivor, scored 36/100
    # overall) sits below "good" here, while WTM/CINF/ELV (32-44%) clear
    # "great" — actual differentiation instead of an automatic max.
    # Monoline mortgage/credit/financial-guaranty insurers (MGIC, Radian,
    # Essent, NMI, Enact, SIC 6351) used to land in this same "insurance"
    # bucket and would have cleared "great" trivially regardless of these
    # thresholds — their 58-78% equity/assets and single-digit combined
    # ratios reflect a structurally different balance sheet, not superior
    # quality. Fixed at the classification level instead of here: SIC 6351
    # was removed from INSURANCE_SIC_CODES (edgar_concept_map.py), so they
    # now classify as "other_financial" and are excluded by the Market
    # Screener's skip toggle like brokers/REITs, until #70 gives them
    # their own subtype and metric set.
    "equity_assets_good_insurance":  0.15,
    "equity_assets_great_insurance": 0.25,
    "provision_ni_safe":     0.10,    # lower is better
    "provision_ni_warning":  0.25,
    # Combined Ratio — tightened from an initial 95%/100% (#36 validation
    # pass, July 2026): the real survivor spread for classic P&C/life
    # insurers ran 41%-90%, clustered mostly 55%-70% in the current
    # (benign) underwriting cycle — a 95% "great" bar cleared nearly
    # everyone. 85%/95% still uses conventional industry benchmarks
    # (under 100% = underwriting profit, under 90% = good, under 85% =
    # excellent) but actually separates the weaker survivors (CNO 90%,
    # Elevance 87%) from the stronger ones instead of everyone maxing out.
    "combined_ratio_great":  0.85,    # lower is better; under 1.00 = underwriting profit
    "combined_ratio_good":   0.95,
}

FINANCIAL_FUNNEL_THRESHOLDS = {
    "lookback_years":          10,
    "min_history_years":       5,
    "roe_avg_min":             0.10,
    "equity_assets_min":            0.06,   # bank capital adequacy floor
    # Insurance capital floor — separate from the bank floor for the same
    # reason as the score thresholds above (insurers structurally run much
    # higher equity/assets than banks). 12% is set so a real weak survivor
    # like CNO Financial (6.8%) actually fails this gate instead of passing
    # it trivially under the old shared 6% floor.
    "equity_assets_min_insurance":  0.12,
    "efficiency_ratio_max":    0.70,   # banks — latest-period, lower is better
    "combined_ratio_max":      1.00,   # insurers — 10-yr avg, lower is better
    "dilution_lookback_years": 5,
}


def score_financial_firm_breakdown(data: dict, subtype: str, weights: dict = None):
    """
    Alternative scoring path for bank/insurer tickers (#36) — parallels
    score_stock_breakdown() but swaps in metrics that actually describe a
    leveraged-balance-sheet business: ROE instead of ROIC, Efficiency
    Ratio/Net Interest Margin instead of Gross Margin, Equity/Assets
    instead of Debt/FCF, Combined Ratio for insurers instead of FCF Yield.
    Same rebalance-to-100-across-available-criteria approach as the
    standard scorer for the same reason: a missing metric shouldn't zero
    out, the remaining criteria should carry the full 100 points.

    `data` is a `latest`-shaped dict (same shape fetch_company_facts()
    returns under "latest") — pass facts["latest"] directly.
    `subtype` is "bank" or "insurance" (see classify_financial_subtype()).
    Returns (rebalanced_score, criteria) — same shape as
    score_stock_breakdown(), so UI code can reuse the same rendering.
    Returns (None, []) for any other subtype — this function doesn't
    attempt to score brokers/REITs/real estate/investment offices.
    """
    if subtype not in ("bank", "insurance"):
        return None, []

    w = weights or (FINANCIAL_WEIGHTS_BANK if subtype == "bank" else FINANCIAL_WEIGHTS_INSURANCE)
    t = FINANCIAL_THRESHOLDS
    criteria = []

    # ROE — both subtypes. Standard profitability yardstick for a
    # leveraged balance-sheet business, where ROIC (which treats leverage
    # as a cost) doesn't apply the same way.
    max_pts = w["ROE"]
    roe = data.get("roe")
    if roe is not None:
        if roe >= t["roe_great"]:   pts = max_pts
        elif roe >= t["roe_good"]:  pts = round(max_pts * 0.60)
        elif roe > 0:               pts = round(max_pts * 0.20)
        else:                       pts = 0
    else:
        pts = 0
    criteria.append({"name": "ROE", "points_earned": pts, "points_max": max_pts, "missing": roe is None})

    # Equity / Assets — both subtypes, but NOT the same thresholds.
    # Substitutes for Debt/FCF (a debt-multiple gate is meaningless for a
    # bank — deposits/borrowings ARE the raw material of the business).
    # Insurers structurally run 3-4x a bank's equity/assets ratio (no
    # deposit-funded leverage), so a shared bank/insurer threshold set
    # let almost every real insurer max this criterion trivially — see
    # the equity_assets_*_insurance comments in FINANCIAL_THRESHOLDS.
    max_pts = w["Equity / Assets"]
    eqa = data.get("equity_to_assets")
    eqa_great = t["equity_assets_great_insurance"] if subtype == "insurance" else t["equity_assets_great"]
    eqa_good  = t["equity_assets_good_insurance"]  if subtype == "insurance" else t["equity_assets_good"]
    if eqa is not None:
        if eqa >= eqa_great:  pts = max_pts
        elif eqa >= eqa_good: pts = round(max_pts * 0.60)
        elif eqa > 0:         pts = round(max_pts * 0.25)
        else:                 pts = 0
    else:
        pts = 0
    criteria.append({"name": "Equity / Assets", "points_earned": pts, "points_max": max_pts, "missing": eqa is None})

    if subtype == "bank":
        max_pts = w["Efficiency Ratio"]
        eff = data.get("efficiency_ratio")
        if eff is not None:
            if eff <= t["efficiency_great"]:   pts = max_pts
            elif eff <= t["efficiency_good"]:  pts = round(max_pts * 0.55)
            else:                              pts = round(max_pts * 0.15)
        else:
            pts = 0
        criteria.append({"name": "Efficiency Ratio", "points_earned": pts, "points_max": max_pts, "missing": eff is None})

        max_pts = w["Net Interest Margin"]
        nim = data.get("nim_proxy")
        if nim is not None:
            if nim >= t["nim_great"]:   pts = max_pts
            elif nim >= t["nim_good"]:  pts = round(max_pts * 0.55)
            elif nim > 0:               pts = round(max_pts * 0.20)
            else:                       pts = 0
        else:
            pts = 0
        criteria.append({"name": "Net Interest Margin", "points_earned": pts, "points_max": max_pts, "missing": nim is None})

        max_pts = w["Provision / NI"]
        pni = data.get("provision_to_ni")
        if pni is not None:
            if pni <= t["provision_ni_safe"]:      pts = max_pts
            elif pni <= t["provision_ni_warning"]: pts = round(max_pts * 0.50)
            else:                                  pts = 0
        else:
            pts = 0
        criteria.append({"name": "Provision / NI", "points_earned": pts, "points_max": max_pts, "missing": pni is None})

    else:  # insurance
        max_pts = w["Combined Ratio"]
        cr = data.get("combined_ratio")
        if cr is not None:
            if cr <= t["combined_ratio_great"]:  pts = max_pts
            elif cr <= t["combined_ratio_good"]:  pts = round(max_pts * 0.55)
            else:                                 pts = round(max_pts * 0.15)
        else:
            pts = 0
        criteria.append({"name": "Combined Ratio", "points_earned": pts, "points_max": max_pts, "missing": cr is None})

    raw_score     = sum(c["points_earned"] for c in criteria)
    missing_pts   = sum(c["points_max"] for c in criteria if c.get("missing"))
    available_pts = 100 - missing_pts
    rebalanced    = round(raw_score / available_pts * 100) if available_pts > 0 else raw_score
    return rebalanced, criteria


def evaluate_financial_firm_funnel(facts: dict, subtype: str, thresholds: dict = None) -> dict:
    """
    Pass/fail checklist for bank/insurer tickers (#36) — the alt-framework
    counterpart to evaluate_buffett_funnel(). Same "does this business
    clear the bar" question, different bar, since standard ROIC/FCF-
    margin/debt checks don't mean anything for a leveraged balance-sheet
    business.

    Shared legs (both subtypes): 10-yr avg ROE > threshold, capital
    cushion (latest equity/assets) > threshold, no dilution (shares
    outstanding today <= 5 years ago).

    Subtype-specific quality leg:
    - Bank: latest-period efficiency ratio <= threshold (noninterest
      expense / revenue — lower is better, single most-watched bank cost
      metric).
    - Insurance: 10-yr avg combined ratio <= threshold (losses +
      underwriting expense / premiums — under 100% = underwriting
      profit, over 100% means the insurer needs investment income from
      the float to be profitable overall).

    Returns a full breakdown dict, not just a bool, so the UI can show
    which leg failed — same design intent as evaluate_buffett_funnel().
    """
    t       = thresholds or FINANCIAL_FUNNEL_THRESHOLDS
    history = facts.get("history", {})
    latest  = facts.get("latest", {})

    roe_r    = _historical_average(history.get("roe", []), t["lookback_years"], t["min_history_years"])
    roe_pass = bool(roe_r["sufficient"] and roe_r["avg"] is not None and roe_r["avg"] > t["roe_avg_min"])

    eqa           = latest.get("equity_to_assets")
    equity_floor  = t["equity_assets_min_insurance"] if subtype == "insurance" else t["equity_assets_min"]
    capital_pass  = eqa is not None and eqa >= equity_floor

    dil_r         = _dilution_check(history.get("diluted_shares", []), t["dilution_lookback_years"])
    dilution_pass = dil_r["passed"] is True  # None (insufficient data) does not pass

    if subtype == "bank":
        eff = latest.get("efficiency_ratio")
        quality_pass  = eff is not None and eff <= t["efficiency_ratio_max"]
        quality_leg   = "efficiency_ratio"
        quality_value = eff
        quality_avg   = None
    else:  # insurance
        cr_r          = _historical_average(history.get("combined_ratio", []), t["lookback_years"], t["min_history_years"])
        quality_pass  = bool(cr_r["sufficient"] and cr_r["avg"] is not None and cr_r["avg"] <= t["combined_ratio_max"])
        quality_leg   = "combined_ratio_avg"
        quality_value = cr_r["avg"]
        quality_avg   = cr_r

    overall_passed  = bool(roe_pass and capital_pass and dilution_pass and quality_pass)
    years_used      = roe_r["years_used"]
    limited_history = 0 < years_used < t["lookback_years"]

    return {
        "overall_passed":  overall_passed,
        "subtype":         subtype,
        "roe_avg":         roe_r,
        "roe_pass":        roe_pass,
        "capital_ratio":   eqa,
        "capital_pass":    capital_pass,
        "quality_leg":     quality_leg,     # "efficiency_ratio" | "combined_ratio_avg"
        "quality_value":   quality_value,
        "quality_avg":     quality_avg,     # full _historical_average() dict, insurers only
        "quality_pass":    quality_pass,
        "dilution":        dil_r,
        "dilution_pass":   dilution_pass,
        "limited_history": limited_history,
        "years_used":      years_used,
    }


def score_stock_breakdown(data: dict, weights: dict):
    """
    5-criteria scoring (FCF Yield, ROIC, Debt/FCF, Gross Margin, Interest
    Coverage), rebalanced to 100 across whatever criteria have data. Returns
    (rebalanced_score, criteria) where criteria is a list of
    {name, points_earned, points_max, missing} dicts.
    """
    criteria = []

    max_pts   = weights["FCF Yield"]
    fcf_yield = data.get('fcf_yield')
    if fcf_yield is not None:
        if fcf_yield >= THRESHOLDS['fcf_yield_great']:   pts = max_pts
        elif fcf_yield >= THRESHOLDS['fcf_yield_good']:  pts = round(max_pts * 0.60)
        elif fcf_yield > 0:                              pts = round(max_pts * 0.15)
        else:                                            pts = 0
    else:
        pts = 0
    criteria.append({"name": "FCF Yield", "points_earned": pts, "points_max": max_pts, "missing": fcf_yield is None})

    max_pts = weights["ROIC"]
    roic    = data.get('roic')
    if roic is not None:
        if roic >= THRESHOLDS['roic_great']:   pts = max_pts
        elif roic >= THRESHOLDS['roic_good']:  pts = round(max_pts * 0.60)
        elif roic > 0:                         pts = round(max_pts * 0.20)
        else:                                  pts = 0
    else:
        pts = 0
    criteria.append({"name": "ROIC", "points_earned": pts, "points_max": max_pts, "missing": roic is None})

    max_pts  = weights["Debt / FCF"]
    debt_fcf = data.get('debt_to_fcf')
    ic       = data.get('interest_coverage') or 0
    is_nc    = data.get('is_net_creditor', False)
    if debt_fcf is not None:
        if debt_fcf < THRESHOLDS['debt_fcf_safe']:        pts = max_pts
        elif debt_fcf < THRESHOLDS['debt_fcf_warning']:   pts = round(max_pts * 0.50)
        elif ic >= THRESHOLDS['interest_coverage_safe'] or is_nc: pts = round(max_pts * 0.50)
        else:                                              pts = 0
    else:
        pts = 0
    criteria.append({"name": "Debt/FCF", "points_earned": pts, "points_max": max_pts, "missing": debt_fcf is None})

    max_pts = weights["Gross Margin"]
    gm      = data.get('gross_margin')
    if gm is not None:
        if gm >= THRESHOLDS['gross_margin_great']:  pts = max_pts
        elif gm >= THRESHOLDS['gross_margin_good']: pts = round(max_pts * 0.67)
        else:                                       pts = round(max_pts * 0.20)
    else:
        pts = 0
    criteria.append({"name": "Gross Margin", "points_earned": pts, "points_max": max_pts, "missing": gm is None})

    max_pts = weights["Interest Coverage"]
    ic_val  = data.get('interest_coverage')
    if is_nc:
        pts = max_pts
    elif ic_val is not None:
        if ic_val >= THRESHOLDS['interest_coverage_safe']: pts = max_pts
        elif ic_val >= 2.5:                                pts = round(max_pts * 0.50)
        elif ic_val > 0:                                   pts = round(max_pts * 0.15)
        else:                                              pts = 0
    else:
        pts = 0
    criteria.append({"name": "Interest Coverage", "points_earned": pts, "points_max": max_pts,
                     "missing": (not is_nc and ic_val is None)})

    raw_score     = sum(c['points_earned'] for c in criteria)
    missing_pts   = sum(c['points_max'] for c in criteria if c.get('missing'))
    available_pts = 100 - missing_pts
    rebalanced    = round(raw_score / available_pts * 100) if available_pts > 0 else raw_score
    return rebalanced, criteria


def score_stock(data: dict, weights: dict) -> int:
    """Thin wrapper around score_stock_breakdown() for callers that only
    need the scalar score, not the per-criterion breakdown."""
    rebalanced, _criteria = score_stock_breakdown(data, weights)
    return rebalanced
