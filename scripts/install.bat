@echo off
rem Official Website Search - dependency installer (Windows)
rem UV first (pyproject.toml + uv.lock), fall back to venv + pip (requirements.txt)
rem Usage: double-click scripts\install.bat
cd /d "%~dp0.."

where uv >nul 2>&1
if %errorlevel%==0 (
    echo [1/2] Detected uv, installing dependencies (versions locked by uv.lock) ...
    uv sync
    if errorlevel 1 goto fail
    echo [2/2] Installing Chromium for Playwright ...
    uv run playwright install chromium
    if errorlevel 1 goto fail
) else (
    echo uv not found, falling back to venv + pip ...
    rem prefer the py launcher (Python 3.13), fall back to python
    set PYTHON=python
    where py >nul 2>&1
    if %errorlevel%==0 set PYTHON=py -3.13

    echo [1/3] Creating virtual environment (.venv) ...
    %PYTHON% -m venv .venv
    if errorlevel 1 goto fail

    echo [2/3] Installing dependencies (see requirements.txt) ...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto fail

    echo [3/3] Installing Chromium for Playwright ...
    ".venv\Scripts\python.exe" -m playwright install chromium
    if errorlevel 1 goto fail
)

echo.
echo Done. Start the panel with scripts\start_panel.bat
pause
exit /b 0

:fail
echo [ERROR] Setup failed. Please check the messages above.
pause
exit /b 1
