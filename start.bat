@echo off
title capps
cd /d "%~dp0"

where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo Error: Python not found.
    if /I not "%~1"=="--startup" pause
    exit /b 1
)

python -c "import fastapi, uvicorn, httpx" 2>nul
if %ERRORLEVEL% neq 0 (
    echo Installing dependencies...
    python -m pip install -r requirements.txt
)

echo.
echo   c-apps dashboard
echo   Open: http://127.0.0.1:8000/
if /I "%~1"=="--startup" (
    echo   Startup mode: will start managed apps as needed
) else (
    echo   Press Ctrl+C to stop, or use Stop server on the dashboard
)
echo   Stop ^(when running^): POST http://127.0.0.1:8000/api/local/shutdown ^(loopback only^)
echo.

python run.py %*
if /I not "%~1"=="--startup" pause
