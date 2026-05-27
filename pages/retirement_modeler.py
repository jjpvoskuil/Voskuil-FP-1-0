import streamlit as st
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

st.set_page_config(page_title="Retirement Modeler | Voskuil FP", layout="wide")

st.title("🏔️ Final Expedition — Retirement Modeler")
st.caption("Purpose-built for age 57, concentrated value portfolio, Long Squeeze macro overlay.")
st.markdown("> *\"The goal is not to maximize returns. The goal is to never be a forced seller.\"*")
st.divider()

# ─────────────────────────────────────────────
# SECTION 1: YOUR NUMBERS
# ─────────────────────────────────────────────
st.header("📋 Your Numbers")
st.caption("Pre-filled with your current profile. Adjust any value and the model updates instantly.")

col1, col2, col3 = st.columns(3)

with col1:
    st.subheader("Portfolio")
    # Pull from session state if dashboard has been loaded
    default_portfolio = st.session_state.get("total_val", 3_790_000.0)
    portfolio_val = st.number_input(
        "Current Portfolio Value ($)",
        min_value=0.0, max_value=50_000_000.0,
        value=float(default_portfolio),
        step=10_000.0, format="%.0f",
    )
    annual_passive = st.number_input(
        "Annual Passive Income (dividends + interest) ($)",
        min_value=0.0, max_value=500_000.0,
        value=float(st.session_state.get("total_income", 96_000.0)),
        step=1_000.0, format="%.0f",
        help="Your estimated annual income from holdings. Pulls from dashboard if loaded."
    )
    cash_buffer = st.number_input(
        "Cash / Short-term Buffer ($)",
        min_value=0.0, max_value=1_000_000.0,
        value=96_000.0,
        step=5_000.0, format="%.0f",
        help="Cash set aside outside investments — your 'never a forced seller' cushion."
    )

with col2:
    st.subheader("Withdrawals & Income")
    monthly_withdrawal = st.number_input(
        "Monthly Withdrawal Target ($)",
        min_value=0.0, max_value=50_000.0,
        value=8_000.0, step=100.0, format="%.0f",
    )
    ss_monthly = st.number_input(
        "Social Security (monthly, when started) ($)",
        min_value=0.0, max_value=10_000.0,
        value=3_200.0, step=100.0, format="%.0f",
        help="Estimated monthly Social Security benefit."
    )
    ss_start_age = st.slider(
        "Social Security Start Age",
        min_value=62, max_value=70, value=67,
        help="62 = reduced benefit, 70 = maximum benefit (+8%/yr delay after FRA)"
    )
    other_income = st.number_input(
        "Other Annual Income (rental, part-time, etc.) ($)",
        min_value=0.0, max_value=200_000.0,
        value=0.0, step=1_000.0, format="%.0f",
    )

with col3:
    st.subheader("Assumptions")
    current_age = st.slider("Current Age", 50, 75, 57)
    plan_to_age = st.slider("Plan Through Age", 80, 100, 90)
    years = plan_to_age - current_age

    st.markdown("**Return Scenarios**")
    base_return = st.slider(
        "Base Case — Annual Return (%)",
        0.0, 12.0, 6.0, step=0.5,
        help="Concentrated value portfolio — conservative 6% base assumption."
    )
    long_squeeze_return = st.slider(
        "Long Squeeze — Annual Return (%)",
        -2.0, 8.0, 3.5, step=0.5,
        help="Financial repression scenario: low nominal returns, elevated inflation."
    )
    bear_return = st.slider(
        "Bear Case — Annual Return (%)",
        -5.0, 4.0, 1.0, step=0.5,
        help="Stagflation / passive bubble pop scenario."
    )
    inflation = st.slider(
        "Inflation Assumption (%)",
        1.0, 8.0, 4.0, step=0.5,
        help="Long Squeeze default: 4%. Standard planning uses 2-3%."
    )
    return_volatility = st.slider(
        "Return Volatility (std dev %)",
        2.0, 20.0, 10.0, step=1.0,
        help="Year-to-year variation in returns. Concentrated portfolio ~10-14%."
    )

st.divider()

# ─────────────────────────────────────────────
# DERIVED CALCULATIONS
# ─────────────────────────────────────────────
annual_withdrawal    = monthly_withdrawal * 12
annual_ss            = ss_monthly * 12
investable_portfolio = portfolio_val - cash_buffer

# Income by phase (before and after SS)
annual_income_pre_ss  = annual_passive + other_income
annual_income_post_ss = annual_passive + annual_ss + other_income

gap_pre_ss  = max(annual_withdrawal - annual_income_pre_ss,  0)
gap_post_ss = max(annual_withdrawal - annual_income_post_ss, 0)

# Simple burn rate (ignoring returns — worst case)
burn_rate_pre_ss  = gap_pre_ss  / investable_portfolio * 100 if investable_portfolio > 0 else 0
ss_years          = ss_start_age - current_age

# ─────────────────────────────────────────────
# SECTION 2: INCOME GAP ANALYSIS
# ─────────────────────────────────────────────
st.header("💰 Income Gap Analysis")

g1, g2, g3, g4 = st.columns(4)
with g1:
    st.metric("Annual Withdrawal Need",   f"${annual_withdrawal:,.0f}")
with g2:
    st.metric("Annual Passive Income",    f"${annual_passive:,.0f}",
              help="Dividends + interest from portfolio")
with g3:
    st.metric(f"Gap (Pre-SS, ages {current_age}–{ss_start_age})",
              f"${gap_pre_ss:,.0f}/yr",
              delta=f"-${gap_pre_ss/12:,.0f}/mo from portfolio",
              delta_color="inverse")
with g4:
    st.metric(f"Gap (Post-SS, age {ss_start_age}+)",
              f"${gap_post_ss:,.0f}/yr",
              delta=f"-${gap_post_ss/12:,.0f}/mo from portfolio",
              delta_color="inverse")

# Gap visualization
fig_gap = go.Figure()
phases     = [f"Ages {current_age}–{ss_start_age}\n(Pre-SS)", f"Ages {ss_start_age}–{plan_to_age}\n(Post-SS)"]
needs      = [annual_withdrawal, annual_withdrawal]
passives   = [annual_passive, annual_passive]
ss_incomes = [0, annual_ss]
others     = [other_income, other_income]
gaps_list  = [gap_pre_ss, gap_post_ss]

fig_gap.add_trace(go.Bar(name="Passive Income",    x=phases, y=passives,   marker_color="#2ecc71"))
fig_gap.add_trace(go.Bar(name="Social Security",   x=phases, y=ss_incomes, marker_color="#3498db"))
fig_gap.add_trace(go.Bar(name="Other Income",      x=phases, y=others,     marker_color="#9b59b6"))
fig_gap.add_trace(go.Bar(name="Portfolio Draw",    x=phases, y=gaps_list,  marker_color="#e74c3c"))
fig_gap.add_trace(go.Scatter(name="Withdrawal Target", x=phases, y=needs,
                              mode="lines+markers", line=dict(color="white", width=3, dash="dash"),
                              marker=dict(size=10)))
fig_gap.update_layout(
    barmode="stack", height=320,
    title="Annual Cash Flow by Phase",
    yaxis_title="$ / Year",
    margin=dict(t=40, b=20),
    legend=dict(orientation="h", yanchor="bottom", y=1.02),
)
st.plotly_chart(fig_gap, use_container_width=True)

# Sustainability check
years_pre_ss     = ss_start_age - current_age
cost_pre_ss      = gap_pre_ss * years_pre_ss
remaining_after  = investable_portfolio - cost_pre_ss
real_return_base = (base_return - inflation) / 100

st.markdown(f"""
**Pre-SS phase cost** ({years_pre_ss} years × ${gap_pre_ss:,.0f}/yr = **${cost_pre_ss:,.0f}** drawn from portfolio, ignoring returns)

**Portfolio remaining at SS start** (no-return scenario): **${remaining_after:,.0f}**

**Real return (base case − inflation):** {base_return:.1f}% − {inflation:.1f}% = **{(base_return-inflation):.1f}%** {'⚠️ Negative real return in Long Squeeze' if base_return < inflation else '✅'}
""")
st.divider()

# ─────────────────────────────────────────────
# SECTION 3: MONTE CARLO
# ─────────────────────────────────────────────
st.header("🎲 Monte Carlo Simulation")
st.caption(f"1,000 simulations × {years} years. Each run draws annual returns from a normal distribution around the scenario mean.")

n_sims    = 1_000
ages      = np.arange(current_age, plan_to_age + 1)
n_periods = len(ages) - 1

np.random.seed(42)

def run_simulation(mean_return_pct, vol_pct, n_sims, n_periods,
                   start_portfolio, gap_pre, gap_post,
                   years_pre_ss, inflation_pct):
    """Run Monte Carlo. Returns array (n_sims, n_periods+1) of portfolio values."""
    results = np.zeros((n_sims, n_periods + 1))
    results[:, 0] = start_portfolio
    mean_r = mean_return_pct / 100
    vol_r  = vol_pct / 100
    inf_r  = inflation_pct / 100

    for t in range(1, n_periods + 1):
        annual_returns = np.random.normal(mean_r, vol_r, n_sims)
        withdrawal = np.where(t <= years_pre_ss, gap_pre, gap_post)
        # Inflation-adjust withdrawal
        withdrawal_real = withdrawal * ((1 + inf_r) ** t)
        results[:, t] = results[:, t-1] * (1 + annual_returns) - withdrawal_real
        results[:, t] = np.maximum(results[:, t], 0)   # floor at zero

    return results

with st.spinner("Running 3,000 simulations..."):
    sims_base   = run_simulation(base_return,         return_volatility, n_sims, n_periods,
                                  investable_portfolio, gap_pre_ss, gap_post_ss,
                                  ss_years, inflation)
    sims_squeeze = run_simulation(long_squeeze_return, return_volatility, n_sims, n_periods,
                                  investable_portfolio, gap_pre_ss, gap_post_ss,
                                  ss_years, inflation)
    sims_bear   = run_simulation(bear_return,          return_volatility, n_sims, n_periods,
                                  investable_portfolio, gap_pre_ss, gap_post_ss,
                                  ss_years, inflation)

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
    height=380, margin=dict(t=40, b=20),
    legend=dict(orientation="h", yanchor="bottom", y=1.02),
)
st.plotly_chart(fig_surv, use_container_width=True)

# ── Portfolio value fan chart ──────────────────────────────────────────────
p10_b, p50_b, p90_b       = percentiles(sims_base)
p10_s, p50_s, p90_s       = percentiles(sims_squeeze)

fig_fan = go.Figure()
# Base case fan
fig_fan.add_trace(go.Scatter(x=np.concatenate([ages, ages[::-1]]),
                              y=np.concatenate([p90_b, p10_b[::-1]]) / 1e6,
                              fill='toself', fillcolor='rgba(46,204,113,0.15)',
                              line=dict(color='rgba(0,0,0,0)'), name='Base 10th–90th %ile', showlegend=True))
fig_fan.add_trace(go.Scatter(x=ages, y=p50_b / 1e6, name=f"Base median ({base_return:.1f}%)",
                              line=dict(color="#2ecc71", width=2)))
# Long Squeeze fan
fig_fan.add_trace(go.Scatter(x=np.concatenate([ages, ages[::-1]]),
                              y=np.concatenate([p90_s, p10_s[::-1]]) / 1e6,
                              fill='toself', fillcolor='rgba(243,156,18,0.15)',
                              line=dict(color='rgba(0,0,0,0)'), name='Long Squeeze 10th–90th %ile', showlegend=True))
fig_fan.add_trace(go.Scatter(x=ages, y=p50_s / 1e6, name=f"Long Squeeze median ({long_squeeze_return:.1f}%)",
                              line=dict(color="#f39c12", width=2)))
fig_fan.add_hline(y=0, line_color="#e74c3c", line_width=2, annotation_text="Ruin")
fig_fan.add_vline(x=ss_start_age, line_dash="dash", line_color="#3498db")
fig_fan.update_layout(
    title="Portfolio Value Fan Chart ($ Millions)",
    xaxis_title="Age", yaxis_title="Portfolio Value ($M)",
    height=380, margin=dict(t=40, b=20),
    legend=dict(orientation="h", yanchor="bottom", y=1.02),
)
st.plotly_chart(fig_fan, use_container_width=True)

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
    final_p50_squeeze = p50_s[-1] / 1e6
    st.metric(f"Median Portfolio at {plan_to_age}",
              f"${final_p50_base:.2f}M base / ${final_p50_squeeze:.2f}M squeeze")

st.divider()

# ─────────────────────────────────────────────
# SECTION 4: SEQUENCE OF RETURNS RISK
# ─────────────────────────────────────────────
st.header("⚠️ Sequence of Returns Risk")
st.caption(f"Three paths drawn from the **same pool of returns** — identical average, different order. Best returns first vs worst returns first. The damage from a bad start is permanent because you're selling depressed assets to fund withdrawals.")

# ── Sequence of returns: construct paths with same AVERAGE but different ORDER ──
# The point: identical long-run average return, different sequence → different outcome.
# Method: generate one full-length random path, then construct a "reversed" version
# that has poor early returns and good late returns by reordering the same draws.
# This guarantees identical averages — only the sequence differs.
np.random.seed(99)
base_r = base_return / 100
vol_r  = return_volatility / 100
inf_r  = inflation / 100

# Generate one pool of returns with a realistic distribution
return_pool = np.random.normal(base_r, vol_r, years * 3)

# Good-start path: sort descending (best returns first)
good_first  = np.sort(return_pool[:years])[::-1]

# Bad-start path: sort ascending (worst returns first)
bad_first   = np.sort(return_pool[:years])

# Average path: randomly shuffled from same pool (same avg, random order)
shuffled    = return_pool[:years].copy()
np.random.shuffle(shuffled)
same_avg    = shuffled

def simulate_path(returns, start, gap_pre, gap_post, years_pre, inflation_rate):
    portfolio  = [start]
    for t, r in enumerate(returns, 1):
        draw = (gap_pre if t <= years_pre else gap_post) * ((1 + inflation_rate) ** t)
        val  = portfolio[-1] * (1 + r) - draw
        portfolio.append(max(val, 0))
    return portfolio

path_good = simulate_path(good_first, investable_portfolio, gap_pre_ss, gap_post_ss, ss_years, inf_r)
path_bad  = simulate_path(bad_first,  investable_portfolio, gap_pre_ss, gap_post_ss, ss_years, inf_r)
path_avg  = simulate_path(same_avg,   investable_portfolio, gap_pre_ss, gap_post_ss, ss_years, inf_r)

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
                   annotation_text="Critical window", annotation_position="top left")
fig_seq.add_vline(x=ss_start_age, line_dash="dash", line_color="#3498db",
                   annotation_text=f"SS starts", annotation_position="top right")
fig_seq.update_layout(
    title="Same Returns, Different Sequence — Permanently Different Outcomes",
    xaxis_title="Age", yaxis_title="Portfolio Value ($M)",
    height=360, margin=dict(t=40, b=20),
    legend=dict(orientation="h", yanchor="bottom", y=1.02),
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
        # Nominal: grows at Long Squeeze return, minus withdrawal gap
        draw      = (gap_pre_ss if t <= ss_years else gap_post_ss) * ((1 + inf_r) ** t)
        nom_new   = nominal_vals[-1] * (1 + long_squeeze_return / 100) - draw
        nominal_vals.append(max(nom_new, 0))
        # Real: deflate by inflation
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
        height=320, margin=dict(t=40, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
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
        height=320, margin=dict(t=40, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
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
    height=300, margin=dict(t=40, b=10),
    legend=dict(orientation="h", yanchor="bottom", y=1.02),
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
