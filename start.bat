@echo off
rem Launch script for Telegram Quiz Bot (Project 5).
rem Reads TG_TOKEN from the root .env, sets QUIZ_BOT_TOKEN, runs the bot.
cd /d "%~dp0"

for /f "usebackq tokens=1,* delims==" %%a in ("..\.env") do (
    if "%%a"=="TG_TOKEN" set "QUIZ_BOT_TOKEN=%%b"
)
if not defined QUIZ_BOT_TOKEN (
    echo [ERROR] TG_TOKEN not found in ..\.env
    pause
    exit /b 1
)

rem 0 = real OpenTDB API (needs internet) | 1 = offline built-in question pool
set "QUIZ_DEMO_MODE=1"
set "PYTHONIOENCODING=utf-8"

..\.venv\Scripts\python.exe -u bot.py
