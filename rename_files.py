"""
rename_files.py — Auto-rename MS Online downloaded files (Mac/Linux/Windows)
=============================================================================
Finds the most recently downloaded MS Online files in your Downloads folder
and renames/copies them to standard names for push_files.py.

Run this immediately after downloading all 5 files from MS Online:
  - Holdings
  - Activity -> Current Year
  - Activity -> Prior Year
  - Realized Gain/Loss -> Current Year
  - Realized Gain/Loss -> Prior Year

DEFAULT MS FILENAMES:
  Holdings Ungrouped.xlsx      -> ms_holdings.xlsx
  Activity.xlsx / Activity (1).xlsx       -> ms_transactions_ytd.xlsx / ms_transactions_prior.xlsx
  Realized GL.xlsx / Realized GL (1).xlsx -> ms_realized_gl_current.xlsx / ms_realized_gl_prior.xlsx

NOTE ON CURRENT VS. PRIOR YEAR (#73): earlier versions of this script guessed
Current vs. Prior year from download order/timestamp ("assume Current Year
was downloaded first"), which is fragile -- it's wrong if you download them
in the other order, or re-download just one. This version instead opens each
candidate file and reads the actual report header text MS Online prints
inside the file itself ("... from Current Year" / "... from Prior Year" for
Activity; "Current Year Realized Gain/Loss ..." / "Previous Year Realized
Gain/Loss ..." for Realized G/L), so it's correct regardless of download
order.
"""

import shutil
import glob
from pathlib import Path

import pandas as pd

DOWNLOADS = Path.home() / "Downloads"


def find_recent(pattern: str, n: int = 5) -> list:
    """Find the n most recently modified files matching pattern."""
    matches = [Path(f) for f in glob.glob(str(DOWNLOADS / pattern))]
    matches.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return matches[:n]


def read_header_text(path: Path, max_rows: int = 8) -> str:
    """Read the first few rows of an MS Online xlsx export and join any
    text found into one lowercased string, for simple substring checks
    like 'current year' / 'prior year' / 'previous year'."""
    try:
        df = pd.read_excel(path, header=None, nrows=max_rows)
    except Exception:
        return ""
    cells = []
    for _, row in df.iterrows():
        cells.extend(str(v) for v in row.dropna().tolist())
    return " ".join(cells).lower()


def classify_year(path: Path) -> str:
    """Returns 'current', 'prior', or 'unknown' based on the file's own
    report header text -- not download order or mtime."""
    text = read_header_text(path)
    if "current year" in text:
        return "current"
    if "prior year" in text or "previous year" in text:
        return "prior"
    return "unknown"


def copy_file(src: Path, dest_name: str) -> bool:
    if not src.exists():
        print(f"  SKIP — not found: {src.name}")
        return False
    dest = DOWNLOADS / dest_name
    shutil.copy2(src, dest)
    print(f"  {src.name} → {dest_name}")
    return True


def handle_year_pair(glob_pattern: str, label: str, current_name: str, prior_name: str) -> dict:
    print(f"\n  {label}:")
    candidates = find_recent(glob_pattern, n=5)
    if not candidates:
        print(f"  SKIP — no {glob_pattern} files found")
        return {current_name: False, prior_name: False}

    results = {current_name: False, prior_name: False}
    seen = {"current": None, "prior": None}
    for f in candidates:
        year = classify_year(f)
        if year in seen and seen[year] is None:
            seen[year] = f
        if seen["current"] and seen["prior"]:
            break

    if seen["current"] is None or seen["prior"] is None:
        print("  Could not identify both Current and Prior year files by content.")
        for f in candidates[:4]:
            print(f"    - {f.name}: looks like '{classify_year(f)}'")
        print(f"  Make sure you downloaded BOTH Current Year and Prior Year for {label.lower()}.")
    if seen["current"] is not None:
        results[current_name] = copy_file(seen["current"], current_name)
    if seen["prior"] is not None:
        results[prior_name] = copy_file(seen["prior"], prior_name)
    return results


def main():
    print("\n" + "=" * 60)
    print("  Voskuil FP — Rename MS Downloads")
    print("=" * 60)
    print(f"  Downloads folder: {DOWNLOADS}")

    results = {}

    print("\n  Holdings:")
    matches = find_recent("Holdings Ungrouped*.xlsx", 1)
    if matches:
        results["ms_holdings.xlsx"] = copy_file(matches[0], "ms_holdings.xlsx")
    else:
        print("  SKIP — no Holdings Ungrouped*.xlsx found")
        results["ms_holdings.xlsx"] = False

    results.update(handle_year_pair(
        "Activity*.xlsx", "Activity (Transactions)",
        "ms_transactions_ytd.xlsx", "ms_transactions_prior.xlsx",
    ))
    results.update(handle_year_pair(
        "Realized GL*.xlsx", "Realized Gain/Loss",
        "ms_realized_gl_current.xlsx", "ms_realized_gl_prior.xlsx",
    ))

    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    all_ok = True
    for name, ok in results.items():
        status = "OK  " if ok else "FAIL"
        print(f"  {status} — {name}")
        if not ok:
            all_ok = False
    print("=" * 60)

    if all_ok:
        print("\n  All files renamed. Now run: python3 push_files.py\n")
    else:
        print("\n  Some files missing/unidentified. Download them from MS Online and re-run.\n")
        print("  Then run: python3 push_files.py\n")


if __name__ == "__main__":
    main()
