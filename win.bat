@echo off
chcp 65001 >nul 2>&1
set "DIR=%~dp0"
set "VENV=%DIR%venv"

if not exist "%VENV%\Scripts\python.exe" (
    echo Virtual muhit yaratilmoqda...
    python -m venv "%VENV%"
    "%VENV%\Scripts\pip.exe" install telethon aiosqlite fastapi uvicorn cryptography openai
)

"%VENV%\Scripts\python.exe" "%DIR%run.py"
