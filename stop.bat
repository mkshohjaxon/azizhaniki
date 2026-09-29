@echo off
chcp 65001 >nul 2>&1
title TG Account Manager - Stop
setlocal

set "PORT=8010"

echo.
echo   TG Account Manager to'xtatilmoqda...
echo.

for /f "tokens=5" %%P in ('netstat -aon 2^>nul ^| findstr "LISTENING" ^| findstr ":%PORT% "') do (
    echo   PID %%P to'xtatilmoqda...
    taskkill /PID %%P /F >nul 2>&1
)

echo   [OK] Server to'xtatildi
echo.
timeout /t 3
