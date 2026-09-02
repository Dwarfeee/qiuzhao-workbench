@echo off
REM Start Qiuzhao Workbench local server (FastAPI/uvicorn)
REM Double-click to run. Keep this window open; close it to stop the server.
cd /d "%~dp0"
echo ===================================================
echo  Qiuzhao Workbench - Local Server
echo  URL: http://127.0.0.1:8787
echo  Keep this window open. Close it to stop.
echo ===================================================
echo.
"C:\Users\19600\.workbuddy\binaries\python\envs\default\Scripts\python.exe" -m uvicorn server.app:app --host 127.0.0.1 --port 8787
echo.
echo Server stopped. Press any key to close.
pause >nul
