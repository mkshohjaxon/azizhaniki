@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
title TG Account Manager

set DIR=%~dp0
set PORT=8010
set VENV=%DIR%venv
set URL=http://127.0.0.1:%PORT%/7878/

echo.
echo   ╔══════════════════════════════════════╗
echo   ║    📱 TG Account Manager             ║
echo   ╚══════════════════════════════════════╝
echo.

:: 1. Python tekshirish
where python >nul 2>&1
if %errorlevel% neq 0 (
    where python3 >nul 2>&1
    if %errorlevel% neq 0 (
        echo ❌ Python topilmadi! python.org dan yuklab o'rnating.
        echo    https://www.python.org/downloads/
        pause
        exit /b 1
    )
    set PY=python3
) else (
    set PY=python
)

:: 2. Venv tekshirish / yaratish
if not exist "%VENV%\Scripts\activate.bat" (
    echo 📦 Virtual muhit yaratilmoqda...
    %PY% -m venv "%VENV%"
    if %errorlevel% neq 0 (
        echo ❌ Venv yaratib bo'lmadi!
        pause
        exit /b 1
    )
    call "%VENV%\Scripts\activate.bat"
    echo 📦 Kutubxonalar o'rnatilmoqda...
    pip install -q telethon aiosqlite fastapi uvicorn cryptography
) else (
    call "%VENV%\Scripts\activate.bat"
)

:: 3. Eski jarayonni to'xtatish (port band bo'lsa)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":%PORT% " ^| findstr "LISTENING" 2^>nul') do (
    echo ⚠️  Port %PORT% band — eski jarayon to'xtatilmoqda (PID %%a^)...
    taskkill /PID %%a /F >nul 2>&1
    timeout /t 2 /nobreak >nul
)

:: 4. Serverni ishga tushirish
echo 🚀 Server ishga tushirilmoqda (port %PORT%^)...

cd /d "%DIR%"
start /b "" "%VENV%\Scripts\uvicorn.exe" api:root --host 127.0.0.1 --port %PORT% --timeout-graceful-shutdown 5 > "%DIR%server.log" 2>&1

:: 5. Server tayyor bo'lguncha kutish
echo    Kutilmoqda...
set READY=0
for /l %%i in (1,1,20) do (
    if !READY!==0 (
        timeout /t 1 /nobreak >nul
        curl -s -o nul -w "%%{http_code}" "http://127.0.0.1:%PORT%/" 2>nul | findstr /r "200 301 302 307" >nul 2>&1
        if !errorlevel!==0 set READY=1
    )
)

:: curl yo'q bo'lsa powershell bilan tekshirish
if %READY%==0 (
    for /l %%i in (1,1,10) do (
        if !READY!==0 (
            timeout /t 1 /nobreak >nul
            powershell -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:%PORT%/' -UseBasicParsing -TimeoutSec 2; if($r.StatusCode -le 307){'OK'} } catch {}" 2>nul | findstr "OK" >nul 2>&1
            if !errorlevel!==0 set READY=1
        )
    )
)

:: 6. Natija
echo.
echo ✅ Server ishlayapti!
echo.
echo    Manzil:  %URL%
echo    Log:     %DIR%server.log
echo.

:: 7. Brauzerni ochish
echo 🌐 Brauzer ochilmoqda...
start "" "%URL%"

echo.
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo   To'xtatish:  stop.bat
echo   Log ko'rish: type server.log
echo ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
echo.
echo Serverni to'xtatish uchun shu oynani yoping yoki stop.bat ni ishga tushiring.
echo.
pause
