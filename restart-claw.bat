@echo off
title Cairn Restart
cd /d D:\claw
echo [Cairn] Restarting...
call stop-claw.bat
timeout /t 3 /nobreak >nul
call start-claw.bat
