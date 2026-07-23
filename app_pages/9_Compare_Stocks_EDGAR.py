"""
pages/9_Compare_Stocks_EDGAR.py — Side-by-side stock comparison (punch list #60/#61)

Entry point: the "⚖️ Compare Top 3" / "⚖️ Compare Selected" buttons on Market
Screener EDGAR, which set st.session_state['compare_tickers'] (2-5 tickers)
and st.session_state['compare_weights'] before calling st.switch_page() here.

Layout:
  1. Summary strip — name, price, market cap, sector, total score per ticker
  2. Score breakdown — per-criterion points, side by side
  3. Financial statements — Income Statement / Cash Flow / Balance Sheet /
     Derived Metrics, one row per line item, one column per ticker
  4. Historical trend charts — pick any line item, see all compared tickers
     plotted together on one combined chart (st.dialog popup)
  5. Claude agent — qualitative comparison with actual SEC 10-K filing text,
     moved here from Market Screener EDGAR so it's scoped to the specific
     shortlist rather than the whole screen. Filings are fetched lazily
     (only once you ask a question), for just the 2-5 compared tickers.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from sec_utils import fetch_fundamentals_edgar, fmt_val, fetch_filings_parallel, extract_tickers_from_text, compute_dcf_value, DCF_DEFAULTS, get_intrinsic_value, DEFAULT_WEIGHTS, THRESHOLDS, score_stock_breakdown, score_financial_firm_display, investment_verdict
from claude_utils import ask_claude_about_equity, get_user_profile
from watchlist_utils import add_to_watchlist, is_watchlisted

st.set_page_config(page_title="Compare Stocks — EDGAR", layout="wide")

MAX_COMPARE = 5


# ── Line items grouped by financial statement, in the order they'll render ──
# Keys must match CONCEPT_MAP fields in edgar_concept_map.py / sec_utils.py
# so history/latest lookups work directly against fetch_fundamentals_edgar()'s
# "_history" and "_latest" dicts.
STATEMENT_SECTIONS = {
    "📊 Income Statement": [
        ("revenue",          "Revenue",           "money"),
        ("cost_of_revenue",  "Cost of Revenue",   "money"),
        ("gross_profit",     "Gross Profit",      "money"),
        ("op_income",        "Operating Income",  "money"),
        ("interest_expense", "Interest Expense",  "money"),
        ("income_tax",       "Income Tax",        "money"),
        ("net_income",       "Net Income",        "money"),
        ("diluted_shares",   "Diluted Shares",    "shares"),
        ("eps_diluted",      "EPS (Diluted)",     "raw"),
    ],
    "💵 Cash Flow Statement": [
        ("op_cf",         "Operating Cash Flow", "money"),
        ("inv_cf",        "Investing Cash Flow", "money"),
        ("capex",         "CapEx",               "money"),
        ("dna",           "D&A",                 "money"),
        ("interest_paid", "Interest Paid",        "money"),
    ],
    "🏦 Balance Sheet": [
        ("total_assets",        "Total Assets",         "money"),
        ("current_assets",      "Current Assets",       "money"),
        ("current_liabilities", "Current Liabilities",  "money"),
        ("total_liabilities",   "Total Liabilities",    "money"),
        ("total_equity",        "Total Equity",         "money"),
        ("long_term_debt",      "Long-Term Debt",       "money"),
        ("short_term_debt",     "Short-Term Debt",      "money"),
        ("cash",                "Cash",                 "money"),
        ("goodwill",            "Goodwill",             "money"),
        ("intangibles",         "Intangibles",          "money"),
        ("retained_earnings",   "Retained Earnings",    "money"),
        ("inventory",           "Inventory",            "money"),
        ("accounts_receivable", "Accounts Receivable",  "money"),
        ("accounts_payable",    "Accounts Payable",     "money"),
        ("ppe_net",             "PP&E (Net)",           "money"),
    ],
    "🎯 Derived Scoring Metrics": [
        ("fcf",              "Free Cash Flow",       "money"),
        ("fcf_yield",        "FCF Yield",             "pct"),
        ("roic_10yr_avg",    "ROIC (10yr avg, cash basis)", "pct"),  # (#34) what feeds the score
        ("roic",             "ROIC (latest yr, cash basis)", "pct"),
        ("gross_margin",     "Gross Margin",          "pct"),
        ("debt_to_fcf",      "Debt / FCF",            "ratio"),
        ("interest_coverage","Interest Coverage",     "ratio"),
        ("owner_earnings",   "Owner Earnings",        "money"),
        ("price_owner_earn", "Price / Owner Earn.",   "ratio"),
    ],
}


def fmt_cell(val, kind):
    if val is None:
        return "—"
    if kind == "money":  return fmt_val(val, "money")
    if kind == "pct":    return fmt_val(val, "pct")
    if kind == "ratio":  return fmt_val(val, "ratio")
    if kind == "shares": return f"{val/1e6:.1f}M" if val else "—"
    return f"{val:.2f}" if isinstance(val, (int, float)) else str(val)


def build_compare_context(tickers, fundamentals, scores, dcf_results=None):
    """Quantitative summary of the comparison set — analogous to
    build_ms_context() on Market Screener, but scoped to just the tickers
    being compared and including the score breakdown, not just the total."""
    dcf_results = dcf_results or {}
    profile = get_user_profile()
    age     = profile.get('age', 57)
    sage    = profile.get('spouse_age', '')
    wd      = profile.get('monthly_withdrawal', 8000)
    pv      = profile.get('portfolio_val', 3_790_000)
    inf     = profile.get('inflation', 4.0)
    age_str = f"{age}-year-old" + (f" and spouse age {sage}" if sage else "")

    lines = [
        "STOCK COMPARISON — Voskuil Owner's Framework\n",
        "Investment context: Buffett + Munger concentrated value philosophy.",
        f"Investor: {age_str} | Portfolio: ${pv/1e6:.1f}M | Monthly target: ${wd:,.0f} | "
        f"Inflation assumption: {inf:.1f}%. Hold horizon 5-10 years.\n",
        f"Comparing {len(tickers)} companies:\n",
    ]
    for t in tickers:
        d = fundamentals.get(t, {})
        score, criteria = scores.get(t, (None, []))
        crit_str = " | ".join(f"{c['name']}: {c['points_earned']}/{c['points_max']}" for c in criteria)
        dcf = dcf_results.get(t, {})
        # (2026-07-23) dcf_results now holds get_intrinsic_value() output,
        # which is either a DCF result (equities) or a Residual Income
        # result (banks/insurers, stamped methodology="residual_income")
        # -- shapes differ, so branch on it here rather than assuming DCF
        # keys exist for every ticker.
        if dcf.get("methodology") == "residual_income":
            if dcf.get("error"):
                dcf_str = f"Residual Income: unavailable ({dcf['error']})"
            else:
                _ss, _ms = dcf.get("single_stage", {}), dcf.get("multi_stage", {})
                _bits = []
                if _ss.get("intrinsic_value_per_share") is not None:
                    _bits.append(f"single-stage ${_ss['intrinsic_value_per_share']:.2f}/share"
                                 + (f" ({_ss['margin_of_safety']:+.0%} MoS)" if _ss.get("margin_of_safety") is not None else ""))
                if _ms.get("intrinsic_value_per_share") is not None:
                    _bits.append(f"multi-stage ${_ms['intrinsic_value_per_share']:.2f}/share"
                                 + (f" ({_ms['margin_of_safety']:+.0%} MoS)" if _ms.get("margin_of_safety") is not None else ""))
                dcf_str = "Residual Income Intrinsic Value: " + (", ".join(_bits) if _bits else "not computed")
                if dcf.get("divergence") is not None:
                    dcf_str += f" | Single/multi divergence: {dcf['divergence']:.0%}"
        elif dcf.get("error"):
            dcf_str = f"DCF: unavailable ({dcf['error']})"
        elif dcf.get("intrinsic_value_per_share") is not None:
            _mos = dcf.get("margin_of_safety")
            dcf_str = (
                f"DCF Intrinsic Value: ${dcf['intrinsic_value_per_share']:.2f}/share "
                f"(assumes {dcf['growth_rate']:.1%} FCF growth, {dcf['discount_rate']:.1%} discount rate, "
                f"{dcf['terminal_growth']:.1%} terminal growth, {dcf['projection_years']}yr projection)"
                + (f" | Margin of Safety: {_mos:+.0%}" if _mos is not None else "")
            )
        else:
            dcf_str = "DCF: not computed"
        _price_line = f"${d['price']:.2f}" if d.get("price") else "N/A"
        lines.append(
            f"\n{t} ({d.get('name','')}) — Score: {score}/100\n"
            f"  {crit_str}\n"
            f"  FCF Yield: {fmt_cell(d.get('fcf_yield'), 'pct')} | ROIC (10yr avg, cash basis): {fmt_cell(d.get('roic_10yr_avg'), 'pct')} | "
            f"Debt/FCF: {fmt_cell(d.get('debt_to_fcf'), 'ratio')} | Gross Margin: {fmt_cell(d.get('gross_margin'), 'pct')} | "
            f"Interest Coverage: {fmt_cell(d.get('interest_coverage'), 'ratio')} | "
            f"P/OE: {fmt_cell(d.get('price_owner_earn'), 'ratio')}\n"
            f"  Price: {_price_line} | {dcf_str}\n"
            f"  Sector: {d.get('sector','N/A')}"
            f"{' | Financial firm' if d.get('is_financial') else ''}"
            f"{' | Cyclical' if d.get('is_cyclical') else ''}"
        )
    return "\n".join(lines)


def build_compare_deep_dive_context(tickers, fundamentals, scores, filings, question, dcf_results=None):
    """Combines the quant comparison summary with actual SEC 10-K filing
    excerpts for qualitative analysis — same pattern as the filings-based
    chat that used to live on Market Screener, scoped to the comparison set."""
    lines = [build_compare_context(tickers, fundamentals, scores, dcf_results), "\n\n=== SEC 10-K FILING EXCERPTS ===\n"]
    n_companies   = len(filings)
    section_limit = max(2500, 7500 // max(n_companies, 1))
    for t in tickers:
        filing = filings.get(t, {})
        sections = filing.get("sections", {})
        err      = filing.get("error")
        lines.append(f"\n--- {t} ---")
        if err:
            lines.append(f"[Filing unavailable: {err}]")
            continue
        for key, label in [
            ("business",     "BUSINESS"),
            ("risk_factors", "RISK FACTORS"),
            ("mda",          "MD&A"),
        ]:
            text = sections.get(key, "")
            if text:
                lines.append(f"[{label}]: {text[:section_limit]}")
    lines.append(f"\n\nQUESTION: {question}")
    return "\n".join(lines)


@st.dialog("📈 Historical Trend Comparison", width="large")
def show_trend_dialog(field_key, field_label, tickers, fundamentals):
    st.caption(f"**{field_label}** — annual values from SEC EDGAR, all compared tickers on one chart.")
    fig = go.Figure()
    any_data = False
    for t in tickers:
        d = fundamentals.get(t, {})
        hist = d.get("_history", {}).get(field_key, [])
        if not hist:
            continue
        any_data = True
        x = [h.get("period") or h.get("end") for h in hist]
        y = [h.get("value") for h in hist]
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines+markers", name=t))
    if not any_data:
        st.warning(f"No historical data found for {field_label} across the selected tickers.")
    else:
        fig.update_layout(
            height=450,
            margin=dict(l=10, r=10, t=30, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            yaxis_title=field_label,
            hovermode="x unified",
        )
        st.plotly_chart(fig, use_container_width=True)
    if st.button("Close"):
        st.rerun()


# ─────────────────────────────────────────────
# PAGE
# ─────────────────────────────────────────────
st.title("⚖️ Compare Stocks — EDGAR")
st.caption("Side-by-side comparison sourced from SEC EDGAR Company Facts. Select tickers from the Market Screener's checkboxes, then click Compare Selected.")

tickers = st.session_state.get("compare_tickers", [])
weights = st.session_state.get("compare_weights", DEFAULT_WEIGHTS.copy())

if not tickers or len(tickers) < 2:
    st.info(
        "No comparison set yet. Go to **Market Screener**, check the boxes next to 2-5 "
        "tickers you want to compare, then click **⚖️ Compare Selected**."
    )
    st.stop()

if len(tickers) > MAX_COMPARE:
    tickers = tickers[:MAX_COMPARE]

# ── Fetch fundamentals for all tickers ──────────────────────────────────
fundamentals = {}
missing_tickers = []
with st.spinner(f"Loading EDGAR fundamentals for {', '.join(tickers)}..."):
    for t in tickers:
        d = fetch_fundamentals_edgar(t)
        if d.get("error"):
            missing_tickers.append((t, d["error"]))
        else:
            fundamentals[t] = d

if missing_tickers:
    for t, err in missing_tickers:
        st.warning(f"Could not load {t}: {err}")

active_tickers = [t for t in tickers if t in fundamentals]
if len(active_tickers) < 2:
    st.error("Fewer than 2 tickers loaded successfully — nothing to compare.")
    st.stop()

col_change = st.columns([4, 1])
with col_change[1]:
    if st.button("🔄 Change Selection", use_container_width=True):
        st.session_state["compare_tickers"] = []
        st.switch_page("app_pages/8_Market_Screener_EDGAR.py")

# ── 1. Summary strip ─────────────────────────────────────────────────────
st.markdown("#### Summary")

with st.expander("⚙️ DCF Assumptions", expanded=False):
    st.caption(
        "Two-stage discounted cash flow: FCF is projected forward using a growth rate derived "
        "from each company's own historical FCF trend (capped to keep extrapolation sane), then "
        "a Gordon Growth terminal value. Simplification: FCF here already reflects post-interest "
        "cash flow, so it's treated as cash flow to equity directly — no separate net-debt adjustment."
    )
    dc1, dc2, dc3 = st.columns(3)
    with dc1:
        _dr = st.number_input("Discount rate (%)", min_value=4.0, max_value=20.0,
                               value=DCF_DEFAULTS["discount_rate"] * 100, step=0.5) / 100
    with dc2:
        _tg = st.number_input("Terminal growth (%)", min_value=0.0, max_value=5.0,
                               value=DCF_DEFAULTS["terminal_growth"] * 100, step=0.25) / 100
    with dc3:
        _yrs = st.number_input("Projection years", min_value=5, max_value=20,
                                value=DCF_DEFAULTS["projection_years"], step=1)
    dcf_assumptions = {"discount_rate": _dr, "terminal_growth": _tg, "projection_years": _yrs}

summary_cols = st.columns(len(active_tickers))
scores = {}
dcf_results = {}
for i, t in enumerate(active_tickers):
    d = fundamentals[t]
    subtype = d.get("financial_subtype")
    if subtype in ("bank", "insurance"):
        score, criteria = score_financial_firm_display(d, subtype)
    else:
        score, criteria = score_stock_breakdown(d, weights)
    scores[t] = (score, criteria)
    # (verdict overhaul) same quality+value gate as Dashboard/Equity Scout
    # -- see sec_utils.investment_verdict() -- rather than a quality-only
    # score threshold, so a name doesn't look like a "Strong Buy" here
    # while Dashboard shows "Hold" for the same underlying reason.
    _verdict = investment_verdict(d)
    label = {"buy": "Strong Buy", "hold": "Hold", "avoid": "Avoid", "unrated": "—"}[_verdict["tier"]]
    emoji = _verdict["icon"]
    # (2026-07-23) get_intrinsic_value() routes banks/insurers to the
    # Residual Income model instead of FCF-DCF -- see sec_utils.py's
    # dispatcher; same "one formula, everywhere" rule as the rest of the
    # app. dcf_assumptions only apply to the DCF path -- Residual Income
    # uses its own defaults (no per-page assumption inputs here yet).
    dcf = get_intrinsic_value(d, dcf_assumptions)
    dcf_results[t] = dcf
    with summary_cols[i]:
        st.markdown(f"### {t}")
        st.caption(d.get("name", t))
        st.metric("Score", f"{emoji} {score}/100", label)
        _price_str = f"${d['price']:.2f}" if d.get("price") else "N/A"
        st.caption(f"**Price:** {_price_str}")

        if dcf.get("methodology") == "residual_income":
            if dcf["error"]:
                st.caption(f"**Residual Income Value:** — _{dcf['error']}_")
            else:
                _ms = dcf["multi_stage"]
                _ss = dcf["single_stage"]
                if _ms.get("intrinsic_value_per_share") is not None:
                    _ms_mos = _ms.get("margin_of_safety")
                    _mos_color = "green" if (_ms_mos or 0) > 0 else "red"
                    st.caption(f"**Value (multi-stage):** ${_ms['intrinsic_value_per_share']:.2f}")
                    if _ms_mos is not None:
                        st.caption(f":{_mos_color}[{_ms_mos:+.0%} MoS]")
                if _ss.get("margin_of_safety") is not None:
                    st.caption(f"Single-stage MoS: {_ss['margin_of_safety']:+.0%}")
                if dcf.get("divergence") is not None and dcf["divergence"] >= 0.30:
                    st.caption(f"⚠️ {dcf['divergence']:.0%} divergence")
        elif dcf["error"]:
            st.caption(f"**DCF Value:** — _{dcf['error']}_")
        else:
            _mos = dcf["margin_of_safety"]
            _mos_str = f"{_mos:+.0%} MoS" if _mos is not None else ""
            _mos_color = "green" if (_mos or 0) > 0 else "red"
            st.caption(f"**DCF Value:** ${dcf['intrinsic_value_per_share']:.2f}")
            if _mos_str:
                st.caption(f":{_mos_color}[{_mos_str}]")

        st.caption(f"**Mkt Cap:** {fmt_val(d.get('market_cap'))}")
        st.caption(f"**Sector:** {d.get('sector', 'N/A')}")
        if d.get("financial_subtype") in ("bank", "insurance"):
            st.caption(f"🏦 {d['financial_subtype'].title()} — alt scoring (#36/#70)")
        elif d.get("is_financial"):
            st.caption("🏦 Financial firm (reference-only score)")
        if d.get("is_cyclical"):
            st.caption("🔄 Cyclical")
        if d.get("foreign_currency"):
            st.caption(f"💱 Reported in {d['foreign_currency']}, FX-converted (#11)")

        # Add-only Watchlist control (#68) -- removal only happens on the
        # Watchlist page itself, same as the other three source pages.
        _already_watched = is_watchlisted(t)
        _watch_checked = st.checkbox(
            "⭐ Watchlist", value=_already_watched, key=f"cmp_watch_{t}",
            disabled=_already_watched,
            help="On Watchlist" if _already_watched else f"Add {t} to Watchlist",
        )
        if _watch_checked and not _already_watched:
            add_to_watchlist(t, name=d.get("name", t), source="Compare Stocks")
            st.rerun()

st.divider()

# ── 2. Score breakdown table ─────────────────────────────────────────────
st.markdown("#### Score Breakdown")
st.caption("Points earned / max per criterion, using your currently committed scoring weights.")

criteria_names = []
for t in active_tickers:
    for c in scores[t][1]:
        if c["name"] not in criteria_names:
            criteria_names.append(c["name"])
breakdown_rows = []
for cname in criteria_names:
    row = {"Criterion": cname}
    for t in active_tickers:
        _, criteria = scores[t]
        c = next((x for x in criteria if x["name"] == cname), None)
        row[t] = f"{c['points_earned']}/{c['points_max']}" if c else "—"
    breakdown_rows.append(row)
total_row = {"Criterion": "**TOTAL**"}
for t in active_tickers:
    total_row[t] = f"{scores[t][0]}/100"
breakdown_rows.append(total_row)

st.dataframe(pd.DataFrame(breakdown_rows).set_index("Criterion"), use_container_width=True)

st.divider()

# ── 3. Financial statements, grouped by section ──────────────────────────
st.markdown("#### Financial Statements")
st.caption(
    "Raw SEC EDGAR values, most recent annual filing. Tables scroll horizontally if they "
    "don't fit — drag or use a trackpad/mouse wheel with Shift."
)

for section_title, fields in STATEMENT_SECTIONS.items():
    with st.expander(section_title, expanded=(section_title == "📊 Income Statement")):
        rows = []
        for field_key, field_label, kind in fields:
            row = {"Line Item": field_label}
            for t in active_tickers:
                latest = fundamentals[t].get("_latest", {})
                # A few fields live at the top level of the data dict rather
                # than in _latest (derived post-processing fields).
                val = latest.get(field_key, fundamentals[t].get(field_key))
                row[t] = fmt_cell(val, kind)
            rows.append(row)
        st.dataframe(pd.DataFrame(rows).set_index("Line Item"), use_container_width=True)

st.divider()

# ── 4. Click-to-chart historical trends ──────────────────────────────────
st.markdown("#### 📈 Historical Trend — Combined Chart")
st.caption("Pick any financial statement line item to see all compared tickers plotted together, year over year.")

_all_fields = []
NO_TREND_FIELDS = {"fcf_yield", "price_owner_earn"}  # need historical price data — not available from EDGAR alone
for section_title, fields in STATEMENT_SECTIONS.items():
    for field_key, field_label, kind in fields:
        _suffix = " (no trend — needs price history)" if field_key in NO_TREND_FIELDS else ""
        _all_fields.append((f"{section_title} — {field_label}{_suffix}", field_key, field_label))

tc1, tc2 = st.columns([4, 1])
with tc1:
    _choice = st.selectbox(
        "Line item", options=_all_fields, format_func=lambda x: x[0],
        label_visibility="collapsed",
    )
with tc2:
    if st.button("📈 Show Trend", type="primary", use_container_width=True):
        show_trend_dialog(_choice[1], _choice[2], active_tickers, fundamentals)

st.divider()

# ── 5. Claude agent — qualitative comparison with SEC 10-K access ────────
st.markdown("#### 🤖 Ask Claude — Qualitative Comparison")
st.caption(
    "Claude reasons over both the quantitative comparison above and actual SEC 10-K filing "
    "text (fetched on your first question, for just these tickers) — moat durability, "
    "management quality, risk factors, competitive position."
)

# Reset the conversation if the comparison set changed since last chat message —
# otherwise old messages about different tickers would linger in context.
_tickers_sig = tuple(sorted(active_tickers))
if st.session_state.get("cmp_tickers_sig") != _tickers_sig:
    st.session_state["cmp_tickers_sig"]   = _tickers_sig
    st.session_state["cmp_claude_convo"]  = []
    st.session_state["cmp_context_key"]   = False
    st.session_state["cmp_filings"]       = {}

cmp_convo_key   = "cmp_claude_convo"
cmp_context_key = "cmp_context_key"
if cmp_convo_key not in st.session_state:
    st.session_state[cmp_convo_key]   = []
    st.session_state[cmp_context_key] = False

# Display history
for msg in st.session_state[cmp_convo_key]:
    role, content = msg["role"], msg["content"]
    if role == "user":
        if "\n---\nQUESTION: " in content:
            content = content.split("\n---\nQUESTION: ", 1)[-1]
        elif "\n\nQUESTION: " in content:
            content = content.rsplit("\n\nQUESTION: ", 1)[-1]
        with st.chat_message("user"):
            st.markdown(content)
    else:
        with st.chat_message("assistant", avatar="🤖"):
            st.markdown(content)

# Suggested starters (only before first message)
if not st.session_state[cmp_convo_key]:
    st.markdown("**Suggested questions:**")
    sc1, sc2 = st.columns(2)
    cmp_starters = [
        "Which of these has the most durable moat, and why?",
        "Apply Munger's inversion to each — what could permanently destroy value?",
        "Run these through a Munger and Buffett assessment and give me a better sense of what is the best selection and why.",
        "Rank these for a 10-year hold and explain the biggest risk for each.",
    ]
    for i, q in enumerate(cmp_starters):
        with (sc1 if i % 2 == 0 else sc2):
            if st.button(q, key=f"cmp_starter_{i}", use_container_width=True):
                st.session_state["cmp_pending_q"] = q
                st.rerun()

# ── Deferred chat_input mount (cold-load scroll fix, same as
# Dashboard's/Equity Scout's) ────────────────────────────────────────────
# st.chat_input's mere presence makes Streamlit wrap the page in its own
# auto-scroll-to-bottom chat container -- see ui_utils.py's scroll-fix
# docstring for the full story. Deferring the widget itself behind a
# click means nothing creates that container on a fresh load here either.
if "cmp_chat_enabled" not in st.session_state:
    st.session_state["cmp_chat_enabled"] = bool(st.session_state[cmp_convo_key])

cmp_pending_q = st.session_state.pop("cmp_pending_q", None)
if cmp_pending_q:
    st.session_state["cmp_chat_enabled"] = True

if not st.session_state["cmp_chat_enabled"]:
    if st.button("💬 Ask Claude about these companies", key="cmp_enable_chat"):
        st.session_state["cmp_chat_enabled"] = True
        st.rerun()
    cmp_user_q = None
else:
    cmp_user_q = st.chat_input("Ask Claude about these companies...", key="cmp_claude_input")
cmp_active_q  = cmp_pending_q or cmp_user_q

if cmp_active_q:
    filings_cache = st.session_state.get("cmp_filings", {})
    missing_filings = [t for t in active_tickers if t not in filings_cache]
    if missing_filings:
        with st.spinner(f"📄 Fetching 10-K filings for {', '.join(missing_filings)}..."):
            filings_cache.update(fetch_filings_parallel(missing_filings))
            st.session_state["cmp_filings"] = filings_cache

    with st.chat_message("user"):
        st.markdown(cmp_active_q)

    with st.chat_message("assistant", avatar="🤖"):
        with st.spinner("Analyzing..."):
            if not st.session_state[cmp_context_key]:
                context_str = build_compare_deep_dive_context(
                    active_tickers, fundamentals, scores, filings_cache, cmp_active_q, dcf_results
                )
                response = ask_claude_about_equity(
                    ticker="COMPARE", data={}, scores={}, sections={},
                    user_question=context_str,
                    conversation_history=None,
                )
                st.session_state[cmp_convo_key].append({"role": "user", "content": context_str})
                st.session_state[cmp_context_key] = True
            else:
                response = ask_claude_about_equity(
                    ticker="COMPARE", data={}, scores={}, sections={},
                    user_question=cmp_active_q,
                    conversation_history=st.session_state[cmp_convo_key],
                )
                st.session_state[cmp_convo_key].append({"role": "user", "content": cmp_active_q})

            st.session_state[cmp_convo_key].append({"role": "assistant", "content": response})
            st.markdown(response)

if st.session_state.get(cmp_convo_key):
    if st.button("🗑️ Clear conversation", key="cmp_clear_convo"):
        st.session_state[cmp_convo_key]   = []
        st.session_state[cmp_context_key] = False
        st.session_state["cmp_filings"]   = {}
        st.rerun()

