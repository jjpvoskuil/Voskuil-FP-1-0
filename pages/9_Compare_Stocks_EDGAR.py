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
from sec_utils import fetch_fundamentals_edgar, fmt_val, fetch_filings_parallel, extract_tickers_from_text
from claude_utils import ask_claude_about_equity, get_user_profile

st.set_page_config(page_title="Compare Stocks — EDGAR", layout="wide")

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
}

MAX_COMPARE = 5


def score_stock_breakdown(data, weights):
    """Identical scoring logic to Market Screener / Equity Scout EDGAR.
    Kept as a local copy rather than a cross-page import — pages/ modules
    execute top-level Streamlit calls (st.set_page_config, etc.) on import,
    which makes importing directly from another page unsafe in a
    multi-page app. See punch list architecture item for a possible future
    consolidation into a shared scoring module."""
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


def score_to_label(score):
    if score >= 80:   return "Strong Buy", "🟢"
    elif score >= 65: return "Watch", "🟡"
    elif score >= 45: return "Caution", "🟠"
    else:             return "Avoid", "🔴"


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
        ("roic",             "ROIC",                  "pct"),
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


def build_compare_context(tickers, fundamentals, scores):
    """Quantitative summary of the comparison set — analogous to
    build_ms_context() on Market Screener, but scoped to just the tickers
    being compared and including the score breakdown, not just the total."""
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
        lines.append(
            f"\n{t} ({d.get('name','')}) — Score: {score}/100\n"
            f"  {crit_str}\n"
            f"  FCF Yield: {fmt_cell(d.get('fcf_yield'), 'pct')} | ROIC: {fmt_cell(d.get('roic'), 'pct')} | "
            f"Debt/FCF: {fmt_cell(d.get('debt_to_fcf'), 'ratio')} | Gross Margin: {fmt_cell(d.get('gross_margin'), 'pct')} | "
            f"Interest Coverage: {fmt_cell(d.get('interest_coverage'), 'ratio')} | "
            f"P/OE: {fmt_cell(d.get('price_owner_earn'), 'ratio')}\n"
            f"  Sector: {d.get('sector','N/A')}"
            f"{' | Financial firm' if d.get('is_financial') else ''}"
            f"{' | Cyclical' if d.get('is_cyclical') else ''}"
        )
    return "\n".join(lines)


def build_compare_deep_dive_context(tickers, fundamentals, scores, filings, question):
    """Combines the quant comparison summary with actual SEC 10-K filing
    excerpts for qualitative analysis — same pattern as the filings-based
    chat that used to live on Market Screener, scoped to the comparison set."""
    lines = [build_compare_context(tickers, fundamentals, scores), "\n\n=== SEC 10-K FILING EXCERPTS ===\n"]
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
        st.switch_page("pages/8_Market_Screener_EDGAR.py")

# ── 1. Summary strip ─────────────────────────────────────────────────────
st.markdown("#### Summary")
summary_cols = st.columns(len(active_tickers))
scores = {}
for i, t in enumerate(active_tickers):
    d = fundamentals[t]
    score, criteria = score_stock_breakdown(d, weights)
    scores[t] = (score, criteria)
    label, emoji = score_to_label(score)
    with summary_cols[i]:
        st.markdown(f"### {t}")
        st.caption(d.get("name", t))
        st.metric("Score", f"{emoji} {score}/100", label)
        _price_str = f"${d['price']:.2f}" if d.get("price") else "N/A"
        st.caption(f"**Price:** {_price_str}")
        st.caption(f"**Mkt Cap:** {fmt_val(d.get('market_cap'))}")
        st.caption(f"**Sector:** {d.get('sector', 'N/A')}")
        if d.get("is_financial"):
            st.caption("🏦 Financial firm")
        if d.get("is_cyclical"):
            st.caption("🔄 Cyclical")

st.divider()

# ── 2. Score breakdown table ─────────────────────────────────────────────
st.markdown("#### Score Breakdown")
st.caption("Points earned / max per criterion, using your currently committed scoring weights.")

criteria_names = [c["name"] for c in scores[active_tickers[0]][1]]
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
    _profile = get_user_profile()
    _wd = _profile.get('monthly_withdrawal', 8000)
    cmp_starters = [
        "Which of these has the most durable moat, and why?",
        "Apply Munger's inversion to each — what could permanently destroy value?",
        f"Which would fit best for our ${_wd:,.0f}/month retirement income target?",
        "Rank these for a 10-year hold and explain the biggest risk for each.",
    ]
    for i, q in enumerate(cmp_starters):
        with (sc1 if i % 2 == 0 else sc2):
            if st.button(q, key=f"cmp_starter_{i}", use_container_width=True):
                st.session_state["cmp_pending_q"] = q
                st.rerun()

cmp_pending_q = st.session_state.pop("cmp_pending_q", None)
cmp_user_q    = st.chat_input("Ask Claude about these companies...", key="cmp_claude_input")
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
                    active_tickers, fundamentals, scores, filings_cache, cmp_active_q
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
