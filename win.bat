@echo off
chcp 65001 >nul 2>&1
title TG Account Manager

set "DIR=%~dp0"
set "VENV=%DIR%venv"

:: Venv yo'q bo'lsa yaratish
if not exist "%VENV%\Scripts\python.exe" (
    echo Virtual muhit yaratilmoqda...
    python -m venv "%VENV%"
    if not exist "%VENV%\Scripts\python.exe" (
        python3 -m venv "%VENV%"
    )
    if not exist "%VENV%\Scripts\python.exe" (
        echo Python topilmadi! python.org dan o'rnating
        pause
        exit /b 1
    )
    "%VENV%\Scripts\pip.exe" install telethon aiosqlite fastapi uvicorn cryptography openai pywebview
)

"%VENV%\Scripts\pythonw.exe" "%DIR%run.py"
