@echo off
REM Entry point for the Windows Startup folder — starts capps and managed c-apps.
cd /d "%~dp0"
call start.bat --startup
