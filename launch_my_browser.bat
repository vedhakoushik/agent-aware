@echo off
REM ── Launch a DEDICATED Chrome the app will drive with YOUR logins ──
REM
REM 1. Run this file (double-click it). This opens your REAL Google Chrome.
REM 2. In that window, sign in (incl. "Sign in with Google") to the travel /
REM    shopping sites you want — and to your Google account if you want OAuth
REM    logins. Use ONLY accounts you're OK automating — NOT bank or work.
REM 3. KEEP THIS WINDOW OPEN. The app attaches to it (port 9222) and runs every
REM    search inside YOUR real, signed-in Chrome — never a guest/testing browser.
REM    Close it and searches fall back to the dedicated profile below.
REM
REM This profile lives in %USERPROFILE%\agent-aware-chrome — separate from your
REM normal Chrome, so your everyday logins are never exposed to the agent.

echo Opening your dedicated automation Chrome (debug port 9222)...
echo Sign in to the travel/shopping sites you want, then leave this window open.
start "" chrome.exe --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\agent-aware-chrome" --no-first-run --no-default-browser-check
