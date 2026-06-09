@echo off
echo === Agent-Aware Setup ===

echo [1/5] Creating virtual environment...
python -m venv .venv
call .venv\Scripts\activate

echo [2/5] Installing dependencies...
pip install -r requirements.txt

echo [3/5] Installing Playwright browsers...
playwright install chromium

echo [4/5] Creating data directory...
mkdir data\chroma 2>nul

echo [5/5] Setting up .env...
if not exist .env (
    copy .env.example .env
    echo.
    echo *** IMPORTANT: Open .env and add your GROQ_API_KEY ***
    echo *** Get a free key at: https://console.groq.com/  ***
    echo.
) else (
    echo .env already exists, skipping.
)

echo.
echo === Setup complete! ===
echo.
echo To run:
echo   .venv\Scripts\activate
echo   python run.py
echo.
pause
