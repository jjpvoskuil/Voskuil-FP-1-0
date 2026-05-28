import streamlit as st
import json
import os
from datetime import datetime

st.set_page_config(page_title="Punch List | Voskuil FP", layout="wide")

# ─────────────────────────────────────────────
# STYLING
# ─────────────────────────────────────────────
st.markdown("""
<style>
/* Card styling */
.punch-card {
    background: #1a1a2e;
    border: 1px solid #2d2d44;
    border-radius: 8px;
    padding: 14px 16px;
    margin-bottom: 8px;
    transition: border-color 0.2s;
}
.punch-card:hover { border-color: #4a4a6a; }
.punch-card.done {
    background: #0f1a0f;
    border-color: #1a3a1a;
    opacity: 0.6;
}

/* Priority badges */
.badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 12px;
    font-size: 0.72em;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    margin-left: 8px;
    vertical-align: middle;
}
.badge-urgent  { background: #4a0000; color: #ff6b6b; border: 1px solid #ff6b6b; }
.badge-high    { background: #3a2000; color: #ffa64d; border: 1px solid #ffa64d; }
.badge-medium  { background: #1a2a3a; color: #4db8ff; border: 1px solid #4db8ff; }
.badge-low     { background: #1a1a1a; color: #888;    border: 1px solid #444; }
.badge-done    { background: #0f1a0f; color: #4dff88; border: 1px solid #4dff88; }

/* Phase header */
.phase-header {
    font-size: 0.75em;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #666;
    padding: 12px 0 6px 0;
    border-bottom: 1px solid #2d2d44;
    margin-bottom: 10px;
}

/* Stats bar */
.stats-bar {
    display: flex; gap: 16px; margin-bottom: 20px;
    padding: 12px 16px;
    background: #12121e;
    border-radius: 8px;
    border: 1px solid #2d2d44;
}
.stat-item { text-align: center; }
.stat-num  { font-size: 1.6em; font-weight: 700; color: #fff; line-height: 1; }
.stat-lbl  { font-size: 0.72em; color: #666; text-transform: uppercase; letter-spacing: 0.08em; }

/* Title styling */
.item-title { font-size: 0.95em; font-weight: 600; color: #e0e0f0; }
.item-title.done-title { text-decoration: line-through; color: #4dff88; }
.item-note  { font-size: 0.78em; color: #888; margin-top: 4px; line-height: 1.5; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# DATA LAYER — persists to PUNCH_LIST.json
# ─────────────────────────────────────────────
DATA_FILE = "punch_list_data.json"

PHASES = [
    "Immediate / Near-Term",
    "Phase 4 — Deeper Analysis",
    "Phase 5 — Retirement Modeling",
    "Data Quality",
    "API / Infrastructure",
    "Commercial Product Track",
    "Done",
]

PRIORITIES = ["Urgent", "High", "Medium", "Low"]

DEFAULT_ITEMS = [
    # Immediate
    {"id": 1,  "title": "Watchlist — save top screener candidates",
     "note": "Save tickers from Market Screener for later review. Needs session-state persistence or a lightweight store.",
     "phase": "Immediate / Near-Term", "priority": "Urgent", "done": False, "created": "2026-05-01"},
    {"id": 2,  "title": "Button styling refinements in Holdings Explorer",
     "note": "CSS targeting via st.markdown is fragile. Revisit approach for SEC/Yahoo/Deep Dive buttons.",
     "phase": "Immediate / Near-Term", "priority": "High", "done": False, "created": "2026-05-01"},
    {"id": 3,  "title": "Sync scoring weights across all three pages",
     "note": "app.py, equity_scout.py, market_screener.py each have independent sliders. Same ticker can score differently. Store active weights in session state and read on all pages.",
     "phase": "Immediate / Near-Term", "priority": "High", "done": False, "created": "2026-05-01"},
    # Phase 4
    {"id": 4,  "title": "SEC filing deep-dive links in Equity Scout",
     "note": "Add direct click-through to actual SEC EDGAR filings from the Equity Scout analysis page.",
     "phase": "Phase 4 — Deeper Analysis", "priority": "High", "done": False, "created": "2026-05-01"},
    {"id": 5,  "title": "Strategy-matched discovery scan",
     "note": "Scan for stocks matching specific criteria: Dividend Aristocrats, commodity ETFs, Long Squeeze survivors.",
     "phase": "Phase 4 — Deeper Analysis", "priority": "High", "done": False, "created": "2026-05-01"},
    # Phase 5
    {"id": 6,  "title": "Real-time tax monitoring (replace MS Parametric)",
     "note": "Daily cap gains / tax-loss harvesting scanner to replace what Morgan Stanley Parametric currently does.",
     "phase": "Phase 5 — Retirement Modeling", "priority": "Medium", "done": False, "created": "2026-05-01"},
    # Data Quality
    {"id": 7,  "title": "Separate maintenance vs growth capex",
     "note": "Currently using total investing CF as proxy — conservative but imprecise. Investigate Polygon v1 fields.",
     "phase": "Data Quality", "priority": "Medium", "done": False, "created": "2026-05-01"},
    {"id": 8,  "title": "D&A direct from financials (not proxy)",
     "note": "New Massive v1 API has depreciation_depletion_and_amortization directly from CF statement — wired in fetch_score_data but not yet in equity_scout fetch_fundamentals.",
     "phase": "Data Quality", "priority": "Medium", "done": False, "created": "2026-05-01"},
    {"id": 9,  "title": "Historical score trending for a ticker",
     "note": "How has conviction score changed over 3-5 years? Requires pulling multiple annual filings from Polygon.",
     "phase": "Data Quality", "priority": "Medium", "done": False, "created": "2026-05-01"},
    # API / Infrastructure
    {"id": 10, "title": "⚠️ Massive API migration — DEADLINE June 22 2026",
     "note": "vX endpoint sunsets June 22. v1 migration is built with vX fallback. Blocker: add Financials & Ratios Expansion ($29/mo) at massive.com dashboard. Once added, v1 activates automatically.",
     "phase": "API / Infrastructure", "priority": "Urgent", "done": False, "created": "2026-05-01"},
    {"id": 11, "title": "Replace yfinance foreign ADR scoring with Morningstar via Rapid API",
     "note": "ASML, ARGX etc. have no SEC filings. Current yfinance fallback works but unreliable. Morningstar ~$10/mo. Deferred until tool proves out on personal portfolio.",
     "phase": "API / Infrastructure", "priority": "Medium", "done": False, "created": "2026-05-01"},
    {"id": 12, "title": "Replace yfinance ETF dist_yield with Polygon when field improves",
     "note": "Polygon distribution_yield field sparse for ETFs — rebalanced out currently. Picks up automatically when Polygon improves coverage.",
     "phase": "API / Infrastructure", "priority": "Low", "done": False, "created": "2026-05-01"},
    # Commercial
    {"id": 13, "title": "Activate Plaid live connection to Morgan Stanley",
     "note": "Full Plaid flow built (connect.py, plaid_data.py). Blocker: MS refusing connection despite third-party sharing enabled. Try: wait 24-48hrs, call MS 1-800-869-3326, toggle from mobile app. Financial planner contacted. Sandbox works: user_good/pass_good, PLAID_ENV=sandbox.",
     "phase": "Commercial Product Track", "priority": "Medium", "done": False, "created": "2026-05-01"},
    {"id": 14, "title": "Watchlist / portfolio tracker persistence across sessions",
     "note": "Saved tickers and notes persist between Streamlit sessions. Options: SQLite, Supabase, secrets-backed JSON.",
     "phase": "Commercial Product Track", "priority": "Low", "done": False, "created": "2026-05-01"},
    {"id": 15, "title": "Multi-user support infrastructure",
     "note": "Authentication, per-user portfolios, isolated sessions for commercial product launch.",
     "phase": "Commercial Product Track", "priority": "Low", "done": False, "created": "2026-05-01"},
    {"id": 16, "title": "Flat-fee subscription + onboarding flow",
     "note": "Payment infrastructure and new-user onboarding. Flat fee not AUM%. Target: middle-class investors.",
     "phase": "Commercial Product Track", "priority": "Low", "done": False, "created": "2026-05-01"},
]

def load_items():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    # First run — seed with defaults
    save_items(DEFAULT_ITEMS)
    return DEFAULT_ITEMS

def save_items(items):
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(items, f, indent=2)
    except Exception as e:
        st.error(f"Could not save: {e}")

def next_id(items):
    return max((i["id"] for i in items), default=0) + 1

# Load
if "punch_items" not in st.session_state:
    st.session_state.punch_items = load_items()

items = st.session_state.punch_items

# ─────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────
st.title("🗂️ Voskuil FP — Dev Punch List")
st.caption("Internal development tracker · Not visible in production · Remove this page before commercial launch")

# Stats bar
total   = len(items)
done    = sum(1 for i in items if i["done"])
open_   = total - done
urgent  = sum(1 for i in items if not i["done"] and i["priority"] == "Urgent")
high    = sum(1 for i in items if not i["done"] and i["priority"] == "High")

st.markdown(f"""
<div class="stats-bar">
  <div class="stat-item"><div class="stat-num">{open_}</div><div class="stat-lbl">Open</div></div>
  <div class="stat-item"><div class="stat-num" style="color:#ff6b6b">{urgent}</div><div class="stat-lbl">Urgent</div></div>
  <div class="stat-item"><div class="stat-num" style="color:#ffa64d">{high}</div><div class="stat-lbl">High</div></div>
  <div class="stat-item"><div class="stat-num" style="color:#4dff88">{done}</div><div class="stat-lbl">Done</div></div>
  <div class="stat-item"><div class="stat-num">{total}</div><div class="stat-lbl">Total</div></div>
</div>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# FILTERS + ADD FORM
# ─────────────────────────────────────────────
f_col1, f_col2, f_col3, add_col = st.columns([2, 2, 1, 1])
with f_col1:
    filter_phase = st.selectbox("Phase", ["All Phases"] + PHASES, label_visibility="collapsed")
with f_col2:
    filter_priority = st.selectbox("Priority", ["All Priorities"] + PRIORITIES + ["Done"], label_visibility="collapsed")
with f_col3:
    show_done = st.toggle("Show Done", value=False)
with add_col:
    add_clicked = st.button("➕ Add Item", type="primary", use_container_width=True)

# ── Add item form ──────────────────────────────────────────────────────────
if add_clicked:
    st.session_state.show_add_form = True

if st.session_state.get("show_add_form"):
    with st.container(border=True):
        st.markdown("**New Punch List Item**")
        a1, a2, a3 = st.columns([3, 1.5, 1.5])
        new_title    = a1.text_input("Title *", placeholder="What needs to be built?", key="new_title")
        new_phase    = a2.selectbox("Phase", PHASES, key="new_phase")
        new_priority = a3.selectbox("Priority", PRIORITIES, key="new_priority")
        new_note     = st.text_area("Context / Notes", placeholder="Approach, dependencies, constraints — enough for Claude to pick this up in a future session...", key="new_note", height=80)
        b1, b2, _ = st.columns([1, 1, 4])
        if b1.button("✅ Add", type="primary"):
            if new_title.strip():
                new_item = {
                    "id":       next_id(items),
                    "title":    new_title.strip(),
                    "note":     new_note.strip(),
                    "phase":    new_phase,
                    "priority": new_priority,
                    "done":     False,
                    "created":  datetime.today().strftime("%Y-%m-%d"),
                }
                items.append(new_item)
                save_items(items)
                st.session_state.show_add_form = False
                st.rerun()
            else:
                st.warning("Title is required.")
        if b2.button("Cancel"):
            st.session_state.show_add_form = False
            st.rerun()

st.divider()

# ─────────────────────────────────────────────
# ITEM LIST
# ─────────────────────────────────────────────
PRIORITY_COLORS = {
    "Urgent": ("#ff6b6b", "badge-urgent"),
    "High":   ("#ffa64d", "badge-high"),
    "Medium": ("#4db8ff", "badge-medium"),
    "Low":    ("#888",    "badge-low"),
}

# Filter
visible = [i for i in items if (
    (filter_phase    == "All Phases"     or i["phase"]    == filter_phase) and
    (filter_priority == "All Priorities" or
     (filter_priority == "Done" and i["done"]) or
     (filter_priority != "Done" and i["priority"] == filter_priority and not i["done"])) and
    (show_done or not i["done"])
)]

# Group by phase
phase_order = PHASES
grouped = {}
for phase in phase_order:
    group = [i for i in visible if i["phase"] == phase]
    if group:
        grouped[phase] = group

if not visible:
    st.info("No items match the current filters.")

for phase, phase_items in grouped.items():
    open_count = sum(1 for i in phase_items if not i["done"])
    done_count = sum(1 for i in phase_items if i["done"])
    count_str  = f"{open_count} open" + (f" · {done_count} done" if done_count else "")
    st.markdown(f'<div class="phase-header">{phase} &nbsp;·&nbsp; {count_str}</div>', unsafe_allow_html=True)

    for item in phase_items:
        pid     = item["id"]
        is_done = item["done"]
        color, badge_cls = PRIORITY_COLORS.get(item["priority"], ("#888", "badge-low"))
        title_cls = "done-title" if is_done else ""

        # Expand/edit toggle key
        edit_key = f"edit_{pid}"
        if edit_key not in st.session_state:
            st.session_state[edit_key] = False

        # Row: checkbox | title+badge | edit | delete
        c_check, c_main, c_edit, c_del = st.columns([0.3, 7, 0.6, 0.4])

        # ── Checkbox ──────────────────────────────────────────────────────
        checked = c_check.checkbox("", value=is_done, key=f"chk_{pid}", label_visibility="collapsed")
        if checked != is_done:
            item["done"] = checked
            save_items(items)
            st.rerun()

        # ── Title + note ───────────────────────────────────────────────────
        with c_main:
            st.markdown(
                f'<div class="item-title {title_cls}">'
                f'{item["title"]}'
                f'<span class="badge {badge_cls}">{item["priority"]}</span>'
                f'</div>'
                + (f'<div class="item-note">{item["note"]}</div>' if item["note"] else ""),
                unsafe_allow_html=True,
            )

        # ── Edit button ────────────────────────────────────────────────────
        if c_edit.button("✏️", key=f"editbtn_{pid}", help="Edit"):
            st.session_state[edit_key] = not st.session_state[edit_key]
            st.rerun()

        # ── Delete button ──────────────────────────────────────────────────
        if c_del.button("🗑", key=f"delbtn_{pid}", help="Delete"):
            items[:] = [i for i in items if i["id"] != pid]
            save_items(items)
            st.rerun()

        # ── Inline edit form ───────────────────────────────────────────────
        if st.session_state.get(edit_key):
            with st.container(border=True):
                e1, e2, e3 = st.columns([3, 1.5, 1.5])
                new_t = e1.text_input("Title",    value=item["title"],    key=f"et_{pid}")
                new_p = e2.selectbox("Phase",     PHASES,                 key=f"ep_{pid}",
                                      index=PHASES.index(item["phase"]) if item["phase"] in PHASES else 0)
                new_pr= e3.selectbox("Priority",  PRIORITIES,             key=f"epr_{pid}",
                                      index=PRIORITIES.index(item["priority"]) if item["priority"] in PRIORITIES else 1)
                new_n = st.text_area("Notes", value=item["note"], key=f"en_{pid}", height=80)
                s1, s2, _ = st.columns([1, 1, 4])
                if s1.button("💾 Save", key=f"save_{pid}", type="primary"):
                    item["title"]    = new_t.strip() or item["title"]
                    item["phase"]    = new_p
                    item["priority"] = new_pr
                    item["note"]     = new_n.strip()
                    save_items(items)
                    st.session_state[edit_key] = False
                    st.rerun()
                if s2.button("Cancel", key=f"cancel_{pid}"):
                    st.session_state[edit_key] = False
                    st.rerun()

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# FOOTER
# ─────────────────────────────────────────────
st.divider()
st.caption(f"Data persisted to `{DATA_FILE}` · {total} items · Last session: {datetime.today().strftime('%B %d, %Y')}")

col_export, col_reset, _ = st.columns([1, 1, 5])
with col_export:
    md_lines = ["# Voskuil FP 1.0 — Punch List\n"]
    for phase in PHASES:
        phase_items = [i for i in items if i["phase"] == phase]
        if phase_items:
            md_lines.append(f"\n## {phase}\n")
            for i in phase_items:
                chk = "x" if i["done"] else " "
                md_lines.append(f"- [{chk}] **{i['title']}** `{i['priority']}`")
                if i["note"]:
                    md_lines.append(f"  {i['note']}")
    st.download_button(
        "⬇️ Export as Markdown",
        data="\n".join(md_lines),
        file_name="PUNCH_LIST.md",
        mime="text/markdown",
        use_container_width=True,
    )
with col_reset:
    if st.button("↺ Reset to Defaults", use_container_width=True):
        if st.session_state.get("confirm_reset"):
            save_items(DEFAULT_ITEMS)
            st.session_state.punch_items = DEFAULT_ITEMS
            st.session_state.confirm_reset = False
            st.rerun()
        else:
            st.session_state.confirm_reset = True
            st.warning("Click again to confirm reset — all changes will be lost.")

