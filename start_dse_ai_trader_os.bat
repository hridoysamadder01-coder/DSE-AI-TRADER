@echo off
setlocal EnableExtensions EnableDelayedExpansion
title DSE AI Trader OS

REM ===========================================================
REM  DSE AI TRADER OS — startup launcher
REM ===========================================================

cd /d "%~dp0"

cls
echo ======================================
echo   DSE AI TRADER OS
echo   Bangladesh Market Intelligence Platform
echo ======================================
echo.

set "PORT=8000"
set "HOST=127.0.0.1"
set "PY=.venv\Scripts\python.exe"
set "URL=http://%HOST%:%PORT%/"

REM ---------- Python check ----------
if not exist "%PY%" (
  echo [..] Python venv
  where python >nul 2>nul
  if errorlevel 1 (
    echo [!!] Python not found on PATH and no .venv. Install Python 3.11+ from https://www.python.org/downloads/
    pause
    exit /b 1
  )
  echo      creating .venv ...
  python -m venv .venv
  if errorlevel 1 (
    echo [!!] failed to create venv
    pause
    exit /b 1
  )
)
echo [OK] Python venv

REM ---------- Dependency check ----------
"%PY%" -c "import fastapi, sqlalchemy, apscheduler, httpx, bs4, loguru, pydantic_settings, tenacity, truststore" >nul 2>nul
if errorlevel 1 (
  echo [..] Installing dependencies (first run)
  "%PY%" -m pip install --upgrade pip --quiet
  "%PY%" -m pip install -e . --quiet
  "%PY%" -m pip install truststore --quiet
  if errorlevel 1 (
    echo [!!] dependency install failed
    pause
    exit /b 1
  )
)
echo [OK] Dependencies

REM ---------- PostgreSQL probe (optional) ----------
"%PY%" -c "import urllib.request, socket; s=socket.socket(); s.settimeout(0.4); s.connect(('127.0.0.1',5432)); s.close()" >nul 2>nul
if errorlevel 1 (
  echo [--] PostgreSQL not detected on :5432 ^(using SQLite at .\data\market.db^)
) else (
  echo [OK] PostgreSQL detected on :5432 ^(SQLite is still the default; set DATABASE_URL to switch^)
)

REM ---------- Redis probe (optional) ----------
"%PY%" -c "import socket; s=socket.socket(); s.settimeout(0.4); s.connect(('127.0.0.1',6379)); s.close()" >nul 2>nul
if errorlevel 1 (
  echo [--] Redis not detected on :6379 ^(in-process scheduler is fine for single-instance use^)
) else (
  echo [OK] Redis detected on :6379
)

REM ---------- Port-in-use check ----------
"%PY%" -c "import socket; s=socket.socket(); s.settimeout(0.4); s.bind(('%HOST%',%PORT%)); s.close()" >nul 2>nul
if errorlevel 1 (
  echo [!!] port %PORT% is already in use. Close the other process or change PORT in this script.
  pause
  exit /b 1
)
echo [OK] Port %PORT% available

REM ---------- Start API + scheduler + collectors (one process) ----------
echo [..] Starting API server ^(uvicorn^)
start "DSE AI Trader OS — server" /min cmd /c ""%PY%" -m uvicorn app.main:app --host %HOST% --port %PORT% --log-level warning"

REM ---------- Wait for health ----------
set "tries=0"
:wait_health
"%PY%" -c "import urllib.request as u; u.urlopen('%URL%health', timeout=1).read()" >nul 2>nul
if errorlevel 1 (
  set /a tries+=1
  if !tries! GEQ 30 (
    echo [!!] Server did not become healthy in 30 seconds.
    echo      Inspect the "DSE AI Trader OS - server" console window for errors.
    pause
    exit /b 1
  )
  ping -n 2 127.0.0.1 >nul
  goto wait_health
)
echo [OK] API server healthy
echo [OK] Scheduler (in-process)
echo [OK] Collectors (in-process)
echo [OK] Frontend served at %URL%
echo.

echo Opening Terminal ...
start "" "%URL%"

echo.
echo --------------------------------------
echo   Trader Terminal:  %URL%
echo   Admin Dashboard:  %URL%admin
echo   API Docs:         %URL%docs
echo --------------------------------------
echo.
echo The server runs in a separate minimized window.
echo Closing THIS window will not stop the server.
echo To stop everything: close the "DSE AI Trader OS - server" window
echo or run:  taskkill /F /FI "WINDOWTITLE eq DSE AI Trader OS - server*"
echo.
pause
endlocal
