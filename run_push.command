#!/bin/bash
# run_push.command — Mac replacement for run_push.bat (#73).
# Double-click in Finder after downloading the 5 MS Online files to
# ~/Downloads. Renames/identifies them by content (not download order),
# converts to CSV, and pushes to GitHub so Streamlit Cloud picks up
# fresh data on next reload.
cd "$(dirname "$0")"
echo
echo "============================================================"
echo "  Voskuil FP -- MS Data Push"
echo "============================================================"
echo
echo "Step 1: Renaming downloaded files..."
python3 rename_files.py
echo
echo "Step 2: Converting and pushing to GitHub..."
python3 push_files.py
echo
echo "============================================================"
echo "  Done! Reload Streamlit to see fresh data."
echo "============================================================"
echo
read -p "Press Enter to close..."
