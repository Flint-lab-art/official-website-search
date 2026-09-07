@echo off
rem Official Website Search - dependency installer (Windows)
rem Usage: double-click scripts\install.bat
cd /d "%~dp0.."

rem prefer the py launcher (Python 3.13), fall back to python
set PYTHON=python
where py >nul 2>&1
if %errorlevel%==0 set PYTHON=py -3.13

echo [1/3] Creating virtual environment (.venv) ...
%PYTHON% -m venv .venv
if errorlevel 1 goto fail

echo [2/3] Installing dependencies (playwright, openpyxl) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install playwright openpyxl
if errorlevel 1 goto fail

echo [3/3] Installing Chromium for Playwright ...
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto fail

echo.
echo Done. Start the panel with scripts\start_panel.bat
pause
exit /b 0

:fail
echo [ERROR] Setup failed. Please check the messages above.
pause
exit /b 1
