@echo off
setlocal EnableExtensions
title DSE AI Trader OS — restart
cd /d "%~dp0"

echo ======================================
echo   DSE AI TRADER OS — restart
echo ======================================
echo.

REM ---- Stop (silently) ----
taskkill /F /FI "WINDOWTITLE eq DSE AI Trader OS - server*" >nul 2>nul

set "PORT=8000"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
  taskkill /F /PID %%P >nul 2>nul
)

REM Give Windows time to release the port
ping -n 2 127.0.0.1 >nul

echo [OK] Stopped any running instance.
echo [..] Starting fresh...
echo.

REM ---- Start ----
call "%~dp0start_dse_ai_trader_os.bat"

endlocal
