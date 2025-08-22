# Multi Subtitle App
- FastAPI + Celery + Redis + Whisper + MarianMT
- 1:2:1 UI: 업로드 | 플레이어 | 자막·용어

## Run
1) Install ffmpeg & redis
2) python -m venv venv && activate
3) pip install -r requirements.txt
4) cp .env.example .env
5) celery -A app.tasks.celery_app worker --loglevel=INFO
6) uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
