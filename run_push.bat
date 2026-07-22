@echo off
REM run_push.bat — LEGACY (Windows only). Since #73, the primary machine is
REM a Mac — use run_push.command instead (or just ask Claude in a Cowork
REM chat with Chrome access to "refresh MS data", which can drive the whole
REM Morgan Stanley download + push end to end). Kept here only in case this
REM is ever run on a Windows machine again.
cd /d "%~dp0"
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
