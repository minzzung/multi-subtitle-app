:: start.bat
@echo off
cd /d C:\Users\mj\Desktop\multi-subtitle-app

:: 가상환경 활성화
call .\venv\Scripts\activate

:: --- 첫 번째 창: Celery worker 실행 ---
start "Celery Worker" cmd /k ^
"celery -A app.tasks.celery_app worker -P solo --loglevel=INFO -Q msapp_queue"

:: --- 두 번째 창: FastAPI(Uvicorn) 실행 ---
start "FastAPI Server" cmd /k ^
"uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"

echo ==============================
echo Multi Subtitle App Started!
echo FastAPI: http://127.0.0.1:8000
echo ==============================
pause
