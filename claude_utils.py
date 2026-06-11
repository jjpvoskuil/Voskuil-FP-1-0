"""
claude_utils.py — Anthropic API integration for Voskuil FP 1.0

Provides ask_claude_about_equity() with:
  - Dynamic user profile from Financial Modeler session state
  - Buffett + Munger combined philosophy system prompt
  - Context-aware scoring language referencing actual portfolio numbers
"""

import streamlit as st
import requests

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL         = "claude-sonnet-4-6"


def get_user_profile() -> dict:
    """
    Pull user profile from session state (set by Financial Modeler).
    Falls back to sensible defaults if Modeler hasn't been visited.
    """
    return {
        "age":                st.session_state.get("fp_age",                57),
        "plan_to_age":        st.session_state.get("fp_plan_to_age",        90),
        "spouse_age":         st.session_state.get("fp_spouse_age",         54),
        "portfolio_val":      st.session_state.get("fp_portfolio_val",      3_790_000),
        "monthly_withdrawal": st.session_state.get("fp_monthly_withdrawal", 8_000),
        "annual_passive":     st.session_state.get("fp_annual_passive",     96_000),
        "cash_buffer":        st.session_state.get("fp_cash_buffer",        96_000),
        "ss_monthly":         st.session_state.get("fp_ss_monthly",         3_200),
        "ss_start_age":       st.session_state.get("fp_ss_start_age",       67),
        "spouse_ss":          st.session_state.get("fp_spouse_ss",          2_200),
        "inflation":          st.session_state.get("fp_inflation",          4.0),
        "base_return":        st.session_state.get("fp_base_return",        6.0),
        "pessimistic_return": st.session_state.get("fp_pessimistic_return", 3.5),
        "bear_return":        st.session_state.get("fp_bear_return",        1.0),
        "survivor_monthly":   st.session_state.get("fp_survivor_monthly",   5_500),
    }


def build_system_prompt(profile: dict) -> str:
    """
    Build the full Buffett + Munger system prompt with dynamic user profile.
    """
    age               = profile["age"]
    plan_to_age       = profile["plan_to_age"]
    portfolio_val     = profile["portfolio_val"]
    monthly_wd        = profile["monthly_withdrawal"]
    annual_passive    = profile["annual_passive"]
    cash_buffer       = profile["cash_buffer"]
    ss_monthly        = profile["ss_monthly"]
    ss_start_age      = profile["ss_start_age"]
    inflation         = profile["inflation"]
    base_return       = profile["base_return"]
    ls_return         = profile.get("pessimistic_return", profile.get("long_squeeze_return", 3.5))
    years_to_plan     = plan_to_age - age
    annual_wd         = monthly_wd * 12
    passive_coverage  = (annual_passive / annual_wd * 100) if annual_wd > 0 else 0
    ss_annual         = ss_monthly * 12
    portfolio_m       = portfolio_val / 1_000_000

    return f"""You are a senior investment analyst embedded inside Voskuil FP 1.0, \
a personal financial dashboard built for a concentrated value investor.

═══════════════════════════════════════════════════════
INVESTOR PROFILE (from Financial Modeler)
═══════════════════════════════════════════════════════
Age: {age} | Planning horizon: to age {plan_to_age} ({years_to_plan} years)
Portfolio: ${portfolio_m:.2f}M | Monthly withdrawal target: ${monthly_wd:,.0f}
Annual passive income: ${annual_passive:,.0f} ({passive_coverage:.0f}% of withdrawal target)
Cash buffer: ${cash_buffer:,.0f} | SS starts age {ss_start_age}: ${ss_monthly:,.0f}/mo (${ss_annual:,.0f}/yr)
Return assumptions: Base {base_return:.1f}% | Pessimistic {ls_return:.1f}% | Inflation {inflation:.1f}%

PRIMARY CONCERN: Permanent capital loss — not underperformance. A concentrated \
holder at this life stage cannot recover from a permanent 40-50% loss the way \
a 35-year-old can. Every analysis must be filtered through this lens first.

═══════════════════════════════════════════════════════
INVESTMENT PHILOSOPHY — BUFFETT + MUNGER COMBINED
═══════════════════════════════════════════════════════

WARREN BUFFETT PRINCIPLES:
1. Circle of competence — only businesses you can genuinely understand 10 years out
2. Economic moat — durable competitive advantage that compounds over time
3. Owner earnings test — Buffett's metric: net income + D&A - maintenance capex
4. Price/Owner Earnings under 15x = bargain; over 35x = speculative
5. FCF yield — real cash return to the owner, not accounting earnings
6. Fortress balance sheet — debt that can be serviced in any credit environment
7. Management integrity — would you trust them with your family's money?
8. "Never a forced seller" — the portfolio must survive a 3-5 year drawdown \
   without requiring asset sales at depressed prices

CHARLIE MUNGER PRINCIPLES:
1. Inversion — always ask "what would have to go wrong for this to destroy value?" \
   before asking what could go right. The absence of catastrophic risk is more \
   important than the presence of upside.
2. Lollapalooza effect — look for businesses where multiple independent advantages \
   reinforce each other (moat + pricing power + switching costs + network effects)
3. Circle of competence boundary — explicitly flag when a business model is \
   genuinely hard to understand. Complexity that can't be explained simply is a risk.
4. Psychological factors — what management incentives exist? Are they aligned with \
   long-term owners or short-term performance metrics?
5. Moat durability test — is the competitive advantage structural (network effects, \
   switching costs, regulatory capture, brand) or merely operational (efficiency, \
   execution)? Operational advantages erode; structural ones compound.
6. Cannibalization signal — does this company take share even in bad markets? \
   Market share gain during downturns is the strongest moat signal.
7. Margin of safety — Munger: "I have nothing to add to Buffett's margin of safety \
   concept except to say that it's the most important thing in investing."
8. Avoid complexity — businesses with simple, understandable economics that \
   produce predictable cash flows are worth more than complex businesses \
   with theoretically higher returns.

═══════════════════════════════════════════════════════
MACRO RESILIENCE — PESSIMISTIC SCENARIO FILTER
═══════════════════════════════════════════════════════
Every holding is evaluated not just on upside potential but on downside survival.
The pessimistic scenario ({ls_return:.1f}% return, {inflation:.1f}% inflation) assumes:
- Below-average nominal returns with elevated inflation
- Credit conditions that tighten and punish overleveraged businesses
- Companies without real pricing power losing margin to inflation
- Passive index concentration creating valuation risk across the market

Portfolio filter: every holding must be able to survive a sustained difficult 
environment without becoming a forced sale. Buffett: "Only when the tide goes 
out do you discover who has been swimming naked."

═══════════════════════════════════════════════════════
OWNER'S FRAMEWORK — 6-METRIC SCORING
═══════════════════════════════════════════════════════
1. FCF Yield (20 pts default) — real cash return vs price
2. ROIC (10 pts) — management capital deployment quality
3. Debt/FCF (20 pts) — survival metric in a credit crunch
4. Gross Margin (15 pts) — pricing power and moat durability
5. Interest Coverage (10 pts) — debt service safety
6. Price/Owner Earnings (25 pts) — Buffett valuation test

Scoring thresholds:
- FCF Yield: ≥6% = excellent | ≥4% = good | >0% = weak
- ROIC: ≥20% = exceptional | ≥12% = strong | >0% = below average
- Debt/FCF: <3x = fortress | <5x = manageable | ≥5x = overleveraged
- Gross Margin: ≥60% = wide moat | ≥40% = solid moat | <40% = commodity risk
- P/Owner Earnings: ≤15x = bargain | ≤25x = fair | ≤35x = stretched | >35x = expensive

═══════════════════════════════════════════════════════
YOUR ROLE & ANALYTICAL STANDARDS
═══════════════════════════════════════════════════════
- Apply BOTH Buffett and Munger lenses — quantitative AND qualitative
- When filing text is available: cite specific language from the document
- When reasoning qualitatively without filing text: say so explicitly
- Apply Munger's inversion: lead with what could permanently destroy value
- Flag circle of competence concerns honestly
- Reference the investor's actual numbers where relevant: \
  "A ${monthly_wd:,.0f}/month draw on a ${portfolio_m:.1f}M portfolio \
  requires X% annual return just to sustain principal"
- Be direct and opinionated — conviction-level analysis, not hedged boilerplate
- End every major analysis with a bottom line: Buy / Watch / Avoid and why

FORMAT: Clear prose with specific observations. Lead with the most important \
risk or opportunity. Flag contradictions between quantitative score and \
qualitative filing evidence."""


def build_context(ticker: str, data: dict, scores: dict, sections: dict,
                  profile: dict = None) -> str:
    """Build the full context string passed as the first user message."""

    if profile is None:
        profile = get_user_profile()

    def fmt(val, kind="pct"):
        if val is None: return "N/A"
        if kind == "pct":   return f"{val:.1%}"
        if kind == "ratio": return f"{val:.1f}x"
        if kind == "money":
            return f"${val/1e9:.2f}B" if abs(val) >= 1e9 else f"${val/1e6:.1f}M"
        return str(val)

    # Context-aware income contribution
    monthly_wd = profile.get("monthly_withdrawal", 8000)
    portfolio  = profile.get("portfolio_val", 3_790_000)
    price      = data.get("price") or 0
    mkt_cap    = data.get("market_cap") or 0
    fcf_yield  = data.get("fcf_yield")

    quant_block = f"""
QUANTITATIVE SNAPSHOT — {ticker} ({data.get('name', ticker)})
Sector: {data.get('sector', 'N/A')}
Price: ${price:,.2f}  |  Market Cap: {fmt(mkt_cap, 'money')}

Owner's Framework Score: {scores.get('rebalanced', 'N/A')}/100  ({scores.get('verdict', 'N/A')})

Metric Breakdown:
  FCF Yield:            {fmt(fcf_yield)}
  ROIC:                 {fmt(data.get('roic'))}
  Debt/FCF:             {fmt(data.get('debt_to_fcf'), 'ratio')}
  Gross Margin:         {fmt(data.get('gross_margin'))}
  Interest Coverage:    {"Net Creditor" if data.get('is_net_creditor') else fmt(data.get('interest_coverage'), 'ratio')}
  Price/Owner Earnings: {fmt(data.get('price_owner_earn'), 'ratio')}
  FCF (TTM):            {fmt(data.get('fcf'), 'money')}
  Owner Earnings:       {fmt(data.get('owner_earnings'), 'money')}
  FCF Growth (1yr):     {fmt(data.get('fcf_growth'))}
  Dividend Yield:       {fmt(data.get('dividend_yield'))}
  Long-Term Debt:       {fmt(data.get('long_term_debt'), 'money')}
"""

    # Add income context if we have enough data
    if fcf_yield and portfolio and monthly_wd:
        position_pct   = 0.10  # assume 10% position as reference
        position_size  = portfolio * position_pct
        annual_fcf_inc = position_size * fcf_yield
        pct_of_target  = (annual_fcf_inc / 12) / monthly_wd * 100
        quant_block += f"""
Income Context (10% position = ${position_size:,.0f}):
  Estimated annual FCF income: ${annual_fcf_inc:,.0f}
  Monthly FCF equivalent:      ${annual_fcf_inc/12:,.0f} ({pct_of_target:.0f}% of ${monthly_wd:,.0f}/mo target)
"""

    filing_block = "\nSEC 10-K FILING EXCERPTS (most recent annual report):\n"
    section_labels = {
        "business":     "ITEM 1 — BUSINESS",
        "risk_factors": "ITEM 1A — RISK FACTORS",
        "mda":          "ITEM 7 — MANAGEMENT'S DISCUSSION & ANALYSIS",
        "quantitative": "ITEM 7A — QUANTITATIVE DISCLOSURES",
    }
    for key, label in section_labels.items():
        text = sections.get(key, "")
        if text:
            filing_block += f"\n--- {label} ---\n{text[:5000]}\n"
        else:
            filing_block += f"\n--- {label} ---\n[Section not available]\n"

    return quant_block + filing_block


def ask_claude_about_equity(
    ticker: str,
    data: dict,
    scores: dict,
    sections: dict,
    user_question: str,
    conversation_history: list = None,
    profile: dict = None,
) -> str:
    """
    Call Claude Sonnet with dynamic user profile + Buffett/Munger system prompt.
    Handles both first-turn (full context) and multi-turn (history) conversations.
    """
    try:
        api_key = st.secrets.get("ANTHROPIC_KEY") or st.secrets.get("ANTHROPIC_API_KEY")
        if not api_key:
            return "⚠️ ANTHROPIC_KEY not found in Streamlit secrets."

        if profile is None:
            profile = get_user_profile()

        system = build_system_prompt(profile)

        if not conversation_history:
            messages = [{
                "role":    "user",
                "content": f"{user_question}"
            }]
        else:
            messages = conversation_history + [{
                "role":    "user",
                "content": user_question
            }]

        headers = {
            "x-api-key":         api_key,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        }
        payload = {
            "model":      MODEL,
            "max_tokens": 4000,
            "system":     system,
            "messages":   messages,
        }

        resp = requests.post(ANTHROPIC_URL, headers=headers, json=payload, timeout=120)

        if resp.status_code == 200:
            return resp.json()["content"][0]["text"]
        elif resp.status_code == 401:
            return "⚠️ Invalid Anthropic API key. Check ANTHROPIC_KEY in Streamlit secrets."
        elif resp.status_code == 429:
            return "⚠️ Anthropic rate limit hit. Wait a moment and try again."
        else:
            return f"⚠️ Anthropic API error {resp.status_code}: {resp.text[:200]}"

    except requests.Timeout:
        return "⚠️ Request timed out. Please try again."
    except Exception as e:
        return f"⚠️ Unexpected error: {e}"
