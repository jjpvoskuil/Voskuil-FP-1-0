import streamlit as st
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import pandas as pd

st.set_page_config(page_title="Financial Modeler | Voskuil FP", layout="wide")

st.title("🏔️ Financial Modeler")
st.caption("Purpose-built for a two-person household, concentrated value portfolio, Long Squeeze macro overlay.")
st.markdown("> *\"The goal is not to maximize returns. The goal is to never be a forced seller.\"*")
st.divider()

# ─────────────────────────────────────────────
# SECTION 1: YOUR NUMBERS
# ─────────────────────────────────────────────
st.header("📋 Your Numbers")
st.caption("Pre-filled with your current profile. Adjust any value and the model updates instantly.")

tab_portfolio, tab_household, tab_assumptions, tab_events = st.tabs([
    "💼 Portfolio & Income", "👫 Household", "📊 Assumptions", "🎯 Goals & Events"
])

# ── Tab 1: Portfolio & Income ──────────────────────────────────────────────
with tab_portfolio:
    pc1, pc2 = st.columns(2)
    with pc1:
        st.subheader("Portfolio")
        default_portfolio = st.session_state.get("total_val", 3_790_000.0)
        portfolio_val = st.number_input(
            "Current Portfolio Value ($)",
            min_value=0.0, max_value=50_000_000.0,
            value=float(default_portfolio), step=10_000.0, format="%.0f",
        )
        annual_passive = st.number_input(
            "Annual Passive Income (dividends + interest) ($)",
            min_value=0.0, max_value=500_000.0,
            value=float(st.session_state.get("total_income", 96_000.0)),
            step=1_000.0, format="%.0f",
            help="Pulls from dashboard CSV if loaded."
        )
        cash_buffer = st.number_input(
            "Cash / Short-term Buffer ($)",
            min_value=0.0, max_value=1_000_000.0,
            value=96_000.0, step=5_000.0, format="%.0f",
            help="Cash outside investments — your 'never a forced seller' cushion."
        )
    with pc2:
        st.subheader("Withdrawals & Other Income")
        monthly_withdrawal = st.number_input(
            "Monthly Withdrawal Target — Household ($)",
            min_value=0.0, max_value=50_000.0,
            value=8_000.0, step=100.0, format="%.0f",
            help="Combined household monthly draw while both are alive."
        )
        other_income = st.number_input(
            "Other Annual Income (rental, part-time, etc.) ($)",
            min_value=0.0, max_value=200_000.0,
            value=0.0, step=1_000.0, format="%.0f",
        )
        st.subheader("Your Social Security")
        ss_monthly = st.number_input(
            "Your SS Benefit (monthly) ($)",
            min_value=0.0, max_value=10_000.0,
            value=3_200.0, step=100.0, format="%.0f",
        )
        ss_start_age = st.slider("Your SS Start Age", 62, 70, 67,
            help="62 = reduced, 70 = max (+8%/yr after FRA)")

# ── Tab 2: Household ──────────────────────────────────────────────────────
with tab_household:
    hc1, hc2 = st.columns(2)
    with hc1:
        st.subheader("You")
        current_age     = st.slider("Your Current Age", 50, 80, 57)
        plan_to_age     = st.slider("Plan Through Age", 80, 105, 90)
    with hc2:
        st.subheader("Spouse / Partner")
        spouse_age      = st.slider("Spouse Current Age", 40, 80, 54)
        spouse_plan_age = st.slider("Spouse Plan Through Age", 75, 105, 92,
            help="Used to extend the plan period if spouse outlives you.")
        spouse_ss       = st.number_input("Spouse SS Benefit (monthly) ($)",
            min_value=0.0, max_value=10_000.0, value=2_200.0, step=100.0, format="%.0f")
        spouse_ss_age   = st.slider("Spouse SS Start Age", 62, 70, 67)

    st.subheader("Survivor Withdrawals")
    st.caption("When the first person dies, household expenses typically drop but don't halve. Set the survivor monthly withdrawal for each scenario.")
    sv1, sv2 = st.columns(2)
    with sv1:
        survivor_monthly_you = st.number_input(
            "If Spouse Dies First — Your Monthly Need ($)",
            min_value=0.0, max_value=30_000.0, value=6_000.0, step=100.0, format="%.0f",
            help="Your monthly withdrawal after spouse passes."
        )
    with sv2:
        survivor_monthly_spouse = st.number_input(
            "If You Die First — Spouse Monthly Need ($)",
            min_value=0.0, max_value=30_000.0, value=5_500.0, step=100.0, format="%.0f",
            help="Spouse's monthly withdrawal after you pass."
        )

# ── Tab 3: Assumptions ────────────────────────────────────────────────────
with tab_assumptions:
    ac1, ac2 = st.columns(2)
    with ac1:
        st.subheader("Return Scenarios")
        base_return = st.slider("Base Case — Annual Return (%)",
            0.0, 12.0, 6.0, step=0.5,
            help="Concentrated value portfolio — conservative 6% base.")
        long_squeeze_return = st.slider("Long Squeeze — Annual Return (%)",
            -2.0, 8.0, 3.5, step=0.5,
            help="Financial repression: low nominal returns, elevated inflation.")
        bear_return = st.slider("Bear Case — Annual Return (%)",
            -5.0, 4.0, 1.0, step=0.5,
            help="Stagflation / passive bubble pop.")
    with ac2:
        st.subheader("Inflation & Volatility")
        inflation = st.slider("Inflation Assumption (%)",
            1.0, 8.0, 4.0, step=0.5,
            help="Long Squeeze default: 4%. Standard planning uses 2-3%.")
        return_volatility = st.slider("Return Volatility (std dev %)",
            2.0, 20.0, 10.0, step=1.0,
            help="Year-to-year variation. Concentrated portfolio ~10-14%.")

# ── Tab 4: Goals & Events ─────────────────────────────────────────────────
with tab_events:
    st.subheader("🎯 Goals, Expenses, Gifts & Inheritance")
    st.caption(
        "Add any irregular cash flows — one-time or recurring. "
        "**Expenses** reduce the portfolio (negative). **Income** adds to it (positive). "
        "Ages are yours. All amounts in today's dollars — inflation-adjusted automatically in the model."
    )

    # Initialise event table in session state
    if "retire_events" not in st.session_state:
        st.session_state.retire_events = [
            {"label": "New car",          "type": "Expense", "amount": 45_000, "start_age": 60, "end_age": 60, "freq": "One-time"},
            {"label": "Travel budget",    "type": "Expense", "amount": 15_000, "start_age": 60, "end_age": 75, "freq": "Annual"},
            {"label": "Wedding gift",     "type": "Expense", "amount": 30_000, "start_age": 65, "end_age": 65, "freq": "One-time"},
            {"label": "Inheritance",      "type": "Income",  "amount": 200_000,"start_age": 68, "end_age": 68, "freq": "One-time"},
        ]

    events = st.session_state.retire_events

    # Column headers
    h0, h1, h2, h3, h4, h5, h6 = st.columns([2.5, 1.2, 1.5, 1.2, 1.2, 1.5, 0.6])
    h0.markdown("**Label**"); h1.markdown("**Type**"); h2.markdown("**Amount ($)**")
    h3.markdown("**Start Age**"); h4.markdown("**End Age**"); h5.markdown("**Frequency**"); h6.markdown("**Del**")

    to_delete = []
    for idx, ev in enumerate(events):
        c0, c1, c2, c3, c4, c5, c6 = st.columns([2.5, 1.2, 1.5, 1.2, 1.2, 1.5, 0.6])
        ev["label"]     = c0.text_input("", value=ev["label"],    key=f"ev_lbl_{idx}", label_visibility="collapsed")
        ev["type"]      = c1.selectbox("", ["Expense","Income"],  key=f"ev_typ_{idx}", label_visibility="collapsed",
                                        index=0 if ev["type"]=="Expense" else 1)
        ev["amount"]    = c2.number_input("", value=float(ev["amount"]), min_value=0.0,
                                           step=1_000.0, format="%.0f", key=f"ev_amt_{idx}", label_visibility="collapsed")
        ev["start_age"] = c3.number_input("", value=int(ev["start_age"]), min_value=current_age,
                                           max_value=110, step=1, key=f"ev_sta_{idx}", label_visibility="collapsed")
        ev["end_age"]   = c4.number_input("", value=int(ev["end_age"]),   min_value=current_age,
                                           max_value=110, step=1, key=f"ev_ena_{idx}", label_visibility="collapsed")
        ev["freq"]      = c5.selectbox("", ["One-time","Annual"], key=f"ev_frq_{idx}", label_visibility="collapsed",
                                        index=0 if ev["freq"]=="One-time" else 1)
        if c6.button("🗑", key=f"ev_del_{idx}"):
            to_delete.append(idx)

    for idx in reversed(to_delete):
        events.pop(idx)
        st.rerun()

    if st.button("➕ Add Event", type="secondary"):
        events.append({"label": "New event", "type": "Expense", "amount": 10_000,
                        "start_age": current_age + 5, "end_age": current_age + 5, "freq": "One-time"})
        st.rerun()

    st.session_state.retire_events = events

    # Preview table
    if events:
        preview = pd.DataFrame(events)
        preview["amount"] = preview["amount"].apply(lambda x: f"${x:,.0f}")
        preview["sign"]   = preview["type"].apply(lambda t: "➖ outflow" if t == "Expense" else "➕ inflow")
        st.dataframe(preview[["label","sign","amount","start_age","end_age","freq"]],
                      hide_index=True, use_container_width=True)

st.divider()

# ─────────────────────────────────────────────
# DERIVED CALCULATIONS — HOUSEHOLD MODEL
# ─────────────────────────────────────────────
annual_withdrawal    = monthly_withdrawal * 12
annual_ss_you        = ss_monthly * 12
annual_ss_spouse     = spouse_ss * 12
investable_portfolio = portfolio_val - cash_buffer
years                = max(plan_to_age, spouse_plan_age) - current_age
inf_r                = inflation / 100

# ── Build net cashflow array by your age ──────────────────────────────────
# net_cf[t] = amount drawn FROM portfolio at year t (positive = draw, negative = add)
# t=0 is current_age, t=1 is current_age+1, etc.
n_years = years + 1
your_ages    = np.arange(current_age, current_age + n_years)
spouse_ages  = spouse_age + (your_ages - current_age)

# Base annual gap before events (both alive, no SS yet)
# Phase 1: pre-SS (both alive)
# Phase 2: post SS (both alive)
# Phase 3: survivor (one has died — simplified: assume you die at plan_to_age, spouse lives to spouse_plan_age)
#           The model runs the household together; survivor adjustment applied from plan_to_age

base_need   = np.full(n_years, annual_withdrawal)       # both alive
ss_income   = np.zeros(n_years)

for t, ya in enumerate(your_ages):
    if ya >= ss_start_age:
        ss_income[t] += annual_ss_you
    if spouse_ages[t] >= spouse_ss_age:
        ss_income[t] += annual_ss_spouse

# Survivor phase: after plan_to_age (simplified: you die, spouse continues)
survivor_annual = survivor_monthly_spouse * 12
for t, ya in enumerate(your_ages):
    if ya > plan_to_age:
        base_need[t]  = survivor_annual
        ss_income[t]  = annual_ss_spouse if spouse_ages[t] >= spouse_ss_age else 0

passive_arr = np.full(n_years, annual_passive + other_income)
net_cf      = base_need - ss_income - passive_arr   # portfolio draw needed each year

# ── Apply event cash flows ────────────────────────────────────────────────
event_cf = np.zeros(n_years)   # additional impacts from goals/events
for ev in st.session_state.get("retire_events", []):
    sign = -1 if ev["type"] == "Expense" else +1   # expense = draw more, income = draw less
    for t, ya in enumerate(your_ages):
        if ev["freq"] == "One-time" and int(ya) == int(ev["start_age"]):
            event_cf[t] += sign * ev["amount"]
        elif ev["freq"] == "Annual" and int(ev["start_age"]) <= int(ya) <= int(ev["end_age"]):
            event_cf[t] += sign * ev["amount"]

# Net portfolio draw per year (positive = draw, negative = add to portfolio)
# Events reduce the draw (income) or increase it (expense)
net_cf_total = net_cf - event_cf   # event income reduces draw; event expense increases it

# Clamp: portfolio can't be drawn below zero (handled in simulation)
# Also store a phase label for the income gap chart
ss_years = max(0, ss_start_age - current_age)

# For backwards compat with gap chart — use year 0 and post-SS-year values
gap_pre_ss  = max(net_cf_total[0], 0)
gap_post_ss = max(net_cf_total[min(ss_years + 1, n_years - 1)], 0)

# ─────────────────────────────────────────────
# SECTION 2: INCOME GAP ANALYSIS
# ─────────────────────────────────────────────
st.header("💰 Income Gap Analysis")

g1, g2, g3, g4 = st.columns(4)
with g1:
    st.metric("Household Monthly Need", f"${monthly_withdrawal:,.0f}",
              help="Both alive, today's dollars")
with g2:
    st.metric("Annual Passive Income",  f"${annual_passive:,.0f}")
with g3:
    combined_ss = annual_ss_you + annual_ss_spouse
    st.metric("Combined SS (both active)", f"${combined_ss:,.0f}/yr",
              help=f"You: ${annual_ss_you:,.0f} | Spouse: ${annual_ss_spouse:,.0f}")
with g4:
    st.metric("Net Portfolio Draw (year 1)", f"${max(net_cf_total[0],0):,.0f}/yr",
              delta=f"-${max(net_cf_total[0],0)/12:,.0f}/mo", delta_color="inverse")

# Full cashflow timeline chart
fig_gap = go.Figure()
fig_gap.add_trace(go.Bar(name="Passive Income",  x=your_ages, y=passive_arr,           marker_color="#2ecc71"))
fig_gap.add_trace(go.Bar(name="Social Security", x=your_ages, y=ss_income,             marker_color="#3498db"))
fig_gap.add_trace(go.Bar(name="Event Income",    x=your_ages, y=np.maximum(event_cf,0),marker_color="#9b59b6"))
fig_gap.add_trace(go.Bar(name="Portfolio Draw",  x=your_ages, y=np.maximum(net_cf_total,0), marker_color="#e74c3c"))
fig_gap.add_trace(go.Bar(name="Event Expense",   x=your_ages, y=np.maximum(-event_cf,0),marker_color="#e67e22"))
fig_gap.add_trace(go.Scatter(
    name="Withdrawal Target", x=your_ages,
    y=np.where(your_ages <= plan_to_age, annual_withdrawal, survivor_annual),
    mode="lines", line=dict(color="white", width=2, dash="dash")))
fig_gap.add_vline(x=plan_to_age, line_dash="dot", line_color="#888",
                   annotation_text=f"Your plan age {plan_to_age}")
fig_gap.add_vline(x=ss_start_age, line_dash="dash", line_color="#3498db",
                   annotation_text=f"Your SS age {ss_start_age}")
spouse_ss_your_age = current_age + (spouse_ss_age - spouse_age)
if spouse_ss_your_age > current_age:
    fig_gap.add_vline(x=spouse_ss_your_age, line_dash="dash", line_color="#5dade2",
                       annotation_text=f"Spouse SS")
fig_gap.update_layout(
    barmode="stack", height=380,
    title="Annual Cash Flow Timeline (Today's Dollars)",
    yaxis_title="$ / Year", xaxis_title="Your Age",
    margin=dict(t=40, b=110),
    legend=dict(orientation="h", yanchor="top", y=-0.18, x=0.5, xanchor="center"),
)
st.plotly_chart(fig_gap, use_container_width=True)

real_return_base = base_return - inflation
st.markdown(f"""
**Real return (base − inflation):** {base_return:.1f}% − {inflation:.1f}% = **{real_return_base:.1f}%** {"⚠️ Negative real return" if real_return_base < 0 else "✅"}
&nbsp;&nbsp;·&nbsp;&nbsp;
**Survivor withdrawal:** ${survivor_monthly_spouse:,.0f}/mo after your plan age {plan_to_age}
&nbsp;&nbsp;·&nbsp;&nbsp;
**Events loaded:** {len(st.session_state.get('retire_events', []))}
""")
st.divider()

# ─────────────────────────────────────────────
# SECTION 3: MONTE CARLO
# ─────────────────────────────────────────────
st.header("🎲 Monte Carlo Simulation")
st.caption(f"1,000 simulations × {years} years. Each run draws annual returns from a normal distribution around the scenario mean.")

n_sims    = 1_000
ages      = np.arange(current_age, current_age + n_years)
n_periods = n_years - 1

np.random.seed(42)

def run_simulation(mean_return_pct, vol_pct, n_sims, n_periods,
                   start_portfolio, net_cf_arr, inflation_pct):
    """
    Run Monte Carlo with a full cashflow array.
    net_cf_arr[t] = net portfolio draw at year t (positive = draw, negative = inflow).
    Withdrawals are inflation-adjusted each year.
    Returns array (n_sims, n_periods+1) of portfolio values.
    """
    results = np.zeros((n_sims, n_periods + 1))
    results[:, 0] = start_portfolio
    mean_r = mean_return_pct / 100
    vol_r  = vol_pct / 100
    inf_r  = inflation_pct / 100

    for t in range(1, n_periods + 1):
        annual_returns  = np.random.normal(mean_r, vol_r, n_sims)
        draw            = net_cf_arr[t] * ((1 + inf_r) ** t)   # inflation-adjust
        results[:, t]   = results[:, t-1] * (1 + annual_returns) - draw
        results[:, t]   = np.maximum(results[:, t], 0)

    return results

with st.spinner("Running 3,000 simulations..."):
    sims_base    = run_simulation(base_return,         return_volatility, n_sims, n_periods,
                                   investable_portfolio, net_cf_total, inflation)
    sims_squeeze = run_simulation(long_squeeze_return, return_volatility, n_sims, n_periods,
                                   investable_portfolio, net_cf_total, inflation)
    sims_bear    = run_simulation(bear_return,         return_volatility, n_sims, n_periods,
                                   investable_portfolio, net_cf_total, inflation)

def survival_rate(sims):
    """% of simulations where portfolio > 0 at each age."""
    return (sims > 0).mean(axis=0) * 100

def percentiles(sims):
    p10 = np.percentile(sims, 10, axis=0)
    p50 = np.percentile(sims, 50, axis=0)
    p90 = np.percentile(sims, 90, axis=0)
    return p10, p50, p90

surv_base    = survival_rate(sims_base)
surv_squeeze = survival_rate(sims_squeeze)
surv_bear    = survival_rate(sims_bear)

# ── Survival probability chart ─────────────────────────────────────────────
fig_surv = go.Figure()
fig_surv.add_trace(go.Scatter(x=ages, y=surv_base,    name=f"Base ({base_return:.1f}%)",
                               line=dict(color="#2ecc71", width=3)))
fig_surv.add_trace(go.Scatter(x=ages, y=surv_squeeze, name=f"Long Squeeze ({long_squeeze_return:.1f}%)",
                               line=dict(color="#f39c12", width=3)))
fig_surv.add_trace(go.Scatter(x=ages, y=surv_bear,    name=f"Bear ({bear_return:.1f}%)",
                               line=dict(color="#e74c3c", width=3)))
fig_surv.add_hline(y=90, line_dash="dot", line_color="#888",
                    annotation_text="90% survival threshold", annotation_position="right")
fig_surv.add_hline(y=50, line_dash="dot", line_color="#555",
                    annotation_text="50% survival", annotation_position="right")
fig_surv.add_vline(x=ss_start_age, line_dash="dash", line_color="#3498db",
                    annotation_text=f"SS starts (age {ss_start_age})", annotation_position="top right")
fig_surv.update_layout(
    title="Portfolio Survival Probability by Age",
    xaxis_title="Age", yaxis_title="% Simulations Surviving",
    yaxis=dict(range=[0, 105]),
    height=380, margin=dict(t=40, b=110),
    legend=dict(orientation="h", yanchor="top", y=-0.18, x=0.5, xanchor="center"),
)
st.plotly_chart(fig_surv, use_container_width=True)

# ── Portfolio value fan chart ──────────────────────────────────────────────
p10_b, p50_b, p90_b  = percentiles(sims_base)
p10_s, p50_s, p90_s  = percentiles(sims_squeeze)

# Mean (average across all 1,000 runs) — will be higher than median due to right skew
mean_b = sims_base.mean(axis=0)
mean_s = sims_squeeze.mean(axis=0)

fig_fan = go.Figure()
# Base case fan
fig_fan.add_trace(go.Scatter(x=np.concatenate([ages, ages[::-1]]),
                              y=np.concatenate([p90_b, p10_b[::-1]]) / 1e6,
                              fill='toself', fillcolor='rgba(46,204,113,0.15)',
                              line=dict(color='rgba(0,0,0,0)'), name='Base 10th–90th %ile', showlegend=True))
fig_fan.add_trace(go.Scatter(x=ages, y=p50_b / 1e6, name=f"Base median/p50 ({base_return:.1f}%)",
                              line=dict(color="#2ecc71", width=2)))
fig_fan.add_trace(go.Scatter(x=ages, y=mean_b / 1e6, name=f"Base mean (avg of all runs)",
                              line=dict(color="#2ecc71", width=2, dash="dot")))
# Long Squeeze fan
fig_fan.add_trace(go.Scatter(x=np.concatenate([ages, ages[::-1]]),
                              y=np.concatenate([p90_s, p10_s[::-1]]) / 1e6,
                              fill='toself', fillcolor='rgba(243,156,18,0.15)',
                              line=dict(color='rgba(0,0,0,0)'), name='Long Squeeze 10th–90th %ile', showlegend=True))
fig_fan.add_trace(go.Scatter(x=ages, y=p50_s / 1e6, name=f"Long Squeeze median/p50 ({long_squeeze_return:.1f}%)",
                              line=dict(color="#f39c12", width=2)))
fig_fan.add_trace(go.Scatter(x=ages, y=mean_s / 1e6, name=f"Long Squeeze mean (avg of all runs)",
                              line=dict(color="#f39c12", width=2, dash="dot")))
fig_fan.add_hline(y=0, line_color="#e74c3c", line_width=2, annotation_text="Ruin")
fig_fan.add_vline(x=ss_start_age, line_dash="dash", line_color="#3498db")
fig_fan.update_layout(
    title="Portfolio Value Fan Chart ($ Millions) — Solid = Median, Dotted = Mean",
    xaxis_title="Age", yaxis_title="Portfolio Value ($M)",
    height=380, margin=dict(t=40, b=110),
    legend=dict(orientation="h", yanchor="top", y=-0.18, x=0.5, xanchor="center"),
)
st.plotly_chart(fig_fan, use_container_width=True)
st.caption("**Median (solid line):** the outcome you have a 50/50 chance of beating — the honest central estimate. **Mean (dotted):** average of all 1,000 runs — skewed upward by the small number of runs where the portfolio compounds strongly. Plan against the median, not the mean.")

# ── Percentile pressure tester ─────────────────────────────────────────────
st.subheader("🎛️ Percentile Pressure Tester")
st.caption("Slide to any percentile to see what that outcome looks like. 50th = median. 10th = bottom 10% of all simulations — the stress test.")

ptile = st.slider(
    "Percentile to examine",
    min_value=5, max_value=95, value=50, step=5,
    format="%d%%",
    help="10th percentile = 90% of simulations did better than this. 50th = median. 90th = only 10% did this well."
)

pval_b = np.percentile(sims_base,   ptile, axis=0)
pval_s = np.percentile(sims_squeeze, ptile, axis=0)
pval_br = np.percentile(sims_bear,   ptile, axis=0)

fig_ptile = go.Figure()
fig_ptile.add_trace(go.Scatter(x=ages, y=pval_b  / 1e6, name=f"Base ({base_return:.1f}%) — {ptile}th %ile",
                                line=dict(color="#2ecc71", width=3)))
fig_ptile.add_trace(go.Scatter(x=ages, y=pval_s  / 1e6, name=f"Long Squeeze ({long_squeeze_return:.1f}%) — {ptile}th %ile",
                                line=dict(color="#f39c12", width=3)))
fig_ptile.add_trace(go.Scatter(x=ages, y=pval_br / 1e6, name=f"Bear ({bear_return:.1f}%) — {ptile}th %ile",
                                line=dict(color="#e74c3c", width=3)))
fig_ptile.add_hline(y=0, line_color="#e74c3c", line_width=1, line_dash="dot")
fig_ptile.add_vline(x=ss_start_age, line_dash="dash", line_color="#3498db",
                     annotation_text=f"SS starts (age {ss_start_age})")

# Label what this percentile means in plain English
if ptile <= 10:
    ptile_label = "Stress test — only 10% of outcomes are this bad or worse"
elif ptile <= 25:
    ptile_label = "Pessimistic — bottom quartile of outcomes"
elif ptile <= 45:
    ptile_label = "Below median — more bad luck than good"
elif ptile <= 55:
    ptile_label = "Median — your most likely outcome (50/50)"
elif ptile <= 75:
    ptile_label = "Above median — more good luck than bad"
else:
    ptile_label = "Optimistic — top quartile; don't plan on this"

fig_ptile.update_layout(
    title=f"{ptile}th Percentile Portfolio Path — {ptile_label}",
    xaxis_title="Age", yaxis_title="Portfolio Value ($M)",
    height=380, margin=dict(t=50, b=110),
    legend=dict(orientation="h", yanchor="top", y=-0.18, x=0.5, xanchor="center"),
)
st.plotly_chart(fig_ptile, use_container_width=True)

# Show terminal values at selected percentile
pv1, pv2, pv3 = st.columns(3)
with pv1:
    st.metric(f"Base at {plan_to_age} ({ptile}th %ile)", f"${pval_b[-1]/1e6:.2f}M",
              delta="✅ Solvent" if pval_b[-1] > 0 else "❌ Depleted", delta_color="normal" if pval_b[-1] > 0 else "inverse")
with pv2:
    st.metric(f"Long Squeeze at {plan_to_age} ({ptile}th %ile)", f"${pval_s[-1]/1e6:.2f}M",
              delta="✅ Solvent" if pval_s[-1] > 0 else "❌ Depleted", delta_color="normal" if pval_s[-1] > 0 else "inverse")
with pv3:
    st.metric(f"Bear at {plan_to_age} ({ptile}th %ile)", f"${pval_br[-1]/1e6:.2f}M",
              delta="✅ Solvent" if pval_br[-1] > 0 else "❌ Depleted", delta_color="normal" if pval_br[-1] > 0 else "inverse")

st.divider()

# ── Spaghetti chart — all simulation lines ─────────────────────────────────
st.subheader("🍝 All Simulation Paths")
st.caption("Every line is one simulation. Green lines survive to your plan age. Red lines hit zero. The density of lines near the bottom tells you the true shape of risk.")

spag_scenario = st.selectbox(
    "Scenario to display",
    options=["Base", "Long Squeeze", "Bear"],
    index=0,
    help="Showing all 1,000 lines at once — select which scenario to examine."
)

sims_map    = {"Base": sims_base, "Long Squeeze": sims_squeeze, "Bear": sims_bear}
color_map   = {"Base": "#2ecc71",  "Long Squeeze": "#f39c12",    "Bear": "#e74c3c"}
chosen_sims  = sims_map[spag_scenario]
chosen_color = color_map[spag_scenario]

# Downsample to 300 lines for performance
n_display  = min(300, n_sims)
rng        = np.random.default_rng(seed=7)
sample_idx = rng.choice(n_sims, size=n_display, replace=False)

# Pre-compute percentile rank for every sampled path based on ending balance.
# rank = what percentile this path's ending balance falls at across ALL 1,000 sims.
all_endings   = chosen_sims[:, -1]                          # ending balance of every sim
sample_endings = chosen_sims[sample_idx, -1]                 # just the sampled ones
# percentile rank: how many of the full 1,000 sims ended below this path
pct_ranks = np.array([
    np.sum(all_endings <= end) / n_sims * 100
    for end in sample_endings
])

# Classify survived vs depleted
survived_mask = sample_endings > 0
survived_idx  = sample_idx[survived_mask]
depleted_idx  = sample_idx[~survived_mask]
survived_ranks = pct_ranks[survived_mask]
depleted_ranks = pct_ranks[~survived_mask]

surv_color_rgba = (
    "rgba(46,204,113,0.20)"  if spag_scenario == "Base" else
    "rgba(243,156,18,0.20)"  if spag_scenario == "Long Squeeze" else
    "rgba(231,76,60,0.20)"
)

fig_spag = go.Figure()

# ── Depleted paths (red, behind) ───────────────────────────────────────────
for rank, i in zip(depleted_ranks, depleted_idx):
    end_val = chosen_sims[i, -1]
    # Build hover text for every age point
    hover = [
        f"<b>Age {a}</b><br>"
        f"Value: ${chosen_sims[i, t]/1e6:.2f}M<br>"
        f"Percentile rank: {rank:.0f}th<br>"
        f"Ending balance: ${end_val/1e6:.2f}M<br>"
        f"Outcome: ❌ Depleted"
        for t, a in enumerate(ages)
    ]
    fig_spag.add_trace(go.Scatter(
        x=ages, y=chosen_sims[i] / 1e6,
        mode="lines",
        line=dict(color="rgba(231,76,60,0.30)", width=1.0),
        showlegend=False,
        hovertemplate="%{customdata}<extra></extra>",
        customdata=hover,
    ))

# ── Surviving paths (green/orange, on top) ─────────────────────────────────
for rank, i in zip(survived_ranks, survived_idx):
    end_val = chosen_sims[i, -1]
    hover = [
        f"<b>Age {a}</b><br>"
        f"Value: ${chosen_sims[i, t]/1e6:.2f}M<br>"
        f"Percentile rank: {rank:.0f}th<br>"
        f"Ending balance: ${end_val/1e6:.2f}M<br>"
        f"Outcome: ✅ Solvent"
        for t, a in enumerate(ages)
    ]
    fig_spag.add_trace(go.Scatter(
        x=ages, y=chosen_sims[i] / 1e6,
        mode="lines",
        line=dict(color=surv_color_rgba, width=1.0),
        showlegend=False,
        hovertemplate="%{customdata}<extra></extra>",
        customdata=hover,
    ))

# ── Overlay lines: selected percentile + median ────────────────────────────
pval_chosen = np.percentile(chosen_sims, ptile, axis=0)
fig_spag.add_trace(go.Scatter(
    x=ages, y=pval_chosen / 1e6,
    name=f"{ptile}th percentile",
    line=dict(color="white", width=2.5),
    hovertemplate="<b>Age %{x}</b><br>%{y:.2f}M — <b>" + str(ptile) + "th percentile</b><extra></extra>",
))
fig_spag.add_trace(go.Scatter(
    x=ages, y=np.percentile(chosen_sims, 50, axis=0) / 1e6,
    name="Median (p50)",
    line=dict(color=chosen_color, width=2.5),
    hovertemplate="<b>Age %{x}</b><br>%{y:.2f}M — <b>Median (p50)</b><extra></extra>",
))

n_survived = survived_mask.sum()
n_depleted = (~survived_mask).sum()
surv_pct   = n_survived / n_display * 100

fig_spag.add_hline(y=0, line_color="#e74c3c", line_width=1)
fig_spag.add_vline(x=ss_start_age, line_dash="dash", line_color="#3498db",
                    annotation_text=f"SS age {ss_start_age}")
fig_spag.update_layout(
    title=(f"{spag_scenario} — {n_display} of 1,000 paths · "
           f"{surv_pct:.0f}% survive to {plan_to_age} · "
           f"{100-surv_pct:.0f}% depleted"),
    xaxis_title="Age",
    yaxis_title="Portfolio Value ($M)",
    height=500,
    margin=dict(t=50, b=110),
    legend=dict(orientation="h", yanchor="top", y=-0.12, x=0.5, xanchor="center"),
    hovermode="closest",
)
st.plotly_chart(fig_spag, use_container_width=True)
st.caption(
    f"**Click / hover any line** to see its percentile rank and ending balance. "
    f"🟢 Faded lines = solvent paths · 🔴 Red lines = depleted · "
    f"White = {ptile}th percentile · Colored = median"
)

# ── Key stats ──────────────────────────────────────────────────────────────
def pct_at_age(surv, target_age):
    idx = target_age - current_age
    return surv[idx] if 0 <= idx < len(surv) else 0

mc1, mc2, mc3 = st.columns(3)
with mc1:
    s80b = pct_at_age(surv_base, 80)
    s80s = pct_at_age(surv_squeeze, 80)
    st.metric("Survival to Age 80", f"{s80b:.0f}% base / {s80s:.0f}% squeeze",
              delta=f"{s80b - s80s:.0f}pp spread", delta_color="normal")
with mc2:
    s90b = pct_at_age(surv_base, 90)
    s90s = pct_at_age(surv_squeeze, 90)
    st.metric("Survival to Age 90", f"{s90b:.0f}% base / {s90s:.0f}% squeeze",
              delta=f"{s90b - s90s:.0f}pp spread", delta_color="normal")
with mc3:
    final_p50_base    = p50_b[-1] / 1e6
    final_mean_base   = mean_b[-1] / 1e6
    final_p50_squeeze = p50_s[-1] / 1e6
    final_mean_squeeze= mean_s[-1] / 1e6
    st.metric(f"At Age {plan_to_age} — Base",
              f"Median ${final_p50_base:.2f}M / Mean ${final_mean_base:.2f}M",
              help="Mean is higher than median due to right-skew — a few great runs pull the average up. Plan against the median.")

st.divider()

# ─────────────────────────────────────────────
# SECTION 4: SEQUENCE OF RETURNS RISK
# ─────────────────────────────────────────────
st.header("⚠️ Sequence of Returns Risk")
st.caption(f"Three independent simulations with the same long-run average return. The only difference: years 1–5 are forced above average (good start) or below average (bad start). The early gap compounds permanently — withdrawals during a downturn lock in losses that never recover.")

# ── Sequence of returns: independent paths, forced early divergence ──────────
# Good-start and bad-start are INDEPENDENT simulations.
# Years 1-5 are forced above/below average. Years 6+ are independent random draws
# from the same distribution — similar long-run averages, but paths never converge
# because the early damage (or boost) compounds permanently through withdrawals.
np.random.seed(99)
base_r = base_return / 100
vol_r  = return_volatility / 100
inf_r  = inflation / 100

crash_depth  = max(vol_r * 1.5, 0.12)   # bad start loses ~1.5 sigma/yr in first 5 yrs
boom_height  = max(vol_r * 1.0, 0.08)   # good start gains ~1.0 sigma/yr above avg

# First 5 years: forced above/below average
early_good = np.random.normal(base_r + boom_height,  vol_r * 0.4, 5)
early_bad  = np.random.normal(base_r - crash_depth,  vol_r * 0.4, 5)

# Years 6+: fully independent random draws from same distribution
tail_good  = np.random.normal(base_r, vol_r, years - 5)
tail_bad   = np.random.normal(base_r, vol_r, years - 5)
tail_avg   = np.random.normal(base_r, vol_r, years - 5)
early_avg  = np.random.normal(base_r, vol_r * 0.6, 5)

good_first = np.concatenate([early_good, tail_good])
bad_first  = np.concatenate([early_bad,  tail_bad])
same_avg   = np.concatenate([early_avg,  tail_avg])

def simulate_path(returns, start, net_cf_arr, inflation_rate):
    portfolio  = [start]
    for t, r in enumerate(returns, 1):
        draw = net_cf_arr[min(t, len(net_cf_arr)-1)] * ((1 + inflation_rate) ** t)
        val  = portfolio[-1] * (1 + r) - draw
        portfolio.append(max(val, 0))
    return portfolio

path_good = simulate_path(good_first, investable_portfolio, net_cf_total, inf_r)
path_bad  = simulate_path(bad_first,  investable_portfolio, net_cf_total, inf_r)
path_avg  = simulate_path(same_avg,   investable_portfolio, net_cf_total, inf_r)

path_ages = list(range(current_age, current_age + years + 1))

fig_seq = go.Figure()
fig_seq.add_trace(go.Scatter(x=path_ages, y=[v/1e6 for v in path_good],
                              name="Good start (above avg first 5 yrs)",
                              line=dict(color="#2ecc71", width=2)))
fig_seq.add_trace(go.Scatter(x=path_ages, y=[v/1e6 for v in path_avg],
                              name="Average start",
                              line=dict(color="#888", width=2, dash="dot")))
fig_seq.add_trace(go.Scatter(x=path_ages, y=[v/1e6 for v in path_bad],
                              name="Bad start (below avg first 5 yrs)",
                              line=dict(color="#e74c3c", width=2)))
fig_seq.add_hline(y=0, line_color="#e74c3c", line_width=1)
fig_seq.add_vrect(x0=current_age, x1=current_age + 5,
                   fillcolor="rgba(231,76,60,0.08)", line_width=0,
                   annotation_text="Forced early divergence", annotation_position="top left")
fig_seq.add_vline(x=ss_start_age, line_dash="dash", line_color="#3498db",
                   annotation_text=f"SS starts", annotation_position="top right")
fig_seq.update_layout(
    title="Same Returns, Different Sequence — Permanently Different Outcomes",
    xaxis_title="Age", yaxis_title="Portfolio Value ($M)",
    height=360, margin=dict(t=40, b=110),
    legend=dict(orientation="h", yanchor="top", y=-0.18, x=0.5, xanchor="center"),
)
st.plotly_chart(fig_seq, use_container_width=True)

final_good = path_good[-1] / 1e6
final_bad  = path_bad[-1] / 1e6
seq_gap    = (final_good - final_bad) / 1e6

sc1, sc2, sc3 = st.columns(3)
with sc1:
    st.metric(f"Good Start — Portfolio at {plan_to_age}", f"${final_good:.2f}M")
with sc2:
    st.metric(f"Bad Start — Portfolio at {plan_to_age}",  f"${final_bad:.2f}M",
              delta=f"-${abs(final_good - final_bad)/1e6:.2f}M vs good start", delta_color="inverse")
with sc3:
    buffer_years = cash_buffer / (gap_pre_ss / 12) if gap_pre_ss > 0 else 99
    st.metric("Cash Buffer Covers", f"{buffer_years:.1f} months of gap",
              help=f"${cash_buffer:,.0f} buffer ÷ ${gap_pre_ss/12:,.0f}/mo portfolio draw")

st.info(f"""
**The defense:** Your ${cash_buffer:,.0f} cash buffer means you can fund **{buffer_years:.0f} months** 
of withdrawals without touching investments. In a bad market, you draw from cash — not from 
depressed equities. This is the single most important structural protection for a concentrated 
portfolio in retirement.
""")

st.divider()

# ─────────────────────────────────────────────
# SECTION 5: LONG SQUEEZE OVERLAY
# ─────────────────────────────────────────────
st.header("🔧 Long Squeeze Overlay")
st.caption("Your macro thesis applied to retirement math. Financial repression erodes purchasing power even when nominal portfolio values look fine.")

ls1, ls2 = st.columns(2)

with ls1:
    st.subheader("Real vs Nominal Portfolio Value")
    # Show how inflation erodes the purchasing power of a growing portfolio
    nominal_vals = [investable_portfolio]
    real_vals    = [investable_portfolio]
    for t in range(1, years + 1):
        draw      = net_cf_total[min(t, n_years-1)] * ((1 + inf_r) ** t)
        nom_new   = nominal_vals[-1] * (1 + long_squeeze_return / 100) - draw
        nominal_vals.append(max(nom_new, 0))
        real_vals.append(nominal_vals[-1] / ((1 + inf_r) ** t))

    fig_real = go.Figure()
    fig_real.add_trace(go.Scatter(x=path_ages, y=[v/1e6 for v in nominal_vals],
                                   name="Nominal value", line=dict(color="#f39c12", width=2)))
    fig_real.add_trace(go.Scatter(x=path_ages, y=[v/1e6 for v in real_vals],
                                   name=f"Real value (today's $)", line=dict(color="#e74c3c", width=2, dash="dot")))
    fig_real.add_hline(y=0, line_color="#e74c3c")
    fig_real.update_layout(
        title=f"Long Squeeze: Nominal vs Real ({inflation:.1f}% inflation)",
        xaxis_title="Age", yaxis_title="Value ($M)",
        height=380, margin=dict(t=40, b=110),
        legend=dict(orientation="h", yanchor="top", y=-0.18, x=0.5, xanchor="center"),
    )
    st.plotly_chart(fig_real, use_container_width=True)

with ls2:
    st.subheader("What $8K/Month Actually Buys")
    # Purchasing power of the monthly withdrawal over time
    future_years    = np.arange(0, years + 1)
    pwr_base        = monthly_withdrawal / ((1 + 0.02) ** future_years)   # standard 2%
    pwr_long_squeeze = monthly_withdrawal / ((1 + inf_r)  ** future_years)  # your thesis

    fig_pwr = go.Figure()
    fig_pwr.add_trace(go.Scatter(x=current_age + future_years, y=pwr_base,
                                  name="2% inflation (standard)",
                                  line=dict(color="#2ecc71", width=2)))
    fig_pwr.add_trace(go.Scatter(x=current_age + future_years, y=pwr_long_squeeze,
                                  name=f"{inflation:.1f}% inflation (Long Squeeze)",
                                  line=dict(color="#e74c3c", width=2)))
    fig_pwr.add_hline(y=monthly_withdrawal * 0.5, line_dash="dot", line_color="#888",
                       annotation_text="50% purchasing power lost")
    fig_pwr.update_layout(
        title=f"Purchasing Power of ${monthly_withdrawal:,.0f}/Month",
        xaxis_title="Age", yaxis_title="Today's Dollar Equivalent ($)",
        height=380, margin=dict(t=40, b=110),
        legend=dict(orientation="h", yanchor="top", y=-0.18, x=0.5, xanchor="center"),
    )
    st.plotly_chart(fig_pwr, use_container_width=True)

# ── Withdrawal adjustment needed ───────────────────────────────────────────
st.subheader("Inflation-Adjusted Withdrawal Requirement")
adj_ages  = list(range(current_age, plan_to_age + 1))
adj_needs = [monthly_withdrawal * ((1 + inf_r) ** t) * 12 for t in range(len(adj_ages))]
adj_std   = [monthly_withdrawal * ((1 + 0.02)  ** t) * 12 for t in range(len(adj_ages))]

fig_adj = go.Figure()
fig_adj.add_trace(go.Scatter(x=adj_ages, y=adj_needs,
                              name=f"Long Squeeze ({inflation:.1f}%)", fill='toself',
                              line=dict(color="#e74c3c"), fillcolor="rgba(231,76,60,0.1)"))
fig_adj.add_trace(go.Scatter(x=adj_ages, y=adj_std,
                              name="Standard (2%)", line=dict(color="#2ecc71", width=2)))
fig_adj.add_hline(y=annual_withdrawal, line_dash="dot", line_color="#888",
                   annotation_text=f"Today's need: ${annual_withdrawal:,.0f}/yr")
fig_adj.update_layout(
    title="Annual Withdrawal Requirement Grows With Inflation",
    xaxis_title="Age", yaxis_title="Annual Withdrawal ($)",
    height=380, margin=dict(t=40, b=110),
    legend=dict(orientation="h", yanchor="top", y=-0.18, x=0.5, xanchor="center"),
)
st.plotly_chart(fig_adj, use_container_width=True)

at_90_ls  = adj_needs[min(90 - current_age, len(adj_needs) - 1)]
at_90_std = adj_std[min(90 - current_age, len(adj_std) - 1)]
st.markdown(f"""
At age 90 your **${monthly_withdrawal:,.0f}/month** target requires:
- **${at_90_ls/12:,.0f}/month** in Long Squeeze ({inflation:.1f}% inflation) — ${at_90_ls:,.0f}/yr
- **${at_90_std/12:,.0f}/month** in standard scenario (2%) — ${at_90_std:,.0f}/yr

The difference: **${(at_90_ls - at_90_std):,.0f}/year** — an additional portfolio burden that 
standard financial planning ignores.
""")

st.divider()
st.caption("Model uses simplified annual Monte Carlo with normal return distribution. Not a financial plan. Past returns do not predict future results.")
