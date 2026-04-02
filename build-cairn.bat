@echo off
echo [Cairn] Building frontend...
cd /d D:\claw\web
call npm run build
if %errorlevel% equ 0 (
    echo [Cairn] Build complete.
) else (
    echo [Cairn] Build FAILED — check errors above
    pause
    exit /b 1
)

echo.
echo [Cairn] Starting Cairn API on port 8765...
cd /d D:\claw
start "Cairn API" cmd /k ".\.venv\Scripts\python -m uvicorn api.main:app --host 0.0.0.0 --port 8765"

REM Start Cairn MCP Server (if built)
if exist "D:\claw\mcp\cairn_mcp_server.py" (
    echo [Cairn] Starting MCP server...
    start "Cairn MCP" cmd /k ".\.venv\Scripts\python mcp\cairn_mcp_server.py"
) else (
    echo [Cairn] MCP server not found — skipping
)

echo.
echo [Cairn] Ready. API running on http://localhost:8765
echo [Cairn] Use: cairn "your prompt" project_name
pause
