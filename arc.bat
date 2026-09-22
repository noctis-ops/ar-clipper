@echo off
REM ============================================================
REM  AR-Clipper launcher for Windows
REM
REM  PowerShell:       .\arc doctor
REM  Command Prompt:   arc doctor
REM
REM  Examples:
REM    .\arc clip video.mp4 -s 0 -e 30 -L personal_test
REM    .\arc serve
REM
REM  NOTE: messages here are ASCII on purpose. The Windows console
REM  defaults to a legacy code page (437/720/1256) and would show
REM  Arabic text as garbage. The app itself switches to UTF-8.
REM ============================================================

setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo.
    echo [ERROR] Virtual environment not found.
    echo.
    echo   Run these first, from the project folder:
    echo       python -m venv .venv
    echo       .venv\Scripts\pip install -r requirements.txt
    echo.
    exit /b 1
)

REM UTF-8 console so Arabic output renders correctly
chcp 65001 >nul 2>&1

"%PY%" -m cli.main %*
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
