@echo off
cd /d D:\Code\official-website-search

rem check if panel is already running
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:27531/' -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if %errorlevel%==0 goto open

rem start service in background (no console window)
start "" /min ".venv\Scripts\pythonw.exe" "server.py" --no-browser

rem poll until ready (up to ~20 seconds)
set /a tries=0
:waitloop
set /a tries+=1
if %tries% gtr 10 goto fail
curl.exe -s -o NUL --connect-timeout 1 http://127.0.0.1:27531/
if %errorlevel%==0 goto open
ping -n 2 127.0.0.1 >nul
goto waitloop

:fail
echo [ERROR] Failed to start panel. Please check port 27531.
pause
exit /b 1

:open
start "" "http://127.0.0.1:27531/"
