@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Gerando executavel autonomo dist\SentinelPC.exe ...
".venv\Scripts\python.exe" "build_exe.py"
echo.
pause
