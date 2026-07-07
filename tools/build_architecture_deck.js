/**
 * tools/build_architecture_deck.js
 *
 * Generates Voskuil_FP_Architecture.pptx — a real, PowerPoint-editable
 * architecture deck (native shapes/connectors/tables, not an inserted
 * image), companion to ARCHITECTURE.md and the in-app Architecture tab
 * on the Punch List page.
 *
 * To regenerate after an architecture change, ask Claude, or run directly:
 *   npm install -g pptxgenjs   (one-time, if not already installed)
 *   node tools/build_architecture_deck.js
 *   python3 /mnt/skills/public/pptx/scripts/rezip.py Voskuil_FP_Architecture.pptx
 *
 * If you edit the .pptx directly in PowerPoint and want those changes
 * reflected back into the "official" record (ARCHITECTURE.md, this
 * script), re-upload the edited file in a chat with Claude — it'll read
 * it and reconcile.
 */
const pptxgen = require("pptxgenjs");

let pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.3" x 7.5"
pres.author = "Claude + John Voskuil";
pres.title = "Voskuil FP 1.0 — Architecture";

// ── Palette: Midnight Executive ──────────────────────────────────────────
const NAVY   = "1E2761";
const ICE    = "CADCFC";
const WHITE  = "FFFFFF";
const SLATE  = "475569";
const LTGRAY = "F1F5F9";
const LINEGY = "94A3B8";
const GREEN  = "16A34A";
const AMBER  = "D97706";

const HEAD_FONT = "Cambria";
const BODY_FONT = "Calibri";

const W = 13.33, H = 7.5;
const MARGIN = 0.5;

// ── Helpers ───────────────────────────────────────────────────────────────
function addSlideNumber(slide, n) {
  slide.addText(`${n}`, {
    x: W - 0.55, y: H - 0.35, w: 0.3, h: 0.25,
    fontFace: BODY_FONT, fontSize: 10, color: LINEGY, align: "right", margin: 0,
  });
}

function titleBar(slide, title, subtitle) {
  slide.addText(title, {
    x: MARGIN, y: 0.35, w: W - MARGIN * 2, h: 0.6,
    fontFace: HEAD_FONT, fontSize: 30, bold: true, color: NAVY, margin: 0,
  });
  if (subtitle) {
    slide.addText(subtitle, {
      x: MARGIN, y: 0.95, w: W - MARGIN * 2, h: 0.4,
      fontFace: BODY_FONT, fontSize: 14, italic: true, color: SLATE, margin: 0,
    });
  }
}

function box(slide, x, y, w, h, label, sublabel, opts = {}) {
  const fill = opts.fill || WHITE;
  const lineColor = opts.line || NAVY;
  const labelColor = opts.labelColor || NAVY;
  const subColor = opts.subColor || SLATE;
  const dashed = opts.dashed || false;
  slide.addShape(pres.shapes.ROUNDED_RECTANGLE, {
    x, y, w, h, rectRadius: 0.08,
    fill: { color: fill },
    line: { color: lineColor, width: 1.5, dashType: dashed ? "dash" : "solid" },
    shadow: opts.noShadow ? undefined : { type: "outer", color: "1E2761", blur: 4, offset: 2, angle: 90, opacity: 0.08 },
  });
  const textItems = [{ text: label, options: { bold: true, breakLine: !!sublabel, fontSize: opts.labelSize || 13 } }];
  if (sublabel) textItems.push({ text: sublabel, options: { fontSize: opts.subSize || 9.5, color: subColor } });
  slide.addText(textItems, {
    x, y, w, h, align: "center", valign: "middle",
    fontFace: BODY_FONT, color: labelColor, margin: 6,
  });
}

function arrow(slide, x1, y1, x2, y2, opts = {}) {
  slide.addShape(pres.shapes.LINE, {
    x: Math.min(x1, x2), y: Math.min(y1, y2),
    w: Math.abs(x2 - x1), h: Math.abs(y2 - y1),
    flipV: y2 < y1 ? false : undefined,
    line: {
      color: opts.color || LINEGY, width: opts.width || 1.5,
      endArrowType: "triangle", dashType: opts.dashed ? "dash" : "solid",
    },
  });
}

function sectionLabel(slide, x, y, w, text) {
  slide.addText(text, {
    x, y, w, h: 0.3, fontFace: HEAD_FONT, fontSize: 13, bold: true, color: NAVY, margin: 0,
  });
}

function bullets(slide, x, y, w, h, items, opts = {}) {
  slide.addText(
    items.map((t, i) => ({ text: t, options: { bullet: { indent: 14 }, breakLine: i < items.length - 1, fontSize: opts.fontSize || 12.5 } })),
    { x, y, w, h, fontFace: BODY_FONT, color: opts.color || "1E293B", valign: "top", margin: 2, lineSpacing: opts.lineSpacing || 17 }
  );
}

// ─────────────────────────────────────────────────────────────────────────
// SLIDE 1 — Title
// ─────────────────────────────────────────────────────────────────────────
{
  let s = pres.addSlide();
  s.background = { color: NAVY };
  s.addShape(pres.shapes.OVAL, { x: 10.3, y: -1.8, w: 5, h: 5, fill: { color: "2A3875" }, line: { type: "none" } });
  s.addShape(pres.shapes.OVAL, { x: -1.5, y: 5, w: 4, h: 4, fill: { color: "2A3875" }, line: { type: "none" } });

  s.addText("VOSKUIL FP 1.0", {
    x: 1, y: 2.55, w: 11.3, h: 1.1, fontFace: HEAD_FONT, fontSize: 54, bold: true, color: WHITE, margin: 0,
  });
  s.addText("System Architecture", {
    x: 1, y: 3.55, w: 11.3, h: 0.6, fontFace: BODY_FONT, fontSize: 22, color: ICE, margin: 0,
  });
  s.addText("A living reference for app structure, data flow, and where it's headed", {
    x: 1, y: 4.15, w: 10, h: 0.4, fontFace: BODY_FONT, fontSize: 13, italic: true, color: "9FB3E8", margin: 0,
  });
  s.addText("Last updated July 6, 2026  ·  Repo: jjpvoskuil/Voskuil-FP-1-0", {
    x: 1, y: 6.7, w: 10, h: 0.35, fontFace: BODY_FONT, fontSize: 11, color: "8494C4", margin: 0,
  });
}

// ─────────────────────────────────────────────────────────────────────────
// SLIDE 2 — Overview & Philosophy
// ─────────────────────────────────────────────────────────────────────────
{
  let s = pres.addSlide();
  titleBar(s, "What This Is", "Two parallel tracks, one scoring engine");

  box(s, MARGIN, 1.55, 5.9, 2.55, "Personal Use", "Portfolio dashboard, stock scoring, retirement modeling, and tax monitoring for a Morgan Stanley account.",
      { fill: LTGRAY, line: NAVY, labelSize: 16, subSize: 12.5 });
  box(s, 6.85, 1.55, 5.9, 2.55, "Commercial Product (Later)", "Same concentrated-value engine, packaged for middle-class investors priced out of institutional research. Target: 2027, after proving the approach personally.",
      { fill: LTGRAY, line: NAVY, labelSize: 16, subSize: 12.5 });

  sectionLabel(s, MARGIN, 4.5, 6, "Owner Profile & Philosophy");
  bullets(s, MARGIN, 4.9, 6.1, 2.2, [
    "Age 57, Buffett/Munger concentrated value approach — not diversification theater",
    "Primary home fully paid off — no mortgage obligations",
    "Goal: a handful of high-conviction positions via owner-earnings analysis, not P/E comparison shopping",
  ]);

  sectionLabel(s, 6.85, 4.5, 6, "Design Principle");
  box(s, 6.85, 4.9, 5.9, 2.0, "No macro market-timing overlay",
      "Every business is evaluated on its own fundamentals — moat, balance sheet, management — under a generic downside-survival stress test. Not a specific predicted economic scenario.",
      { fill: "FEF3C7", line: AMBER, labelColor: "78350F", subColor: "78350F", labelSize: 14, subSize: 11.5, noShadow: true });

  addSlideNumber(s, 2);
}

// ─────────────────────────────────────────────────────────────────────────
// SLIDE 3 — Tech Stack
// ─────────────────────────────────────────────────────────────────────────
{
  let s = pres.addSlide();
  titleBar(s, "Tech Stack", "What it's built on, and what changed");

  const rows = [
    [{ text: "Component", options: { bold: true, color: WHITE, fill: { color: NAVY } } },
     { text: "Choice", options: { bold: true, color: WHITE, fill: { color: NAVY } } },
     { text: "Notes", options: { bold: true, color: WHITE, fill: { color: NAVY } } }],
    ["Language", "Python", ""],
    ["UI Framework", "Streamlit", "Hosted on Streamlit Community Cloud"],
    ["Version Control", "GitHub", "jjpvoskuil/Voskuil-FP-1-0, main branch"],
    ["Primary financial data", "SEC EDGAR", "Free, direct from source — no third-party normalization"],
    ["Pricing data", "yfinance", "Live price, market cap, sector, dividend yield — EDGAR has none of this"],
    ["Portfolio data", "Morgan Stanley CSV", "Manual export → rename → push_files.py → GitHub"],
    [{ text: "Polygon.io", options: { color: "991B1B" } }, { text: "RETIRED", options: { bold: true, color: "991B1B" } },
     { text: "Fully removed — do not reintroduce as a data source", options: { color: "991B1B" } }],
  ];
  s.addTable(rows, {
    x: MARGIN, y: 1.6, w: W - MARGIN * 2, colW: [3.0, 2.6, 6.73],
    fontFace: BODY_FONT, fontSize: 12.5, color: "1E293B", valign: "middle",
    border: { pt: 0.75, color: "E2E8F0" }, autoPage: false,
    rowH: 0.55,
  });

  addSlideNumber(s, 3);
}

// ─────────────────────────────────────────────────────────────────────────
// SLIDE 4 — Data Flow Diagram (the centerpiece — real editable shapes)
// ─────────────────────────────────────────────────────────────────────────
{
  let s = pres.addSlide();
  titleBar(s, "Data Flow", "From raw sources to the four scoring pages");

  const topY = 1.55, topH = 0.85;
  // Row 1: sources
  box(s, 0.5, topY, 3.6, topH, "SEC EDGAR", "Financial statements — income, cash flow, balance sheet, history + latest", { fill: "EFF6FF", line: "3B82F6", labelColor: "1E3A8A", subColor: "3B5A8A", subSize: 9 });
  box(s, 4.4, topY, 3.6, topH, "yfinance", "Live price, market cap, sector, dividend yield", { fill: "EFF6FF", line: "3B82F6", labelColor: "1E3A8A", subColor: "3B5A8A", subSize: 9 });
  box(s, 9.2, topY, 3.6, topH, "Morgan Stanley CSV", "Manual export → GitHub", { fill: "F0FDF4", line: "22C55E", labelColor: "14532D", subColor: "3A6A3A", subSize: 9 });

  arrow(s, 2.3, topY + topH, 4.6, 2.75);
  arrow(s, 6.2, topY + topH, 6.2, 2.75);

  // Row 2: shared engine
  const engY = 2.75, engH = 0.85;
  box(s, 4.15, engY, 4.1, engH, "sec_utils.py", "Canonical scoring engine + DCF (shared by all 4 pages)", { fill: "FEF9C3", line: "CA8A04", labelColor: "713F12", subColor: "854D0E", subSize: 9.5 });

  arrow(s, 5.2, engY + engH, 1.85, 4.05);
  arrow(s, 5.7, engY + engH, 5.0, 4.05);
  arrow(s, 6.7, engY + engH, 8.2, 4.05);
  arrow(s, 7.2, engY + engH, 11.35, 4.05);
  // MS CSV -> Dashboard direct (bypasses sec_utils.py — routed around it, not through it)
  arrow(s, 11.5, topY + topH, 11.5, 3.85, { color: "86EFAC" });
  arrow(s, 11.5, 3.85, 1.9, 3.85, { color: "86EFAC" });
  arrow(s, 1.9, 3.85, 1.9, 4.05, { color: "86EFAC" });

  // Row 3: 4 consuming pages
  const pgY = 4.05, pgH = 0.85, pgW = 2.85;
  box(s, 0.5,  pgY, pgW, pgH, "🛡️ Dashboard", "Holdings score + agent", { fill: WHITE, line: "64748B", labelSize: 12.5, subSize: 9.5 });
  box(s, 3.75, pgY, pgW, pgH, "🔍 Equity Scout", "Single ticker + 10-K agent", { fill: WHITE, line: "64748B", labelSize: 12.5, subSize: 9.5 });
  box(s, 7.0,  pgY, pgW, pgH, "📡 Market Screener", "Broad scan, persisted cache", { fill: WHITE, line: "64748B", labelSize: 12.5, subSize: 9.5 });
  box(s, 10.25,pgY, pgW, pgH, "⚖️ Compare Stocks", "2-5 tickers + 10-K agent", { fill: WHITE, line: "64748B", labelSize: 12.5, subSize: 9.5 });

  // Only Market Screener actually writes to github_store.py today (scan cache).
  // Punch list has its own separate implementation (see Known Gaps in ARCHITECTURE.md).
  arrow(s, 8.4,  pgY + pgH, 4.9, 5.35, { color: "F0ABFC", dashed: true });

  // Persistence layer
  const persY = 5.35, persH = 0.75;
  box(s, 3.75, persY, 6.1, persH, "github_store.py", "Persistence layer — market screener scan cache + this architecture doc. Survives Streamlit Cloud reboots.",
      { fill: "FDF2F8", line: "DB2777", labelColor: "831843", subColor: "9D174D", subSize: 9, dashed: true, noShadow: true });

  sectionLabel(s, 0.5, 6.35, 4, "Supporting Pages (not part of scoring)");
  bullets(s, 0.5, 6.6, 11.4, 0.4, ["🏔️ Financial Modeler   🏦 MS Financial Modeler   ⬇️ Downloads   ✅ Punch List — see Page Map for detail"], { fontSize: 11 });

  addSlideNumber(s, 4);
}

// ─────────────────────────────────────────────────────────────────────────
// SLIDE 5 — Page Map
// ─────────────────────────────────────────────────────────────────────────
{
  let s = pres.addSlide();
  titleBar(s, "Page Map", "Registered via st.navigation() in app.py");

  const rows = [
    [{ text: "Page", options: { bold: true, color: WHITE, fill: { color: NAVY } } },
     { text: "File", options: { bold: true, color: WHITE, fill: { color: NAVY } } },
     { text: "Purpose", options: { bold: true, color: WHITE, fill: { color: NAVY } } }],
    ["🛡️ Dashboard", "0_Dashboard.py", "Portfolio overview, holdings scoring, hold/add/trim signals, Claude agent"],
    ["🔍 Equity Scout", "7_Equity_Scout_EDGAR.py", "Single-ticker deep dive — full scoring, DCF, historical trends, 10-K agent"],
    ["📡 Market Screener", "8_Market_Screener_EDGAR.py", "Broad-universe scan, persistent scan cache, quant-only agent"],
    ["⚖️ Compare Stocks", "9_Compare_Stocks_EDGAR.py", "Side-by-side (2-5 tickers), score breakdown, trend charts, 10-K agent"],
    ["🏔️ Financial Modeler", "3_Financial_Modeler.py", "Retirement / cash-flow modeling"],
    ["🏦 MS Financial Modeler", "4_MS_Financial_Modeler.py", "MS-holdings-specific modeling"],
    ["⬇️ Downloads", "5_Downloads.py", "Data export"],
    ["✅ Punch List", "6_Punch_List.py", "Dev roadmap tracker + Architecture tab (this deck's live counterpart)"],
  ];
  s.addTable(rows, {
    x: MARGIN, y: 1.55, w: W - MARGIN * 2, colW: [2.6, 3.3, 6.43],
    fontFace: BODY_FONT, fontSize: 11.5, color: "1E293B", valign: "middle",
    border: { pt: 0.75, color: "E2E8F0" }, autoPage: false,
    rowH: 0.53,
  });

  s.addText("Retired, still on disk, not in navigation: 1_Equity_Scout.py, 2_Market_Screener.py (original Polygon-based versions — kept for rollback reference, not reachable from the sidebar)",
    { x: MARGIN, y: 6.9, w: 11.6, h: 0.4, fontFace: BODY_FONT, fontSize: 10, italic: true, color: SLATE, margin: 0 });

  addSlideNumber(s, 5);
}

// ─────────────────────────────────────────────────────────────────────────
// Helper for the 4 deep-dive page slides
// ─────────────────────────────────────────────────────────────────────────
function pageDeepDive(slideNum, icon, name, subtitle, inputs, metrics, functionality, interconnections) {
  let s = pres.addSlide();
  titleBar(s, `${icon}  ${name}`, subtitle);

  sectionLabel(s, MARGIN, 1.55, 3.9, "Key Inputs");
  bullets(s, MARGIN, 1.9, 3.9, 2.3, inputs, { fontSize: 11.5, lineSpacing: 15 });

  sectionLabel(s, 4.65, 1.55, 3.9, "Metrics & Calculations");
  bullets(s, 4.65, 1.9, 3.9, 2.3, metrics, { fontSize: 11.5, lineSpacing: 15 });

  sectionLabel(s, 8.8, 1.55, 4.0, "Functionality");
  bullets(s, 8.8, 1.9, 4.0, 2.3, functionality, { fontSize: 11.5, lineSpacing: 15 });

  box(s, MARGIN, 4.45, W - MARGIN * 2, 1.85, "", "", { fill: LTGRAY, line: "CBD5E1", noShadow: true });
  sectionLabel(s, MARGIN + 0.25, 4.6, 5, "Interconnections");
  bullets(s, MARGIN + 0.25, 4.95, W - MARGIN * 2 - 0.5, 1.25, interconnections, { fontSize: 11.5, lineSpacing: 15 });

  addSlideNumber(s, slideNum);
  return s;
}

// SLIDE 6 — Dashboard
pageDeepDive(6, "🛡️", "Dashboard", "Portfolio overview and holdings scoring",
  [
    "Morgan Stanley CSV exports (holdings, realized G/L, transactions)",
    "Selected holding symbol (for detail drill-in)",
    "Scoring weights (shared committed_weights across pages)",
    "Hold/add/trim thresholds (ROIC, Debt/FCF, P/OE, FCF Yield floors)",
  ],
  [
    "Consolidates ~200 raw rows into unique symbols across accounts",
    "Scores every holding via the shared 5-criteria EDGAR engine",
    "Hold/Add/Trim verdict — separate from the scoring engine, uses ROIC + Debt/FCF for quality, P/OE + FCF Yield for value",
    "Superinvestor Conviction — how many of 82 tracked value investors hold each position",
  ],
  [
    "⚡ Score All Holdings button — batch-scores every unique symbol",
    "Sortable holdings table (value, score, symbol)",
    "Account-level breakdown for any selected holding",
    "🤖 Ask Claude — portfolio-wide questions, quant only",
  ],
  [
    "Reads: sec_utils.py (fetch_fundamentals_edgar, score_stock)",
    "Links to: Equity Scout (Deep Dive button per holding)",
    "Shares committed_weights session state with every other scoring page",
  ]
);

// SLIDE 7 — Equity Scout
pageDeepDive(7, "🔍", "Equity Scout", "Single-ticker deep dive",
  [
    "Ticker symbol (manual entry or ?ticker=XXX&auto=1 from Dashboard)",
    "Scoring weights (own committed_weights, same shared schema)",
    "DCF assumptions — discount rate, terminal growth, projection years",
  ],
  [
    "Full 5-criteria score + rebalanced total (see Scoring Engine slide)",
    "DCF Intrinsic Value per share + Margin of Safety, shown under live price",
    "Historical trend charts — FCF, ROIC, Gross Margin, Debt/FCF, Interest Coverage, Owner Earnings (all derived year-by-year from raw EDGAR history)",
    "Full financial statements + raw XBRL concepts (transparency panel)",
  ],
  [
    "🏛️ EDGAR Raw Data expander — what's driving the score",
    "📋 Full Financial Statements expander",
    "📈 Historical trend charts",
    "🤖 Ask Claude — fetches actual 10-K filing text (business, risk factors, MD&A)",
  ],
  [
    "Reads: sec_utils.py (fetch_fundamentals_edgar, score_stock_breakdown, compute_dcf_value, fetch_10k_sections)",
    "Entry point from: Dashboard's Deep Dive button (dive_ticker session handoff)",
  ]
);

// SLIDE 8 — Market Screener
pageDeepDive(8, "📡", "Market Screener", "Broad-universe scan",
  [
    "Universe choice — S&P 500 (~500) or All US Common Stocks (~7,000+, Nasdaq Trader Symbol Directory)",
    "Scan ALL toggle (default on) vs. a seeded random sample",
    "Sector / Industry / Sub-Industry filters (GICS + SIC codes)",
    "Scoring weights",
  ],
  [
    "Stage 1: quality scan — 55% quality floor on the price-independent 65 points before a price lookup",
    "Stage 2: pricing + full score for Stage 1 survivors",
    "Net Creditor detection — full Interest Coverage points if a company earns more interest than it pays",
    "Financial firm / Cyclical firm flags (SIC-code based)",
  ],
  [
    "🔁 Re-apply Filters — re-price + re-filter the cached Stage 1 pool, no EDGAR re-fetch",
    "⚡ Re-score with New Weights — arithmetic only, near-instant",
    "Persistent scan cache (github_store.py) — survives reboots, shows last-scan date",
    "⚖️ Compare Top 3 / Compare Selected — send tickers to Compare Stocks",
  ],
  [
    "Reads: sec_utils.py (canonical scoring, imported not duplicated)",
    "Writes: market_screener_scan_cache.json via github_store.py",
    "Sends tickers to: Compare Stocks EDGAR (session state handoff)",
  ]
);

// SLIDE 9 — Compare Stocks
pageDeepDive(9, "⚖️", "Compare Stocks", "Side-by-side comparison, 2-5 tickers",
  [
    "compare_tickers (2-5 tickers, from Market Screener's Compare buttons)",
    "compare_weights (snapshot of weights at time of selection)",
    "DCF assumptions (page-level expander, same as Equity Scout)",
  ],
  [
    "Score breakdown per criterion, side by side across all compared tickers",
    "Full financial statements by section (Income Statement, Cash Flow, Balance Sheet, Derived Metrics)",
    "DCF Intrinsic Value + Margin of Safety per ticker",
    "Historical trend — any line item, all compared tickers on one combined chart",
  ],
  [
    "📈 Click-to-chart — pick any line item, see a combined historical chart",
    "🤖 Ask Claude — 10-K-aware, scoped to just the compared tickers, fetched lazily on first question",
    "Auto-resets conversation when the comparison set changes",
  ],
  [
    "Reads: sec_utils.py (fetch_fundamentals_edgar, score_stock_breakdown, compute_dcf_value, fetch_filings_parallel)",
    "Entry point from: Market Screener's Compare Top 3 / Compare Selected buttons",
  ]
);

// ─────────────────────────────────────────────────────────────────────────
// SLIDE 10 — Scoring Engine
// ─────────────────────────────────────────────────────────────────────────
{
  let s = pres.addSlide();
  titleBar(s, "Scoring Engine", "sec_utils.py — the single source of truth");

  const rows = [
    [{ text: "Criterion", options: { bold: true, color: WHITE, fill: { color: NAVY } } },
     { text: "Default Weight", options: { bold: true, color: WHITE, fill: { color: NAVY } } },
     { text: "What It Measures", options: { bold: true, color: WHITE, fill: { color: NAVY } } }],
    ["FCF Yield", "30", "Real owner earnings relative to price"],
    ["ROIC", "20", "Total Equity + Total Debt as invested capital (corrected from the old, wrong Total Assets − Current Liabilities formula)"],
    ["Debt / FCF", "25", "Balance sheet strength; Net Creditor detection gives full points to companies earning more interest than they pay"],
    ["Gross Margin", "15", "Pricing power / moat durability"],
    ["Interest Coverage", "10", "Ability to service debt, cash-basis preferred"],
  ];
  s.addTable(rows, {
    x: MARGIN, y: 1.55, w: 8.0, colW: [2.2, 1.5, 4.3],
    fontFace: BODY_FONT, fontSize: 11, color: "1E293B", valign: "middle",
    border: { pt: 0.75, color: "E2E8F0" }, autoPage: false,
    rowH: 0.62,
  });
  s.addText("Rebalanced to 100 across whatever criteria have data — a missing metric doesn't just lose points, remaining criteria are rescaled proportionally.",
    { x: MARGIN, y: 5.35, w: 8.0, h: 0.5, fontFace: BODY_FONT, fontSize: 10.5, italic: true, color: SLATE, margin: 0 });

  box(s, 8.85, 1.55, 3.98, 3.75, "DCF Intrinsic Value", "compute_dcf_value() — two-stage discounted cash flow. Growth rate from the company's own historical FCF trend (clipped to a sane range). Gordon Growth terminal value. Adjustable discount rate, terminal growth, and projection years.",
    { fill: "FEF9C3", line: "CA8A04", labelColor: "713F12", subColor: "854D0E", labelSize: 13, subSize: 10.5, noShadow: true });

  box(s, 8.85, 5.5, 3.98, 1.35, "Not Scored", "Price/Owner Earnings is shown as a reference valuation metric — used in Dashboard's hold/trim thresholds, but excluded from the composite score.",
    { fill: LTGRAY, line: "CBD5E1", labelColor: NAVY, subColor: SLATE, labelSize: 12, subSize: 10, noShadow: true });

  addSlideNumber(s, 10);
}

// ─────────────────────────────────────────────────────────────────────────
// SLIDE 11 — Persistence, Caching & Claude Agents
// ─────────────────────────────────────────────────────────────────────────
{
  let s = pres.addSlide();
  titleBar(s, "Persistence & Claude Agents", "What survives a reboot, and who knows what");

  sectionLabel(s, MARGIN, 1.55, 6, "Persistence (github_store.py)");
  bullets(s, MARGIN, 1.9, 6, 2.3, [
    "Streamlit Cloud wipes the container's filesystem on every reboot/redeploy",
    "Punch list — its own GitHub-backed implementation (predates github_store.py)",
    "Market Screener scan cache — Stage 1 survivor pool, so a multi-minute full-universe scan isn't repeated on every reboot",
    "Architecture doc (this content) — editable in-app, saved via github_get_text/put_text",
  ], { fontSize: 12 });

  sectionLabel(s, 7.0, 1.55, 5.8, "Claude Agent Scopes");
  const rows = [
    [{ text: "Page", options: { bold: true, color: WHITE, fill: { color: NAVY }, fontSize: 11 } },
     { text: "Scope", options: { bold: true, color: WHITE, fill: { color: NAVY }, fontSize: 11 } },
     { text: "10-K?", options: { bold: true, color: WHITE, fill: { color: NAVY }, fontSize: 11 } }],
    ["Dashboard", "Portfolio-wide", "No"],
    ["Equity Scout", "Single ticker", "Yes"],
    ["Market Screener", "Full screen (quant only)", "No"],
    ["Compare Stocks", "2-5 compared tickers", "Yes, lazy"],
  ];
  s.addTable(rows, {
    x: 7.0, y: 1.9, w: 5.8, colW: [1.8, 2.8, 1.2],
    fontFace: BODY_FONT, fontSize: 10.5, color: "1E293B", valign: "middle",
    border: { pt: 0.75, color: "E2E8F0" }, autoPage: false,
    rowH: 0.42,
  });

  box(s, MARGIN, 4.5, W - MARGIN * 2, 1.8, "Development Workflow",
    "Claude has direct git push access via a dedicated fine-grained GitHub PAT (separate from the app's own GITHUB_TOKEN secret). Session-start: paste the token, Claude configures the credentialed remote, verifies with a no-op fetch, then edits/commits/pushes directly — no copy-paste into the GitHub web editor.",
    { fill: LTGRAY, line: "CBD5E1", labelColor: NAVY, subColor: SLATE, labelSize: 14, subSize: 11.5, noShadow: true });

  addSlideNumber(s, 11);
}

// ─────────────────────────────────────────────────────────────────────────
// SLIDE 12 — Future Development Roadmap
// ─────────────────────────────────────────────────────────────────────────
{
  let s = pres.addSlide();
  titleBar(s, "Future Development Roadmap", "34 open items, from the live punch list");

  const cols = [
    { title: "Equity Scoring (8) — mostly High", items: [
      "Demote Gross Margin, restructure Debt/FCF",
      "Overhaul ROIC to 10-yr cash basis",
      "Fix interest coverage (cash-basis)",
      "Financial firm / cyclical firm flags",
    ]},
    { title: "Data Quality (12)", items: [
      "#63 Redo stock scoring metrics (scope still open)",
      "Bank of America integration (High)",
      "Historical score trending",
      "Smart ingestion phases 1-4",
    ]},
    { title: "Modeling & Fund Deep Dive (5)", items: [
      "Real-time tax monitoring",
      "Retirement modeler allocator + crisis overlays",
      "Strategy-matched discovery scan",
      "Separate ETF/fund deep-dive format",
    ]},
    { title: "Infrastructure & Later (9)", items: [
      "EDGAR historical ingestion layer",
      "Morningstar for foreign ADR scoring",
      "Watchlist persistence",
      "Multi-user + subscription (commercial track)",
    ]},
  ];

  const colW = 2.95, gap = 0.28, startX = MARGIN;
  cols.forEach((c, i) => {
    const x = startX + i * (colW + gap);
    box(s, x, 1.6, colW, 0.55, c.title, "", { fill: NAVY, labelColor: WHITE, labelSize: 11.5, noShadow: true });
    bullets(s, x, 2.3, colW, 3.6, c.items, { fontSize: 10.5, lineSpacing: 15 });
  });

  s.addText("#63 and the Equity Scoring items (#31-#39) are deliberately kept side by side — scope for the metrics overhaul isn't finalized, so this is a placeholder for \"needs work\" rather than a decision to reconcile them yet.",
    { x: MARGIN, y: 6.7, w: 11.8, h: 0.5, fontFace: BODY_FONT, fontSize: 10, italic: true, color: SLATE, margin: 0 });

  addSlideNumber(s, 12);
}

// ─────────────────────────────────────────────────────────────────────────
// SLIDE 13 — Keeping This Current
// ─────────────────────────────────────────────────────────────────────────
{
  let s = pres.addSlide();
  s.background = { color: NAVY };
  s.addText("Keeping This Current", {
    x: MARGIN, y: 0.6, w: W - MARGIN * 2, h: 0.7, fontFace: HEAD_FONT, fontSize: 30, bold: true, color: WHITE, margin: 0,
  });

  bullets(s, MARGIN, 1.7, 5.9, 4.5, [
    "ARCHITECTURE.md in the repo is the source of truth — same principle as the punch list",
    "The in-app Architecture tab (Punch List page) renders and edits it directly, saving straight to GitHub",
    "This deck is a snapshot generated from that content — regenerate it anytime by asking Claude",
    "Claude re-reads the repo each session rather than relying on Project Knowledge, which lags",
  ], { fontSize: 14, color: WHITE, lineSpacing: 24 });

  box(s, 7.2, 1.7, 5.6, 4.5, "If you edit this deck directly...",
    "Rearrange boxes, tweak wording, add slides — it's yours. If you want those changes folded back into the official architecture record, just re-upload the edited file in a chat with Claude; it'll read it and reconcile the content into ARCHITECTURE.md.",
    { fill: "2A3875", line: ICE, labelColor: WHITE, subColor: ICE, labelSize: 15, subSize: 12.5, noShadow: true });

  addSlideNumber(s, 13);
}

pres.writeFile({ fileName: "Voskuil_FP_Architecture.pptx" }).then(() => console.log("done"));
