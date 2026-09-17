@echo off
REM ============================================================
REM  WeChat Group Chat Summary Assistant - launcher
REM  (wxauto4 / WeChat 4.x adapted build)
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found.
    echo         Please run install.bat first.
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" wechat_summary_gui.py
if errorlevel 1 (
    echo.
    echo [ERROR] The program exited abnormally. See logs\setup.log and the console above.
    pause
)
