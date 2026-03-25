@echo off
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
set HUGGING_FACE_HUB_TOKEN=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
cd /d D:\claw
"D:\claw\.venv\Scripts\python.exe" -m uvicorn api.main:app --host 0.0.0.0 --port 8765 --workers 1
