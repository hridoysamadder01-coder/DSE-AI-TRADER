@echo off
setlocal EnableExtensions
title DSE AI Trader OS — stop
cd /d "%~dp0"

echo ======================================
echo   DSE AI TRADER OS — stop
echo ======================================
echo.

REM ---- Kill the server window if it's still running ----
taskkill /F /FI "WINDOWTITLE eq DSE AI Trader OS - server*" >nul 2>nul
if errorlevel 1 (
  echo [--] No "DSE AI Trader OS - server" window found.
) else (
  echo [OK] Stopped server window.
)

REM ---- Belt-and-suspenders: kill any python process holding port 8000 ----
set "PORT=8000"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
  echo [..] Killing PID %%P holding port %PORT%
  taskkill /F /PID %%P >nul 2>nul
)

echo.
echo Done. Server is stopped.
echo.
pause
endlocal
