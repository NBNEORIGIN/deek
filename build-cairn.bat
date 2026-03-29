@echo off
echo [Cairn] Building frontend...
cd /d D:\claw\web
call npm run build
if %errorlevel% equ 0 (
    echo [Cairn] Build complete. Restart Cairn to apply.
) else (
    echo [Cairn] Build FAILED — check errors above
)
pause
