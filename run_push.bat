@echo off
cd "C:\John V\Fox Den Holdings\Voskuil FP 1.0"
echo.
echo ============================================================
echo   Voskuil FP -- MS Data Push
echo ============================================================
echo.
echo Step 1: Renaming downloaded files...
python rename_files.py
echo.
echo Step 2: Converting and pushing to GitHub...
python push_files.py
echo.
echo ============================================================
echo   Done! Reload Streamlit to see fresh data.
echo ============================================================
echo.
pause