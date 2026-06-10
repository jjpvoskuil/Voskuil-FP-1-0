"""
claude_utils.py — Anthropic API integration for Voskuil FP 1.0

Provides ask_claude_about_equity():
  - Builds a rich system prompt with Owner's Framework philosophy
  - Injects quantitative scores + SEC filing sections as context
  - Returns Claude's analysis as a string

Requires ANTHROPIC_KEY in st.secrets.
Model: claude-opus-4-6 (deep reasoning for filing analysis)
"""

import streamlit as st
import requests
import json

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL         = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a senior investment analyst embedded inside Voskuil FP 1.0, 
a personal financial dashboard built for a 57-year-old investor following a 
Buffett/Munger-style concentrated value philosophy.

INVESTMENT PHILOSOPHY:
- Concentrated positions in high-conviction, fortress-balance-sheet businesses
- "Long Squeeze" macro overlay: financial repression environment where passive 
  index investors face risk from bubble valuations; favor companies with real 
  FCF, pricing power, and low debt that can survive a credit cycle turn
- Target: $8,000/month in retirement income — portfolio must be recession-resistant, 
  not just return-maximizing
- Hold horizon: 5-10 years minimum; permanent capital loss is the primary risk to avoid

OWNER'S FRAMEWORK (6-metric scoring system):
1. FCF Yield (20 pts default) — real cash earnings relative to price
2. ROIC (10 pts) — management capital deployment quality
3. Debt/FCF (20 pts) — survival metric in a credit crunch
4. Gross Margin (15 pts) — pricing power and moat durability  
5. Interest Coverage (10 pts) — debt service safety
6. Price/Owner Earnings (25 pts) — Buffett valuation test (under 15x = bargain)

YOUR ROLE:
- Analyze the SEC 10-K filing sections provided alongside the quantitative scores
- Flag qualitative risks or strengths that the numbers alone don't capture
- Specifically look for: management candor, capital allocation language, 
  competitive moat evidence, debt covenant risks, and anything that would 
  concern a long-term concentrated holder
- Reference specific language from the filing when relevant
- Be direct and opinionated — this investor wants conviction-level analysis, 
  not hedged boilerplate
- Keep the Long Squeeze thesis in mind: how does this company perform if 
  credit tightens and passive money rotates out?

FORMAT: Clear prose with specific observations. Lead with the most important 
insight. Flag any contradictions between the quantitative score and what the 
filing actually says. End with a one-line bottom line."""


def build_context(ticker: str, data: dict, scores: dict, sections: dict) -> str:
    """Build the full context string to send to Claude."""

    def fmt(val, kind="pct"):
        if val is None:
            return "N/A"
        if kind == "pct":
            return f"{val:.1%}"
        if kind == "ratio":
            return f"{val:.1f}x"
        if kind == "money":
            return f"${val/1e9:.2f}B" if abs(val) >= 1e9 else f"${val/1e6:.1f}M"
        return str(val)

    quant_block = f"""
QUANTITATIVE SNAPSHOT — {ticker} ({data.get('name', ticker)})
Sector: {data.get('sector', 'N/A')}
Price: ${data.get('price', 0) or 0:,.2f}  |  Market Cap: {fmt(data.get('market_cap'), 'money')}

Owner's Framework Score: {scores.get('rebalanced', 'N/A')}/100  ({scores.get('verdict', 'N/A')})

Metric Breakdown:
  FCF Yield:            {fmt(data.get('fcf_yield'))}
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
            filing_block += f"\n--- {label} ---\n{text[:6000]}\n"
        else:
            filing_block += f"\n--- {label} ---\n[Section not found in filing]\n"

    return quant_block + filing_block


def ask_claude_about_equity(
    ticker: str,
    data: dict,
    scores: dict,
    sections: dict,
    user_question: str,
    conversation_history: list | None = None,
) -> str:
    """
    Call Claude Opus with quantitative data + 10-K sections + user question.
    
    conversation_history: list of {role, content} dicts for multi-turn Q&A.
    Returns the assistant's response text, or an error string.
    """
    try:
        api_key = st.secrets.get("ANTHROPIC_KEY") or st.secrets.get("ANTHROPIC_API_KEY")
        if not api_key:
            return "⚠️ ANTHROPIC_KEY not found in Streamlit secrets. Add it to enable Claude analysis."

        context = build_context(ticker, data, scores, sections)

        # Build messages — context is injected once as first user turn
        if not conversation_history:
            messages = [
                {
                    "role": "user",
                    "content": f"{context}\n\n---\nQUESTION: {user_question}"
                }
            ]
        else:
            # Multi-turn: context was already in the first message
            messages = conversation_history + [
                {"role": "user", "content": user_question}
            ]

        headers = {
            "x-api-key":         api_key,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        }

        payload = {
            "model":      MODEL,
            "max_tokens": 1500,
            "system":     SYSTEM_PROMPT,
            "messages":   messages,
        }

        resp = requests.post(ANTHROPIC_URL, headers=headers, json=payload, timeout=60)

        if resp.status_code == 200:
            result = resp.json()
            return result["content"][0]["text"]
        elif resp.status_code == 401:
            return "⚠️ Invalid Anthropic API key. Check ANTHROPIC_KEY in Streamlit secrets."
        elif resp.status_code == 429:
            return "⚠️ Anthropic rate limit hit. Wait a moment and try again."
        else:
            return f"⚠️ Anthropic API error {resp.status_code}: {resp.text[:200]}"

    except requests.Timeout:
        return "⚠️ Request timed out — the filing analysis can take up to 60 seconds. Please try again."
    except Exception as e:
        return f"⚠️ Unexpected error: {e}"
