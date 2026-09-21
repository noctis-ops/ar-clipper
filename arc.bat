@echo off
REM ============================================================
REM  AR-Clipper — مشغّل ويندوز
REM  يوفّر عليك كتابة .venv\Scripts\python -m cli.main في كل مرة.
REM
REM  الاستخدام:   arc doctor
REM               arc clip video.mp4 -s 0 -e 30 -L personal_test
REM               arc serve
REM ============================================================

setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo.
    echo [خطأ] البيئة الافتراضية غير موجودة.
    echo.
    echo   شغّل أولاً:
    echo       python -m venv .venv
    echo       .venv\Scripts\pip install -r requirements.txt
    echo.
    exit /b 1
)

"%PY%" -m cli.main %*
endlocal
