"""
pages/9_Compare_Stocks_EDGAR.py — Side-by-side stock comparison (punch list #60)

Entry point: the "⚖️ Compare Selected" button on Market Screener EDGAR, which
sets st.session_state['compare_tickers'] (2-5 tickers) and
st.session_state['compare_weights'] before calling st.switch_page() here.

Layout:
  1. Summary strip — name, price, market cap, sector, total score per ticker
  2. Score breakdown — per-criterion points, side by side
  3. Financial statements — Income Statement / Cash Flow / Balance Sheet /
     Derived Metrics, one row per line item, one column per ticker
  4. Historical trend charts — pick any line item, see all compared tickers
     plotted together on one combined chart (st.dialog popup)

A right-hand notes area is intentionally left light on this first pass —
punch list #61 (Claude agent on this page) will use that space next.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from sec_utils import fetch_fundamentals_edgar, fmt_val

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
for section_title, fields in STATEMENT_SECTIONS.items():
    for field_key, field_label, kind in fields:
        _all_fields.append((f"{section_title} — {field_label}", field_key, field_label))

tc1, tc2 = st.columns([4, 1])
with tc1:
    _choice = st.selectbox(
        "Line item", options=_all_fields, format_func=lambda x: x[0],
        label_visibility="collapsed",
    )
with tc2:
    if st.button("📈 Show Trend", type="primary", use_container_width=True):
        show_trend_dialog(_choice[1], _choice[2], active_tickers, fundamentals)
