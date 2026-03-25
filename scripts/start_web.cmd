@echo off
set PORT=3000
cd /d D:\claw\web
echo [claw-web] Starting Next.js dev server on port 3000...
"D:\claw\web\node_modules\.bin\next.cmd" dev -p 3000
