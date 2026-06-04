"""
rename_files.py — Auto-rename MS Online downloaded files
=========================================================
Finds the most recently downloaded MS Online files in your
Downloads folder and renames them to standard names for push_files.py.

Run this immediately after downloading all 5 files from MS Online.

DEFAULT MS FILENAMES:
  Holdings Ungrouped.xlsx      → ms_holdings.xlsx
  Activity.xlsx                → ms_transactions_ytd.xlsx   (most recent)
  Activity (1).xlsx            → ms_transactions_prior.xlsx (second most recent)
  Realized GL.xlsx             → ms_realized_gl_current.xlsx (most recent)
  Realized GL (1).xlsx         → ms_realized_gl_prior.xlsx  (second most recent)
"""

import shutil
import glob
from pathlib import Path
from datetime import datetime

DOWNLOADS = Path(r"C:\Users\John Voskuil\Downloads")

def find_latest(pattern: str, n: int = 1) -> list:
    """Find the n most recently modified files matching pattern."""
    matches = [Path(f) for f in glob.glob(str(DOWNLOADS / pattern))]
    matches.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return matches[:n]


def rename_file(src: Path, dest_name: str) -> bool:
    """Copy src to dest_name in Downloads. Returns True on success."""
    if not src.exists():
        print(f"  SKIP — not found: {src.name}")
        return False
    dest = DOWNLOADS / dest_name
    shutil.copy2(src, dest)
    print(f"  {src.name} → {dest_name}")
    return True


def main():
    print("\n" + "="*60)
    print("  Voskuil FP — Rename MS Downloads")
    print("="*60)
    print(f"  Downloads folder: {DOWNLOADS}\n")

    results = {}

    # ── Holdings ──────────────────────────────────────────────────────────────
    print("  Holdings:")
    matches = find_latest("Holdings Ungrouped*.xlsx", 1)
    if matches:
        results["ms_holdings.xlsx"] = rename_file(matches[0], "ms_holdings.xlsx")
    else:
        print("  SKIP — no Holdings Ungrouped*.xlsx found")
        results["ms_holdings.xlsx"] = False

    # ── Activity — two files, sorted by time ─────────────────────────────────
    print("\n  Activity (Transactions):")
    matches = find_latest("Activity*.xlsx", 2)
    if len(matches) >= 2:
        # Most recent = whichever was downloaded last
        # We ask user to download Current Year first, Prior Year second
        # So most recent = Prior Year, second most recent = Current Year
        # BUT this depends on download order — let's confirm with user
        print(f"  Found {len(matches)} Activity files:")
        for i, f in enumerate(matches):
            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%H:%M:%S")
            print(f"    [{i+1}] {f.name} (downloaded at {mtime})")
        print()
        print("  Assuming download order: Current Year first, Prior Year second")
        print("  (Most recently downloaded = Prior Year)")
        results["ms_transactions_ytd.xlsx"]   = rename_file(matches[1], "ms_transactions_ytd.xlsx")
        results["ms_transactions_prior.xlsx"]  = rename_file(matches[0], "ms_transactions_prior.xlsx")
    elif len(matches) == 1:
        print(f"  Only 1 Activity file found — assigning as Current Year")
        results["ms_transactions_ytd.xlsx"]  = rename_file(matches[0], "ms_transactions_ytd.xlsx")
        results["ms_transactions_prior.xlsx"] = False
        print("  SKIP ms_transactions_prior.xlsx — download Prior Year file and re-run")
    else:
        print("  SKIP — no Activity*.xlsx files found")
        results["ms_transactions_ytd.xlsx"]   = False
        results["ms_transactions_prior.xlsx"]  = False

    # ── Realized G/L — two files, sorted by time ─────────────────────────────
    print("\n  Realized G/L:")
    matches = find_latest("Realized GL*.xlsx", 2)
    if len(matches) >= 2:
        print(f"  Found {len(matches)} Realized GL files:")
        for i, f in enumerate(matches):
            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%H:%M:%S")
            print(f"    [{i+1}] {f.name} (downloaded at {mtime})")
        print()
        print("  Assuming download order: Current Year first, Prior Year second")
        results["ms_realized_gl_current.xlsx"] = rename_file(matches[1], "ms_realized_gl_current.xlsx")
        results["ms_realized_gl_prior.xlsx"]   = rename_file(matches[0], "ms_realized_gl_prior.xlsx")
    elif len(matches) == 1:
        print(f"  Only 1 Realized GL file found — assigning as Current Year")
        results["ms_realized_gl_current.xlsx"] = rename_file(matches[0], "ms_realized_gl_current.xlsx")
        results["ms_realized_gl_prior.xlsx"]   = False
        print("  SKIP ms_realized_gl_prior.xlsx — download Prior Year file and re-run")
    else:
        print("  SKIP — no Realized GL*.xlsx files found")
        results["ms_realized_gl_current.xlsx"] = False
        results["ms_realized_gl_prior.xlsx"]   = False

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("  SUMMARY")
    print("="*60)
    all_ok = True
    for name, ok in results.items():
        status = "OK  " if ok else "FAIL"
        print(f"  {status} — {name}")
        if not ok:
            all_ok = False
    print("="*60)

    if all_ok:
        print("\n  All files renamed. Now run: python push_files.py\n")
    else:
        print("\n  Some files missing. Download them from MS Online and re-run.\n")
        print("  Then run: python push_files.py\n")


if __name__ == "__main__":
    main()
