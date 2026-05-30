@echo off
title capps
cd /d "%~dp0"

where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo Error: Python not found.
    pause
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
echo   Press Ctrl+C to stop
echo.

python run.py
pause
