@echo off
REM ============================================================
REM  Cairn Launcher — double-click to start API + Web UI
REM  Frontend runs as production build (npm start), not dev server
REM ============================================================
title Cairn Launcher
cd /d D:\claw

echo [Cairn] Stopping existing processes...
taskkill /f /fi "WINDOWTITLE eq CLAW API*" >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq CLAW Web*" >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq Cairn API*" >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq Cairn Web*" >nul 2>&1

for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8765 " 2^>nul') do (
    if not "%%a"=="0" if not "%%a"=="" (
        taskkill /f /pid %%a >nul 2>&1
    )
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":3000 " 2^>nul') do (
    if not "%%a"=="0" if not "%%a"=="" (
        taskkill /f /pid %%a >nul 2>&1
    )
)

timeout /t 3 /nobreak >nul

REM Build frontend if no production build exists
if not exist "web\.next\BUILD_ID" (
    echo [Cairn] No production build found — building frontend...
    cd /d D:\claw\web
    call npm run build
    if %errorlevel% neq 0 (
        echo [Cairn] Frontend build FAILED — check errors above
        pause
        exit /b 1
    )
    cd /d D:\claw
    echo [Cairn] Frontend build complete.
)

echo [Cairn] Starting API...
start "Cairn API" cmd /k "cd /d D:\claw && .\.venv\Scripts\python -m uvicorn api.main:app --host 0.0.0.0 --port 8765"

timeout /t 5 /nobreak >nul

echo [Cairn] Starting Web UI (production)...
start "Cairn Web" cmd /k "cd /d D:\claw\web && npm start"

timeout /t 5 /nobreak >nul

start "" http://localhost:3000
start "" http://localhost:3000/status

echo [Cairn] Started. Check the two terminal windows.
timeout /t 3 /nobreak >nul
