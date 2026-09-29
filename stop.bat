@echo off
chcp 65001 >nul 2>&1
title TG Account Manager - Stop

set PORT=8010

echo.
echo 🛑 TG Account Manager to'xtatilmoqda...
echo.

for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":%PORT% " ^| findstr "LISTENING" 2^>nul') do (
    echo    Jarayon to'xtatilmoqda (PID %%a^)...
    taskkill /PID %%a /F >nul 2>&1
)

echo ✅ Server to'xtatildi
echo.
timeout /t 3
