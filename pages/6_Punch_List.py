import streamlit as st
import json
import os
import time
from datetime import datetime


# ─────────────────────────────────────────────
# STYLING
# ─────────────────────────────────────────────
st.markdown("""
<style>
/* ── Base card ─────────────────────────────────────────────────────── */
.punch-card {
    background: #ffffff;
    border: 1px solid #e8e8ed;
    border-radius: 6px;
    padding: 12px 16px;
    margin-bottom: 6px;
    border-left: 4px solid #e8e8ed;
    transition: box-shadow 0.15s, border-color 0.15s;
}
.punch-card:hover {
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
}
.punch-card.done {
    background: #f9fafb;
    border-left-color: #22c55e;
    opacity: 0.75;
}

/* ── Left-border accent by priority ────────────────────────────────── */
.priority-urgent { border-left-color: #ef4444 !important; }
.priority-high   { border-left-color: #f97316 !important; }
.priority-medium { border-left-color: #3b82f6 !important; }
.priority-low    { border-left-color: #9ca3af !important; }

/* ── Priority badges ────────────────────────────────────────────────── */
.badge {
    display: inline-block;
    padding: 2px 9px;
    border-radius: 4px;
    font-size: 0.68em;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    margin-left: 8px;
    vertical-align: middle;
}
.badge-urgent { background: #fef2f2; color: #dc2626; border: 1px solid #fca5a5; }
.badge-high   { background: #fff7ed; color: #c2410c; border: 1px solid #fdba74; }
.badge-medium { background: #eff6ff; color: #1d4ed8; border: 1px solid #93c5fd; }
.badge-low    { background: #f9fafb; color: #6b7280; border: 1px solid #d1d5db; }
.badge-done   { background: #f0fdf4; color: #15803d; border: 1px solid #86efac; }

/* ── Phase header ───────────────────────────────────────────────────── */
.phase-header {
    font-size: 0.72em;
    font-weight: 700;
    letter-spacing: 0.10em;
    text-transform: uppercase;
    color: #6b7280;
    padding: 14px 0 6px 0;
    border-bottom: 2px solid #f3f4f6;
    margin-bottom: 10px;
}

/* ── Stats bar ──────────────────────────────────────────────────────── */
.stats-bar {
    display: flex;
    gap: 12px;
    margin-bottom: 20px;
    padding: 14px 20px;
    background: #f8f9fc;
    border-radius: 8px;
    border: 1px solid #e8e8ed;
}
.stat-item { text-align: center; min-width: 56px; }
.stat-num  {
    font-size: 1.7em;
    font-weight: 800;
    color: #111827;
    line-height: 1;
    font-variant-numeric: tabular-nums;
}
.stat-lbl  {
    font-size: 0.68em;
    color: #9ca3af;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-top: 2px;
}

/* ── Item text ──────────────────────────────────────────────────────── */
.item-title {
    font-size: 0.92em;
    font-weight: 600;
    color: #111827;
}
.item-title.done-title {
    text-decoration: line-through;
    color: #22c55e;
}
.item-note {
    font-size: 0.77em;
    color: #6b7280;
    margin-top: 3px;
    line-height: 1.55;
}
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# DATA LAYER — persists to PUNCH_LIST.json
# ─────────────────────────────────────────────
DATA_FILE = "punch_list_data.json"

DEFAULT_PHASES = [
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
    {"id": 17, "title": "Punch list page — light theme polish",
     "note": "Current light-theme styling is functional but basic. Could improve card layout, spacing, and typography to better match the rest of the app. Low priority — works fine as-is.",
     "phase": "Immediate / Near-Term", "priority": "Low", "done": False, "created": "2026-05-29"},
    # Phase 4
    {"id": 4,  "title": "SEC filing deep-dive links in Equity Scout",
     "note": "SEC and Yahoo Finance buttons added directly to Equity Scout page after Deep Dive click. Complete.",
     "phase": "Phase 4 — Deeper Analysis", "priority": "High", "done": True, "created": "2026-05-01"},
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
     "note": "Wired into fetch_score_data (app.py, market_screener.py) via new Massive v1 depreciation_depletion_and_amortization field. Still using proxy in equity_scout fetch_fundamentals — carry forward.",
     "phase": "Data Quality", "priority": "Medium", "done": True, "created": "2026-05-01"},
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

def _github_get_current():
    """GET the current punch_list_data.json + SHA from GitHub.
    Returns (sha, content_str, ok, msg). SHA is None on 404 (file doesn't exist yet)."""
    import base64, requests as _req
    token = st.secrets.get("GITHUB_TOKEN", "")
    repo  = st.secrets.get("GITHUB_REPO",  "jjpvoskuil/Voskuil-FP-1-0")
    if not token:
        return None, None, False, "GITHUB_TOKEN not found in Streamlit secrets."
    try:
        api   = f"https://api.github.com/repos/{repo}/contents/{DATA_FILE}"
        heads = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
        }
        r = _req.get(api, headers=heads, timeout=8)
        if r.status_code == 404:
            return None, None, True, "File does not exist on remote yet."
        if r.status_code != 200:
            return None, None, False, f"GET failed: {r.status_code} {r.json().get('message', r.text)[:150]}"
        body = r.json()
        sha  = body.get("sha")
        content = base64.b64decode(body.get("content", "")).decode()
        return sha, content, True, "OK"
    except Exception as e:
        return None, None, False, f"GET exception: {e}"


def _parse_punch_list(content: str):
    """Parse punch_list_data.json content string into (items, phases).
    Handles both old list format and new dict format."""
    parsed = json.loads(content)
    if isinstance(parsed, list):
        items  = parsed
        phases = DEFAULT_PHASES.copy()
    else:
        items  = parsed.get("items",  DEFAULT_ITEMS)
        phases = parsed.get("phases", DEFAULT_PHASES.copy())
        for item in items:
            if item["phase"] not in phases:
                phases.append(item["phase"])
    return items, phases


def load_from_github():
    """Load punch list from GitHub (bypasses local disk cache).
    Sets session_state loaded_sha. Falls back to disk on error.
    Returns (items, phases)."""
    sha, content, ok, msg = _github_get_current()
    if ok and content:
        try:
            items, phases = _parse_punch_list(content)
            # Mirror to disk so other code paths stay consistent
            with open(DATA_FILE, "w") as f:
                f.write(content)
            st.session_state.punch_list_backup     = content
            st.session_state.punch_list_saved_at   = datetime.today().strftime("%b %d %Y %H:%M")
            st.session_state.punch_list_loaded_sha = sha
            st.session_state.punch_list_last_sha_check = time.time()
            return items, phases
        except Exception:
            pass  # fall through to disk load
    # Fallback: read from local disk
    return load_items(_skip_github=True)


def load_items(_skip_github: bool = False):
    """Load punch list from disk (fallback path).
    Normal callers should use load_from_github() instead."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE) as f:
                data = f.read()
            items, phases = _parse_punch_list(data)
            st.session_state.punch_list_backup   = data
            st.session_state.punch_list_saved_at = datetime.today().strftime("%b %d %Y %H:%M")
            return items, phases
        except Exception:
            pass
    save_items(DEFAULT_ITEMS, DEFAULT_PHASES.copy())
    return DEFAULT_ITEMS, DEFAULT_PHASES.copy()


def _github_backup(data: str):
    """Push punch_list_data.json to GitHub via API.
    Returns (success: bool, message: str, new_sha: str|None) — never fails silently."""
    import base64, requests as _req
    token = st.secrets.get("GITHUB_TOKEN", "")
    repo  = st.secrets.get("GITHUB_REPO",  "jjpvoskuil/Voskuil-FP-1-0")
    if not token:
        return False, "GITHUB_TOKEN not found in Streamlit secrets — edits are NOT syncing to GitHub.", None
    try:
        api   = f"https://api.github.com/repos/{repo}/contents/{DATA_FILE}"
        heads = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
        }
        r = _req.get(api, headers=heads, timeout=8)
        if r.status_code not in (200, 404):
            return False, f"GitHub GET failed: {r.status_code} {r.json().get('message', r.text)[:150]}", None
        sha = r.json().get("sha") if r.status_code == 200 else None

        payload = {
            "message": f"Auto-backup punch list {datetime.today().strftime('%Y-%m-%d %H:%M')}",
            "content": base64.b64encode(data.encode()).decode(),
        }
        if sha:
            payload["sha"] = sha

        put_r = _req.put(api, headers=heads, json=payload, timeout=8)
        if put_r.status_code not in (200, 201):
            return False, f"GitHub PUSH failed: {put_r.status_code} {put_r.json().get('message', put_r.text)[:150]}", None
        # Capture the new SHA so we can update loaded_sha and avoid false-positive
        # staleness on our own write.
        new_sha = None
        try:
            new_sha = put_r.json().get("content", {}).get("sha")
        except Exception:
            pass
        return True, "Synced", new_sha
    except Exception as e:
        return False, f"GitHub push exception: {e}", None


def save_items(items, phases=None):
    """Save items + phases to disk, session state, and GitHub — with SHA guard
    to prevent overwriting a newer remote version (e.g., Claude just pushed).
    """
    try:
        if phases is None:
            phases = st.session_state.get("punch_phases", DEFAULT_PHASES.copy())

        # ── SHA guard ───────────────────────────────────────────────────────
        # If remote SHA doesn't match what we loaded, someone else (Claude,
        # another tab) pushed since our page load. Refuse to write and force
        # the user to reload before re-applying their edit.
        expected_sha = st.session_state.get("punch_list_loaded_sha")
        if expected_sha:
            remote_sha, _, sha_ok, sha_msg = _github_get_current()
            if sha_ok and remote_sha and remote_sha != expected_sha:
                st.session_state.punch_list_stale = True
                st.warning(
                    "⚠️ **Punch list changed on GitHub since you opened this page** "
                    "(likely a push from Claude or another browser tab). "
                    "Your edit was **NOT saved** — saving it would overwrite the newer version. "
                    "Click **🔄 Reload from GitHub** at the top of the page, then re-apply your change."
                )
                return
            # If we couldn't check SHA (network hiccup), warn but proceed — better
            # than blocking the user entirely.
            if not sha_ok:
                st.info(f"ℹ️ Could not verify remote version before saving ({sha_msg}). Proceeding anyway.")

        payload = {"items": items, "phases": phases}
        data    = json.dumps(payload, indent=2)
        with open(DATA_FILE, "w") as f:
            f.write(data)
        st.session_state.punch_list_backup   = data
        st.session_state.punch_list_saved_at = datetime.today().strftime("%b %d %Y %H:%M")

        ok, msg, new_sha = _github_backup(data)
        st.session_state.punch_list_github_ok  = ok
        st.session_state.punch_list_github_msg = msg
        if ok and new_sha:
            # Track the SHA we just wrote so our next save doesn't false-positive
            # against our own push.
            st.session_state.punch_list_loaded_sha     = new_sha
            st.session_state.punch_list_last_sha_check = time.time()
            st.session_state.punch_list_stale          = False
        if not ok:
            st.error(f"⚠️ Local save OK, but GitHub sync FAILED: {msg}\n\n"
                     f"Your changes exist only in this session and WILL BE LOST on reboot/redeploy "
                     f"unless you download a backup (💾 Backup JSON) or fix the sync.")
    except Exception as e:
        st.error(f"Could not save: {e}")

def next_id(items):
    return max((i["id"] for i in items), default=0) + 1

# Load — always try GitHub first, fall back to disk. This makes GitHub the
# source of truth and captures the current SHA so save_items can detect
# out-of-band writes (Claude pushing from the sandbox, another tab, etc).
if "punch_items" not in st.session_state or "punch_phases" not in st.session_state:
    _items, _phases = load_from_github()
    st.session_state.punch_items  = _items
    st.session_state.punch_phases = _phases

# ── Auto-refresh watcher ──────────────────────────────────────────────────
# On every rerun, check if the remote SHA changed since we last looked.
# Throttled to at most 1 check per 20 seconds to stay well under GitHub's
# 5000/hr rate limit even with active clicking. If remote is newer, silently
# reload session state and show a small toast so the user knows.
_now = time.time()
_last_check = st.session_state.get("punch_list_last_sha_check", 0)
if _now - _last_check > 20:
    _remote_sha, _, _rc_ok, _ = _github_get_current()
    st.session_state.punch_list_last_sha_check = _now
    if _rc_ok and _remote_sha and _remote_sha != st.session_state.get("punch_list_loaded_sha"):
        _items, _phases = load_from_github()
        st.session_state.punch_items  = _items
        st.session_state.punch_phases = _phases
        try:
            st.toast("🔄 Auto-refreshed from GitHub — remote changed", icon="🔄")
        except Exception:
            pass  # toast may not exist on older Streamlit versions

items  = st.session_state.punch_items
PHASES = st.session_state.punch_phases   # live list — may grow as user adds phases

# ─────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────
st.title("🗂️ Voskuil FP — Dev Punch List")
st.caption("Internal development tracker · Not visible in production · Remove this page before commercial launch")

# ── Persistent GitHub sync error banner ─────────────────────────────────────
# Survives st.rerun() by reading from session state. Prevents the flash-and-vanish
# error that used to happen when a checkbox toggle triggered save → rerun.
if st.session_state.get("punch_list_github_ok") is False:
    st.error(
        f"⚠️ **GitHub sync FAILED on last save.** Your changes exist ONLY in this session "
        f"and WILL BE LOST on reboot/redeploy.\n\n"
        f"**Error:** {st.session_state.get('punch_list_github_msg', '(no message)')}\n\n"
        f"**Action:** Download a backup with the 💾 button below, then fix the sync "
        f"(most likely: GITHUB_TOKEN expired or missing repo scope in Streamlit secrets)."
    )
    if st.button("Dismiss banner (until next failure)"):
        st.session_state.punch_list_github_ok = None
        st.rerun()

# Stats bar
total   = len(items)
done    = sum(1 for i in items if i["done"])
open_   = total - done
urgent  = sum(1 for i in items if not i["done"] and i["priority"] == "Urgent")
high    = sum(1 for i in items if not i["done"] and i["priority"] == "High")

st.markdown(f"""
<div class="stats-bar">
  <div class="stat-item"><div class="stat-num">{open_}</div><div class="stat-lbl">Open</div></div>
  <div class="stat-item"><div class="stat-num" style="color:#dc2626">{urgent}</div><div class="stat-lbl">Urgent</div></div>
  <div class="stat-item"><div class="stat-num" style="color:#c2410c">{high}</div><div class="stat-lbl">High</div></div>
  <div class="stat-item"><div class="stat-num" style="color:#15803d">{done}</div><div class="stat-lbl">Done</div></div>
  <div class="stat-item"><div class="stat-num" style="color:#6b7280">{total}</div><div class="stat-lbl">Total</div></div>
</div>
""", unsafe_allow_html=True)

# ── Always-visible backup + reload controls ───────────────────────────────
# Backup: Download current JSON — save this after every editing session as a
#   local backup. Streamlit Cloud's filesystem resets on redeploy.
# Reload: Force re-read from GitHub (bypasses session state) — use if you
#   suspect the app is showing a stale copy after Claude or another tab
#   pushed changes.
backup_data = st.session_state.get("punch_list_backup", json.dumps(items, indent=2))
saved_at    = st.session_state.get("punch_list_saved_at", "now")

# Stale warning: if last save was blocked by SHA guard, remind user to reload
if st.session_state.get("punch_list_stale"):
    st.warning(
        "⚠️ **Session is out of sync with GitHub.** Your last edit was not saved. "
        "Click **🔄 Reload from GitHub** below to pull the latest, then re-apply your change."
    )

bcol1, bcol2, bcol3 = st.columns([1, 1, 5])
with bcol1:
    st.download_button(
        "💾 Backup JSON",
        data=backup_data,
        file_name=f"punch_list_data.json",
        mime="application/json",
        use_container_width=True,
        help="Download your punch list as JSON. Re-upload to restore after a Streamlit Cloud redeploy wipes the filesystem.",
    )
with bcol2:
    if st.button("🔄 Reload from GitHub", use_container_width=True,
                 help="Force re-read punch_list_data.json from GitHub (bypasses session state). Use this if the app looks stale after Claude pushed changes."):
        _items, _phases = load_from_github()
        st.session_state.punch_items  = _items
        st.session_state.punch_phases = _phases
        st.session_state.punch_list_stale = False
        st.success("Reloaded from GitHub.")
        st.rerun()
with bcol3:
    gh_ok  = st.session_state.get("punch_list_github_ok", None)
    gh_msg = st.session_state.get("punch_list_github_msg", "")
    if gh_ok is True:
        st.caption(f"✅ **Synced to GitHub.** Last saved: {saved_at}")
    elif gh_ok is False:
        st.caption(f"❌ **GitHub sync FAILED** — {gh_msg}  "
                   f"Changes only live in this session. Download a backup now. Last local save: {saved_at}")
    else:
        st.caption(
            f"ℹ️ GitHub sync status unknown until your next edit. "
            f"The 💾 button is a manual backup if you want a local copy too. "
            f"Last saved: {saved_at}"
        )

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

# ── Manage Phases ──────────────────────────────────────────────────────────
with st.expander("🗂️ Manage Phases", expanded=False):
    st.caption("Add new phases or remove unused ones. Changes apply immediately to all dropdowns.")
    pc1, pc2 = st.columns([3, 1])
    new_phase_name = pc1.text_input("New phase name", placeholder="e.g. Bug Fixes, Version 2.0, On Hold...",
                                     label_visibility="collapsed", key="new_phase_input")
    if pc2.button("➕ Add Phase", use_container_width=True):
        name = new_phase_name.strip()
        if name and name not in PHASES:
            PHASES.append(name)
            st.session_state.punch_phases = PHASES
            save_items(items, PHASES)
            st.rerun()
        elif name in PHASES:
            st.warning(f'"{name}" already exists.')
        else:
            st.warning("Enter a phase name first.")

    st.markdown("**Current phases** — click 🗑 to remove (only if no items use it):")
    for ph in list(PHASES):
        ph_col1, ph_col2 = st.columns([5, 1])
        ph_col1.markdown(f"• {ph}")
        in_use = any(i["phase"] == ph for i in items)
        if ph_col2.button("🗑", key=f"del_phase_{ph}", disabled=in_use,
                           help="In use — reassign items first" if in_use else f"Remove '{ph}'"):
            PHASES.remove(ph)
            st.session_state.punch_phases = PHASES
            save_items(items, PHASES)
            st.rerun()

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
    "Urgent": ("#dc2626", "badge-urgent"),
    "High":   ("#c2410c", "badge-high"),
    "Medium": ("#1d4ed8", "badge-medium"),
    "Low":    ("#6b7280", "badge-low"),
}

# Filter
visible = [i for i in items if (
    (filter_phase    == "All Phases"     or i["phase"]    == filter_phase) and
    (filter_priority == "All Priorities" or
     (filter_priority == "Done" and i["done"]) or
     (filter_priority != "Done" and i["priority"] == filter_priority and not i["done"])) and
    (show_done or filter_priority == "Done" or not i["done"])
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
                f'<span style="color:#9ca3af; font-weight:400; margin-right:6px">#{pid}</span>'
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

@st.dialog("⚠️ Reset to Defaults?")
def confirm_reset_dialog():
    st.warning(
        f"This will delete all **{len(st.session_state.punch_items)} items** "
        f"and restore the original {len(DEFAULT_ITEMS)}-item default list. "
        f"**This cannot be undone** unless you have a backup."
    )
    st.caption("Tip: Download a backup first using the 💾 Backup JSON button above.")
    c1, c2 = st.columns(2)
    if c1.button("Yes, reset everything", type="primary", use_container_width=True):
        save_items(DEFAULT_ITEMS, DEFAULT_PHASES.copy())
        st.session_state.punch_items  = DEFAULT_ITEMS.copy()
        st.session_state.punch_phases = DEFAULT_PHASES.copy()
        st.session_state.confirm_reset = False
        st.rerun()
    if c2.button("Cancel", use_container_width=True):
        st.rerun()

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
        confirm_reset_dialog()
