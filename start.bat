@echo off
chcp 65001 >nul 2>&1
title TG Account Manager
setlocal

set "DIR=%~dp0"
set "PORT=8010"
set "VENV=%DIR%venv"
set "URL=http://127.0.0.1:%PORT%/7878/"

echo.
echo   ========================================
echo        TG Account Manager
echo   ========================================
echo.

:: --- Python tekshirish ---
where python >nul 2>&1
if %errorlevel% equ 0 (
    set "PY=python"
    goto :found_python
)
where python3 >nul 2>&1
if %errorlevel% equ 0 (
    set "PY=python3"
    goto :found_python
)
echo [X] Python topilmadi!
echo     https://www.python.org/downloads/ dan yuklab o'rnating
pause
exit /b 1

:found_python
echo [OK] Python: %PY%

:: --- Venv tekshirish ---
if not exist "%VENV%\Scripts\python.exe" (
    echo [..] Virtual muhit yaratilmoqda...
    %PY% -m venv "%VENV%"
    if not exist "%VENV%\Scripts\python.exe" (
        echo [X] Venv yaratib bo'lmadi!
        pause
        exit /b 1
    )
    call "%VENV%\Scripts\activate.bat"
    echo [..] Kutubxonalar o'rnatilmoqda...
    pip install telethon aiosqlite fastapi uvicorn cryptography openai
    echo [OK] Kutubxonalar o'rnatildi
) else (
    call "%VENV%\Scripts\activate.bat"
    echo [OK] Virtual muhit tayyor
)

:: --- Port bo'shatish ---
for /f "tokens=5" %%P in ('netstat -aon 2^>nul ^| findstr "LISTENING" ^| findstr ":%PORT% "') do (
    echo [..] Port %PORT% band — PID %%P to'xtatilmoqda...
    taskkill /PID %%P /F >nul 2>&1
    timeout /t 2 /nobreak >nul
)

:: --- Serverni ishga tushirish ---
echo [..] Server ishga tushirilmoqda (port %PORT%)...
cd /d "%DIR%"
start "" /b "%VENV%\Scripts\python.exe" -m uvicorn api:root --host 127.0.0.1 --port %PORT% --timeout-graceful-shutdown 5

:: --- Kutish ---
echo [..] Server tayyorlanmoqda...
timeout /t 3 /nobreak >nul

:: PowerShell bilan tekshirish (curl kerak emas)
set "OK=0"
for /l %%i in (1,1,15) do (
    if "!OK!"=="0" (
        powershell -NoProfile -Command "try{$null=Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/' -UseBasicParsing -TimeoutSec 2;'READY'}catch{}" 2>nul | findstr "READY" >nul 2>&1
        if not errorlevel 1 (
            set "OK=1"
            goto :server_ready
        )
        timeout /t 1 /nobreak >nul
    )
)

:: 15 soniyadan keyin ham tekshiramiz
powershell -NoProfile -Command "try{$null=Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/' -UseBasicParsing -TimeoutSec 3;'READY'}catch{}" 2>nul | findstr "READY" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [X] Server ishga tushmadi!
    echo     Logni tekshiring: type "%DIR%server.log"
    pause
    exit /b 1
)

:server_ready
echo.
echo   ==========================================
echo    [OK] Server ishlayapti!
echo.
echo    Manzil:  %URL%
echo    Port:    %PORT%
echo   ==========================================
echo.

:: --- Brauzerni ochish ---
echo [..] Brauzer ochilmoqda...
start "" "%URL%"

echo.
echo   To'xtatish: stop.bat yoki shu oynani yoping
echo.
pause
