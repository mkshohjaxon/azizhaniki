@echo off
chcp 65001 >nul 2>&1
title TG Account Manager

set "DIR=%~dp0"
set "VENV=%DIR%venv"

:: Venv yo'q bo'lsa yaratish
if not exist "%VENV%\Scripts\python.exe" (
    echo [..] Virtual muhit yaratilmoqda...
    python -m venv "%VENV%"
    if not exist "%VENV%\Scripts\python.exe" (
        echo [X] Python topilmadi! python.org dan o'rnating
        pause
        exit /b 1
    )
    "%VENV%\Scripts\pip.exe" install telethon aiosqlite fastapi uvicorn cryptography openai pywebview
)

:: run.py ni ishga tushirish
"%VENV%\Scripts\python.exe" "%DIR%run.py"
