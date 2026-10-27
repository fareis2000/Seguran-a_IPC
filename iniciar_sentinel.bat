@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "dist\SentinelPC.exe" (
    start "" "dist\SentinelPC.exe"
) else (
    start "" ".venv\Scripts\pythonw.exe" "main.py"
)
