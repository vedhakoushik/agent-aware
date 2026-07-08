@echo off
REM Reopen YOUR vedha-koushik Chrome (Profile 1) with automation enabled so the app
REM drives your real, logged-in browser — no signed-out/headless browser ever.
REM NOTE: this closes all current Chrome windows first (debug port needs a clean start).
echo Closing Chrome, reopening your vedha-koushik account with automation enabled...
taskkill /F /IM chrome.exe >nul 2>&1
timeout /t 2 /nobreak >nul
start "" chrome --remote-debugging-port=9222 --profile-directory="Profile 1" --no-first-run --no-default-browser-check "https://www.makemytrip.com/flights/"
echo Done. Keep this Chrome open — the app now searches through YOUR logged-in account.
