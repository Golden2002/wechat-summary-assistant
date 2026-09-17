@echo off
REM ============================================================
REM  WeChat Group Chat Summary Assistant - installer
REM  Creates a Python 3.12 virtual environment and installs deps.
REM
REM  NOTE: wxauto4 requires Python >=3.9,<3.14.
REM        Python 3.14 has NO wheels published, so it cannot work.
REM ============================================================
chcp 65001 >nul
cd /d "%~dp0"
setlocal

echo.
echo === [1/4] Locating a usable Python (3.9 - 3.13) ===
set "BASEPY="
for %%V in (3.12 3.13 3.11 3.10 3.9) do (
    if not defined BASEPY (
        py -%%V -c "import sys" >nul 2>nul && set "BASEPY=py -%%V"
    )
)
if not defined BASEPY (
    echo [ERROR] No Python 3.9-3.13 found via the py launcher.
    echo         Python 3.14 is NOT supported by wxauto4.
    echo         Install Python 3.12 from https://www.python.org/downloads/release/python-31210/
    echo         and make sure "py launcher" is selected during setup.
    echo.
    pause
    exit /b 1
)
echo     Using: %BASEPY%

echo.
echo === [2/4] Creating virtual environment at .venv ===
if exist ".venv\Scripts\python.exe" (
    echo     .venv already exists - reusing it.
) else (
    %BASEPY% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create the virtual environment.
        pause
        exit /b 1
    )
)

echo.
echo === [3/4] Installing dependencies from requirements.txt ===
".venv\Scripts\python.exe" -m pip install --upgrade pip --index-url https://pypi.org/simple
".venv\Scripts\python.exe" -m pip install -r requirements.txt --index-url https://pypi.org/simple
if errorlevel 1 (
    echo.
    echo [ERROR] Dependency installation failed.
    echo         If you are behind a slow mirror, retry with:
    echo         ".venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    pause
    exit /b 1
)

echo.
echo === [4/4] Environment self-check ===
".venv\Scripts\python.exe" check_env.py

echo.
echo Done. Launch the app with run.bat
echo.
pause
endlocal
